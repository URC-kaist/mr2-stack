# Rocket field management addresses and XBee defaults — 2026-10-29

## Cause

The gateway's normal loaded configuration polls Base at 192.168.1.100, Rover
at 192.168.1.106, and Drone at 192.168.1.103. The two actual radios, identified
over SSH as `base-rocket` and `rover-rocket`, had only DHCP management addresses
192.168.0.244 and 192.168.0.245. Both run airOS XW.v6.2.0 in bridge mode.
Their configured DHCP fallback was the same 192.168.1.20. Switching only the
computers to field profiles did not install the gateway's field addresses on
the radios. Those expected addresses had no answering neighbor/management port.

The missing `/dev/ttySIK` was independent: an optional MAVProxy process was
enabled by default, while the rover's primary control link already used XBee.
The `no base survey-in fix` antenna message is a separate GNSS readiness issue.
Management polling failure alone is not proof that the radio data link is down.

## Applied repair

| Radio | Existing DHCP management | Added fixed management alias | Unique DHCP fallback |
| --- | --- | --- | --- |
| Base | 192.168.0.244/24 | 192.168.1.100/24 | 192.168.1.100/24 |
| Rover | 192.168.0.245/24 | 192.168.1.106/24 | 192.168.1.106/24 |

Each alias was added to `br0` without removing any existing address. Existing
DHCP operation remains available on the development network; the field alias
does not depend on a DHCP server. The unique fallback avoids both radios
falling back to the same address when DHCP is unavailable.

The installed firmware's UI/config schema was inspected to verify support for
`netconf.3.alias.1.{ip,netmask,status,comment}`. The supported management-alias
feature is also described in the manufacturer's
[airOS 6 guide](https://dl.ui.com/guides/airOS/airOS_6_UG.pdf).
After live authenticated polling succeeded through both old and new addresses,
the exact five changed config keys per device were verified against private
backups, written with `cfgmtd`, then read back from flash and checked.

No radio reboot, interface restart, wireless/AP/SSID change, host address
change, route change or firewall change was performed. Actual power-cycle
acceptance is outside this run; persistent flash content was verified.

Original complete radio configurations are backed up privately on base under
`~/.local/state/mr2-radio/`, mode 0600. Their hashes matched the originals read
from each device. They contain secrets and are deliberately excluded from this
evidence directory. `verified-diff.log` lists only the changed public keys/IPs.

The gateway default `mavproxyEnable` is now false. Its primary control port
remains `/dev/ttyXBEE` at 115200 baud. SiK/MAVLink remains an optional,
explicit `--mavproxy-enable true` path. It must not open the XBee control device.
Poll startup and failure logs now include each management IP to expose address
mismatches directly. Updated source and tests were applied to the actual base.

## Verification

* Both actual radios authenticated and returned numeric signal/noise values
  through their field addresses and their existing development addresses:
  `verified-live.json`.
* A real `npm start` process was exercised on a temporary HTTP port, a PTY
  instead of the physical XBee, and an unused temporary video UDP port. Antenna
  movement and the GNSS/RTCM relay were disabled for this fixture. Rocket target
  IPs and the MAVProxy default came from the normal loaded configuration.
  `/rocket-m2/status` returned Base/Rover `connected: true` at about -24 dBm.
  No MAVProxy or `/dev/ttySIK` attempt occurred: `gateway-smoke.json` and log.
* Full gateway suite: 121/121 pass on each host. Additional affected config,
  MAVProxy and Rocket tests passed after the log update.
* All test processes stopped; port 8081 is free for the operator. Existing
  developer Internet access was retained.

No device answered the configured Drone address 192.168.1.103; no third radio
was identified or reconfigured. Its status remains unreachable independently of
the repaired pair. When Drone is not used for a session, suppress that poller
explicitly with `npm start -- --drone-rocket ''`. The field Drone default is
preserved for sessions where the device is present.

With the computers using their field addresses, normal startup remains
`npm start` on base. Restart a gateway that was already running before the
source update. Development sessions can continue using their per-run IP flags.
