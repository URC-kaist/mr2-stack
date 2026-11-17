# Steady-stream probe evidence

See the [review and recommendations](../../steady-stream-latency-review-2026-10-30.md)
for boundaries, methods and limitations. All fixtures are generated video;
these experiments do not measure physical camera-to-screen latency.

- `browser-results.json`: actual base Chromium, constant 30 FPS, AVCC,
  low-delay flag on/off, one/five decoders, POC controls and repeated default.
- `browser-lowfps-results.json`: constant 5 FPS on/off comparison.
- `pacer-results.json`: installed native sender, fixed 30 FPS, loopback,
  simple/complex scenes and controlled pacing rates.
- `encoder-stages.json`: raw conversion/encoder/videorate/POC observations.
- `jitter-results.json`: same-host UDP receiver with sync=false; matched
  markers before/after jitter and matching downstream AU subsets.
- `harness/`: observational scripts; fixture generation needs Jetson plugins.
  Fixtures remained under `/tmp/mr2-steady-latency-review/fixtures`; hashes and
  the excluded initial setup attempts are in `setup-notes.json`.
- `cleanup.json`: unchanged recorded product hashes, unlocked clock policies
  and unused temporary ports after all probes stopped.

The 8.4 Mbps loopback pacing condition isolates sender waiting. It is not a
field transmit-rate recommendation. Do not sum separate probe percentiles to
claim a combined end-to-end improvement.
