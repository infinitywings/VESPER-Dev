import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.split_run import split_run
from dataset.net_features import parse_pcap
from scapy.all import wrpcap, RadioTap, Dot11, Dot11Deauth, Ether, ARP

def _rf(t):
    p = RadioTap()/Dot11(type=0, subtype=12, addr1="a", addr2="b", addr3="b")/Dot11Deauth(); p.time = t; return p
def _ap(t):
    p = Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(op=2, psrc="10.0.0.1", pdst="10.0.0.20", hwsrc="02:00:00:00:99:00"); p.time = t; return p

def test_split_two_homes(tmp_path):
    r = str(tmp_path/"run"); os.makedirs(r)
    with open(f"{r}/events.jsonl", "w") as f:
        for t in (100.0, 101.0, 102.0):
            f.write(json.dumps({"ts": t, "home": "homeA", "model": "qwen", "run": 1, "event_type": "motion_detected", "room": "k"})+"\n")
        for t in (200.0, 201.0, 202.0):
            f.write(json.dumps({"ts": t, "home": "homeB", "model": "qwen", "run": 1, "event_type": "motion_detected", "room": "k"})+"\n")
    open(f"{r}/bridge_sync_mac.jsonl", "w").write(json.dumps({"mac_ts": 100.0, "seq": 1})+"\n")
    open(f"{r}/bridge_sync_vm.jsonl", "w").write(json.dumps({"vm_ts": 150.0, "seq": 1})+"\n")  # offset 50
    with open(f"{r}/attack_schedule.jsonl", "w") as f:  # VM clock
        f.write(json.dumps({"class": "deauth", "round": 1, "start_ts": 151.0, "end_ts": 151.5})+"\n")     # homeA window
        f.write(json.dumps({"class": "arp_spoof", "round": 2, "start_ts": 251.0, "end_ts": 251.5})+"\n")  # homeB window
    wrpcap(f"{r}/rf.pcap", [_rf(151.2), _rf(251.2)])
    wrpcap(f"{r}/ap.pcap", [_ap(151.3), _ap(251.3)])

    out = str(tmp_path/"eps")
    names = split_run(r, out)
    assert set(names) == {"homeA__qwen__1", "homeB__qwen__1"}

    a = f"{out}/homeA__qwen__1"
    aev = [json.loads(l) for l in open(f"{a}/events.jsonl")]
    assert len(aev) == 3 and all(e["home"] == "homeA" for e in aev)
    asched = [json.loads(l) for l in open(f"{a}/attack_schedule.jsonl")]
    assert len(asched) == 1 and asched[0]["class"] == "deauth"          # only homeA's attack
    assert os.path.exists(f"{a}/bridge_sync_mac.jsonl") and os.path.exists(f"{a}/bridge_sync_vm.jsonl")
    assert len(parse_pcap(f"{a}/rf.pcap", "rf")) == 1                    # pcap sliced to homeA's window

    bsched = [json.loads(l) for l in open(f"{out}/homeB__qwen__1/attack_schedule.jsonl")]
    assert len(bsched) == 1 and bsched[0]["class"] == "arp_spoof"
