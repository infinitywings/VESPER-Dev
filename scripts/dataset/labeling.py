"""Per-window labels from the attack schedule (mapped to canonical clock)."""
import json

def load_schedule(path, offset):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: r = json.loads(line)
            except Exception: continue
            out.append({"class": r["class"],
                        "start": float(r["start_ts"]) - float(offset),
                        "end": float(r["end_ts"]) - float(offset)})
    out.sort(key=lambda x: x["start"])
    return out

def label_window(schedule, t0, t1):
    for s in schedule:                 # sorted by start; earliest-start wins
        if s["start"] < t1 and s["end"] > t0:
            return s["class"]
    return "benign"
