#!/usr/bin/env python3
"""
VESPER ML Experiment Runner

Runs the full suite of anomaly detection experiments:
  1. Fidelity comparison:  FIL (QEMU) vs VESPER-Soft baseline
  2. Config comparison:    Config A (context-free) vs B (context-aware) vs C (oracle)
  3. Model comparison:     RF, XGBoost, 1D-CNN, Transformer
  4. Ablation study:       Drop feature groups to measure contribution

Usage:
    python scripts/run_ml_experiments.py --all
    python scripts/run_ml_experiments.py --fidelity
    python scripts/run_ml_experiments.py --config-comparison
    python scripts/run_ml_experiments.py --ablation
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vesper.evaluation.anomaly_detection.trainer import ModelTrainer
from vesper.evaluation.feature_extraction import (
    PcapFeatureExtractor,
    FLOW_FEATURES,
    MQTT_FEATURES,
    TEMPORAL_FEATURES,
    PAYLOAD_FEATURES,
    ALL_FEATURE_NAMES,
)
from vesper.evaluation.context_features import ALL_CONTEXT_FEATURE_NAMES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ml_experiments")

MODELS = ["random_forest", "xgboost", "cnn_1d", "transformer"]
N_SPLITS = 5


def run_config_comparison(dataset_dir: str, output_dir: str):
    """
    Experiment 1: Compare Config A/B/C across all models.

    This is the core experiment demonstrating that context-aware features
    (Config B) outperform context-free features (Config A).
    """
    print("\n" + "=" * 70)
    print("  EXPERIMENT: Feature Configuration Comparison (A vs B vs C)")
    print("=" * 70)

    configs = {}
    for name, filename in [
        ("A", "config_A_context_free.csv"),
        ("B", "config_B_context_aware.csv"),
        ("C", "config_C_oracle.csv"),
    ]:
        path = os.path.join(dataset_dir, filename)
        if os.path.exists(path):
            configs[name] = path
        else:
            logger.warning(f"Config {name} not found: {path}")

    if not configs:
        print("  No datasets found. Run pcap_attack_capture.py and dataset_builder first.")
        return {}

    trainer = ModelTrainer(dataset_path=list(configs.values())[0])
    results = trainer.run_config_comparison(
        config_paths=configs,
        models=MODELS,
        n_splits=N_SPLITS,
    )

    # Export
    out = Path(output_dir) / "config_comparison"
    out.mkdir(parents=True, exist_ok=True)

    with open(out / "results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Print summary table
    print("\n  Config Comparison Summary (Mean F1 ± Std):")
    print(f"  {'Model':<20} {'Config A':>12} {'Config B':>12} {'Config C':>12}")
    print("  " + "-" * 56)
    for model in MODELS:
        row = f"  {model:<20}"
        for config in ["A", "B", "C"]:
            if config in results and model in results[config]:
                mean = results[config][model].get("mean_metrics", {}).get("f1", 0)
                std = results[config][model].get("std_metrics", {}).get("f1", 0)
                row += f" {mean:.3f}±{std:.3f}"
            else:
                row += f" {'N/A':>12}"
        print(row)

    return results


def run_fidelity_comparison(
    fil_dataset: str,
    soft_dataset: str,
    output_dir: str,
):
    """
    Experiment 2: Compare FIL (QEMU) vs VESPER-Soft detection rates.

    Demonstrates that firmware-in-the-loop produces higher-fidelity
    traffic that is more representative of real IoT devices.
    """
    print("\n" + "=" * 70)
    print("  EXPERIMENT: Fidelity Comparison (FIL vs VESPER-Soft)")
    print("=" * 70)

    results = {}

    for label, dataset_path in [("FIL", fil_dataset), ("VESPER-Soft", soft_dataset)]:
        if not os.path.exists(dataset_path):
            logger.warning(f"{label} dataset not found: {dataset_path}")
            continue

        print(f"\n  --- {label} ---")
        trainer = ModelTrainer(dataset_path=dataset_path)
        trainer.load_data()
        results[label] = trainer.run_experiment(models=MODELS, n_splits=N_SPLITS)

    out = Path(output_dir) / "fidelity_comparison"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Summary
    if len(results) == 2:
        print("\n  Fidelity Comparison (Mean F1):")
        print(f"  {'Model':<20} {'FIL':>10} {'VESPER-Soft':>12} {'Delta':>10}")
        print("  " + "-" * 52)
        for model in MODELS:
            fil_f1 = results.get("FIL", {}).get(model, {}).get("mean_metrics", {}).get("f1", 0)
            soft_f1 = results.get("VESPER-Soft", {}).get(model, {}).get("mean_metrics", {}).get("f1", 0)
            delta = fil_f1 - soft_f1
            print(f"  {model:<20} {fil_f1:>10.3f} {soft_f1:>12.3f} {delta:>+10.3f}")

    return results


def run_ablation(dataset_path: str, output_dir: str):
    """
    Experiment 3: Feature group ablation study.

    Drops one feature group at a time to measure each group's contribution.
    """
    print("\n" + "=" * 70)
    print("  EXPERIMENT: Feature Ablation Study")
    print("=" * 70)

    if not os.path.exists(dataset_path):
        print(f"  Dataset not found: {dataset_path}")
        return {}

    feature_groups = {
        "all_features": list(ALL_FEATURE_NAMES) + list(ALL_CONTEXT_FEATURE_NAMES),
        "drop_flow": [f for f in ALL_FEATURE_NAMES if f not in FLOW_FEATURES] + list(ALL_CONTEXT_FEATURE_NAMES),
        "drop_mqtt": [f for f in ALL_FEATURE_NAMES if f not in MQTT_FEATURES] + list(ALL_CONTEXT_FEATURE_NAMES),
        "drop_temporal": [f for f in ALL_FEATURE_NAMES if f not in TEMPORAL_FEATURES] + list(ALL_CONTEXT_FEATURE_NAMES),
        "drop_payload": [f for f in ALL_FEATURE_NAMES if f not in PAYLOAD_FEATURES] + list(ALL_CONTEXT_FEATURE_NAMES),
        "drop_context": list(ALL_FEATURE_NAMES),
        "flow_only": list(FLOW_FEATURES),
        "context_only": list(ALL_CONTEXT_FEATURE_NAMES),
    }

    results = {}
    # Use a single model for ablation (XGBoost is fast + accurate)
    ablation_models = ["xgboost", "random_forest"]

    for group_name, features in feature_groups.items():
        print(f"\n  [{group_name}] ({len(features)} features)")
        trainer = ModelTrainer(
            dataset_path=dataset_path,
            feature_cols=features,
        )
        try:
            trainer.load_data()
            results[group_name] = trainer.run_experiment(
                models=ablation_models, n_splits=N_SPLITS
            )
        except Exception as e:
            logger.error(f"  Failed: {e}")
            results[group_name] = {"error": str(e)}

    out = Path(output_dir) / "ablation"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Summary
    print("\n  Ablation Summary (XGBoost Mean F1):")
    print(f"  {'Feature Set':<25} {'F1':>8} {'Delta':>10}")
    print("  " + "-" * 43)
    baseline_f1 = 0
    for group_name, data in results.items():
        if "error" in data:
            continue
        f1 = data.get("xgboost", {}).get("mean_metrics", {}).get("f1", 0)
        if group_name == "all_features":
            baseline_f1 = f1
        delta = f1 - baseline_f1
        print(f"  {group_name:<25} {f1:>8.3f} {delta:>+10.3f}")

    return results


def main():
    parser = argparse.ArgumentParser(description="VESPER ML Experiment Runner")
    parser.add_argument("--all", action="store_true", help="Run all experiments")
    parser.add_argument("--config-comparison", action="store_true")
    parser.add_argument("--fidelity", action="store_true")
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument(
        "--dataset-dir", type=str, default="results/datasets",
        help="Directory containing Config A/B/C CSV files"
    )
    parser.add_argument(
        "--output-dir", type=str, default="results/ml",
        help="Output directory for experiment results"
    )
    parser.add_argument(
        "--fil-dataset", type=str, default="results/datasets/config_B_context_aware.csv",
        help="FIL dataset for fidelity comparison"
    )
    parser.add_argument(
        "--soft-dataset", type=str, default="results/datasets_soft/config_B_context_aware.csv",
        help="VESPER-Soft dataset for fidelity comparison"
    )
    args = parser.parse_args()

    run_all = args.all or not any([args.config_comparison, args.fidelity, args.ablation])

    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " VESPER ML Experiment Suite ".center(68) + "║")
    print("╚" + "═" * 68 + "╝")

    start = time.time()

    if run_all or args.config_comparison:
        run_config_comparison(args.dataset_dir, args.output_dir)

    if run_all or args.fidelity:
        run_fidelity_comparison(args.fil_dataset, args.soft_dataset, args.output_dir)

    if run_all or args.ablation:
        ablation_dataset = os.path.join(args.dataset_dir, "config_B_context_aware.csv")
        run_ablation(ablation_dataset, args.output_dir)

    elapsed = time.time() - start
    print(f"\n  Total experiment time: {elapsed:.1f}s")
    print(f"  Results saved to: {args.output_dir}")


if __name__ == "__main__":
    main()
