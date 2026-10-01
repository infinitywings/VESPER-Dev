#!/usr/bin/env python3
"""
VESPER Home WiFi Topology — Mininet-WiFi

Builds a realistic smart-home WiFi network using Mininet-WiFi
(Fontes et al., SoftCOM 2015).

Topology:
    [Internet / WAN]
          |
    [ap1] Access Point  (VESPER-IoT-Network, WPA2-PSK, ch 6)
      ├── 192.168.4.1   gateway + Matter bridge proxy + DNS
      |
      ├── [sta1] Kitchen Light     192.168.4.10
      ├── [sta2] Living Room Light 192.168.4.11
      ├── [sta3] Bedroom Light     192.168.4.12
      ├── [sta4] Motion Sensor     192.168.4.13
      ├── [sta5] Temp Sensor       192.168.4.14
      ├── [sta6] Door Sensor       192.168.4.15
      ├── [sta7] Smart Plug        192.168.4.16
      └── [sta8] Humidity Sensor   192.168.4.17

Each station runs in its own network namespace (full L2/L3 isolation).
Real 802.11 frames traverse mac80211_hwsim radios.
hostapd handles WPA2-PSK authentication per station.

Usage:
    # Standalone
    sudo python3 vesper_topology.py [--stations N] [--pcap] [--no-matter]

    # From Python
    from vesper_topology import VesperWiFiTopology
    topo = VesperWiFiTopology()
    topo.start()
    topo.get_station_ip("sta1")  # "192.168.4.10"
    topo.run_on_station("sta1", "ping -c1 192.168.4.1")
    topo.stop()
"""

import argparse
import os
import signal
import subprocess
import sys
import time
import logging
from typing import Dict, List, Optional, Tuple

# Mininet-WiFi imports
from mininet.log import setLogLevel, info, error
from mininet.node import Controller
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import AP, Station
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference

logger = logging.getLogger("vesper.topology")

# ── Default device definitions ────────────────────────────────────────────────

DEFAULT_DEVICES = [
    {"name": "sta1", "label": "Kitchen Light",     "ip": "192.168.4.10/24", "type": "smart_light"},
    {"name": "sta2", "label": "Living Room Light",  "ip": "192.168.4.11/24", "type": "smart_light"},
    {"name": "sta3", "label": "Bedroom Light",      "ip": "192.168.4.12/24", "type": "smart_light"},
    {"name": "sta4", "label": "Motion Sensor",      "ip": "192.168.4.13/24", "type": "motion_sensor"},
    {"name": "sta5", "label": "Temp Sensor",        "ip": "192.168.4.14/24", "type": "temperature_sensor"},
    {"name": "sta6", "label": "Door Sensor",        "ip": "192.168.4.15/24", "type": "door_sensor"},
    {"name": "sta7", "label": "Smart Plug",         "ip": "192.168.4.16/24", "type": "smart_plug"},
    {"name": "sta8", "label": "Humidity Sensor",    "ip": "192.168.4.17/24", "type": "humidity_sensor"},
]

# WiFi parameters
WIFI_SSID = os.environ.get("VESPER_WIFI_SSID", "VESPER-IoT-Network")
WIFI_PASS = os.environ.get("VESPER_WIFI_PASS", "vesper-secure-2026")
WIFI_CHANNEL = os.environ.get("VESPER_WIFI_CHANNEL", "6")
WIFI_MODE = "g"  # 802.11g (2.4 GHz)

# Network parameters
GATEWAY_IP = "192.168.4.1"
SUBNET_MASK = "255.255.255.0"
MATTER_BRIDGE_PORT = 8484


