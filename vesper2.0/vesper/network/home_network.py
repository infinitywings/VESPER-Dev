"""
VESPER Simulated Home Network

Creates a virtual home network topology for IoT security testing.
Models a realistic smart home network with:

- WiFi Access Point (virtual bridge)
- IoT subnet with device VLANs
- MQTT broker for device communication  
- Protocol bridges (Zigbee↔MQTT, TCP↔MQTT)
- Network monitoring and packet capture
- Configurable latency, packet loss, bandwidth
- Docker macvlan/bridge networking for Wireshark capture

Network Topology:
    
    [Cloud/Internet]
          |
    [Home Router / Gateway]  ← vesper-gateway
          |
    ┌─────┼─────────────────────────────────────┐
    │  IoT Network (configurable)               │
    │                                            │
    │  Mode A — bridge (default, isolated):      │
    │    docker0 bridge ──► container IPs        │
    │    Wireshark captures on docker0 / veth    │
    │                                            │
    │  Mode B — macvlan (real LAN):              │
    │    WiFi/eth parent ──► containers get      │
    │    real LAN IPs visible to router/AP       │
    │    Wireshark captures on parent interface  │
    │                                            │
    │  [MQTT Broker]  ← vesper-mqtt              │
    │     |   |   |                              │
    │  [Dev1][Dev2][Dev3] ...                    │
    │  QEMU  QEMU  QEMU                         │
    │                                            │
    │  [Protocol Bridge]                         │
    │  Zigbee/Z-Wave/BLE simulation              │
    └────────────────────────────────────────────┘
"""

import asyncio
import json
import logging
import os
import shutil
import socket
import time
import threading
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class Protocol(Enum):
    """Supported IoT communication protocols."""
    TCP = "tcp"
    MQTT = "mqtt"
    ZIGBEE = "zigbee"
    ZWAVE = "zwave"
    BLE = "ble"
    COAP = "coap"
    HTTP = "http"


