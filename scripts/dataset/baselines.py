"""IsolationForest (unsupervised) + RandomForest (supervised) baselines."""
import numpy as np
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import precision_recall_fscore_support, f1_score, confusion_matrix

def run_isolation_forest(Xtr, Xte, yte, fpr=0.01, seed=0):
    sc = StandardScaler().fit(Xtr.values)
    clf = IsolationForest(random_state=seed, n_estimators=200).fit(sc.transform(Xtr.values))
    s_tr = clf.score_samples(sc.transform(Xtr.values))
    thr = np.quantile(s_tr, fpr)                  # lower score = more anomalous
    s_te = clf.score_samples(sc.transform(Xte.values))
    pred_attack = s_te < thr
    yte = np.asarray(yte)
    benign_mask = yte == "benign"
    benign_fpr = float(pred_attack[benign_mask].mean()) if benign_mask.any() else 0.0
    per = {}
    for cls in sorted(set(yte) - {"benign"}):
        m = yte == cls
        per[cls] = float(pred_attack[m].mean()) if m.any() else 0.0
    return {"benign_fpr": benign_fpr, "per_attack_recall": per, "threshold": float(thr)}

def run_random_forest(Xtr, ytr, Xte, yte, seed=0):
    sc = StandardScaler().fit(Xtr.values)
    clf = RandomForestClassifier(n_estimators=300, random_state=seed, n_jobs=-1)
    clf.fit(sc.transform(Xtr.values), np.asarray(ytr))
    pred = clf.predict(sc.transform(Xte.values))
    yte = np.asarray(yte)
    labels = sorted(set(yte) | set(pred))
    p, r, f, _ = precision_recall_fscore_support(yte, pred, labels=labels, zero_division=0)
    per = {labels[i]: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i])}
           for i in range(len(labels))}
    return {"per_class": per,
            "macro_f1": float(f1_score(yte, pred, average="macro", zero_division=0)),
            "labels": labels,
            "confusion": confusion_matrix(yte, pred, labels=labels).tolist()}
