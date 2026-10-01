#!/usr/bin/env python3
"""
Collect REAL experiment data for all Research Questions (RQ1–RQ5).

Uses:
  - VESPER autonomous eval results (30 scenes × 5 days)
  - CASAS sensor data (Aruba, Milan, Cairo) — room-based CSVs
  - ARAS activity data (House A, House B) — per-second labeled files
  - Event bus & DB latency benchmarks (measured live)
  - Device scaling benchmarks (measured live)
  - LLM ablation via LMStudio (if available)
  - Sim2Real Random Forest classifier

Outputs JSON results to results/rq_data/ for each RQ.
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_ROOT / "logs" / "collect_real_data.log"),
    ],
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = PROJECT_ROOT / "results" / "rq_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# Utility: Statistical functions
# =============================================================================

def kl_divergence(p, q, eps=1e-10):
    p = np.asarray(p, dtype=np.float64) + eps
    q = np.asarray(q, dtype=np.float64) + eps
    p = p / p.sum()
    q = q / q.sum()
    return float(np.sum(p * np.log(p / q)))


def js_divergence(p, q, eps=1e-10):
    p = np.asarray(p, dtype=np.float64) + eps
    q = np.asarray(q, dtype=np.float64) + eps
    p = p / p.sum()
    q = q / q.sum()
    m = 0.5 * (p + q)
    return float(0.5 * kl_divergence(p, m, 0) + 0.5 * kl_divergence(q, m, 0))


def wasserstein_1d(p, q):
    p = np.asarray(p, dtype=np.float64)
    q = np.asarray(q, dtype=np.float64)
    p = p / p.sum() if p.sum() > 0 else p
    q = q / q.sum() if q.sum() > 0 else q
    return float(np.sum(np.abs(np.cumsum(p) - np.cumsum(q))))


def schedule_entropy(categories_list):
    """Compute entropy of activity-category distribution."""
    entropies = []
    for cats in categories_list:
        if not cats:
            continue
        counts = Counter(cats)
        total = sum(counts.values())
        probs = np.array([c / total for c in counts.values()])
        ent = -np.sum(probs * np.log2(probs + 1e-10))
        entropies.append(ent)
    return float(np.mean(entropies)) if entropies else 0.0


def confidence_interval_95(data):
    """Return (mean, ci_lower, ci_upper) at 95% confidence."""
    from scipy import stats
    a = np.asarray(data, dtype=np.float64)
    n = len(a)
    if n < 2:
        m = float(a.mean()) if n > 0 else 0.0
        return m, m, m
    m = float(a.mean())
    se = float(stats.sem(a))
    t_val = stats.t.ppf(0.975, n - 1)
    margin = t_val * se
    return m, m - margin, m + margin


def cohens_d(g1, g2):
    g1 = np.asarray(g1, dtype=np.float64)
    g2 = np.asarray(g2, dtype=np.float64)
    n1, n2 = len(g1), len(g2)
    if n1 < 2 or n2 < 2:
        return 0.0
    pooled_var = ((n1 - 1) * g1.var(ddof=1) + (n2 - 1) * g2.var(ddof=1)) / (n1 + n2 - 2)
    pooled_std = np.sqrt(pooled_var)
    if pooled_std == 0:
        return 0.0
    return float((g1.mean() - g2.mean()) / pooled_std)


def transition_matrix(sequences, states):
    idx = {s: i for i, s in enumerate(states)}
    n = len(states)
    counts = np.zeros((n, n), dtype=np.float64)
    for seq in sequences:
        for i in range(len(seq) - 1):
            if seq[i] in idx and seq[i + 1] in idx:
                counts[idx[seq[i]]][idx[seq[i + 1]]] += 1
    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1
    return counts / row_sums


# =============================================================================
# Unified Activity Taxonomy
# =============================================================================

UNIFIED_CATEGORIES = [
    "Sleeping", "Personal_Hygiene", "Meal_Preparation", "Working",
    "Exercising", "Relaxing", "Socializing", "Housekeeping", "Other",
]

# VESPER task name → unified category (keyword-based)
def classify_vesper_task(task_name: str) -> str:
    name = task_name.lower()
    if any(w in name for w in ["sleep", "bed", "nap", "wake up", "wind down"]):
        return "Sleeping"
    if any(w in name for w in ["hygiene", "shower", "bath", "brush", "groom", "wash up"]):
        return "Personal_Hygiene"
    if any(w in name for w in ["breakfast", "lunch", "dinner", "cook", "meal", "eat", "snack", "prep", "food"]):
        return "Meal_Preparation"
    if any(w in name for w in ["work", "study", "office", "email", "meeting", "report"]):
        return "Working"
    if any(w in name for w in ["exercise", "yoga", "gym", "run", "walk", "stretch", "fitness"]):
        return "Exercising"
    if any(w in name for w in ["relax", "leisure", "read", "tv", "watch", "music", "game", "hobby", "meditat"]):
        return "Relaxing"
    if any(w in name for w in ["social", "call", "visit", "chat", "friend", "family", "phone", "guest"]):
        return "Socializing"
    if any(w in name for w in ["chore", "clean", "laundry", "dishes", "tidy", "vacuum", "iron", "house"]):
        return "Housekeeping"
    return "Other"


# CASAS room → unified (sensor-based, no activity labels in this version)
CASAS_ROOM_TO_UNIFIED = {
    "bedroom": "Sleeping",
    "bathroom": "Personal_Hygiene",
    "kitchen": "Meal_Preparation",
    "livingroom": "Relaxing",
    "living room": "Relaxing",
    "office": "Working",
    "study": "Working",
    "garage": "Other",
    "hallway": "Other",
    "entry": "Other",
    "outdoor": "Other",
    "outside": "Other",
    "dining": "Meal_Preparation",
    "laundry": "Housekeeping",
    "utility": "Housekeeping",
    "porch": "Other",
    "closet": "Other",
    "pantry": "Meal_Preparation",
}

# ARAS activity ID → unified
ARAS_ID_TO_UNIFIED = {
    1: "Other",           # Other
    2: "Other",           # Going Out
    3: "Meal_Preparation",  # Preparing Breakfast
    4: "Meal_Preparation",  # Having Breakfast
    5: "Meal_Preparation",  # Preparing Lunch
    6: "Meal_Preparation",  # Having Lunch
    7: "Meal_Preparation",  # Preparing Dinner
    8: "Meal_Preparation",  # Having Dinner
    9: "Housekeeping",    # Washing Dishes
    10: "Meal_Preparation", # Having Snack
    11: "Sleeping",       # Sleeping
    12: "Relaxing",       # Watching TV
    13: "Working",        # Studying
    14: "Personal_Hygiene", # Having Shower
    15: "Personal_Hygiene", # Toileting
    16: "Sleeping",       # Napping
    17: "Relaxing",       # Using Internet
    18: "Relaxing",       # Reading Book
    19: "Housekeeping",   # Laundry
    20: "Personal_Hygiene", # Shaving
    21: "Personal_Hygiene", # Brushing Teeth
    22: "Socializing",    # Talking on the Phone
    23: "Relaxing",       # Listening to Music
    24: "Housekeeping",   # Cleaning
    25: "Socializing",    # Having Conversation
    26: "Socializing",    # Having Guest
    27: "Other",          # Changing Clothes
}


# =============================================================================
# Data Loaders
# =============================================================================

def load_vesper_schedules() -> Dict[str, Any]:
    """Load VESPER eval results and extract per-day activity distributions."""
    eval_path = PROJECT_ROOT / "results" / "vesper_autonomous_eval" / "eval_results.json"
    with open(eval_path) as f:
        data = json.load(f)

    all_categories = []  # list of lists (per-day)
    all_tasks = []
    hourly_bins = np.zeros(24)
    per_scene_cats = {}

    for scene in data:
        scene_id = scene["scene_id"]
        scene_cats = []
        for trial in scene["nav_trials"]:
            task_name = trial["task_name"]
            cat = classify_vesper_task(task_name)
            scene_cats.append(cat)
            all_tasks.append({
                "name": task_name,
                "category": cat,
                "room": trial.get("target_room", ""),
                "source_room": trial.get("source_room", ""),
                "success": trial.get("success", False),
            })
        per_scene_cats[scene_id] = scene_cats

    # Group into pseudo-days (each scene had 5 simulated days, ~15 tasks/day)
    day_categories = []
    for scene_id, cats in per_scene_cats.items():
        # Split into chunks of ~15 (approximate day boundaries)
        chunk_size = max(1, len(cats) // 5)
        for i in range(0, len(cats), chunk_size):
            chunk = cats[i:i + chunk_size]
            if chunk:
                day_categories.append(chunk)
                all_categories.extend(chunk)

    # Count distribution
    cat_counts = Counter(all_categories)

    logger.info(f"VESPER: {len(all_tasks)} tasks, {len(day_categories)} pseudo-days, "
                f"{len(cat_counts)} categories")

    return {
        "cat_counts": dict(cat_counts),
        "day_categories": day_categories,
        "all_tasks": all_tasks,
        "total_tasks": len(all_tasks),
        "num_days": len(day_categories),
    }


def load_casas_data() -> Dict[str, Any]:
    """Load CASAS CSV sensor data and derive activity distributions from rooms."""
    casas_dir = PROJECT_ROOT / "data" / "datasets" / "casas" / "data"
    results = {}

    for home in ["aruba", "milan", "cairo"]:
        csv_path = casas_dir / f"{home}.csv"
        if not csv_path.exists():
            logger.warning(f"CASAS {home} not found at {csv_path}")
            continue

        logger.info(f"Loading CASAS/{home} from {csv_path} ...")
        categories = []
        day_categories = []
        current_date = None
        current_day_cats = []

        with open(csv_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) < 4:
                    continue

                date_str = parts[0]
                time_str = parts[1]
                room = parts[2].strip().lower()
                state = parts[3].strip().upper()

                # Only count ON events (activity start)
                if state != "ON":
                    continue

                cat = CASAS_ROOM_TO_UNIFIED.get(room, "Other")
                categories.append(cat)

                # Track per-day
                if date_str != current_date:
                    if current_day_cats:
                        day_categories.append(list(current_day_cats))
                    current_day_cats = []
                    current_date = date_str
                current_day_cats.append(cat)

        if current_day_cats:
            day_categories.append(current_day_cats)

        cat_counts = Counter(categories)
        results[home] = {
            "cat_counts": dict(cat_counts),
            "day_categories": day_categories,
            "total_events": len(categories),
            "num_days": len(day_categories),
        }
        logger.info(f"  CASAS/{home}: {len(categories)} ON events, "
                     f"{len(day_categories)} days")

    return results


def load_aras_data() -> Dict[str, Any]:
    """Load ARAS per-second activity data."""
    aras_base = PROJECT_ROOT / "data" / "datasets" / "aras" / "Aras"
    results = {}

    for house in ["House A", "House B"]:
        house_dir = aras_base / house
        if not house_dir.exists():
            logger.warning(f"ARAS {house} not found at {house_dir}")
            continue

        logger.info(f"Loading ARAS/{house} ...")
        all_categories = []
        day_categories = []

        for day_file in sorted(house_dir.glob("DAY_*.txt")):
            day_cats = []
            prev_activity = None

            with open(day_file) as f:
                for line_idx, line in enumerate(f):
                    parts = line.strip().split()
                    if len(parts) < 22:
                        continue
                    try:
                        activity_id = int(parts[20])  # Resident 1
                        cat = ARAS_ID_TO_UNIFIED.get(activity_id, "Other")

                        # Only record transitions (not every second)
                        if cat != prev_activity:
                            day_cats.append(cat)
                            all_categories.append(cat)
                            prev_activity = cat
                    except (ValueError, IndexError):
                        continue

            if day_cats:
                day_categories.append(day_cats)

        cat_counts = Counter(all_categories)
        house_key = house.lower().replace(" ", "_")
        results[house_key] = {
            "cat_counts": dict(cat_counts),
            "day_categories": day_categories,
            "total_transitions": len(all_categories),
            "num_days": len(day_categories),
        }
        logger.info(f"  ARAS/{house}: {len(all_categories)} transitions, "
                     f"{len(day_categories)} days")

    return results


# =============================================================================
# RQ1: Activity Realism
# =============================================================================

def run_rq1_activity_comparison():
    """Compare VESPER activity distributions against CASAS and ARAS."""
    logger.info("=" * 60)
    logger.info("RQ1: Activity Realism Comparison")
    logger.info("=" * 60)

    vesper = load_vesper_schedules()
    casas = load_casas_data()
    aras = load_aras_data()

    # Build VESPER distribution vector
    v_counts = vesper["cat_counts"]
    v_vec = np.array([v_counts.get(c, 0) for c in UNIFIED_CATEGORIES], dtype=np.float64)

    results = {"vesper": vesper["cat_counts"], "comparisons": {}}

    # Compare against each reference dataset
    all_js = []
    all_kl = []
    all_wass = []

    for dataset_name, dataset_dict in [("casas", casas), ("aras", aras)]:
        for home_name, home_data in dataset_dict.items():
            ref_counts = home_data["cat_counts"]
            r_vec = np.array([ref_counts.get(c, 0) for c in UNIFIED_CATEGORIES], dtype=np.float64)

            kl = kl_divergence(r_vec, v_vec)
            js = js_divergence(r_vec, v_vec)
            wass = wasserstein_1d(r_vec, v_vec)

            # Transition matrix distance
            v_seqs = vesper["day_categories"]
            r_seqs = home_data["day_categories"]
            v_trans = transition_matrix(v_seqs, UNIFIED_CATEGORIES)
            r_trans = transition_matrix(r_seqs, UNIFIED_CATEGORIES)
            trans_dist = float(np.linalg.norm(v_trans - r_trans, ord="fro"))

            # Schedule entropy
            v_entropy = schedule_entropy(vesper["day_categories"])
            r_entropy = schedule_entropy(home_data["day_categories"])

            # Temporal correlation (using distribution vectors as proxy)
            if v_vec.std() > 0 and r_vec.std() > 0:
                temp_corr = float(np.corrcoef(v_vec, r_vec)[0, 1])
            else:
                temp_corr = 0.0

            comp_key = f"{dataset_name}/{home_name}"
            results["comparisons"][comp_key] = {
                "reference_counts": ref_counts,
                "kl_divergence": round(kl, 4),
                "js_divergence": round(js, 4),
                "wasserstein": round(wass, 4),
                "transition_matrix_dist": round(trans_dist, 4),
                "temporal_correlation": round(temp_corr, 4),
                "vesper_entropy": round(v_entropy, 4),
                "reference_entropy": round(r_entropy, 4),
                "vesper_days": len(v_seqs),
                "reference_days": len(r_seqs),
            }

            all_js.append(js)
            all_kl.append(kl)
            all_wass.append(wass)

            logger.info(f"  {comp_key}: JS={js:.4f}, KL={kl:.4f}, "
                        f"Wass={wass:.4f}, TransDist={trans_dist:.4f}, "
                        f"TempCorr={temp_corr:.4f}")

    # Aggregate statistics with 95% CI
    js_mean, js_lo, js_hi = confidence_interval_95(all_js)
    kl_mean, kl_lo, kl_hi = confidence_interval_95(all_kl)
    wass_mean, wass_lo, wass_hi = confidence_interval_95(all_wass)

    results["aggregate"] = {
        "mean_js": round(js_mean, 4),
        "js_ci_95": [round(js_lo, 4), round(js_hi, 4)],
        "mean_kl": round(kl_mean, 4),
        "kl_ci_95": [round(kl_lo, 4), round(kl_hi, 4)],
        "mean_wasserstein": round(wass_mean, 4),
        "wass_ci_95": [round(wass_lo, 4), round(wass_hi, 4)],
        "num_comparisons": len(all_js),
    }

    logger.info(f"\n  AGGREGATE: JS={js_mean:.4f} [{js_lo:.4f}, {js_hi:.4f}], "
                f"KL={kl_mean:.4f}, Wass={wass_mean:.4f}")

    # Save
    out_path = OUTPUT_DIR / "rq1_activity_realism.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"  Saved to {out_path}")

    return results


# =============================================================================
# RQ2: LLM Ablation (check LMStudio availability)
# =============================================================================

async def run_rq2_llm_ablation():
    """Run LLM ablation study if LMStudio is available."""
    logger.info("=" * 60)
    logger.info("RQ2: LLM Ablation Study")
    logger.info("=" * 60)

    import httpx

    lmstudio_url = "http://localhost:1234/v1/chat/completions"
    models_url = "http://localhost:1234/v1/models"

    # Check if LMStudio is running
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(models_url)
            if resp.status_code != 200:
                logger.warning("LMStudio not responding. Skipping RQ2.")
                return None
            models_data = resp.json()
            available_models = [m["id"] for m in models_data.get("data", [])]
            logger.info(f"LMStudio available. Models: {available_models}")
    except Exception as e:
        logger.warning(f"LMStudio not available ({e}). Skipping RQ2.")
        return None

    # Persona library (subset for speed)
    PERSONAS = [
        {"name": "Alex", "age": 32, "occupation": "Software Engineer",
         "wake_time": "07:00", "sleep_time": "23:30", "works_from_home": True,
         "exercise_frequency": 0.7, "social_frequency": 0.4},
        {"name": "Maria", "age": 68, "occupation": "Retired Teacher",
         "wake_time": "06:00", "sleep_time": "21:30", "works_from_home": False,
         "exercise_frequency": 0.4, "social_frequency": 0.6},
        {"name": "James", "age": 45, "occupation": "Accountant",
         "wake_time": "06:30", "sleep_time": "22:00", "works_from_home": False,
         "exercise_frequency": 0.3, "social_frequency": 0.3},
        {"name": "Priya", "age": 28, "occupation": "Graduate Student",
         "wake_time": "08:00", "sleep_time": "00:30", "works_from_home": True,
         "exercise_frequency": 0.5, "social_frequency": 0.5},
        {"name": "Robert", "age": 55, "occupation": "Retired",
         "wake_time": "05:30", "sleep_time": "21:00", "works_from_home": False,
         "exercise_frequency": 0.6, "social_frequency": 0.5},
    ]

    PROMPT_TEMPLATE = """Generate a realistic daily schedule for this person as a JSON array.

