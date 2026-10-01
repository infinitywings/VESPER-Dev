#!/usr/bin/env python3
"""
VESPER Smoke Test — Validate Full Pipeline Before Big Batch

Purpose: Run 1 scene, 1 day to verify everything works end-to-end before
committing to a 30-scene × 7-day evaluation (which can take 4-5 days).

Pipeline per scene:
  Phase 0: Setup (load scene, init IoT, init LLM, init sensors)
  Phase 1: BASELINE — Humanoid does LLM tasks, collect nav metrics (no attacks)
  Phase 2: ATTACK  — Launch 5 attack suites while humanoid continues tasks
  Phase 3: POST-ATTACK — Humanoid continues tasks, measure degraded performance
  Phase 4: Packet capture summary + data export

The key insight for MobiCom: We measure humanoid task performance BEFORE and
DURING attacks so we can show how attacks degrade smart-home functionality.

Usage:
    conda activate vesper
    # Quick sanity (1 scene, 1 day, headless):
    python scripts/run_smoke_test.py --headless

    # With display:
    python scripts/run_smoke_test.py

    # Full batch (after smoke test passes):
    python scripts/run_smoke_test.py --num-scenes 30 --num-days 7 --headless
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import signal
import subprocess
import sys
import threading
import time as time_module
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ── LLM defaults (local LM Studio) ───────────────────────────────────────
os.environ.setdefault("OPENWEBUI_URL", "http://localhost:1234/v1/chat/completions")
os.environ.setdefault("OPENWEBUI_API_KEY", "lm-studio")

# ── Project root ──────────────────────────────────────────────────────────
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

# ── Logging ───────────────────────────────────────────────────────────────
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "smoke_test.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("vesper.smoke")

# ── Results directory ─────────────────────────────────────────────────────
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results", "smoke_test")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ── Safe imports with clear error messages ────────────────────────────────
def _safe_import(label, fn):
    try:
        return fn()
    except Exception as e:
        logger.error(f"Import failed for {label}: {e}")
        return None

HAS_HABITAT = False
HAS_PYGAME = False

try:
    import habitat_sim
    import magnum as mn
    HAS_HABITAT = True
except ImportError:
    logger.warning("habitat_sim not available — 3D simulation disabled")

try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    logger.warning("pygame not available — headless mode only")

# ── VESPER imports ────────────────────────────────────────────────────────
from vesper.attacks.firmware_attacks import (
    FirmwareAttackFramework, FirmwareTarget, AttackResult, AttackSeverity,
)
from vesper.attacks.network_attacks import (
    NetworkAttackFramework, NetworkTarget, NetworkAttackResult,
)
from vesper.attacks.phantom_delay_attack import (
    PhantomDelayAttackSuite, DelayAttackResult,
    DelayAttackVariant, PhantomDelayCategory,
)

try:
    from vesper.attacks.wifi_attacks import WiFiAttackFramework, WiFiAttackResult
    HAS_WIFI_ATTACKS = True
except Exception:
    HAS_WIFI_ATTACKS = False

from vesper.core.event_bus import EventBus, Event, EventPriority


# ══════════════════════════════════════════════════════════════════════════
# DATA CLASSES FOR RESULTS
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class NavigationTrial:
    """One humanoid navigation task."""
    trial_id: int = 0
    task_name: str = ""
    source_room: str = ""
    target_room: str = ""
    success: bool = False
    num_steps: int = 0
    navigation_time_sec: float = 0.0
    geodesic_distance: float = 0.0
    actual_distance: float = 0.0
    spl: float = 0.0
    phase: str = "baseline"  # "baseline", "under_attack", "post_attack"
    attack_active: str = ""  # which attack was running during this trial


@dataclass
class AttackTrialResult:
    """Result from a single attack execution."""
    attack_name: str = ""
    suite: str = ""  # "firmware", "network", "phantom_delay", "wifi"
    category: str = ""
    severity: str = ""
    success: bool = False
    duration_ms: float = 0.0
    impact: str = ""
    evidence: list = field(default_factory=list)
    cvss_score: float = 0.0
    packets_sent: int = 0
    packets_captured: int = 0
    pcap_file: str = ""  # path to packet capture for this attack


@dataclass
class PhaseMetrics:
    """Metrics for one evaluation phase (baseline / under_attack / post_attack)."""
    phase_name: str = ""
    nav_trials: int = 0
    nav_success: int = 0
    nav_success_rate: float = 0.0
    mean_spl: float = 0.0
    motion_events: int = 0
    automation_triggers: int = 0
    device_response_time_ms: float = 0.0  # mean IoT device response latency
    matter_updates: int = 0
    duration_sec: float = 0.0


@dataclass
class SceneResult:
    """Full results for one scene evaluation."""
    scene_id: str = ""
    scene_path: str = ""
    num_rooms: int = 0
    room_names: list = field(default_factory=list)
    num_devices: int = 0
    navmesh_area_m2: float = 0.0

    # Phase metrics (the key MobiCom contribution — before vs during attack)
    baseline_metrics: dict = field(default_factory=dict)
    attack_metrics: dict = field(default_factory=dict)
    post_attack_metrics: dict = field(default_factory=dict)

    # Navigation trials (all phases combined)
    nav_trials: list = field(default_factory=list)

    # Attack results
    attack_results: list = field(default_factory=list)
    total_attacks_run: int = 0
    total_attacks_success: int = 0
    vulnerability_rate: float = 0.0

    # Packet captures
    pcap_files: list = field(default_factory=list)

    # WiFi bridge stats
    wifi_bridge_stats: dict = field(default_factory=dict)

    # Schedule info
    tasks_scheduled: int = 0
    tasks_navigated: int = 0

    # Timing
    eval_duration_sec: float = 0.0


# ══════════════════════════════════════════════════════════════════════════
# PACKET CAPTURE (tshark)
# ══════════════════════════════════════════════════════════════════════════

class PacketCapture:
    """Manages tshark packet captures for evidence collection.

    Captures on the loopback interface (where Docker firmware containers
    communicate) and stores pcap files per attack phase.
    """

    def __init__(self, output_dir: str, interface: str = "lo0"):
        self.output_dir = output_dir
        self.interface = interface
        self._process: Optional[subprocess.Popen] = None
        self._current_file: Optional[str] = None
        os.makedirs(output_dir, exist_ok=True)

    def start(self, label: str, port_filter: Optional[int] = None) -> str:
        """Start capturing packets.

        Args:
            label: Name for this capture (used in filename)
            port_filter: Optional port to filter on

        Returns:
            Path to the pcap file being written
        """
        self.stop()  # stop any existing capture

        safe_label = label.replace(" ", "_").replace("/", "-")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pcap_file = os.path.join(self.output_dir, f"{ts}_{safe_label}.pcap")
        self._current_file = pcap_file

        cmd = [
            "tshark",
            "-i", self.interface,
            "-w", pcap_file,
            "-q",  # quiet
        ]
        if port_filter:
            cmd.extend(["-f", f"port {port_filter}"])

        try:
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            logger.info(f"📦 tshark capture started → {pcap_file}")
        except FileNotFoundError:
            logger.warning("tshark not found — packet capture disabled")
            self._process = None
        except PermissionError:
            logger.warning("tshark needs permissions — try: sudo chmod +x $(which tshark)")
            self._process = None

        return pcap_file

    def stop(self) -> Optional[str]:
        """Stop the current capture. Returns path to pcap file."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

        result = self._current_file
        self._current_file = None
        if result and os.path.exists(result):
            size = os.path.getsize(result)
            logger.info(f"📦 tshark capture stopped → {result} ({size:,} bytes)")
        return result

    def get_packet_count(self, pcap_file: str) -> int:
        """Count packets in a pcap file using tshark."""
        try:
            result = subprocess.run(
                ["tshark", "-r", pcap_file, "-T", "fields", "-e", "frame.number"],
                capture_output=True, text=True, timeout=30,
            )
            return len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
        except Exception:
            return 0

    def get_protocol_summary(self, pcap_file: str) -> Dict[str, int]:
        """Get protocol breakdown from a pcap file."""
        try:
            result = subprocess.run(
                ["tshark", "-r", pcap_file, "-z", "io,phs", "-q"],
                capture_output=True, text=True, timeout=30,
            )
            return {"raw_output": result.stdout[:2000]}
        except Exception:
            return {}


