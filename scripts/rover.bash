#!/usr/bin/env bash
# Select defaults for the next mr2_launch invocation; never start/stop hardware.
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 || $1 != start || ( $2 != flat && $2 != original ) ||
      ( $# == 3 && ( $2 != flat || $3 != --cameras-only ) ) ]]; then
  echo "Usage: $0 start {flat|original} [--cameras-only]" >&2
  exit 2
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
selection=$(mktemp "$repo_root/.mr2-rover-profile.XXXXXX")
trap 'rm -f "$selection"' EXIT
printf '%s\n' "$2" > "$selection"
mv -f "$selection" "$repo_root/.mr2-rover-profile"
options="$repo_root/.mr2-rover-profile.options.json"
if [[ ${3:-} == --cameras-only ]]; then
  printf '%s\n' '{"profile":"flat","common":{"enable_flat_motor":"false","enable_gnss":"false"}}' > "$selection"
  mv -f "$selection" "$options"
else
  rm -f "$options"
fi
printf 'Selected rover profile: %s\n' "$2"
[[ ${3:-} != --cameras-only ]] || echo 'Camera experiment: flat motor and GNSS disabled.'
echo 'Applies to the next ros2 launch mr2_launch invocation. No processes or network settings changed.'
