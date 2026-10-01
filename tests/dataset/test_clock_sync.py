import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.clock_sync import compute_offset, to_canonical

def _w(p, rows):
    with open(p, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")

def test_offset_is_median_of_matched(tmp_path):
    mac = tmp_path/"m.jsonl"; vm = tmp_path/"v.jsonl"
    _w(mac, [{"mac_ts":100.0,"seq":1},{"mac_ts":101.0,"seq":2},{"mac_ts":102.0,"seq":3}])
    _w(vm,  [{"vm_ts":150.0,"seq":1},{"vm_ts":151.5,"seq":2},{"vm_ts":152.0,"seq":3}])  # offsets 50,50.5,50
    assert abs(compute_offset(str(mac), str(vm)) - 50.0) < 1e-6
    assert abs(to_canonical(152.0, 50.0) - 102.0) < 1e-6

def test_raises_without_matches(tmp_path):
    mac = tmp_path/"m.jsonl"; vm = tmp_path/"v.jsonl"
    _w(mac, [{"mac_ts":1.0,"seq":1}]); _w(vm, [{"vm_ts":9.0,"seq":99}])
    import pytest
    with pytest.raises(ValueError): compute_offset(str(mac), str(vm))
