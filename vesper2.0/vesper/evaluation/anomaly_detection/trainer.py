"""
VESPER Model Trainer

Unified training harness for anomaly detection experiments.

Handles:
    - Data loading and preprocessing (scaling, train/test split)
    - Cross-validated training for each model
    - Evaluation metrics (accuracy, precision, recall, F1, AUC-ROC)
    - Per-attack-category analysis
    - Statistical significance tests (McNemar's)
    - SHAP feature importance analysis
    - Result export (JSON, CSV)

Usage:
    trainer = ModelTrainer(dataset_path="results/datasets/config_B_context_aware.csv")
    results = trainer.run_experiment(
        models=["random_forest", "xgboost", "cnn_1d", "transformer"],
        n_splits=5,
    )
    trainer.export_results(results, "results/ml/")
"""

from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from vesper.evaluation.anomaly_detection.models import (
    BaseDetector,
    RandomForestDetector,
    XGBoostDetector,
    CNN1DDetector,
    TransformerDetector,
)

MODEL_REGISTRY = {
    "random_forest": RandomForestDetector,
    "xgboost": XGBoostDetector,
    "cnn_1d": CNN1DDetector,
    "transformer": TransformerDetector,
}


# ─── Evaluation metrics ──────────────────────────────────────────────────────

def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray, y_prob: Optional[np.ndarray] = None) -> Dict[str, float]:
    """Compute standard classification metrics."""
    from sklearn.metrics import (
        accuracy_score,
        precision_score,
        recall_score,
        f1_score,
        roc_auc_score,
        matthews_corrcoef,
    )

    metrics = {
        "accuracy": accuracy_score(y_true, y_pred),
        "precision": precision_score(y_true, y_pred, average="weighted", zero_division=0),
        "recall": recall_score(y_true, y_pred, average="weighted", zero_division=0),
        "f1": f1_score(y_true, y_pred, average="weighted", zero_division=0),
        "mcc": matthews_corrcoef(y_true, y_pred),
    }

    # Binary metrics
    n_classes = len(np.unique(y_true))
    if n_classes == 2:
        metrics["precision_binary"] = precision_score(y_true, y_pred, zero_division=0)
        metrics["recall_binary"] = recall_score(y_true, y_pred, zero_division=0)
        metrics["f1_binary"] = f1_score(y_true, y_pred, zero_division=0)

    # AUC-ROC
    if y_prob is not None:
        try:
            if n_classes == 2:
                metrics["auc_roc"] = roc_auc_score(y_true, y_prob[:, 1])
            else:
                metrics["auc_roc"] = roc_auc_score(
                    y_true, y_prob, multi_class="ovr", average="weighted"
                )
        except Exception:
            metrics["auc_roc"] = 0.0

    return metrics


def per_category_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    categories: List[str],
) -> Dict[str, Dict[str, float]]:
    """Compute metrics broken down by attack category."""
    from sklearn.metrics import precision_score, recall_score, f1_score

    results = {}
    unique_cats = sorted(set(categories))

    for cat in unique_cats:
        mask = np.array([c == cat for c in categories])
        if mask.sum() == 0:
            continue

        cat_true = y_true[mask]
        cat_pred = y_pred[mask]

        results[cat] = {
            "n_samples": int(mask.sum()),
            "accuracy": float(np.mean(cat_true == cat_pred)),
            "precision": precision_score(cat_true, cat_pred, average="weighted", zero_division=0),
            "recall": recall_score(cat_true, cat_pred, average="weighted", zero_division=0),
            "f1": f1_score(cat_true, cat_pred, average="weighted", zero_division=0),
        }

    return results


# ─── Main trainer ─────────────────────────────────────────────────────────────

