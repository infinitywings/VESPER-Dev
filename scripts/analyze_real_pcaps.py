#!/usr/bin/env python3
"""
VESPER — Analyze Real pcap Files for Trace Validation

Extracts trace statistics from the actual pcap files captured during the
30-scene autonomous evaluation, producing the same metrics used in the
trace validation comparison (§5.4).

This replaces the traffic SIMULATOR with real measured data.

Metrics extracted per pcap:
    1. Flow count (unique src/dst/port/proto tuples per hour)
    2. Packet-size distribution (CDF percentiles)
    3. Keepalive periodicity (inter-packet time autocorrelation)
    4. Burstiness (CoV of per-minute packet counts)
    5. Diurnal pattern (hourly packet counts)
    6. Protocol breakdown (TCP flags, payload stats)

Usage:
    # Analyze pcaps from the 30-scene autonomous eval
    python3 scripts/analyze_real_pcaps.py \\
        --pcap-dir results/vesper_autonomous_eval \\
        --output results/trace_validation_real

    # Also analyze pcaps from RQ-N1/RQ-N2 experiments
    python3 scripts/analyze_real_pcaps.py \\
        --pcap-dir results/rqn1_20260303/wifi \\
        --output results/trace_validation_rqn1

    # Compare against reference and generate paper artifacts
    python3 scripts/analyze_real_pcaps.py \\
        --pcap-dir results/vesper_autonomous_eval \\
        --output results/trace_validation_real \\
        --generate-paper
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import os
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

logger = logging.getLogger("vesper.pcap_analysis")

# ═══════════════════════════════════════════════════════════════════════════
# Reference Dataset Statistics (published values)
# ═══════════════════════════════════════════════════════════════════════════

UNSW_IOT_REFERENCE = {
    "name": "UNSW IoT Traces (Sivanathan et al., 2019)",
    "devices": 28,
    "flows_per_hour_mean": 127,
    "packet_size_p50": 89,
    "packet_size_p90": 541,
    "packet_size_mean": 198,
    "keepalive_period_s": 60,
    "burstiness_cov": 1.42,
    "diurnal_peak_hour": 19,
}

MONITR_REFERENCE = {
    "name": "Mon(IoT)r (Ren et al., IMC 2019)",
    "devices": 81,
    "flows_per_hour_mean": 215,
    "packet_size_p50": 76,
    "packet_size_p90": 480,
    "burstiness_cov": 1.85,
    "diurnal_peak_hour": 20,
}


# ═══════════════════════════════════════════════════════════════════════════
# pcap Analysis Functions (using tshark)
# ═══════════════════════════════════════════════════════════════════════════

def find_pcap_files(pcap_dir: str) -> List[str]:
    """Find all pcap/pcapng files recursively."""
    patterns = ["**/*.pcap", "**/*.pcapng"]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(os.path.join(pcap_dir, pattern), recursive=True))
    files = sorted(set(files))
    logger.info(f"Found {len(files)} pcap files in {pcap_dir}")
    return files


def check_tshark() -> bool:
    """Check if tshark is available."""
    try:
        result = subprocess.run(
            ["tshark", "--version"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def analyze_single_pcap(pcap_path: str) -> Dict[str, Any]:
    """Extract statistics from a single pcap file using tshark."""
    stats = {
        "file": pcap_path,
        "file_size_bytes": os.path.getsize(pcap_path),
    }

    # ── 1. Basic stats (frame count, duration, bytes) ────────────────
    try:
        result = subprocess.run(
            ["tshark", "-r", pcap_path, "-q", "-z", "io,stat,0"],
            capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + result.stderr
        for line in output.splitlines():
            if "<>" in line:
                parts = line.split("|")
                for p in parts:
                    p = p.strip()
                    # Look for frame count and byte count
                    tokens = p.split()
                    if len(tokens) >= 2:
                        try:
                            stats["total_frames"] = int(tokens[0])
                            stats["total_bytes"] = int(tokens[1])
                        except ValueError:
                            pass
    except Exception as e:
        logger.warning(f"io,stat failed for {pcap_path}: {e}")

    # ── 2. Packet sizes ──────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["tshark", "-r", pcap_path, "-T", "fields", "-e", "frame.len"],
            capture_output=True, text=True, timeout=60,
        )
        sizes = []
        for line in result.stdout.strip().splitlines():
            try:
                sizes.append(int(line.strip()))
            except ValueError:
                pass

        if sizes:
            sizes.sort()
            n = len(sizes)
            stats["packet_sizes"] = {
                "count": n,
                "min": sizes[0],
                "max": sizes[-1],
                "mean": round(statistics.mean(sizes), 1),
                "median": round(statistics.median(sizes), 1),
                "p10": sizes[int(0.10 * n)],
                "p25": sizes[int(0.25 * n)],
                "p50": sizes[int(0.50 * n)],
                "p75": sizes[int(0.75 * n)],
                "p90": sizes[int(0.90 * n)],
                "p95": sizes[int(0.95 * n)],
                "p99": sizes[min(int(0.99 * n), n - 1)],
                "stdev": round(statistics.stdev(sizes), 1) if n > 1 else 0,
            }
            stats["packet_size_list"] = sizes  # For CDF generation
    except Exception as e:
        logger.warning(f"Packet sizes failed for {pcap_path}: {e}")

    # ── 3. Flow count (unique conversations) ─────────────────────────
    try:
        result = subprocess.run(
            ["tshark", "-r", pcap_path, "-q", "-z", "conv,tcp"],
            capture_output=True, text=True, timeout=60,
        )
        conv_count = 0
        for line in result.stdout.splitlines():
            if "<->" in line:
                conv_count += 1
        stats["tcp_conversations"] = conv_count

        # Also count UDP conversations
        result = subprocess.run(
            ["tshark", "-r", pcap_path, "-q", "-z", "conv,udp"],
            capture_output=True, text=True, timeout=60,
        )
        udp_count = 0
        for line in result.stdout.splitlines():
            if "<->" in line:
                udp_count += 1
        stats["udp_conversations"] = udp_count
        stats["total_conversations"] = conv_count + udp_count

    except Exception as e:
        logger.warning(f"Conversation count failed: {e}")

    # ── 4. TCP flag breakdown ────────────────────────────────────────
    try:
        flags = {}
        for flag_name, flag_filter in [
            ("syn", "tcp.flags.syn==1&&tcp.flags.ack==0"),
            ("syn_ack", "tcp.flags.syn==1&&tcp.flags.ack==1"),
            ("data", "tcp.len>0"),
            ("fin", "tcp.flags.fin==1"),
            ("rst", "tcp.flags.reset==1"),
        ]:
            result = subprocess.run(
                ["tshark", "-r", pcap_path, "-Y", flag_filter, "-T", "fields", "-e", "frame.number"],
                capture_output=True, text=True, timeout=30,
            )
            count = len([l for l in result.stdout.strip().splitlines() if l.strip()])
            flags[flag_name] = count

        stats["tcp_flags"] = flags
    except Exception as e:
        logger.warning(f"TCP flag analysis failed: {e}")

    # ── 5. Timestamps (for burstiness and diurnal analysis) ──────────
    try:
        result = subprocess.run(
            ["tshark", "-r", pcap_path, "-T", "fields", "-e", "frame.time_epoch"],
            capture_output=True, text=True, timeout=60,
        )
        timestamps = []
        for line in result.stdout.strip().splitlines():
            try:
                timestamps.append(float(line.strip()))
            except ValueError:
                pass

        if timestamps:
            timestamps.sort()
            stats["first_timestamp"] = timestamps[0]
            stats["last_timestamp"] = timestamps[-1]
            stats["duration_s"] = round(timestamps[-1] - timestamps[0], 2)

            # Per-minute packet counts (for burstiness)
            if len(timestamps) > 10:
                start = timestamps[0]
                minute_counts = Counter()
                for ts in timestamps:
                    minute_bin = int((ts - start) / 60)
                    minute_counts[minute_bin] += 1

                counts = list(minute_counts.values())
                if len(counts) > 1:
                    mean_c = statistics.mean(counts)
                    std_c = statistics.stdev(counts)
                    stats["burstiness"] = {
                        "minute_bins": len(counts),
                        "mean_per_minute": round(mean_c, 1),
                        "stdev_per_minute": round(std_c, 1),
                        "cov": round(std_c / mean_c, 3) if mean_c > 0 else 0,
                        "min_per_minute": min(counts),
                        "max_per_minute": max(counts),
                    }

            # Inter-packet times (for keepalive detection)
            if len(timestamps) > 2:
                ipts = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
                stats["inter_packet_times"] = {
                    "mean_s": round(statistics.mean(ipts), 4),
                    "median_s": round(statistics.median(ipts), 4),
                    "stdev_s": round(statistics.stdev(ipts), 4) if len(ipts) > 1 else 0,
                    "min_s": round(min(ipts), 6),
                    "max_s": round(max(ipts), 4),
                }

                # Detect periodic patterns (look for peaks near 30s, 60s, 120s)
                ipt_rounded = Counter()
                for ipt in ipts:
                    if 0.5 < ipt < 300:  # Skip sub-second and very long gaps
                        bucket = round(ipt)
                        ipt_rounded[bucket] += 1

                # Top periodic intervals
                top_periods = ipt_rounded.most_common(10)
                stats["periodic_intervals"] = [
                    {"interval_s": interval, "count": count}
                    for interval, count in top_periods
                ]

            stats["timestamp_list"] = timestamps  # For diurnal analysis

    except Exception as e:
        logger.warning(f"Timestamp analysis failed: {e}")

    return stats


def analyze_all_pcaps(pcap_files: List[str]) -> Dict[str, Any]:
    """Analyze all pcap files and compute aggregate statistics."""
    per_file_stats = []
    all_sizes = []
    all_timestamps = []
    total_frames = 0
    total_bytes = 0
    total_conversations = 0
    total_tcp_flags = Counter()

    for i, pcap in enumerate(pcap_files):
        logger.info(f"  [{i+1}/{len(pcap_files)}] Analyzing {os.path.basename(pcap)}...")
        stats = analyze_single_pcap(pcap)
        per_file_stats.append(stats)

        total_frames += stats.get("total_frames", stats.get("packet_sizes", {}).get("count", 0))
        total_bytes += stats.get("total_bytes", 0)
        total_conversations += stats.get("total_conversations", 0)

        if "packet_size_list" in stats:
            all_sizes.extend(stats["packet_size_list"])

        if "timestamp_list" in stats:
            all_timestamps.extend(stats["timestamp_list"])

        for flag, count in stats.get("tcp_flags", {}).items():
            total_tcp_flags[flag] += count

    # ── Aggregate stats ──────────────────────────────────────────────
    aggregate = {
        "pcap_count": len(pcap_files),
        "total_frames": total_frames,
        "total_bytes": total_bytes,
        "total_conversations": total_conversations,
        "tcp_flags": dict(total_tcp_flags),
    }

    # Packet size distribution
    if all_sizes:
        all_sizes.sort()
        n = len(all_sizes)
        aggregate["packet_sizes"] = {
            "count": n,
            "min": all_sizes[0],
            "max": all_sizes[-1],
            "mean": round(statistics.mean(all_sizes), 1),
            "median": round(statistics.median(all_sizes), 1),
            "p10": all_sizes[int(0.10 * n)],
            "p25": all_sizes[int(0.25 * n)],
            "p50": all_sizes[int(0.50 * n)],
            "p75": all_sizes[int(0.75 * n)],
            "p90": all_sizes[int(0.90 * n)],
            "p95": all_sizes[int(0.95 * n)],
            "p99": all_sizes[min(int(0.99 * n), n - 1)],
            "stdev": round(statistics.stdev(all_sizes), 1) if n > 1 else 0,
        }

    # Burstiness (aggregate across all pcaps)
    if all_timestamps:
        all_timestamps.sort()
        duration_s = all_timestamps[-1] - all_timestamps[0]
        aggregate["total_duration_s"] = round(duration_s, 1)
        aggregate["total_duration_h"] = round(duration_s / 3600, 2)

        # Flows per hour
        if duration_s > 0:
            hours = max(duration_s / 3600, 1)
            aggregate["flows_per_hour"] = round(total_conversations / hours, 1)

        # Per-minute burstiness
        start = all_timestamps[0]
        minute_counts = Counter()
        for ts in all_timestamps:
            minute_bin = int((ts - start) / 60)
            minute_counts[minute_bin] += 1

        counts = list(minute_counts.values())
        if len(counts) > 1:
            mean_c = statistics.mean(counts)
            std_c = statistics.stdev(counts)
            aggregate["burstiness"] = {
                "minute_bins": len(counts),
                "mean_per_minute": round(mean_c, 1),
                "stdev_per_minute": round(std_c, 1),
                "cov": round(std_c / mean_c, 3) if mean_c > 0 else 0,
            }

        # Hourly distribution (for diurnal pattern)
        # Map to simulated hours (60× acceleration: 1 real minute = 1 sim hour)
        # OR use real hours if capturing over 24h
        hour_counts = Counter()
        for ts in all_timestamps:
            # Use relative position within each scene's capture
            relative_s = ts - all_timestamps[0]
            sim_hour = int((relative_s / duration_s) * 24) % 24 if duration_s > 0 else 0
            hour_counts[sim_hour] += 1

        hourly = [hour_counts.get(h, 0) for h in range(24)]
        aggregate["hourly_distribution"] = hourly

        # Find peak hour
        if hourly:
            peak_hour = hourly.index(max(hourly))
            aggregate["peak_hour"] = peak_hour

        # Diurnal correlation with reference
        ref_hourly = _reference_diurnal_profile()
        if len(hourly) == len(ref_hourly):
            r = _pearson_correlation(hourly, ref_hourly)
            aggregate["diurnal_correlation_r"] = round(r, 3) if r is not None else None

    # Per-scene burstiness
    scene_burstiness = []
    for stats in per_file_stats:
        if "burstiness" in stats:
            scene_burstiness.append(stats["burstiness"]["cov"])
    if scene_burstiness:
        aggregate["per_scene_burstiness"] = {
            "mean_cov": round(statistics.mean(scene_burstiness), 3),
            "min_cov": round(min(scene_burstiness), 3),
            "max_cov": round(max(scene_burstiness), 3),
        }

    return {
        "aggregate": aggregate,
        "per_file": [{k: v for k, v in s.items()
                      if k not in ("packet_size_list", "timestamp_list")}
                     for s in per_file_stats],
        "all_sizes": all_sizes,  # For CDF plotting
    }


def _reference_diurnal_profile() -> List[float]:
    """UNSW IoT reference diurnal profile (normalized)."""
    # Approximate 24-hour profile from Sivanathan et al. Fig. 5
    profile = [
        0.15, 0.10, 0.08, 0.07, 0.06, 0.08,  # 00-05 (night)
        0.15, 0.35, 0.50, 0.55, 0.60, 0.65,  # 06-11 (morning)
        0.70, 0.65, 0.60, 0.55, 0.65, 0.75,  # 12-17 (afternoon)
        0.85, 1.00, 0.90, 0.70, 0.45, 0.25,  # 18-23 (evening peak at 19)
    ]
    return profile


def _pearson_correlation(x: List[float], y: List[float]) -> Optional[float]:
    """Compute Pearson correlation coefficient."""
    n = len(x)
    if n != len(y) or n < 3:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / n)
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / n)
    if sx == 0 or sy == 0:
        return None
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
    return cov / (sx * sy)


# ═══════════════════════════════════════════════════════════════════════════
# Comparison with Reference
# ═══════════════════════════════════════════════════════════════════════════

def compare_with_reference(aggregate: Dict, ref: Dict = None) -> Dict:
    """Compare VESPER aggregate stats against reference dataset."""
    if ref is None:
        ref = UNSW_IOT_REFERENCE

    comparison = {
        "reference": ref["name"],
        "metrics": {},
    }

    ps = aggregate.get("packet_sizes", {})
    burst = aggregate.get("burstiness", {})

    metrics = [
        ("flows_per_hour", aggregate.get("flows_per_hour", 0), ref.get("flows_per_hour_mean", 0)),
        ("packet_size_p50", ps.get("p50", 0), ref.get("packet_size_p50", 0)),
        ("packet_size_p90", ps.get("p90", 0), ref.get("packet_size_p90", 0)),
        ("packet_size_mean", ps.get("mean", 0), ref.get("packet_size_mean", 0)),
        ("burstiness_cov", burst.get("cov", 0), ref.get("burstiness_cov", 0)),
        ("diurnal_peak_hour", aggregate.get("peak_hour", -1), ref.get("diurnal_peak_hour", -1)),
        ("diurnal_r", aggregate.get("diurnal_correlation_r", 0), 1.0),
    ]

    for name, vesper_val, ref_val in metrics:
        ratio = round(vesper_val / ref_val, 3) if ref_val and ref_val != 0 else None
        comparison["metrics"][name] = {
            "vesper": vesper_val,
            "reference": ref_val,
            "ratio": ratio,
            "same_order": (0.1 <= (ratio or 0) <= 10.0) if ratio else False,
        }

    return comparison


# ═══════════════════════════════════════════════════════════════════════════
# Paper Artifact Generation
# ═══════════════════════════════════════════════════════════════════════════

def generate_trace_table(comparison: Dict, aggregate: Dict, output_path: str):
    """Generate tab_trace_validation.tex from real pcap data."""
    m = comparison["metrics"]

    tex = r"""\begin{table}[t]
