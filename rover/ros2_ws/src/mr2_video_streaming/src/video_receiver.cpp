#include "rtp_send_time.hpp"
#include "link_probe.hpp"
#include "receiver_correlation.hpp"
#include "receive_freshness.hpp"
#include <gst/app/gstappsink.h>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <fcntl.h>
#include <unistd.h>

static volatile std::sig_atomic_t stopped = 0;
static ReceiverCorrelation correlation;
static bool native_metadata = false;
static ReceiveFreshness freshness;
static void stop(int) {stopped = 1;}
static bool write_all(const guint8 * data, size_t size)
{
  while (size && !stopped) {
    const auto count = write(STDOUT_FILENO, data, size);
    if (count < 0 && errno == EINTR) {continue;}
    if (count <= 0) {return false;}
    data += count; size -= count;
  }
  return size == 0;
}
static GstFlowReturn access_unit(GstAppSink * sink, gpointer)
{
  auto * sample = gst_app_sink_pull_sample(sink);
  if (!sample) {return GST_FLOW_EOS;}
  GstMapInfo map = GST_MAP_INFO_INIT;
  auto * buffer = gst_sample_get_buffer(sample);
  bool ok = false;
  if (gst_buffer_map(buffer, &map, GST_MAP_READ)) {
    guint8 header[28]{};
    GST_WRITE_UINT32_BE(header, map.size | (native_metadata ? 0x80000000u : 0));
    if (native_metadata) {
      ReceiverCorrelation::Identity id{};
      const bool verified = correlation.match(map.data, map.size, GST_BUFFER_PTS(buffer), id);
      GST_WRITE_UINT32_BE(header + 4, verified ? 1 : 0);
      GST_WRITE_UINT32_BE(header + 8, id.ssrc); GST_WRITE_UINT32_BE(header + 12, id.timestamp);
      GST_WRITE_UINT16_BE(header + 16, id.sequence);
      GST_WRITE_UINT64_BE(header + 20, rtp_send_time::epoch_us());
    }
    ok = write_all(header, native_metadata ? sizeof(header) : 4) && write_all(map.data, map.size);
    gst_buffer_unmap(buffer, &map);
  }
  gst_sample_unref(sample);
  return ok ? GST_FLOW_OK : GST_FLOW_ERROR;
}
// Fixed 52-byte records on fd=4, written atomically and nonblocking. This probe
// executes before the jitter buffer; IPC delivery time is never arrival time.
static GstPadProbeReturn observe(GstPad *, GstPadProbeInfo * info, gpointer)
{
  static uint32_t dropped = 0;
  GstBuffer * buffer = GST_PAD_PROBE_INFO_BUFFER(info);
  if (!buffer) {return GST_PAD_PROBE_OK;}
  const auto arrival = rtp_send_time::mono_us();
  const auto epoch = rtp_send_time::epoch_us();
  GstRTPBuffer rtp = GST_RTP_BUFFER_INIT;
  if (!gst_rtp_buffer_map(buffer, GST_MAP_READ, &rtp)) {return GST_PAD_PROBE_OK;}
  const bool probe = gst_rtp_buffer_get_payload_type(&rtp) == link_probe::kPayloadType &&
    gst_rtp_buffer_get_ssrc(&rtp) == link_probe::kSsrc;
  guint8 appbits = 0; gpointer ext = nullptr; guint size = 0;
  uint64_t sent = 0;
  if (gst_rtp_buffer_get_extension_twobytes_header(
      &rtp, &appbits, rtp_send_time::kId, 0, &ext,
      &size) && size == 16)
  {
    sent = GST_READ_UINT64_BE(ext);
    guint8 record[52] = {};
    GST_WRITE_UINT64_BE(record, arrival);
    GST_WRITE_UINT64_BE(record + 8, epoch);
    GST_WRITE_UINT64_BE(record + 16, GST_READ_UINT64_BE(ext));
    GST_WRITE_UINT64_BE(record + 24, GST_READ_UINT64_BE(static_cast<guint8 *>(ext) + 8));
    GST_WRITE_UINT32_BE(record + 32, gst_rtp_buffer_get_ssrc(&rtp));
    GST_WRITE_UINT32_BE(record + 36, gst_rtp_buffer_get_timestamp(&rtp));
    GST_WRITE_UINT16_BE(record + 40, gst_rtp_buffer_get_seq(&rtp));
    GST_WRITE_UINT16_BE(record + 42, gst_buffer_get_size(buffer) + 28); // UDP/IPv4 overhead
    GST_WRITE_UINT32_BE(record + 44, 0x4d523201);
    GST_WRITE_UINT32_BE(record + 48, dropped);
    if (write(4, record, sizeof(record)) != sizeof(record)) {++dropped;}
  }
  gst_rtp_buffer_unmap(&rtp);
  return probe || !freshness.permits(sent, arrival) ? GST_PAD_PROBE_DROP : GST_PAD_PROBE_OK;
}
int main(int argc, char ** argv)
{
  if (argc < 2) {return 2;}
  gst_init(nullptr, nullptr);
  const int flags = fcntl(4, F_GETFL);
  if (flags >= 0) {fcntl(4, F_SETFL, flags | O_NONBLOCK);}
  GError * error = nullptr;
  GstElement * pipeline = gst_parse_launchv(const_cast<const gchar **>(argv + 1), &error);
  if (!pipeline || error) {
    std::fprintf(stderr, "video_receiver: %s\n", error ? error->message : "invalid pipeline");
    if (error) {g_error_free(error);}
    if (pipeline) {gst_object_unref(pipeline);}
    return 1;
  }
  GstElement * source = gst_bin_get_by_name(GST_BIN(pipeline), "video_source");
  if (!source) {gst_object_unref(pipeline); return 2;}
  GstPad * pad = gst_element_get_static_pad(source, "src");
  gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, observe, nullptr, nullptr);
  auto * output = gst_bin_get_by_name(GST_BIN(pipeline), "video_output");
  auto * jitter = gst_bin_get_by_name(GST_BIN(pipeline), "video_jitter");
  if (jitter) {
    native_metadata = true;
    auto * pad = gst_element_get_static_pad(jitter, "src");
    gst_pad_add_probe(
      pad, GST_PAD_PROBE_TYPE_BUFFER, [](GstPad *, GstPadProbeInfo * info, gpointer) {
        auto * buffer = GST_PAD_PROBE_INFO_BUFFER(info);
        GstRTPBuffer rtp = GST_RTP_BUFFER_INIT;
        uint64_t sent = 0;
        if (gst_rtp_buffer_map(buffer, GST_MAP_READ, &rtp)) {
          guint8 bits = 0; gpointer ext = nullptr; guint size = 0;
          if (gst_rtp_buffer_get_extension_twobytes_header(
            &rtp, &bits, rtp_send_time::kId, 0, &ext,
            &size) && size == 16)
          {
            sent = GST_READ_UINT64_BE(ext);
          }
          gst_rtp_buffer_unmap(&rtp);
        }
        if (!freshness.permits(sent, rtp_send_time::mono_us())) {return GST_PAD_PROBE_DROP;}
        correlation.packet(buffer); return GST_PAD_PROBE_OK;
      }, nullptr, nullptr);
    gst_object_unref(pad); gst_object_unref(jitter);
  }
  if (output && GST_IS_APP_SINK(output)) {
    GstAppSinkCallbacks callbacks = {};
    callbacks.new_sample = access_unit;
    gst_app_sink_set_callbacks(GST_APP_SINK(output), &callbacks, nullptr, nullptr);
  }
  std::signal(SIGTERM, stop); std::signal(SIGINT, stop); std::signal(SIGPIPE, SIG_IGN);
  auto * bus = gst_element_get_bus(pipeline);
  int result =
    gst_element_set_state(pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE ? 1 : 0;
  while (!stopped && !result) {
    freshness.poll();
    auto * msg = gst_bus_timed_pop_filtered(
      bus, 20 * GST_MSECOND,
      GstMessageType(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));
    if (!msg) {continue;}
    if (GST_MESSAGE_TYPE(msg) == GST_MESSAGE_ERROR) {
      gst_message_parse_error(msg, &error, nullptr);
      std::fprintf(stderr, "video_receiver: %s\n", error->message);
      g_error_free(error); result = 1;
    }
    gst_message_unref(msg); break;
  }
  gst_element_set_state(pipeline, GST_STATE_NULL);
  if (output) {gst_object_unref(output);}
  gst_object_unref(pad); gst_object_unref(source); gst_object_unref(bus);
  gst_object_unref(pipeline);
  return result;
}
