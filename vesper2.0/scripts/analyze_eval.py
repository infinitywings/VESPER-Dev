#!/usr/bin/env python3
"""
VESPER — Comprehensive analysis of the 28-scene autonomous evaluation.
Reads eval_metrics.csv and produces aggregate statistics for the paper.
"""
import csv
import json
import math
import os
import statistics
from pathlib import Path
from collections import Counter

DATA_DIR = Path(__file__).parent.parent / "results" / "vesper_autonomous_eval"
CSV_PATH = DATA_DIR / "eval_metrics.csv"

def load_data():
    rows = []
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for r in reader:
            # Convert numeric fields
            for k in r:
                try:
                    if '.' in r[k]:
                        r[k] = float(r[k])
                    else:
                        r[k] = int(r[k])
                except (ValueError, TypeError):
                    pass
            rows.append(r)
    return rows

def mean(vals):
    return sum(vals) / len(vals) if vals else 0

def std(vals):
    return statistics.stdev(vals) if len(vals) > 1 else 0

def median(vals):
    return statistics.median(vals) if vals else 0

def ci95(vals):
    if len(vals) < 2:
        return (0, 0)
    m = mean(vals)
    s = std(vals)
    margin = 1.96 * s / math.sqrt(len(vals))
    return (m - margin, m + margin)