# ══════════════════════════════════════════════════════════════════════════
# ATTACK RUNNER — Runs all 5 attack suites with pcap
# ══════════════════════════════════════════════════════════════════════════

def run_attacks_with_capture(
    pcap: PacketCapture,
    fw_target: FirmwareTarget,
    net_target: NetworkTarget,
    scene_id: str,
) -> List[AttackTrialResult]:
    """Run all attack suites with per-suite packet capture.

    Five attack suites:
      1. Firmware Attacks (18 attacks: buffer overflow, auth bypass, DoS, etc.)
      2. Network Attacks  (14 attacks: Matter sniff/inject, TCP MITM, ARP, DNS, etc.)
      3. Phantom-Delay Attacks (3 attacks: Fu et al. DSN 2022)
      4. WiFi Attacks (7 attacks: deauth, evil twin, DHCP starvation — if available)
      5. Combined/cross-layer (firmware + network simultaneously)

    Returns list of AttackTrialResult with per-attack pcap file references.
    """
    all_results: List[AttackTrialResult] = []

    # ── Suite 1: Firmware Attacks ──────────────────────────────────────
    print(f"\n  ━━━ Suite 1/5: FIRMWARE ATTACKS (18 attacks) ━━━")
    pcap_file = pcap.start(f"firmware_attacks_{scene_id}", port_filter=fw_target.port)
    fw = FirmwareAttackFramework()
    try:
        fw_results = fw.run_all_attacks(fw_target)
    except Exception as e:
        logger.error(f"Firmware attacks error: {e}")
        fw_results = []
    pcap.stop()

    for r in fw_results:
        all_results.append(AttackTrialResult(
            attack_name=r.attack_name,
            suite="firmware",
            category=r.category.value,
            severity=r.severity.value,
            success=r.success,
            duration_ms=r.duration_ms,
            impact=r.impact,
            evidence=r.evidence[:3],
            pcap_file=pcap_file,
        ))
    ok = sum(1 for r in fw_results if r.success)
    print(f"  Result: {ok}/{len(fw_results)} vulnerabilities exploited")

    # ── Suite 2: Network Attacks ───────────────────────────────────────
    print(f"\n  ━━━ Suite 2/5: NETWORK ATTACKS (14 attacks) ━━━")
    pcap_file = pcap.start(f"network_attacks_{scene_id}")
    net = NetworkAttackFramework()
    try:
        net_results = net.run_all_attacks(net_target)
    except Exception as e:
        logger.error(f"Network attacks error: {e}")
        net_results = []
    pcap.stop()

    for r in net_results:
        all_results.append(AttackTrialResult(
            attack_name=r.attack_name,
            suite="network",
            category=r.category.value,
            success=r.success,
            duration_ms=r.duration_ms,
            impact=r.impact,
            evidence=r.evidence[:3],
            packets_sent=r.packets_sent,
            packets_captured=r.packets_captured,
            pcap_file=pcap_file,
        ))
    ok = sum(1 for r in net_results if r.success)
    print(f"  Result: {ok}/{len(net_results)} vulnerabilities exploited")

    # ── Suite 3: Phantom-Delay Attacks ─────────────────────────────────
    print(f"\n  ━━━ Suite 3/5: PHANTOM-DELAY ATTACKS (3 attacks) ━━━")
    pcap_file = pcap.start(f"phantom_delay_{scene_id}")
    pd_suite = PhantomDelayAttackSuite()
    pd_attacks = [
        ("State-Update Delay", "attack_state_update_delay", DelayAttackVariant.STATE_UPDATE_DELAY),
        ("Erroneous Execution", "attack_erroneous_execution", DelayAttackVariant.ERRONEOUS_EXECUTION),
        ("Action Reorder", "attack_action_reorder", DelayAttackVariant.ACTION_REORDER),
    ]
    for name, method, variant in pd_attacks:
        try:
            r = getattr(pd_suite, method)(net_target, delay_seconds=5.0)
            all_results.append(AttackTrialResult(
                attack_name=name,
                suite="phantom_delay",
                category=r.category.value,
                success=r.success,
                duration_ms=r.duration_ms or 0.0,
                impact=r.impact,
                cvss_score=r.cvss_score,
                evidence=r.evidence[:3],
                pcap_file=pcap_file,
            ))
            status = "✓" if r.success else "✗"
            print(f"    {status} {name} (CVSS {r.cvss_score})")
        except Exception as e:
            logger.error(f"    ✗ {name}: {e}")
            all_results.append(AttackTrialResult(
                attack_name=name, suite="phantom_delay",
                category="error", success=False,
            ))
    pcap.stop()

    # ── Suite 4: WiFi Attacks (if Mininet-WiFi available) ──────────────
    if HAS_WIFI_ATTACKS:
        print(f"\n  ━━━ Suite 4/5: WIFI ATTACKS (Layer 2 — 802.11) ━━━")
        print("    ⚠️  Requires Mininet-WiFi Docker container running")
        # WiFi attacks need the WiFiEmulator running — skip if not available
        all_results.append(AttackTrialResult(
            attack_name="WiFi Suite (deauth, evil twin, ARP, DNS, Matter, DHCP)",
            suite="wifi",
            category="requires_mininet_wifi",
            success=False,
            impact="Skipped — run with --with-wifi and Docker for full WiFi attacks",
        ))
    else:
        print(f"\n  ━━━ Suite 4/5: WIFI ATTACKS — skipped (no WiFiEmulator) ━━━")

    # ── Suite 5: Cross-layer combined ──────────────────────────────────
    print(f"\n  ━━━ Suite 5/5: CROSS-LAYER ATTACK (firmware + network simultaneous) ━━━")
    pcap_file = pcap.start(f"cross_layer_{scene_id}")

    # Run firmware DoS + Matter injection simultaneously in threads
    cross_results = []

    def _run_fw_dos():
        try:
            r = fw.attack_dos_command_flood(fw_target)
            cross_results.append(("fw_dos_flood", r.success, r.duration_ms))
        except Exception as e:
            cross_results.append(("fw_dos_flood", False, 0.0))

    def _run_matter_inject():
        try:
            r_list = net._matter_suite.run_all(net_target)
            ok = sum(1 for r in r_list if r.success)
            cross_results.append(("matter_all", ok > 0, sum(r.duration_ms for r in r_list)))
        except Exception as e:
            cross_results.append(("matter_all", False, 0.0))

    t1 = threading.Thread(target=_run_fw_dos)
    t2 = threading.Thread(target=_run_matter_inject)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    pcap.stop()

    for name, success, dur in cross_results:
        all_results.append(AttackTrialResult(
            attack_name=f"cross_layer_{name}",
            suite="cross_layer",
            category="combined",
            success=success,
            duration_ms=dur,
            pcap_file=pcap_file,
        ))
    ok = sum(1 for _, s, _ in cross_results if s)
    print(f"  Result: {ok}/{len(cross_results)} cross-layer attacks succeeded")

    return all_results


