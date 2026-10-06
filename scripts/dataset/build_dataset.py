"""Export every raw episode dir (<home>__<model>__<run>) into the dataset root."""
import os, sys, glob
from dataset.export_episode import export

def build(raw_root, out_root):
    for d in sorted(glob.glob(f"{raw_root}/*__*__*")):
        name = os.path.basename(d)
        parts = name.split("__")
        if len(parts) != 3:   # name-malformed dir: skip visibly, don't abort the whole batch
            print(f"SKIP {name}: malformed name (expected home__model__run)")
            continue
        home, model, run = parts
        try:
            export(d, f"{out_root}/episodes/{name}", home, model, run)
            print(f"exported {name}")
        except Exception as e:
            print(f"SKIP {name}: {e}")

if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