def main():
    rows = load_data()
    N = len(rows)
    print(f"=" * 70)
    print(f"VESPER AUTONOMOUS EVALUATION — {N} SCENES ANALYSIS")
    print(f"=" * 70)

    # ===== SCENE OVERVIEW =====
    print(f"\n{'='*70}")
    print("1. SCENE OVERVIEW")
    print(f"{'='*70}")
    rooms = [r['num_rooms'] for r in rows]
    devices = [r['num_devices'] for r in rows]
    automations = [r['num_automations'] for r in rows]
    sensors = [r['num_firmware_sensors'] for r in rows]
    navmesh = [r['navmesh_area_m2'] for r in rows]
    
    print(f"  Scenes: {N}")
    print(f"  Rooms:      min={min(rooms)}, max={max(rooms)}, mean={mean(rooms):.1f}, median={median(rooms):.0f}")
    print(f"  Devices:    min={min(devices)}, max={max(devices)}, mean={mean(devices):.1f}, median={median(devices):.0f}")
    print(f"  Automations: min={min(automations)}, max={max(automations)}, mean={mean(automations):.1f}")
    print(f"  FW Sensors: min={min(sensors)}, max={max(sensors)}, mean={mean(sensors):.1f}")
    print(f"  Navmesh area: min={min(navmesh):.1f}, max={max(navmesh):.1f}, mean={mean(navmesh):.1f} m²")
    
    # ===== NAVIGATION =====
    print(f"\n{'='*70}")
    print("2. NAVIGATION PERFORMANCE")
    print(f"{'='*70}")
    # Exclude scenes with 0 trials (failed to start nav)
    nav_rows = [r for r in rows if r['nav_trials'] > 0]
    nav_fail_rows = [r for r in rows if r['nav_trials'] == 0]
    
    all_trials = [r['nav_trials'] for r in nav_rows]
    all_success = [r['nav_success_rate'] for r in nav_rows]
    all_spl = [r['mean_spl'] for r in nav_rows]
    total_trials = sum(all_trials)
    
    # Weighted success rate
    weighted_success = sum(r['nav_trials'] * r['nav_success_rate'] for r in nav_rows) / total_trials if total_trials > 0 else 0
    
    print(f"  Scenes with navigation: {len(nav_rows)}/{N}")
    print(f"  Scenes without navigation (0 trials): {len(nav_fail_rows)}")
    if nav_fail_rows:
        print(f"    Failed scene IDs: {[r['scene_id'] for r in nav_fail_rows]}")
    print(f"  Total nav trials: {total_trials}")
    print(f"  Trials per scene: min={min(all_trials)}, max={max(all_trials)}, mean={mean(all_trials):.1f}")
    print(f"  Weighted success rate: {weighted_success:.4f} ({weighted_success*100:.1f}%)")
    print(f"  Per-scene success rate: mean={mean(all_success):.4f}, std={std(all_success):.4f}")
    ci = ci95(all_success)
    print(f"    95% CI: [{ci[0]:.4f}, {ci[1]:.4f}]")
    print(f"  Mean SPL: {mean(all_spl):.4f}")
    
    # ===== ROOM COVERAGE =====
    print(f"\n{'='*70}")
    print("3. ROOM COVERAGE")
    print(f"{'='*70}")
    coverage = [r['room_coverage'] for r in nav_rows]
    print(f"  Room coverage: mean={mean(coverage):.4f} ({mean(coverage)*100:.1f}%)")
    print(f"    min={min(coverage):.4f}, max={max(coverage):.4f}")
    print(f"    median={median(coverage):.4f}")
    ci = ci95(coverage)
    print(f"    95% CI: [{ci[0]:.4f}, {ci[1]:.4f}]")
    
    # ===== LLM ACTIVITY GENERATION =====
    print(f"\n{'='*70}")
    print("4. LLM ACTIVITY GENERATION")
    print(f"{'='*70}")
    tasks_sched = [r['tasks_scheduled'] for r in rows]
    tasks_nav = [r['tasks_navigated'] for r in rows]
    total_sched = sum(tasks_sched)
    total_nav = sum(tasks_nav)
    
    print(f"  Total scheduled tasks: {total_sched}")
    print(f"  Total navigated tasks: {total_nav}")
    print(f"  Tasks per scene: mean={mean(tasks_sched):.1f}, min={min(tasks_sched)}, max={max(tasks_sched)}")
    print(f"  Task completion rate: {total_nav/total_sched*100:.1f}%" if total_sched > 0 else "  N/A")
    
    # ===== SMARTTHINGS / FIRMWARE =====
    print(f"\n{'='*70}")
    print("5. SMARTTHINGS & FIRMWARE INTEGRATION")
    print(f"{'='*70}")
    st_toggles = [r['st_proximity_toggles'] for r in rows]
    st_pushes = [r['st_cloud_pushes'] for r in rows]
    containers = [r['st_docker_containers'] for r in rows]
    ngrok = [r['st_ngrok_connected'] for r in rows]
    
    total_toggles = sum(st_toggles)
    total_pushes = sum(st_pushes)
    
    print(f"  Docker containers per scene: {mean(containers):.1f} (typically 6)")
    print(f"  ngrok connected: {sum(ngrok)}/{N} scenes")
    print(f"  Total proximity toggles: {total_toggles}")
    print(f"    Per scene: mean={mean(st_toggles):.0f}, min={min(st_toggles)}, max={max(st_toggles)}")
    print(f"  Total cloud pushes: {total_pushes}")
    print(f"    Per scene: mean={mean(st_pushes):.0f}")
    print(f"  Push success rate: {total_pushes/total_toggles*100:.1f}%" if total_toggles > 0 else "")
    
    # ===== ARTICULATED OBJECTS =====
    print(f"\n{'='*70}")
    print("6. ARTICULATED OBJECT INTERACTIONS")
    print(f"{'='*70}")
    art_obj = [r['num_articulated_objects'] for r in rows]
    art_int = [r['articulated_interactions'] for r in rows]
    total_art = sum(art_int)
    
    print(f"  Total articulated objects across scenes: {sum(art_obj)}")
    print(f"  Per scene: mean={mean(art_obj):.1f}, min={min(art_obj)}, max={max(art_obj)}")
    print(f"  Total interactions: {total_art}")
    print(f"  Interactions per scene: mean={mean(art_int):.1f}, min={min(art_int)}, max={max(art_int)}")
    
    # ===== MOTION / SENSOR DETECTION =====
    print(f"\n{'='*70}")
    print("7. MOTION & SENSOR DETECTION")
    print(f"{'='*70}")
    motion_det = [r['motion_detections'] for r in rows]
    camera_ev = [r['camera_tracking_events'] for r in rows]
    total_motion = [r['total_motion_events'] for r in rows]
    
    print(f"  Total motion detections: {sum(motion_det)}")
    print(f"  Total camera events: {sum(camera_ev)}")
    print(f"  Total motion events: {sum(total_motion)}")
    print(f"  Per scene: mean={mean(total_motion):.0f}, min={min(total_motion)}, max={max(total_motion)}")
    
    # ===== SECURITY ATTACKS =====
    print(f"\n{'='*70}")
    print("8. SECURITY ASSESSMENT")
    print(f"{'='*70}")
    fw_run = [r['firmware_attacks_run'] for r in rows]
    fw_succ = [r['firmware_attacks_success'] for r in rows]
    fw_cat = [r['firmware_attack_categories_hit'] for r in rows]
    net_run = [r['network_attacks_run'] for r in rows]
    net_succ = [r['network_attacks_success'] for r in rows]
    net_cat = [r['network_attack_categories_hit'] for r in rows]
    pd_run = [r['phantom_delay_attacks_run'] for r in rows]
    pd_succ = [r['phantom_delay_attacks_success'] for r in rows]
    pd_cvss = [r['phantom_delay_mean_cvss'] for r in rows]
    total_run = [r['total_attacks_run'] for r in rows]
    total_succ = [r['total_attacks_success'] for r in rows]
    sec_score = [r['security_score'] for r in rows]
    
    grand_total_run = sum(total_run)
    grand_total_succ = sum(total_succ)
    
    print(f"  --- Per Scene (consistent across all scenes) ---")
    print(f"  Firmware attacks/scene: {fw_run[0]} ({fw_succ[0]} successful, {fw_cat[0]} categories)")
    print(f"  Network attacks/scene: {net_run[0]} ({net_succ[0]} successful, {net_cat[0]} categories)")
    print(f"  Phantom delay attacks/scene: {pd_run[0]} (mean CVSS: {pd_cvss[0]})")
    print(f"  Total attacks/scene: {total_run[0]}")
    print()
    print(f"  --- Aggregate Across {N} Scenes ---")
    print(f"  Grand total attacks run: {grand_total_run}")
    print(f"  Grand total successful: {grand_total_succ}")
    print(f"  Overall exploit rate: {grand_total_succ/grand_total_run*100:.1f}%")
    print()
    print(f"  Firmware exploit rate: {sum(fw_succ)/sum(fw_run)*100:.1f}%")
    print(f"  Network exploit rate: {sum(net_succ)/sum(net_run)*100:.1f}%")
    print(f"  Phantom delay exploit rate: {sum(pd_succ)/sum(pd_run)*100:.1f}%")
    print()
    print(f"  Security score: mean={mean(sec_score):.1f}%, min={min(sec_score):.1f}%, max={max(sec_score):.1f}%")
    ci = ci95(sec_score)
    print(f"    95% CI: [{ci[0]:.1f}%, {ci[1]:.1f}%]")
    
    # Per-scene variation in fw attacks
    fw_succ_rates = [r['firmware_attacks_success']/r['firmware_attacks_run']*100 for r in rows if r['firmware_attacks_run'] > 0]
    net_succ_rates = [r['network_attacks_success']/r['network_attacks_run']*100 for r in rows if r['network_attacks_run'] > 0]
    
    print(f"\n  FW attack success rates across scenes: mean={mean(fw_succ_rates):.1f}%, std={std(fw_succ_rates):.1f}%")
    print(f"  Network attack success rates: mean={mean(net_succ_rates):.1f}%, std={std(net_succ_rates):.1f}%")
    
    # ===== DURATION =====
    print(f"\n{'='*70}")
    print("9. EVALUATION DURATION")
    print(f"{'='*70}")
    durations = [r['eval_duration_sec'] for r in rows]
    total_dur = sum(durations)
    
    print(f"  Total eval time: {total_dur:.0f}s = {total_dur/3600:.1f} hours")
    print(f"  Per scene: mean={mean(durations):.0f}s ({mean(durations)/60:.1f} min), min={min(durations):.0f}s, max={max(durations):.0f}s")
    print(f"  Median: {median(durations):.0f}s")
    
    # ===== CORRELATIONS =====
    print(f"\n{'='*70}")
    print("10. NOTABLE CORRELATIONS")
    print(f"{'='*70}")
    
    # Toggles vs navmesh area (expect negative = compact = more toggles)
    nav_only = [r for r in rows if r['nav_trials'] > 0 and r['st_proximity_toggles'] > 0]
    if len(nav_only) > 2:
        x = [r['navmesh_area_m2'] for r in nav_only]
        y = [r['st_proximity_toggles'] for r in nav_only]
        n = len(x)
        mx, my = mean(x), mean(y)
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (n - 1)
        sx, sy = std(x), std(y)
        r_val = cov / (sx * sy) if sx > 0 and sy > 0 else 0
        print(f"  Navmesh area vs toggles (Pearson r): {r_val:.3f}")
    
    # Rooms vs devices
    x = [r['num_rooms'] for r in rows]
    y = [r['num_devices'] for r in rows]
    n = len(x)
    mx, my = mean(x), mean(y)
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (n - 1)
    sx, sy = std(x), std(y)
    r_val = cov / (sx * sy) if sx > 0 and sy > 0 else 0
    print(f"  Rooms vs devices (Pearson r): {r_val:.3f}")
    
    # Duration vs rooms
    y = [r['eval_duration_sec'] for r in rows]
    cov = sum((xi - mx) * (yi - mean(y)) for xi, yi in zip(x, y)) / (n - 1)
    r_val = cov / (std(x) * std(y)) if std(x) > 0 and std(y) > 0 else 0
    print(f"  Rooms vs duration (Pearson r): {r_val:.3f}")
    
    # ===== SUMMARY FOR PAPER =====
    print(f"\n{'='*70}")
    print("PAPER-READY SUMMARY NUMBERS")
    print(f"{'='*70}")
    
    completed_full = len([r for r in rows if r['nav_trials'] > 0])
    
    print(f"  Scenes evaluated: {N}")
    print(f"  Scenes with full navigation: {completed_full}/{N} ({completed_full/N*100:.1f}%)")
    print(f"  Total navigation trials: {total_trials}")
    print(f"  Navigation success rate: {weighted_success*100:.1f}%")
    print(f"  Mean SPL: {mean(all_spl):.3f}")
    print(f"  Room coverage: {mean(coverage)*100:.1f}%")
    print(f"  Total scheduled tasks: {total_sched}")
    print(f"  Total SmartThings toggles: {total_toggles}")
    print(f"  Total cloud pushes: {total_pushes}")
    print(f"  Total attacks run: {grand_total_run}")
    print(f"  Total attacks successful: {grand_total_succ}")
    print(f"  Overall exploit rate: {grand_total_succ/grand_total_run*100:.1f}%")
    print(f"  Mean security score: {mean(sec_score):.1f}%")
    print(f"  Total evaluation time: {total_dur/3600:.1f} hours")
    print(f"  Total articulated interactions: {total_art}")
    print(f"  Total motion events: {sum(total_motion)}")


if __name__ == "__main__":
    main()
