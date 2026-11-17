# 640×360 at approximately 8 FPS — 2026-10-29

The same symptom was reproduced **before encoding or network transmission** on
Front Right (`/dev/videoFR`) and Right (`/dev/videoRIGHT`). Both cameras were
requested at MJPEG 640×360, 30/1 FPS. Their automatic-exposure dynamic-frame-rate
control was enabled. Toggling only that control changed actual capture rate
from about 7.67 FPS to 30 FPS and back, with resolution, requested FPS and scene
unchanged. No congestion-control or profile code was changed.

## Observations

Initial simultaneous capture measured GStreamer v4l2src output directly to
fakesink, without a decoder, H.264 encoder, pacer, UDP or browser:

| Camera | exposure_dynamic_framerate | Actual capture FPS |
| --- | --- | --- |
| Front Left | 0 | 30.01 |
| Front Right | 1 | 7.67 |
| Right | 1 | 7.68 |
| Left | 1 | Capture error; no valid FPS measurement |

The separate Left camera capture failure and USB re-enumeration are recorded
in `capture.json`/log. They should not be mistaken for a measured zero-FPS
profile. The controlled experiment excluded that failing source.

Controlled comparison of Front Right and Right:

| Dynamic frame rate | Front Right FPS | Right FPS |
| --- | --- | --- |
| Enabled (1), baseline | 7.676 | 7.675 |
| Disabled (0) | 29.999 | 30.015 |
| Enabled (1), restored | 7.676 | 7.675 |

Each phase ran ten seconds, discarding its first two seconds before measuring
buffer arrival intervals. The approximately 8 FPS phases had roughly 132 ms
median frame intervals. The fixed-rate phase had roughly 32 ms median intervals.
`exposure-ab.json` contains the raw measurements. No capture errors occurred in
these controlled phases.

## Why it happens

These UVC cameras expose control 0x009a0903 as `exposure_dynamic_framerate`.
It is the standard `V4L2_CID_EXPOSURE_AUTO_PRIORITY` control. The
[Linux camera-control documentation](https://docs.kernel.org/userspace-api/media/v4l/ext-ctrls-camera.html)
states that AUTO/APERTURE_PRIORITY exposure can vary frame rate when this
control is enabled. The measured cameras use APERTURE_PRIORITY (3).
This permits the automatic exposure system to slow the source. The precise
sensor exposure duration/scene brightness was not independently measured;
the causal control toggle and source-rate effect were measured directly.

The pipeline's caps and JSON request 30 FPS. They do not override the camera's
permission to vary the real capture interval. `videorate drop-only=true` can
discard frames but cannot create missing input frames. Encoding at full
resolution therefore remains compatible with an actual input/output rate near
8 FPS, even on a good network.

`VideoStreamCard.tsx` reports FPS from actual VideoFrame canvas draws per elapsed
second. It displays the received/rendered rate, not the selected target FPS in
the rover's profile. Current profile targets are upper bounds; 640×360/8 FPS
is not an explicit congestion-control candidate. No fabricated frames or
forced 30 FPS UI reading should be used to hide the lower input rate.

## Scope and possible correction

The diagnostic temporarily set `exposure_dynamic_framerate=0` on Front Right and
Right, then restored both to their original value 1 in a finally block. The
Front Left setting was unchanged. No persistent camera policy, JSON, launch
defaults, ROS RealSense configuration, encoder, bitrate allocation or UI logic
was changed by this investigation. Cameras and all diagnostic pipelines were
released afterward; the network and running services were untouched.

A fixed capture-rate policy can set this control to 0 on supported cameras
while keeping automatic exposure enabled. This limits exposure to the requested
frame interval and can reduce brightness in dark scenes. Such a production
policy should handle unsupported cameras and be reapplied after USB replug;
a one-time v4l2-ctl setting does not establish a durable startup policy.

At investigation start the normal rover/gateway were stopped, so these are
real-camera source tests, not a new rover-to-browser network acceptance run.
The operator's exact observed camera/client has not been supplied. Front Right
and Right reproduce the reported combination; another feed could have a
different downstream limitation.

The included manual diagnostic runners open physical cameras. Run them only
when those cameras are idle. `exposure_ab.py` temporarily toggles the control
and restores the values it reads at startup. They are evidence helpers, not CI
tests or automatically applied configuration.

## Fixed-rate policy applied after the investigation

At the operator's subsequent request, `video_sender.cpp` now connects to each
V4L2 source's `prepare-format` signal and sets
`V4L2_CID_EXPOSURE_AUTO_PRIORITY=0` through the source's actual capture FD.
Automatic exposure mode is not modified. The hook also covers the unnamed
source used by the one-frame capture check, and runs again when a sender
reopens the camera. An unsupported optional control is skipped; a genuine
configuration failure is logged without aborting the existing capture path.
No output profile or congestion-control rule was changed.

The canonical rover workspace was rebuilt and its installed `video_sender`
symlink verified. Front Left, Front Right, Left and Right were all read back
with `exposure_dynamic_framerate=0` and unchanged APERTURE_PRIORITY (3).
RGBD already had the frame-rate control disabled; its ROS settings were not
changed.

Validation deliberately re-enabled the control before each process launch:

- Both one-frame capture checks disabled it again and completed successfully.
- Two consecutive rounds captured and hardware-encoded 300 frames per camera
  on Front Right and Right at 640×360. Each round used simultaneous cameras,
  the installed sender, Jetson H.264, RTP, and a local fakesink. Actual source
  FPS measured from matched source timestamps was 30.010/30.009 in round one
  and 30.009/29.994 in round two, after discarding the first two seconds.
- Existing software/Jetson sender timing checks matched all 30 frames each.
- Existing video-control, video-pacer and link-probe CTests all passed.

`fixed-rate-result.json` and `fixed-rate-verify.log` record the measurements;
`fixed-rate-verify.py` is the manual hardware validation runner. Unlike the
earlier reversible investigation runner, it deliberately leaves the dynamic
frame-rate control disabled as requested. All diagnostic processes stopped
and cameras were released. No base gateway or full vehicle launch was started,
and no physical USB reconnect or rover-to-browser acceptance run was performed
for this change. The Left control was applied/read back, but this follow-up
does not claim to resolve its earlier USB capture failure.
