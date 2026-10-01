#!/usr/bin/env python3
"""Analyze 30-scene batch results for paper updates."""
import json, numpy as np
from scipy import stats as st

data = json.load(open('results/vesper_autonomous_eval/eval_results.json'))

def nav_count(s):
    nt = s['nav_trials']
    return len(nt) if isinstance(nt, list) else (nt or 0)

def nav_succ_count(s):
    nt = s['nav_trials']
    if isinstance(nt, list):
        return sum(1 for t in nt if t.get('success', False))
    return 0

active = [s for s in data if nav_count(s) > 0]
navfail_scenes = [s for s in data if nav_count(s) == 0]

total_trials = sum(nav_count(s) for s in data)
total_succ = sum(nav_succ_count(s) for s in data)
total_tgl = sum(s['st_proximity_toggles'] for s in data)
total_tasks_sched = sum(s['tasks_scheduled'] for s in data)
total_tasks_nav = sum(s['tasks_navigated'] for s in data)
total_dur = sum(s['eval_duration_sec'] for s in data)
total_art = sum(s.get('articulated_interactions', 0) for s in data)
total_art_obj = sum(s.get('num_articulated_objects', 0) for s in data)

print(f"=== CORE AGGREGATES ===")
print(f"Scenes: {len(data)}, Active: {len(active)}, NavFail: {len(navfail_scenes)}")
print(f"NavFail IDs: {[s['scene_id'] for s in navfail_scenes]}")
print(f"Nav trials: {total_trials}, successes: {total_succ}, rate: {total_succ/total_trials*100:.1f}%")
print(f"Toggles: {total_tgl}")
print(f"Tasks sched: {total_tasks_sched}, nav: {total_tasks_nav}")
print(f"Task completion: {total_tasks_nav/total_tasks_sched*100:.1f}%")
print(f"Wall-clock: {total_dur/3600:.1f}h")
print(f"Art objects: {total_art_obj}, interactions: {total_art}")

rooms = [s['num_rooms'] for s in data]
devs = [s['num_devices'] for s in data]
print(f"\nRooms: mean={np.mean(rooms):.1f} [{min(rooms)},{max(rooms)}]")
print(f"Devices: mean={np.mean(devs):.1f} [{min(devs)},{max(devs)}]")

nr = [s['nav_success_rate']*100 if s['nav_success_rate']<=1 else s['nav_success_rate'] for s in active]
nav_ci = st.t.interval(0.95, len(nr)-1, loc=np.mean(nr), scale=st.sem(nr))
print(f"\nNav rate (active): {np.mean(nr):.1f}% CI:[{nav_ci[0]:.1f},{nav_ci[1]:.1f}]")

ca = [s['room_coverage']*100 if s['room_coverage']<=1 else s['room_coverage'] for s in active]
cov_ci = st.t.interval(0.95, len(ca)-1, loc=np.mean(ca), scale=st.sem(ca))
print(f"Coverage (active): {np.mean(ca):.1f}% CI:[{cov_ci[0]:.1f},{cov_ci[1]:.1f}]")

da = [s['eval_duration_sec'] for s in data]
print(f"Duration: mean={np.mean(da):.0f}s={np.mean(da)/60:.1f}min [{min(da):.0f},{max(da):.0f}]")

ta = [s['st_proximity_toggles'] for s in data]
print(f"Toggles: mean={np.mean(ta):.1f} [{min(ta)},{max(ta)}]")

tp = [s['tasks_scheduled'] for s in data]
print(f"Tasks/scene: mean={np.mean(tp):.1f} [{min(tp)},{max(tp)}]")
tap_tasks = [s['tasks_scheduled'] for s in active]
print(f"Tasks/active: mean={np.mean(tap_tasks):.1f}")

# Security
fw_t = sum(s['firmware_attacks_run'] for s in data)
fw_s = sum(s['firmware_attacks_success'] for s in data)
net_t = sum(s['network_attacks_run'] for s in data)
net_s = sum(s['network_attacks_success'] for s in data)
pd_t = sum(s['phantom_delay_attacks_run'] for s in data)
pd_s = sum(s['phantom_delay_attacks_success'] for s in data)
all_t = fw_t+net_t+pd_t; all_s = fw_s+net_s+pd_s
print(f"\n=== SECURITY ===")
print(f"Firmware: {fw_s}/{fw_t} ({fw_s/fw_t*100:.1f}%)")
print(f"Network: {net_s}/{net_t} ({net_s/net_t*100:.1f}%)")
print(f"Phantom: {pd_s}/{pd_t} ({pd_s/pd_t*100:.1f}%)")
print(f"3-suite: {all_s}/{all_t} ({all_s/all_t*100:.1f}%)")
print(f"+standalone: {all_s+2}/{all_t+2} ({(all_s+2)/(all_t+2)*100:.1f}%)")

