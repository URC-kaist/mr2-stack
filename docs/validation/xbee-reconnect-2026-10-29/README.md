# XBee Lost / class ACK loss investigation — 2026-10-29

The rover logs establish a USB disconnection and a missing serial recovery
path. The operator observed XBee Lost, class commands without acknowledgement,
and video failing to recover after rotating the directional antenna. The exact
first time Lost appeared has not been established. Base was initially unreachable;
after the operator restarted it, discovery found 192.168.0.55 and verified its
pinned SSH identity. Persisted base kernel logs were then inspected. They show
no corresponding base XBee USB disconnection during the incident. The base
XBee is FT231X D30JZD5Z on USB 3-3.4 / ttyUSB1; a different FT232R adapter's
startup warning must not be confused with it. The ordinary terminal gateway
stdout/heartbeat history was not found in persistent logs, so the exact first
heartbeat gap and RF state cannot be reconstructed. This report therefore
does not claim a complete two-host RF diagnosis. See `base-usb.log`.

## Confirmed timeline (KST)

| Time | Evidence |
| --- | --- |
| 20:41:16 | XBee bridge and video node started; bridge PID 9241 remained alive until shutdown |
| From 20:43:00 | Camera on USB 1-2.4.2 repeatedly disconnects/re-enumerates |
| 20:44:56 | FTDI ttyUSB0 reports a stopped USB transfer (-32) |
| 20:45:02 | Parent USB hub 1-2 disconnects; FTDI adapter on 1-2.1 disconnects from ttyUSB0; several cameras also disappear |
| 20:45:04 | Same FTDI branch returns, now attached as ttyUSB1 |
| 20:45:08 / 20:45:11 | RGB-D restarts at 160×120 / 5 FPS; Front Right then Front Left at 320×180 / 30 FPS |
| 20:49:14 | Operator sends Ctrl+C; bridge exits cleanly |

The current stable `/dev/ttyXBEE` points to ttyUSB1, with FT231X serial
`D30JZD4O` on the same USB topology branch 1-2.1. Kernel evidence is in
`usb-timeline.log` and `usb-reset-window.log`; launch/video logs are included.
The XBee-specific ROS log contains only the eventual shutdown signal. Its lack
of read errors did not imply a healthy serial connection: the old code ignored
them without logging.

## Failure path

The old C++ bridge opened `/dev/ttyXBEE` only once. Reassigning the udev symlink
to ttyUSB1 did not change the already-open descriptor for the disconnected
ttyUSB0. Read errors/EOF and failed writes were ignored, with no reopen.

Base marks the dashboard Lost if its serial port is unavailable, no rover
heartbeat has arrived, or the most recent heartbeat exceeds the configured
timeout (default 2 seconds). Video class commands and acknowledgements use
this same XBee path; a disconnected rover UART therefore explains both Lost and
No command acknowledgement after the USB failure.

Video feedback also uses XBee. More than two seconds without previously active
feedback caps the rover's aggregate wire budget to 500 kbps. That cap explains
the observed low-resolution RGB-D/one funded HIGH feed after 20:45, and persists
until fresh feedback arrives. Restoring the 2.4 GHz propagation path alone does
not repair a stale USB descriptor.

The simultaneous hub/camera events justify examining the shared USB hub, its
power, cabling and mechanical connections. Logs do not identify whether the
reset was caused by movement, power disturbance, a cable, a camera fault, or an
intentional unplug during troubleshooting. No claim is made that antenna
rotation itself caused the reset. Radio LED colors alone do not establish
successful application heartbeat/ACK delivery. A separate failure of the
actual 2.4 GHz RSSI to recover would still require base/radio evidence.

## Applied repair

`mr2_xbee_bridge/src/xbee_bridge_node.cpp` now detects UART hangup/error/EOF and
fatal writes, closes the obsolete descriptor and reopens the stable device
name, retrying once per second. Startup also survives a temporarily absent or
unconfigured serial device. Connect/disconnect/configuration failures are
logged; repeated unavailable-device warnings are throttled.

Only the reader replaces descriptors, preventing descriptor-reuse races with
writes. Shutdown joins the reader before closing the descriptor. Partial
protocol input and kernel UART RX/TX buffers are discarded on reopen, and
outgoing messages are never retained for replay across disconnection. No fresh
heartbeat is synthesized on reconnect; existing command watchdog behavior is
retained. The wire protocol, baud rate (115200), control topics and ordinary
startup command remain unchanged.

The rover's canonical installed bridge was rebuilt, so the next normal launch
uses the repair. No real vehicle launch, motion command, camera disconnect,
host address/route change, or Internet disconnection was used to test it.
Base code did not need a corresponding change for this repair.

## Verification

`scripts/latency/test_xbee_reconnect.py` uses ROS domain 174 and PTYs only. It
covers an absent startup device, an invalid TTY, initial heartbeat/class ACK,
six unplug/replug cycles with a stable symlink pointing to a different endpoint,
and recovery of heartbeat and bidirectional class acknowledgement each time.
Partial old commands, pre-open kernel input, and disconnected outgoing status
must not be replayed on the new endpoint.

The extended `scripts/latency/test_video_xbee_control.py` uses ROS domain 171,
generated images, the actual bridge/video node and an actual H.264 decoder.
It verifies class ACK after USB replug, the 500 kbps feedback timeout fallback,
renewed feedback raising the target, and fresh 640×360 decoding after recovery,
in addition to existing priority/state/retry/probe/exclusive-lease checks.

Results and the final canonical build log are included alongside this report.
These isolated checks establish software recovery; they do not repair a
physical USB/power fault or establish the cause of the initial antenna-related
symptom. Real hardware acceptance after a controlled disconnect remains to be
performed in an appropriate operational window.

The separately reported 640×360 / approximately 8 FPS observation is deferred
at the operator's request. No profile/FPS policy change was made for it.
