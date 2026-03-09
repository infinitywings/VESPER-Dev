"""
VESPER Dataset Builder

Merges network traffic features (from pcap) with behavioral context features
(from EventBus logs) to produce labelled datasets for ML anomaly detection.

Produces three feature configurations:
    Config A — context-free:  network features only (35 features)
    Config B — context-aware: network + context features (49 features)
    Config C — oracle:        Config B + ground-truth attack labels (50 features)

Output format: CSV with header row, one sample per pcap file.

Usage:
    builder = DatasetBuilder()
    builder.build(
        pcap_manifest="results/pcap/manifest.json",
        event_log="logs/events.jsonl",
        output_dir="results/datasets/",
    )
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from vesper.evaluation.feature_extraction import (
    PcapFeatureExtractor,
    ALL_FEATURE_NAMES as NETWORK_FEATURES,
)
from vesper.evaluation.context_features import (
    ContextFeatureExtractor,
    ALL_CONTEXT_FEATURE_NAMES as CONTEXT_FEATURES,
)


# ─── Feature configs ─────────────────────────────────────────────────────────

METADATA_COLS = [
    "pcap_path",
    "attack_name",
    "attack_category",
    "attack_suite",
    "device_type",
    "label",
]

CONFIG_A_FEATURES = list(NETWORK_FEATURES)
CONFIG_B_FEATURES = list(NETWORK_FEATURES) + list(CONTEXT_FEATURES)
CONFIG_C_FEATURES = CONFIG_B_FEATURES + ["oracle_attack_type"]


class DatasetBuilder:
    """
    Builds labelled CSV datasets from pcap manifest + event logs.
    """

    def __init__(
        self,
        server_port: Optional[int] = None,
        context_window_s: float = 10.0,
    ):
        """
        Args:
            server_port: Firmware/broker port for direction detection.
            context_window_s: Time window (seconds) around each pcap capture
                              for extracting context features.
        """
        self.pcap_extractor = PcapFeatureExtractor(server_port=server_port)
        self.context_extractor = ContextFeatureExtractor()
        self.context_window_s = context_window_s

    def build(
        self,
        pcap_manifest: str,
        event_log: str,
        output_dir: str,
    ) -> Dict[str, str]:
        """
        Build Config A, B, C datasets.

        Args:
            pcap_manifest: Path to manifest.json from pcap_attack_capture.py
            event_log: Path to events.jsonl from EventBus
            output_dir: Directory to write output CSV files

        Returns:
            Dict mapping config name to output file path:
            {"A": "path/to/config_A.csv", "B": "...", "C": "..."}
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Load manifest
        with open(pcap_manifest) as f:
            manifest = json.load(f)

        captures = manifest.get("captures", [])
        logger.info(f"Building datasets from {len(captures)} captures")

        rows = []
        for i, entry in enumerate(captures):
            pcap_path = entry["pcap_path"]
            logger.info(f"[{i + 1}/{len(captures)}] {Path(pcap_path).name}")

            # Network features
            net_features = self.pcap_extractor.extract(pcap_path)

            # Context features — use timestamp from manifest
            ts_str = entry.get("timestamp", "")
            capture_time = self._parse_timestamp(ts_str)
            if capture_time and Path(event_log).exists():
                ctx_features = self.context_extractor.extract_window(
                    event_log,
                    capture_time - self.context_window_s / 2,
                    capture_time + self.context_window_s / 2,
                )
            else:
                ctx_features = {name: 0.0 for name in CONTEXT_FEATURES}

            # Metadata
            meta = {
                "pcap_path": pcap_path,
                "attack_name": entry.get("attack_name", ""),
                "attack_category": entry.get("attack_category", ""),
                "attack_suite": entry.get("attack_suite", ""),
                "device_type": entry.get("device_type", ""),
                "label": entry.get("label", -1),
            }

            row = {**meta, **net_features, **ctx_features}
            # Oracle feature = numeric encoding of attack category
            row["oracle_attack_type"] = self._encode_attack_category(
                entry.get("attack_category", "benign")
            )
            rows.append(row)

        # Write Config A (network features only)
        path_a = str(out / "config_A_context_free.csv")
        self._write_csv(rows, path_a, METADATA_COLS + CONFIG_A_FEATURES)

        # Write Config B (network + context features)
        path_b = str(out / "config_B_context_aware.csv")
        self._write_csv(rows, path_b, METADATA_COLS + CONFIG_B_FEATURES)

        # Write Config C (network + context + oracle)
        path_c = str(out / "config_C_oracle.csv")
        self._write_csv(rows, path_c, METADATA_COLS + CONFIG_C_FEATURES)

        logger.info(f"Datasets written to {output_dir}")
        return {"A": path_a, "B": path_b, "C": path_c}

    def _write_csv(
        self,
        rows: List[Dict],
        output_path: str,
        columns: List[str],
    ):
        """Write rows to CSV with specified columns."""
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"Wrote {len(rows)} rows to {output_path}")

    @staticmethod
    def _parse_timestamp(ts_str: str) -> Optional[float]:
        """Parse ISO 8601 timestamp to unix time."""
        if not ts_str:
            return None
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(ts_str)
            return dt.timestamp()
        except Exception:
            return None

    @staticmethod
    def _encode_attack_category(category: str) -> float:
        """Deterministic numeric encoding of attack category."""
        categories = {
            "benign": 0,
            "buffer_overflow": 1,
            "command_injection": 2,
            "authentication_bypass": 3,
            "firmware_update_attack": 4,
            "information_disclosure": 5,
            "denial_of_service": 6,
            "state_manipulation": 7,
            "replay_attack": 8,
            "protocol_fuzzing": 9,
            "mqtt_message_injection": 10,
            "mqtt_topic_hijack": 11,
            "mqtt_eavesdropping": 12,
            "tcp_man_in_the_middle": 13,
            "tcp_connection_hijack": 14,
            "protocol_replay": 15,
            "protocol_downgrade": 16,
            "arp_spoofing": 17,
            "dns_poisoning": 18,
            "network_denial_of_service": 19,
            "traffic_analysis": 20,
            "deauthentication_attack": 21,
            "evil_twin_ap": 22,
        }
        return float(categories.get(category.lower(), -1))

    @staticmethod
    def feature_configs() -> Dict[str, List[str]]:
        """Return feature lists for each config."""
        return {
            "A": CONFIG_A_FEATURES,
            "B": CONFIG_B_FEATURES,
            "C": CONFIG_C_FEATURES,
        }
