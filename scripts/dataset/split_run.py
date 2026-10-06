"""Split a combined coupled-run capture into per-episode dirs for build_dataset.

A full generation run writes ONE `events.jsonl` (each event tagged with home/model/run)
+ `bridge_sync_mac.jsonl` on the Mac, and ONE `ap.pcap` / `rf.pcap` /
`attack_schedule.jsonl` / `bridge_sync_vm.jsonl` on the VM. This slices them into
`<home>__<model>__<run>/` episode dirs: events by tag, and the pcaps + attack schedule
by that episode's canonical (Mac-clock) time range mapped to the VM clock via the
run-global clock offset. The run-global sync files are copied into each episode so the
exporter re-derives the same offset.
"""
import json, os, shutil, subprocess, collections
from dataset.clock_sync import compute_offset


def _load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _slice_pcap(src, dst, vm_t0, vm_t1):
    """Write frames of src whose epoch time is in [vm_t0, vm_t1] to dst (via tshark)."""
    if not os.path.exists(src):
        return
    flt = f"frame.time_epoch >= {vm_t0} && frame.time_epoch <= {vm_t1}"
    subprocess.run(["tshark", "-r", src, "-Y", flt, "-w", dst], capture_output=True, text=True)


def split_run(run_dir, out_dir, pad=2.0):
    """Split run_dir → per-episode dirs under out_dir. Returns the list of episode names."""
    events = _load_jsonl(f"{run_dir}/events.jsonl")
    mac_sync = f"{run_dir}/bridge_sync_mac.jsonl"
    vm_sync = f"{run_dir}/bridge_sync_vm.jsonl"
    try:
        offset = compute_offset(mac_sync, vm_sync)
    except Exception:
        offset = 0.0
    sched = _load_jsonl(f"{run_dir}/attack_schedule.jsonl")

    groups = collections.defaultdict(list)
    for e in events:
        key = (e.get("home", "unknown"), e.get("model", "unknown"), e.get("run", 1))
        groups[key].append(e)

    written = []
    for (home, model, run), evs in groups.items():
        ts = [float(e["ts"]) for e in evs if "ts" in e]
        if not ts:
            continue
        t0, t1 = min(ts) - pad, max(ts) + pad          # canonical (Mac) window
        vm_t0, vm_t1 = t0 + offset, t1 + offset         # VM-clock window (for pcaps/schedule)
        name = f"{home}__{model}__{run}"
        ed = f"{out_dir}/{name}"
        os.makedirs(ed, exist_ok=True)
        with open(f"{ed}/events.jsonl", "w") as f:
            for e in evs:
                f.write(json.dumps(e) + "\n")
        with open(f"{ed}/attack_schedule.jsonl", "w") as f:
            for s in sched:
                if float(s["start_ts"]) < vm_t1 and float(s["end_ts"]) > vm_t0:
                    f.write(json.dumps(s) + "\n")
        for sy in ("bridge_sync_mac.jsonl", "bridge_sync_vm.jsonl"):
            if os.path.exists(f"{run_dir}/{sy}"):
                shutil.copy(f"{run_dir}/{sy}", f"{ed}/{sy}")
        _slice_pcap(f"{run_dir}/ap.pcap", f"{ed}/ap.pcap", vm_t0, vm_t1)
        _slice_pcap(f"{run_dir}/rf.pcap", f"{ed}/rf.pcap", vm_t0, vm_t1)
        written.append(name)
    return written


if __name__ == "__main__":
    import sys
    print(split_run(sys.argv[1], sys.argv[2]))
