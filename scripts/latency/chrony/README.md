# MR2 offline clock policy

The supported configuration is described in
[offline-clock-policy.md](../../../docs/offline-clock-policy.md).

The base field address is `192.168.1.101`; rover is `192.168.1.102`.
Internet access is optional. Before/after a real rover ROS launch both hosts
may synchronize to public NTP. For the entire ROS launch lifetime Internet
corrections are locked out; rover slews only to a base with an acknowledged
clock lock. Simulation does not acquire this lock.

## Installation

Install chrony on each host and back up any existing custom settings. With real
ROS launches stopped, run `sudo python3 scripts/clock_policy/install.py --role
base` on base, then `--role rover` on rover. Installer flags `--dev-base-ip` and
`--dev-subnet` add development-network addresses/access without changing routes
or interface addresses. The installer backs up overwritten host configuration.

On rover, pass `--known-hosts-file` with the verified base SSH host key using
alias `mr2-clock-base`. Add rover's `/etc/mr2-clock/base_key.pub` to base user's
`~/.ssh/authorized_keys` with exactly this restriction before the public key:

```
restrict,command="/usr/local/bin/mr2-clock-client remote"
```

This key accepts clock lease requests only. No account password is stored.
Keep unrelated authorized keys. Service code is installed root-owned under
`/usr/local/lib/mr2-clock`; configuration is `/etc/mr2-clock/config.json`.

The old `configure_chrony.py` remains an idle-profile preview utility. It refuses
to overwrite a clock-manager installation; do not deploy a static idle profile
for operation that requires launch locks.

## Use and verify

Real `rover_real`, `rover_direct`, and real-mode `rover` launches acquire their
clock lock before starting nodes. No extra clock launch flag is needed. A
missing local manager blocks the launch with a clear error; a missing base
allows standalone operation with time held and measurements disabled.

```sh
systemctl status mr2-clock.service chrony.service --no-pager
mr2-clock-client status
chronyc -n sources -v
chronyc tracking
```

The manager tracks launch PID/start time and marked ROS children. The last
process exit releases the local lock; remote leases expire if disconnected.
Base's lock is renewed every ten seconds and expires after sixty seconds.
Rover removes a base source on renewal failure, before its lease expires.

Measurement readiness requires policy freshness, both clock locks, the actual
selected base source, and offset/error bounds <= 2 ms. A base local reference
supports relative synchronization and does not establish UTC accuracy.
Internet correction is allowed again only when no real launch remains.

RTC/disk recovery still provides boot-time approximate dates. Neither a saved
file nor offline synchronization can infer elapsed powered-off time. Verify RTC
backup power if correct offline UTC is required.
