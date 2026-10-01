import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.net_features import parse_pcap, window_net
from scapy.all import wrpcap, RadioTap, Dot11, Dot11Deauth, Dot11Beacon, Ether, ARP

def test_rf_parse_and_window(tmp_path):
    p = str(tmp_path/"rf.pcap")
    pkts = [RadioTap()/Dot11(type=0,subtype=12,addr1="a",addr2="b",addr3="b")/Dot11Deauth() for _ in range(3)]
    pkts += [RadioTap()/Dot11(type=0,subtype=8,addr1="a",addr2="b",addr3="b")/Dot11Beacon()]
    wrpcap(p, pkts)
    frames = parse_pcap(p, "rf")
    assert len(frames) == 4
    t0 = min(f["ts"] for f in frames); w = window_net(frames, [], t0, t0+3600, 0.0)
    assert w["net_deauth"] == 3 and w["net_beacon"] == 1

def test_ap_parse_arp(tmp_path):
    p = str(tmp_path/"ap.pcap")
    wrpcap(p, [Ether()/ARP(op=2) for _ in range(5)])
    frames = parse_pcap(p, "ap")
    t0 = min(f["ts"] for f in frames); w = window_net([], frames, t0, t0+3600, 0.0)
    assert w["net_arp"] == 5

def test_net_mgmt_counts_only_management(tmp_path):
    from scapy.all import wrpcap, RadioTap, Dot11, Dot11Deauth
    p = str(tmp_path/"rf_mixed.pcap")
    mgmt = [RadioTap()/Dot11(type=0, subtype=12, addr1="a", addr2="b", addr3="b")/Dot11Deauth() for _ in range(2)]  # 2 management (deauth)
    data = [RadioTap()/Dot11(type=2, subtype=0, addr1="a", addr2="b", addr3="b")/b"payload"]                        # 1 data
    wrpcap(p, mgmt + data)
    frames = parse_pcap(p, "rf")
    t0 = min(f["ts"] for f in frames); w = window_net(frames, [], t0, t0+3600, 0.0)
    assert w["net_mgmt"] == 2          # NOT 3 — data frame excluded
    assert w["net_total"] == 3         # all frames still counted in total
