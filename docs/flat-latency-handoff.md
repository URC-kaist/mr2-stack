# Flat rover latency: base/rover handoff

This integration supports a **separate base station and Jetson**, using the four
flat video feeds and the existing XBee command path. Install the same commit on
both machines. Source checkout changes alone do not update running programs,
ROS installations, `/var/www/mr2-dashboard`, or nginx.

The initial integration did not change host configuration. The subsequent
two-host deployment and measured results are recorded in
[the router acceptance report](latency-router-acceptance-20260918.md).
The prior feasibility review is in [latency-integration-assessment.md](latency-integration-assessment.md).

## Delivery and version agreement

The delivery archive contains a Git bundle, `REVISION`, this guide, and local
verification results. The bundle includes history after the common ancestor
`5f04032484cd0c5a37b27c039c6a425aaaf0fdf3`, which both existing rover-direct and
chrony feature checkouts should have. Verify the archive checksum before use.
On the base, with `DELIVERY` set to the extracted delivery directory:

```bash
cd /path/to/mr2-stack
git status --short                    # Preserve any local work before switching.
git rev-parse HEAD > "$DELIVERY/base-previous-revision.txt"
git bundle verify "$DELIVERY/mr2-flat-latency.bundle"
git fetch "$DELIVERY/mr2-flat-latency.bundle" \
  refs/heads/2026-nepo-demo:refs/remotes/handoff/flat-latency
test "$(git rev-parse refs/remotes/handoff/flat-latency)" = "$(cat "$DELIVERY/REVISION")"
git switch -c deploy/flat-latency refs/remotes/handoff/flat-latency
```

If the branch already exists, inspect it rather than forcing a reset. If bundle
verification reports missing prerequisite history, obtain the common ancestor
from the repository remote before fetching. An online alternative is to publish
and fetch the same integration commit; this integration did not push a branch.

Use a branch for rollback and preserve the old `.env`/`.env.local`, nginx config,
dashboard files, and ROS installation. Do not copy Jetson `node_modules`, ROS
binaries, or local environment files to the base; rebuild for the base architecture.

## Base changes

1. Use Node.js from `.nvmrc` (24.15.0), npm, and the base's existing ROS environment
   required by `rclnodejs`. Install missing system dependencies for this feature:

   ```bash
   sudo apt-get install chrony tcpdump libcap2-bin gstreamer1.0-tools \
     gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
   source /opt/ros/humble/setup.bash
   # Also source the base's existing ROS workspace as in its normal startup.
   npm --prefix base/gateway ci
   npm --prefix dashboard ci --include=dev
   npm --prefix base/gateway test
   npm --prefix dashboard run test:latency
   npm --prefix dashboard run check
   node scripts/latency/smoke_video.cjs > /tmp/base-latency-video-smoke.json
   node scripts/latency/smoke_video.cjs --late-join > /tmp/base-latency-video-late-join.json
   ```

   The smoke check generates four synthetic feeds on ephemeral loopback ports.
   It runs the real gateway video receiver and WebSocket path, requires no cameras,
   serial devices or CAN, and does not test browser rendering or radio latency.
   The late-join variant drops the initial keyframe before starting the receivers
   to check that both receiver branches recover when joining an active stream.

2. Merge [the flat example](../base/gateway/.env.latency-flat.example) into the
   existing ignored `base/gateway/.env.local`. Keep all unrelated settings.
   Set an **absolute** `VIDEO_CONFIG_PATH` to this checkout's
   `rover/ros2_ws/src/mr2_launch/config/video_streams_flat.json`, enable
   `MR2_LATENCY_DIAGNOSTICS_ENABLE=true`, and replace the interface/IP placeholders.
   Determine the actual interface with `ip route get <ROVER_IP>`.

   The configured stream map must be:

   | ID | UDP port |
   | --- | --- |
   | `flat_cam_1` | 5010 |
   | `flat_cam_2` | 5012 |
   | `flat_cam_3` | 5014 |
   | `flat_cam_4` | 5016 |

   Explicit gateway `--video-config` arguments override `VIDEO_CONFIG_PATH`;
   update the existing startup command/service if it supplies the original file.
   The flat selector on the rover does not configure the base gateway.

   Use `/tmp/mr2-latency-base` or a non-hidden writable directory such as
   `/home/mr2/latency-artifacts` for captures. Ubuntu's tcpdump AppArmor profile
   explicitly denies hidden home directories, including `~/.local/state`, even
   when file capabilities and Unix permissions are correct. Keep AppArmor
   enabled; select a permitted artifact directory instead.

