import json, os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.vesper_sh import discover, make_splits, load_xy

def _episode(root, home, model, run, label):
    d = f"{root}/episodes/{home}__{model}__{run}"; os.makedirs(d, exist_ok=True)
    pd.DataFrame([{"window_idx":0,"ts":0.0,"act_motion":1,"net_total":2}]).to_parquet(f"{d}/windows.parquet", index=False)
    pd.DataFrame([{"window_idx":0,"ts":0.0,"label":label,"binary":0 if label=="benign" else 1}]).to_csv(f"{d}/labels.csv", index=False)
    json.dump({"home":home,"model":model,"run":run}, open(f"{d}/meta.json","w"))

def test_discover_and_load(tmp_path):
    root = str(tmp_path)
    for h in range(4):
        _episode(root, f"home{h}", "qwen", 1, "benign" if h%2 else "deauth")
    eps = discover(root); assert len(eps) == 4 and eps[0]["home"].startswith("home")
    make_splits(root, seed=0)
    tr = set(open(f"{root}/splits/by_home/train.txt").read().split())
    te = set(open(f"{root}/splits/by_home/test.txt").read().split())
    assert tr and te and not (tr & te)            # no home leakage (by_home)
    folds = json.load(open(f"{root}/splits/folds.json"))
    for f in folds:
        assert not (set(f["train_homes"]) & set(f["test_homes"]))   # no home leakage (per fold)
    X, y, g = load_xy(root, list(tr))
    assert len(X) == len(y) == len(g) and "act_motion" in X.columns
    assert not ({"window_idx", "ts", "label"} & set(X.columns))     # X has no index/ts/label leakage
