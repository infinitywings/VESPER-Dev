import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.labeling import load_schedule, label_window

def test_overlap_labels(tmp_path):
    p = tmp_path/"sched.jsonl"
    with open(p,"w") as f:
        f.write(json.dumps({"class":"deauth","round":1,"start_ts":150.0,"end_ts":155.0})+"\n")
    sched = load_schedule(str(p), offset=50.0)   # -> canonical 100..105
    assert label_window(sched, 100.0, 101.0) == "deauth"
    assert label_window(sched, 104.9, 105.9) == "deauth"   # partial overlap
    assert label_window(sched, 106.0, 107.0) == "benign"
    assert label_window(sched, 98.0, 99.0) == "benign"
