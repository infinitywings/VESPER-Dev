"""
VESPER Network Traffic Feature Extraction

Extracts features from .pcap files for ML-based anomaly detection.
Uses scapy for packet parsing and produces a feature vector per
pcap file (per-flow or per-window aggregation).

Feature categories (35 features):
  1.  Flow features         (12) — packet/byte counts, durations, rates
  2.  MQTT features         (8)  — topic depth, payload size, QoS, msg types
  3.  Temporal features     (8)  — inter-arrival time stats, burst detection
  4.  Payload features      (7)  — entropy, ASCII ratio, unique bytes, lengths

Usage:
    extractor = PcapFeatureExtractor()
    features = extractor.extract("path/to/capture.pcap")
    df = extractor.extract_directory("results/pcap/firmware/smart_light/")
"""

from __future__ import annotations

import json
import logging
import math
import os
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    from scapy.all import rdpcap, TCP, UDP, IP, Raw, MQTT
    HAS_SCAPY = True
except ImportError:
    try:
        from scapy.all import rdpcap, TCP, UDP, IP, Raw
        HAS_SCAPY = True
    except ImportError:
        HAS_SCAPY = False
        logger.warning("scapy not installed — feature extraction will not work")

# Check for MQTT layer support
try:
    from scapy.contrib.mqtt import MQTT as ScapyMQTT, MQTTPublish, MQTTSubscribe
    HAS_SCAPY_MQTT = True
except ImportError:
    HAS_SCAPY_MQTT = False


# ─── Feature names ────────────────────────────────────────────────────────────

FLOW_FEATURES = [
    "total_packets",
    "total_bytes",
    "fwd_packets",          # client → server
    "bwd_packets",          # server → client
    "fwd_bytes",
    "bwd_bytes",
    "flow_duration_s",
    "packets_per_second",
    "bytes_per_second",
    "avg_packet_size",
    "max_packet_size",
    "min_packet_size",
]

MQTT_FEATURES = [
    "mqtt_publish_count",
    "mqtt_subscribe_count",
    "mqtt_connect_count",
    "mqtt_avg_topic_depth",     # avg number of '/' in topics
    "mqtt_max_topic_depth",
    "mqtt_avg_payload_size",
    "mqtt_max_payload_size",
    "mqtt_unique_topics",
]

TEMPORAL_FEATURES = [
    "iat_mean",                 # inter-arrival time mean
    "iat_std",
    "iat_min",
    "iat_max",
    "iat_median",
    "burst_count",              # bursts = ≥3 packets within 10ms
    "burst_max_size",
    "idle_time_ratio",          # fraction of time with no traffic (gaps > 1s)
]

PAYLOAD_FEATURES = [
    "payload_entropy_mean",     # Shannon entropy of payload bytes
    "payload_entropy_max",
    "payload_ascii_ratio",      # fraction of printable ASCII bytes
    "payload_unique_bytes",     # distinct byte values
    "payload_avg_length",
    "payload_max_length",
    "payload_zero_count",       # number of null bytes (common in exploits)
]

ALL_FEATURE_NAMES = FLOW_FEATURES + MQTT_FEATURES + TEMPORAL_FEATURES + PAYLOAD_FEATURES


# ─── Helper functions ─────────────────────────────────────────────────────────

def _shannon_entropy(data: bytes) -> float:
    """Compute Shannon entropy of a byte sequence."""
    if not data:
        return 0.0
    freq = Counter(data)
    length = len(data)
    entropy = 0.0
    for count in freq.values():
        p = count / length
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def _ascii_ratio(data: bytes) -> float:
    """Fraction of bytes that are printable ASCII (32-126)."""
    if not data:
        return 0.0
    printable = sum(1 for b in data if 32 <= b <= 126)
    return printable / len(data)


def _detect_bursts(timestamps: List[float], threshold_s: float = 0.01, min_size: int = 3) -> Tuple[int, int]:
    """
    Detect packet bursts (≥min_size packets within threshold_s).
    Returns (burst_count, max_burst_size).
    """
    if len(timestamps) < min_size:
        return 0, 0

    burst_count = 0
    max_burst = 0
    current_burst = 1

    for i in range(1, len(timestamps)):
        if timestamps[i] - timestamps[i - 1] <= threshold_s:
            current_burst += 1
        else:
            if current_burst >= min_size:
                burst_count += 1
                max_burst = max(max_burst, current_burst)
            current_burst = 1

    # Final burst
    if current_burst >= min_size:
        burst_count += 1
        max_burst = max(max_burst, current_burst)

    return burst_count, max_burst


def _idle_time_ratio(timestamps: List[float], idle_threshold_s: float = 1.0) -> float:
    """Fraction of total time spent idle (gaps > idle_threshold_s)."""
    if len(timestamps) < 2:
        return 0.0
    total_duration = timestamps[-1] - timestamps[0]
    if total_duration <= 0:
        return 0.0
    idle_time = sum(
        timestamps[i] - timestamps[i - 1]
        for i in range(1, len(timestamps))
        if timestamps[i] - timestamps[i - 1] > idle_threshold_s
    )
    return idle_time / total_duration