class VesperWiFiTopology:
    """
    Manages the VESPER emulated home WiFi network.

    Wraps Mininet-WiFi to provide a clean API for:
    - Starting/stopping the WiFi topology
    - Running commands on individual stations
    - Capturing packets
    - Managing Matter bridge proxy
    - Querying station state (associated, IP, signal, etc.)
    """

    def __init__(
        self,
        devices: Optional[List[Dict]] = None,
        ssid: str = WIFI_SSID,
        passwd: str = WIFI_PASS,
        channel: str = WIFI_CHANNEL,
        enable_matter: bool = True,
        enable_pcap: bool = False,
        pcap_dir: str = "/var/lib/vesper/pcap",
        enable_nat: bool = True,
        wmediumd_mode: str = "interference",
    ):
        self.devices = devices or DEFAULT_DEVICES
        self.ssid = ssid
        self.passwd = passwd
        self.channel = channel
        self.enable_matter = enable_matter
        self.enable_pcap = enable_pcap
        self.pcap_dir = pcap_dir
        self.enable_nat = enable_nat
        self.wmediumd_mode = wmediumd_mode

        self.net: Optional[Mininet_wifi] = None
        self.ap = None
        self.stations: Dict[str, Station] = {}
        self.matter_proxy_proc = None
        self.pcap_proc = None
        self._running = False

    # ── Topology construction ─────────────────────────────────────────────

    def start(self) -> None:
        """Build and start the full WiFi topology."""
        info("*** VESPER WiFi Topology — Starting\n")

        # Select wmediumd mode
        wm_mode = interference
        info(f"*** Wmediumd mode: {self.wmediumd_mode}\n")

        self.net = Mininet_wifi(
            link=wmediumd,
            wmediumd_mode=wm_mode,
            ssid=self.ssid,
            mode=WIFI_MODE,
            channel=self.channel,
            encrypt="wpa2",
            passwd=self.passwd,
        )

        info("*** Creating access point\n")
        self.ap = self.net.addAccessPoint(
            "ap1",
            ssid=self.ssid,
            mode=WIFI_MODE,
            channel=self.channel,
            passwd=self.passwd,
            encrypt="wpa2",
            ip=f"{GATEWAY_IP}/24",
            position="50,50,0",
            failMode="standalone",
            datapath="user",
        )

        info(f"*** Creating {len(self.devices)} IoT stations\n")
        for dev in self.devices:
            sta = self.net.addStation(
                dev["name"],
                ip=dev["ip"],
                passwd=self.passwd,
                encrypt="wpa2",
                position=self._device_position(dev["name"]),
            )
            self.stations[dev["name"]] = sta

        info("*** Configuring nodes\n")
        self.net.configureNodes()

        info("*** Creating links (station ↔ AP)\n")
        for sta in self.stations.values():
            self.net.addLink(sta, self.ap)

        info("*** Building network\n")
        self.net.build()
        self.ap.start([])

        # Set default route on each station
        for dev in self.devices:
            sta = self.stations[dev["name"]]
            sta.cmd(f"ip route add default via {GATEWAY_IP}")

        # Enable NAT on the AP for internet access
        if self.enable_nat:
            self._setup_nat()

        # Start Matter bridge proxy on the AP
        if self.enable_matter:
            self._start_matter_proxy()

        # Start dnsmasq on the AP
        self._start_dnsmasq()

        # Start packet capture
        if self.enable_pcap:
            self._start_pcap()

        self._running = True
        info("*** VESPER WiFi Topology — Ready\n")
        self._print_topology()

    def stop(self) -> None:
        """Tear down the topology cleanly."""
        info("*** VESPER WiFi Topology — Stopping\n")

        if self.pcap_proc:
            self.pcap_proc.terminate()
            self.pcap_proc.wait(timeout=5)
            info("*** Packet capture stopped\n")

        if self.matter_proxy_proc:
            self.matter_proxy_proc.terminate()
            self.matter_proxy_proc.wait(timeout=5)
            info("*** Matter bridge proxy stopped\n")

        if self.net:
            self.net.stop()

        self._running = False
        info("*** VESPER WiFi Topology — Stopped\n")

    # ── Station operations ────────────────────────────────────────────────

    def run_on_station(self, station_name: str, cmd: str) -> str:
        """Execute a command inside a station's network namespace."""
        if station_name not in self.stations:
            raise ValueError(f"Unknown station: {station_name}")
        return self.stations[station_name].cmd(cmd)

    def get_station_ip(self, station_name: str) -> str:
        """Get the IP address of a station."""
        for dev in self.devices:
            if dev["name"] == station_name:
                return dev["ip"].split("/")[0]
        raise ValueError(f"Unknown station: {station_name}")

    def get_station_by_type(self, device_type: str) -> List[str]:
        """Get all station names matching a device type."""
        return [d["name"] for d in self.devices if d["type"] == device_type]

    def get_station_info(self, station_name: str) -> Dict:
        """Get detailed info about a station (IP, MAC, signal, etc.)."""
        sta = self.stations.get(station_name)
        if not sta:
            raise ValueError(f"Unknown station: {station_name}")

        dev_info = next((d for d in self.devices if d["name"] == station_name), {})
        iw_info = sta.cmd("iw dev | grep -E 'ssid|signal|freq'").strip()

        return {
            "name": station_name,
            "label": dev_info.get("label", ""),
            "type": dev_info.get("type", ""),
            "ip": dev_info.get("ip", "").split("/")[0],
            "mac": sta.cmd("cat /sys/class/net/*/address 2>/dev/null | head -1").strip(),
            "associated": self.ssid in sta.cmd("iw dev sta*-wlan0 link 2>/dev/null"),
            "iw_info": iw_info,
        }

    def get_all_station_ips(self) -> Dict[str, str]:
        """Return {station_name: ip} for all stations."""
        return {d["name"]: d["ip"].split("/")[0] for d in self.devices}

    def is_station_reachable(self, station_name: str) -> bool:
        """Ping the station from the AP."""
        ip = self.get_station_ip(station_name)
        result = self.ap.cmd(f"ping -c1 -W1 {ip}")
        return "1 received" in result

    # ── Matter bridge operations ───────────────────────────────────────────

    def send_matter_command(self, device_id: str, state: dict, station_name: Optional[str] = None) -> str:
        """Send a state update to the Matter bridge (from AP or a specific station)."""
        import json
        payload = json.dumps(state)
        cmd = (
            f"curl -s -X PUT http://{GATEWAY_IP}:{MATTER_BRIDGE_PORT}/devices/{device_id}/state "
            f"-H 'Content-Type: application/json' -d '{payload}'"
        )
        if station_name:
            return self.run_on_station(station_name, cmd)
        return self.ap.cmd(cmd)

    def list_matter_devices(self, station_name: Optional[str] = None) -> str:
        """List all devices registered on the Matter bridge."""
        cmd = f"curl -s http://{GATEWAY_IP}:{MATTER_BRIDGE_PORT}/devices"
        if station_name:
            return self.run_on_station(station_name, cmd)
        return self.ap.cmd(cmd)

    # ── Packet capture ────────────────────────────────────────────────────

    def start_capture(self, interface: str = "ap1-wlan1", output_file: Optional[str] = None) -> int:
        """Start packet capture on a specific interface. Returns PID."""
        if not output_file:
            ts = int(time.time())
            output_file = f"{self.pcap_dir}/vesper_capture_{ts}.pcap"
        cmd = f"tshark -i {interface} -w {output_file} -q &"
        pid_str = self.ap.cmd(cmd + " echo $!")
        return int(pid_str.strip().split()[-1])

    def stop_capture(self, pid: int) -> None:
        """Stop a specific packet capture."""
        self.ap.cmd(f"kill {pid} 2>/dev/null")

    # ── Network attack helpers ────────────────────────────────────────────

    def get_ap_interface(self) -> str:
        """Get the AP's wireless interface name (for attacks)."""
        return self.ap.cmd("ls /sys/class/net/ | grep wlan").strip().split()[0]

    def get_station_interface(self, station_name: str) -> str:
        """Get a station's wireless interface name."""
        sta = self.stations.get(station_name)
        if not sta:
            raise ValueError(f"Unknown station: {station_name}")
        return sta.cmd("ls /sys/class/net/ | grep wlan").strip().split()[0]

    def get_station_mac(self, station_name: str) -> str:
        """Get a station's MAC address."""
        sta = self.stations.get(station_name)
        if not sta:
            raise ValueError(f"Unknown station: {station_name}")
        iface = self.get_station_interface(station_name)
        return sta.cmd(f"cat /sys/class/net/{iface}/address").strip()

    def get_ap_mac(self) -> str:
        """Get the AP's MAC address."""
        iface = self.get_ap_interface()
        return self.ap.cmd(f"cat /sys/class/net/{iface}/address").strip()

    def inject_frame(self, station_name: str, frame_hex: str) -> str:
        """Inject a raw 802.11 frame from a station (for deauth attacks etc.)."""
        # scapy-based injection in the station's namespace
        cmd = (
            f"python3 -c \""
            f"from scapy.all import *; "
            f"sendp(RadioTap()/Dot11(bytes.fromhex('{frame_hex}')), "
            f"iface='{self.get_station_interface(station_name)}', verbose=0)\""
        )
        return self.run_on_station(station_name, cmd)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _device_position(self, name: str) -> str:
        """Assign a 2D position for wmediumd signal propagation."""
        positions = {
            "sta1": "30,50,0",   # Kitchen
            "sta2": "50,70,0",   # Living room
            "sta3": "70,30,0",   # Bedroom
            "sta4": "40,60,0",   # Hallway (motion)
            "sta5": "50,50,0",   # Central (temp)
            "sta6": "20,50,0",   # Front door
            "sta7": "60,50,0",   # Outlet (plug)
            "sta8": "50,40,0",   # Bathroom (humidity)
        }
        return positions.get(name, "50,50,0")

    def _setup_nat(self) -> None:
        """
        Configure NAT + stateful firewall on the AP.

        Mirrors a real consumer WiFi router's default security posture:
        - Stateful connection tracking (conntrack)
        - NAT masquerade for WAN egress
        - SYN flood protection (rate-limiting)
        - ICMP rate limiting
        - Drop invalid packets
        - Explicit ACCEPT only for DHCP, DNS, NTP, Matter
        - Default DROP policy for INPUT from WAN
        - Optional AP isolation (station-to-station block)
        - Logging of dropped packets for forensic pcap analysis

        Reference: TP-Link Archer AX6000 / ASUS RT-AX88U default iptables
        """
        info("*** Setting up NAT + stateful firewall on AP\n")

        # ── Enable IP forwarding ──────────────────────────────────────────
        self.ap.cmd("sysctl -w net.ipv4.ip_forward=1")

        # ── Conntrack tuning (real routers have limited tables) ───────────
        self.ap.cmd("sysctl -w net.netfilter.nf_conntrack_max=4096")
        self.ap.cmd("sysctl -w net.netfilter.nf_conntrack_tcp_timeout_established=3600")

        # ── Flush existing rules ──────────────────────────────────────────
        self.ap.cmd("iptables -F")
        self.ap.cmd("iptables -t nat -F")
        self.ap.cmd("iptables -X")

        # ── Default policies ──────────────────────────────────────────────
        self.ap.cmd("iptables -P INPUT DROP")
        self.ap.cmd("iptables -P FORWARD DROP")
        self.ap.cmd("iptables -P OUTPUT ACCEPT")

        # ── Loopback ─────────────────────────────────────────────────────
        self.ap.cmd("iptables -A INPUT -i lo -j ACCEPT")
        self.ap.cmd("iptables -A OUTPUT -o lo -j ACCEPT")

        # ── Drop invalid packets (real routers do this) ──────────────────
        self.ap.cmd("iptables -A INPUT -m conntrack --ctstate INVALID -j DROP")
        self.ap.cmd("iptables -A FORWARD -m conntrack --ctstate INVALID -j DROP")

        # ── Allow established/related connections (stateful firewall) ─────
        self.ap.cmd("iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT")
        self.ap.cmd("iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT")

        # ── SYN flood protection (real routers rate-limit new TCP) ────────
        self.ap.cmd(
            "iptables -A INPUT -p tcp --syn -m limit "
            "--limit 25/sec --limit-burst 50 -j ACCEPT"
        )
        self.ap.cmd(
            "iptables -A INPUT -p tcp --syn -j DROP"
        )

        # ── ICMP rate limiting (real routers limit ping) ─────────────────
        self.ap.cmd(
            "iptables -A INPUT -p icmp --icmp-type echo-request "
            "-m limit --limit 10/sec --limit-burst 20 -j ACCEPT"
        )
        self.ap.cmd("iptables -A INPUT -p icmp --icmp-type echo-request -j DROP")
        # Allow other ICMP (destination-unreachable, time-exceeded)
        self.ap.cmd("iptables -A INPUT -p icmp -j ACCEPT")

        # ── Allowed LAN services on the AP ────────────────────────────────
        # DHCP (UDP 67/68) — essential for WiFi clients
        self.ap.cmd("iptables -A INPUT -p udp --dport 67 -j ACCEPT")
        self.ap.cmd("iptables -A INPUT -p udp --dport 68 -j ACCEPT")

        # DNS (UDP/TCP 53) — clients resolve via router
        self.ap.cmd("iptables -A INPUT -p udp --dport 53 -j ACCEPT")
        self.ap.cmd("iptables -A INPUT -p tcp --dport 53 -j ACCEPT")

        # NTP (UDP 123) — real IoT devices sync time via router
        self.ap.cmd("iptables -A INPUT -p udp --dport 123 -j ACCEPT")

        # Matter bridge (TCP 8484 REST, UDP 5540 commissioning)
        self.ap.cmd("iptables -A INPUT -p tcp --dport 8484 -j ACCEPT")
        self.ap.cmd("iptables -A INPUT -p udp --dport 5540 -j ACCEPT")

        # ── NAT masquerade (LAN → WAN) ───────────────────────────────────
        self.ap.cmd("iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE")

        # ── FORWARD chain: LAN → WAN (allow outbound) ────────────────────
        self.ap.cmd(
            "iptables -A FORWARD -i ap1-wlan1 -o eth0 "
            "-m conntrack --ctstate NEW -j ACCEPT"
        )
        # WAN → LAN: only established (no inbound initiation — like real NAT)
        self.ap.cmd(
            "iptables -A FORWARD -i eth0 -o ap1-wlan1 "
            "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
        )

        # ── Station-to-station forwarding (through AP) ────────────────────
        # Real routers forward between WiFi clients by default.
        # AP Isolation (if enabled) blocks this — common security feature.
        ap_isolation = os.environ.get("VESPER_AP_ISOLATION", "0")
        if ap_isolation == "1":
            info("*** AP Isolation ENABLED: blocking station-to-station\n")
            self.ap.cmd(
                "iptables -A FORWARD -i ap1-wlan1 -o ap1-wlan1 -j DROP"
            )
        else:
            # Default: allow station-to-station (most consumer routers)
            self.ap.cmd(
                "iptables -A FORWARD -i ap1-wlan1 -o ap1-wlan1 "
                "-m conntrack --ctstate NEW -j ACCEPT"
            )

        # ── Log dropped packets (for forensic analysis) ──────────────────
        self.ap.cmd(
            "iptables -A INPUT -j LOG --log-prefix 'VESPER-FW-DROP: ' "
            "--log-level 4 --log-tcp-options"
        )
        self.ap.cmd(
            "iptables -A FORWARD -j LOG --log-prefix 'VESPER-FW-FWD-DROP: ' "
            "--log-level 4"
        )

        # ── Anti-spoofing: drop packets from LAN with non-LAN source ─────
        self.ap.cmd(
            "iptables -t raw -A PREROUTING -i ap1-wlan1 "
            "! -s 192.168.4.0/24 -j DROP"
        )

        info("*** Stateful firewall configured (NAT + conntrack + rate-limit)\n")
        info("***   INPUT policy:   DROP (whitelist DHCP/DNS/NTP/Matter)\n")
        info("***   FORWARD policy: DROP (stateful NAT + LAN-to-LAN)\n")
        info("***   SYN flood:      25/sec burst 50\n")
        info("***   ICMP:           10/sec burst 20\n")
        info("***   Anti-spoof:     drop non-192.168.4.0/24 from LAN\n")

    def _start_matter_proxy(self) -> None:
        """Start Matter bridge proxy on the AP (forwards to vesper-matter-bridge container)."""
        info("*** Starting Matter bridge proxy on AP\n")
        # The matter.js bridge runs in a separate Docker container.
        # We set up socat to forward local port 8484 → vesper-matter-bridge:8484
        # so stations can reach the bridge via the gateway IP.
        bridge_host = os.environ.get("MATTER_BRIDGE_HOST", "vesper-matter-bridge")
        self.matter_proxy_proc = subprocess.Popen(
            [
                "socat",
                f"TCP-LISTEN:{MATTER_BRIDGE_PORT},fork,reuseaddr",
                f"TCP:{bridge_host}:{MATTER_BRIDGE_PORT}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        info(f"*** Matter bridge proxy running on {GATEWAY_IP}:{MATTER_BRIDGE_PORT}\n")

    def _start_dnsmasq(self) -> None:
        """Start dnsmasq for DHCP/DNS on the AP."""
        info("*** Starting dnsmasq on AP\n")
        self.ap.cmd(
            f"dnsmasq --interface=ap1-wlan1 "
            f"--dhcp-range=192.168.4.100,192.168.4.200,255.255.255.0,12h "
            f"--dhcp-option=3,{GATEWAY_IP} "
            f"--dhcp-option=6,{GATEWAY_IP} "
            f"--log-facility=/var/log/vesper/dnsmasq.log "
            f"--no-daemon &"
        )
        time.sleep(0.5)
        info("*** dnsmasq running\n")

    def _start_pcap(self) -> None:
        """Start packet capture on the AP wireless interface."""
        info("*** Starting packet capture\n")
        os.makedirs(self.pcap_dir, exist_ok=True)
        ts = int(time.time())
        pcap_file = f"{self.pcap_dir}/vesper_wifi_{ts}.pcap"
        self.pcap_proc = subprocess.Popen(
            ["tshark", "-i", "ap1-wlan1", "-w", pcap_file, "-q"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        info(f"*** Capturing to {pcap_file}\n")

    def _print_topology(self) -> None:
        """Print a summary of the running topology."""
        info("\n")
        info("╔══════════════════════════════════════════════════════════════╗\n")
        info("║  VESPER Emulated Home WiFi Network (Mininet-WiFi)          ║\n")
        info("╠══════════════════════════════════════════════════════════════╣\n")
        info(f"║  SSID:     {self.ssid:<48}║\n")
        info(f"║  Auth:     WPA2-PSK{' ' * 40}║\n")
        info(f"║  Channel:  {self.channel:<48}║\n")
        info(f"║  Gateway:  {GATEWAY_IP:<48}║\n")
        info(f"║  Matter:   http://{GATEWAY_IP}:{MATTER_BRIDGE_PORT:<29}║\n")
        info("╠══════════════════════════════════════════════════════════════╣\n")
        for dev in self.devices:
            ip = dev["ip"].split("/")[0]
            info(f"║  {dev['name']:6s}  {ip:16s}  {dev['label']:<25s}║\n")
        info("╚══════════════════════════════════════════════════════════════╝\n")
        info("\n")


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="VESPER WiFi Topology (Mininet-WiFi)")
    parser.add_argument("--stations", type=int, default=len(DEFAULT_DEVICES),
                        help="Number of IoT stations (default: 8)")
    parser.add_argument("--pcap", action="store_true", help="Enable packet capture")
    parser.add_argument("--no-matter", action="store_true", help="Disable Matter bridge proxy")
    parser.add_argument("--ssid", default=WIFI_SSID, help="WiFi SSID")
    parser.add_argument("--channel", default=WIFI_CHANNEL, help="WiFi channel")
    parser.add_argument("--cli", action="store_true", help="Drop into Mininet CLI")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    setLogLevel("info" if not args.verbose else "debug")

    devices = DEFAULT_DEVICES[:args.stations]
    topo = VesperWiFiTopology(
        devices=devices,
        ssid=args.ssid,
        channel=args.channel,
        enable_matter=not args.no_matter,
        enable_pcap=args.pcap,
    )

    def _signal_handler(sig, frame):
        info("\n*** Caught signal, shutting down...\n")
        topo.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        topo.start()

        if args.cli:
            from mn_wifi.cli import CLI
            CLI(topo.net)
        else:
            info("*** Topology running. Press Ctrl+C to stop.\n")
            while True:
                time.sleep(1)
    finally:
        topo.stop()


if __name__ == "__main__":
    main()
