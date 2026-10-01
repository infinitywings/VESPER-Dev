import json, os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.export_episode import export
from scapy.all import wrpcap, RadioTap, Dot11, Dot11Deauth, Ether, ARP

def _mk(tmp):
    os.makedirs(tmp, exist_ok=True)
    # sync: mac seq1@100, vm seq1@150 -> offset 50
    open(f"{tmp}/bridge_sync_mac.jsonl","w").write(json.dumps({"mac_ts":100.0,"seq":1})+"\n")
    open(f"{tmp}/bridge_sync_vm.jsonl","w").write(json.dumps({"vm_ts":150.0,"seq":1})+"\n")
    with open(f"{tmp}/events.jsonl","w") as f:
        for t in (100.2,100.6,101.3):
            f.write(json.dumps({"ts":t,"event_type":"motion_detected","room":"kitchen"})+"\n")
    # attack at vm 152..153 -> canonical 102..103
    open(f"{tmp}/attack_schedule.jsonl","w").write(json.dumps({"class":"deauth","round":1,"start_ts":152.0,"end_ts":153.0})+"\n")
    # IMPORTANT: stamp frame times at VM-clock 152.5 (inside the attack window) so after
    # offset (-50) they land at canonical 102.5, next to the events. Without an explicit
    # .time, scapy stamps frames at wall-clock ~now (~1.7e9); the grid would then span
    # 100..1.7e9 -> ~1.7 billion 1s windows -> hang/OOM.
    _rf = RadioTap()/Dot11(type=0,subtype=12,addr1="a",addr2="b",addr3="b")/Dot11Deauth(); _rf.time = 152.5
    wrpcap(f"{tmp}/rf.pcap", [_rf])
    _ap = Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(op=2, psrc="10.0.0.1", pdst="10.0.0.20", hwsrc="02:00:00:00:99:00"); _ap.time = 152.5
    wrpcap(f"{tmp}/ap.pcap", [_ap])

def test_export_produces_windows_and_labels(tmp_path):
    ein = str(tmp_path/"ep"); eout = str(tmp_path/"out"); _mk(ein)
    export(ein, eout, home="102343992", model="qwen", run=1)
    df = pd.read_parquet(f"{eout}/windows.parquet")
    lab = pd.read_csv(f"{eout}/labels.csv")
    meta = json.load(open(f"{eout}/meta.json"))
    assert len(df) == len(lab) and len(df) >= 3
    assert set(["window_idx","ts"]).issubset(df.columns)
    assert any(c.startswith("act_") for c in df.columns) and any(c.startswith("net_") for c in df.columns)
    assert "deauth" in set(lab["label"]) and "benign" in set(lab["label"])
    assert meta["home"] == "102343992" and abs(meta["offset"] - 50.0) < 1e-6

def test_broken_sync_raises(tmp_path):
    import pytest, json
    ein = str(tmp_path/"ep"); eout = str(tmp_path/"out"); _mk(ein)
    # sync files exist but seqs don't match (mac has seq=1, make vm seq=999) → unmatchable
    open(f"{ein}/bridge_sync_vm.jsonl","w").write(json.dumps({"vm_ts":150.0,"seq":999})+"\n")
    with pytest.raises(ValueError):
        export(ein, eout, home="h", model="m", run=1)
