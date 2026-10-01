#!/usr/bin/env python3
"""Audit retained summaries and geometric model behavior without external services.

This is a re-analysis of an existing snapshot, not a new 90-run experiment.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "results" / "reference"


def scene_summary():
    snapshot = json.loads((REFERENCE / "scene_runs.json").read_text())
    rows = snapshot["runs"]
    rooms = {}
    by_model = Counter()
    for row in rows:
        by_model[row["model"]] += 1
        scene = row["scene_id"]
        if scene in rooms and rooms[scene] != row["num_rooms"]:
            raise ValueError(f"Inconsistent room counts for scene {scene}")
        rooms[scene] = row["num_rooms"]
    return {
        "source_commit": snapshot["source_commit"],
        "recorded_rows": len(rows),
        "unique_scenes": len(rooms),
        "rows_per_model": dict(sorted(by_model.items())),
        "mean_rooms_per_scene": sum(rooms.values()) / len(rooms),
        "rooms_min": min(rooms.values()),
        "rooms_max": max(rooms.values()),
        "room_count_matches_proposal_14_8": math.isclose(sum(rooms.values()) / len(rooms), 14.8, abs_tol=0.05),
        "infrastructure_failure_field_available": snapshot["infrastructure_failure_field_available"],
        "zero_infrastructure_failures_independently_verified": False,
        "note": "Record counts establish the stored matrix, not failure-free execution or semantic success.",
    }


def js_divergence(counts_p, counts_q, categories, eps=1e-10):
    # Same smoothing and natural-log definition as collect_real_data.py.
    p = [counts_p.get(c, 0) + eps for c in categories]
    q = [counts_q.get(c, 0) + eps for c in categories]
    sp, sq = sum(p), sum(q)
    p, q = [v / sp for v in p], [v / sq for v in q]
    midpoint = [(a + b) / 2 for a, b in zip(p, q)]
    return 0.5 * sum(a * math.log(a / m) + b * math.log(b / m)
                     for a, b, m in zip(p, q, midpoint))


def activity_summary():
    data = json.loads((REFERENCE / "activity_counts.json").read_text())
    comparisons = {}
    for name, ref in data["references"].items():
        value = js_divergence(data["generated_counts"], ref["counts"], data["categories"])
        comparisons[name] = {
            "recomputed_js_nats": value,
            "stored_js": ref["reported_js"],
            "agrees_at_four_decimals": round(value, 4) == ref["reported_js"],
            "generated_days": ref["generated_days"],
            "reference_days": ref["reference_days"],
        }
    values = [r["recomputed_js_nats"] for r in comparisons.values()]
    return {
        "comparisons": comparisons, "min_js": min(values), "max_js": max(values),
        "interpretation": "Mapped category-count marginals only; not temporal, semantic, or physical fidelity.",
        "provenance_limit": "The stored distribution reports 174 generated days; its model-level composition is not established by this snapshot.",
    }


def sensor_summary():
    from vesper.habitat.sensors.motion_sensor import MotionSensorConfig, PIRMotionSensor, SensitivityLevel

    def run_speed(displacement):
        sensor = PIRMotionSensor(MotionSensorConfig(
            position=(0.0, 1.0, 0.0), tilt=0.0, cooldown=0.0,
            sensitivity=SensitivityLevel.HIGH,
        ))
        first = sensor.update({"synthetic_target": (0.0, 1.0, 2.0)}, current_time=10.0)
        second = sensor.update({"synthetic_target": (0.0, 1.0, 2.0 + displacement)}, current_time=11.0)
        return bool(first), bool(second)

    first_slow, slow = run_speed(0.05)
    _, fast = run_speed(0.20)
    backwards = PIRMotionSensor(MotionSensorConfig(position=(0.0, 1.0, 0.0), tilt=0.0, cooldown=0.0))
    outside = bool(backwards.update({"synthetic_target": (0.0, 1.0, -2.0)}, current_time=10.0))
    low = PIRMotionSensor.SENSITIVITY_PARAMS[SensitivityLevel.LOW]["range_mult"]
    high = PIRMotionSensor.SENSITIVITY_PARAMS[SensitivityLevel.HIGH]["range_mult"]
    return {
        "min_motion_speed_m_per_s": MotionSensorConfig().min_motion_speed,
        "first_observation_can_trigger_without_velocity_history": first_slow,
        "tracked_target_at_0_05_m_per_s_triggers": slow,
        "tracked_target_at_0_20_m_per_s_triggers": fast,
        "target_behind_sensor_triggers": outside,
        "low_nominal_range_multiplier": low,
        "high_nominal_range_multiplier": high,
        "low_over_high_nominal_range_ratio": low / high,
        "note": "Parameter ratio, not measured physical detection range; confidence and angle thresholds also affect detection.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim", choices=["scenes", "activity", "sensors", "all"], default="all")
    parser.add_argument("--output", type=Path, help="Optional JSON audit output (default: stdout only)")
    args = parser.parse_args()
    actions = {"scenes": scene_summary, "activity": activity_summary, "sensors": sensor_summary}
    result = {name: fn() for name, fn in actions.items() if args.claim in (name, "all")}
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(serialized, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    if "activity" in result and not all(x["agrees_at_four_decimals"] for x in result["activity"]["comparisons"].values()):
        return 1
    if "sensors" in result:
        s = result["sensors"]
        if s["tracked_target_at_0_05_m_per_s_triggers"] or not s["tracked_target_at_0_20_m_per_s_triggers"] or s["target_behind_sensor_triggers"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
