"""
Activity Comparison Pipeline for VESPER.

Compares VESPER-generated daily activity schedules against
real-world smart-home datasets (CASAS, ARAS) to measure
activity realism for conference evaluation.

Supported reference datasets:
- CASAS (WSU): 30+ smart homes, sensor-driven activity labels
- ARAS (Bogazici): 2 homes, 27 activities over 30 days
- Custom: Any dataset conforming to the standard format

Metrics computed:
- Activity type distribution (KL divergence, Wasserstein)
- Activity duration distribution per category
- Room transition probability matrices (Frobenius norm)
- Temporal patterns (start-time correlation)
- Schedule diversity (entropy)
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .metrics import (
    ActivityDistributionMetrics,
    compute_kl_divergence,
    compute_js_divergence,
    compute_schedule_entropy,
    compute_temporal_correlation,
    compute_transition_matrix,
    compute_transition_matrix_distance,
    compute_wasserstein_distance,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Standard Activity Taxonomy
# =============================================================================

# Mapping from VESPER categories to a unified taxonomy
VESPER_TO_UNIFIED = {
    "sleep": "Sleeping",
    "hygiene": "Personal_Hygiene",
    "eating": "Meal_Preparation",
    "work": "Working",
    "exercise": "Exercising",
    "leisure": "Relaxing",
    "social": "Socializing",
    "household": "Housekeeping",
    "errands": "Other",
    "idle": "Idle",
}

# CASAS activity labels → unified taxonomy
CASAS_TO_UNIFIED = {
    "Sleep": "Sleeping",
    "Sleeping": "Sleeping",
    "Bed_to_Toilet": "Personal_Hygiene",
    "Bathing": "Personal_Hygiene",
    "Personal_Hygiene": "Personal_Hygiene",
    "Grooming": "Personal_Hygiene",
    "Meal_Preparation": "Meal_Preparation",
    "Eating": "Meal_Preparation",
    "Relax": "Relaxing",
    "Relaxing": "Relaxing",
    "Watch_TV": "Relaxing",
    "Work": "Working",
    "Working": "Working",
    "Study": "Working",
    "Housekeeping": "Housekeeping",
    "Wash_Dishes": "Housekeeping",
    "Laundry": "Housekeeping",
    "Exercise": "Exercising",
    "Enter_Home": "Other",
    "Leave_Home": "Other",
    "Other_Activity": "Other",
    "Other": "Other",
}

# ARAS activity labels → unified taxonomy
ARAS_TO_UNIFIED = {
    "Sleeping": "Sleeping",
    "Having_Breakfast": "Meal_Preparation",
    "Having_Lunch": "Meal_Preparation",
    "Having_Dinner": "Meal_Preparation",
    "Having_Snack": "Meal_Preparation",
    "Preparing_Breakfast": "Meal_Preparation",
    "Preparing_Lunch": "Meal_Preparation",
    "Preparing_Dinner": "Meal_Preparation",
    "Preparing_Snack": "Meal_Preparation",
    "Washing_Dishes": "Housekeeping",
    "Using_Bathroom": "Personal_Hygiene",
    "Brushing_Teeth": "Personal_Hygiene",
    "Shaving": "Personal_Hygiene",
    "Watching_TV": "Relaxing",
    "Using_Internet": "Relaxing",
    "Reading_Book": "Relaxing",
    "Listening_Music": "Relaxing",
    "Using_Computer": "Working",
    "Working_At_Table": "Working",
    "Talking_On_Phone": "Socializing",
    "Having_Guest": "Socializing",
    "Exercising": "Exercising",
    "Cleaning": "Housekeeping",
    "Doing_Laundry": "Housekeeping",
    "Ironing": "Housekeeping",
    "Going_Out": "Other",
    "Idle": "Idle",
    "Other": "Other",
}

UNIFIED_ACTIVITIES = sorted(set(VESPER_TO_UNIFIED.values()) | {"Idle", "Other"})


# =============================================================================
# Data Loaders
# =============================================================================

@dataclass
class ActivityRecord:
    """A single activity record from any dataset."""
    activity: str  # Unified activity label
    start_time: datetime
    end_time: datetime
    duration_minutes: float
    room: Optional[str] = None
    source_dataset: str = ""
    raw_label: str = ""

    @property
    def hour_of_day(self) -> float:
        return self.start_time.hour + self.start_time.minute / 60.0


@dataclass
class DaySchedule:
    """A full day of activities."""
    date: datetime
    activities: List[ActivityRecord] = field(default_factory=list)
    source: str = ""

    @property
    def activity_sequence(self) -> List[str]:
        return [a.activity for a in self.activities]

    @property
    def room_sequence(self) -> List[str]:
        return [a.room for a in self.activities if a.room]


def load_vesper_schedules(
    db_path: Optional[str] = None,
    json_export_path: Optional[str] = None,
    days: int = 30,
) -> List[DaySchedule]:
    """
    Load VESPER-generated schedules from the task database or JSON export.

    Args:
        db_path: Path to vesper_tasks.db.
        json_export_path: Path to exported JSON file.
        days: Number of days to load.

    Returns:
        List of DaySchedule objects.
    """
    schedules = []

    if json_export_path and Path(json_export_path).exists():
        with open(json_export_path) as f:
            data = json.load(f)

        # Group tasks by date
        by_date: Dict[str, List[Dict]] = defaultdict(list)
        for task in data.get("tasks", []):
            date_key = task.get("scheduled_time", "")[:10]
            if date_key:
                by_date[date_key].append(task)

        for date_str, tasks in sorted(by_date.items()):
            day = DaySchedule(
                date=datetime.fromisoformat(date_str),
                source="vesper",
            )
            for t in tasks:
                category = t.get("category", "idle")
                unified = VESPER_TO_UNIFIED.get(category, "Other")

                start = datetime.fromisoformat(t["scheduled_time"]) if t.get("scheduled_time") else datetime.now()
                duration_s = t.get("actual_duration_seconds") or t.get("duration_seconds", 900)
                end = start + timedelta(seconds=duration_s)

                day.activities.append(ActivityRecord(
                    activity=unified,
                    start_time=start,
                    end_time=end,
                    duration_minutes=duration_s / 60.0,
                    room=t.get("room"),
                    source_dataset="vesper",
                    raw_label=category,
                ))
            schedules.append(day)
        return schedules

    if db_path and Path(db_path).exists():
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE scheduled_time >= ? ORDER BY scheduled_time",
            (cutoff,),
        ).fetchall()
        conn.close()

        by_date: Dict[str, List] = defaultdict(list)
        for row in rows:
            date_key = (row["scheduled_time"] or "")[:10]
            if date_key:
                by_date[date_key].append(dict(row))

        for date_str, tasks in sorted(by_date.items()):
            day = DaySchedule(
                date=datetime.fromisoformat(date_str),
                source="vesper",
            )
            for t in tasks:
                category = t.get("category", "idle")
                unified = VESPER_TO_UNIFIED.get(category, "Other")
                start = datetime.fromisoformat(t["scheduled_time"])
                duration_s = t.get("actual_duration_seconds") or t.get("duration_seconds", 900)
                end = start + timedelta(seconds=duration_s)

                day.activities.append(ActivityRecord(
                    activity=unified,
                    start_time=start,
                    end_time=end,
                    duration_minutes=duration_s / 60.0,
                    room=t.get("room"),
                    source_dataset="vesper",
                    raw_label=category,
                ))
            schedules.append(day)

    return schedules


def load_casas_dataset(data_dir: str) -> List[DaySchedule]:
    """
    Load CASAS smart-home dataset.

    Expected format: Tab-separated files with columns:
    date time sensorID sensorState activity_label

    Args:
        data_dir: Directory containing CASAS data files.

    Returns:
        List of DaySchedule objects.
    """
    schedules = []
    data_path = Path(data_dir)

    # CASAS datasets come in various formats; handle common ones
    for data_file in sorted(data_path.glob("*.txt")) + sorted(data_path.glob("*.csv")):
        current_activity = None
        current_start = None
        current_date = None
        day_activities: List[ActivityRecord] = []

        with open(data_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                parts = line.split()
                if len(parts) < 4:
                    continue

                try:
                    date_str = parts[0]
                    time_str = parts[1]
                    # Some formats have microseconds
                    ts_str = f"{date_str} {time_str}"
                    for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"]:
                        try:
                            timestamp = datetime.strptime(ts_str, fmt)
                            break
                        except ValueError:
                            continue
                    else:
                        continue

                    # Activity label is usually the last column
                    activity_raw = parts[-1]
                    # Remove begin/end markers
                    activity_raw = activity_raw.replace("_begin", "").replace("_end", "")

                    unified = CASAS_TO_UNIFIED.get(activity_raw, "Other")

                    # Track activity changes
                    if unified != current_activity:
                        if current_activity and current_start:
                            duration = (timestamp - current_start).total_seconds() / 60.0
                            if duration > 0:
                                day_activities.append(ActivityRecord(
                                    activity=current_activity,
                                    start_time=current_start,
                                    end_time=timestamp,
                                    duration_minutes=duration,
                                    source_dataset="casas",
                                    raw_label=activity_raw,
                                ))

                        current_activity = unified
                        current_start = timestamp

                    # Track date changes
                    day = timestamp.date()
                    if current_date and day != current_date:
                        if day_activities:
                            schedules.append(DaySchedule(
                                date=datetime.combine(current_date, datetime.min.time()),
                                activities=list(day_activities),
                                source="casas",
                            ))
                            day_activities.clear()
                    current_date = day

                except (ValueError, IndexError):
                    continue

        # Final day
        if day_activities:
            schedules.append(DaySchedule(
                date=datetime.combine(current_date, datetime.min.time()),
                activities=day_activities,
                source="casas",
            ))

    logger.info(f"Loaded {len(schedules)} days from CASAS dataset at {data_dir}")
    return schedules


def load_aras_dataset(data_dir: str) -> List[DaySchedule]:
    """
    Load ARAS smart-home dataset.

    ARAS format: Each day is a separate file with per-second activity labels.
    Columns: sensor1...sensor20 activity_resident1 activity_resident2

    Args:
        data_dir: Directory containing ARAS data files (Day1.txt ... Day30.txt).

    Returns:
        List of DaySchedule objects.
    """
    # ARAS activity ID to label mapping
    aras_id_to_label = {
        1: "Other", 2: "Going_Out", 3: "Preparing_Breakfast",
        4: "Having_Breakfast", 5: "Preparing_Lunch", 6: "Having_Lunch",
        7: "Preparing_Dinner", 8: "Having_Dinner", 9: "Washing_Dishes",
        10: "Having_Snack", 11: "Sleeping", 12: "Watching_TV",
        13: "Studying", 14: "Having_Shower", 15: "Using_Bathroom",
        16: "Using_Internet", 17: "Reading_Book", 18: "Doing_Laundry",
        19: "Shaving", 20: "Brushing_Teeth", 21: "Talking_On_Phone",
        22: "Listening_Music", 23: "Cleaning", 24: "Having_Conversation",
        25: "Having_Guest", 26: "Changing_Clothes", 27: "Preparing_Snack",
    }

    schedules = []
    data_path = Path(data_dir)

    for day_file in sorted(data_path.glob("Day*.txt")):
        day_num_match = re.search(r"Day(\d+)", day_file.name)
        if not day_num_match:
            continue
        day_num = int(day_num_match.group(1))

        # Create a reference date (ARAS: 30 consecutive days)
        base_date = datetime(2025, 1, 1) + timedelta(days=day_num - 1)

        current_activity = None
        current_start = None
        day_activities: List[ActivityRecord] = []

        with open(day_file) as f:
            for second_idx, line in enumerate(f):
                parts = line.strip().split()
                if len(parts) < 22:  # 20 sensors + 2 activity labels
                    continue

                try:
                    activity_id = int(parts[20])  # Resident 1
                    raw_label = aras_id_to_label.get(activity_id, "Other")
                    unified = ARAS_TO_UNIFIED.get(raw_label, "Other")

                    timestamp = base_date + timedelta(seconds=second_idx)

                    if unified != current_activity:
                        if current_activity and current_start:
                            duration = (timestamp - current_start).total_seconds() / 60.0
                            if duration > 0.5:  # Skip very short segments
                                day_activities.append(ActivityRecord(
                                    activity=current_activity,
                                    start_time=current_start,
                                    end_time=timestamp,
                                    duration_minutes=duration,
                                    source_dataset="aras",
                                    raw_label=raw_label,
                                ))
                        current_activity = unified
                        current_start = timestamp

                except (ValueError, IndexError):
                    continue

        if day_activities:
            schedules.append(DaySchedule(
                date=base_date,
                activities=day_activities,
                source="aras",
            ))

    logger.info(f"Loaded {len(schedules)} days from ARAS dataset at {data_dir}")
    return schedules


def load_custom_dataset(
    json_path: str,
    label_mapping: Optional[Dict[str, str]] = None,
) -> List[DaySchedule]:
    """
    Load a custom dataset in VESPER's standard JSON format.

    Expected format:
    {
        "schedules": [
            {
                "date": "2025-01-01",
                "activities": [
                    {"activity": "Sleeping", "start": "00:00", "end": "07:00", "room": "bedroom"},
                    ...
                ]
            }
        ]
    }
    """
    with open(json_path) as f:
        data = json.load(f)

    mapping = label_mapping or {}
    schedules = []

    for sched in data.get("schedules", []):
        date = datetime.fromisoformat(sched["date"])
        day = DaySchedule(date=date, source="custom")

        for act in sched.get("activities", []):
            label = act["activity"]
            unified = mapping.get(label, label)

            start_parts = act["start"].split(":")
            start = date.replace(
                hour=int(start_parts[0]),
                minute=int(start_parts[1]) if len(start_parts) > 1 else 0,
            )
            end_parts = act["end"].split(":")
            end = date.replace(
                hour=int(end_parts[0]),
                minute=int(end_parts[1]) if len(end_parts) > 1 else 0,
            )
            if end <= start:
                end += timedelta(days=1)

            duration = (end - start).total_seconds() / 60.0

            day.activities.append(ActivityRecord(
                activity=unified,
                start_time=start,
                end_time=end,
                duration_minutes=duration,
                room=act.get("room"),
                source_dataset="custom",
                raw_label=label,
            ))

        schedules.append(day)

    logger.info(f"Loaded {len(schedules)} days from custom dataset at {json_path}")
    return schedules


# =============================================================================
# Comparison Engine
# =============================================================================

class ActivityComparisonPipeline:
    """
    Compares VESPER-generated activities against reference datasets.

    Produces all metrics needed for conference evaluation:
    - Distribution similarity (KL, JS, Wasserstein)
    - Transition matrix distance
    - Temporal correlation
    - Duration analysis
    - Schedule diversity
    """

    def __init__(
        self,
        vesper_schedules: List[DaySchedule],
        reference_schedules: List[DaySchedule],
        reference_name: str = "reference",
    ):
        self.vesper = vesper_schedules
        self.reference = reference_schedules
        self.reference_name = reference_name

        # Compute unified activity set
        all_activities = set()
        for s in self.vesper + self.reference:
            for a in s.activities:
                all_activities.add(a.activity)
        self.activity_labels = sorted(all_activities)

        logger.info(
            f"Comparison: {len(self.vesper)} VESPER days vs "
            f"{len(self.reference)} {reference_name} days, "
            f"{len(self.activity_labels)} activity types"
        )

    def _count_activities(self, schedules: List[DaySchedule]) -> Dict[str, int]:
        """Count activity occurrences."""
        counts = Counter()
        for s in schedules:
            for a in s.activities:
                counts[a.activity] += 1
        return dict(counts)

    def _distribution_vector(self, counts: Dict[str, int]) -> np.ndarray:
        """Convert counts dict to aligned probability vector."""
        vec = np.array([counts.get(a, 0) for a in self.activity_labels], dtype=np.float64)
        total = vec.sum()
        return vec / total if total > 0 else vec

    def _duration_stats(self, schedules: List[DaySchedule]) -> Dict[str, Dict[str, float]]:
        """Compute duration statistics per activity type."""
        durations: Dict[str, List[float]] = defaultdict(list)
        for s in schedules:
            for a in s.activities:
                durations[a.activity].append(a.duration_minutes)

        stats = {}
        for activity, durs in durations.items():
            arr = np.array(durs)
            stats[activity] = {
                "mean_min": float(arr.mean()),
                "std_min": float(arr.std()),
                "median_min": float(np.median(arr)),
                "min_min": float(arr.min()),
                "max_min": float(arr.max()),
                "count": len(durs),
            }
        return stats

    def _start_time_distributions(
        self, schedules: List[DaySchedule]
    ) -> Dict[str, List[float]]:
        """Extract start time distributions (hour of day) per activity."""
        times: Dict[str, List[float]] = defaultdict(list)
        for s in schedules:
            for a in s.activities:
                times[a.activity].append(a.hour_of_day)
        return dict(times)

    def _room_sequences(self, schedules: List[DaySchedule]) -> List[List[str]]:
        """Extract room transition sequences."""
        sequences = []
        for s in schedules:
            seq = [a.room for a in s.activities if a.room]
            if len(seq) > 1:
                sequences.append(seq)
        return sequences

    def compare(self) -> ActivityDistributionMetrics:
        """
        Run full comparison pipeline.

        Returns:
            ActivityDistributionMetrics with all computed metrics.
        """
        # Activity type distributions
        v_counts = self._count_activities(self.vesper)
        r_counts = self._count_activities(self.reference)
        v_dist = self._distribution_vector(v_counts)
        r_dist = self._distribution_vector(r_counts)

        # Distribution divergences
        kl = compute_kl_divergence(r_dist, v_dist)
        js = compute_js_divergence(r_dist, v_dist)
        wass = compute_wasserstein_distance(r_dist, v_dist)

        # Transition matrices
        v_seqs = [s.activity_sequence for s in self.vesper if len(s.activity_sequence) > 1]
        r_seqs = [s.activity_sequence for s in self.reference if len(s.activity_sequence) > 1]

        v_trans, states = compute_transition_matrix(v_seqs, self.activity_labels)
        r_trans, _ = compute_transition_matrix(r_seqs, self.activity_labels)
        trans_dist = compute_transition_matrix_distance(v_trans, r_trans)

        # Temporal correlation
        v_times = self._start_time_distributions(self.vesper)
        r_times = self._start_time_distributions(self.reference)
        temp_corr = compute_temporal_correlation(v_times, r_times)

        # Schedule diversity
        v_act_seqs = [s.activity_sequence for s in self.vesper]
        entropy = compute_schedule_entropy(v_act_seqs)

        # Duration stats
        v_dur = self._duration_stats(self.vesper)
        r_dur = self._duration_stats(self.reference)

        metrics = ActivityDistributionMetrics(
            kl_divergence=kl,
            js_divergence=js,
            wasserstein_distance=wass,
            transition_matrix_distance=trans_dist,
            temporal_correlation=temp_corr,
            schedule_entropy=entropy,
            activity_counts_vesper=v_counts,
            activity_counts_reference=r_counts,
            duration_stats_vesper=v_dur,
            duration_stats_reference=r_dur,
            reference_dataset=self.reference_name,
            num_vesper_schedules=len(self.vesper),
            num_reference_schedules=len(self.reference),
        )

        logger.info(
            f"Comparison results: KL={kl:.4f}, JS={js:.4f}, "
            f"Wasserstein={wass:.4f}, TransMatrix={trans_dist:.4f}, "
            f"Entropy={entropy:.4f}"
        )

        return metrics

    def export(self, output_path: str):
        """Run comparison and export results to JSON."""
        metrics = self.compare()
        data = {
            "comparison": metrics.to_dict(),
            "activity_labels": self.activity_labels,
            "timestamp": datetime.now().isoformat(),
        }
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Exported comparison to {output_path}")
        return metrics


# =============================================================================
# Sim2Real Transfer Evaluation
# =============================================================================

class Sim2RealEvaluator:
    """
    Evaluate Sim2Real transfer: train an activity classifier on
    VESPER-generated data, test on real sensor data.

    Uses simple feature extraction + sklearn classifier.
    """

    def __init__(self):
        self._results: Dict[str, Any] = {}

    def prepare_features(
        self,
        schedules: List[DaySchedule],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Extract features from schedules for classification.

        Features per activity:
        - Hour of day (normalized)
        - Duration (normalized)
        - Previous activity (one-hot)
        - Next activity (one-hot)
        """
        features = []
        labels = []

        for sched in schedules:
            for i, act in enumerate(sched.activities):
                feat = [
                    act.hour_of_day / 24.0,
                    min(act.duration_minutes / 480.0, 1.0),  # Cap at 8h
                ]
                features.append(feat)
                labels.append(act.activity)

        return np.array(features), np.array(labels)

    def evaluate(
        self,
        train_schedules: List[DaySchedule],
        test_schedules: List[DaySchedule],
        n_folds: int = 5,
    ) -> Dict[str, Any]:
        """
        Run Sim2Real evaluation with cross-validation.

        Args:
            train_schedules: VESPER-generated schedules for training.
            test_schedules: Real-world schedules for testing.
            n_folds: Number of cross-validation folds.

        Returns:
            Dict with F1, accuracy, confusion matrix.
        """
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.metrics import (
                accuracy_score,
                classification_report,
                f1_score,
            )
            from sklearn.model_selection import cross_val_score
            from sklearn.preprocessing import LabelEncoder
        except ImportError:
            logger.warning("sklearn not available for Sim2Real evaluation")
            return {"error": "sklearn not installed"}

        X_train, y_train = self.prepare_features(train_schedules)
        X_test, y_test = self.prepare_features(test_schedules)

        if len(X_train) == 0 or len(X_test) == 0:
            return {"error": "Insufficient data"}

        # Encode labels
        le = LabelEncoder()
        all_labels = np.concatenate([y_train, y_test])
        le.fit(all_labels)
        y_train_enc = le.transform(y_train)
        y_test_enc = le.transform(y_test)

        # Train classifier
        clf = RandomForestClassifier(n_estimators=100, random_state=42)
        clf.fit(X_train, y_train_enc)

        # Predict
        y_pred = clf.predict(X_test)

        # Metrics
        accuracy = accuracy_score(y_test_enc, y_pred)
        f1_macro = f1_score(y_test_enc, y_pred, average="macro", zero_division=0)
        f1_weighted = f1_score(y_test_enc, y_pred, average="weighted", zero_division=0)

        report = classification_report(
            y_test_enc, y_pred,
            target_names=le.classes_,
            output_dict=True,
            zero_division=0,
        )

        # Cross-val on combined
        X_all = np.vstack([X_train, X_test])
        y_all = np.concatenate([y_train_enc, y_test_enc])
        cv_scores = cross_val_score(clf, X_all, y_all, cv=min(n_folds, len(set(y_all))))

        self._results = {
            "accuracy": float(accuracy),
            "f1_macro": float(f1_macro),
            "f1_weighted": float(f1_weighted),
            "cross_val_mean": float(cv_scores.mean()),
            "cross_val_std": float(cv_scores.std()),
            "classification_report": report,
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "num_classes": len(le.classes_),
            "classes": list(le.classes_),
        }

        logger.info(
            f"Sim2Real results: acc={accuracy:.3f}, "
            f"F1(macro)={f1_macro:.3f}, F1(weighted)={f1_weighted:.3f}"
        )

        return self._results

    def export(self, output_path: str):
        """Export Sim2Real results to JSON."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(self._results, f, indent=2, default=str)
