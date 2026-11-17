#pragma once
#include <gst/gst.h>
#include <gst/rtp/gstrtpbuffer.h>
#include <array>
#include <deque>
#include <string>

class ReceiverCorrelation
{
public:
  struct Identity { uint32_t ssrc, timestamp; uint16_t sequence; };

private:
  struct Entry { std::string digest; GstClockTime pts; Identity id; };
  std::deque<Entry> entries_;
  GChecksum * hash_{g_checksum_new(G_CHECKSUM_SHA256)};
  bool active_{false}, valid_{false}, vcl_{false};
  int fragment_{-1}; uint16_t next_{0}; uint32_t ssrc_{0}, timestamp_{0};
  static bool vcl(int type) {return type >= 1 && type <= 5;}
  void nal(const guint8 * p, size_t n)
  {
    if (!n) {valid_ = false; return;}
    if (vcl(p[0] & 31)) {g_checksum_update(hash_, p, n); vcl_ = true;}
  }
  static std::string digest(GChecksum * hash)
  {
    guint8 bytes[32]; gsize n = sizeof(bytes); g_checksum_get_digest(hash, bytes, &n);
    return std::string(reinterpret_cast<char *>(bytes), n);
  }

public:
  ~ReceiverCorrelation() {g_checksum_free(hash_);}
  void packet(GstBuffer * buffer)
  {
    GstRTPBuffer r = GST_RTP_BUFFER_INIT;
    if (!gst_rtp_buffer_map(buffer, GST_MAP_READ, &r)) {return;}
    const auto ssrc = gst_rtp_buffer_get_ssrc(&r), ts = gst_rtp_buffer_get_timestamp(&r);
    const auto seq = gst_rtp_buffer_get_seq(&r);
    if (!active_ || ssrc != ssrc_ || ts != timestamp_) {
      active_ = true; valid_ = true; vcl_ = false; fragment_ = -1; next_ = seq;
      ssrc_ = ssrc; timestamp_ = ts; g_checksum_reset(hash_);
    }
    if (seq != next_) {valid_ = false;}
    next_ = uint16_t(seq + 1);
    const auto * p = static_cast<const guint8 *>(gst_rtp_buffer_get_payload(&r));
    const auto n = gst_rtp_buffer_get_payload_len(&r);
    if (!p || !n) {valid_ = false;} else {
      const auto type = p[0] & 31;
      if (type > 0 && type < 24) {
        if (fragment_ != -1) {
          valid_ = false;
        }
        nal(p, n);
      } else if (type == 24) {
        if (fragment_ != -1) {valid_ = false;}
        size_t at = 1;
        while (at + 2 <= n) {
          const auto size = GST_READ_UINT16_BE(p + at); at += 2;
          if (!size || at + size > n) {valid_ = false; break;}
          nal(p + at, size); at += size;
        }
        if (at != n) {valid_ = false;}
      } else if (type == 28 && n >= 2) {
        const int header = (p[0] & 0xe0) | (p[1] & 31);
        const bool start = p[1] & 128, end = p[1] & 64;
        if (start && end) {valid_ = false;}
        if (start) {
          if (fragment_ != -1) {
            valid_ = false;
          }
          fragment_ = header; const guint8 h = header; nal(&h, 1);
        } else if (fragment_ != header) {valid_ = false;}
        if (vcl(header & 31)) {g_checksum_update(hash_, p + 2, n - 2);}
        if (end) {fragment_ = -1;}
      } else {valid_ = false;}
    }
    if (gst_rtp_buffer_get_marker(&r)) {
      if (valid_ && vcl_ && fragment_ == -1) {
        entries_.push_back({digest(hash_), GST_BUFFER_PTS(buffer), {ssrc, ts, seq}});
      }
      active_ = false;
      while (entries_.size() > 128) {entries_.pop_front();}
    }
    gst_rtp_buffer_unmap(&r);
  }
  bool match(const guint8 * bytes, size_t size, GstClockTime pts, Identity & result)
  {
    auto * h = g_checksum_new(G_CHECKSUM_SHA256); bool any = false;
    auto code = [&](size_t at) -> size_t {
        if (at + 3 <= size && !bytes[at] && !bytes[at + 1] && bytes[at + 2] == 1) {return 3;}
        if (at + 4 <= size && !bytes[at] && !bytes[at + 1] && !bytes[at + 2] &&
          bytes[at + 3] == 1)
        {
          return 4;
        }
        return 0;
      };
    size_t at = 0;
    while (at < size) {
      const auto prefix = code(at); if (!prefix) {++at; continue;}
      const auto start = at + prefix; at = start;
      while (at < size && !code(at)) {++at;}
      if (start < at && vcl(bytes[start] & 31)) {
        g_checksum_update(h, bytes + start, at - start); any = true;
      }
    }
    const auto key = digest(h); g_checksum_free(h);
    if (!any || !GST_CLOCK_TIME_IS_VALID(pts)) {return false;}
    auto found = entries_.end();
    for (auto it = entries_.begin(); it != entries_.end(); ++it) {
      const auto distance = pts > it->pts ? pts - it->pts : it->pts - pts;
      if (it->digest == key && GST_CLOCK_TIME_IS_VALID(it->pts) && distance <= GST_MSECOND) {
        if (found != entries_.end()) {return false;}
        found = it;
      }
    }
    if (found == entries_.end()) {return false;}
    result = found->id; entries_.erase(found); return true;
  }
};