fw_r = [s['firmware_attacks_success']/s['firmware_attacks_run']*100 for s in data if s['firmware_attacks_run']>0]
net_r = [s['network_attacks_success']/s['network_attacks_run']*100 for s in data if s['network_attacks_run']>0]
sec_r = [s['total_attacks_success']/s['total_attacks_run']*100 for s in data if s['total_attacks_run']>0]
print(f"FW std: {np.std(fw_r):.1f}%, Net std: {np.std(net_r):.1f}%, Sec std: {np.std(sec_r):.1f}%")

sec_ci = st.t.interval(0.95, len(sec_r)-1, loc=np.mean(sec_r), scale=st.sem(sec_r))
print(f"Sec mean: {np.mean(sec_r):.1f}% CI:[{sec_ci[0]:.1f},{sec_ci[1]:.1f}]")

# TAP
tap_fires = sum(s.get('tap_rules_fired',0) for s in data)
tap_actions = sum(s.get('tap_actions_executed',0) for s in data)
tap_succeeded = sum(s.get('tap_actions_succeeded',0) for s in data)
tap_lats = [s.get('tap_mean_latency_ms',0) for s in data if s.get('tap_rules_fired',0)>0]
tap_p95s = [s.get('tap_p95_latency_ms',0) for s in data if s.get('tap_rules_fired',0)>0]
print(f"\n=== TAP ===")
print(f"Total fires: {tap_fires}, actions: {tap_actions}, succeeded: {tap_succeeded}")
if tap_lats:
    print(f"Mean latency: {np.mean(tap_lats):.2f}ms, P95: {np.mean(tap_p95s):.2f}ms")
print(f"Scenes w/fires: {sum(1 for s in data if s.get('tap_rules_fired',0)>0)}")

# PCAP
total_pkts = sum(s.get('pcap_total_packets',0) for s in data)
total_bytes = sum(s.get('pcap_total_bytes',0) for s in data)
total_pcap_files = sum(len(s.get('pcap_files',[])) if isinstance(s.get('pcap_files'), list) else s.get('pcap_files',0) for s in data)
print(f"\n=== PCAP ===")
print(f"Packets: {total_pkts}, Bytes: {total_bytes} ({total_bytes/1024/1024:.1f}MB)")
print(f"Pcap files: {total_pcap_files}")

# Motion
total_motion = sum(s.get('total_motion_events',0) for s in data)
total_detect = sum(s.get('motion_detections',0) for s in data)
print(f"\n=== MOTION ===")
print(f"Events: {total_motion}, Detections: {total_detect}, Avg/scene: {total_motion/len(data):.0f}")

print(f"\nAutomations/scene: {np.mean([s.get('num_automations',0) for s in data]):.1f}")

# Per-scene table
print(f"\n=== PER-SCENE ===")
for i,s in enumerate(sorted(data, key=lambda x: x['scene_id']),1):
    nt = nav_count(s)
    ns = nav_succ_count(s)
    nr2 = (ns/nt*100) if nt>0 else 0
    cov = s['room_coverage']*100 if s['room_coverage']<=1 else s['room_coverage']
    secr = (s['total_attacks_success']/s['total_attacks_run']*100) if s['total_attacks_run']>0 else 0
    mark = 'd' if nt==0 else ' '
    print(f"{i:>2}{mark} R={s['num_rooms']:>2} D={s['num_devices']:>2} Nav={nt:>3} {nr2:>5.1f}% Art={s.get('articulated_interactions',0):>5} Tgl={s['st_proximity_toggles']:>5} Cov={cov:>5.1f}% Sec={secr:>5.1f}% Dur={s['eval_duration_sec']:>8.0f}s Tsk={s['tasks_scheduled']:>3} NM={s.get('navmesh_area_m2',0):>6.0f}")
