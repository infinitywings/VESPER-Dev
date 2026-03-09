"""
VESPER Security Evaluation Framework

Comprehensive, conference-grade evaluation of IoT security testing results.
Produces quantitative metrics, statistical analysis, and paper-ready
artifacts suitable for top-tier security venues (IEEE S&P, USENIX Security,
CCS, NDSS).

Evaluation Dimensions:
    1. CVSS 3.1 Scoring — Industry-standard vulnerability severity
    2. MITRE ATT&CK for IoT — Tactic/technique coverage mapping
    3. Attack Surface Analysis — Per-device, per-category, cross-layer
    4. Statistical Rigor — CI, effect sizes, Fisher's exact, chi-squared
    5. Comparative Analysis — Device-type heat maps, attack-class breakdown
    6. Temporal Analysis — Attack latency distributions, time-to-exploit
    7. Kill Chain Coverage — End-to-end attack path completeness
    8. LaTeX/Figure Generation — Paper-ready tables and plots
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    from matplotlib.patches import FancyBboxPatch
    import matplotlib.colors as mcolors
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

try:
    from scipy import stats as scipy_stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


# =============================================================================
# CVSS 3.1 Base Score Calculator
# =============================================================================

class CVSSv31:
    """
    CVSS v3.1 Base Score calculator following FIRST specification.
    https://www.first.org/cvss/v3.1/specification-document

    We compute base scores from attack vector properties to produce
    industry-standard severity ratings for each vulnerability.
    """

    # Metric value weights per CVSS 3.1 spec
    AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20}  # Attack Vector
    AC = {"L": 0.77, "H": 0.44}                          # Attack Complexity
    PR_UNCHANGED = {"N": 0.85, "L": 0.62, "H": 0.27}    # Privileges Required (scope unchanged)
    PR_CHANGED   = {"N": 0.85, "L": 0.68, "H": 0.50}    # Privileges Required (scope changed)
    UI = {"N": 0.85, "R": 0.62}                          # User Interaction
    CIA = {"H": 0.56, "L": 0.22, "N": 0.0}              # Confidentiality/Integrity/Availability

    @classmethod
    def compute(
        cls,
        av: str = "N",   # Attack Vector: N(etwork), A(djacent), L(ocal), P(hysical)
        ac: str = "L",   # Attack Complexity: L(ow), H(igh)
        pr: str = "N",   # Privileges Required: N(one), L(ow), H(igh)
        ui: str = "N",   # User Interaction: N(one), R(equired)
        s: str = "U",    # Scope: U(nchanged), C(hanged)
        c: str = "N",    # Confidentiality: N(one), L(ow), H(igh)
        i: str = "N",    # Integrity: N(one), L(ow), H(igh)
        a: str = "N",    # Availability: N(one), L(ow), H(igh)
    ) -> Tuple[float, str]:
        """
        Compute CVSS 3.1 base score and severity rating.

        Returns:
            (score, severity) where score is 0.0-10.0 and severity is
            "Critical"/"High"/"Medium"/"Low"/"None".
        """
        # Impact Sub-Score (ISS)
        iss = 1.0 - (
            (1.0 - cls.CIA[c]) *
            (1.0 - cls.CIA[i]) *
            (1.0 - cls.CIA[a])
        )

        # Impact
        if s == "U":
            impact = 6.42 * iss
        else:
            impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15

        if impact <= 0:
            return 0.0, "None"

        # Exploitability
        pr_weights = cls.PR_CHANGED if s == "C" else cls.PR_UNCHANGED
        exploitability = 8.22 * cls.AV[av] * cls.AC[ac] * pr_weights[pr] * cls.UI[ui]

        # Base Score
        if s == "U":
            base = min(impact + exploitability, 10.0)
        else:
            base = min(1.08 * (impact + exploitability), 10.0)

        # Round up to 1 decimal
        base = math.ceil(base * 10) / 10

        # Severity rating
        if base >= 9.0:
            severity = "Critical"
        elif base >= 7.0:
            severity = "High"
        elif base >= 4.0:
            severity = "Medium"
        elif base > 0.0:
            severity = "Low"
        else:
            severity = "None"

        return base, severity

    @classmethod
    def vector_string(
        cls,
        av: str = "N", ac: str = "L", pr: str = "N", ui: str = "N",
        s: str = "U", c: str = "N", i: str = "N", a: str = "N",
    ) -> str:
        """Generate CVSS 3.1 vector string."""
        return f"CVSS:3.1/AV:{av}/AC:{ac}/PR:{pr}/UI:{ui}/S:{s}/C:{c}/I:{i}/A:{a}"


# =============================================================================
# MITRE ATT&CK for IoT Mapping
# =============================================================================

# Mapping of our attack categories → MITRE ATT&CK for IoT Techniques
# Reference: https://attack.mitre.org/techniques/iot/
MITRE_ATTACK_IOT: Dict[str, Dict[str, Any]] = {
    # --- Initial Access ---
    "authentication_bypass": {
        "tactic": "Initial Access",
        "technique_id": "T0866",
        "technique": "Exploitation of Remote Services",
        "sub_techniques": ["T0866.001"],
        "description": "Exploit weak/missing authentication to gain device access",
    },
    # --- Execution ---
    "command_injection": {
        "tactic": "Execution",
        "technique_id": "T0807",
        "technique": "Command-Line Interface",
        "sub_techniques": [],
        "description": "Inject commands through protocol interface",
    },
    "firmware_update_attack": {
        "tactic": "Persistence",
        "technique_id": "T0857",
        "technique": "System Firmware",
        "sub_techniques": ["T0857.001"],
        "description": "Inject malicious firmware via unvalidated update mechanism",
    },
    # --- Discovery ---
    "information_disclosure": {
        "tactic": "Discovery",
        "technique_id": "T0846",
        "technique": "Remote System Discovery",
        "sub_techniques": [],
        "description": "Extract sensitive device state and credentials via debug interfaces",
    },
    # --- Lateral Movement ---
    "mqtt_message_injection": {
        "tactic": "Lateral Movement",
        "technique_id": "T0867",
        "technique": "Lateral Tool Transfer",
        "sub_techniques": [],
        "description": "Inject messages to control adjacent devices via MQTT bus",
    },
    "mqtt_topic_hijack": {
        "tactic": "Collection",
        "technique_id": "T0802",
        "technique": "Automated Collection",
        "sub_techniques": [],
        "description": "Intercept and modify device commands in transit",
    },
    # --- Impact ---
    "denial_of_service": {
        "tactic": "Impact",
        "technique_id": "T0814",
        "technique": "Denial of Service",
        "sub_techniques": [],
        "description": "Degrade or crash firmware via resource exhaustion",
    },
    "state_manipulation": {
        "tactic": "Impact",
        "technique_id": "T0831",
        "technique": "Manipulation of Control",
        "sub_techniques": [],
        "description": "Alter device operational state (disarm sensors, change setpoints)",
    },
    # --- Collection ---
    "mqtt_eavesdropping": {
        "tactic": "Collection",
        "technique_id": "T0801",
        "technique": "Monitor Process State",
        "sub_techniques": [],
        "description": "Passively subscribe to device topics to exfiltrate data",
    },
    # --- Credential Access ---
    "buffer_overflow": {
        "tactic": "Privilege Escalation",
        "technique_id": "T0890",
        "technique": "Exploitation for Privilege Escalation",
        "sub_techniques": [],
        "description": "Overflow buffers to corrupt memory and escalate privileges",
    },
    # --- Evasion ---
    "replay_attack": {
        "tactic": "Evasion",
        "technique_id": "T0858",
        "technique": "Change Operating Mode",
        "sub_techniques": [],
        "description": "Replay captured commands to change device state without detection",
    },
    "protocol_fuzzing": {
        "tactic": "Execution",
        "technique_id": "T0807",
        "technique": "Command-Line Interface",
        "sub_techniques": [],
        "description": "Send malformed input to trigger undefined behavior",
    },
    # --- Network attacks ---
    "protocol_replay": {
        "tactic": "Evasion",
        "technique_id": "T0858",
        "technique": "Change Operating Mode",
        "sub_techniques": [],
        "description": "Replay Zigbee frames to retrigger device actions",
    },
    "protocol_downgrade": {
        "tactic": "Credential Access",
        "technique_id": "T0859",
        "technique": "Valid Accounts",
        "sub_techniques": [],
        "description": "Extract Zigbee network key via well-known Trust Center key",
    },
    "arp_spoofing": {
        "tactic": "Collection",
        "technique_id": "T0830",
        "technique": "Man in the Middle",
        "sub_techniques": [],
        "description": "ARP cache poisoning to redirect traffic through attacker",
    },
    "dns_poisoning": {
        "tactic": "Initial Access",
        "technique_id": "T0848",
        "technique": "Rogue Master",
        "sub_techniques": [],
        "description": "Redirect device cloud connections via DNS spoofing",
    },
    "deauthentication_attack": {
        "tactic": "Impact",
        "technique_id": "T0814",
        "technique": "Denial of Service",
        "sub_techniques": [],
        "description": "WiFi deauthentication to disconnect IoT devices",
    },
    "evil_twin_ap": {
        "tactic": "Initial Access",
        "technique_id": "T0866",
        "technique": "Exploitation of Remote Services",
        "sub_techniques": [],
        "description": "Fake AP to capture credentials after deauthentication",
    },
    "tcp_connection_hijack": {
        "tactic": "Collection",
        "technique_id": "T0830",
        "technique": "Man in the Middle",
        "sub_techniques": [],
        "description": "TCP session hijacking to inject commands",
    },
    "tcp_man_in_the_middle": {
        "tactic": "Collection",
        "technique_id": "T0830",
        "technique": "Man in the Middle",
        "sub_techniques": [],
        "description": "Proxy TCP connections to intercept credentials",
    },
    "network_denial_of_service": {
        "tactic": "Impact",
        "technique_id": "T0814",
        "technique": "Denial of Service",
        "sub_techniques": [],
        "description": "Network-layer DoS via connection flooding",
    },
    "traffic_analysis": {
        "tactic": "Discovery",
        "technique_id": "T0840",
        "technique": "Network Sniffing",
        "sub_techniques": [],
        "description": "Passive traffic fingerprinting to identify devices",
    },
}

# CVSS 3.1 vectors for each attack (pre-mapped from firmware behavior)
ATTACK_CVSS_VECTORS: Dict[str, Dict[str, str]] = {
    # Firmware attacks
    "Buffer Overflow (Command Buffer)":        {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "L", "a": "H"},
    "Buffer Overflow (SET_ID)":                {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "H", "a": "L"},
    "Authentication Bypass (No Token)":        {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "C", "c": "H", "i": "H", "a": "H"},
    "Authentication Bypass (Weak Token Validation)": {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "C", "c": "H", "i": "H", "a": "H"},
    "Information Disclosure (Debug Dump)":     {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "H", "i": "N", "a": "N"},
    "Information Disclosure (Token Leakage)":  {"av": "N", "ac": "H", "pr": "N", "ui": "N", "s": "U", "c": "L", "i": "N", "a": "N"},
    "Command Injection (Newline)":             {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "H", "a": "L"},
    "Command Injection (Null Byte)":           {"av": "N", "ac": "H", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "L", "a": "N"},
    "Firmware Update Buffer Overflow":         {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "C", "c": "H", "i": "H", "a": "H"},
    "Unsigned Firmware Update":                {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "C", "c": "H", "i": "H", "a": "H"},
    "Denial of Service (Command Flood)":       {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "N", "a": "H"},
    "Denial of Service (Large Payload)":       {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "N", "a": "L"},
    "State Manipulation (Sensor Disarm)":      {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "C", "c": "N", "i": "H", "a": "H"},
    "State Manipulation (Calibration Abuse)":  {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "L", "a": "N"},
    "Replay Attack":                           {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "H", "a": "L"},
    "Protocol Fuzzing (Random)":               {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "L", "a": "H"},
    "Format String Attack":                    {"av": "N", "ac": "H", "pr": "N", "ui": "N", "s": "C", "c": "H", "i": "H", "a": "H"},
    "Out-of-Bounds Write (Schedule Array)":    {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "H", "a": "L"},
    # Network attacks
    "MQTT Unauthorized Subscribe (Eavesdropping)": {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "H", "i": "N", "a": "N"},
    "MQTT Message Injection":                  {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "C", "c": "N", "i": "H", "a": "L"},
    "MQTT Topic Hijack (Command MITM)":        {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "C", "c": "H", "i": "H", "a": "N"},
    "TCP Connection Hijack":                   {"av": "A", "ac": "H", "pr": "N", "ui": "N", "s": "U", "c": "L", "i": "H", "a": "N"},
    "TCP Man-in-the-Middle Proxy":             {"av": "A", "ac": "H", "pr": "N", "ui": "N", "s": "U", "c": "H", "i": "H", "a": "N"},
    "TCP Connection Flood":                    {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "N", "a": "H"},
    "Zigbee Frame Replay":                     {"av": "A", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "H", "a": "N"},
    "Zigbee Network Key Extraction":           {"av": "A", "ac": "L", "pr": "N", "ui": "N", "s": "C", "c": "H", "i": "H", "a": "N"},
    "Protocol Downgrade (No Encryption)":      {"av": "N", "ac": "H", "pr": "N", "ui": "N", "s": "U", "c": "H", "i": "N", "a": "N"},
    "ARP Cache Poisoning (Simulated)":         {"av": "A", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "H", "i": "H", "a": "N"},
    "DNS Poisoning (Simulated)":               {"av": "N", "ac": "L", "pr": "N", "ui": "N", "s": "C", "c": "H", "i": "H", "a": "N"},
    "WiFi Deauthentication Attack (Simulated)": {"av": "A", "ac": "L", "pr": "N", "ui": "N", "s": "U", "c": "N", "i": "N", "a": "H"},
    "Evil Twin Access Point (Simulated)":      {"av": "A", "ac": "L", "pr": "N", "ui": "N", "s": "C", "c": "H", "i": "H", "a": "N"},
    "Traffic Fingerprinting":                  {"av": "A", "ac": "H", "pr": "N", "ui": "N", "s": "U", "c": "L", "i": "N", "a": "N"},
}


# IoT Cyber Kill Chain stages (Lockheed Martin-inspired, IoT-adapted)
IOT_KILL_CHAIN = [
    "Reconnaissance",
    "Weaponization",
    "Delivery",
    "Exploitation",
    "Installation",
    "Command & Control",
    "Actions on Objectives",
]

ATTACK_KILL_CHAIN_STAGE: Dict[str, List[str]] = {
    "information_disclosure":   ["Reconnaissance"],
    "traffic_analysis":         ["Reconnaissance"],
    "mqtt_eavesdropping":       ["Reconnaissance", "Collection"],
    "buffer_overflow":          ["Weaponization", "Exploitation"],
    "command_injection":        ["Weaponization", "Delivery"],
    "protocol_fuzzing":         ["Weaponization"],
    "authentication_bypass":    ["Exploitation"],
    "firmware_update_attack":   ["Delivery", "Installation"],
    "replay_attack":            ["Delivery"],
    "protocol_replay":          ["Delivery"],
    "protocol_downgrade":       ["Exploitation"],
    "mqtt_message_injection":   ["Delivery", "Command & Control"],
    "mqtt_topic_hijack":        ["Command & Control"],
    "arp_spoofing":             ["Exploitation", "Command & Control"],
    "dns_poisoning":            ["Exploitation", "Command & Control"],
    "evil_twin_ap":             ["Exploitation", "Command & Control"],
    "tcp_connection_hijack":    ["Exploitation"],
    "tcp_man_in_the_middle":    ["Exploitation", "Collection"],
    "deauthentication_attack":  ["Delivery", "Actions on Objectives"],
    "denial_of_service":        ["Actions on Objectives"],
    "network_denial_of_service": ["Actions on Objectives"],
    "state_manipulation":       ["Actions on Objectives"],
}


# =============================================================================
# Core Data Structures
# =============================================================================

@dataclass
class ScoredAttack:
    """A single attack with full evaluation metadata."""
    name: str
    category: str
    success: bool
    severity_original: str
    # CVSS
    cvss_score: float = 0.0
    cvss_severity: str = "None"
    cvss_vector: str = ""
    # MITRE
    mitre_tactic: str = ""
    mitre_technique_id: str = ""
    mitre_technique: str = ""
    # Kill chain
    kill_chain_stages: List[str] = field(default_factory=list)
    # Timing
    duration_ms: float = 0.0
    # Context
    device_type: str = ""
    layer: str = ""  # "firmware" or "network"
    evidence: List[str] = field(default_factory=list)
    impact: str = ""
    mitigation: str = ""
    cve_reference: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "success": self.success,
            "cvss_score": self.cvss_score,
            "cvss_severity": self.cvss_severity,
            "cvss_vector": self.cvss_vector,
            "mitre_tactic": self.mitre_tactic,
            "mitre_technique_id": self.mitre_technique_id,
            "mitre_technique": self.mitre_technique,
            "kill_chain_stages": self.kill_chain_stages,
            "duration_ms": self.duration_ms,
            "device_type": self.device_type,
            "layer": self.layer,
            "impact": self.impact,
            "mitigation": self.mitigation,
            "cve_reference": self.cve_reference,
        }


# =============================================================================
# Security Evaluator
# =============================================================================

class SecurityEvaluator:
    """
    Conference-grade security evaluation framework.

    Loads raw attack results, enriches them with CVSS scores and MITRE
    ATT&CK mappings, then computes quantitative metrics and generates
    paper-ready LaTeX tables and matplotlib figures.

    Usage:
        evaluator = SecurityEvaluator("results/security")
        evaluator.load_results()
        report = evaluator.evaluate()
        evaluator.generate_latex(report, "results/report")
        evaluator.generate_figures(report, "results/report/figures")
    """

    DEVICE_TYPES = [
        "motion_sensor", "temperature_sensor", "smart_light",
        "humidity_sensor", "door_sensor", "smart_plug",
    ]

    DEVICE_DISPLAY_NAMES = {
        "motion_sensor": "Motion Sensor",
        "temperature_sensor": "Temp. Sensor",
        "smart_light": "Smart Light",
        "humidity_sensor": "Humidity Sensor",
        "door_sensor": "Door Sensor",
        "smart_plug": "Smart Plug",
    }

    SEVERITY_ORDER = ["Critical", "High", "Medium", "Low", "None"]
    SEVERITY_COLORS = {
        "Critical": "#d32f2f",
        "High": "#f57c00",
        "Medium": "#fbc02d",
        "Low": "#388e3c",
        "None": "#9e9e9e",
    }

    def __init__(self, results_dir: str = "results/security"):
        self.results_dir = Path(results_dir)
        self.firmware_results: Dict[str, List[Dict]] = {}  # device_type → attacks
        self.network_results: List[Dict] = []
        self.scored_attacks: List[ScoredAttack] = []

    # --------------------------------------------------------------------- #
    # Loading
    # --------------------------------------------------------------------- #

    def load_results(self, timestamp: Optional[str] = None):
        """
        Load attack results from JSON files.
        If timestamp is None, uses the most recent results.
        """
        # Find the most recent timestamp
        if timestamp is None:
            summaries = sorted(self.results_dir.glob("security_summary_*.json"))
            if not summaries:
                raise FileNotFoundError(f"No security_summary files in {self.results_dir}")
            timestamp = summaries[-1].stem.replace("security_summary_", "")

        logger.info(f"Loading security results with timestamp: {timestamp}")

        # Load per-device firmware results
        for device_type in self.DEVICE_TYPES:
            fpath = self.results_dir / f"firmware_attacks_{device_type}_{timestamp}.json"
            if fpath.exists():
                with open(fpath) as f:
                    self.firmware_results[device_type] = json.load(f)
                logger.info(f"  Loaded {len(self.firmware_results[device_type])} attacks for {device_type}")

        # Load network results
        npath = self.results_dir / f"network_attacks_{timestamp}.json"
        if npath.exists():
            with open(npath) as f:
                self.network_results = json.load(f)
            logger.info(f"  Loaded {len(self.network_results)} network attacks")

    # --------------------------------------------------------------------- #
    # Scoring & Enrichment
    # --------------------------------------------------------------------- #

    def _score_attack(self, raw: Dict, device_type: str, layer: str) -> ScoredAttack:
        """Enrich a raw attack result with CVSS, MITRE, and kill chain data."""
        name = raw["attack_name"]
        category = raw["category"]

        # CVSS scoring
        cvss_vec = ATTACK_CVSS_VECTORS.get(name, {})
        if cvss_vec:
            score, severity = CVSSv31.compute(**cvss_vec)
            vector_str = CVSSv31.vector_string(**cvss_vec)
        else:
            score, severity = 0.0, "None"
            vector_str = ""

        # MITRE ATT&CK
        mitre = MITRE_ATTACK_IOT.get(category, {})

        # Kill chain
        kc_stages = ATTACK_KILL_CHAIN_STAGE.get(category, [])

        return ScoredAttack(
            name=name,
            category=category,
            success=raw.get("success", False),
            severity_original=raw.get("severity", ""),
            cvss_score=score,
            cvss_severity=severity,
            cvss_vector=vector_str,
            mitre_tactic=mitre.get("tactic", ""),
            mitre_technique_id=mitre.get("technique_id", ""),
            mitre_technique=mitre.get("technique", ""),
            kill_chain_stages=kc_stages,
            duration_ms=raw.get("duration_ms", 0.0),
            device_type=device_type,
            layer=layer,
            evidence=raw.get("evidence", []),
            impact=raw.get("impact", ""),
            mitigation=raw.get("mitigation", ""),
            cve_reference=raw.get("cve_reference", ""),
        )

    def _enrich_all(self):
        """Score and enrich all loaded attack results."""
        self.scored_attacks = []

        for device_type, attacks in self.firmware_results.items():
            for raw in attacks:
                scored = self._score_attack(raw, device_type, "firmware")
                self.scored_attacks.append(scored)

        for raw in self.network_results:
            scored = self._score_attack(raw, "network", "network")
            self.scored_attacks.append(scored)

        logger.info(f"Enriched {len(self.scored_attacks)} total attacks")

    # --------------------------------------------------------------------- #
    # Core Evaluation
    # --------------------------------------------------------------------- #

    def evaluate(self) -> Dict[str, Any]:
        """
        Run the full evaluation pipeline. Returns a comprehensive report dict.
        """
        self._enrich_all()

        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": self._compute_summary(),
            "cvss_analysis": self._cvss_analysis(),
            "mitre_coverage": self._mitre_coverage(),
            "kill_chain_analysis": self._kill_chain_analysis(),
            "device_comparison": self._device_comparison(),
            "attack_surface": self._attack_surface_analysis(),
            "temporal_analysis": self._temporal_analysis(),
            "statistical_tests": self._statistical_tests(),
            "cross_layer_analysis": self._cross_layer_analysis(),
            "scored_attacks": [a.to_dict() for a in self.scored_attacks],
        }

        return report

    def _compute_summary(self) -> Dict[str, Any]:
        """High-level summary statistics."""
        fw_attacks = [a for a in self.scored_attacks if a.layer == "firmware"]
        net_attacks = [a for a in self.scored_attacks if a.layer == "network"]
        all_attacks = self.scored_attacks

        def _stats(attacks):
            total = len(attacks)
            successful = sum(1 for a in attacks if a.success)
            return {
                "total": total,
                "successful": successful,
                "rate": successful / total if total > 0 else 0.0,
                "mean_cvss": float(np.mean([a.cvss_score for a in attacks])) if attacks else 0.0,
                "max_cvss": max((a.cvss_score for a in attacks), default=0.0),
                "mean_cvss_successful": float(np.mean([
                    a.cvss_score for a in attacks if a.success
                ])) if any(a.success for a in attacks) else 0.0,
                "unique_categories": len(set(a.category for a in attacks)),
            }

        return {
            "total_attacks": len(all_attacks),
            "firmware": _stats(fw_attacks),
            "network": _stats(net_attacks),
            "combined": _stats(all_attacks),
            "device_types_tested": len(self.firmware_results),
            "attack_categories": len(set(a.category for a in all_attacks)),
        }

    def _cvss_analysis(self) -> Dict[str, Any]:
        """CVSS 3.1 score distribution analysis."""
        scores = [a.cvss_score for a in self.scored_attacks]
        successful_scores = [a.cvss_score for a in self.scored_attacks if a.success]

        # Distribution by severity
        severity_dist = defaultdict(lambda: {"total": 0, "exploited": 0})
        for a in self.scored_attacks:
            severity_dist[a.cvss_severity]["total"] += 1
            if a.success:
                severity_dist[a.cvss_severity]["exploited"] += 1

        # Compute exploit rates per severity
        for sev_data in severity_dist.values():
            sev_data["exploit_rate"] = (
                sev_data["exploited"] / sev_data["total"]
                if sev_data["total"] > 0 else 0.0
            )

        # Weighted risk score (CVSS × exploit probability)
        weighted_risk = sum(
            a.cvss_score * (1.0 if a.success else 0.0)
            for a in self.scored_attacks
        ) / len(self.scored_attacks) if self.scored_attacks else 0.0

        result = {
            "score_distribution": {
                "mean": float(np.mean(scores)) if scores else 0.0,
                "median": float(np.median(scores)) if scores else 0.0,
                "std": float(np.std(scores)) if scores else 0.0,
                "min": min(scores) if scores else 0.0,
                "max": max(scores) if scores else 0.0,
            },
            "successful_distribution": {
                "mean": float(np.mean(successful_scores)) if successful_scores else 0.0,
                "median": float(np.median(successful_scores)) if successful_scores else 0.0,
                "std": float(np.std(successful_scores)) if successful_scores else 0.0,
            },
            "severity_breakdown": dict(severity_dist),
            "weighted_risk_score": weighted_risk,
        }

        # Confidence interval on mean CVSS
        if HAS_SCIPY and len(scores) >= 2:
            ci = scipy_stats.t.interval(
                0.95, df=len(scores) - 1,
                loc=np.mean(scores), scale=scipy_stats.sem(scores)
            )
            result["mean_cvss_95ci"] = [float(ci[0]), float(ci[1])]

        return result

    def _mitre_coverage(self) -> Dict[str, Any]:
        """MITRE ATT&CK for IoT tactic/technique coverage."""
        # Tactic coverage
        all_tactics = set()
        exploited_tactics = set()
        technique_map: Dict[str, Dict[str, Any]] = {}

        for a in self.scored_attacks:
            if a.mitre_tactic:
                all_tactics.add(a.mitre_tactic)
                if a.success:
                    exploited_tactics.add(a.mitre_tactic)

            if a.mitre_technique_id:
                tid = a.mitre_technique_id
                if tid not in technique_map:
                    technique_map[tid] = {
                        "technique": a.mitre_technique,
                        "tactic": a.mitre_tactic,
                        "total_attempts": 0,
                        "successful": 0,
                        "devices_affected": set(),
                    }
                technique_map[tid]["total_attempts"] += 1
                if a.success:
                    technique_map[tid]["successful"] += 1
                    technique_map[tid]["devices_affected"].add(a.device_type)

        # Serialize sets
        for tid in technique_map:
            technique_map[tid]["devices_affected"] = list(technique_map[tid]["devices_affected"])
            total = technique_map[tid]["total_attempts"]
            technique_map[tid]["exploit_rate"] = (
                technique_map[tid]["successful"] / total if total > 0 else 0.0
            )

        # IoT ATT&CK has 12 tactics; compute our coverage
        iot_tactics = {
            "Initial Access", "Execution", "Persistence", "Privilege Escalation",
            "Defense Evasion", "Credential Access", "Discovery", "Lateral Movement",
            "Collection", "Command & Control", "Impact", "Evasion",
        }

        return {
            "tactics_attempted": sorted(all_tactics),
            "tactics_exploited": sorted(exploited_tactics),
            "tactic_coverage": len(all_tactics) / len(iot_tactics),
            "tactic_exploit_coverage": len(exploited_tactics) / len(iot_tactics),
            "techniques": technique_map,
            "unique_techniques": len(technique_map),
        }

    def _kill_chain_analysis(self) -> Dict[str, Any]:
        """IoT Cyber Kill Chain stage coverage."""
        stage_coverage: Dict[str, Dict[str, Any]] = {}

        for stage in IOT_KILL_CHAIN:
            attacks_in_stage = [
                a for a in self.scored_attacks
                if stage in a.kill_chain_stages
            ]
            exploited = [a for a in attacks_in_stage if a.success]

            stage_coverage[stage] = {
                "total_attacks": len(attacks_in_stage),
                "exploited": len(exploited),
                "exploit_rate": len(exploited) / len(attacks_in_stage) if attacks_in_stage else 0.0,
                "categories": sorted(set(a.category for a in attacks_in_stage)),
                "max_cvss": max((a.cvss_score for a in attacks_in_stage), default=0.0),
            }

        # Longest exploitable kill chain path
        exploited_stages = [
            s for s in IOT_KILL_CHAIN
            if stage_coverage[s]["exploited"] > 0
        ]

        return {
            "stages": stage_coverage,
            "total_stages": len(IOT_KILL_CHAIN),
            "stages_covered": len([s for s in stage_coverage if stage_coverage[s]["total_attacks"] > 0]),
            "stages_exploited": len(exploited_stages),
            "kill_chain_completeness": len(exploited_stages) / len(IOT_KILL_CHAIN),
            "longest_exploitable_chain": exploited_stages,
        }

    def _device_comparison(self) -> Dict[str, Any]:
        """Cross-device vulnerability comparison matrix."""
        comparison: Dict[str, Dict[str, Any]] = {}

        for device_type in self.DEVICE_TYPES:
            dev_attacks = [a for a in self.scored_attacks if a.device_type == device_type]
            if not dev_attacks:
                continue

            successful = [a for a in dev_attacks if a.success]
            cvss_scores = [a.cvss_score for a in dev_attacks]
            successful_cvss = [a.cvss_score for a in successful]

            # Per-category breakdown
            cat_breakdown = defaultdict(lambda: {"total": 0, "exploited": 0})
            for a in dev_attacks:
                cat_breakdown[a.category]["total"] += 1
                if a.success:
                    cat_breakdown[a.category]["exploited"] += 1

            comparison[device_type] = {
                "total_attacks": len(dev_attacks),
                "successful": len(successful),
                "exploit_rate": len(successful) / len(dev_attacks) if dev_attacks else 0.0,
                "mean_cvss": float(np.mean(cvss_scores)),
                "mean_cvss_successful": float(np.mean(successful_cvss)) if successful_cvss else 0.0,
                "max_cvss": max(cvss_scores),
                "critical_vulns": sum(1 for a in dev_attacks if a.cvss_severity == "Critical" and a.success),
                "high_vulns": sum(1 for a in dev_attacks if a.cvss_severity == "High" and a.success),
                "category_breakdown": dict(cat_breakdown),
                # Time-to-exploit (median of successful attacks)
                "median_tte_ms": float(np.median([a.duration_ms for a in successful])) if successful else 0.0,
            }

        return comparison

    def _attack_surface_analysis(self) -> Dict[str, Any]:
        """
        Attack surface quantification across firmware and network layers.
        Produces the data for an attack surface radar/spider chart.
        """
        # Aggregate by category
        categories = sorted(set(a.category for a in self.scored_attacks))
        surface: Dict[str, Dict[str, Any]] = {}

        for cat in categories:
            cat_attacks = [a for a in self.scored_attacks if a.category == cat]
            exploited = [a for a in cat_attacks if a.success]
            surface[cat] = {
                "total": len(cat_attacks),
                "exploited": len(exploited),
                "exploit_rate": len(exploited) / len(cat_attacks) if cat_attacks else 0.0,
                "mean_cvss": float(np.mean([a.cvss_score for a in cat_attacks])),
                "max_cvss": max((a.cvss_score for a in cat_attacks), default=0.0),
                "devices_affected": len(set(a.device_type for a in exploited)),
                "layers": sorted(set(a.layer for a in cat_attacks)),
            }

        # Risk score per category = exploit_rate × mean_CVSS × breadth
        for cat, data in surface.items():
            breadth = data["devices_affected"] / max(len(self.DEVICE_TYPES), 1)
            data["risk_score"] = data["exploit_rate"] * data["mean_cvss"] * (1 + breadth)

        return surface

    def _temporal_analysis(self) -> Dict[str, Any]:
        """Attack timing/duration analysis."""
        fw_durations = [a.duration_ms for a in self.scored_attacks if a.layer == "firmware"]
        net_durations = [a.duration_ms for a in self.scored_attacks if a.layer == "network"]
        succ_durations = [a.duration_ms for a in self.scored_attacks if a.success]
        fail_durations = [a.duration_ms for a in self.scored_attacks if not a.success]

        def _duration_stats(durations: List[float]) -> Dict[str, float]:
            if not durations:
                return {}
            arr = np.array(durations)
            return {
                "mean_ms": float(arr.mean()),
                "median_ms": float(np.median(arr)),
                "std_ms": float(arr.std()),
                "p95_ms": float(np.percentile(arr, 95)),
                "p99_ms": float(np.percentile(arr, 99)),
                "min_ms": float(arr.min()),
                "max_ms": float(arr.max()),
                "total_ms": float(arr.sum()),
            }

        result = {
            "firmware_durations": _duration_stats(fw_durations),
            "network_durations": _duration_stats(net_durations),
            "successful_durations": _duration_stats(succ_durations),
            "failed_durations": _duration_stats(fail_durations),
        }

        # Time-to-exploit distribution by severity
        tte_by_severity: Dict[str, List[float]] = defaultdict(list)
        for a in self.scored_attacks:
            if a.success:
                tte_by_severity[a.cvss_severity].append(a.duration_ms)

        result["tte_by_severity"] = {
            sev: _duration_stats(durations)
            for sev, durations in tte_by_severity.items()
        }

        return result

    def _statistical_tests(self) -> Dict[str, Any]:
        """
        Statistical significance tests for key comparisons.
        Provides the rigor expected at top venues.
        """
        results = {}

        if not HAS_SCIPY:
            results["warning"] = "scipy not available — statistical tests skipped"
            return results

        # 1. Chi-squared test: Is exploit rate independent of device type?
        device_types = [dt for dt in self.DEVICE_TYPES if dt in self.firmware_results]
        if len(device_types) >= 2:
            contingency = []
            for dt in device_types:
                dt_attacks = [a for a in self.scored_attacks if a.device_type == dt and a.layer == "firmware"]
                succ = sum(1 for a in dt_attacks if a.success)
                fail = len(dt_attacks) - succ
                contingency.append([succ, fail])

            contingency = np.array(contingency)
            chi2, p_chi2, dof, expected = scipy_stats.chi2_contingency(contingency)
            results["chi2_device_independence"] = {
                "test": "Chi-squared test of independence",
                "hypothesis": "H0: Exploit rate is independent of device type",
                "chi2_statistic": float(chi2),
                "p_value": float(p_chi2),
                "dof": int(dof),
                "significant_at_005": p_chi2 < 0.05,
                "significant_at_001": p_chi2 < 0.01,
                "interpretation": (
                    "Reject H0 — exploit rate differs significantly across devices"
                    if p_chi2 < 0.05 else
                    "Fail to reject H0 — no significant difference across devices"
                ),
            }

        # 2. Fisher's exact test: Firmware vs network exploit rates
        fw_attacks = [a for a in self.scored_attacks if a.layer == "firmware"]
        net_attacks = [a for a in self.scored_attacks if a.layer == "network"]
        if fw_attacks and net_attacks:
            fw_succ = sum(1 for a in fw_attacks if a.success)
            fw_fail = len(fw_attacks) - fw_succ
            net_succ = sum(1 for a in net_attacks if a.success)
            net_fail = len(net_attacks) - net_succ

            table = np.array([[fw_succ, fw_fail], [net_succ, net_fail]])
            # Use chi2 for larger tables (Fisher's exact for 2×2)
            if min(table.flatten()) < 5:
                odds, p_fisher = scipy_stats.fisher_exact(table)
                test_name = "Fisher's exact test"
            else:
                chi2_val, p_fisher, _, _ = scipy_stats.chi2_contingency(table, correction=True)
                odds = (fw_succ * net_fail) / (fw_fail * net_succ) if fw_fail * net_succ > 0 else float('inf')
                test_name = "Chi-squared test (Yates correction)"

            results["fw_vs_network_exploit_rate"] = {
                "test": test_name,
                "hypothesis": "H0: Firmware and network exploit rates are equal",
                "firmware_rate": fw_succ / len(fw_attacks),
                "network_rate": net_succ / len(net_attacks),
                "odds_ratio": float(odds),
                "p_value": float(p_fisher),
                "significant_at_005": p_fisher < 0.05,
                "interpretation": (
                    "Reject H0 — exploit rates differ between layers"
                    if p_fisher < 0.05 else
                    "Fail to reject H0 — no significant difference between layers"
                ),
            }

        # 3. Kruskal-Wallis: CVSS scores across device types
        if len(device_types) >= 3:
            groups = []
            for dt in device_types:
                dt_scores = [a.cvss_score for a in self.scored_attacks if a.device_type == dt and a.success]
                if dt_scores:
                    groups.append(dt_scores)

            if len(groups) >= 2:
                h_stat, p_kw = scipy_stats.kruskal(*groups)
                results["kruskal_wallis_cvss"] = {
                    "test": "Kruskal-Wallis H test",
                    "hypothesis": "H0: CVSS score distributions are equal across device types",
                    "h_statistic": float(h_stat),
                    "p_value": float(p_kw),
                    "significant_at_005": p_kw < 0.05,
                }

        # 4. Effect size: Cohen's w for device exploit rates
        if len(device_types) >= 2:
            rates = []
            for dt in device_types:
                dt_attacks = [a for a in self.scored_attacks if a.device_type == dt and a.layer == "firmware"]
                if dt_attacks:
                    rates.append(sum(1 for a in dt_attacks if a.success) / len(dt_attacks))

            results["exploit_rate_effect_size"] = {
                "metric": "Coefficient of variation of exploit rates",
                "rates_by_device": {
                    dt: rates[i] for i, dt in enumerate(device_types) if i < len(rates)
                },
                "mean_rate": float(np.mean(rates)),
                "std_rate": float(np.std(rates)),
                "cv": float(np.std(rates) / np.mean(rates)) if np.mean(rates) > 0 else 0.0,
            }

        # 5. Mann-Whitney U: time-to-exploit for successful vs failed
        succ_dur = [a.duration_ms for a in self.scored_attacks if a.success and a.duration_ms > 0]
        fail_dur = [a.duration_ms for a in self.scored_attacks if not a.success and a.duration_ms > 0]
        if len(succ_dur) >= 2 and len(fail_dur) >= 2:
            u_stat, p_mw = scipy_stats.mannwhitneyu(succ_dur, fail_dur, alternative='two-sided')
            results["mann_whitney_duration"] = {
                "test": "Mann-Whitney U test",
                "hypothesis": "H0: Duration distributions are equal for successful vs failed attacks",
                "u_statistic": float(u_stat),
                "p_value": float(p_mw),
                "significant_at_005": p_mw < 0.05,
                "successful_median_ms": float(np.median(succ_dur)),
                "failed_median_ms": float(np.median(fail_dur)),
            }

        return results

    def _cross_layer_analysis(self) -> Dict[str, Any]:
        """Analyze attack effectiveness across firmware and network layers."""
        fw = [a for a in self.scored_attacks if a.layer == "firmware"]
        net = [a for a in self.scored_attacks if a.layer == "network"]

        def _layer_stats(attacks):
            if not attacks:
                return {}
            succ = [a for a in attacks if a.success]
            return {
                "total": len(attacks),
                "successful": len(succ),
                "exploit_rate": len(succ) / len(attacks),
                "mean_cvss": float(np.mean([a.cvss_score for a in attacks])),
                "mean_cvss_exploited": float(np.mean([a.cvss_score for a in succ])) if succ else 0.0,
                "median_tte_ms": float(np.median([a.duration_ms for a in succ])) if succ else 0.0,
                "categories": len(set(a.category for a in attacks)),
                "tactics_covered": len(set(a.mitre_tactic for a in attacks if a.mitre_tactic)),
            }

        # Combined multi-layer attack paths
        fw_cats = set(a.category for a in fw if a.success)
        net_cats = set(a.category for a in net if a.success)

        return {
            "firmware": _layer_stats(fw),
            "network": _layer_stats(net),
            "combined_exploited_categories": len(fw_cats | net_cats),
            "firmware_only_categories": sorted(fw_cats - net_cats),
            "network_only_categories": sorted(net_cats - fw_cats),
            "both_layers_categories": sorted(fw_cats & net_cats),
        }

    # --------------------------------------------------------------------- #
    # LaTeX Generation
    # --------------------------------------------------------------------- #

    def generate_latex(self, report: Dict[str, Any], output_dir: str = "results/report/tables"):
        """Generate paper-ready LaTeX tables."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        self._latex_vulnerability_summary(report, out)
        self._latex_cvss_table(report, out)
        self._latex_device_comparison(report, out)
        self._latex_mitre_coverage(report, out)
        self._latex_kill_chain(report, out)
        self._latex_statistical_tests(report, out)

        logger.info(f"Generated LaTeX tables in {out}")

    def _latex_escape(self, s: str) -> str:
        for ch in ("_", "%", "&", "#", "$", "{", "}"):
            s = s.replace(ch, f"\\{ch}")
        return s

    def _latex_vulnerability_summary(self, report: Dict, out: Path):
        """Table: Overall vulnerability summary."""
        summary = report["summary"]
        fw = summary["firmware"]
        net = summary["network"]
        comb = summary["combined"]

        lines = [
            r"\begin{table}[t]",
            r"\centering",
            r"\caption{Security Assessment Summary. Exploit rate, mean CVSS 3.1 score, and attack category coverage across firmware and network layers.}",
            r"\label{tab:security-summary}",
            r"\small",
            r"\begin{tabular}{l r r r r r}",
            r"\toprule",
            r"\textbf{Layer} & \textbf{Attacks} & \textbf{Exploited} & \textbf{Rate} & \textbf{Mean CVSS} & \textbf{Categories} \\",
            r"\midrule",
            f"Firmware & {fw['total']} & {fw['successful']} & {fw['rate']:.1%} & {fw['mean_cvss']:.1f} & {fw['unique_categories']} \\\\",
            f"Network  & {net['total']} & {net['successful']} & {net['rate']:.1%} & {net['mean_cvss']:.1f} & {net['unique_categories']} \\\\",
            r"\midrule",
            f"\\textbf{{Combined}} & \\textbf{{{comb['total']}}} & \\textbf{{{comb['successful']}}} & \\textbf{{{comb['rate']:.1%}}} & \\textbf{{{comb['mean_cvss']:.1f}}} & \\textbf{{{comb['unique_categories']}}} \\\\",
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
        ]
        (out / "tab_security_summary.tex").write_text("\n".join(lines))

    def _latex_cvss_table(self, report: Dict, out: Path):
        """Table: CVSS severity distribution with exploit rates."""
        cvss = report["cvss_analysis"]
        sev_data = cvss["severity_breakdown"]

        lines = [
            r"\begin{table}[t]",
            r"\centering",
            r"\caption{CVSS 3.1 severity distribution. For each severity band, we report the total number of vulnerability instances tested, successful exploits, and exploit rate.}",
            r"\label{tab:cvss-distribution}",
            r"\small",
            r"\begin{tabular}{l r r r}",
            r"\toprule",
            r"\textbf{CVSS Severity} & \textbf{Total} & \textbf{Exploited} & \textbf{Exploit Rate} \\",
            r"\midrule",
        ]

        for sev in self.SEVERITY_ORDER:
            if sev in sev_data:
                d = sev_data[sev]
                lines.append(
                    f"{sev} & {d['total']} & {d['exploited']} & {d['exploit_rate']:.1%} \\\\"
                )

        # Totals
        total_all = sum(d["total"] for d in sev_data.values())
        total_exp = sum(d["exploited"] for d in sev_data.values())
        rate = total_exp / total_all if total_all > 0 else 0.0

        lines += [
            r"\midrule",
            f"\\textbf{{Total}} & \\textbf{{{total_all}}} & \\textbf{{{total_exp}}} & \\textbf{{{rate:.1%}}} \\\\",
            r"\bottomrule",
            r"\end{tabular}",
            f"\\\\[2pt]",
            f"\\footnotesize Weighted risk score: {cvss['weighted_risk_score']:.2f}. "
            f"Mean CVSS: {cvss['score_distribution']['mean']:.1f} "
            f"(95\\% CI: [{cvss.get('mean_cvss_95ci', [0, 0])[0]:.1f}, "
            f"{cvss.get('mean_cvss_95ci', [0, 0])[1]:.1f}]).",
            r"\end{table}",
        ]
        (out / "tab_cvss_distribution.tex").write_text("\n".join(lines))

    def _latex_device_comparison(self, report: Dict, out: Path):
        """Table: Per-device vulnerability comparison."""
        comp = report["device_comparison"]

        lines = [
            r"\begin{table*}[t]",
            r"\centering",
            r"\caption{Per-device firmware vulnerability comparison. Each device type is subjected to the same 18-attack suite. Mean CVSS is computed over successful exploits only. TTE = time-to-exploit (median).}",
            r"\label{tab:device-comparison}",
            r"\small",
            r"\begin{tabular}{l r r r r r r r}",
            r"\toprule",
            r"\textbf{Device Type} & \textbf{Total} & \textbf{Exploited} & \textbf{Rate} & \textbf{Mean CVSS\textsubscript{succ}} & \textbf{Critical} & \textbf{High} & \textbf{Med. TTE (ms)} \\",
            r"\midrule",
        ]

        for dt in self.DEVICE_TYPES:
            if dt not in comp:
                continue
            d = comp[dt]
            name = self._latex_escape(self.DEVICE_DISPLAY_NAMES.get(dt, dt))
            lines.append(
                f"{name} & {d['total_attacks']} & {d['successful']} & "
                f"{d['exploit_rate']:.1%} & {d['mean_cvss_successful']:.1f} & "
                f"{d['critical_vulns']} & {d['high_vulns']} & "
                f"{d['median_tte_ms']:.1f} \\\\"
            )

        lines += [
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table*}",
        ]
        (out / "tab_device_comparison.tex").write_text("\n".join(lines))

    def _latex_mitre_coverage(self, report: Dict, out: Path):
        """Table: MITRE ATT&CK for IoT technique coverage."""
        mitre = report["mitre_coverage"]

        lines = [
            r"\begin{table}[t]",
            r"\centering",
            r"\caption{MITRE ATT\&CK for IoT technique coverage. Tactic coverage indicates the fraction of IoT ATT\&CK tactics addressed by our attack suite.}",
            r"\label{tab:mitre-coverage}",
            r"\small",
            r"\begin{tabular}{l l r r r}",
            r"\toprule",
            r"\textbf{Technique ID} & \textbf{Technique} & \textbf{Tactic} & \textbf{Attempts} & \textbf{Exploit Rate} \\",
            r"\midrule",
        ]

        for tid, data in sorted(mitre["techniques"].items()):
            tech_name = self._latex_escape(data["technique"][:25])
            tactic = self._latex_escape(data["tactic"])
            lines.append(
                f"{tid} & {tech_name} & {tactic} & "
                f"{data['total_attempts']} & {data['exploit_rate']:.1%} \\\\"
            )

        lines += [
            r"\midrule",
            rf"\multicolumn{{5}}{{l}}{{\footnotesize Tactic coverage: {mitre['tactic_coverage']:.1%} "
            rf"({len(mitre['tactics_attempted'])}/12 IoT ATT\&CK tactics)}} \\",
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
        ]
        (out / "tab_mitre_coverage.tex").write_text("\n".join(lines))

    def _latex_kill_chain(self, report: Dict, out: Path):
        """Table: IoT Kill Chain stage coverage."""
        kc = report["kill_chain_analysis"]

        lines = [
            r"\begin{table}[t]",
            r"\centering",
            r"\caption{IoT Cyber Kill Chain coverage. Each stage shows the number of applicable attacks, successful exploits, and maximum CVSS score achieved.}",
            r"\label{tab:kill-chain}",
            r"\small",
            r"\begin{tabular}{l r r r r}",
            r"\toprule",
            r"\textbf{Kill Chain Stage} & \textbf{Attacks} & \textbf{Exploited} & \textbf{Rate} & \textbf{Max CVSS} \\",
            r"\midrule",
        ]

        for stage in IOT_KILL_CHAIN:
            d = kc["stages"][stage]
            checkmark = r"\cmark" if d["exploited"] > 0 else r"\xmark"
            lines.append(
                f"{self._latex_escape(stage)} & {d['total_attacks']} & "
                f"{d['exploited']} & {d['exploit_rate']:.1%} & {d['max_cvss']:.1f} \\\\"
            )

        lines += [
            r"\midrule",
            rf"\multicolumn{{5}}{{l}}{{\footnotesize Kill chain completeness: "
            rf"{kc['kill_chain_completeness']:.1%} ({kc['stages_exploited']}/{kc['total_stages']} stages)}} \\",
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
        ]
        (out / "tab_kill_chain.tex").write_text("\n".join(lines))

    def _latex_statistical_tests(self, report: Dict, out: Path):
        """Table: Statistical test results."""
        stat_tests = report["statistical_tests"]
        if "warning" in stat_tests:
            return

        lines = [
            r"\begin{table}[t]",
            r"\centering",
            r"\caption{Statistical significance tests for security evaluation. $\alpha = 0.05$ for all tests. Effect sizes and confidence intervals ensure results are not artifacts of sample size.}",
            r"\label{tab:stat-tests}",
            r"\small",
            r"\begin{tabular}{l l r l}",
            r"\toprule",
            r"\textbf{Test} & \textbf{Hypothesis} & \textbf{\textit{p}-value} & \textbf{Result} \\",
            r"\midrule",
        ]

        test_display = {
            "chi2_device_independence": ("$\\chi^2$ (device independence)", "Exploit rate $\\perp$ device type"),
            "fw_vs_network_exploit_rate": ("Fisher / $\\chi^2$ (layer)", "FW rate $=$ Network rate"),
            "kruskal_wallis_cvss": ("Kruskal-Wallis", "CVSS dist. equal across devices"),
            "mann_whitney_duration": ("Mann-Whitney $U$", "TTE dist. equal (succ. vs fail)"),
        }

        for key, (display_name, hypothesis) in test_display.items():
            if key in stat_tests:
                d = stat_tests[key]
                p = d["p_value"]
                sig = r"\textbf{*}" if d.get("significant_at_005") else ""
                sig2 = r"\textbf{**}" if d.get("significant_at_001") else ""
                marker = sig2 if sig2 else sig
                result = "Reject $H_0$" if d.get("significant_at_005") else "Fail to reject"
                lines.append(
                    f"{display_name} & {hypothesis} & {p:.4f}{marker} & {result} \\\\"
                )

        lines += [
            r"\bottomrule",
            r"\end{tabular}",
            r"\\\footnotesize * $p < 0.05$, ** $p < 0.01$",
            r"\end{table}",
        ]
        (out / "tab_statistical_tests.tex").write_text("\n".join(lines))

    # --------------------------------------------------------------------- #
    # Figure Generation
    # --------------------------------------------------------------------- #

    def generate_figures(self, report: Dict[str, Any], output_dir: str = "results/report/figures"):
        """Generate paper-ready matplotlib figures."""
        if not HAS_MATPLOTLIB:
            logger.warning("matplotlib not available — skipping figure generation")
            return

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        self._fig_device_heatmap(report, out)
        self._fig_cvss_distribution(report, out)
        self._fig_kill_chain_bar(report, out)
        self._fig_attack_surface_radar(report, out)
        self._fig_tte_boxplot(report, out)
        self._fig_mitre_tactic_wheel(report, out)

        logger.info(f"Generated figures in {out}")

    def _fig_device_heatmap(self, report: Dict, out: Path):
        """
        Heatmap: Device type × Attack category exploit success.
        This is the signature figure for cross-device comparison.
        """
        comp = report["device_comparison"]
        categories = sorted(set(a.category for a in self.scored_attacks if a.layer == "firmware"))
        devices = [dt for dt in self.DEVICE_TYPES if dt in comp]

        # Build matrix: rows=categories, cols=devices, values=exploit rate
        matrix = np.zeros((len(categories), len(devices)))
        for j, dt in enumerate(devices):
            cat_data = comp[dt].get("category_breakdown", {})
            for i, cat in enumerate(categories):
                if cat in cat_data:
                    cd = cat_data[cat]
                    matrix[i, j] = cd["exploited"] / cd["total"] if cd["total"] > 0 else 0.0

        fig, ax = plt.subplots(figsize=(10, 8))
        im = ax.imshow(matrix, cmap="RdYlGn_r", aspect="auto", vmin=0, vmax=1)

        # Labels
        cat_labels = [c.replace("_", " ").title() for c in categories]
        dev_labels = [self.DEVICE_DISPLAY_NAMES.get(dt, dt) for dt in devices]
        ax.set_xticks(range(len(devices)))
        ax.set_xticklabels(dev_labels, rotation=45, ha="right", fontsize=9)
        ax.set_yticks(range(len(categories)))
        ax.set_yticklabels(cat_labels, fontsize=9)

        # Annotate cells
        for i in range(len(categories)):
            for j in range(len(devices)):
                val = matrix[i, j]
                color = "white" if val > 0.5 else "black"
                ax.text(j, i, f"{val:.0%}", ha="center", va="center",
                        color=color, fontsize=8, fontweight="bold")

        ax.set_title("Exploit Success Rate by Device Type and Attack Category", fontsize=12, pad=15)
        plt.colorbar(im, ax=ax, label="Exploit Rate", shrink=0.8)
        fig.tight_layout()
        fig.savefig(out / "fig_device_heatmap.pdf", dpi=300, bbox_inches="tight")
        fig.savefig(out / "fig_device_heatmap.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    def _fig_cvss_distribution(self, report: Dict, out: Path):
        """CVSS score distribution: histogram + box plot (successful vs all)."""
        all_scores = [a.cvss_score for a in self.scored_attacks]
        succ_scores = [a.cvss_score for a in self.scored_attacks if a.success]
        fail_scores = [a.cvss_score for a in self.scored_attacks if not a.success]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

        # Left: Histogram
        bins = np.arange(0, 11, 0.5)
        ax1.hist(succ_scores, bins=bins, alpha=0.7, label="Exploited", color="#d32f2f", edgecolor="white")
        ax1.hist(fail_scores, bins=bins, alpha=0.5, label="Not Exploited", color="#9e9e9e", edgecolor="white")
        ax1.set_xlabel("CVSS 3.1 Base Score", fontsize=11)
        ax1.set_ylabel("Count", fontsize=11)
        ax1.set_title("CVSS Score Distribution", fontsize=12)
        ax1.legend(fontsize=9)

        # Severity band shading
        for xmin, xmax, color, label in [
            (0, 3.9, "#388e3c20", "Low"), (4.0, 6.9, "#fbc02d20", "Med"),
            (7.0, 8.9, "#f57c0020", "High"), (9.0, 10.0, "#d32f2f20", "Crit")
        ]:
            ax1.axvspan(xmin, xmax, alpha=0.15, color=color.rstrip("20"))

        # Right: Box plot by layer
        fw_scores = [a.cvss_score for a in self.scored_attacks if a.layer == "firmware" and a.success]
        net_scores = [a.cvss_score for a in self.scored_attacks if a.layer == "network" and a.success]
        box_data = [fw_scores, net_scores] if net_scores else [fw_scores]
        box_labels = ["Firmware", "Network"] if net_scores else ["Firmware"]

        bp = ax2.boxplot(box_data, tick_labels=box_labels, patch_artist=True,
                         boxprops=dict(facecolor="#e3f2fd"), medianprops=dict(color="#d32f2f", linewidth=2))
        ax2.set_ylabel("CVSS 3.1 Score (Exploited)", fontsize=11)
        ax2.set_title("CVSS by Attack Layer", fontsize=12)
        ax2.grid(axis="y", alpha=0.3)

        fig.tight_layout()
        fig.savefig(out / "fig_cvss_distribution.pdf", dpi=300, bbox_inches="tight")
        fig.savefig(out / "fig_cvss_distribution.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    def _fig_kill_chain_bar(self, report: Dict, out: Path):
        """Horizontal bar chart: Kill chain stage coverage."""
        kc = report["kill_chain_analysis"]

        stages = list(reversed(IOT_KILL_CHAIN))
        total = [kc["stages"][s]["total_attacks"] for s in stages]
        exploited = [kc["stages"][s]["exploited"] for s in stages]

        fig, ax = plt.subplots(figsize=(8, 5))
        y = range(len(stages))
        ax.barh(y, total, color="#bbdefb", edgecolor="white", label="Attempted", height=0.6)
        ax.barh(y, exploited, color="#d32f2f", edgecolor="white", label="Exploited", height=0.6)
        ax.set_yticks(y)
        ax.set_yticklabels(stages, fontsize=10)
        ax.set_xlabel("Number of Attacks", fontsize=11)
        ax.set_title("IoT Cyber Kill Chain Coverage", fontsize=12)
        ax.legend(loc="lower right", fontsize=9)
        ax.grid(axis="x", alpha=0.3)

        fig.tight_layout()
        fig.savefig(out / "fig_kill_chain.pdf", dpi=300, bbox_inches="tight")
        fig.savefig(out / "fig_kill_chain.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    def _fig_attack_surface_radar(self, report: Dict, out: Path):
        """Radar/spider chart of attack surface by category."""
        surface = report["attack_surface"]
        categories = sorted(surface.keys())[:12]  # Limit for readability

        if len(categories) < 3:
            return

        values = [surface[c]["risk_score"] for c in categories]
        max_val = max(values) if values else 1.0
        values_norm = [v / max_val for v in values]

        # Close the polygon
        values_norm += [values_norm[0]]
        N = len(categories)
        angles = [n / N * 2 * math.pi for n in range(N)]
        angles += [angles[0]]

        fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(projection="polar"))
        ax.fill(angles, values_norm, alpha=0.25, color="#d32f2f")
        ax.plot(angles, values_norm, color="#d32f2f", linewidth=2)

        labels = [c.replace("_", "\n").title() for c in categories]
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, fontsize=8)
        ax.set_title("Attack Surface Risk Profile", fontsize=12, pad=20)
        ax.set_ylim(0, 1.1)

        fig.tight_layout()
        fig.savefig(out / "fig_attack_surface.pdf", dpi=300, bbox_inches="tight")
        fig.savefig(out / "fig_attack_surface.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    def _fig_tte_boxplot(self, report: Dict, out: Path):
        """Box plot: Time-to-exploit by CVSS severity."""
        severity_groups: Dict[str, List[float]] = defaultdict(list)
        for a in self.scored_attacks:
            if a.success and a.duration_ms > 0:
                severity_groups[a.cvss_severity].append(a.duration_ms)

        ordered = [s for s in self.SEVERITY_ORDER if s in severity_groups and severity_groups[s]]
        if not ordered:
            return

        data = [severity_groups[s] for s in ordered]
        colors = [self.SEVERITY_COLORS[s] for s in ordered]

        fig, ax = plt.subplots(figsize=(8, 5))
        bp = ax.boxplot(data, tick_labels=ordered, patch_artist=True, showfliers=True,
                        flierprops=dict(marker="o", markersize=3, alpha=0.5))
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color + "40")  # Semi-transparent
            patch.set_edgecolor(color)
        for median in bp["medians"]:
            median.set_color("black")
            median.set_linewidth(2)

        ax.set_ylabel("Time-to-Exploit (ms)", fontsize=11)
        ax.set_xlabel("CVSS 3.1 Severity", fontsize=11)
        ax.set_title("Time-to-Exploit Distribution by Severity", fontsize=12)
        ax.set_yscale("log")
        ax.grid(axis="y", alpha=0.3)

        fig.tight_layout()
        fig.savefig(out / "fig_tte_boxplot.pdf", dpi=300, bbox_inches="tight")
        fig.savefig(out / "fig_tte_boxplot.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    def _fig_mitre_tactic_wheel(self, report: Dict, out: Path):
        """Pie/donut chart: MITRE ATT&CK tactic distribution."""
        mitre = report["mitre_coverage"]
        techniques = mitre["techniques"]

        # Group by tactic
        tactic_counts: Dict[str, int] = defaultdict(int)
        tactic_exploited: Dict[str, int] = defaultdict(int)
        for tid, data in techniques.items():
            tactic_counts[data["tactic"]] += data["total_attempts"]
            tactic_exploited[data["tactic"]] += data["successful"]

        tactics = sorted(tactic_counts.keys())
        totals = [tactic_counts[t] for t in tactics]
        exploited = [tactic_exploited[t] for t in tactics]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

        # Left: Donut chart of attack distribution
        colors = plt.cm.Set3(np.linspace(0, 1, len(tactics)))
        wedges, texts, autotexts = ax1.pie(
            totals, labels=tactics, autopct="%1.0f%%",
            colors=colors, pctdistance=0.8, startangle=90
        )
        centre_circle = plt.Circle((0, 0), 0.55, fc="white")
        ax1.add_artist(centre_circle)
        ax1.set_title("Attack Distribution by Tactic", fontsize=12)
        for text in texts:
            text.set_fontsize(8)

        # Right: Grouped bar
        x = np.arange(len(tactics))
        width = 0.35
        ax2.bar(x - width / 2, totals, width, label="Attempted", color="#bbdefb", edgecolor="white")
        ax2.bar(x + width / 2, exploited, width, label="Exploited", color="#d32f2f", edgecolor="white")
        ax2.set_xticks(x)
        ax2.set_xticklabels(tactics, rotation=45, ha="right", fontsize=8)
        ax2.set_ylabel("Count", fontsize=11)
        ax2.set_title("MITRE ATT&CK IoT Tactic Coverage", fontsize=12)
        ax2.legend(fontsize=9)
        ax2.grid(axis="y", alpha=0.3)

        fig.tight_layout()
        fig.savefig(out / "fig_mitre_tactics.pdf", dpi=300, bbox_inches="tight")
        fig.savefig(out / "fig_mitre_tactics.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    # --------------------------------------------------------------------- #
    # Export
    # --------------------------------------------------------------------- #

    def export_report(self, report: Dict[str, Any], output_dir: str = "results/report"):
        """Export the full evaluation report as JSON."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fpath = out / f"security_evaluation_{ts}.json"
        with open(fpath, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"Exported security evaluation to {fpath}")
        return fpath


# =============================================================================
# CLI Runner
# =============================================================================

def main():
    """Run security evaluation from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="VESPER Security Evaluation")
    parser.add_argument("--results-dir", default="results/security",
                        help="Directory containing attack result JSONs")
    parser.add_argument("--output-dir", default="results/report",
                        help="Output directory for evaluation report")
    parser.add_argument("--timestamp", default=None,
                        help="Specific result timestamp (default: latest)")
    parser.add_argument("--no-figures", action="store_true",
                        help="Skip figure generation")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    evaluator = SecurityEvaluator(args.results_dir)
    evaluator.load_results(args.timestamp)

    print("\n" + "=" * 72)
    print("  VESPER Security Evaluation")
    print("=" * 72)

    report = evaluator.evaluate()

    # Print summary
    summary = report["summary"]
    print(f"\n  Total Attacks: {summary['total_attacks']}")
    print(f"  Device Types:  {summary['device_types_tested']}")
    print(f"  Categories:    {summary['attack_categories']}")

    comb = summary["combined"]
    print(f"\n  Exploit Rate:  {comb['rate']:.1%} ({comb['successful']}/{comb['total']})")
    print(f"  Mean CVSS:     {comb['mean_cvss']:.1f}")
    print(f"  Mean CVSS (exploited): {comb['mean_cvss_successful']:.1f}")

    # CVSS summary
    cvss = report["cvss_analysis"]
    print(f"\n  CVSS Distribution:")
    for sev in SecurityEvaluator.SEVERITY_ORDER:
        if sev in cvss["severity_breakdown"]:
            d = cvss["severity_breakdown"][sev]
            print(f"    {sev:10s}: {d['exploited']:2d}/{d['total']:3d} exploited ({d['exploit_rate']:.0%})")
    print(f"  Weighted Risk Score: {cvss['weighted_risk_score']:.2f}")

    # MITRE summary
    mitre = report["mitre_coverage"]
    print(f"\n  MITRE ATT&CK IoT:")
    print(f"    Tactic Coverage:    {mitre['tactic_coverage']:.0%} ({len(mitre['tactics_attempted'])}/12)")
    print(f"    Unique Techniques:  {mitre['unique_techniques']}")

    # Kill chain
    kc = report["kill_chain_analysis"]
    print(f"\n  Kill Chain Completeness: {kc['kill_chain_completeness']:.0%} ({kc['stages_exploited']}/{kc['total_stages']})")
    for stage in IOT_KILL_CHAIN:
        d = kc["stages"][stage]
        mark = "✓" if d["exploited"] > 0 else "✗"
        print(f"    {mark} {stage:26s} {d['exploited']:2d}/{d['total_attacks']:2d} ({d['exploit_rate']:.0%})")

    # Device comparison
    print(f"\n  Per-Device Exploit Rates:")
    comp = report["device_comparison"]
    for dt in SecurityEvaluator.DEVICE_TYPES:
        if dt not in comp:
            continue
        d = comp[dt]
        name = SecurityEvaluator.DEVICE_DISPLAY_NAMES.get(dt, dt)
        print(f"    {name:20s}: {d['exploit_rate']:.1%} ({d['successful']}/{d['total_attacks']}), "
              f"Mean CVSS={d['mean_cvss_successful']:.1f}, "
              f"Med TTE={d['median_tte_ms']:.0f}ms")

    # Statistical tests
    stat = report["statistical_tests"]
    if "warning" not in stat:
        print(f"\n  Statistical Tests:")
        for key, data in stat.items():
            if isinstance(data, dict) and "p_value" in data:
                sig = "***" if data.get("significant_at_001") else ("*" if data.get("significant_at_005") else "n.s.")
                print(f"    {data.get('test', key):30s}: p={data['p_value']:.4f} {sig}")

    # Export
    evaluator.export_report(report, args.output_dir)
    evaluator.generate_latex(report, f"{args.output_dir}/tables")
    if not args.no_figures:
        evaluator.generate_figures(report, f"{args.output_dir}/figures")

    print(f"\n  Reports: {args.output_dir}/")
    print("=" * 72)


if __name__ == "__main__":
    main()
