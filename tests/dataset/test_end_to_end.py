import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.build_dataset import build
from dataset.run_baselines import main as run_baselines
from tests.dataset.test_export_episode import _mk   # reuse crafted episode

def test_full_pipeline(tmp_path):
    raw = tmp_path/"raw";
    for h in ("homeA","homeB","homeC"):
        _mk(str(raw/f"{h}__qwen__1"))
    out = str(tmp_path/"ds")
    build(str(raw), out)
    assert os.path.exists(f"{out}/episodes/homeA__qwen__1/windows.parquet")
    res = run_baselines(out)
    assert "random_forest" in res and "isolation_forest" in res
    assert os.path.exists(f"{out}/tables/baselines.tex")

def test_build_skips_name_malformed_dir(tmp_path):
    # a name-malformed raw dir (4 "__" parts) must be SKIPPED, not crash the whole batch,
    # so a co-located valid episode still exports. (bad name sorts before the valid one.)
    raw = tmp_path/"raw"
    _mk(str(raw/"homeA__qwen__1"))                        # valid
    os.makedirs(str(raw/"bad__x__y__z"), exist_ok=True)  # matches *__*__* glob, but 4 parts
    out = str(tmp_path/"ds")
    build(str(raw), out)                                 # must NOT raise
    assert os.path.exists(f"{out}/episodes/homeA__qwen__1/windows.parquet")
    assert not os.path.exists(f"{out}/episodes/bad__x__y__z/windows.parquet")
