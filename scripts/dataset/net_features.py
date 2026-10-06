"""Per-frame parse (via tshark) + per-window network feature block."""
import subprocess

_RF_FIELDS = ["frame.time_epoch","wlan.fc.type","wlan.fc.type_subtype","wlan.sa","frame.len"]
_AP_FIELDS = ["frame.time_epoch","arp","bootp.type","eth.src","ip.proto","tcp.flags.syn","tcp.dstport","frame.len"]

def _tshark(path, fields):
    cmd = ["tshark","-r",path,"-T","fields"] + sum([["-e",f] for f in fields], [])
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return [line.split("\t") for line in out.splitlines() if line.strip()]

def parse_pcap(path, kind):
    frames = []
    if kind == "rf":
        for r in _tshark(path, _RF_FIELDS):
            r = (r + [""]*5)[:5]
            try: ts = float(r[0])
            except Exception: continue
            frames.append({"ts": ts, "type": r[1], "subtype": r[2], "sa": r[3],
                           "len": int(r[4]) if r[4].isdigit() else 0})
    else:
        for r in _tshark(path, _AP_FIELDS):
            r = (r + [""]*8)[:8]
            try: ts = float(r[0])
            except Exception: continue
            frames.append({"ts": ts, "arp": bool(r[1]), "dhcp": r[2] != "",
                           "src": r[3], "syn": r[5] in ("1","True"),
                           "dport": r[6], "len": int(r[7]) if r[7].isdigit() else 0})
    return frames

def window_net(frames_rf, frames_ap, t0, t1, offset):
    f = lambda ts: (ts - offset)
    rf = [x for x in frames_rf if t0 <= f(x["ts"]) < t1]
    ap = [x for x in frames_ap if t0 <= f(x["ts"]) < t1]
    def cnt(xs, key): return sum(1 for x in xs if x.get("subtype") == key)
    srcs = {x.get("sa") for x in rf if x.get("sa")} | {x.get("src") for x in ap if x.get("src")}
    dports = {x["dport"] for x in ap if x.get("dport")}
    return {
        "net_total": len(rf) + len(ap),
        "net_mgmt": sum(1 for x in rf if x.get("type") == "0"), "net_data": len(ap),
        "net_beacon": cnt(rf, "0x0008"), "net_deauth": cnt(rf, "0x000c"),
        "net_probe": cnt(rf, "0x0004"), "net_disassoc": cnt(rf, "0x000a"),
        "net_arp": sum(1 for x in ap if x.get("arp")),
        "net_dhcp": sum(1 for x in ap if x.get("dhcp")),
        "net_unique_src": len(srcs - {None, ""}),
        "net_bytes": sum(x.get("len", 0) for x in rf + ap),
        "net_syn": sum(1 for x in ap if x.get("syn")),
        "net_unique_dports": len(dports - {None, ""}),
    }
