#!/usr/bin/env bash
# Clean, correctly-chained IoT-J WiFi experiment run (runs INSIDE the VM as root).
# RQ-N1 (15 trials, full) -> RQ-N2 (15 trials, 8 configs) -> DONE marker.
# Fresh logs/output dirs; no self-pgrep, no background sub-shells to stall on.
set -u
cd /home/ubuntu/vesper || exit 1
D=/home/ubuntu/iotj
mkdir -p "$D"
S="$D/chain.status"

echo "RQN1 start $(date)"                          >  "$S"
python3 scripts/run_rqn1_native.py --full --trials 15 --wmediumd \
        --output "$D/rqn1" > "$D/rqn1.log" 2>&1
echo "RQN1 end   $(date) (rc=$?)"                  >> "$S"

echo "RQN2 start $(date)"                          >> "$S"
python3 scripts/run_rqn2_native.py --full --trials 15 --wmediumd \
        --output "$D/rqn2" > "$D/rqn2.log" 2>&1
echo "RQN2 end   $(date) (rc=$?)"                  >> "$S"

touch "$D/DONE"
echo "ALL DONE   $(date)"                          >> "$S"