# ══════════════════════════════════════════════════════════════════════════
# SCENE DISCOVERY
# ══════════════════════════════════════════════════════════════════════════

def find_scenes(max_scenes: int = 1, seed: int = 42) -> List[Tuple[str, Optional[str]]]:
    """Find HSSD-HAB scenes."""
    data_path = os.path.join(PROJECT_ROOT, "data")
    hssd_artic = os.path.join(data_path, "scene_datasets", "hssd-hab", "scenes-articulated")
    hssd_static = os.path.join(data_path, "scene_datasets", "hssd-hab", "scenes")
    hssd_path = hssd_artic if os.path.exists(hssd_artic) else hssd_static

    config_name = ("hssd-hab-articulated.scene_dataset_config.json"
                   if hssd_path == hssd_artic else "hssd-hab.scene_dataset_config.json")
    config_path = os.path.join(data_path, "scene_datasets", "hssd-hab", config_name)

    scene_files = sorted(
        f for f in os.listdir(hssd_path) if f.endswith(".scene_instance.json")
    )
    if not scene_files:
        raise FileNotFoundError(f"No scenes in {hssd_path}")

    rng = np.random.RandomState(seed)
    if max_scenes < len(scene_files):
        indices = rng.choice(len(scene_files), size=max_scenes, replace=False)
        indices.sort()
        scene_files = [scene_files[i] for i in indices]

    scenes = [
        (os.path.join(hssd_path, sf), config_path if os.path.exists(config_path) else None)
        for sf in scene_files
    ]
    print(f"🏠 Selected {len(scenes)} scenes for evaluation")
    return scenes


# ══════════════════════════════════════════════════════════════════════════
# MAIN EVALUATION LOGIC
# ══════════════════════════════════════════════════════════════════════════

