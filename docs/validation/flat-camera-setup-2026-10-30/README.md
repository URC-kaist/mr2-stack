# Flat rover camera experiment — 2026-10-30

Rover and base are configured for the flat video map. The actual three
connected cameras render in the deployed dashboard. Motors are not attached
and no motor, CAN controller, GNSS or autonomy nodes are started.

## Saved setup

- Both checkouts select `flat` with local camera-only options, using
  `bash scripts/rover.bash start flat --cameras-only`. The selector persists
  `enable_flat_motor=false` and `enable_gnss=false`; the ordinary ROS launch
  consumes them. Regular `start flat` or `start original` clears those local
  options. Explicit launch arguments continue to take precedence.
- The base's ignored `base/gateway/.env.local` selects the absolute flat JSON
  path through `VIDEO_CONFIG_PATH`. Other environment settings are preserved.
  Thus its usual `npm start` needs no extra video configuration option.
- `video_streams_flat.json` maps `flat_cam_1` to USB port `2.3.1`, matching the
  currently attached camera. Ports `2.4.1` and `2.4.2` remain cams 3 and 4.
  Cam 2's `2.2.2` port is unconnected and its tile waits for video.
- `start_chrony.sh` resolves selected CAN module defaults and explicit flags
  before initializing CAN. This experiment skips CAN setup and needs no sudo
  for that step. The field wrapper preserves an existing flat camera-only
  selection. Original/regular flat hardware defaults remain available.

## Verification

The launch package built successfully. Thirty profile/composition tests pass,
including all three launch entrypoints with persisted camera-only settings
and an explicit motor override. Installed `ros2 launch --show-args` confirms
motor, GNSS and original hardware defaults are false and selects the flat JSON.

The normal start commands in the field network are:

```bash
# Base:
cd ~/mr2-stack/base/gateway
npm start

# Rover:
source /opt/ros/humble/setup.bash
cd ~/mr2-stack/rover/ros2_ws
source install/setup.bash
ros2 launch mr2_launch rover_real.launch.py
```

These commands inherit the saved flat camera-only configuration. The base
defaults to rover `192.168.1.102`; the rover's environment and clock policy use
base `192.168.1.101`. Development-network overrides belong only in command
arguments: `npm start -- --rover-host 192.168.0.57` and
`ros2 launch mr2_launch rover_real.launch.py video_base_host:=192.168.0.55`.
The rover workspace is loaded manually with the two setup commands above.
No MR2 workspace auto-source change is retained in `.bashrc`.
The base already auto-loads its ROS/Node environment in a normal terminal.
Neither command requires the `start_chrony*.sh` wrapper.

The initial 100-second observation below used those wrappers, which invoke
the same underlying npm/ROS entrypoints. A subsequent check launches the npm
and ROS commands directly, sourcing `/opt/ros/humble/setup.bash` and
`install/setup.bash` explicitly before the ROS launch and using only the above
development IP overrides, independently of the wrappers.

That direct-start check exposed a persistent browser decoder failure for cam 1
after a resolution change; the other two feeds continued. A subsequent trace
also captured key chunks with `SPS,PPS,AUD,SPS,PPS,IDR` NAL order. The AVC
conversion now removes in-band SPS/PPS and access delimiters; the parameter
sets already live in `VideoDecoderConfig.description`. This avoids a separate
parameter-only prefix being mistaken for the key picture. AVC/description
framing is documented in the [W3C AVC registration](https://www.w3.org/TR/webcodecs-avc-codec-registration/).
The renderer also creates a new decoder for each codec configuration to fence
old callbacks. AVC remains the preferred format. Dashboard regression tests,
typecheck, lint and production build pass; corrected assets are deployed on
base. The direct manual-source test is repeated on the final frontend revision.

Both XBee devices opened at `/dev/ttyXBEE`. The browser was observed for 100
seconds. All three connected feeds reached 640×360; each rendered approximately
30 FPS over the final 20 seconds. Renderer worker completion events were counted
in the real deployed Chromium dashboard, rather than inferring playback from
UDP traffic or cumulative historical counts. Actual image output was visually
checked in a screenshot. All three final overlays read `Live`.

During initial capacity discovery and encoder profile changes, cam 1 briefly
reported a decoder/keyframe error and recovered automatically. All three
were Live by the 40-second sample and continued through the end. This run
does not establish long-duration USB or radio reliability.

The ROS graph contained only gateway relay, latency agent, rosbridge, system
status, video streaming and XBee bridge nodes. `can0` remained DOWN. The final
UI packet delay was 2.3 ms median / 8.4 ms P95; this excludes capture, encoding
and browser rendering. Evidence is in `summary.json`, `nodes.log`, build/test
logs and installed launch arguments.

## Final direct-command verification

After the framing fix, the base was started with `npm start` (plus the runtime
development-network rover address). On rover, the test explicitly sourced
`/opt/ros/humble/setup.bash`, changed to `rover/ros2_ws`, sourced
`install/setup.bash`, and executed `ros2 launch mr2_launch rover_real.launch.py`
with only its runtime development-network video address. No start wrapper or
hardware/flat configuration flag was used.

The final frontend passed a new 60-second browser observation. All three
connected feeds reached 640×360 and approximately 30 FPS over the final
20 seconds. Decoder instrumentation saw **zero decoder errors across ten codec
configurations**, including the initial tiny software-encoded profile and
hardware encoder/resolution changes. The first AVC key chunk contained only
its IDR NAL, with SPS/PPS supplied through the decoder description. The unused
2.2.2 camera slot remained unavailable as expected.

`direct-manual-start.json` records this final result separately from the earlier
wrapper observation and failed diagnostic runs. A dashboard reload loads the
corrected decoder. Test processes were stopped and port 8081 was released.

No radio, Ethernet, routing or Internet setting was changed. The existing
clock preparation remains, including its up-to-90-second warmup. Field defaults
remain base `192.168.1.101` and rover `192.168.1.102`; the `.0.55/.0.57` values
above are runtime overrides for this development network. Test-launched base,
rover and browser processes were stopped; port 8081 is free for the user.
