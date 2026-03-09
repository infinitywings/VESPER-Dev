"""
Metrics computation for VESPER evaluation.

Implements statistical metrics for measuring:
- Activity realism (KL divergence, Wasserstein distance, transition matrices)
- Schedule diversity (entropy, variance)
- System performance (latency percentiles, throughput)
- Scalability (resource usage under load)
- Statistical significance (Cohen's d, confidence intervals)
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# =============================================================================
# Statistical Utility Functions
# =============================================================================

def compute_kl_divergence(
    p: np.ndarray,
    q: np.ndarray,
    epsilon: float = 1e-10,
) -> float:
    """
    Compute KL divergence D_KL(P || Q).

    Args:
        p: Reference distribution (e.g., real-world dataset).
        q: Target distribution (e.g., VESPER-generated).
        epsilon: Smoothing constant to avoid log(0).

    Returns:
        KL divergence (non-negative float). Lower = more similar.
    """
    p = np.asarray(p, dtype=np.float64) + epsilon
    q = np.asarray(q, dtype=np.float64) + epsilon
    p = p / p.sum()
    q = q / q.sum()
    return float(np.sum(p * np.log(p / q)))


def compute_js_divergence(
    p: np.ndarray,
    q: np.ndarray,
    epsilon: float = 1e-10,
) -> float:
    """
    Compute Jensen-Shannon divergence (symmetric, bounded [0, ln2]).

    Args:
        p: Distribution P.
        q: Distribution Q.
        epsilon: Smoothing constant.

    Returns:
        JS divergence (bounded, symmetric).
    """
    p = np.asarray(p, dtype=np.float64) + epsilon
    q = np.asarray(q, dtype=np.float64) + epsilon
    p = p / p.sum()
    q = q / q.sum()
    m = 0.5 * (p + q)
    return float(0.5 * compute_kl_divergence(p, m, 0) + 0.5 * compute_kl_divergence(q, m, 0))


def compute_wasserstein_distance(
    p: np.ndarray,
    q: np.ndarray,
) -> float:
    """
    Compute 1D Wasserstein (Earth Mover's) distance.

    Args:
        p: Distribution P (histogram counts or probabilities).
        q: Distribution Q.

    Returns:
        Wasserstein-1 distance.
    """
    p = np.asarray(p, dtype=np.float64)
    q = np.asarray(q, dtype=np.float64)
    p = p / p.sum() if p.sum() > 0 else p
    q = q / q.sum() if q.sum() > 0 else q
    # CDF difference method for 1D EMD
    cdf_p = np.cumsum(p)
    cdf_q = np.cumsum(q)
    return float(np.sum(np.abs(cdf_p - cdf_q)))


def compute_transition_matrix(
    sequences: List[List[str]],
    states: Optional[List[str]] = None,
) -> Tuple[np.ndarray, List[str]]:
    """
    Compute state transition probability matrix from sequences.

    Args:
        sequences: List of state sequences (e.g., room visit sequences).
        states: Ordered list of state labels. Auto-detected if None.

    Returns:
        (transition_matrix, state_labels) where matrix[i][j] = P(j | i).
    """
    if states is None:
        states = sorted(set(s for seq in sequences for s in seq))

    state_idx = {s: i for i, s in enumerate(states)}
    n = len(states)
    counts = np.zeros((n, n), dtype=np.float64)

    for seq in sequences:
        for i in range(len(seq) - 1):
            if seq[i] in state_idx and seq[i + 1] in state_idx:
                counts[state_idx[seq[i]]][state_idx[seq[i + 1]]] += 1

    # Normalize rows to probabilities
    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1  # Avoid division by zero
    matrix = counts / row_sums

    return matrix, states


def compute_transition_matrix_distance(
    matrix_a: np.ndarray,
    matrix_b: np.ndarray,
) -> float:
    """
    Compute Frobenius norm distance between two transition matrices.

    Args:
        matrix_a: First transition matrix.
        matrix_b: Second transition matrix.

    Returns:
        Frobenius norm ||A - B||_F.
    """
    return float(np.linalg.norm(matrix_a - matrix_b, ord="fro"))


def compute_schedule_entropy(
    schedules: List[List[str]],
) -> float:
    """
    Compute average entropy of activity type distributions across schedules.
    Higher entropy = more diverse schedules.

    Args:
        schedules: List of schedules, each being a list of activity type strings.

    Returns:
        Mean entropy across schedules.
    """
    entropies = []
    for schedule in schedules:
        if not schedule:
            continue
        counts = Counter(schedule)
        total = sum(counts.values())
        probs = np.array([c / total for c in counts.values()])
        entropy = -np.sum(probs * np.log2(probs + 1e-10))
        entropies.append(entropy)

    return float(np.mean(entropies)) if entropies else 0.0


def compute_temporal_correlation(
    vesper_start_times: Dict[str, List[float]],
    real_start_times: Dict[str, List[float]],
    bins: int = 24,
) -> Dict[str, float]:
    """
    Compute temporal correlation of activity start times (hour of day).

    Args:
        vesper_start_times: {activity_type: [hour_of_day, ...]}.
        real_start_times: {activity_type: [hour_of_day, ...]}.
        bins: Number of hour bins (24 = hourly).

    Returns:
        {activity_type: pearson_correlation}.
    """
    correlations = {}
    for activity in set(vesper_start_times) & set(real_start_times):
        v_hist, _ = np.histogram(vesper_start_times[activity], bins=bins, range=(0, 24))
        r_hist, _ = np.histogram(real_start_times[activity], bins=bins, range=(0, 24))

        v_hist = v_hist.astype(np.float64)
        r_hist = r_hist.astype(np.float64)

        if v_hist.std() > 0 and r_hist.std() > 0:
            corr = np.corrcoef(v_hist, r_hist)[0, 1]
            correlations[activity] = float(corr)
        else:
            correlations[activity] = 0.0

    return correlations


def cohens_d(
    group1: Sequence[float],
    group2: Sequence[float],
) -> float:
    """
    Compute Cohen's d effect size between two groups.

    Args:
        group1: First group of measurements.
        group2: Second group of measurements.

    Returns:
        Cohen's d. |d| > 0.8 = large effect, > 1.2 = very large.
    """
    g1 = np.asarray(group1, dtype=np.float64)
    g2 = np.asarray(group2, dtype=np.float64)
    n1, n2 = len(g1), len(g2)
    if n1 < 2 or n2 < 2:
        return 0.0

    mean_diff = g1.mean() - g2.mean()
    pooled_var = ((n1 - 1) * g1.var(ddof=1) + (n2 - 1) * g2.var(ddof=1)) / (n1 + n2 - 2)
    pooled_std = np.sqrt(pooled_var)

    if pooled_std == 0:
        return 0.0
    return float(mean_diff / pooled_std)


def confidence_interval(
    data: Sequence[float],
    confidence: float = 0.95,
) -> Tuple[float, float, float]:
    """
    Compute mean and confidence interval using t-distribution.

    Args:
        data: Sample data.
        confidence: Confidence level (default 0.95 = 95% CI).

    Returns:
        (mean, ci_lower, ci_upper).
    """
    a = np.asarray(data, dtype=np.float64)
    n = len(a)
    if n < 2:
        m = float(a.mean()) if n > 0 else 0.0
        return m, m, m

    from scipy import stats
    m = float(a.mean())
    se = float(stats.sem(a))
    t_val = stats.t.ppf((1 + confidence) / 2, n - 1)
    margin = t_val * se
    return m, m - margin, m + margin


def wilcoxon_test(
    group1: Sequence[float],
    group2: Sequence[float],
) -> Tuple[float, float]:
    """
    Perform Wilcoxon signed-rank test (non-parametric paired test).

    Args:
        group1: First group.
        group2: Second group (paired).

    Returns:
        (statistic, p_value).
    """
    from scipy import stats
    try:
        stat, p = stats.wilcoxon(group1, group2)
        return float(stat), float(p)
    except ValueError:
        return 0.0, 1.0


def bonferroni_correction(
    p_values: List[float],
    alpha: float = 0.05,
) -> List[Tuple[float, bool]]:
    """
    Apply Bonferroni correction for multiple comparisons.

    Args:
        p_values: List of raw p-values.
        alpha: Family-wise significance level.

    Returns:
        List of (corrected_p, is_significant).
    """
    n = len(p_values)
    adjusted_alpha = alpha / n if n > 0 else alpha
    return [(p, p < adjusted_alpha) for p in p_values]


# =============================================================================
# Metric Data Structures
# =============================================================================

@dataclass
class ActivityDistributionMetrics:
    """Metrics comparing activity distributions against reference datasets."""
    kl_divergence: float = 0.0
    js_divergence: float = 0.0
    wasserstein_distance: float = 0.0
    transition_matrix_distance: float = 0.0
    temporal_correlation: Dict[str, float] = field(default_factory=dict)
    schedule_entropy: float = 0.0
    activity_counts_vesper: Dict[str, int] = field(default_factory=dict)
    activity_counts_reference: Dict[str, int] = field(default_factory=dict)
    duration_stats_vesper: Dict[str, Dict[str, float]] = field(default_factory=dict)
    duration_stats_reference: Dict[str, Dict[str, float]] = field(default_factory=dict)
    reference_dataset: str = ""
    num_vesper_schedules: int = 0
    num_reference_schedules: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kl_divergence": self.kl_divergence,
            "js_divergence": self.js_divergence,
            "wasserstein_distance": self.wasserstein_distance,
            "transition_matrix_distance": self.transition_matrix_distance,
            "temporal_correlation": self.temporal_correlation,
            "schedule_entropy": self.schedule_entropy,
            "activity_counts_vesper": self.activity_counts_vesper,
            "activity_counts_reference": self.activity_counts_reference,
            "duration_stats_vesper": self.duration_stats_vesper,
            "duration_stats_reference": self.duration_stats_reference,
            "reference_dataset": self.reference_dataset,
            "num_vesper_schedules": self.num_vesper_schedules,
            "num_reference_schedules": self.num_reference_schedules,
        }


@dataclass
class LatencyMetrics:
    """End-to-end latency measurements."""
    path_name: str = ""
    samples: List[float] = field(default_factory=list)
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    mean: float = 0.0
    std: float = 0.0
    min_val: float = 0.0
    max_val: float = 0.0
    count: int = 0

    def compute(self):
        """Compute statistics from samples."""
        if not self.samples:
            return
        a = np.array(self.samples)
        self.count = len(a)
        self.mean = float(a.mean())
        self.std = float(a.std())
        self.p50 = float(np.percentile(a, 50))
        self.p95 = float(np.percentile(a, 95))
        self.p99 = float(np.percentile(a, 99))
        self.min_val = float(a.min())
        self.max_val = float(a.max())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path_name,
            "count": self.count,
            "mean_ms": round(self.mean * 1000, 2),
            "std_ms": round(self.std * 1000, 2),
            "p50_ms": round(self.p50 * 1000, 2),
            "p95_ms": round(self.p95 * 1000, 2),
            "p99_ms": round(self.p99 * 1000, 2),
            "min_ms": round(self.min_val * 1000, 2),
            "max_ms": round(self.max_val * 1000, 2),
        }


@dataclass
class ScalabilityMetrics:
    """Resource usage metrics under scaling."""
    parameter_name: str = ""  # e.g., "num_devices"
    parameter_value: int = 0
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    event_throughput: float = 0.0  # events/sec
    avg_latency_ms: float = 0.0
    fps: float = 0.0  # frames per second (sim)
    container_startup_s: float = 0.0
    db_size_mb: float = 0.0
    errors: int = 0
    duration_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parameter": self.parameter_name,
            "value": self.parameter_value,
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_mb": round(self.memory_mb, 1),
            "event_throughput_per_sec": round(self.event_throughput, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "fps": round(self.fps, 1),
            "container_startup_s": round(self.container_startup_s, 2),
            "db_size_mb": round(self.db_size_mb, 2),
            "errors": self.errors,
            "duration_s": round(self.duration_s, 1),
        }


@dataclass
class LLMAblationResult:
    """Results from a single LLM ablation run."""
    model_name: str = ""
    persona_name: str = ""
    seed: int = 42
    num_schedules: int = 0
    generation_latency_s: float = 0.0
    schedule_entropy: float = 0.0
    activity_distribution: Dict[str, float] = field(default_factory=dict)
    duration_mean_min: float = 0.0
    duration_std_min: float = 0.0
    plausibility_score: Optional[float] = None  # Human evaluation if available
    context_sensitivity: float = 0.0  # Weekday vs weekend variance
    num_unique_activities: int = 0
    num_tasks_per_schedule: float = 0.0
    error_rate: float = 0.0  # Parse failures

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model_name,
            "persona": self.persona_name,
            "seed": self.seed,
            "num_schedules": self.num_schedules,
            "generation_latency_s": round(self.generation_latency_s, 2),
            "schedule_entropy": round(self.schedule_entropy, 4),
            "activity_distribution": self.activity_distribution,
            "duration_mean_min": round(self.duration_mean_min, 1),
            "duration_std_min": round(self.duration_std_min, 1),
            "plausibility_score": self.plausibility_score,
            "context_sensitivity": round(self.context_sensitivity, 4),
            "num_unique_activities": self.num_unique_activities,
            "num_tasks_per_schedule": round(self.num_tasks_per_schedule, 1),
            "error_rate": round(self.error_rate, 4),
        }


# =============================================================================
# Metrics Collector
# =============================================================================

class MetricsCollector:
    """
    Central metrics collector for VESPER evaluation.

    Collects, computes, and exports all evaluation metrics.
    Thread-safe for concurrent data collection.
    """

    def __init__(self, output_dir: str = "results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Latency tracking
        self._latency_records: Dict[str, List[float]] = defaultdict(list)

        # Event counting
        self._event_counts: Dict[str, int] = Counter()
        self._event_timestamps: List[float] = []

        # Resource snapshots
        self._resource_snapshots: List[Dict[str, Any]] = []

        # Schedule records
        self._generated_schedules: List[Dict[str, Any]] = []

        # Timing context
        self._timers: Dict[str, float] = {}

        logger.info(f"MetricsCollector initialized, output: {self.output_dir}")

    # --- Latency tracking ---

    def start_timer(self, name: str):
        """Start a named timer."""
        self._timers[name] = time.perf_counter()

    def stop_timer(self, name: str) -> float:
        """Stop a named timer and record the latency. Returns elapsed seconds."""
        if name not in self._timers:
            return 0.0
        elapsed = time.perf_counter() - self._timers.pop(name)
        self._latency_records[name].append(elapsed)
        return elapsed

    def record_latency(self, path_name: str, latency_seconds: float):
        """Record a single latency measurement."""
        self._latency_records[path_name].append(latency_seconds)

    def get_latency_metrics(self, path_name: str) -> LatencyMetrics:
        """Get computed latency metrics for a path."""
        m = LatencyMetrics(
            path_name=path_name,
            samples=list(self._latency_records.get(path_name, [])),
        )
        m.compute()
        return m

    def get_all_latency_metrics(self) -> Dict[str, LatencyMetrics]:
        """Get latency metrics for all tracked paths."""
        return {
            name: self.get_latency_metrics(name)
            for name in self._latency_records
        }

    # --- Event counting ---

    def record_event(self, event_type: str):
        """Record an event occurrence."""
        self._event_counts[event_type] += 1
        self._event_timestamps.append(time.time())

    def get_event_throughput(self, window_seconds: float = 60.0) -> float:
        """Compute events per second over recent window."""
        now = time.time()
        cutoff = now - window_seconds
        recent = [t for t in self._event_timestamps if t >= cutoff]
        if len(recent) < 2:
            return 0.0
        duration = recent[-1] - recent[0]
        return len(recent) / duration if duration > 0 else 0.0

    # --- Resource monitoring ---

    def snapshot_resources(self) -> Dict[str, Any]:
        """Take a snapshot of current system resource usage."""
        import psutil
        process = psutil.Process()
        snapshot = {
            "timestamp": time.time(),
            "cpu_percent": process.cpu_percent(interval=0.1),
            "memory_mb": process.memory_info().rss / (1024 * 1024),
            "threads": process.num_threads(),
            "open_files": len(process.open_files()),
        }
        self._resource_snapshots.append(snapshot)
        return snapshot

    # --- Schedule tracking ---

    def record_schedule(self, schedule_data: Dict[str, Any]):
        """Record a generated schedule for analysis."""
        self._generated_schedules.append({
            "timestamp": datetime.now().isoformat(),
            **schedule_data,
        })

    # --- Export ---

    def export_all(self, experiment_name: str) -> Path:
        """Export all collected metrics to a timestamped directory."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        exp_dir = self.output_dir / f"{experiment_name}_{ts}"
        exp_dir.mkdir(parents=True, exist_ok=True)

        # Latency
        latency_data = {
            name: self.get_latency_metrics(name).to_dict()
            for name in self._latency_records
        }
        (exp_dir / "latency.json").write_text(
            json.dumps(latency_data, indent=2)
        )

        # Events
        (exp_dir / "event_counts.json").write_text(
            json.dumps(dict(self._event_counts), indent=2)
        )

        # Resources
        (exp_dir / "resource_snapshots.json").write_text(
            json.dumps(self._resource_snapshots, indent=2, default=str)
        )

        # Schedules
        (exp_dir / "schedules.json").write_text(
            json.dumps(self._generated_schedules, indent=2, default=str)
        )

        logger.info(f"Exported metrics to {exp_dir}")
        return exp_dir
