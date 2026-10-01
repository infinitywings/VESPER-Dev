import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.event_log import DatasetEventLogger

class _Ev:
    def __init__(self, et, room=None, src=None):
        self.event_type = et; self.payload = {"room": room} if room else {}; self.source_id = src

def test_logs_event_with_context(tmp_path):
    log = DatasetEventLogger(str(tmp_path))
    log.set_context("102343992", "qwen2.5-7b-instruct", 1)
    log.log(_Ev("motion_detected", room="bedroom.004", src="motion_1"))
    log.close()
    lines = (tmp_path / "events.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["home"] == "102343992" and rec["model"] == "qwen2.5-7b-instruct"
    assert rec["run"] == 1 and rec["event_type"] == "motion_detected"
    assert rec["room"] == "bedroom.004" and rec["device"] == "motion_1"
    assert isinstance(rec["ts"], float) and rec["ts"] > 0

def test_bridge_sync_line(tmp_path):
    log = DatasetEventLogger(str(tmp_path))
    log.set_context("h", "m", 1)
    log.log_bridge_sync(seq=7, device="d", state="motion_detected", room="kitchen")
    log.close()
    rec = json.loads((tmp_path / "bridge_sync_mac.jsonl").read_text().strip())
    assert rec["seq"] == 7 and rec["room"] == "kitchen" and isinstance(rec["mac_ts"], float)
