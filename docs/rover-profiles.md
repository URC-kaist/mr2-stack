# Rover profiles

Select the hardware defaults from the repository root:

```bash
./scripts/rover.bash start flat
./scripts/rover.bash start original
```

These are the selector's only two commands. Despite the name `start`, they only
atomically write `.mr2-rover-profile` (ignored by Git). They do not launch or stop
processes, configure CAN, install udev rules, or change Wi-Fi/IP settings. A running
launch keeps its existing profile. Stop it normally before launching another stack.
The selection persists across shells and reboots; absent a selection, the original
launch defaults apply. An invalid selection fails instead of falling back.

Launch normally, for example with the Jetson-hosted gateway and dashboard:

```bash
source /opt/ros/humble/setup.bash
source rover/ros2_ws/install/setup.bash
ros2 launch mr2_launch rover_direct.launch.py
```

`rover.launch.py`, `rover_real.launch.py`, `rover_direct.launch.py`, and
`video_streaming.launch.py` read the selected profile. Explicit ROS launch arguments
override profile defaults. `rover_real.launch.py` retains its remote base-station
networking; `rover_direct.launch.py` keeps video and gateway traffic on loopback.
The older `run_rover_direct.bash` wrapper supplies explicit arguments and should not
be used for profile-based operation; use the ROS launch command above.

## Flat rover: direct and base-station operation

The hardware profile and network topology are independent. Both entrypoints
support `flat`; select it once with `./scripts/rover.bash start flat`.

For an initial camera-only test on the Jetson, with the installed nginx dashboard:

```bash
ros2 launch mr2_launch rover_direct.launch.py enable_gnss:=false enable_flat_motor:=false
```

This starts the local gateway, ROSBridge and four camera streams, using the same
flat video configuration on both the sender and receiver. It uses the existing
nginx service. Open the Jetson's HTTPS dashboard and select **Live Feed**. Omitting
`enable_gnss:=false` restores the profile's two GPS receivers. The explicit `enable_flat_motor:=false` keeps the single-motor CAN node off for
this camera-only test.

For operation through a separate base station, stop the direct launch first, then
use the following on the Jetson (replace `BASE_STATION_IP` with the actual address):

```bash
ros2 launch mr2_launch rover_real.launch.py video_base_host:=BASE_STATION_IP
```

On the base station, run its existing gateway/nginx setup and append this option to
the gateway command, from the repository root:

```text
--video-config rover/ros2_ws/src/mr2_launch/config/video_streams_flat.json
```

Both machines need this same stream configuration: stream IDs and UDP ports
(`5010`, `5012`, `5014`, `5016`) must match. The base gateway receives RTP and does
not open the camera device paths in the JSON. The base's ROSBridge proxy must point
to the Jetson for ROS controls; video reception alone uses the listed UDP ports.
Selecting a profile on the Jetson does not configure a different computer's gateway.
The launch argument changes only the video destination for that invocation, not
Wi-Fi, IP assignments or saved `.env` values. Remote base-station operation still
requires a separate network/end-to-end test.

Direct camera verification on 2026-09-18: the existing nginx served the dashboard
with HTTP 200 and reported all four streams available. Subscribing through
`wss://192.168.0.51/video-ws` delivered 60 H.264 frames per camera; the captured
streams decoded successfully to 320x180 images, which were visually checked.
This verifies capture, encoding, gateway and nginx transport plus frame decoding;
the operator's browser rendering and remote base-station path were not tested.
Wi-Fi/IP/nginx configuration remained unchanged. GPS was disabled for this test
with the launch argument above; CAN remained down.

Profile JSON files live in `rover/ros2_ws/src/mr2_launch/config/`:

- `profile_original.json` leaves existing entrypoint defaults unchanged.
- `profile_flat.json` selects four USB streams and two GNSS receivers. Original
  drive/arm controllers, battery monitors, LED, turret, science actuators,
  RealSense, localization, and navigation are disabled by default. The isolated
  `mr2_flat_motor` node controls motor 104 from the existing `/base/cmd_vel.linear.x`.
  The existing XBee command bridge is enabled: physical XBee in base-station mode,
  or a PTY to the local gateway in direct mode. Base dashboard/nginx code is unchanged.
- `video_streams_flat.json` uses the observed capture-node paths `2.2.1`, `2.2.2`,
  `2.4.1`, and `2.4.2`, with neutral USB-port labels until viewing directions are
  assigned. Keep the hub/Jetson port wiring fixed. All four cameras share a serial,
  so `/dev/v4l/by-id` is not a unique identifier here. Existing original-rover udev
  aliases are not used or changed.

The GNSS receivers are selected by their existing `TowerRx_` and `NorthRx_` serials,
using the existing left/right namespaces. GPS reception does not itself enable
navigation or RTK correction delivery. Direct mode listens for corrections on
`/base/rtcm` by default; a correction source must publish there for RTK operation.

The connected single AK60-6 is V3.0, as confirmed by the operator. UART reports
motor ID **104 (0x68)**. Its CAN bitrate, feedback configuration, and suitable
travel limits initially required verification. UART configuration now confirms
**500 kbit/s, periodic feedback at 50 Hz, and UART 921600 baud**. CAN communication and a 90-degree motion were subsequently verified. The flat
profile now starts the dedicated single-motor velocity node. Its earlier +/-200
degree software clamp has been removed; the default maximum output speed is
180 degrees/s, and physical travel clearance depends on the installation.
Do not enable
`enable_rover_hardware` to add it: that flag starts the original rover hardware
stack.

