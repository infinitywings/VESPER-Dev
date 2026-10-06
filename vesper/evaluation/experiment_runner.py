"""
Experiment Runner for VESPER.

Orchestrates reproducible evaluation experiments from YAML configs.
Handles:
- Loading experiment configurations
- Running experiments with multiple seeds
- Collecting and aggregating results
- Exporting results for analysis and paper generation
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import yaml

from .activity_comparison import (
    ActivityComparisonPipeline,
    load_aras_dataset,
    load_casas_dataset,
    load_custom_dataset,
    load_vesper_schedules,
)
from .latency_profiler import LatencyBenchmark, LatencyProfiler
from .llm_ablation import LLMAblationRunner, PERSONA_LIBRARY, LMSTUDIO_MODELS
from .metrics import MetricsCollector, confidence_interval
from .scalability_bench import ScalabilitySuite

logger = logging.getLogger(__name__)


# =============================================================================
# Experiment Configuration
# =============================================================================

@dataclass
class ExperimentConfig:
    """Configuration for a single experiment."""
    name: str = "vesper_evaluation"
    description: str = ""
    seed: int = 42
    output_dir: str = "results"

    # Which experiments to run
    run_activity_comparison: bool = True
    run_scalability: bool = True
    run_latency: bool = True
    run_llm_ablation: bool = True

    # Activity comparison settings
    reference_datasets: Dict[str, str] = field(default_factory=dict)
    vesper_db_path: str = "logs/vesper_tasks.db"
    vesper_json_export: str = ""
    comparison_days: int = 30

    # Scalability settings
    device_counts: List[int] = field(default_factory=lambda: [5, 10, 25, 50, 100, 200])
    container_counts: List[int] = field(default_factory=lambda: [3, 5, 10, 20])
    duration_hours: List[float] = field(default_factory=lambda: [1, 6, 24, 168])
    scalability_trials: int = 5
    scalability_duration_s: float = 30.0

    # Latency settings
    latency_iterations: int = 1000

    # LLM ablation settings
    lmstudio_url: str = "http://localhost:1234/v1/chat/completions"
    llm_models: List[str] = field(default_factory=lambda: list(LMSTUDIO_MODELS.keys()))
    llm_personas: List[Dict[str, Any]] = field(default_factory=lambda: PERSONA_LIBRARY)
    llm_schedules_per_config: int = 30
    llm_temperature: float = 0.7

    # Statistical settings
    confidence_level: float = 0.95
    num_trials: int = 5

    @classmethod
    def from_yaml(cls, path: str) -> "ExperimentConfig":
        """Load configuration from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})

    def to_yaml(self, path: str):
        """Save configuration to YAML file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "name": self.name,
            "description": self.description,
            "seed": self.seed,
            "output_dir": self.output_dir,
            "run_activity_comparison": self.run_activity_comparison,
            "run_scalability": self.run_scalability,
            "run_latency": self.run_latency,
            "run_llm_ablation": self.run_llm_ablation,
            "reference_datasets": self.reference_datasets,
            "vesper_db_path": self.vesper_db_path,
            "comparison_days": self.comparison_days,
            "device_counts": self.device_counts,
            "container_counts": self.container_counts,
            "duration_hours": self.duration_hours,
            "scalability_trials": self.scalability_trials,
            "scalability_duration_s": self.scalability_duration_s,
            "latency_iterations": self.latency_iterations,
            "lmstudio_url": self.lmstudio_url,
            "llm_models": self.llm_models,
            "llm_schedules_per_config": self.llm_schedules_per_config,
            "llm_temperature": self.llm_temperature,
            "confidence_level": self.confidence_level,
            "num_trials": self.num_trials,
        }
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# =============================================================================
# Experiment Runner
# =============================================================================

class ExperimentRunner:
    """
    Orchestrates all VESPER evaluation experiments.

    Loads a config, runs selected experiments, collects results,
    and exports everything for paper generation.
    """

    def __init__(self, config: Optional[ExperimentConfig] = None):
        self.config = config or ExperimentConfig()
        self.output_dir = Path(self.config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.collector = MetricsCollector(str(self.output_dir))
        self._results: Dict[str, Any] = {}
        self._start_time: float = 0
        self._experiment_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Set seeds
        random.seed(self.config.seed)
        np.random.seed(self.config.seed)

        logger.info(
            f"ExperimentRunner initialized: {self.config.name} "
            f"(id={self._experiment_id})"
        )

    async def run(self) -> Dict[str, Any]:
        """Run all configured experiments."""
        self._start_time = time.time()
        self._results = {
            "experiment_name": self.config.name,
            "experiment_id": self._experiment_id,
            "config": {
                "seed": self.config.seed,
                "confidence_level": self.config.confidence_level,
                "num_trials": self.config.num_trials,
            },
            "started_at": datetime.now().isoformat(),
        }

        try:
            if self.config.run_activity_comparison:
                logger.info("=" * 60)
                logger.info("Running Activity Comparison...")
                logger.info("=" * 60)
                self._results["activity_comparison"] = await self._run_activity_comparison()

            if self.config.run_latency:
                logger.info("=" * 60)
                logger.info("Running Latency Benchmark...")
                logger.info("=" * 60)
                self._results["latency"] = await self._run_latency_benchmark()

            if self.config.run_scalability:
                logger.info("=" * 60)
                logger.info("Running Scalability Benchmark...")
                logger.info("=" * 60)
                self._results["scalability"] = await self._run_scalability()

            if self.config.run_llm_ablation:
                logger.info("=" * 60)
                logger.info("Running LLM Ablation Study...")
                logger.info("=" * 60)
                self._results["llm_ablation"] = await self._run_llm_ablation()

        except Exception as e:
            logger.error(f"Experiment failed: {e}", exc_info=True)
            self._results["error"] = str(e)

        self._results["completed_at"] = datetime.now().isoformat()
        self._results["total_duration_s"] = time.time() - self._start_time

        # Export
        self._export_results()

        logger.info(
            f"Experiment complete in {self._results['total_duration_s']:.1f}s. "
            f"Results at {self.output_dir}"
        )

        return self._results

    async def _run_activity_comparison(self) -> Dict[str, Any]:
        """Run activity comparison against reference datasets."""
        results = {}

        # Load VESPER schedules
        vesper_schedules = load_vesper_schedules(
            db_path=self.config.vesper_db_path,
            json_export_path=self.config.vesper_json_export or None,
            days=self.config.comparison_days,
        )

        if not vesper_schedules:
            logger.warning("No VESPER schedules found — generating synthetic data for comparison")
            # Generate some using rule-based baseline for demonstration
            from .llm_ablation import RuleBasedScheduleGenerator
            from .activity_comparison import DaySchedule, ActivityRecord, VESPER_TO_UNIFIED
            gen = RuleBasedScheduleGenerator()
            for d in range(30):
                persona = PERSONA_LIBRARY[d % len(PERSONA_LIBRARY)]
                day_type = "weekday" if d % 7 < 5 else "weekend"
                sched = gen.generate(persona, day_type, seed=self.config.seed + d)
                base_date = datetime(2025, 1, 1) + timedelta(days=d)
                day = DaySchedule(date=base_date, source="vesper_synthetic")
                for t in sched:
                    h, m = map(int, t["start_time"].split(":"))
                    start = base_date.replace(hour=h, minute=m)
                    dur = t["duration_minutes"]
                    end = start + timedelta(minutes=dur)
                    unified = VESPER_TO_UNIFIED.get(t["category"], "Other")
                    day.activities.append(ActivityRecord(
                        activity=unified,
                        start_time=start,
                        end_time=end,
                        duration_minutes=dur,
                        room=t.get("room"),
                        source_dataset="vesper",
                        raw_label=t["category"],
                    ))
                vesper_schedules.append(day)

        results["vesper_schedule_count"] = len(vesper_schedules)

        # Compare against each reference dataset
        for dataset_name, dataset_path in self.config.reference_datasets.items():
            if not Path(dataset_path).exists():
                logger.warning(f"Reference dataset not found: {dataset_path}")
                continue

            if dataset_name.lower() == "casas":
                ref_schedules = load_casas_dataset(dataset_path)
            elif dataset_name.lower() == "aras":
                ref_schedules = load_aras_dataset(dataset_path)
            else:
                ref_schedules = load_custom_dataset(dataset_path)

            if ref_schedules:
                pipeline = ActivityComparisonPipeline(
                    vesper_schedules, ref_schedules, dataset_name
                )
                metrics = pipeline.compare()
                results[dataset_name] = metrics.to_dict()

        return results

    async def _run_latency_benchmark(self) -> Dict[str, Any]:
        """Run latency benchmarks."""
        profiler = LatencyProfiler(self.collector)
        benchmark = LatencyBenchmark(
            profiler=profiler,
            iterations=self.config.latency_iterations,
        )

        results = {}

        # Event bus latency
        from vesper.simulation.event_stream import EventStream
        event_stream = EventStream()
        event_stream.start()
        metrics = await benchmark.benchmark_event_bus(event_stream)
        results["event_bus"] = metrics.to_dict()
        event_stream.stop()

        # DB write latency
        metrics = await benchmark.benchmark_db_writes()
        results["db_write"] = metrics.to_dict()

        # Docker round-trip (if containers running)
        try:
            metrics = await benchmark.benchmark_docker_roundtrip()
            if metrics.count > 0:
                results["docker_roundtrip"] = metrics.to_dict()
        except Exception:
            logger.info("Docker containers not running — skipping TCP benchmark")

        return results

    async def _run_scalability(self) -> Dict[str, Any]:
        """Run scalability benchmarks."""
        suite = ScalabilitySuite(
            output_dir=str(self.output_dir / "scalability")
        )

        results = {}

        # Device scaling
        device_results = await suite.run_device_scaling(
            device_counts=self.config.device_counts,
            duration_s=self.config.scalability_duration_s,
            trials=self.config.scalability_trials,
        )
        results["device_scaling"] = device_results

        # Duration stability (shorter for CI)
        stability_results = await suite.run_duration_stability(
            durations_hours=[1, 6],  # Shorter for automated runs
            trials=min(self.config.scalability_trials, 2),
        )
        results["duration_stability"] = stability_results

        return results

    async def _run_llm_ablation(self) -> Dict[str, Any]:
        """Run LLM ablation study."""
        runner = LLMAblationRunner(
            lmstudio_url=self.config.lmstudio_url,
            models=self.config.llm_models,
            personas=self.config.llm_personas,
            schedules_per_config=self.config.llm_schedules_per_config,
            temperature=self.config.llm_temperature,
            output_dir=str(self.output_dir / "llm_ablation"),
        )

        results_list = await runner.run_all()

        return {
            "model_summary": runner.get_model_summary(),
            "pairwise_comparisons": runner.compute_pairwise_comparisons(),
            "num_results": len(results_list),
            "details": [r.to_dict() for r in results_list],
        }

    def _export_results(self):
        """Export all results to files."""
        exp_dir = self.output_dir / f"experiment_{self._experiment_id}"
        exp_dir.mkdir(parents=True, exist_ok=True)

        # Main results
        (exp_dir / "results.json").write_text(
            json.dumps(self._results, indent=2, default=str)
        )

        # Config (for reproducibility)
        self.config.to_yaml(str(exp_dir / "config.yaml"))

        # Metrics
        self.collector.export_all(f"metrics_{self._experiment_id}")

        logger.info(f"Results exported to {exp_dir}")


# =============================================================================
# CLI Entry Point
# =============================================================================

async def main():
    """CLI entry point for running experiments."""
    import argparse

    parser = argparse.ArgumentParser(description="VESPER Evaluation Runner")
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to experiment config YAML",
    )
    parser.add_argument(
        "--experiment", "-e",
        type=str,
        choices=["all", "activity", "latency", "scalability", "llm"],
        default="all",
        help="Which experiment to run",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="results",
        help="Output directory",
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=42,
        help="Random seed",
    )
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="Generate a default config file and exit",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if args.generate_config:
        config = ExperimentConfig()
        config.to_yaml("configs/evaluation.yaml")
        print("Generated default config at configs/evaluation.yaml")
        return

    if args.config:
        config = ExperimentConfig.from_yaml(args.config)
    else:
        config = ExperimentConfig(
            seed=args.seed,
            output_dir=args.output,
        )

    # Apply experiment filter
    if args.experiment != "all":
        config.run_activity_comparison = args.experiment == "activity"
        config.run_latency = args.experiment == "latency"
        config.run_scalability = args.experiment == "scalability"
        config.run_llm_ablation = args.experiment == "llm"

    runner = ExperimentRunner(config)
    results = await runner.run()

    print(f"\n{'=' * 60}")
    print(f"Experiment complete: {config.name}")
    print(f"Duration: {results.get('total_duration_s', 0):.1f}s")
    print(f"Results: {config.output_dir}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
