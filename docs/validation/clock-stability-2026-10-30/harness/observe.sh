set -e
source /home/mr2/.nvm/nvm.sh
exec timeout --signal=TERM --kill-after=10s 410s node /tmp/mr2-clock-stability/live/observe.cjs
