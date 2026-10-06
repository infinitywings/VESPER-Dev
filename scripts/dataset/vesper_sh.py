"""VESPER-SH loader + cross-environment split generation."""
import json, os, glob, random
import pandas as pd

def discover(root):
    eps = []
    for d in sorted(glob.glob(f"{root}/episodes/*")):
        name = os.path.basename(d)
        parts = name.split("__")
        if len(parts) != 3 or not os.path.exists(f"{d}/windows.parquet"): continue
        eps.append({"home": parts[0], "model": parts[1], "run": parts[2], "path": d})
    return eps

def make_splits(root, seed=0, test_frac=0.34, k=5):
    eps = discover(root)
    homes = sorted({e["home"] for e in eps}); models = sorted({e["model"] for e in eps})
    rng = random.Random(seed); rng.shuffle(homes)
    n_test = max(1, round(len(homes) * test_frac))
    test_h, train_h = set(homes[:n_test]), set(homes[n_test:])
    os.makedirs(f"{root}/splits/by_home", exist_ok=True)
    open(f"{root}/splits/by_home/train.txt","w").write("\n".join(sorted(train_h)))
    open(f"{root}/splits/by_home/test.txt","w").write("\n".join(sorted(test_h)))
    os.makedirs(f"{root}/splits/by_resident", exist_ok=True)
    if len(models) >= 2:
        open(f"{root}/splits/by_resident/train.txt","w").write("\n".join(models[:-1]))
        open(f"{root}/splits/by_resident/test.txt","w").write(models[-1])
    folds = []
    hl = sorted(homes); rng.shuffle(hl)
    for i in range(k):
        te = hl[i::k]; folds.append({"fold": i, "test_homes": te,
                                     "train_homes": [h for h in hl if h not in te]})
    json.dump(folds, open(f"{root}/splits/folds.json","w"), indent=2)

def load_xy(root, homes):
    homes = set(homes); Xs, ys, gs = [], [], []
    for e in discover(root):
        if e["home"] not in homes: continue
        w = pd.read_parquet(f"{e['path']}/windows.parquet")
        l = pd.read_csv(f"{e['path']}/labels.csv")
        m = w.merge(l[["window_idx","label"]], on="window_idx")
        feat = m.drop(columns=[c for c in ("window_idx","ts","label") if c in m.columns])
        Xs.append(feat); ys.append(m["label"]); gs.append(pd.Series([e["home"]]*len(m)))
    if not Xs: raise ValueError("no episodes for given homes")
    return (pd.concat(Xs, ignore_index=True), pd.concat(ys, ignore_index=True),
            pd.concat(gs, ignore_index=True))
