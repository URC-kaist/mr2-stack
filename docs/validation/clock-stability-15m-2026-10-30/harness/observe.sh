set -e
source /home/mr2/.nvm/nvm.sh
exec timeout --signal=TERM --kill-after=10s 1010s node /tmp/mr2-clock-stability/15m/observe.cjs