3. Configure the two-host clock arrangement using the preview/validation/install
   procedure in [chrony/README.md](../scripts/latency/chrony/README.md).
   Install the base configuration first, then the rover's. Check capture capability
   on **both hosts** with:

   ```bash
   bash scripts/latency/install_capture_permissions.bash --check
   # If missing, provision once during host setup:
   bash scripts/latency/install_capture_permissions.bash
   ```

4. Deploy the rebuilt dashboard and both base nginx changes. The existing
   `bash scripts/deploy_dashboard.bash` performs dashboard installation and nginx
   generation/reload using repository `.env` settings. It changes host files;
   run it during the planned deployment after backing up custom nginx changes.
   Existing customized installations should merge these source changes instead:

   - `scripts/nginx/mr2-dashboard-locations.conf`: time, clock-status, and uplink
     proxy locations on the HTTPS dashboard.
   - `scripts/nginx/mr2-dashboard.conf.template`: HTTP `/latency/uplink/` upload
     location before the general HTTPS redirect, with a 256 MB body limit.

   Validate with `sudo nginx -t`, then reload nginx. Restart the existing base
   gateway through its normal supervisor or startup command with the new settings.
   Reload the browser to load the new dashboard bundle.

   The upload client rejects redirects and verifies HTTPS certificates. The
   documented `MR2_LATENCY_PUBLIC_BASE_URL=http://<BASE_IP>` uses the new HTTP
   upload route, including bearer-token-protected artifact uploads. Checking
   `/latency/uplink/status` on that same origin verifies the public route without
   creating a trial or capture. Do not substitute the Jetson/direct nginx template.

## Rover changes

Stop the running rover launch normally before installing/restarting these
packages. From the repository root, source the existing workspace and build:

```bash
source /opt/ros/humble/setup.bash
source rover/ros2_ws/install/setup.bash
cd rover/ros2_ws
colcon build --symlink-install --packages-select \
  mr2_latency_msgs mr2_latency_diagnostics mr2_system_status \
  mr2_video_streaming mr2_xbee_bridge mr2_launch
cd ../..
source rover/ros2_ws/install/setup.bash
./scripts/rover.bash start flat
```

On a machine without the previously built flat profile, build `mr2_flat_motor`
as well. These feature packages do not require selecting `mr2_control` or enabling
the original rover hardware. Reuse the existing CAN setup for actual driving.

For the first video/diagnostics acceptance run, set concrete values and launch:

```bash
export MR2_ROVER_ROCKET_INTERFACE=<ROVER_INTERFACE>
ros2 launch mr2_launch rover_real.launch.py \
  video_base_host:=<BASE_IP> \
  enable_latency_diagnostics:=true \
  enable_flat_motor:=false enable_gnss:=false
```

This uses the normal base-station XBee path while keeping the motor node off for
measurement checks. Restore the desired motor/GNSS flags for actual operation
after acceptance. The flat defaults keep the original hardware stack disabled.

Verify these ROS endpoints are present using `ros2 service list -t` and
`ros2 topic list -t` (read-only):

- `/latency/uplink/prepare`, `/latency/uplink/start`, `/latency/uplink/cancel`;
- `/video_streaming/acquire_stream_lease`, `/video_streaming/release_stream_lease`;
- `/system_status/clock` and `/latency/trace`.

The new message types are built on the rover, where rosbridge provides these
services to the browser; the base need not build the whole rover workspace for
this feature. Keep its existing ROS relay dependencies installed.

## Collect and compare deployment evidence

Run as the normal gateway/rover account after deployment, with the intended ROS
environment sourced on the rover. The preflight is read-only apart from its JSON
report: no capture, command publication, clock adjustment, or service restart.
Both invocations target the base's public upload origin:

