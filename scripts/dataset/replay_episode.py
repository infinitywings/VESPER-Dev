"""Replay a raw episode's recorded activity to the VM device agent (:6000) live,
re-timestamped to NOW, so a fresh coupled capture aligns with a fresh events.jsonl
on one current clock. Writes fresh events.jsonl + bridge_sync_mac.jsonl into out_dir.

Drive from events.jsonl (per-episode, correct) NOT bridge_sync_mac.jsonl — the latter
is copied in RUN-GLOBAL by split_run.py (spans the whole ~10h run, not this episode).
Only the Wi-Fi-relevant event types are forwarded to the VM (matching the eval's
_WIFI_EVTS filter); the fresh bridge_sync_mac we write records our actual send times
so the exporter re-derives the tiny replay-time clock offset.

Why re-timestamp: export_episode builds a 1s-window grid over min..max(event_ts,
net_ts-offset) and refuses grids >1 week. Recorded events are days old; the fresh
capture is now. Shifting recorded events onto the replay wall-clock keeps the activity
*pattern* (identical relative timing -> identical activity/device features).

Usage: replay_episode.py <raw_episode_dir> <out_episode_dir> <vm_ip>
"""
import json
import os
import socket
import sys
import time

# must match run_autonomous_eval.py _WIFI_EVTS (events forwarded to the VM)
WIFI_EVTS = {"motion_detected", "agent_entered_room", "agent_left_room",
             "door_opened", "state_change", "device_state_changed",
             "firmware_state_update"}
MAX_SPAN = 1800  # s; a per-episode span above this means we grabbed the wrong log


def _load_jsonl(path):
    out = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    return out


def replay(raw_dir, out_dir, vm_ip, lead=3.0):
    os.makedirs(out_dir, exist_ok=True)
    events = [e for e in _load_jsonl(f"{raw_dir}/events.jsonl") if "ts" in e]
    if not events:
        raise SystemExit("no events to replay")
    ev_ts = [e["ts"] for e in events]
    t0 = min(ev_ts)
    span = max(ev_ts) - t0
    if span > MAX_SPAN:
        raise SystemExit(f"episode span {span:.0f}s > {MAX_SPAN}s — refusing (wrong/global log?)")
    now0 = time.time() + lead

    # fresh events.jsonl, shifted onto the replay wall-clock (same relative structure)
    with open(f"{out_dir}/events.jsonl", "w") as f:
        for e in events:
            e2 = dict(e)
            e2["ts"] = now0 + (e2["ts"] - t0)
            f.write(json.dumps(e2) + "\n")

    wifi = sorted((e for e in events if e.get("event_type") in WIFI_EVTS),
                  key=lambda e: e["ts"])
    conn = socket.create_connection((vm_ip, 6000), timeout=5)
    sent = 0
    with open(f"{out_dir}/bridge_sync_mac.jsonl", "w", buffering=1) as ms:
        for seq, e in enumerate(wifi, 1):
            target = now0 + (e["ts"] - t0)
            dt = target - time.time()
            if dt > 0:
                time.sleep(dt)
            send_ts = time.time()
            dev = e.get("device") or e.get("event_type")
            room = e.get("room", "")
            msg = json.dumps({"device": dev, "state": e.get("event_type"),
                              "room": room, "seq": seq}) + "\n"
            try:
                conn.sendall(msg.encode())
                sent += 1
            except Exception as ex:
                print(f"[replay] send failed at seq={seq}: {ex}", flush=True)
                break
            ms.write(json.dumps({"mac_ts": send_ts, "seq": seq, "device": dev,
                                 "state": e.get("event_type"), "room": room}) + "\n")
    conn.close()
    print(f"[replay] {os.path.basename(raw_dir)}: {len(events)} events "
          f"({sent}/{len(wifi)} wifi forwarded) over ~{span:.0f}s", flush=True)


if __name__ == "__main__":
    replay(sys.argv[1], sys.argv[2], sys.argv[3])
