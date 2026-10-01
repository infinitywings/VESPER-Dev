"""Append-only JSONL logger for the VESPER-SH dataset (Mac-side, canonical clock)."""
import json, os, threading, time

class DatasetEventLogger:
    def __init__(self, out_dir: str):
        os.makedirs(out_dir, exist_ok=True)
        self._ev = open(os.path.join(out_dir, "events.jsonl"), "a", buffering=1)
        self._sy = open(os.path.join(out_dir, "bridge_sync_mac.jsonl"), "a", buffering=1)
        self._ctx = {"home": "unknown", "model": "unknown", "run": 0}
        self._lock = threading.Lock()

    def set_context(self, home: str, model: str, run: int) -> None:
        with self._lock:
            self._ctx = {"home": home, "model": model, "run": int(run)}

    def log(self, ev) -> None:
        try:
            payload = getattr(ev, "payload", None) or {}
            rec = {"ts": time.time(), **self._ctx,
                   "event_type": getattr(ev, "event_type", ""),
                   "room": payload.get("room", ""),
                   "device": getattr(ev, "source_id", None) or getattr(ev, "event_type", "")}
            with self._lock:
                self._ev.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def log_bridge_sync(self, seq: int, device: str, state: str, room: str) -> None:
        try:
            rec = {"mac_ts": time.time(), "seq": int(seq), "device": device,
                   "state": state, "room": room}
            with self._lock:
                self._sy.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def close(self) -> None:
        for f in (self._ev, self._sy):
            try: f.close()
            except Exception: pass
