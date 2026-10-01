import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import vm_device_agent as A

def test_sync_record_shape(tmp_path):
    p = tmp_path / "bridge_sync_vm.jsonl"
    A.write_sync(str(p), {"device": "d", "state": "motion_detected", "room": "kitchen", "seq": 3})
    rec = json.loads(p.read_text().strip())
    assert rec["seq"] == 3 and rec["device"] == "d" and rec["room"] == "kitchen"
    assert isinstance(rec["vm_ts"], float) and rec["vm_ts"] > 0