class DeviceNetworkState(Enum):
    """Network state of a device."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    ERROR = "error"


class NetworkMode(Enum):
    """Docker network driver mode."""
    BRIDGE = "bridge"           # Default isolated bridge (captures on docker0/veth)
    MACVLAN = "macvlan"         # Containers get real LAN IPs (captures on parent iface)
    IPVLAN = "ipvlan"           # Like macvlan but shares parent MAC
    HOST = "host"               # Containers share host network stack


@dataclass
class WiresharkConfig:
    """Packet-capture / Wireshark integration settings."""
    enabled: bool = False
    # Network interface for live capture (e.g. "en0", "eth0", "docker0")
    capture_interface: str = ""
    # BPF filter applied to live capture (e.g. "tcp port 1883 or tcp port 5555")
    capture_filter: str = ""
    # Directory to store .pcap files
    pcap_dir: str = "logs/pcap"
    # Rotate capture file every N seconds (0 = single file)
    rotate_seconds: int = 0
    # Enable ring buffer with N files
    ring_buffer_count: int = 0
    # tshark / dumpcap binary path (auto-detected if empty)
    tshark_path: str = ""
    dumpcap_path: str = ""


@dataclass
class NetworkConfig:
    """Configuration for the simulated home network."""
    # ── Network mode ──────────────────────────────────────────────────────────
    mode: NetworkMode = NetworkMode.BRIDGE

    # ── Network addressing ────────────────────────────────────────────────────
    subnet: str = "172.20.0.0/24"
    gateway_ip: str = "172.20.0.1"
    mqtt_broker_ip: str = "172.20.0.2"
    dns_ip: str = "172.20.0.1"
    device_ip_start: str = "172.20.0.10"
    
    # ── MQTT broker config ────────────────────────────────────────────────────
    mqtt_port: int = 1883
    mqtt_ws_port: int = 9001
    mqtt_enable_auth: bool = False
    mqtt_username: str = "vesper"
    mqtt_password: str = "vesper_iot"
    
    # ── Network simulation parameters ─────────────────────────────────────────
    latency_ms: float = 5.0           # Base latency (ms)
    latency_jitter_ms: float = 2.0    # Latency jitter
    packet_loss_rate: float = 0.0     # 0.0 - 1.0
    bandwidth_bps: int = 100_000_000  # 100 Mbps
    
    # ── Protocol distribution ─────────────────────────────────────────────────
    zigbee_enabled: bool = True
    zwave_enabled: bool = False
    ble_enabled: bool = False
    
    # ── Docker network ────────────────────────────────────────────────────────
    docker_network_name: str = "vesper-home-network"
    docker_network_driver: str = "bridge"       # overridden by mode
    
    # ── macvlan / ipvlan specific ─────────────────────────────────────────────
    parent_interface: str = ""          # e.g. "en0", "eth0", "wlan0"
    macvlan_mode: str = "bridge"        # bridge | vepa | passthru | private
    aux_addresses: Dict[str, str] = field(default_factory=dict)  # reserved IPs
    
    # ── Wireshark / packet capture ────────────────────────────────────────────
    wireshark: WiresharkConfig = field(default_factory=WiresharkConfig)


@dataclass
class NetworkDevice:
    """A device on the simulated network."""
    device_id: str
    ip_address: str
    mac_address: str
    protocol: Protocol
    state: DeviceNetworkState = DeviceNetworkState.DISCONNECTED
    tcp_port: int = 0
    mqtt_topics: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Traffic stats
    bytes_sent: int = 0
    bytes_received: int = 0
    packets_sent: int = 0
    packets_received: int = 0
    last_seen: float = 0.0


@dataclass
class NetworkPacket:
    """A captured network packet."""
    timestamp: float
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: str
    payload: bytes
    size: int
    direction: str = "outbound"  # inbound/outbound


class MQTTBrokerSimulator:
    """
    Lightweight MQTT broker simulation for the virtual network.
    Handles publish/subscribe, topic matching, QoS levels.
    """

    def __init__(self, config: NetworkConfig):
        self.config = config
        self.subscriptions: Dict[str, List[Tuple[str, Callable]]] = {}  # topic -> [(client_id, callback)]
        self.retained: Dict[str, bytes] = {}
        self.clients: Dict[str, Dict] = {}
        self._lock = threading.Lock()
        self._running = False
        self._server_socket: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        
    def start(self, port: Optional[int] = None):
        """Start the MQTT broker on the specified port."""
        self._running = True
        listen_port = port or self.config.mqtt_port
        
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.settimeout(1.0)
        self._server_socket.bind(("0.0.0.0", listen_port))
        self._server_socket.listen(32)
        
        self._server_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._server_thread.start()
        logger.info(f"MQTT broker started on port {listen_port}")
    
    def stop(self):
        """Stop the MQTT broker."""
        self._running = False
        if self._server_socket:
            self._server_socket.close()
        if self._server_thread:
            self._server_thread.join(timeout=3)
        logger.info("MQTT broker stopped")
    
    def _accept_loop(self):
        """Accept incoming MQTT connections."""
        while self._running:
            try:
                client_sock, addr = self._server_socket.accept()
                threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, addr),
                    daemon=True
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break
    
    def _handle_client(self, sock: socket.socket, addr):
        """Handle a single MQTT client connection (simplified protocol)."""
        client_id = f"{addr[0]}:{addr[1]}"
        sock.settimeout(5.0)
        
        with self._lock:
            self.clients[client_id] = {
                "addr": addr,
                "connected_at": time.time(),
                "subscriptions": [],
            }
        
        try:
            while self._running:
                data = sock.recv(4096)
                if not data:
                    break
                self._process_mqtt_packet(client_id, sock, data)
        except (socket.timeout, ConnectionError, OSError):
            pass
        finally:
            with self._lock:
                self.clients.pop(client_id, None)
            sock.close()
    
    def _process_mqtt_packet(self, client_id: str, sock: socket.socket, data: bytes):
        """
        Process MQTT-like packets (simplified text protocol for simulation).
        
        Format: COMMAND TOPIC PAYLOAD\n
        """
        try:
            text = data.decode("utf-8", errors="ignore").strip()
            for line in text.split("\n"):
                parts = line.strip().split(" ", 2)
                if len(parts) < 2:
                    continue
                
                cmd = parts[0].upper()
                topic = parts[1]
                payload = parts[2] if len(parts) > 2 else ""
                
                if cmd == "PUB" or cmd == "PUBLISH":
                    self.publish(topic, payload.encode(), client_id)
                    sock.sendall(b"PUBACK\n")
                elif cmd == "SUB" or cmd == "SUBSCRIBE":
                    self.subscribe(topic, client_id, lambda t, p, s=sock: self._deliver(s, t, p))
                    sock.sendall(b"SUBACK\n")
                elif cmd == "UNSUB":
                    self.unsubscribe(topic, client_id)
                    sock.sendall(b"UNSUBACK\n")
                elif cmd == "PING":
                    sock.sendall(b"PONG\n")
        except Exception as e:
            logger.debug(f"MQTT packet error from {client_id}: {e}")
    
    def _deliver(self, sock: socket.socket, topic: str, payload: bytes):
        """Deliver a message to a subscriber's socket."""
        try:
            msg = f"MSG {topic} {payload.decode()}\n".encode()
            sock.sendall(msg)
        except Exception:
            pass
    
    def publish(self, topic: str, payload: bytes, publisher_id: str = ""):
        """Publish a message to a topic."""
        with self._lock:
            # Store retained messages
            self.retained[topic] = payload
            
            # Deliver to matching subscribers
            for sub_topic, subscribers in self.subscriptions.items():
                if self._topic_matches(sub_topic, topic):
                    for client_id, callback in subscribers:
                        if client_id != publisher_id:  # Don't echo back
                            try:
                                callback(topic, payload)
                            except Exception as e:
                                logger.debug(f"Delivery error to {client_id}: {e}")
    
    def subscribe(self, topic: str, client_id: str, callback: Callable):
        """Subscribe to a topic pattern."""
        with self._lock:
            if topic not in self.subscriptions:
                self.subscriptions[topic] = []
            self.subscriptions[topic].append((client_id, callback))
            
            # Deliver retained messages
            for ret_topic, ret_payload in self.retained.items():
                if self._topic_matches(topic, ret_topic):
                    try:
                        callback(ret_topic, ret_payload)
                    except Exception:
                        pass
    
    def unsubscribe(self, topic: str, client_id: str):
        """Unsubscribe from a topic."""
        with self._lock:
            if topic in self.subscriptions:
                self.subscriptions[topic] = [
                    (cid, cb) for cid, cb in self.subscriptions[topic]
                    if cid != client_id
                ]
    
    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        """MQTT-style topic matching with + and # wildcards."""
        pat_parts = pattern.split("/")
        top_parts = topic.split("/")
        
        for i, pat in enumerate(pat_parts):
            if pat == "#":
                return True
            if i >= len(top_parts):
                return False
            if pat != "+" and pat != top_parts[i]:
                return False
        
        return len(pat_parts) == len(top_parts)