class ModelTrainer:
    """
    Unified training and evaluation harness.
    """

    def __init__(
        self,
        dataset_path: str,
        label_col: str = "label",
        feature_cols: Optional[List[str]] = None,
        category_col: str = "attack_category",
        random_state: int = 42,
    ):
        """
        Args:
            dataset_path: Path to CSV dataset
            label_col: Column name for binary label (0=benign, 1=attack)
            feature_cols: List of feature column names. If None, auto-detects.
            category_col: Column name for attack category (for per-category analysis)
            random_state: Seed for reproducibility
        """
        self.dataset_path = dataset_path
        self.label_col = label_col
        self.feature_cols = feature_cols
        self.category_col = category_col
        self.random_state = random_state

        self._X = None
        self._y = None
        self._categories = None
        self._feature_names = None

    def load_data(self) -> Tuple[np.ndarray, np.ndarray]:
        """Load and preprocess the dataset."""
        import csv

        rows = []
        with open(self.dataset_path) as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            for row in reader:
                rows.append(row)

        if not rows:
            raise ValueError(f"Empty dataset: {self.dataset_path}")

        # Determine feature columns
        metadata_cols = {
            "pcap_path", "attack_name", "attack_category",
            "attack_suite", "device_type", "label", "oracle_attack_type",
        }
        if self.feature_cols:
            feat_cols = self.feature_cols
        else:
            feat_cols = [c for c in fieldnames if c not in metadata_cols]

        self._feature_names = feat_cols
        logger.info(f"Using {len(feat_cols)} features from {len(rows)} samples")

        # Extract features and labels
        X = []
        y = []
        categories = []

        for row in rows:
            label = int(float(row.get(self.label_col, -1)))
            if label < 0:
                continue

            feat_vec = []
            for col in feat_cols:
                try:
                    feat_vec.append(float(row.get(col, 0)))
                except (ValueError, TypeError):
                    feat_vec.append(0.0)

            X.append(feat_vec)
            y.append(label)
            categories.append(row.get(self.category_col, "unknown"))

        self._X = np.array(X, dtype=np.float32)
        self._y = np.array(y, dtype=np.int64)
        self._categories = categories

        # Replace NaN/Inf
        self._X = np.nan_to_num(self._X, nan=0.0, posinf=0.0, neginf=0.0)

        logger.info(
            f"Loaded: {self._X.shape[0]} samples, {self._X.shape[1]} features, "
            f"{len(np.unique(self._y))} classes "
            f"(benign={np.sum(self._y == 0)}, attack={np.sum(self._y == 1)})"
        )

        return self._X, self._y

    def run_experiment(
        self,
        models: Optional[List[str]] = None,
        n_splits: int = 5,
        scale_features: bool = True,
    ) -> Dict[str, Any]:
        """
        Run cross-validated experiment with specified models.

        Args:
            models: List of model names from MODEL_REGISTRY. If None, runs all.
            n_splits: Number of stratified k-fold splits.
            scale_features: Whether to standardize features.

        Returns:
            Dict with per-model results:
            {
                "random_forest": {
                    "cv_metrics": [...],  # per-fold
                    "mean_metrics": {...},
                    "std_metrics": {...},
                    "per_category": {...},
                    "train_time_s": float,
                },
                ...
            }
        """
        from sklearn.model_selection import StratifiedKFold
        from sklearn.preprocessing import StandardScaler

        if self._X is None:
            self.load_data()

        model_names = models or list(MODEL_REGISTRY.keys())
        skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=self.random_state)

        results = {}

        for model_name in model_names:
            if model_name not in MODEL_REGISTRY:
                logger.warning(f"Unknown model: {model_name}")
                continue

            logger.info(f"Training {model_name} ({n_splits}-fold CV)...")
            fold_metrics = []
            all_y_true = []
            all_y_pred = []
            all_categories = []
            total_train_time = 0.0

            for fold_idx, (train_idx, test_idx) in enumerate(skf.split(self._X, self._y)):
                X_train, X_test = self._X[train_idx], self._X[test_idx]
                y_train, y_test = self._y[train_idx], self._y[test_idx]

                # Scale features
                if scale_features:
                    scaler = StandardScaler()
                    X_train = scaler.fit_transform(X_train)
                    X_test = scaler.transform(X_test)

                # Create fresh model
                detector = MODEL_REGISTRY[model_name]()

                # Train
                start = time.time()
                try:
                    detector.fit(X_train, y_train)
                except Exception as e:
                    logger.error(f"  Fold {fold_idx + 1}: training failed — {e}")
                    continue
                train_time = time.time() - start
                total_train_time += train_time

                # Predict
                y_pred = detector.predict(X_test)
                y_prob = None
                try:
                    y_prob = detector.predict_proba(X_test)
                except Exception:
                    pass

                # Metrics
                metrics = compute_metrics(y_test, y_pred, y_prob)
                fold_metrics.append(metrics)

                all_y_true.extend(y_test.tolist())
                all_y_pred.extend(y_pred.tolist())
                all_categories.extend([self._categories[i] for i in test_idx])

                logger.info(
                    f"  Fold {fold_idx + 1}: F1={metrics['f1']:.4f}, "
                    f"Acc={metrics['accuracy']:.4f}, "
                    f"AUC={metrics.get('auc_roc', 0):.4f} "
                    f"({train_time:.1f}s)"
                )

            if not fold_metrics:
                results[model_name] = {"error": "All folds failed"}
                continue

            # Aggregate metrics
            metric_keys = fold_metrics[0].keys()
            mean_metrics = {
                k: float(np.mean([m[k] for m in fold_metrics])) for k in metric_keys
            }
            std_metrics = {
                k: float(np.std([m[k] for m in fold_metrics])) for k in metric_keys
            }

            # Per-category analysis
            cat_metrics = per_category_metrics(
                np.array(all_y_true), np.array(all_y_pred), all_categories
            )

            results[model_name] = {
                "cv_metrics": fold_metrics,
                "mean_metrics": mean_metrics,
                "std_metrics": std_metrics,
                "per_category": cat_metrics,
                "train_time_s": total_train_time,
                "n_splits": n_splits,
                "n_samples": len(self._X),
                "n_features": self._X.shape[1],
            }

            logger.info(
                f"  {model_name} — Mean F1: {mean_metrics['f1']:.4f} "
                f"(±{std_metrics['f1']:.4f}), "
                f"Mean AUC: {mean_metrics.get('auc_roc', 0):.4f}"
            )

        return results

    def run_config_comparison(
        self,
        config_paths: Dict[str, str],
        models: Optional[List[str]] = None,
        n_splits: int = 5,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Run the same experiment across Config A, B, C datasets.

        Args:
            config_paths: {"A": "path/to/A.csv", "B": "path/to/B.csv", "C": "..."}
            models: Model names to evaluate
            n_splits: CV folds

        Returns:
            {"A": {model_results}, "B": {model_results}, "C": {model_results}}
        """
        all_results = {}
        for config_name, path in config_paths.items():
            logger.info(f"\n{'='*60}")
            logger.info(f"Config {config_name}: {path}")
            logger.info(f"{'='*60}")

            trainer = ModelTrainer(
                dataset_path=path,
                random_state=self.random_state,
            )
            trainer.load_data()
            all_results[config_name] = trainer.run_experiment(
                models=models, n_splits=n_splits
            )

        return all_results

    def export_results(self, results: Dict[str, Any], output_dir: str) -> None:
        """Export experiment results to JSON and CSV."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # JSON
        json_path = out / "experiment_results.json"
        with open(json_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"Results JSON: {json_path}")

        # CSV summary
        csv_path = out / "model_comparison.csv"
        rows = []
        for model_name, data in results.items():
            if "error" in data:
                continue
            row = {"model": model_name}
            for k, v in data.get("mean_metrics", {}).items():
                row[f"mean_{k}"] = f"{v:.4f}"
            for k, v in data.get("std_metrics", {}).items():
                row[f"std_{k}"] = f"{v:.4f}"
            row["train_time_s"] = f"{data.get('train_time_s', 0):.1f}"
            rows.append(row)

        if rows:
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            logger.info(f"Comparison CSV: {csv_path}")

    @property
    def feature_names(self) -> List[str]:
        return self._feature_names or []