def evaluate_scene(
    scene_path: str,
    config_path: Optional[str],
    args: argparse.Namespace,
) -> SceneResult:
    """Run the full evaluation pipeline for one scene.

    Pipeline:
      Phase 0: Setup (simulator, IoT, sensors, LLM schedule)
      Phase 1: BASELINE nav — humanoid does tasks, no attacks
      Phase 2: ATTACK nav  — attacks launched while humanoid continues
      Phase 3: POST-ATTACK — humanoid finishes remaining tasks
      Phase 4: Export data + pcap summary
    """
    scene_id = os.path.basename(scene_path).split(".")[0]
    result = SceneResult(scene_id=scene_id, scene_path=scene_path)
    t0 = time_module.time()

    print(f"\n{'='*70}")
    print(f"  SCENE: {scene_id}")
    print(f"{'='*70}")

    if not HAS_HABITAT:
        print("  ⚠️  habitat_sim not available — running attack-only mode")
        return _run_attack_only(result, args)

    # ── Phase 0: Setup ─────────────────────────────────────────────────
    print("\n  Phase 0: Setting up simulator...")

    # Import the eval script's classes (they have all the heavy logic)
    # We use the existing ObjectNavDemo from run_autonomous_eval.py
    try:
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
        from run_autonomous_eval import (
            ObjectNavDemo, GameUI, SmartThings3DBridge,
            NavigationTrial as EvalNavTrial,
            _finalise_trial, _guess_current_room,
            find_scenes as _find_scenes_orig,
        )
    except ImportError as e:
        logger.error(f"Cannot import run_autonomous_eval: {e}")
        return _run_attack_only(result, args)

    demo = ObjectNavDemo()
    demo._current_scene_path = scene_path
    demo.use_wifi = args.with_wifi

    try:
        demo.sim = demo.create_simulator(scene_path, config_path)
    except Exception as e:
        logger.error(f"Failed to create simulator: {e}")
        result.eval_duration_sec = time_module.time() - t0
        return result

    demo.agent = demo.sim.get_agent(0)
    demo.init_path_follower()
    demo.init_llm_client()

    if args.model and demo.llm_client:
        demo.llm_client.config.model = args.model

    # Navmesh
    pf = demo.sim.pathfinder
    if pf.is_loaded:
        bounds = pf.get_bounds()
        result.navmesh_area_m2 = float(
            (bounds[1][0] - bounds[0][0]) * (bounds[1][2] - bounds[0][2])
        )

    # Extract rooms — same pattern as run_autonomous_eval.py
    room_positions = demo.get_room_positions()
    if "hssd" in scene_path.lower():
        hssd_rooms, _ = demo.load_hssd_semantics(scene_path)
        room_positions.update(hssd_rooms)

    room_pos_dict = {}
    for room, positions in room_positions.items():
        if positions:
            pos = positions[0]
            room_pos_dict[room] = (float(pos[0]), float(pos[1]), float(pos[2]))

    # Place agent in a random room for better navigation success
    import random as _rand
    _rand.seed(42)
    start_pos = demo.sim.pathfinder.get_random_navigable_point()
    if room_pos_dict:
        random_room = _rand.choice(list(room_pos_dict.keys()))
        candidate_pos = mn.Vector3(*room_pos_dict[random_room])
        start_pos = demo.sim.pathfinder.snap_point(candidate_pos)
        logger.info(f"Spawning humanoid in room: {random_room} at {start_pos}")

    agent_state = habitat_sim.AgentState()
    agent_state.position = start_pos
    demo.agent.set_state(agent_state)

    # Filter to reachable rooms
    agent_st = demo.agent.get_state()
    spawn = agent_st.position
    reachable = {}
    for room, pos in room_pos_dict.items():
        path = habitat_sim.ShortestPath()
        path.requested_start = spawn
        path.requested_end = mn.Vector3(*pos)
        if pf.find_path(path):
            reachable[room] = pos
        else:
            logger.debug(f"Filtering out unreachable room: {room}")
    logger.info(f"Reachable rooms: {len(reachable)}/{len(room_pos_dict)}")
    room_pos_dict = reachable if reachable else room_pos_dict

    result.num_rooms = len(room_pos_dict)
    result.room_names = list(room_pos_dict.keys())
    print(f"  Rooms: {result.num_rooms} ({', '.join(list(room_pos_dict.keys())[:5])}...)")

    if result.num_rooms == 0:
        logger.warning("No rooms — skipping")
        demo.sim.close()
        return result

    # Humanoid
    demo.load_humanoid()

    # VESPER IoT
    demo.init_vesper_integration(list(room_pos_dict.keys()), room_pos_dict)
    if demo.vesper and demo.vesper.iot_manager:
        result.num_devices = len(demo.vesper.iot_manager.devices)

    # SmartThings Docker firmware (needed for attacks)
    if args.with_smartthings:
        print("  Starting SmartThings Docker bridge...")
        try:
            demo.smartthings_bridge = SmartThings3DBridge()
            demo.smartthings_bridge.start(room_pos_dict)
            time_module.sleep(5)  # wait for containers
            n_devs = len(demo.smartthings_bridge.firmware_devices)
            print(f"  ✅ {n_devs} firmware containers running")
        except Exception as e:
            logger.error(f"SmartThings bridge failed: {e}")
            demo.smartthings_bridge = None

    # Set time scale (sensors + autonomous sim already init'd in init_vesper_integration)
    demo.time_manager._time_scale = args.time_scale

    # UI
    ui = GameUI(demo, headless=args.headless)
    ui.init_pygame()
    ui.available_targets = list(room_pos_dict.keys())

    # ── Packet capture setup ───────────────────────────────────────────
    scene_pcap_dir = os.path.join(RESULTS_DIR, f"pcap_{scene_id}")
    pcap = PacketCapture(scene_pcap_dir)

    # ── Phase 1: BASELINE Navigation ──────────────────────────────────
    print(f"\n  Phase 1: BASELINE navigation (no attacks)...")
    baseline_pcap = pcap.start(f"baseline_{scene_id}")
    baseline_metrics, baseline_trials = _run_navigation_phase(
        demo, ui, room_pos_dict, args,
        phase_name="baseline",
        max_frames=args.baseline_frames,
    )
    pcap.stop()
    result.baseline_metrics = asdict(baseline_metrics) if hasattr(baseline_metrics, '__dataclass_fields__') else baseline_metrics.__dict__
    result.nav_trials.extend(baseline_trials)
    print(f"    Nav: {baseline_metrics.nav_success}/{baseline_metrics.nav_trials} "
          f"({baseline_metrics.nav_success_rate:.0%}), SPL={baseline_metrics.mean_spl:.3f}")

    # ── Phase 2: ATTACK + Navigation ──────────────────────────────────
    print(f"\n  Phase 2: ATTACKS (while humanoid continues tasks)...")

    # Determine attack targets
    fw_target = None
    net_target = None
    if demo.smartthings_bridge and demo.smartthings_bridge.firmware_devices:
        endpoints = [(
            "127.0.0.1", fw.host_port
        ) for fw in demo.smartthings_bridge.firmware_devices.values()]
        first_host, first_port = endpoints[0]
        fw_target = FirmwareTarget(host=first_host, port=first_port)
        net_target = NetworkTarget(
            matter_bridge_url="http://127.0.0.1:8484",
            devices=endpoints,
            gateway_ip="172.20.0.1", subnet="172.20.0.0/24",
        )
    else:
        # No Docker containers — use localhost for attack framework testing
        # Attacks will mostly fail (expected) but the pipeline still runs
        fw_target = FirmwareTarget(host="127.0.0.1", port=19999)
        net_target = NetworkTarget(
            matter_bridge_url="http://127.0.0.1:8484",
            devices=[("127.0.0.1", 19999)],
            gateway_ip="192.168.4.1", subnet="192.168.4.0/24",
        )
        print("    ⚠️  No Docker firmware — attacks will target localhost (partial results)")

    # Run attacks in a background thread while humanoid continues
    attack_results_container: List[AttackTrialResult] = []
    attack_done = threading.Event()

    def _attack_thread():
        try:
            results = run_attacks_with_capture(pcap, fw_target, net_target, scene_id)
            attack_results_container.extend(results)
        except Exception as e:
            logger.error(f"Attack thread error: {e}")
        finally:
            attack_done.set()

    attack_thread = threading.Thread(target=_attack_thread, daemon=True)
    attack_thread.start()

    # Meanwhile, humanoid continues navigation
    attack_nav_metrics, attack_trials = _run_navigation_phase(
        demo, ui, room_pos_dict, args,
        phase_name="under_attack",
        max_frames=args.attack_frames,
        stop_event=attack_done,
    )
    result.attack_metrics = asdict(attack_nav_metrics) if hasattr(attack_nav_metrics, '__dataclass_fields__') else attack_nav_metrics.__dict__
    result.nav_trials.extend(attack_trials)

    # Wait for attacks to finish
    attack_thread.join(timeout=120)
    result.attack_results = [asdict(r) for r in attack_results_container]
    result.total_attacks_run = len(attack_results_container)
    result.total_attacks_success = sum(1 for r in attack_results_container if r.success)
    result.vulnerability_rate = (
        result.total_attacks_success / result.total_attacks_run * 100
        if result.total_attacks_run > 0 else 0.0
    )

    print(f"\n    Attacks: {result.total_attacks_success}/{result.total_attacks_run} exploitable "
          f"({result.vulnerability_rate:.0f}%)")
    print(f"    Nav during attacks: {attack_nav_metrics.nav_success}/{attack_nav_metrics.nav_trials} "
          f"({attack_nav_metrics.nav_success_rate:.0%})")

    # ── Phase 3: POST-ATTACK Navigation ───────────────────────────────
    print(f"\n  Phase 3: POST-ATTACK navigation...")
    post_pcap = pcap.start(f"post_attack_{scene_id}")
    post_metrics, post_trials = _run_navigation_phase(
        demo, ui, room_pos_dict, args,
        phase_name="post_attack",
        max_frames=args.post_attack_frames,
    )
    pcap.stop()
    result.post_attack_metrics = asdict(post_metrics) if hasattr(post_metrics, '__dataclass_fields__') else post_metrics.__dict__
    result.nav_trials.extend(post_trials)

    # ── WiFi bridge stats ──────────────────────────────────────────────
    if demo.vesper and demo.vesper.wifi_bridge:
        result.wifi_bridge_stats = dict(demo.vesper.wifi_bridge.stats)

    # Collect pcap files
    result.pcap_files = [
        f for f in os.listdir(scene_pcap_dir)
        if f.endswith(".pcap")
    ] if os.path.exists(scene_pcap_dir) else []

    # ── Phase 4: Summary ──────────────────────────────────────────────
    result.eval_duration_sec = time_module.time() - t0

    # Print comparison table
    print(f"\n  {'─'*60}")
    print(f"  📊 PERFORMANCE COMPARISON: {scene_id}")
    print(f"  {'─'*60}")
    print(f"  {'Phase':<20} {'Nav SR':>8} {'SPL':>8} {'Motion':>8} {'Auto':>8}")
    print(f"  {'─'*60}")
    for name, m in [("Baseline", baseline_metrics),
                    ("Under Attack", attack_nav_metrics),
                    ("Post-Attack", post_metrics)]:
        print(f"  {name:<20} {m.nav_success_rate:>7.0%} {m.mean_spl:>8.3f} "
              f"{m.motion_events:>8} {m.automation_triggers:>8}")
    print(f"  {'─'*60}")
    print(f"  Attacks: {result.total_attacks_success}/{result.total_attacks_run} "
          f"exploitable ({result.vulnerability_rate:.0f}%)")
    print(f"  Pcap files: {len(result.pcap_files)}")
    print(f"  Duration: {result.eval_duration_sec:.0f}s")

    # ── Cleanup ────────────────────────────────────────────────────────
    if demo.vesper:
        demo.vesper.close()
    if demo.smartthings_bridge:
        demo.smartthings_bridge.stop()
    demo.sim.close()
    if not args.headless and HAS_PYGAME:
        pygame.quit()

    return result