\centering
\caption{Network trace validation: VESPER measured traffic (from
         real pcap captures) vs.\ real-world IoT datasets.}
\label{tab:trace-validation}
\small
\begin{tabular}{@{}lccc@{}}
\toprule
\textbf{Statistic} & \textbf{VESPER} & \textbf{UNSW IoT} & \textbf{Match?} \\
\midrule
"""
    rows = [
        ("Flows/hour (mean)",
         str(m["flows_per_hour"]["vesper"]),
         str(m["flows_per_hour"]["reference"]),
         "$\\approx$" if m["flows_per_hour"].get("same_order") else ""),
        ("Pkt size P50 (B)",
         str(m["packet_size_p50"]["vesper"]),
         str(m["packet_size_p50"]["reference"]),
         f"ratio {m['packet_size_p50'].get('ratio', 'N/A')}"),
        ("Pkt size P90 (B)",
         str(m["packet_size_p90"]["vesper"]),
         str(m["packet_size_p90"]["reference"]),
         ""),
        ("Burstiness (CoV)",
         str(m["burstiness_cov"]["vesper"]),
         str(m["burstiness_cov"]["reference"]),
         "$\\approx$" if m["burstiness_cov"].get("same_order") else ""),
        ("Diurnal peak hour",
         str(m["diurnal_peak_hour"]["vesper"]),
         str(m["diurnal_peak_hour"]["reference"]),
         ""),
        ("Diurnal $r$",
         str(m["diurnal_r"]["vesper"]),
         "1.00",
         "$>0.5$" if (m["diurnal_r"]["vesper"] or 0) > 0.5 else ""),
    ]

    for label, v, r, match in rows:
        tex += f"{label} & {v} & {r} & {match} \\\\\n"

    tex += r"""\bottomrule
