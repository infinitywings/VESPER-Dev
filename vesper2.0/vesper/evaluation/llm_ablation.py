"""
LLM Ablation Study for VESPER.

Compares activity schedule generation quality across:
- Multiple LLM models (loaded in LMStudio)
- Rule-based baseline
- Multiple persona profiles
- Weekday vs. weekend context sensitivity
- Varying temperatures

Produces per-model metrics:
- Schedule entropy (diversity)
- Activity distribution alignment with reference
- Duration realism
- Context sensitivity (weekday/weekend variance)
- Generation latency
- Parse error rate
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .metrics import (
    LLMAblationResult,
    compute_kl_divergence,
    compute_schedule_entropy,
    confidence_interval,
    cohens_d,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Persona Library
# =============================================================================

PERSONA_LIBRARY = [
    {
        "name": "Alex", "age": 28, "occupation": "Software Engineer",
        "wake_time": "07:30", "sleep_time": "23:30",
        "works_from_home": True, "exercise_frequency": 0.7,
        "social_frequency": 0.3, "description": "Young tech worker, active lifestyle",
    },
    {
        "name": "Maria", "age": 65, "occupation": "Retired Teacher",
        "wake_time": "06:00", "sleep_time": "21:30",
        "works_from_home": False, "exercise_frequency": 0.4,
        "social_frequency": 0.6, "description": "Retired, social, early riser",
    },
    {
        "name": "James", "age": 42, "occupation": "Nurse (Night Shift)",
        "wake_time": "14:00", "sleep_time": "06:00",
        "works_from_home": False, "exercise_frequency": 0.3,
        "social_frequency": 0.2, "description": "Night shift worker, irregular schedule",
    },
    {
        "name": "Priya", "age": 22, "occupation": "University Student",
        "wake_time": "09:00", "sleep_time": "01:00",
        "works_from_home": True, "exercise_frequency": 0.5,
        "social_frequency": 0.8, "description": "Student, late nights, social",
    },
    {
        "name": "Robert", "age": 55, "occupation": "Corporate Manager",
        "wake_time": "05:30", "sleep_time": "22:00",
        "works_from_home": False, "exercise_frequency": 0.6,
        "social_frequency": 0.4, "description": "Early riser, structured routine",
    },
    {
        "name": "Yuki", "age": 35, "occupation": "Freelance Artist",
        "wake_time": "10:00", "sleep_time": "02:00",
        "works_from_home": True, "exercise_frequency": 0.2,
        "social_frequency": 0.5, "description": "Creative, flexible schedule, night owl",
    },
    {
        "name": "David", "age": 48, "occupation": "Stay-at-Home Parent",
        "wake_time": "06:30", "sleep_time": "22:30",
        "works_from_home": True, "exercise_frequency": 0.4,
        "social_frequency": 0.5, "description": "Household-focused, regular routine",
    },
    {
        "name": "Sofia", "age": 30, "occupation": "Fitness Instructor",
        "wake_time": "05:00", "sleep_time": "21:00",
        "works_from_home": False, "exercise_frequency": 0.9,
        "social_frequency": 0.6, "description": "Very active, early schedule",
    },
    {
        "name": "Chen", "age": 70, "occupation": "Retired (Sedentary)",
        "wake_time": "07:00", "sleep_time": "20:00",
        "works_from_home": True, "exercise_frequency": 0.1,
        "social_frequency": 0.2, "description": "Sedentary elderly, TV-focused",
    },
    {
        "name": "Aisha", "age": 38, "occupation": "Doctor (Day Shift)",
        "wake_time": "05:30", "sleep_time": "22:00",
        "works_from_home": False, "exercise_frequency": 0.5,
        "social_frequency": 0.3, "description": "Long work hours, structured routine",
    },
]


# =============================================================================
# LMStudio Model Configurations
# =============================================================================

# Models recommended for LMStudio evaluation
LMSTUDIO_MODELS = {
    "qwen2.5-7b-instruct": {
        "name": "Qwen2.5 7B Instruct",
        "params": "7B",
        "family": "Qwen",
        "description": "Fast, good instruction following",
        "lmstudio_id": "qwen2.5-7b-instruct",
    },
    "meta-llama-3.1-8b-instruct": {
        "name": "Llama 3.1 8B Instruct",
        "params": "8B",
        "family": "Llama",
        "description": "Meta's flagship small model",
        "lmstudio_id": "meta-llama-3.1-8b-instruct",
    },
    "mistralai/mistral-7b-instruct-v0.3": {
        "name": "Mistral 7B Instruct v0.3",
        "params": "7B",
        "family": "Mistral",
        "description": "Strong European model, good JSON output",
        "lmstudio_id": "mistralai/mistral-7b-instruct-v0.3",
    },
    "gemma-2-9b-it": {
        "name": "Gemma 2 9B IT",
        "params": "9B",
        "family": "Gemma",
        "description": "Google's instruction-tuned model",
        "lmstudio_id": "gemma-2-9b-it",
    },
    "phi-3.5-mini-instruct": {
        "name": "Phi 3.5 Mini Instruct",
        "params": "3.8B",
        "family": "Phi",
        "description": "Microsoft's small but capable model",
        "lmstudio_id": "phi-3.5-mini-instruct",
    },
    "openai/gpt-oss-20b": {
        "name": "GPT-OSS 20B",
        "params": "20B",
        "family": "OpenAI",
        "description": "OpenAI's open-source 20B model",
        "lmstudio_id": "openai/gpt-oss-20b",
    },
}


# =============================================================================
# Schedule Generation Prompt
# =============================================================================

SCHEDULE_GENERATION_PROMPT = """You are simulating a realistic daily schedule for a person in a smart home.