class ProtocolSimulator:
    """
    Simulates IoT protocols (Zigbee, Z-Wave, BLE) with realistic
    behavior for security testing.
    """

    def __init__(self, protocol: Protocol, config: NetworkConfig):
        self.protocol = protocol
        self.config = config
        self.devices: Dict[str, Dict] = {}
        self._running = False
        self.packet_log: List[NetworkPacket] = []
    
    def register_device(self, device_id: str, address: str):
        """Register a device on this protocol."""
        self.devices[device_id] = {
            "address": address,
            "joined_at": time.time(),
            "state": "paired",
            "key": self._generate_network_key(),
        }
        logger.info(f"[{self.protocol.value}] Device {device_id} registered at {address}")
    
    def send_message(self, src_id: str, dst_id: str, payload: bytes) -> bool:
        """
        Simulate sending a message between two protocol devices.
        Returns True if delivered successfully.
        """
        if src_id not in self.devices or dst_id not in self.devices:
            return False
        
        # Simulate packet loss
        import random
        if random.random() < self.config.packet_loss_rate:
            logger.debug(f"[{self.protocol.value}] Packet lost: {src_id} → {dst_id}")
            return False
        
        # Log the packet
        packet = NetworkPacket(
            timestamp=time.time(),
            src_ip=self.devices[src_id]["address"],
            dst_ip=self.devices[dst_id]["address"],
            src_port=0,
            dst_port=0,
            protocol=self.protocol.value,
            payload=payload,
            size=len(payload),
        )
        self.packet_log.append(packet)
        
        # Simulate latency
        import random
        latency = self.config.latency_ms + random.uniform(
            -self.config.latency_jitter_ms, self.config.latency_jitter_ms
        )
        time.sleep(latency / 1000.0)
        
        return True
    
    def _generate_network_key(self) -> str:
        """Generate a protocol-specific network key."""
        import random
        return "".join(random.choices("0123456789abcdef", k=32))
    
    def get_traffic_stats(self) -> Dict[str, Any]:
        """Get protocol traffic statistics."""
        return {
            "protocol": self.protocol.value,
            "devices": len(self.devices),
            "packets": len(self.packet_log),
            "total_bytes": sum(p.size for p in self.packet_log),
        }