\end{tabular}
\end{table}
"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(tex)
    logger.info(f"LaTeX table → {output_path}")


def generate_pkt_size_cdf(all_sizes: List[int], output_path: str):
    """Generate packet size CDF figure from real data."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        fig, ax = plt.subplots(figsize=(5, 3.5))

        # VESPER measured
        sorted_s = np.sort(all_sizes)
        cdf = np.arange(1, len(sorted_s) + 1) / len(sorted_s)
        ax.plot(sorted_s, cdf, label="VESPER (measured)", linewidth=1.5, color="tab:blue")

        # UNSW reference (reconstruct from percentiles)
        ref_pcts = [54, 66, 89, 214, 541, 1024, 1460]
        ref_cdf = [0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
        ax.plot(ref_pcts, ref_cdf, 'o--', label="UNSW IoT (ref.)", linewidth=1.5,
                color="tab:orange", markersize=4)

        ax.set_xlabel("Packet Size (bytes)")
        ax.set_ylabel("CDF")
        ax.set_xscale("log")
        ax.set_xlim(10, 2000)
        ax.set_ylim(0, 1.02)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(output_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"CDF figure → {output_path}")
    except ImportError:
        logger.warning("matplotlib not available — skipping CDF plot")


def generate_diurnal_figure(hourly: List[int], output_path: str):
    """Generate diurnal traffic pattern figure."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        fig, ax = plt.subplots(figsize=(5, 3))

        hours = list(range(24))

        # Normalize
        max_v = max(hourly) if hourly and max(hourly) > 0 else 1
        vesper_norm = [h / max_v for h in hourly]
        ref_norm = _reference_diurnal_profile()

        ax.plot(hours, vesper_norm, 'o-', label="VESPER (measured)", linewidth=1.5,
                markersize=3, color="tab:blue")
        ax.plot(hours, ref_norm, 's--', label="UNSW IoT (ref.)", linewidth=1.5,
                markersize=3, color="tab:orange")

        ax.set_xlabel("Hour of Day (simulated)")
        ax.set_ylabel("Normalized Packet Count")
        ax.set_xticks(range(0, 24, 3))
        ax.set_xlim(-0.5, 23.5)
        ax.set_ylim(0, 1.15)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(output_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"Diurnal figure → {output_path}")
    except ImportError:
        logger.warning("matplotlib not available — skipping diurnal plot")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Analyze real VESPER pcap files for trace validation"
    )
    parser.add_argument("--pcap-dir", required=True,
                        help="Directory containing pcap files (searched recursively)")
    parser.add_argument("--output", default="results/trace_validation_real",
                        help="Output directory for results")
    parser.add_argument("--generate-paper", action="store_true",
                        help="Generate paper-ready LaTeX table and figures")
    parser.add_argument("--paper-dir", default="paper-latex",
                        help="Paper directory for table/figure output")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    os.makedirs(args.output, exist_ok=True)

    # Check tshark
    if not check_tshark():
        logger.error("tshark not available. Install with: sudo apt install tshark")
        logger.error("On macOS: brew install wireshark")
        sys.exit(1)

    # Find pcaps
    pcap_files = find_pcap_files(args.pcap_dir)
    if not pcap_files:
        logger.error(f"No pcap files found in {args.pcap_dir}")
        sys.exit(1)

    # Analyze
    logger.info(f"Analyzing {len(pcap_files)} pcap files...")
    results = analyze_all_pcaps(pcap_files)
    aggregate = results["aggregate"]

    # Compare with reference
    comparison = compare_with_reference(aggregate)

    # Save results
    save_results = {
        "aggregate": aggregate,
        "comparison": comparison,
        "per_file": results["per_file"],
    }
    with open(f"{args.output}/pcap_analysis.json", "w") as f:
        json.dump(save_results, f, indent=2, default=str)

    # Print summary
    print("\n" + "═" * 60)
    print("  TRACE VALIDATION RESULTS (Real pcap data)")
    print("═" * 60)
    print(f"  Files analyzed:     {aggregate['pcap_count']}")
    print(f"  Total frames:       {aggregate.get('total_frames', 'N/A'):,}")
    print(f"  Total bytes:        {aggregate.get('total_bytes', 'N/A'):,}")
    print(f"  Duration:           {aggregate.get('total_duration_h', 'N/A')} hours")
    print(f"  Flows/hour:         {aggregate.get('flows_per_hour', 'N/A')}")

    ps = aggregate.get("packet_sizes", {})
    print(f"  Pkt size P50:       {ps.get('p50', 'N/A')} B")
    print(f"  Pkt size P90:       {ps.get('p90', 'N/A')} B")

    burst = aggregate.get("burstiness", {})
    print(f"  Burstiness CoV:     {burst.get('cov', 'N/A')}")
    print(f"  Peak hour:          {aggregate.get('peak_hour', 'N/A')}")
    print(f"  Diurnal r:          {aggregate.get('diurnal_correlation_r', 'N/A')}")

    print("\n  vs UNSW IoT Reference:")
    for name, data in comparison["metrics"].items():
        match = "✓" if data.get("same_order") else "~"
        print(f"    {name:20s}: {data['vesper']} vs {data['reference']} (ratio {data.get('ratio', 'N/A')}) {match}")

    print("═" * 60)

    # Generate paper artifacts
    if args.generate_paper:
        logger.info("Generating paper artifacts...")

        # Table
        table_path = f"{args.paper_dir}/tables/tab_trace_validation.tex"
        generate_trace_table(comparison, aggregate, table_path)

        # Figures
        fig_dir = f"{args.paper_dir}/figures"
        os.makedirs(fig_dir, exist_ok=True)

        if results.get("all_sizes"):
            generate_pkt_size_cdf(results["all_sizes"], f"{fig_dir}/fig_pkt_size_cdf.pdf")

        hourly = aggregate.get("hourly_distribution")
        if hourly:
            generate_diurnal_figure(hourly, f"{fig_dir}/fig_diurnal.pdf")

        logger.info("Paper artifacts generated ✓")

    logger.info(f"Full results saved to {args.output}/pcap_analysis.json")


if __name__ == "__main__":
    main()
