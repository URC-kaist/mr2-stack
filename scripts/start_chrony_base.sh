#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

source /opt/ros/humble/setup.bash
source "$REPO_ROOT/rover/ros2_ws/install/setup.bash"

if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
  source "$HOME/.nvm/nvm.sh"
  cd "$REPO_ROOT"
  nvm use
fi

cd "$REPO_ROOT/base/gateway"
exec npm start -- "$@"
