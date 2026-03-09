"""
Shared display helpers, colour codes, and low-level TCP utilities used by
all five VESPER ESP32 attack modules.
"""

import socket
import time
from typing import Optional   # noqa: F401  (re-exported for attack modules)

# ── ANSI colour / style codes ─────────────────────────────────────────────────
GREEN   = "\033[92m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
CYAN    = "\033[96m"
MAGENTA = "\033[95m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"

# Script-start epoch; each module resets this in its own main() so timestamps
# are relative to when *that* attack started.
_T0: float = time.time()

def reset_clock():
    global _T0
    _T0 = time.time()


# ── wire-level display helpers ────────────────────────────────────────────────

def _ts() -> str:
    """Relative timestamp string like  [+ 2.341s] """
    return f"{DIM}[+{time.time()-_T0:6.3f}s]{RESET} "


def tx(cmd: str, via: str = ""):
    """Print a command going hub → device."""
    tag = f"  {DIM}via {via}{RESET}" if via else ""
    print(f"  {_ts()}{CYAN}→ HUB:{RESET}{tag}    {BOLD}{cmd}{RESET}")


def rx(resp: str, source: str = "ESP32"):
    """Print each response line annotated with known sensitive fields."""
    lines = [ln for ln in resp.strip().splitlines() if ln]
    if not lines:
        return
    for i, line in enumerate(lines):
        ts_part  = _ts()          if i == 0 else " " * 13
        src_part = f"{GREEN}← {source}:{RESET}  " if i == 0 else " " * (len(source) + 5)
        note     = annotate(line)
        print(f"  {ts_part}{src_part}{line}{note}")


def annotate(line: str) -> str:
    """Return a coloured side-note for known sensitive / state-change fields."""
    if "TOKEN:" in line:
        val = line.split("TOKEN:", 1)[1].strip()
        return (f"   {RED}{BOLD}◄ AUTH TOKEN LEAKED{RESET}" if val
                else f"   {DIM}◄ token empty (call AUTH: first){RESET}")
    if "SEED:" in line:
        return f"   {RED}{BOLD}◄ PRNG SEED LEAKED  — enables state prediction{RESET}"
    if "WIFI_IP:" in line:
        return f"   {YELLOW}◄ internal IP leaked{RESET}"
    if "TICKS:" in line:
        return f"   {DIM}◄ uptime exposed{RESET}"
    if "DEBUG:MEMORY_DUMP" in line:
        return f"   {RED}◄ DUMP START — no auth required!{RESET}"
    if "ARMED:yes" in line:
        return f"   {GREEN}◄ device is ARMED{RESET}"
    if "ARMED:no" in line:
        return f"   {RED}◄ device is DISARMED{RESET}"
    if "OVERFLOW" in line:
        return f"   {RED}{BOLD}◄ BUFFER OVERFLOW — stack corrupted!{RESET}"
    if "PWNED" in line or "VESPER_PWNED" in line:
        return f"   {RED}{BOLD}◄ SHELLCODE EXECUTED — RCE confirmed!{RESET}"
    if "FAULT" in line or "CRASH" in line:
        return f"   {RED}◄ device fault / crash{RESET}"
    return ""


def state_box(state: str):
    """Print a coloured ARM / DISARM state banner."""
    if state.upper() == "ARMED":
        print(f"\n  ╔══════════════════════════╗")
        print(f"  ║  Device State: {GREEN}{BOLD}ARMED  🟢{RESET}  ║")
        print(f"  ╚══════════════════════════╝\n")
    else:
        print(f"\n  ╔════════════════════════════════╗")
        print(f"  ║  Device State: {RED}{BOLD}DISARMED  🔴{RESET}  ║")
        print(f"  ╚════════════════════════════════╝\n")


def section(title: str, subtitle: str = "", width: int = 64):
    """Print a framed attack-section header."""
    print(f"\n  ┌{'─'*width}┐")
    pad = width - 2 - len(title)
    print(f"  │  {BOLD}{title}{RESET}{' '*max(pad,0)}│")
    if subtitle:
        pad2 = width - 2 - len(subtitle)
        print(f"  │  {DIM}{subtitle}{RESET}{' '*max(pad2,0)}│")
    print(f"  └{'─'*width}┘\n")


def divider(label: str = ""):
    """Print a light horizontal divider with an optional label."""
    if label:
        print(f"  {DIM}── {label} {'─'*(52-len(label))}{RESET}")
    else:
        print(f"  {DIM}{'─'*56}{RESET}")


def progress_bar(elapsed: float, total: float, width: int = 28) -> str:
    """Return a Unicode block filled/empty progress bar string."""
    filled = int(width * min(elapsed / total, 1.0))
    bar    = "█" * filled + "░" * (width - filled)
    pct    = int(100 * min(elapsed / total, 1.0))
    return f"[{bar}] {elapsed:4.1f}s / {total:.0f}s  ({pct}%)"


# ── low-level TCP helper ──────────────────────────────────────────────────────

def send(host: str, port: int, cmd: str, timeout: float = 4.0) -> str:
    """Open a fresh TCP connection, drain the banner, send cmd, return response."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        time.sleep(0.15)
        try:
            sock.recv(1024)          # drain connection banner
        except socket.timeout:
            pass
        sock.sendall((cmd + "\n").encode())
        buf      = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"ACK" in buf or b"ERROR" in buf or buf.count(b"\n") >= 2:
                    break
            except socket.timeout:
                break
        sock.close()
        return buf.decode("utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: {e}"


# ── connection check ──────────────────────────────────────────────────────────

def verify_connection(host: str, port: int) -> bool:
    print(f"Verifying connection to {host}:{port} ...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect((host, port))
        time.sleep(0.2)
        try:
            resp = sock.recv(1024).decode("utf-8", errors="replace")
        except socket.timeout:
            resp = ""
        sock.close()
        if "VESPER" in resp or "READY" in resp or "BOOTED" in resp:
            print(f"{GREEN}✓ ESP32 connected and responding{RESET}\n")
            print(f"Device banner:\n{resp.strip()}\n")
            return True
        print(f"⚠️  Connected but unexpected response: {resp[:60]}")
        return True
    except socket.timeout:
        print(f"{RED}✗ Connection timeout — is ESP32 powered on?{RESET}")
        return False
    except ConnectionRefusedError:
        print(f"{RED}✗ Connection refused — is the firmware running?{RESET}")
        return False
    except Exception as e:
        print(f"{RED}✗ {e}{RESET}")
        return False


# ── result display ────────────────────────────────────────────────────────────

def print_result(result: dict, idx: int = 0, total: int = 0):
    ok        = result["success"]
    status    = f"{GREEN}✓ SUCCESS{RESET}" if ok else f"{RED}✗ FAILED{RESET}"
    sev_color = RED if result["severity"] == "CRITICAL" else YELLOW
    header    = f"[{idx}/{total}] " if idx else ""
    print(f"\n{header}Result — {result['name']}")
    print(f"  Severity: {sev_color}{result['severity']}{RESET}  (CVSS {result.get('cvss', 0):.1f})")
    print(f"  Status:   {status}")
    if result.get("evidence"):
        print("  Evidence:")
        for i, ev in enumerate(result["evidence"][:5], 1):
            print(f"    {i}. {ev}")
    print(f"  Impact:   {result['impact']}")
    print(f"  Duration: {result['duration_ms']:.0f}ms\n")