class PacketCapture:
    """
    Network packet capture and analysis for the simulated network.
    Records all traffic for security analysis.
    """

    def __init__(self):
        self.packets: List[NetworkPacket] = []
        self._lock = threading.Lock()
        self.capture_active = False
    
    def start_capture(self):
        """Start capturing packets."""
        self.capture_active = True
        self.packets.clear()
        logger.info("Packet capture started")
    
    def stop_capture(self) -> List[NetworkPacket]:
        """Stop capturing and return captured packets."""
        self.capture_active = False
        logger.info(f"Packet capture stopped: {len(self.packets)} packets")
        return list(self.packets)
    
    def record_packet(self, packet: NetworkPacket):
        """Record a network packet."""
        if not self.capture_active:
            return
        with self._lock:
            self.packets.append(packet)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get capture statistics."""
        with self._lock:
            if not self.packets:
                return {"total_packets": 0}
            
            protocols = {}
            src_ips = {}
            total_bytes = 0
            
            for pkt in self.packets:
                protocols[pkt.protocol] = protocols.get(pkt.protocol, 0) + 1
                src_ips[pkt.src_ip] = src_ips.get(pkt.src_ip, 0) + 1
                total_bytes += pkt.size
            
            return {
                "total_packets": len(self.packets),
                "total_bytes": total_bytes,
                "protocols": protocols,
                "top_talkers": dict(sorted(src_ips.items(), key=lambda x: -x[1])[:10]),
                "duration_sec": self.packets[-1].timestamp - self.packets[0].timestamp
                    if len(self.packets) > 1 else 0,
            }
    
    def export_pcap_json(self, filepath: str):
        """Export captured packets to JSON for analysis."""
        with self._lock:
            data = []
            for pkt in self.packets:
                data.append({
                    "timestamp": pkt.timestamp,
                    "src": f"{pkt.src_ip}:{pkt.src_port}",
                    "dst": f"{pkt.dst_ip}:{pkt.dst_port}",
                    "protocol": pkt.protocol,
                    "size": pkt.size,
                    "payload_hex": pkt.payload.hex() if pkt.payload else "",
                })
        
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Exported {len(data)} packets to {filepath}")


class WiresharkLiveCapture:
    """
    Live packet capture using tshark/dumpcap for Wireshark-compatible .pcap output.
    
    Captures real traffic on the Docker network interface so you can analyse
    device-to-device, device-to-broker, and device-to-cloud communication in
    Wireshark, tshark, or pyshark after the experiment.
    
    Typical usage:
        Bridge  mode → captures on 'docker0' or the veth interface
        Macvlan mode → captures on the parent WiFi/ethernet interface (en0, eth0)
    """

    def __init__(self, config: NetworkConfig):
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._pcap_path: Optional[Path] = None
        self._interface = config.wireshark.capture_interface
        
        # Auto-detect capture tool
        self._tool = self._find_capture_tool()
    
    def _find_capture_tool(self) -> Optional[str]:
        """Find tshark or dumpcap on PATH."""
        for name_attr, name in [
            ("tshark_path", "tshark"),
            ("dumpcap_path", "dumpcap"),
        ]:
            explicit = getattr(self.config.wireshark, name_attr, "")
            if explicit and Path(explicit).exists():
                return explicit
            found = shutil.which(name)
            if found:
                return found
        return None
    
    def _resolve_interface(self) -> str:
        """Pick the capture interface based on network mode."""
        if self._interface:
            return self._interface
        
        if self.config.mode == NetworkMode.BRIDGE:
            return "docker0"            # Linux; macOS uses bridge100 etc.
        elif self.config.mode in (NetworkMode.MACVLAN, NetworkMode.IPVLAN):
            return self.config.parent_interface or "en0"
        else:
            return "lo0"                # loopback fallback
    
    def start(self):
        """Start background packet capture to a .pcap file."""
        if not self._tool:
            logger.warning(
                "Wireshark capture enabled but tshark/dumpcap not found — "
                "install Wireshark CLI tools or set tshark_path"
            )
            return
        
        iface = self._resolve_interface()
        pcap_dir = Path(self.config.wireshark.pcap_dir)
        pcap_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._pcap_path = pcap_dir / f"vesper_{self.config.mode.value}_{timestamp}.pcap"
        
        cmd = [self._tool, "-i", iface, "-w", str(self._pcap_path)]
        
        # Add BPF filter
        bpf = self.config.wireshark.capture_filter
        if bpf:
            cmd += ["-f", bpf]
        
        # Ring buffer options (dumpcap/tshark)
        if self.config.wireshark.ring_buffer_count > 0:
            cmd += ["-b", f"files:{self.config.wireshark.ring_buffer_count}"]
        if self.config.wireshark.rotate_seconds > 0:
            cmd += ["-b", f"duration:{self.config.wireshark.rotate_seconds}"]
        
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            logger.info(
                f"Wireshark capture started: iface={iface}, "
                f"pcap={self._pcap_path}, tool={Path(self._tool).name}"
            )
        except Exception as e:
            logger.error(f"Failed to start capture: {e}")
            self._process = None
    
    def stop(self) -> Optional[str]:
        """Stop the capture and return the .pcap path."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            logger.info(f"Wireshark capture stopped → {self._pcap_path}")
            self._process = None
        return str(self._pcap_path) if self._pcap_path else None
    
    @property
    def pcap_file(self) -> Optional[str]:
        return str(self._pcap_path) if self._pcap_path else None


