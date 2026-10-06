import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from dataset.event_features import window_events

def test_activity_and_device_counts():
    evs = [
        {"ts":10.1,"event_type":"motion_detected","room":"kitchen"},
        {"ts":10.5,"event_type":"agent_entered_room","room":"kitchen"},
        {"ts":10.9,"event_type":"door_opened","room":"kitchen"},
        {"ts":10.7,"event_type":"device_state_changed","room":"kitchen"},
        {"ts":10.8,"event_type":"tap_fired","room":"kitchen"},
        {"ts":99.0,"event_type":"motion_detected","room":"den"},  # outside window
    ]
    w = window_events(evs, 10.0, 11.0)
    assert w["act_motion"] == 1 and w["act_doors"] == 1 and w["act_transitions"] == 1
    assert w["act_rooms"] == 1 and w["act_any"] == 1
    assert w["dev_state_changes"] == 1 and w["dev_tap_firings"] == 1
