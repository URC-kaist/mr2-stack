#!/usr/bin/env bash
# Select defaults for the next mr2_launch invocation; never start/stop hardware.
set -euo pipefail

if [[ $# != 2 || $1 != start || ( $2 != flat && $2 != original ) ]]; then
  echo "Usage: $0 start {flat|original}" >&2
  exit 2
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
selection=$(mktemp "$repo_root/.mr2-rover-profile.XXXXXX")
trap 'rm -f "$selection"' EXIT
printf '%s\n' "$2" > "$selection"
mv -f "$selection" "$repo_root/.mr2-rover-profile"
printf 'Selected rover profile: %s\n' "$2"
echo 'Applies to the next ros2 launch mr2_launch invocation. No processes or network settings changed.'
