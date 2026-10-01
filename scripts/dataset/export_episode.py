"""Assemble one episode: raw logs+pcaps -> windows.parquet + labels.csv + meta.json."""
import json, math, os
import pandas as pd
from dataset.clock_sync import compute_offset
from dataset.net_features import parse_pcap, window_net
from dataset.event_features import load_events, window_events
from dataset.labeling import load_schedule, label_window

def export(episode_in, episode_out, home, model, run):
    os.makedirs(episode_out, exist_ok=True)
    mac_sync = f"{episode_in}/bridge_sync_mac.jsonl"
    vm_sync = f"{episode_in}/bridge_sync_vm.jsonl"
    if os.path.exists(mac_sync) and os.path.exists(vm_sync):
        # both present → a failure here means broken/mismatched sync; let it raise (don't mask corruption)
        offset = compute_offset(mac_sync, vm_sync)
    else:
        offset = 0.0  # genuinely sync-less episode: best-effort, canonical == VM clock
    events = load_events(f"{episode_in}/events.jsonl") if os.path.exists(f"{episode_in}/events.jsonl") else []
    rf = parse_pcap(f"{episode_in}/rf.pcap", "rf") if os.path.exists(f"{episode_in}/rf.pcap") else []
    ap = parse_pcap(f"{episode_in}/ap.pcap", "ap") if os.path.exists(f"{episode_in}/ap.pcap") else []
    sched = load_schedule(f"{episode_in}/attack_schedule.jsonl", offset) if os.path.exists(f"{episode_in}/attack_schedule.jsonl") else []

    ev_ts = [float(e["ts"]) for e in events if "ts" in e]
    net_ts = [x["ts"] - offset for x in rf + ap]
    all_ts = ev_ts + net_ts
    if not all_ts:
        raise ValueError("empty episode: no events or frames")
    lo = math.floor(min(all_ts)); hi = math.ceil(max(all_ts))
    if hi - lo > 604800:  # >1 week of 1s windows → clocks misaligned; fail loudly, don't OOM
        raise ValueError(f"window grid too large ({hi - lo}s) — check clock alignment/offset")

    rows, labels = [], []
    for i, t0 in enumerate(range(lo, max(hi, lo + 1))):
        t1 = t0 + 1
        feat = {"window_idx": i, "ts": float(t0)}
        feat.update(window_events(events, t0, t1))
        feat.update(window_net(rf, ap, t0, t1, offset))
        lab = label_window(sched, t0, t1)
        rows.append(feat)
        labels.append({"window_idx": i, "ts": float(t0), "label": lab,
                       "binary": 0 if lab == "benign" else 1})
    df = pd.DataFrame(rows); lab_df = pd.DataFrame(labels)
    df.to_parquet(f"{episode_out}/windows.parquet", index=False)
    lab_df.to_csv(f"{episode_out}/labels.csv", index=False)
    meta = {"home": home, "model": model, "run": int(run), "offset": offset,
            "n_windows": len(df),
            "class_counts": lab_df["label"].value_counts().to_dict()}
    json.dump(meta, open(f"{episode_out}/meta.json", "w"), indent=2)
    return meta