Person: {name}, age {age}, {occupation}
Wake time: {wake_time}, Sleep time: {sleep_time}
Works from home: {works_from_home}
Day type: {day_type}

Each task should have: "name", "category" (one of: sleep, hygiene, eating, work, exercise, leisure, social, household, errands), "room", "start_time" (HH:MM), "duration_minutes" (integer).

Respond with ONLY the JSON array, no other text."""

    results = {"models": {}, "pairwise": {}}
    model_entropies = defaultdict(list)

    # Use the currently loaded model (or iterate available ones)
    models_to_test = available_models[:6]  # Up to 6 models
    if not models_to_test:
        logger.warning("No models loaded in LMStudio")
        return None

    seeds = list(range(42, 52))  # 10 seeds per config

    for model in models_to_test:
        logger.info(f"  Testing model: {model}")
        model_results = []
        latencies = []
        errors = 0

        for persona in PERSONAS:
            for seed_idx, seed in enumerate(seeds):
                day_type = "weekday" if seed_idx % 7 < 5 else "weekend"
                exercise_desc = "Often" if persona["exercise_frequency"] > 0.5 else "Sometimes"
                prompt = PROMPT_TEMPLATE.format(
                    name=persona["name"],
                    age=persona["age"],
                    occupation=persona["occupation"],
                    wake_time=persona["wake_time"],
                    sleep_time=persona["sleep_time"],
                    works_from_home=persona["works_from_home"],
                    day_type=day_type,
                )

                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 2048,
                    "seed": seed,
                }

                start = time.perf_counter()
                try:
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        resp = await client.post(lmstudio_url, json=payload)
                        latency = time.perf_counter() - start
                        latencies.append(latency)

                        if resp.status_code != 200:
                            errors += 1
                            continue

                        content = resp.json()["choices"][0]["message"]["content"]
                        # Parse JSON
                        content = content.strip()
                        if content.startswith("```json"):
                            content = content[7:]
                        if content.startswith("```"):
                            content = content[3:]
                        if content.endswith("```"):
                            content = content[:-3]
                        # Try to find JSON array
                        content = content.strip()
                        if not content.startswith("["):
                            # Try to find array in text
                            match = re.search(r'\[.*\]', content, re.DOTALL)
                            if match:
                                content = match.group(0)

                        schedule = json.loads(content)
                        if isinstance(schedule, list) and len(schedule) > 0:
                            cats = [t.get("category", "idle") for t in schedule]
                            model_entropies[model].append(
                                schedule_entropy([cats])
                            )
                            model_results.append({
                                "persona": persona["name"],
                                "seed": seed,
                                "day_type": day_type,
                                "num_tasks": len(schedule),
                                "categories": cats,
                                "unique_categories": len(set(cats)),
                                "latency_s": latency,
                            })
                        else:
                            errors += 1

                except json.JSONDecodeError:
                    errors += 1
                    latencies.append(time.perf_counter() - start)
                except Exception as e:
                    errors += 1
                    latencies.append(time.perf_counter() - start)
                    logger.debug(f"    Error: {e}")

        total_attempts = len(PERSONAS) * len(seeds)
        success_rate = (total_attempts - errors) / total_attempts if total_attempts > 0 else 0

        avg_entropy = float(np.mean(model_entropies[model])) if model_entropies[model] else 0
        ent_mean, ent_lo, ent_hi = confidence_interval_95(model_entropies[model]) if len(model_entropies[model]) > 1 else (avg_entropy, avg_entropy, avg_entropy)
        lat_mean, lat_lo, lat_hi = confidence_interval_95(latencies) if len(latencies) > 1 else (0, 0, 0)

        results["models"][model] = {
            "total_attempts": total_attempts,
            "successes": total_attempts - errors,
            "error_rate": round(errors / total_attempts, 4) if total_attempts > 0 else 1.0,
            "avg_entropy": round(ent_mean, 4),
            "entropy_ci_95": [round(ent_lo, 4), round(ent_hi, 4)],
            "avg_latency_s": round(lat_mean, 2),
            "latency_ci_95": [round(lat_lo, 2), round(lat_hi, 2)],
            "avg_tasks_per_schedule": round(
                float(np.mean([r["num_tasks"] for r in model_results])), 1
            ) if model_results else 0,
            "avg_unique_categories": round(
                float(np.mean([r["unique_categories"] for r in model_results])), 1
            ) if model_results else 0,
            "context_sensitivity": 0.0,  # Computed below
        }

        # Context sensitivity: weekday vs weekend distribution difference
        wd_cats = Counter()
        we_cats = Counter()
        for r in model_results:
            valid_cats = [c for c in r["categories"] if c is not None]
            if r["day_type"] == "weekday":
                wd_cats.update(valid_cats)
            else:
                we_cats.update(valid_cats)
        all_cats = sorted(set(wd_cats) | set(we_cats))
        if all_cats:
            wd_vec = np.array([wd_cats.get(c, 0) for c in all_cats], dtype=float)
            we_vec = np.array([we_cats.get(c, 0) for c in all_cats], dtype=float)
            wd_vec = wd_vec / wd_vec.sum() if wd_vec.sum() > 0 else wd_vec
            we_vec = we_vec / we_vec.sum() if we_vec.sum() > 0 else we_vec
            results["models"][model]["context_sensitivity"] = round(
                float(np.sum(np.abs(wd_vec - we_vec))), 4
            )

        logger.info(f"    {model}: entropy={ent_mean:.4f}, latency={lat_mean:.2f}s, "
                     f"error_rate={errors / total_attempts:.2%}")

    # Pairwise Cohen's d
    model_names = list(model_entropies.keys())
    for i, m1 in enumerate(model_names):
        for m2 in model_names[i + 1:]:
            d = cohens_d(model_entropies[m1], model_entropies[m2])
            results["pairwise"][f"{m1}_vs_{m2}"] = {
                "cohens_d": round(d, 4),
                "effect_size": "large" if abs(d) > 0.8 else "medium" if abs(d) > 0.5 else "small",
            }

    out_path = OUTPUT_DIR / "rq2_llm_ablation.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"  Saved to {out_path}")

    return results


# =============================================================================
# RQ3: Scalability Benchmarks
# =============================================================================

async def run_rq3_scalability():
    """Run device scaling and duration stability benchmarks."""
    logger.info("=" * 60)
    logger.info("RQ3: Scalability Benchmarks")
    logger.info("=" * 60)

    import psutil
    from vesper.simulation.event_stream import EventStream, EventType

    results = {"device_scaling": [], "duration_stability": []}

    # --- Device Scaling ---
    device_counts = [5, 10, 25, 50, 100, 200]
    duration_s = 30.0
    trials = 5

    for n_devices in device_counts:
        trial_throughputs = []
        trial_cpus = []
        trial_mems = []
        trial_latencies = []

        for trial in range(trials):
            logger.info(f"  Device scaling: {n_devices} devices, trial {trial + 1}/{trials}")

            event_stream = EventStream(max_history=10000)
            event_stream.start()

            received_count = 0
            latencies = []

            def handler(event):
                nonlocal received_count
                received_count += 1
                emit_time = event.data.get("emit_time", 0)
                if emit_time > 0:
                    latencies.append(time.perf_counter() - emit_time)

            event_stream.subscribe(EventType.IOT_DEVICE_STATE, handler)

            process = psutil.Process()
            cpu_samples = []
            mem_samples = []

            start = time.perf_counter()
            total_events = 0

            while (time.perf_counter() - start) < duration_s:
                for d in range(n_devices):
                    event_stream.publish(
                        EventType.IOT_DEVICE_STATE,
                        f"device_{d}",
                        {"state": {"switch": "on"}, "emit_time": time.perf_counter()},
                    )
                    total_events += 1

                cpu_samples.append(process.cpu_percent(interval=0))
                mem_samples.append(process.memory_info().rss / (1024 * 1024))
                await asyncio.sleep(0.01)

            elapsed = time.perf_counter() - start
            event_stream.stop()

            throughput = received_count / elapsed if elapsed > 0 else 0
            avg_lat = float(np.mean(latencies)) * 1000 if latencies else 0

            trial_throughputs.append(throughput)
            trial_cpus.append(float(np.mean(cpu_samples)) if cpu_samples else 0)
            trial_mems.append(float(np.mean(mem_samples)) if mem_samples else 0)
            trial_latencies.append(avg_lat)

        tp_mean, tp_lo, tp_hi = confidence_interval_95(trial_throughputs)
        results["device_scaling"].append({
            "num_devices": n_devices,
            "throughput_events_per_sec": round(tp_mean, 1),
            "throughput_ci_95": [round(tp_lo, 1), round(tp_hi, 1)],
            "cpu_percent": round(float(np.mean(trial_cpus)), 1),
            "memory_mb": round(float(np.mean(trial_mems)), 1),
            "avg_latency_ms": round(float(np.mean(trial_latencies)), 2),
            "trials": trials,
        })
        logger.info(f"    {n_devices} devices: {tp_mean:.0f} events/s, "
                     f"CPU={np.mean(trial_cpus):.1f}%, "
                     f"latency={np.mean(trial_latencies):.2f}ms")

    # --- Duration Stability ---
    sim_hours = [1, 6, 24, 168]
    acceleration = 60.0  # 1 real second = 60 sim seconds

    for hours in sim_hours:
        real_duration = min((hours * 3600) / acceleration, 120)  # Cap at 2 min real
        logger.info(f"  Duration stability: {hours}h sim ({real_duration:.0f}s real)")

        event_stream = EventStream(max_history=100000)
        event_stream.start()

        db_path = Path(tempfile.mkdtemp()) / "stability_test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, type TEXT, ts REAL, data TEXT)")
        conn.commit()

        process = psutil.Process()
        mem_start = process.memory_info().rss / (1024 * 1024)

        event_count = 0
        start = time.perf_counter()

        while (time.perf_counter() - start) < real_duration:
            for _ in range(5):
                event_stream.publish(
                    EventType.IOT_DEVICE_STATE,
                    f"device_{event_count % 5}",
                    {"sim_time": event_count, "switch": "on"},
                )
                conn.execute(
                    "INSERT INTO events (type, ts, data) VALUES (?, ?, ?)",
                    ("state_change", time.time(), f'{{"count": {event_count}}}'),
                )
                event_count += 1

            if event_count % 100 == 0:
                conn.commit()
            await asyncio.sleep(0.01)

        conn.commit()
        elapsed = time.perf_counter() - start

        mem_end = process.memory_info().rss / (1024 * 1024)
        db_size_mb = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0

        results["duration_stability"].append({
            "sim_hours": hours,
            "real_duration_s": round(elapsed, 1),
            "event_count": event_count,
            "events_per_sec": round(event_count / elapsed, 1) if elapsed > 0 else 0,
            "memory_start_mb": round(mem_start, 1),
            "memory_end_mb": round(mem_end, 1),
            "memory_growth_mb": round(mem_end - mem_start, 2),
            "db_size_mb": round(db_size_mb, 3),
        })

        conn.close()
        event_stream.stop()
        if db_path.exists():
            db_path.unlink()

        logger.info(f"    {hours}h: {event_count} events, "
                     f"mem_growth={mem_end - mem_start:.2f}MB, "
                     f"db={db_size_mb:.3f}MB")

    # --- Docker Container Scaling (if Docker available) ---
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        docker_available = result.returncode == 0
    except Exception:
        docker_available = False

    if docker_available:
        logger.info("  Docker available — checking for vesper-qemu-arm image...")
        result = subprocess.run(
            ["docker", "images", "-q", "vesper-qemu-arm:latest"],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip():
            logger.info("  Running Docker container scaling benchmark...")
            container_counts = [3, 5, 10, 20]
            results["docker_scaling"] = []

            for n in container_counts:
                containers = []
                startup_times = []
                total_start = time.perf_counter()

                for i in range(n):
                    port = 16000 + i
                    name = f"vesper-bench-{i}"
                    start = time.perf_counter()
                    try:
                        r = subprocess.run(
                            ["docker", "run", "-d", "--name", name,
                             "-p", f"{port}:5555",
                             "-e", "DEVICE_TYPE=switch",
                             "-e", f"DEVICE_NAME=bench_{i}",
                             "vesper-qemu-arm:latest"],
                            capture_output=True, text=True, timeout=30,
                        )
                        if r.returncode == 0:
                            containers.append(name)
                            startup_times.append(time.perf_counter() - start)
                    except Exception as e:
                        logger.debug(f"Container start failed: {e}")

                total_startup = time.perf_counter() - total_start
                await asyncio.sleep(2)

                # TCP roundtrip
                tcp_latencies = []
                for i, name in enumerate(containers):
                    port = 16000 + i
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(3.0)
                        t0 = time.perf_counter()
                        sock.connect(("localhost", port))
                        sock.send(b"STATUS\n")
                        sock.recv(1024)
                        tcp_latencies.append(time.perf_counter() - t0)
                        sock.close()
                    except Exception:
                        pass

                # Cleanup
                for name in containers:
                    subprocess.run(["docker", "rm", "-f", name],
                                   capture_output=True, timeout=10)

                results["docker_scaling"].append({
                    "num_containers": n,
                    "avg_startup_s": round(float(np.mean(startup_times)), 2) if startup_times else 0,
                    "total_startup_s": round(total_startup, 2),
                    "avg_tcp_latency_ms": round(float(np.mean(tcp_latencies)) * 1000, 2) if tcp_latencies else 0,
                    "started_ok": len(containers),
                })
                logger.info(f"    {n} containers: startup={total_startup:.1f}s, "
                             f"tcp={np.mean(tcp_latencies)*1000:.1f}ms" if tcp_latencies else "")
        else:
            logger.info("  vesper-qemu-arm image not found, skipping Docker scaling")
    else:
        logger.info("  Docker not available, skipping Docker scaling")

    out_path = OUTPUT_DIR / "rq3_scalability.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"  Saved to {out_path}")

    return results


# =============================================================================
# RQ4: Latency Profiling
# =============================================================================

async def run_rq4_latency():
    """Run latency benchmarks for all critical paths."""
    logger.info("=" * 60)
    logger.info("RQ4: Latency Profiling")
    logger.info("=" * 60)

    from vesper.simulation.event_stream import EventStream, EventType

    iterations = 1000
    results = {}

    # --- Event Bus Dispatch Latency ---
    logger.info(f"  Benchmarking event bus ({iterations} iterations)...")
    event_stream = EventStream(max_history=10000)
    event_stream.start()

    bus_latencies = []
    received_times = []

    def handler(event):
        received_times.append(time.perf_counter())

    event_stream.subscribe(EventType.IOT_DEVICE_STATE, handler)

    for i in range(iterations):
        received_times.clear()
        start = time.perf_counter()
        event_stream.publish(
            EventType.IOT_DEVICE_STATE,
            "benchmark",
            {"state": {"switch": "on"}, "i": i},
        )
        # Small wait for handler
        await asyncio.sleep(0.0001)
        if received_times:
            bus_latencies.append(received_times[0] - start)

    event_stream.stop()

    if bus_latencies:
        a = np.array(bus_latencies) * 1000  # Convert to ms
        results["event_bus_dispatch"] = {
            "count": len(bus_latencies),
            "mean_ms": round(float(a.mean()), 3),
            "std_ms": round(float(a.std()), 3),
            "p50_ms": round(float(np.percentile(a, 50)), 3),
            "p95_ms": round(float(np.percentile(a, 95)), 3),
            "p99_ms": round(float(np.percentile(a, 99)), 3),
            "min_ms": round(float(a.min()), 3),
            "max_ms": round(float(a.max()), 3),
        }
        logger.info(f"    Event bus: p50={np.percentile(a, 50):.3f}ms, "
                     f"p95={np.percentile(a, 95):.3f}ms, "
                     f"p99={np.percentile(a, 99):.3f}ms")

    # --- SQLite DB Write Latency ---
    logger.info(f"  Benchmarking DB writes ({iterations} iterations)...")
    db_path = Path(tempfile.mkdtemp()) / "latency_bench.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE bench (id INTEGER PRIMARY KEY, data TEXT, ts REAL)")
    conn.commit()

    db_latencies = []
    for i in range(iterations):
        start = time.perf_counter()
        conn.execute(
            "INSERT INTO bench (data, ts) VALUES (?, ?)",
            (f"test_data_{i}", time.time()),
        )
        conn.commit()
        db_latencies.append(time.perf_counter() - start)

    conn.close()
    if db_path.exists():
        db_path.unlink()

    a = np.array(db_latencies) * 1000
    results["db_task_write"] = {
        "count": len(db_latencies),
        "mean_ms": round(float(a.mean()), 3),
        "std_ms": round(float(a.std()), 3),
        "p50_ms": round(float(np.percentile(a, 50)), 3),
        "p95_ms": round(float(np.percentile(a, 95)), 3),
        "p99_ms": round(float(np.percentile(a, 99)), 3),
        "min_ms": round(float(a.min()), 3),
        "max_ms": round(float(a.max()), 3),
    }
    logger.info(f"    DB write: p50={np.percentile(a, 50):.3f}ms, "
                 f"p95={np.percentile(a, 95):.3f}ms, "
                 f"p99={np.percentile(a, 99):.3f}ms")

    # --- Docker TCP Roundtrip (if available) ---
    docker_port = 15011  # Default VESPER firmware port
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(("localhost", docker_port))
        sock.close()
        docker_available = True
    except Exception:
        docker_available = False

    if docker_available:
        logger.info(f"  Benchmarking Docker TCP roundtrip (port {docker_port})...")
        tcp_latencies = []
        for i in range(min(iterations, 100)):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                start = time.perf_counter()
                sock.connect(("localhost", docker_port))
                sock.send(b"STATUS\n")
                sock.recv(1024)
                tcp_latencies.append(time.perf_counter() - start)
                sock.close()
            except Exception:
                pass

        if tcp_latencies:
            a = np.array(tcp_latencies) * 1000
            results["docker_tcp_roundtrip"] = {
                "count": len(tcp_latencies),
                "mean_ms": round(float(a.mean()), 3),
                "p50_ms": round(float(np.percentile(a, 50)), 3),
                "p95_ms": round(float(np.percentile(a, 95)), 3),
                "p99_ms": round(float(np.percentile(a, 99)), 3),
            }
            logger.info(f"    Docker TCP: p50={np.percentile(a, 50):.3f}ms, "
                         f"p95={np.percentile(a, 95):.3f}ms")
    else:
        logger.info(f"  No Docker container on port {docker_port}, skipping TCP benchmark")

    # --- DB Query (Read) Latency ---
    logger.info(f"  Benchmarking DB reads ({iterations} iterations)...")
    db_path = Path(tempfile.mkdtemp()) / "read_bench.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE bench (id INTEGER PRIMARY KEY, data TEXT, ts REAL)")
    for i in range(1000):
        conn.execute("INSERT INTO bench (data, ts) VALUES (?, ?)",
                     (f"data_{i}", time.time()))
    conn.commit()

    read_latencies = []
    for i in range(iterations):
        start = time.perf_counter()
        conn.execute("SELECT * FROM bench WHERE id = ?", (i % 1000 + 1,)).fetchone()
        read_latencies.append(time.perf_counter() - start)

    conn.close()
    if db_path.exists():
        db_path.unlink()

    a = np.array(read_latencies) * 1000
    results["db_query"] = {
        "count": len(read_latencies),
        "mean_ms": round(float(a.mean()), 3),
        "p50_ms": round(float(np.percentile(a, 50)), 3),
        "p95_ms": round(float(np.percentile(a, 95)), 3),
        "p99_ms": round(float(np.percentile(a, 99)), 3),
    }
    logger.info(f"    DB query: p50={np.percentile(a, 50):.3f}ms, "
                 f"p95={np.percentile(a, 95):.3f}ms")

    # --- LLM Latency (if LMStudio available) ---
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://localhost:1234/v1/models")
            if resp.status_code == 200:
                logger.info("  Benchmarking LLM generation latency...")
                llm_latencies = []
                for i in range(10):
                    start = time.perf_counter()
                    try:
                        resp = await client.post(
                            "http://localhost:1234/v1/chat/completions",
                            json={
                                "model": "auto",
                                "messages": [{"role": "user",
                                              "content": "Generate a 3-task morning schedule in JSON format."}],
                                "max_tokens": 512,
                                "temperature": 0.7,
                            },
                            timeout=60.0,
                        )
                        if resp.status_code == 200:
                            llm_latencies.append(time.perf_counter() - start)
                    except Exception:
                        pass

                if llm_latencies:
                    a = np.array(llm_latencies) * 1000
                    results["llm_schedule_generation"] = {
                        "count": len(llm_latencies),
                        "mean_ms": round(float(a.mean()), 1),
                        "p50_ms": round(float(np.percentile(a, 50)), 1),
                        "p95_ms": round(float(np.percentile(a, 95)), 1),
                        "p99_ms": round(float(np.percentile(a, 99)), 1),
                    }
                    logger.info(f"    LLM gen: mean={a.mean():.0f}ms, "
                                 f"p95={np.percentile(a, 95):.0f}ms")
    except Exception:
        logger.info("  LMStudio not available, skipping LLM latency")

    out_path = OUTPUT_DIR / "rq4_latency.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"  Saved to {out_path}")

    return results


# =============================================================================
# RQ5: Sim2Real Transfer
# =============================================================================

def run_rq5_sim2real():
    """Train classifier on VESPER data, test on real CASAS/ARAS data."""
    logger.info("=" * 60)
    logger.info("RQ5: Sim2Real Transfer Evaluation")
    logger.info("=" * 60)

    try:
        from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
        from sklearn.metrics import accuracy_score, f1_score, classification_report
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import LabelEncoder
    except ImportError:
        logger.error("sklearn not installed. Cannot run RQ5.")
        return None

    vesper = load_vesper_schedules()
    casas = load_casas_data()
    aras = load_aras_data()

    def extract_features(tasks, source="vesper"):
        """Extract (hour_proxy, duration_proxy) features + category labels."""
        features = []
        labels = []

        if source == "vesper":
            for i, task in enumerate(tasks):
                # Estimate hour from task position in day
                day_pos = (i % 15) / 15.0  # Rough position in day
                hour_proxy = day_pos  # Normalized 0-1
                features.append([hour_proxy, 0.5])  # Duration unknown, use median
                labels.append(task["category"])
        else:
            # For reference datasets, use day_categories
            for day_cats in tasks:
                for j, cat in enumerate(day_cats):
                    hour_proxy = j / max(len(day_cats), 1)
                    features.append([hour_proxy, 0.5])
                    labels.append(cat)

        return np.array(features) if features else np.zeros((0, 2)), np.array(labels) if labels else np.array([])

    results = {"classifiers": {}}

    # VESPER training data
    X_train, y_train = extract_features(vesper["all_tasks"], "vesper")
    logger.info(f"  VESPER training data: {len(X_train)} samples")

    # Test against each reference dataset
    for dataset_name, dataset_dict in [("casas", casas), ("aras", aras)]:
        for home_name, home_data in dataset_dict.items():
            X_test, y_test = extract_features(home_data["day_categories"], "reference")

            if len(X_test) == 0 or len(X_train) == 0:
                logger.warning(f"  Insufficient data for {dataset_name}/{home_name}")
                continue

            # Filter to common labels
            common_labels = set(y_train) & set(y_test)
            if len(common_labels) < 2:
                logger.warning(f"  Too few common labels for {dataset_name}/{home_name}")
                continue

            train_mask = np.isin(y_train, list(common_labels))
            test_mask = np.isin(y_test, list(common_labels))
            X_tr = X_train[train_mask]
            y_tr = y_train[train_mask]
            X_te = X_test[test_mask]
            y_te = y_test[test_mask]

            le = LabelEncoder()
            le.fit(list(common_labels))
            y_tr_enc = le.transform(y_tr)
            y_te_enc = le.transform(y_te)

            # Random Forest
            rf = RandomForestClassifier(n_estimators=100, random_state=42)
            rf.fit(X_tr, y_tr_enc)
            y_pred_rf = rf.predict(X_te)

            acc_rf = accuracy_score(y_te_enc, y_pred_rf)
            f1_rf = f1_score(y_te_enc, y_pred_rf, average="weighted", zero_division=0)

            # Gradient Boosting (stronger classifier)
            gb = GradientBoostingClassifier(n_estimators=100, random_state=42)
            gb.fit(X_tr, y_tr_enc)
            y_pred_gb = gb.predict(X_te)

            acc_gb = accuracy_score(y_te_enc, y_pred_gb)
            f1_gb = f1_score(y_te_enc, y_pred_gb, average="weighted", zero_division=0)

            # Cross-validation on combined data
            X_all = np.vstack([X_tr, X_te])
            y_all = np.concatenate([y_tr_enc, y_te_enc])
            n_classes = len(set(y_all))
            cv_folds = min(5, n_classes, len(X_all) // 10)
            if cv_folds >= 2:
                cv_scores = cross_val_score(rf, X_all, y_all, cv=cv_folds)
                cv_mean, cv_lo, cv_hi = confidence_interval_95(cv_scores)
            else:
                cv_mean, cv_lo, cv_hi = acc_rf, acc_rf, acc_rf

            comp_key = f"{dataset_name}/{home_name}"
            results["classifiers"][comp_key] = {
                "train_samples": len(X_tr),
                "test_samples": len(X_te),
                "common_classes": len(common_labels),
                "classes": sorted(common_labels),
                "random_forest": {
                    "accuracy": round(acc_rf, 4),
                    "f1_weighted": round(f1_rf, 4),
                },
                "gradient_boosting": {
                    "accuracy": round(acc_gb, 4),
                    "f1_weighted": round(f1_gb, 4),
                },
                "cross_val_mean": round(cv_mean, 4),
                "cross_val_ci_95": [round(cv_lo, 4), round(cv_hi, 4)],
            }

            logger.info(f"  {comp_key}: RF acc={acc_rf:.3f} F1={f1_rf:.3f}, "
                         f"GB acc={acc_gb:.3f} F1={f1_gb:.3f}, "
                         f"CV={cv_mean:.3f}")

    # Aggregate across all test sets
    all_rf_acc = [v["random_forest"]["accuracy"] for v in results["classifiers"].values()]
    all_rf_f1 = [v["random_forest"]["f1_weighted"] for v in results["classifiers"].values()]
    all_gb_acc = [v["gradient_boosting"]["accuracy"] for v in results["classifiers"].values()]
    all_gb_f1 = [v["gradient_boosting"]["f1_weighted"] for v in results["classifiers"].values()]

    if all_rf_acc:
        rf_acc_m, rf_acc_lo, rf_acc_hi = confidence_interval_95(all_rf_acc)
        rf_f1_m, rf_f1_lo, rf_f1_hi = confidence_interval_95(all_rf_f1)
        gb_acc_m, gb_acc_lo, gb_acc_hi = confidence_interval_95(all_gb_acc)
        gb_f1_m, gb_f1_lo, gb_f1_hi = confidence_interval_95(all_gb_f1)

        results["aggregate"] = {
            "rf_accuracy": round(rf_acc_m, 4),
            "rf_accuracy_ci_95": [round(rf_acc_lo, 4), round(rf_acc_hi, 4)],
            "rf_f1_weighted": round(rf_f1_m, 4),
            "rf_f1_ci_95": [round(rf_f1_lo, 4), round(rf_f1_hi, 4)],
            "gb_accuracy": round(gb_acc_m, 4),
            "gb_accuracy_ci_95": [round(gb_acc_lo, 4), round(gb_acc_hi, 4)],
            "gb_f1_weighted": round(gb_f1_m, 4),
            "gb_f1_ci_95": [round(gb_f1_lo, 4), round(gb_f1_hi, 4)],
        }
        logger.info(f"\n  AGGREGATE: RF acc={rf_acc_m:.3f}, GB acc={gb_acc_m:.3f}")

    out_path = OUTPUT_DIR / "rq5_sim2real.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"  Saved to {out_path}")

    return results


# =============================================================================
# Main
# =============================================================================

async def main():
    logger.info("=" * 60)
    logger.info("VESPER Real Data Collection — All RQs")
    logger.info(f"Started at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    all_results = {}

    # RQ1: Activity Realism (no async needed)
    try:
        all_results["rq1"] = run_rq1_activity_comparison()
    except Exception as e:
        logger.error(f"RQ1 failed: {e}", exc_info=True)

    # RQ4: Latency (fast, no external deps)
    try:
        all_results["rq4"] = await run_rq4_latency()
    except Exception as e:
        logger.error(f"RQ4 failed: {e}", exc_info=True)

    # RQ3: Scalability
    try:
        all_results["rq3"] = await run_rq3_scalability()
    except Exception as e:
        logger.error(f"RQ3 failed: {e}", exc_info=True)

    # RQ5: Sim2Real (needs sklearn)
    try:
        all_results["rq5"] = run_rq5_sim2real()
    except Exception as e:
        logger.error(f"RQ5 failed: {e}", exc_info=True)

    # RQ2: LLM Ablation (needs LMStudio — longest)
    try:
        all_results["rq2"] = await run_rq2_llm_ablation()
    except Exception as e:
        logger.error(f"RQ2 failed: {e}", exc_info=True)

    # Save combined results
    combined_path = OUTPUT_DIR / "all_rq_results.json"
    with open(combined_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    logger.info("=" * 60)
    logger.info(f"All experiments completed at {datetime.now().isoformat()}")
    logger.info(f"Results saved to {OUTPUT_DIR}")
    logger.info("=" * 60)

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for rq, data in all_results.items():
        if data is None:
            print(f"  {rq}: SKIPPED (dependency not available)")
        else:
            print(f"  {rq}: ✓ collected")
    print(f"\nOutput directory: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    if "--only-rq2" in sys.argv:
        async def _rq2_only():
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            result = await run_rq2_llm_ablation()
            if result:
                combined = OUTPUT_DIR / "all_rq_results.json"
                if combined.exists():
                    with open(combined) as f:
                        all_res = json.load(f)
                else:
                    all_res = {}
                all_res["rq2"] = result
                with open(combined, "w") as f:
                    json.dump(all_res, f, indent=2, default=str)
                print("RQ2 ✓ saved")
        asyncio.run(_rq2_only())
    else:
        asyncio.run(main())
