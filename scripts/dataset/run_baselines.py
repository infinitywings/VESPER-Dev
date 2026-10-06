"""Splits + IF/RF baselines + LaTeX tables from a dataset root."""
import json, os, sys
from dataset.vesper_sh import make_splits, load_xy, discover
from dataset.baselines import run_isolation_forest, run_random_forest

def _tex_escape(s): return str(s).replace("_", r"\_")

def main(root):
    make_splits(root, seed=0)
    train_h = open(f"{root}/splits/by_home/train.txt").read().split()
    test_h = open(f"{root}/splits/by_home/test.txt").read().split()
    Xtr, ytr, _ = load_xy(root, train_h)
    Xte, yte, _ = load_xy(root, test_h)
    Xtr_ben = Xtr[ytr == "benign"]
    rf = run_random_forest(Xtr, ytr, Xte, yte, seed=0)
    iff = run_isolation_forest(Xtr_ben, Xte, yte, fpr=0.01)
    res = {"random_forest": rf, "isolation_forest": iff,
           "n_train": int(len(Xtr)), "n_test": int(len(Xte))}
    os.makedirs(f"{root}/tables", exist_ok=True)
    json.dump(res, open(f"{root}/baseline_results.json", "w"), indent=2)
    # composition table
    eps = discover(root)
    homes = {e["home"] for e in eps}; models = {e["model"] for e in eps}
    with open(f"{root}/tables/composition.tex", "w") as f:
        f.write("\\begin{tabular}{lr}\\toprule\n")
        f.write(f"Episodes & {len(eps)} \\\\\n Homes & {len(homes)} \\\\\n Resident models & {len(models)} \\\\\n")
        f.write(f"Train windows & {len(Xtr)} \\\\\n Test windows & {len(Xte)} \\\\\n\\bottomrule\\end{{tabular}}\n")
    # baseline table (per-attack F1 from RF + recall from IF)
    with open(f"{root}/tables/baselines.tex", "w") as f:
        f.write("\\begin{tabular}{lrr}\\toprule\n Attack & RF F1 & IF recall \\\\\\midrule\n")
        for cls in sorted(k for k in rf["per_class"] if k != "benign"):
            f1 = rf["per_class"][cls]["f1"]; rec = iff["per_attack_recall"].get(cls, 0.0)
            f.write(f"{_tex_escape(cls)} & {f1:.2f} & {rec:.2f} \\\\\n")
        f.write(f"\\midrule Macro-F1 & {rf['macro_f1']:.2f} & --- \\\\\n")
        f.write(f"Benign FPR & --- & {iff['benign_fpr']:.2f} \\\\\n\\bottomrule\\end{{tabular}}\n")
    return res

if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1]), indent=2)[:500])