# ─── Main extractor ──────────────────────────────────────────────────────────

class PcapFeatureExtractor:
    """
    Extracts a fixed-size feature vector from a pcap file.

    Returns a dict mapping feature names to float values.
    Missing/unavailable features default to 0.0.
    """

    def __init__(self, server_port: Optional[int] = None):
        """
        Args:
            server_port: Port used by the firmware/broker. Used to determine
                         fwd (client→server) vs bwd (server→client) direction.
                         If None, uses the lower port as server.
        """
        self.server_port = server_port

    def extract(self, pcap_path: str) -> Dict[str, float]:
        """
        Extract features from a single pcap file.

        Returns dict of {feature_name: value} with all 35 features.
        """
        features = {name: 0.0 for name in ALL_FEATURE_NAMES}

        if not HAS_SCAPY:
            logger.error("scapy not installed — cannot extract features")
            return features

        path = Path(pcap_path)
        if not path.exists() or path.stat().st_size == 0:
            return features

        try:
            packets = rdpcap(str(path))
        except Exception as e:
            logger.error(f"Failed to read {pcap_path}: {e}")
            return features

        if len(packets) == 0:
            return features

        # Separate by direction and collect metadata
        timestamps = []
        sizes = []
        fwd_sizes = []
        bwd_sizes = []
        payloads = []
        mqtt_topics = []
        mqtt_payload_sizes = []
        mqtt_pub_count = 0
        mqtt_sub_count = 0
        mqtt_connect_count = 0

        for pkt in packets:
            ts = float(pkt.time)
            pkt_len = len(pkt)
            timestamps.append(ts)
            sizes.append(pkt_len)

            # Direction
            is_fwd = True
            if pkt.haslayer(TCP):
                sport = pkt[TCP].sport
                dport = pkt[TCP].dport
                if self.server_port:
                    is_fwd = (dport == self.server_port)
                else:
                    is_fwd = (dport <= sport)
            elif pkt.haslayer(UDP):
                sport = pkt[UDP].sport
                dport = pkt[UDP].dport
                if self.server_port:
                    is_fwd = (dport == self.server_port)
                else:
                    is_fwd = (dport <= sport)

            if is_fwd:
                fwd_sizes.append(pkt_len)
            else:
                bwd_sizes.append(pkt_len)

            # Payload
            if pkt.haslayer(Raw):
                raw = bytes(pkt[Raw].load)
                payloads.append(raw)
            elif pkt.haslayer(TCP) and pkt[TCP].payload:
                raw = bytes(pkt[TCP].payload)
                if raw:
                    payloads.append(raw)

            # MQTT (scapy contrib)
            if HAS_SCAPY_MQTT:
                if pkt.haslayer(MQTTPublish):
                    mqtt_pub_count += 1
                    topic = pkt[MQTTPublish].topic
                    if isinstance(topic, bytes):
                        topic = topic.decode("utf-8", errors="replace")
                    mqtt_topics.append(topic)
                    val = pkt[MQTTPublish].value
                    mqtt_payload_sizes.append(len(val) if val else 0)
                if pkt.haslayer(MQTTSubscribe):
                    mqtt_sub_count += 1
                if pkt.haslayer(ScapyMQTT):
                    mtype = pkt[ScapyMQTT].type
                    if mtype == 1:  # CONNECT
                        mqtt_connect_count += 1

            # Fallback MQTT detection: check TCP payload for MQTT headers
            if not HAS_SCAPY_MQTT and payloads:
                raw = payloads[-1]
                if len(raw) >= 2:
                    pkt_type = (raw[0] >> 4) & 0x0F
                    if pkt_type == 3:  # PUBLISH
                        mqtt_pub_count += 1
                        # Try to extract topic from MQTT binary format
                        if len(raw) >= 4:
                            try:
                                topic_len = struct.unpack("!H", raw[2:4])[0]
                                if 4 + topic_len <= len(raw):
                                    topic = raw[4:4 + topic_len].decode("utf-8", errors="replace")
                                    mqtt_topics.append(topic)
                                    mqtt_payload_sizes.append(len(raw) - 4 - topic_len)
                            except Exception:
                                pass
                    elif pkt_type == 8:  # SUBSCRIBE
                        mqtt_sub_count += 1
                    elif pkt_type == 1:  # CONNECT
                        mqtt_connect_count += 1

        # ── Flow features ────────────────────────────────────────────
        total_packets = len(packets)
        total_bytes = sum(sizes)
        flow_duration = (timestamps[-1] - timestamps[0]) if len(timestamps) > 1 else 0.0

        features["total_packets"] = float(total_packets)
        features["total_bytes"] = float(total_bytes)
        features["fwd_packets"] = float(len(fwd_sizes))
        features["bwd_packets"] = float(len(bwd_sizes))
        features["fwd_bytes"] = float(sum(fwd_sizes))
        features["bwd_bytes"] = float(sum(bwd_sizes))
        features["flow_duration_s"] = flow_duration
        features["packets_per_second"] = total_packets / flow_duration if flow_duration > 0 else 0.0
        features["bytes_per_second"] = total_bytes / flow_duration if flow_duration > 0 else 0.0
        features["avg_packet_size"] = total_bytes / total_packets if total_packets > 0 else 0.0
        features["max_packet_size"] = float(max(sizes)) if sizes else 0.0
        features["min_packet_size"] = float(min(sizes)) if sizes else 0.0

        # ── MQTT features ────────────────────────────────────────────
        features["mqtt_publish_count"] = float(mqtt_pub_count)
        features["mqtt_subscribe_count"] = float(mqtt_sub_count)
        features["mqtt_connect_count"] = float(mqtt_connect_count)

        if mqtt_topics:
            topic_depths = [t.count("/") + 1 for t in mqtt_topics]
            features["mqtt_avg_topic_depth"] = float(np.mean(topic_depths))
            features["mqtt_max_topic_depth"] = float(max(topic_depths))
            features["mqtt_unique_topics"] = float(len(set(mqtt_topics)))
        if mqtt_payload_sizes:
            features["mqtt_avg_payload_size"] = float(np.mean(mqtt_payload_sizes))
            features["mqtt_max_payload_size"] = float(max(mqtt_payload_sizes))

        # ── Temporal features ────────────────────────────────────────
        if len(timestamps) > 1:
            iats = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
            features["iat_mean"] = float(np.mean(iats))
            features["iat_std"] = float(np.std(iats))
            features["iat_min"] = float(min(iats))
            features["iat_max"] = float(max(iats))
            features["iat_median"] = float(np.median(iats))

            burst_count, burst_max = _detect_bursts(timestamps)
            features["burst_count"] = float(burst_count)
            features["burst_max_size"] = float(burst_max)
            features["idle_time_ratio"] = _idle_time_ratio(timestamps)

        # ── Payload features ─────────────────────────────────────────
        if payloads:
            entropies = [_shannon_entropy(p) for p in payloads]
            features["payload_entropy_mean"] = float(np.mean(entropies))
            features["payload_entropy_max"] = float(max(entropies))

            ascii_ratios = [_ascii_ratio(p) for p in payloads]
            features["payload_ascii_ratio"] = float(np.mean(ascii_ratios))

            all_bytes = b"".join(payloads)
            features["payload_unique_bytes"] = float(len(set(all_bytes)))

            payload_lengths = [len(p) for p in payloads]
            features["payload_avg_length"] = float(np.mean(payload_lengths))
            features["payload_max_length"] = float(max(payload_lengths))

            features["payload_zero_count"] = float(all_bytes.count(0))

        return features

    def extract_directory(
        self,
        directory: str,
        manifest_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Extract features from all .pcap files in a directory.

        If manifest_path is provided, reads labels from the manifest.

        Returns list of dicts with feature values + metadata (attack_name,
        device_type, label, pcap_path).
        """
        rows = []
        pcap_dir = Path(directory)

        # Load manifest for labels
        labels = {}
        if manifest_path:
            with open(manifest_path) as f:
                manifest = json.load(f)
            for entry in manifest.get("captures", []):
                labels[entry["pcap_path"]] = entry

        # Find all pcap files
        pcap_files = sorted(pcap_dir.rglob("*.pcap"))
        logger.info(f"Found {len(pcap_files)} pcap files in {directory}")

        for pcap_file in pcap_files:
            pcap_path = str(pcap_file)
            logger.info(f"Extracting: {pcap_file.name}")

            features = self.extract(pcap_path)

            # Add metadata
            meta = labels.get(pcap_path, {})
            row = {
                "pcap_path": pcap_path,
                "attack_name": meta.get("attack_name", pcap_file.stem),
                "attack_category": meta.get("attack_category", ""),
                "attack_suite": meta.get("attack_suite", ""),
                "device_type": meta.get("device_type", ""),
                "label": meta.get("label", -1),  # -1 = unknown
                **features,
            }
            rows.append(row)

        return rows

    def to_csv(
        self,
        rows: List[Dict[str, Any]],
        output_path: str,
    ) -> str:
        """Write feature rows to CSV. Returns the output path."""
        import csv

        fieldnames = [
            "pcap_path", "attack_name", "attack_category", "attack_suite",
            "device_type", "label",
        ] + ALL_FEATURE_NAMES

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        logger.info(f"Wrote {len(rows)} rows to {output_path}")
        return output_path

    @staticmethod
    def feature_names() -> List[str]:
        """Return the ordered list of all feature names."""
        return list(ALL_FEATURE_NAMES)