class SimulatedHomeNetwork:
    """
    Complete simulated home network for VESPER IoT security testing.
    
    Orchestrates:
    - Docker bridge/macvlan/ipvlan network for device containers
    - MQTT broker for pub/sub communication
    - Protocol simulators (Zigbee, Z-Wave, BLE)
    - Packet capture and traffic analysis
    - Wireshark-compatible live capture (tshark/dumpcap)
    - Network condition simulation (latency, loss, bandwidth)
    
    Network modes:
    - BRIDGE  : default isolated Docker bridge (capture on docker0 / veth)
    - MACVLAN : containers get real LAN IPs, visible to WiFi router
                Wireshark captures on parent interface (en0, eth0, etc.)
    - IPVLAN  : like macvlan but shares parent MAC
    - HOST    : containers share host network (no isolation)
    """

    def __init__(self, config: Optional[NetworkConfig] = None):
        self.config = config or NetworkConfig()
        self.devices: Dict[str, NetworkDevice] = {}
        self.mqtt_broker = MQTTBrokerSimulator(self.config)
        self.packet_capture = PacketCapture()
        self.protocol_simulators: Dict[Protocol, ProtocolSimulator] = {}
        self._next_ip_octet = 10  # Start device IPs at .10
        self._running = False
        self._wireshark: Optional[WiresharkLiveCapture] = None
        
        # Initialize protocol simulators
        if self.config.zigbee_enabled:
            self.protocol_simulators[Protocol.ZIGBEE] = ProtocolSimulator(
                Protocol.ZIGBEE, self.config
            )
        if self.config.zwave_enabled:
            self.protocol_simulators[Protocol.ZWAVE] = ProtocolSimulator(
                Protocol.ZWAVE, self.config
            )
        if self.config.ble_enabled:
            self.protocol_simulators[Protocol.BLE] = ProtocolSimulator(
                Protocol.BLE, self.config
            )
    
    def start(self):
        """Start the simulated home network."""
        logger.info("Starting simulated home network...")
        
        # Resolve driver from mode
        self.config.docker_network_driver = self.config.mode.value
        
        # Create Docker network
        self._create_docker_network()
        
        # Start MQTT broker
        self.mqtt_broker.start()
        
        # Start packet capture
        self.packet_capture.start_capture()
        
        # Start Wireshark live capture if configured
        if self.config.wireshark.enabled:
            self._wireshark = WiresharkLiveCapture(self.config)
            self._wireshark.start()
        
        self._running = True
        logger.info(f"Home network running: subnet={self.config.subnet}, mode={self.config.mode.value}")
    
    def stop(self):
        """Stop the simulated home network."""
        logger.info("Stopping simulated home network...")
        self._running = False
        
        # Stop Wireshark live capture
        if hasattr(self, '_wireshark') and self._wireshark:
            self._wireshark.stop()
        
        # Stop packet capture
        self.packet_capture.stop_capture()
        
        # Stop MQTT broker
        self.mqtt_broker.stop()
        
        # Disconnect all devices
        for dev in self.devices.values():
            dev.state = DeviceNetworkState.DISCONNECTED
        
        logger.info("Home network stopped")
    
    def _create_docker_network(self):
        """
        Create the Docker network for device containers.
        
        Supports multiple drivers:
          - bridge   : default isolated bridge; capture via docker0 / veth pairs
          - macvlan  : containers get real LAN IPs; capture on parent WiFi/eth iface
          - ipvlan   : like macvlan but shares parent MAC
          - host     : no network isolation (no special network needed)
        """
        if self.config.mode == NetworkMode.HOST:
            logger.info("Host network mode — no Docker network needed")
            return
        
        try:
            # Check if network already exists
            result = subprocess.run(
                ["docker", "network", "inspect", self.config.docker_network_name],
                capture_output=True, timeout=10
            )
            if result.returncode == 0:
                logger.info(f"Docker network '{self.config.docker_network_name}' already exists")
                return
            
            # Build the create command
            cmd = [
                "docker", "network", "create",
                "--driver", self.config.docker_network_driver,
                "--subnet", self.config.subnet,
            ]
            
            if self.config.mode == NetworkMode.BRIDGE:
                # Standard bridge with explicit gateway
                cmd += ["--gateway", self.config.gateway_ip]
            
            elif self.config.mode in (NetworkMode.MACVLAN, NetworkMode.IPVLAN):
                # macvlan/ipvlan require a parent interface
                parent = self.config.parent_interface
                if not parent:
                    parent = self._detect_parent_interface()
                    self.config.parent_interface = parent
                
                if self.config.mode == NetworkMode.MACVLAN:
                    cmd += [
                        f"--opt=parent={parent}",
                        f"--opt=macvlan_mode={self.config.macvlan_mode}",
                    ]
                else:
                    cmd += [f"--opt=parent={parent}"]
                
                cmd += ["--gateway", self.config.gateway_ip]
                
                # Reserve host / router IPs to avoid conflicts
                for label, addr in self.config.aux_addresses.items():
                    cmd += ["--aux-address", f"{label}={addr}"]
            
            cmd.append(self.config.docker_network_name)
            
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                logger.error(f"Docker network create failed: {result.stderr.strip()}")
            else:
                logger.info(
                    f"Created Docker network: {self.config.docker_network_name} "
                    f"(driver={self.config.docker_network_driver}, subnet={self.config.subnet})"
                )
        except Exception as e:
            logger.warning(f"Docker network creation failed (non-fatal): {e}")
    
    @staticmethod
    def _detect_parent_interface() -> str:
        """Auto-detect the active network interface for macvlan/ipvlan."""
        # macOS: en0 (WiFi), en1 (Thunderbolt Ethernet)
        # Linux: eth0, wlan0, ens33, etc.
        import platform
        
        if platform.system() == "Darwin":
            # macOS — find default route interface
            try:
                out = subprocess.check_output(
                    ["route", "-n", "get", "default"],
                    text=True, timeout=5
                )
                for line in out.splitlines():
                    if "interface:" in line:
                        return line.split("interface:")[-1].strip()
            except Exception:
                pass
            return "en0"
        else:
            # Linux — ip route
            try:
                out = subprocess.check_output(
                    ["ip", "route", "show", "default"],
                    text=True, timeout=5
                )
                parts = out.split()
                if "dev" in parts:
                    return parts[parts.index("dev") + 1]
            except Exception:
                pass
            return "eth0"
    
    def remove_docker_network(self):
        """Remove the Docker network (cleanup)."""
        if self.config.mode == NetworkMode.HOST:
            return
        try:
            subprocess.run(
                ["docker", "network", "rm", self.config.docker_network_name],
                capture_output=True, timeout=10
            )
            logger.info(f"Removed Docker network: {self.config.docker_network_name}")
        except Exception as e:
            logger.debug(f"Network removal failed: {e}")
    
    def _generate_mac(self) -> str:
        """Generate a unique MAC address for a virtual device."""
        import random
        mac = [0x52, 0x54, 0x00,  # QEMU OUI prefix
               random.randint(0, 255),
               random.randint(0, 255),
               random.randint(0, 255)]
        return ":".join(f"{b:02x}" for b in mac)
    
    def _next_ip(self) -> str:
        """Allocate the next IP address."""
        ip = f"172.20.0.{self._next_ip_octet}"
        self._next_ip_octet += 1
        return ip
    
    def add_device(
        self,
        device_id: str,
        protocol: Protocol = Protocol.TCP,
        tcp_port: int = 0,
        ip_address: Optional[str] = None,
    ) -> NetworkDevice:
        """Add a device to the simulated network."""
        ip = ip_address or self._next_ip()
        mac = self._generate_mac()
        
        device = NetworkDevice(
            device_id=device_id,
            ip_address=ip,
            mac_address=mac,
            protocol=protocol,
            tcp_port=tcp_port,
            mqtt_topics=[
                f"vesper/devices/{device_id}/state",
                f"vesper/devices/{device_id}/events",
                f"vesper/devices/{device_id}/commands",
            ],
            last_seen=time.time(),
        )
        
        self.devices[device_id] = device
        
        # Register with protocol simulator if applicable
        if protocol in self.protocol_simulators:
            self.protocol_simulators[protocol].register_device(device_id, ip)
        
        logger.info(f"Added device {device_id}: IP={ip}, MAC={mac}, protocol={protocol.value}")
        return device
    
    def connect_device(self, device_id: str) -> bool:
        """Simulate device connecting to the network."""
        if device_id not in self.devices:
            return False
        
        device = self.devices[device_id]
        device.state = DeviceNetworkState.CONNECTING
        
        # Simulate connection delay
        time.sleep(self.config.latency_ms / 1000.0)
        
        device.state = DeviceNetworkState.CONNECTED
        device.last_seen = time.time()
        
        # Auto-subscribe to MQTT topics
        for topic in device.mqtt_topics:
            self.mqtt_broker.subscribe(
                topic, device_id,
                lambda t, p, did=device_id: self._on_device_message(did, t, p)
            )
        
        logger.info(f"Device {device_id} connected to network")
        return True
    
    def disconnect_device(self, device_id: str):
        """Simulate device disconnecting from the network."""
        if device_id in self.devices:
            self.devices[device_id].state = DeviceNetworkState.DISCONNECTED
            for topic in self.devices[device_id].mqtt_topics:
                self.mqtt_broker.unsubscribe(topic, device_id)
            logger.info(f"Device {device_id} disconnected")
    
    def send_device_message(
        self,
        src_device_id: str,
        topic: str,
        payload: str,
    ) -> bool:
        """Send a message from a device to the MQTT bus."""
        if src_device_id not in self.devices:
            return False
        
        device = self.devices[src_device_id]
        if device.state not in (DeviceNetworkState.CONNECTED, DeviceNetworkState.AUTHENTICATED):
            return False
        
        payload_bytes = payload.encode()
        
        # Record packet
        packet = NetworkPacket(
            timestamp=time.time(),
            src_ip=device.ip_address,
            dst_ip=self.config.mqtt_broker_ip,
            src_port=device.tcp_port,
            dst_port=self.config.mqtt_port,
            protocol="mqtt",
            payload=payload_bytes,
            size=len(payload_bytes),
        )
        self.packet_capture.record_packet(packet)
        
        # Update stats
        device.bytes_sent += len(payload_bytes)
        device.packets_sent += 1
        device.last_seen = time.time()
        
        # Publish via broker
        self.mqtt_broker.publish(topic, payload_bytes, src_device_id)
        return True
    
    def _on_device_message(self, device_id: str, topic: str, payload: bytes):
        """Handle message delivered to a device."""
        if device_id in self.devices:
            device = self.devices[device_id]
            device.bytes_received += len(payload)
            device.packets_received += 1
            device.last_seen = time.time()
    
    def get_network_topology(self) -> Dict[str, Any]:
        """Get the current network topology as a dictionary."""
        return {
            "subnet": self.config.subnet,
            "gateway": self.config.gateway_ip,
            "mqtt_broker": self.config.mqtt_broker_ip,
            "devices": {
                did: {
                    "ip": dev.ip_address,
                    "mac": dev.mac_address,
                    "protocol": dev.protocol.value,
                    "state": dev.state.value,
                    "port": dev.tcp_port,
                    "bytes_sent": dev.bytes_sent,
                    "bytes_received": dev.bytes_received,
                }
                for did, dev in self.devices.items()
            },
            "protocols": {
                proto.value: sim.get_traffic_stats()
                for proto, sim in self.protocol_simulators.items()
            },
            "capture_stats": self.packet_capture.get_stats(),
        }
    
    def set_network_conditions(
        self,
        latency_ms: Optional[float] = None,
        packet_loss_rate: Optional[float] = None,
        bandwidth_bps: Optional[int] = None,
    ):
        """Dynamically change network conditions (for attack simulation)."""
        if latency_ms is not None:
            self.config.latency_ms = latency_ms
        if packet_loss_rate is not None:
            self.config.packet_loss_rate = packet_loss_rate
        if bandwidth_bps is not None:
            self.config.bandwidth_bps = bandwidth_bps
        
        logger.info(
            f"Network conditions updated: latency={self.config.latency_ms}ms, "
            f"loss={self.config.packet_loss_rate:.1%}, "
            f"bw={self.config.bandwidth_bps}bps"
        )