```bash
# Base
python3 scripts/latency/preflight.py --role base \
  --interface <BASE_INTERFACE> --peer-ip <ROVER_IP> \
  --gateway-url http://<BASE_IP> --expected-commit "$(cat "$DELIVERY/REVISION")" \
  --report /tmp/base-latency-preflight.json

# Rover (set DELIVERY to its local copy of the handoff directory)
python3 scripts/latency/preflight.py --role rover \
  --interface <ROVER_INTERFACE> --peer-ip <BASE_IP> \
  --gateway-url http://<BASE_IP> --expected-commit "$(cat "$DELIVERY/REVISION")" \
  --report /tmp/rover-latency-preflight.json

# After copying both reports to one machine:
python3 scripts/latency/preflight.py --compare \
  /tmp/base-latency-preflight.json /tmp/rover-latency-preflight.json
```

Pass means the checkouts match the expected commit, the running gateway reports
that startup commit, both video files have the same canonical SHA-256, active
gateway IDs/ports match, all four feeds are available, capture capability exists,
peer routing uses the chosen NIC, and clock checks pass. A rover offset or error
bound above 2 ms fails. An HTTP redirect, original stream names on flat ports,
or stale gateway process fails. `--ca-file` supports a trusted HTTPS origin.

Package discovery and source hashes do not prove that every installed ROS binary
is current; the clean build/source/restart steps above and live ROS checks are
still required. File capabilities are checked without opening a capture socket;
service sandbox restrictions or capture drops can still fail the live trial.
Reports are point-in-time evidence, not a permanent readiness guarantee.

## Two-host acceptance and return package

1. Confirm all four feeds render at 320×180 in the HTTPS dashboard with the motor
   node disabled. Confirm readiness reports fresh synchronized clocks.
2. Run Uplink with **1, then 2, then 4 feeds**. Each selected feed must have matched
   rendered-frame samples and a completed report with finite segment values.
   Investigate clock-invalid/negative segments and capture-kernel-drop warnings;
   do not interpret those as valid radio measurements.
3. Cancel during capture. Verify capture processes exit, the previous stream
   selection returns, and another trial completes. Verify the same after a timeout.
   For selected-load comparisons, require `stream_lease_acquired: true` in rover
   metadata; the fallback can leave unselected streams transmitting.
4. With the motor still disabled, run Downlink using neutral then one deliberate
   manual input. Repeat in orbit mode, then verify mode switch/disconnect cancels
   a pending measurement without changing existing stop behavior.
5. Return `base-latency-preflight.json`, `rover-latency-preflight.json`, the base
   synthetic smoke result, exported downlink JSONL, each uplink JSON report and
   capture metadata, and any relevant gateway/rover errors. Preserve matching pcaps
   from the configured artifact directories for investigation. Keep environment
   files/credentials out of the evidence package.

Downlink ends at `/base/cmd_vel` publication, before control-loop/CAN/motor
response. Uplink starts at rover RTP capture, after camera exposure/encoding,
and ends at canvas draw completion. Single-host direct operation traverses
loopback/PTY and must not be reported as Rocket latency; this preflight rejects
loopback as the capture interface.

## Rollback

Stop the new launches/gateway normally, switch to each host's recorded previous
revision, restore its prior environment/nginx/dashboard files, and rebuild/source
the prior ROS packages before restarting. If chrony was reconfigured, use its
installer's timestamped configuration backup as documented in the chrony guide.
Restore custom deployment files from the backups taken above. Do not force-reset
a checkout with unrelated local work.

## Integration verification on the Jetson

- Gateway: 73 tests passed; dashboard: nine latency test modules plus typecheck,
  lint, and production build passed (existing large-bundle warning remains).
- Python: 93 tests passed (92 parser/preflight/profile/flat/control-launch tests
  and one ROS agent test against newly generated service types).
- Six ROS packages built in an isolated temporary overlay, using the existing
  workspace underlay; no deployed ROS installation was overwritten.
- Four-feed synthetic RTP → gateway → WebSocket smoke passed with at least 60
  timed frames per feed, 320×180 metadata, and no fallback chunks in that run.
- Actual base host installation, Rocket transport, browser draw timing, and
  physical cameras/motors were not exercised by these software checks.