Persona:
- Name: {name}
- Age: {age}
- Occupation: {occupation}
- Wake time: {wake_time}
- Sleep time: {sleep_time}
- Works from home: {works_from_home}
- Exercise frequency: {exercise_desc}
- Social frequency: {social_desc}

Day type: {day_type}

Available rooms: bedroom, bathroom, kitchen, living room, office/study

Generate a realistic daily schedule as a JSON array. Each task should have:
- "name": descriptive task name
- "category": one of [sleep, hygiene, eating, work, exercise, leisure, social, household, idle]
- "room": which room
- "start_time": "HH:MM" format
- "duration_minutes": integer

The schedule should:
1. Start with waking up and end with going to sleep
2. Include meals at appropriate times
3. Reflect the persona's occupation and preferences
4. Have realistic durations (e.g., shower 10-15min, cooking 30-60min)
5. Include transitions and idle time
6. Be different for weekdays vs weekends
7. Have 12-20 tasks total

Respond with ONLY the JSON array, no other text.
"""


# =============================================================================
# Rule-Based Baseline
# =============================================================================

class RuleBasedScheduleGenerator:
    """
    Simple rule-based schedule generator as a baseline.
    Produces deterministic schedules based on persona parameters.
    """

    def generate(
        self,
        persona: Dict[str, Any],
        day_type: str = "weekday",
        seed: int = 42,
    ) -> List[Dict[str, Any]]:
        """Generate a rule-based schedule."""
        rng = random.Random(seed)

        wake_h, wake_m = map(int, persona.get("wake_time", "07:00").split(":"))
        sleep_h, sleep_m = map(int, persona.get("sleep_time", "23:00").split(":"))

        schedule = []
        current_h, current_m = wake_h, wake_m

        def add_task(name, category, room, duration):
            nonlocal current_h, current_m
            schedule.append({
                "name": name,
                "category": category,
                "room": room,
                "start_time": f"{current_h:02d}:{current_m:02d}",
                "duration_minutes": duration,
            })
            current_m += duration
            current_h += current_m // 60
            current_m %= 60

        # Morning routine
        add_task("Wake up", "sleep", "bedroom", 5)
        add_task("Use bathroom", "hygiene", "bathroom", 15)
        add_task("Breakfast", "eating", "kitchen", rng.randint(20, 40))

        # Morning activity
        if day_type == "weekday" and persona.get("occupation") not in ["Retired", "Retired Teacher"]:
            if persona.get("works_from_home"):
                add_task("Morning work", "work", "office/study", rng.randint(120, 180))
            else:
                add_task("Commute & work", "work", "office/study", rng.randint(180, 240))
        else:
            add_task("Leisure time", "leisure", "living room", rng.randint(60, 120))

        # Lunch
        add_task("Prepare lunch", "eating", "kitchen", rng.randint(20, 40))
        add_task("Eat lunch", "eating", "kitchen", rng.randint(20, 30))

        # Afternoon
        if day_type == "weekday" and persona.get("occupation") not in ["Retired", "Retired Teacher"]:
            add_task("Afternoon work", "work", "office/study", rng.randint(120, 180))
        else:
            if rng.random() < persona.get("exercise_frequency", 0.5):
                add_task("Exercise", "exercise", "living room", rng.randint(30, 60))
            if rng.random() < persona.get("social_frequency", 0.3):
                add_task("Social activity", "social", "living room", rng.randint(30, 60))
            else:
                add_task("Afternoon leisure", "leisure", "living room", rng.randint(60, 90))

        # Household chores
        if rng.random() < 0.5:
            add_task("Household chores", "household", "kitchen", rng.randint(15, 30))

        # Dinner
        add_task("Prepare dinner", "eating", "kitchen", rng.randint(30, 60))
        add_task("Eat dinner", "eating", "kitchen", rng.randint(20, 30))

        # Evening
        add_task("Evening relaxation", "leisure", "living room", rng.randint(60, 120))
        add_task("Night hygiene", "hygiene", "bathroom", rng.randint(10, 20))
        add_task("Go to sleep", "sleep", "bedroom", 0)

        return schedule


# =============================================================================
# LLM Ablation Runner
# =============================================================================

class LLMAblationRunner:
    """
    Runs ablation study comparing LLM models for schedule generation.

    For each model × persona × day_type × seed combination:
    1. Generate a daily schedule
    2. Parse and validate
    3. Compute quality metrics
    4. Aggregate results
    """

    def __init__(
        self,
        lmstudio_url: str = "http://localhost:1234/v1/chat/completions",
        models: Optional[List[str]] = None,
        personas: Optional[List[Dict[str, Any]]] = None,
        schedules_per_config: int = 30,
        seeds: Optional[List[int]] = None,
        temperature: float = 0.7,
        output_dir: str = "results/llm_ablation",
    ):
        self.lmstudio_url = lmstudio_url
        self.models = models or list(LMSTUDIO_MODELS.keys())
        self.personas = personas or PERSONA_LIBRARY
        self.schedules_per_config = schedules_per_config
        self.seeds = seeds or list(range(42, 42 + schedules_per_config))
        self.temperature = temperature
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._results: List[LLMAblationResult] = []
        self._raw_schedules: Dict[str, List[Dict]] = defaultdict(list)
        self._rule_baseline = RuleBasedScheduleGenerator()

    async def _generate_schedule_llm(
        self,
        model: str,
        persona: Dict[str, Any],
        day_type: str = "weekday",
        seed: int = 42,
    ) -> Tuple[Optional[List[Dict]], float]:
        """Generate a schedule using an LLM via LMStudio. Returns (schedule, latency_s)."""
        try:
            import httpx
        except ImportError:
            logger.error("httpx not installed")
            return None, 0.0

        exercise_desc = "Often" if persona.get("exercise_frequency", 0.5) > 0.5 else "Sometimes"
        social_desc = "Frequent" if persona.get("social_frequency", 0.3) > 0.5 else "Occasional"

        prompt = SCHEDULE_GENERATION_PROMPT.format(
            name=persona["name"],
            age=persona["age"],
            occupation=persona["occupation"],
            wake_time=persona.get("wake_time", "07:00"),
            sleep_time=persona.get("sleep_time", "23:00"),
            works_from_home=persona.get("works_from_home", True),
            exercise_desc=exercise_desc,
            social_desc=social_desc,
            day_type=day_type,
        )

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": 2048,
            "seed": seed,
        }

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(self.lmstudio_url, json=payload)
                latency = time.perf_counter() - start

                if response.status_code != 200:
                    logger.warning(f"LLM request failed: {response.status_code}")
                    return None, latency

                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # Parse JSON from response
                content = content.strip()
                if content.startswith("```json"):
                    content = content[7:]
                if content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]

                schedule = json.loads(content.strip())
                if isinstance(schedule, list):
                    return schedule, latency
                return None, latency

        except json.JSONDecodeError:
            return None, time.perf_counter() - start
        except Exception as e:
            logger.warning(f"LLM generation error: {e}")
            return None, time.perf_counter() - start

    def _analyze_schedule(
        self,
        schedule: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Analyze a single schedule for quality metrics."""
        categories = [t.get("category", "idle") for t in schedule]
        durations = [t.get("duration_minutes", 0) for t in schedule]
        rooms = [t.get("room", "unknown") for t in schedule]

        cat_counts = Counter(categories)
        total = sum(cat_counts.values())
        cat_dist = {k: v / total for k, v in cat_counts.items()} if total > 0 else {}

        return {
            "num_tasks": len(schedule),
            "category_distribution": cat_dist,
            "category_counts": dict(cat_counts),
            "unique_categories": len(set(categories)),
            "unique_rooms": len(set(rooms)),
            "mean_duration_min": float(np.mean(durations)) if durations else 0,
            "std_duration_min": float(np.std(durations)) if durations else 0,
            "total_duration_min": sum(durations),
            "categories": categories,
        }

    async def run_single_model(
        self,
        model: str,
        persona: Dict[str, Any],
    ) -> LLMAblationResult:
        """Run ablation for a single model × persona combination."""
        weekday_analyses = []
        weekend_analyses = []
        latencies = []
        errors = 0
        all_cat_sequences = []

        for i, seed in enumerate(self.seeds):
            # Alternate weekday/weekend
            day_type = "weekday" if i % 7 < 5 else "weekend"

            if model == "rule_based":
                schedule = self._rule_baseline.generate(persona, day_type, seed)
                latency = 0.0
            else:
                schedule, latency = await self._generate_schedule_llm(
                    model, persona, day_type, seed
                )

            latencies.append(latency)

            if schedule is None:
                errors += 1
                continue

            analysis = self._analyze_schedule(schedule)
            all_cat_sequences.append(analysis["categories"])

            if day_type == "weekday":
                weekday_analyses.append(analysis)
            else:
                weekend_analyses.append(analysis)

            self._raw_schedules[f"{model}_{persona['name']}"].append({
                "model": model,
                "persona": persona["name"],
                "day_type": day_type,
                "seed": seed,
                "schedule": schedule,
                "analysis": analysis,
            })

        # Aggregate metrics
        all_analyses = weekday_analyses + weekend_analyses
        if not all_analyses:
            return LLMAblationResult(
                model_name=model,
                persona_name=persona["name"],
                error_rate=1.0,
            )

        # Activity distribution (averaged)
        combined_dist: Dict[str, List[float]] = defaultdict(list)
        for a in all_analyses:
            for cat, pct in a["category_distribution"].items():
                combined_dist[cat].append(pct)
        avg_dist = {k: float(np.mean(v)) for k, v in combined_dist.items()}

        # Schedule entropy
        entropy = compute_schedule_entropy(all_cat_sequences)

        # Context sensitivity: how different are weekday vs weekend
        wd_cats = Counter()
        we_cats = Counter()
        for a in weekday_analyses:
            wd_cats.update(a["category_counts"])
        for a in weekend_analyses:
            we_cats.update(a["category_counts"])

        all_cats = set(wd_cats) | set(we_cats)
        if all_cats:
            wd_vec = np.array([wd_cats.get(c, 0) for c in sorted(all_cats)], dtype=float)
            we_vec = np.array([we_cats.get(c, 0) for c in sorted(all_cats)], dtype=float)
            wd_vec = wd_vec / wd_vec.sum() if wd_vec.sum() > 0 else wd_vec
            we_vec = we_vec / we_vec.sum() if we_vec.sum() > 0 else we_vec
            context_sensitivity = float(np.sum(np.abs(wd_vec - we_vec)))
        else:
            context_sensitivity = 0.0

        result = LLMAblationResult(
            model_name=model,
            persona_name=persona["name"],
            num_schedules=len(all_analyses),
            generation_latency_s=float(np.mean(latencies)) if latencies else 0,
            schedule_entropy=entropy,
            activity_distribution=avg_dist,
            duration_mean_min=float(np.mean([a["mean_duration_min"] for a in all_analyses])),
            duration_std_min=float(np.mean([a["std_duration_min"] for a in all_analyses])),
            context_sensitivity=context_sensitivity,
            num_unique_activities=len(set(c for a in all_analyses for c in a["categories"])),
            num_tasks_per_schedule=float(np.mean([a["num_tasks"] for a in all_analyses])),
            error_rate=errors / len(self.seeds) if self.seeds else 0,
        )

        self._results.append(result)
        return result

    async def run_all(self) -> List[LLMAblationResult]:
        """Run full ablation study across all models and personas."""
        all_models = ["rule_based"] + self.models

        total = len(all_models) * len(self.personas)
        completed = 0

        for model in all_models:
            for persona in self.personas:
                completed += 1
                logger.info(
                    f"[{completed}/{total}] Ablation: model={model}, "
                    f"persona={persona['name']}"
                )
                await self.run_single_model(model, persona)

        self.export()
        return self._results

    def get_model_summary(self) -> Dict[str, Dict[str, Any]]:
        """Get aggregated summary per model."""
        by_model: Dict[str, List[LLMAblationResult]] = defaultdict(list)
        for r in self._results:
            by_model[r.model_name].append(r)

        summary = {}
        for model, results in by_model.items():
            summary[model] = {
                "avg_entropy": float(np.mean([r.schedule_entropy for r in results])),
                "avg_latency_s": float(np.mean([r.generation_latency_s for r in results])),
                "avg_tasks_per_schedule": float(np.mean([r.num_tasks_per_schedule for r in results])),
                "avg_context_sensitivity": float(np.mean([r.context_sensitivity for r in results])),
                "avg_error_rate": float(np.mean([r.error_rate for r in results])),
                "avg_unique_activities": float(np.mean([r.num_unique_activities for r in results])),
                "num_personas_tested": len(results),
            }

        return summary

    def compute_pairwise_comparisons(self) -> Dict[str, Any]:
        """Compute pairwise statistical comparisons between models."""
        by_model: Dict[str, List[float]] = defaultdict(list)
        for r in self._results:
            by_model[r.model_name].append(r.schedule_entropy)

        comparisons = {}
        models = list(by_model.keys())
        for i, m1 in enumerate(models):
            for m2 in models[i + 1:]:
                d = cohens_d(by_model[m1], by_model[m2])
                comparisons[f"{m1}_vs_{m2}"] = {
                    "cohens_d": round(d, 4),
                    "effect_size": "large" if abs(d) > 0.8 else "medium" if abs(d) > 0.5 else "small",
                    "n1": len(by_model[m1]),
                    "n2": len(by_model[m2]),
                }

        return comparisons

    def export(self):
        """Export all ablation results."""
        # Per-result details
        details = [r.to_dict() for r in self._results]
        (self.output_dir / "ablation_details.json").write_text(
            json.dumps(details, indent=2, default=str)
        )

        # Model summary
        summary = self.get_model_summary()
        (self.output_dir / "model_summary.json").write_text(
            json.dumps(summary, indent=2)
        )

        # Pairwise comparisons
        comparisons = self.compute_pairwise_comparisons()
        (self.output_dir / "pairwise_comparisons.json").write_text(
            json.dumps(comparisons, indent=2)
        )

        # Raw schedules (for reproducibility)
        raw_path = self.output_dir / "raw_schedules.json"
        with open(raw_path, "w") as f:
            json.dump(dict(self._raw_schedules), f, indent=2, default=str)

        logger.info(f"Exported ablation results to {self.output_dir}")