def _run_attack_only(result: SceneResult, args: argparse.Namespace) -> SceneResult:
    """Run attack frameworks without 3D simulation (for testing attacks work)."""
    print("  Running attacks in standalone mode (no 3D simulation)...")

    pcap_dir = os.path.join(RESULTS_DIR, f"pcap_{result.scene_id or 'standalone'}")
    pcap = PacketCapture(pcap_dir)

    fw_target = FirmwareTarget(host="127.0.0.1", port=19999)
    net_target = NetworkTarget(
        matter_bridge_url="http://127.0.0.1:8484",
        devices=[("127.0.0.1", 19999)],
        gateway_ip="192.168.4.1", subnet="192.168.4.0/24",
    )

    attack_results = run_attacks_with_capture(pcap, fw_target, net_target, "standalone")
    result.attack_results = [asdict(r) for r in attack_results]
    result.total_attacks_run = len(attack_results)
    result.total_attacks_success = sum(1 for r in attack_results if r.success)
    result.vulnerability_rate = (
        result.total_attacks_success / result.total_attacks_run * 100
        if result.total_attacks_run > 0 else 0.0
    )
    return result


def _run_navigation_phase(
    demo,
    ui,
    room_pos_dict: Dict[str, Tuple],
    args: argparse.Namespace,
    phase_name: str,
    max_frames: int,
    stop_event: Optional[threading.Event] = None,
) -> Tuple[PhaseMetrics, List[NavigationTrial]]:
    """Run one phase of navigation and collect metrics.

    Args:
        demo: ObjectNavDemo instance
        ui: GameUI instance
        room_pos_dict: room positions
        args: CLI args
        phase_name: "baseline", "under_attack", "post_attack"
        max_frames: max frames for this phase
        stop_event: optional event that signals attacks are done

    Returns:
        (PhaseMetrics, list of NavigationTrial)
    """
    metrics = PhaseMetrics(phase_name=phase_name)
    trials: List[NavigationTrial] = []
    t0 = time_module.time()

    frame = 0
    nav_steps = 0
    current_trial = None
    prev_task_id = None
    nav_start_pos = None
    nav_start_time = None
    nav_goal_pos = None
    trial_counter = len(trials)

    # Access habitat_sim and mn from outer scope
    import habitat_sim as _hs
    import magnum as _mn

    while frame < max_frames:
        frame += 1

        # Check if stop event fired (attacks done, move to next phase)
        if stop_event and stop_event.is_set() and frame > 100:
            break

        # Observations
        try:
            obs = demo.sim.get_sensor_observations()
        except Exception:
            break

        # Update simulation time
        time_info = demo.update_simulation_time()

        # Detect new task
        current_task = time_info.get("current_task")
        current_task_id = current_task.task_id if current_task else None

        if current_task_id and current_task_id != prev_task_id:
            # Finalize previous trial
            if current_trial is not None:
                trials.append(current_trial)

            trial_counter += 1
            target_room = current_task.location.room_name
            agent_st = demo.agent.get_state()
            nav_start_pos = tuple(agent_st.position)
            nav_start_time = time_module.time()
            nav_steps = 0
            nav_goal_pos = room_pos_dict.get(target_room)

            current_trial = NavigationTrial(
                trial_id=trial_counter,
                task_name=current_task.name,
                source_room=_guess_current_room_local(nav_start_pos, room_pos_dict),
                target_room=target_room,
                phase=phase_name,
            )
            prev_task_id = current_task_id

            if demo.current_goal:
                ui.goal_pos = demo.current_goal
                ui.goal_name = demo.current_goal_name
                ui.auto_navigate = True

        # VESPER IoT updates
        if demo.vesper:
            agent_st = demo.agent.get_state()
            agent_pos = tuple(agent_st.position)

            if demo.vesper.humanoid:
                quat = agent_st.rotation
                demo.vesper.update_humanoid(
                    agent_position=agent_pos,
                    agent_rotation=(quat.x, quat.y, quat.z, quat.w),
                )

            iot_events = demo.vesper.update_agent_position(agent_pos)
            for evt in iot_events:
                etype = evt.get("event_type", "")
                if etype == "motion_detected":
                    metrics.motion_events += 1
                elif etype == "automation_triggered":
                    metrics.automation_triggers += 1

        # Sensor updates
        if hasattr(demo, 'room_sensor_state') and demo.room_sensor_state:
            agent_st = demo.agent.get_state()
            sensor_detections = demo.update_sensors(tuple(agent_st.position), dt=0.016)
            metrics.motion_events += len(sensor_detections)

        # SmartThings proximity
        if demo.smartthings_bridge:
            agent_st = demo.agent.get_state()
            st_events = demo.smartthings_bridge.check_proximity_interaction(
                tuple(agent_st.position), time_module.time()
            )

        # Render
        ui.render_frame(obs)

        # Handle events (headless: skip)
        if not args.headless and HAS_PYGAME:
            action = ui.handle_events()
            if action == "quit":
                break
        else:
            action = None

        # Auto-navigation
        if ui.auto_navigate and ui.goal_pos is not None and action is None:
            auto_action, goal_reached = demo.get_action_to_goal(ui.goal_pos)
            if auto_action:
                demo.agent.act(auto_action)
                nav_steps += 1
                is_moving = auto_action in ["move_forward", "move_backward"]
                demo.update_humanoid_position(
                    is_moving=is_moving,
                    target_pos=_mn.Vector3(*ui.goal_pos),
                )
            else:
                if current_trial is not None:
                    current_trial.success = goal_reached
                    current_trial.num_steps = nav_steps
                    current_trial.navigation_time_sec = time_module.time() - (nav_start_time or time_module.time())
                    # Compute SPL
                    if goal_reached and nav_goal_pos:
                        agent_st = demo.agent.get_state()
                        end_pos = tuple(agent_st.position)
                        current_trial.actual_distance = float(np.linalg.norm(
                            np.array(end_pos) - np.array(nav_start_pos or end_pos)
                        ))
                        path = _hs.ShortestPath()
                        path.requested_start = _mn.Vector3(*(nav_start_pos or end_pos))
                        path.requested_end = _mn.Vector3(*nav_goal_pos)
                        if demo.sim.pathfinder.find_path(path):
                            current_trial.geodesic_distance = float(path.geodesic_distance)
                            if current_trial.actual_distance > 0 and current_trial.geodesic_distance > 0:
                                current_trial.spl = (
                                    current_trial.geodesic_distance /
                                    max(current_trial.actual_distance, current_trial.geodesic_distance)
                                )
                ui.clear_goal()

            # Timeout
            if nav_steps >= args.nav_timeout_steps:
                if current_trial is not None:
                    current_trial.success = False
                    current_trial.num_steps = nav_steps
                    current_trial.navigation_time_sec = time_module.time() - (nav_start_time or time_module.time())
                ui.clear_goal()

        if not args.headless and HAS_PYGAME and ui.clock:
            ui.clock.tick(60)

        # Check day complete
        if (demo.autonomous_sim and demo.autonomous_sim.current_task is None and
            demo.autonomous_sim.current_task_index >= len(
                demo.autonomous_sim.current_schedule.tasks
                if demo.autonomous_sim.current_schedule else []
            )):
            break

    # Finalize last trial
    if current_trial is not None:
        trials.append(current_trial)

    # Compute phase metrics
    metrics.nav_trials = len(trials)
    metrics.nav_success = sum(1 for t in trials if t.success)
    metrics.nav_success_rate = metrics.nav_success / metrics.nav_trials if metrics.nav_trials > 0 else 0.0
    spls = [t.spl for t in trials if t.spl > 0]
    metrics.mean_spl = sum(spls) / len(spls) if spls else 0.0
    metrics.duration_sec = time_module.time() - t0

    return metrics, trials


