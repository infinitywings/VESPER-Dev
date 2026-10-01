"""Activity + device feature blocks from the Mac-clock events.jsonl."""
import json

_MOTION = {"motion_detected"}
_TRANS = {"agent_entered_room", "agent_left_room"}
_DOOR = {"door_opened"}
_DEVCHG = {"device_state_changed", "state_change"}
_TAP = {"tap_fired"}
_FW = {"firmware_state_update"}

def load_events(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: out.append(json.loads(line))
            except Exception: pass
    return out

def window_events(events, t0, t1):
    w = [e for e in events if t0 <= float(e.get("ts", -1)) < t1]
    rooms = {e.get("room", "") for e in w if e.get("event_type") in _MOTION | _TRANS and e.get("room")}
    def c(types): return sum(1 for e in w if e.get("event_type") in types)
    return {
        "act_motion": c(_MOTION), "act_rooms": len(rooms),
        "act_transitions": c(_TRANS), "act_doors": c(_DOOR),
        "act_any": 1 if w else 0,
        "dev_state_changes": c(_DEVCHG), "dev_tap_firings": c(_TAP),
        "dev_firmware_updates": c(_FW),
    }
