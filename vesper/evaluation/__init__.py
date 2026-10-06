"""
VESPER Evaluation Framework.

Comprehensive evaluation suite for conference-quality research assessment.
Includes metrics computation, scalability benchmarks, activity realism
comparison, LLM ablation studies, and automated report generation.
"""

from .metrics import (
    MetricsCollector,
    ActivityDistributionMetrics,
    LatencyMetrics,
    ScalabilityMetrics,
    compute_kl_divergence,
    compute_wasserstein_distance,
    compute_transition_matrix,
    compute_schedule_entropy,
    cohens_d,
)
from .experiment_runner import ExperimentRunner, ExperimentConfig
from .report_generator import ReportGenerator
from .instrumentation import instrument_all

__all__ = [
    "MetricsCollector",
    "ActivityDistributionMetrics",
    "LatencyMetrics",
    "ScalabilityMetrics",
    "ExperimentRunner",
    "ExperimentConfig",
    "ReportGenerator",
    "instrument_all",
    "compute_kl_divergence",
    "compute_wasserstein_distance",
    "compute_transition_matrix",
    "compute_schedule_entropy",
    "cohens_d",
]