def _guess_current_room_local(pos, room_pos_dict):
    """Return closest room name."""
    best, best_d = "unknown", float("inf")
    for room, rpos in room_pos_dict.items():
        d = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos, rpos)))
        if d < best_d:
            best_d = d
            best = room
    return best


# ══════════════════════════════════════════════════════════════════════════
# RESULTS EXPORT
# ══════════════════════════════════════════════════════════════════════════

def write_results(results: List[SceneResult], output_dir: str):
    """Write comprehensive results for MobiCom paper."""
    os.makedirs(output_dir, exist_ok=True)

    # ── JSON (full data) ───────────────────────────────────────────────
    json_path = os.path.join(output_dir, "eval_results.json")
    with open(json_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2, default=str)
    print(f"📄 JSON: {json_path}")

    # ── Per-scene comparison CSV (the key MobiCom table) ───────────────
    csv_path = os.path.join(output_dir, "phase_comparison.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "scene_id", "num_rooms", "num_devices",
            "baseline_nav_sr", "baseline_spl", "baseline_motion", "baseline_auto",
            "attack_nav_sr", "attack_spl", "attack_motion", "attack_auto",
            "post_nav_sr", "post_spl", "post_motion", "post_auto",
            "delta_nav_sr", "delta_spl",
            "attacks_run", "attacks_success", "vuln_rate",
            "pcap_files", "duration_sec",
        ])
        for r in results:
            bm = r.baseline_metrics
            am = r.attack_metrics
            pm = r.post_attack_metrics

            b_sr = bm.get("nav_success_rate", 0)
            a_sr = am.get("nav_success_rate", 0)
            b_spl = bm.get("mean_spl", 0)
            a_spl = am.get("mean_spl", 0)

            w.writerow([
                r.scene_id, r.num_rooms, r.num_devices,
                f"{b_sr:.4f}", f"{b_spl:.4f}",
                bm.get("motion_events", 0), bm.get("automation_triggers", 0),
                f"{a_sr:.4f}", f"{a_spl:.4f}",
                am.get("motion_events", 0), am.get("automation_triggers", 0),
                f"{pm.get('nav_success_rate', 0):.4f}", f"{pm.get('mean_spl', 0):.4f}",
                pm.get("motion_events", 0), pm.get("automation_triggers", 0),
                f"{a_sr - b_sr:.4f}", f"{a_spl - b_spl:.4f}",
                r.total_attacks_run, r.total_attacks_success,
                f"{r.vulnerability_rate:.1f}",
                len(r.pcap_files), f"{r.eval_duration_sec:.1f}",
            ])
    print(f"📊 Phase comparison CSV: {csv_path}")

    # ── Attack detail CSV ──────────────────────────────────────────────
    atk_csv = os.path.join(output_dir, "attack_results.csv")
    with open(atk_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "scene_id", "attack_name", "suite", "category", "severity",
            "success", "duration_ms", "cvss_score", "pcap_file",
        ])
        for r in results:
            for a in r.attack_results:
                w.writerow([
                    r.scene_id,
                    a.get("attack_name", ""),
                    a.get("suite", ""),
                    a.get("category", ""),
                    a.get("severity", ""),
                    a.get("success", False),
                    f"{a.get('duration_ms', 0):.1f}",
                    f"{a.get('cvss_score', 0):.1f}",
                    a.get("pcap_file", ""),
                ])
    print(f"🔓 Attack detail CSV: {atk_csv}")

    # ── Navigation trial CSV ───────────────────────────────────────────
    nav_csv = os.path.join(output_dir, "navigation_trials.csv")
    with open(nav_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "scene_id", "trial_id", "task_name", "source_room", "target_room",
            "success", "num_steps", "nav_time_sec", "geodesic_dist",
            "actual_dist", "spl", "phase",
        ])
        for r in results:
            for t in r.nav_trials:
                t_dict = t if isinstance(t, dict) else asdict(t)
                w.writerow([
                    r.scene_id,
                    t_dict.get("trial_id", 0),
                    t_dict.get("task_name", ""),
                    t_dict.get("source_room", ""),
                    t_dict.get("target_room", ""),
                    t_dict.get("success", False),
                    t_dict.get("num_steps", 0),
                    f"{t_dict.get('navigation_time_sec', 0):.2f}",
                    f"{t_dict.get('geodesic_distance', 0):.3f}",
                    f"{t_dict.get('actual_distance', 0):.3f}",
                    f"{t_dict.get('spl', 0):.4f}",
                    t_dict.get("phase", ""),
                ])
    print(f"🚶 Navigation trial CSV: {nav_csv}")

    # ── Human-readable summary ─────────────────────────────────────────
    txt_path = os.path.join(output_dir, "eval_summary.txt")
    with open(txt_path, "w") as f:
        f.write("VESPER Evaluation Summary\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write("=" * 60 + "\n\n")

        for r in results:
            f.write(f"Scene: {r.scene_id}\n")
            f.write(f"  Rooms: {r.num_rooms}, Devices: {r.num_devices}\n")
            f.write(f"  Navmesh: {r.navmesh_area_m2:.1f} m²\n\n")

            f.write(f"  BASELINE:    SR={r.baseline_metrics.get('nav_success_rate', 0):.0%}, "
                    f"SPL={r.baseline_metrics.get('mean_spl', 0):.3f}\n")
            f.write(f"  UNDER ATTACK: SR={r.attack_metrics.get('nav_success_rate', 0):.0%}, "
                    f"SPL={r.attack_metrics.get('mean_spl', 0):.3f}\n")
            f.write(f"  POST-ATTACK: SR={r.post_attack_metrics.get('nav_success_rate', 0):.0%}, "
                    f"SPL={r.post_attack_metrics.get('mean_spl', 0):.3f}\n\n")

            f.write(f"  Attacks: {r.total_attacks_success}/{r.total_attacks_run} "
                    f"({r.vulnerability_rate:.0f}% vuln rate)\n")
            f.write(f"  Pcap files: {len(r.pcap_files)}\n")
            f.write(f"  Duration: {r.eval_duration_sec:.0f}s\n\n")

        # Aggregate
        if len(results) > 1:
            f.write("AGGREGATE\n")
            f.write("-" * 40 + "\n")
            all_trials = []
            for r in results:
                for t in r.nav_trials:
                    all_trials.append(t if isinstance(t, dict) else asdict(t))

            baseline_trials = [t for t in all_trials if t.get("phase") == "baseline"]
            attack_trials = [t for t in all_trials if t.get("phase") == "under_attack"]

            if baseline_trials:
                b_sr = sum(1 for t in baseline_trials if t["success"]) / len(baseline_trials)
                b_spls = [t["spl"] for t in baseline_trials if t["spl"] > 0]
                f.write(f"  Baseline: {b_sr:.0%} SR, {sum(b_spls)/len(b_spls):.3f} SPL ({len(baseline_trials)} trials)\n")

            if attack_trials:
                a_sr = sum(1 for t in attack_trials if t["success"]) / len(attack_trials)
                a_spls = [t["spl"] for t in attack_trials if t["spl"] > 0]
                f.write(f"  Attack:   {a_sr:.0%} SR, {sum(a_spls)/len(a_spls) if a_spls else 0:.3f} SPL ({len(attack_trials)} trials)\n")

            total_atk = sum(r.total_attacks_run for r in results)
            total_ok = sum(r.total_attacks_success for r in results)
            f.write(f"\n  Total attacks: {total_ok}/{total_atk} exploitable\n")

    print(f"📝 Summary: {txt_path}")


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        description="VESPER Smoke Test — validate pipeline before big batch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick smoke test (1 scene, attack-only):
  python scripts/run_smoke_test.py --attack-only

  # Full smoke test (1 scene, 1 day, with 3D):
  python scripts/run_smoke_test.py --headless

  # Production batch (30 scenes, 7 days):
  python scripts/run_smoke_test.py --num-scenes 30 --num-days 7 --headless
""",
    )
    p.add_argument("--num-scenes", type=int, default=1)
    p.add_argument("--num-days", type=int, default=1)
    p.add_argument("--headless", action="store_true", default=False)
    p.add_argument("--model", type=str, default=None, help="LLM model override")
    p.add_argument("--time-scale", type=float, default=60.0,
                   help="Sim time scale (60 = 1 real sec = 1 sim min)")
    p.add_argument("--nav-timeout-steps", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--with-smartthings", action="store_true",
                   help="Start Docker firmware containers (needed for real attack targets)")
    p.add_argument("--with-wifi", action="store_true",
                   help="Enable Mininet-WiFi + ESP32 bridge")
    p.add_argument("--attack-only", action="store_true",
                   help="Skip 3D simulation, only run attack frameworks")
    p.add_argument("--allow-fallback-tasks", action="store_true")

    # Frame budgets per phase
    p.add_argument("--baseline-frames", type=int, default=10000,
                   help="Max frames for baseline phase")
    p.add_argument("--attack-frames", type=int, default=15000,
                   help="Max frames for attack phase")
    p.add_argument("--post-attack-frames", type=int, default=5000,
                   help="Max frames for post-attack phase")

    return p.parse_args()


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    args = parse_args()
    np.random.seed(args.seed)

    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " " * 15 + "VESPER Evaluation Pipeline" + " " * 27 + "║")
    print("║" + " " * 15 + f"Scenes: {args.num_scenes}, Days: {args.num_days}" + " " * (52 - len(str(args.num_scenes)) - len(str(args.num_days))) + "║")
    print("╚" + "═" * 68 + "╝\n")

    # ── Pre-flight checks ──────────────────────────────────────────────
    print("🔍 Pre-flight checks...")
    print(f"  habitat_sim: {'✅' if HAS_HABITAT else '❌'}")
    print(f"  pygame:      {'✅' if HAS_PYGAME else '❌ (headless only)'}")
    print(f"  tshark:      {'✅' if subprocess.run(['which', 'tshark'], capture_output=True).returncode == 0 else '❌'}")
    print(f"  docker:      {'✅' if subprocess.run(['which', 'docker'], capture_output=True).returncode == 0 else '❌'}")

    # Check LLM
    if not args.attack_only:
        print("  LLM:         ", end="")
        try:
            from vesper.agents.llm_client import LLMConfig, LLMClient, LLMMessage
            cfg = LLMConfig()
            if cfg.validate():
                client = LLMClient(cfg)
                resp = client.chat([LLMMessage("user", "Say OK")])
                print(f"✅ ({cfg.model})")
            else:
                print("❌ Config invalid")
                if not args.allow_fallback_tasks:
                    print("  Use --allow-fallback-tasks or fix LLM config")
                    return
        except Exception as e:
            print(f"❌ {e}")
            if not args.allow_fallback_tasks:
                return

    # Attack frameworks
    print("  Firmware attacks: ✅ (18 attacks)")
    print("  Network attacks:  ✅ (14 attacks)")
    print("  Phantom-delay:    ✅ (3 attacks)")
    print(f"  WiFi attacks:     {'✅ (7 attacks)' if HAS_WIFI_ATTACKS else '⚠️  (needs Mininet-WiFi Docker)'}")
    print()

    # ── Attack-only mode ───────────────────────────────────────────────
    if args.attack_only:
        print("🔓 Running attack-only mode (no 3D simulation)...")
        result = SceneResult(scene_id="attack_only")
        result = _run_attack_only(result, args)
        write_results([result], RESULTS_DIR)
        print(f"\n✅ Done. Results in {RESULTS_DIR}/")
        return

    # ── Scene evaluation ───────────────────────────────────────────────
    scenes = find_scenes(args.num_scenes, args.seed)
    all_results: List[SceneResult] = []

    eval_start = time_module.time()

    for idx, (scene_path, config_path) in enumerate(scenes):
        elapsed = time_module.time() - eval_start
        if idx > 0:
            avg = elapsed / idx
            remaining = avg * (len(scenes) - idx)
            eta_m = int(remaining // 60)
            print(f"\n  ⏱️  Progress: {idx}/{len(scenes)} | ETA: {eta_m}m")

        try:
            result = evaluate_scene(scene_path, config_path, args)
            all_results.append(result)
        except Exception as e:
            logger.error(f"Scene {idx + 1} failed: {e}", exc_info=True)

        # Incremental save after each scene
        write_results(all_results, RESULTS_DIR)

    # ── Final summary ──────────────────────────────────────────────────
    total_elapsed = time_module.time() - eval_start
    print(f"\n{'='*70}")
    print(f"  ✅ EVALUATION COMPLETE")
    print(f"{'='*70}")
    print(f"  Scenes: {len(all_results)}")
    print(f"  Duration: {total_elapsed/60:.1f} minutes")
    print(f"  Results: {RESULTS_DIR}/")

    all_trials = []
    for r in all_results:
        all_trials.extend(r.nav_trials)
    baseline_t = [t for t in all_trials if (t.get("phase") if isinstance(t, dict) else t.phase) == "baseline"]
    attack_t = [t for t in all_trials if (t.get("phase") if isinstance(t, dict) else t.phase) == "under_attack"]

    if baseline_t:
        b_sr = sum(1 for t in baseline_t if (t.get("success") if isinstance(t, dict) else t.success)) / len(baseline_t)
        print(f"  Baseline nav SR: {b_sr:.0%} ({len(baseline_t)} trials)")
    if attack_t:
        a_sr = sum(1 for t in attack_t if (t.get("success") if isinstance(t, dict) else t.success)) / len(attack_t)
        print(f"  Attack nav SR:   {a_sr:.0%} ({len(attack_t)} trials)")

    total_atk = sum(r.total_attacks_run for r in all_results)
    total_ok = sum(r.total_attacks_success for r in all_results)
    print(f"  Attacks: {total_ok}/{total_atk} exploitable")
    print(f"\n  📁 Output files:")
    print(f"     eval_results.json      — Full JSON data")
    print(f"     phase_comparison.csv   — Baseline vs Attack performance (for paper Table)")
    print(f"     attack_results.csv     — Per-attack detail")
    print(f"     navigation_trials.csv  — Per-trial navigation data")
    print(f"     eval_summary.txt       — Human-readable summary")
    print(f"     pcap_*/                — Packet captures (Wireshark evidence)")
    print(f"{'='*70}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
    except Exception as e:
        logger.error(f"FATAL: {e}", exc_info=True)
        raise
