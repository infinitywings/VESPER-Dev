#!/usr/bin/env python3
"""
VM-side device agent — turns 3D-driven device events into REAL 802.11 traffic.

Runs INSIDE the Linux VM (root namespace). Listens for device events forwarded
from the Mac-native Habitat 3D sim (JSON lines over TCP), and for each event
sends an MQTT-style UDP packet FROM the WiFi station (in netns ``ns-sta1``,
associated to the emulated WPA2 AP) TO the hub at 10.0.0.1. Because the station
is a real mac80211_hwsim radio joined to the AP, each send is a genuine 802.11
data frame on the emulated home network — so the embodied 3D activity actually
drives the wireless traffic that attacks target and tshark captures.

Each event triggers a one-shot ``ip netns exec`` send (robust: no long-lived
pipe/buffering). Prereq: the attackable WiFi is up (scripts/vm_wifi_net.sh) so
ns-sta1 exists and is associated. Run as root.
"""
import argparse
import json
import socket
import subprocess
import threading
import time


def send_from_station(netns: str, hub: str, port: int, payload: str) -> None:
    """One-shot UDP send from inside the station netns → real 802.11 frame."""
    code = (
        "import socket;"
        f"socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto({payload!r}.encode(),('{hub}',{port}))"
    )
    subprocess.run(["ip", "netns", "exec", netns, "python3", "-c", code],
                   timeout=3, stderr=subprocess.DEVNULL)


def write_sync(path: str, ev: dict) -> None:
    """Append a clock-sync record (VM clock) for a received bridge event."""
    rec = {"vm_ts": time.time(), "seq": int(ev.get("seq", -1)),
           "device": ev.get("device", "?"), "state": ev.get("state", "?"),
           "room": ev.get("room", "?")}
    with open(path, "a", buffering=1) as f:
        f.write(json.dumps(rec) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=6000)
    ap.add_argument("--netns", default="ns-sta1")
    ap.add_argument("--hub", default="10.0.0.1")
    ap.add_argument("--hub-port", type=int, default=1883)
    ap.add_argument("--sync-log", default=None)
    args = ap.parse_args()

    def handle(conn):
        with conn, conn.makefile() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    ev = {"raw": line}
                if args.sync_log and isinstance(ev, dict):
                    try: write_sync(args.sync_log, ev)
                    except Exception: pass
                msg = f"PUB dev={ev.get('device','?')} state={ev.get('state','?')} room={ev.get('room','?')}"
                try:
                    send_from_station(args.netns, args.hub, args.hub_port, msg)
                    print(f"[agent] 3D event -> 802.11 tx: {msg}", flush=True)
                except Exception as e:
                    print(f"[agent] send failed: {e}", flush=True)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.listen, args.port)); srv.listen(8)
    print(f"[agent] listening {args.listen}:{args.port} | station netns={args.netns} "
          f"| hub={args.hub}:{args.hub_port} (traffic traverses the emulated 802.11 link)", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
