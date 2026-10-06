import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.baselines import run_isolation_forest, run_random_forest

def _data(n=200, seed=0):
    rng = np.random.default_rng(seed)
    benign = pd.DataFrame({"net_deauth": rng.normal(0,0.3,n), "act_motion": rng.normal(1,0.3,n)})
    attack = pd.DataFrame({"net_deauth": rng.normal(8,0.3,n), "act_motion": rng.normal(1,0.3,n)})
    X = pd.concat([benign, attack], ignore_index=True)
    y = pd.Series(["benign"]*n + ["deauth"]*n)
    return X, y

def test_rf_separates(tmp_path):
    X, y = _data(); Xtr, ytr = X.iloc[::2], y.iloc[::2]; Xte, yte = X.iloc[1::2], y.iloc[1::2]
    res = run_random_forest(Xtr, ytr, Xte, yte, seed=0)
    assert res["macro_f1"] > 0.9 and res["per_class"]["deauth"]["f1"] > 0.9

def test_if_flags_attacks():
    X, y = _data(); Xtr = X[y=="benign"].iloc[::2]
    Xte, yte = X.iloc[1::2], y.iloc[1::2]
    res = run_isolation_forest(Xtr, Xte, yte, fpr=0.10)
    assert res["per_attack_recall"]["deauth"] > 0.8 and res["benign_fpr"] <= 0.15