Motor bring-up on 2026-09-18: the R-LINK enumerates at
`/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C7B016252-if00`. The operator confirmed
the 3.3 V switch setting and dedicated R-LINK 8-pin to motor UART 3-pin cable.
Initial queries received no bytes. After the operator connected the motor's
external XT30 power supply, V3 firmware/status queries succeeded at **921600 baud**
with valid CRCs; 115200 baud received no response. R-LINK USB power alone does not
power the motor through its three-wire UART connection. The V3 status request is
`AA 01 45 18 61 BB`, different from the older AK UART framing. Firmware identification
contains `CMESC_AK60_6_HW_V3.3` and `AK60_6_SE_V3.3`. Five subsequent status samples
reported ID 104, input voltage 22.4–22.5 V, motor temperature 24.2–24.3 °C, MOS
temperature 26.7 °C, zero electrical RPM, and fault code 0. The reported outer-loop
position was approximately 138.33°; no new origin was set.

`can0` received no motor frames at either 500 kbit/s or, after the operator changed
the interface, 1 Mbit/s. One documented motor-disable frame (extended ID `0x0F68`,
DLC 0) was sent at 1 Mbit/s to check for the documented reply. No motor reply or
successful transmission was recorded; the controller reported transmit errors,
entered bus-off once, and automatically restarted. The four received CAN error
frames are not motor feedback. CAN signal wiring and transceiver operation still
need checking. A subsequent UART query reported ID 104, fault code 0, and zero
electrical RPM.

UART application-configuration readback subsequently confirmed that the motor
was configured for 1 Mbit/s and periodic feedback at 50 Hz. Using the official
CubeMarsTool V3.2.3 application's protocol definitions, only its CAN bitrate was
changed to **500 kbit/s**, matching `scripts/can0.bash` and the original rover.
The write was acknowledged, and a full configuration readback verified that
every other byte was unchanged. No calibration or rotation commands were sent.
The motor configuration step does not require a working CAN connection; external
motor power and R-LINK UART are sufficient.

The operator subsequently ran `./scripts/can0.bash`, restoring the Jetson to
500 kbit/s. Both ends now report 500 kbit/s, but a five-second passive capture
still received no CAN frames. A motor power-cycle was requested before repeating
the capture to rule out a retained CAN error state. After the operator power-cycled
the motor, UART readback confirmed that ID 104, 500 kbit/s and 50 Hz feedback were
retained, with fault code 0. CAN still received no frames in five seconds. One
additional disable frame at the now-matching 500 kbit/s again caused transmit
errors and bus-off followed by automatic recovery, with no successful transmitted
packet. Further progress requires checking the new transceiver's model, supply,
enable/standby pins, and Jetson TX/RX connections; another configuration rewrite
or repeated reboot is not indicated by these observations.
Read the motor's saved settings without changing them with:

```bash
python3 scripts/ak_v3_uart.py
```

The helper uses only Python's standard library. It saves CRC-validated raw
responses under `~/.local/state/mr2/ak60-6/`. The verified before/after backup for
this motor is `config-1789671903099979093.json` in that directory. For this observed
AK60-6 V3.3 firmware/configuration layout, an explicit bitrate change is supported:

```bash
python3 scripts/ak_v3_uart.py --can-bitrate 500000 --expect-id 104
```

An already matching bitrate performs no flash write. The helper preserves all
other settings, requires a stationary motor with no reported fault, checks the
expected motor ID, and verifies the entire configuration after a single write.
It rejects unrecognized layouts for interpretation/writing; do not generalize
the captured application layout to other AK firmware versions.

For V3, Servo/MIT control does not require the older persistent mode distinction;
CAN feedback can be configured as periodic or request-response. See the
[official FAQ](https://www.cubemars.com/faq) and
[V3 manual](https://www.cubemars.com/data/cms/202602/ak-series-prodcut-manual-v3-2-0-for-ak-3-0-robotic-actuator.pdf).
The current integration reuses the base station's unchanged **Manual drive**
controls: RT/LT contribute positive/negative dashboard Y, serialized as ROS
`/base/cmd_vel.linear.x`. A flat-only rover node maps this value to motor 104
rotation; do not use Arm Servo for the single motor. See
[flat motor operation](../rover/ros2_ws/src/mr2_flat_motor/README.md) for commands,
speed scaling, direction inversion, continuous rotation, and watchdog behavior. The earlier
no-CAN observations above are bring-up history, superseded by the successful
CAN/motion checks. Physical base-station radio/gamepad operation still requires
an end-to-end operator check.

After installing these files, build the flat motor and launch packages:

```bash
cd rover/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select mr2_flat_motor mr2_launch
```

No rebuild is required when selecting an already installed profile.

## Network settings

Network settings belong to the Jetson and operating location, separately from the
rover hardware profile. Moving the same Jetson between chassis on the same network
does not require new Wi-Fi credentials or an IP change. Use a router DHCP
reservation if a stable LAN address is needed.

On 2026-09-18 the Jetson was connected as a Wi-Fi client at `192.168.0.51` and via
Ethernet at `192.168.0.60`; both were on `192.168.0.0/24`. These DHCP addresses may
change. The saved `.env` base/rover addresses used `192.168.1.*`; update them to the
actual endpoints before using a separate base station. They are not the local
video destination in rover-direct mode.

The installed HTTPS certificate covers `192.168.0.62`, `10.42.0.1`, `rover`, and
`rover.local`, not the current LAN IPs. For HTTPS access, use a covered hostname that
resolves to this Jetson, or issue a certificate covering the chosen stable address.
Certificate trust is still required for the existing self-signed certificate.

If operating through the Jetson's own AP, configure that separately using the
existing AP setup procedure in [rover-direct-operation.md](rover-direct-operation.md).
Do not switch Wi-Fi connections as a side effect of selecting a rover profile.
