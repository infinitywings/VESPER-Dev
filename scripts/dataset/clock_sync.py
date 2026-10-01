"""Align VM clock to the canonical Mac clock via seq-matched bridge events."""
import json
from statistics import median

def _load(path, ts_key):
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: r = json.loads(line)
            except Exception: continue
            if "seq" in r and ts_key in r: out[int(r["seq"])] = float(r[ts_key])
    return out

def compute_offset(mac_sync_path: str, vm_sync_path: str) -> float:
    mac = _load(mac_sync_path, "mac_ts"); vm = _load(vm_sync_path, "vm_ts")
    diffs = [vm[s] - mac[s] for s in mac.keys() & vm.keys()]
    if not diffs: raise ValueError("no seq-matched bridge events for clock sync")
    return float(median(diffs))

def to_canonical(t_vm: float, offset: float) -> float:
    return float(t_vm) - float(offset)
