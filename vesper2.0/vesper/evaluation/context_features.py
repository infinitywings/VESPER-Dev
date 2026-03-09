"""
VESPER Context Feature Extraction

Extracts behavioral context features from EventBus JSONL logs.
These features capture the *physical/behavioral context* of the smart home
at the time of each network capture, enabling context-aware anomaly detection.

Context features (14):
  1.  Agent features    (4) — agent position, room, velocity, action
  2.  Device features   (5) — active devices, recent events, automation state
  3.  Temporal context  (3) — time of day, day of week, activity phase
  4.  Occupancy features(2) — room occupancy, movement pattern

The key insight: attacks that look identical in network traffic may be
distinguishable when behavioral context is considered (e.g., a light-on
command while no one is home is suspicious).

Usage:
    extractor = ContextFeatureExtractor()
    features = extractor.extract_window(
        event_log="logs/events.jsonl",
        window_start=1700000000.0,
        window_end=1700000010.0,
    )
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ─── Feature names ────────────────────────────────────────────────────────────

AGENT_FEATURES = [
    "agent_count",              # number of active agents
    "agent_room_id",            # encoded room ID (hash)
    "agent_velocity",           # movement speed (m/s)
    "agent_action_id",          # encoded current action
]

DEVICE_FEATURES = [
    "active_device_count",      # devices that fired events in window
    "event_count_in_window",    # total events in the time window
    "unique_event_types",       # number of distinct event types
    "automation_triggers",      # automation rules that fired
    "device_state_changes",     # number of state changes
]

TEMPORAL_CONTEXT_FEATURES = [
    "hour_of_day",              # 0-23
    "day_of_week",              # 0=Monday, 6=Sunday
    "activity_phase",           # 0=sleep, 1=morning, 2=day, 3=evening, 4=night
]

OCCUPANCY_FEATURES = [
    "rooms_occupied",           # number of rooms with agent presence
    "movement_events",          # motion_detected events in window
]

ALL_CONTEXT_FEATURE_NAMES = (
    AGENT_FEATURES + DEVICE_FEATURES + TEMPORAL_CONTEXT_FEATURES + OCCUPANCY_FEATURES
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _encode_room(room_name: str) -> float:
    """Deterministic numeric encoding of a room name."""
    if not room_name:
        return 0.0
    return float(hash(room_name.lower().strip()) % 10000)


def _encode_action(action: str) -> float:
    """Deterministic numeric encoding of an action string."""
    actions = {
        "idle": 0, "walking": 1, "sitting": 2, "standing": 3,
        "sleeping": 4, "cooking": 5, "cleaning": 6, "unknown": 7,
    }
    return float(actions.get(action.lower(), 7))


def _activity_phase(hour: int) -> int:
    """Map hour to activity phase: 0=sleep, 1=morning, 2=day, 3=evening, 4=night."""
    if 0 <= hour < 6:
        return 0  # sleep
    elif 6 <= hour < 10:
        return 1  # morning
    elif 10 <= hour < 17:
        return 2  # daytime
    elif 17 <= hour < 21:
        return 3  # evening
    else:
        return 4  # night


# ─── Main extractor ──────────────────────────────────────────────────────────

class ContextFeatureExtractor:
    """
    Extracts context features from EventBus JSONL logs.

    Each line of the JSONL file is an Event dict:
        {
            "event_id": "...",
            "event_type": "motion_detected",
            "source_id": "sensor_living_room",
            "timestamp": 1700000005.123,
            "priority": 2,
            "payload": { "room": "living room", "agent_id": "agent_0", ... }
        }
    """

    def extract_window(
        self,
        event_log: str,
        window_start: float,
        window_end: float,
    ) -> Dict[str, float]:
        """
        Extract context features for events within [window_start, window_end].

        Args:
            event_log: Path to JSONL event log
            window_start: Unix timestamp for window start
            window_end: Unix timestamp for window end

        Returns:
            Dict of {feature_name: value} with all 14 context features.
        """
        features = {name: 0.0 for name in ALL_CONTEXT_FEATURE_NAMES}
        events = self._load_events_in_window(event_log, window_start, window_end)

        if not events:
            return features

        # ── Agent features ───────────────────────────────────────────
        agent_ids = set()
        agent_rooms = []
        agent_velocities = []
        agent_actions = []

        for ev in events:
            payload = ev.get("payload", {})
            agent_id = payload.get("agent_id")
            if agent_id:
                agent_ids.add(agent_id)
            room = payload.get("room", "")
            if room:
                agent_rooms.append(room)
            velocity = payload.get("velocity", payload.get("speed", 0))
            if velocity:
                agent_velocities.append(float(velocity))
            action = payload.get("action", "")
            if action:
                agent_actions.append(action)

        features["agent_count"] = float(len(agent_ids))
        if agent_rooms:
            # Use the most recent room
            features["agent_room_id"] = _encode_room(agent_rooms[-1])
        if agent_velocities:
            features["agent_velocity"] = float(np.mean(agent_velocities))
        if agent_actions:
            # Most common action in window
            most_common = Counter(agent_actions).most_common(1)[0][0]
            features["agent_action_id"] = _encode_action(most_common)

        # ── Device features ──────────────────────────────────────────
        source_ids = set()
        event_types = set()
        automation_count = 0
        state_changes = 0

        for ev in events:
            src = ev.get("source_id")
            if src:
                source_ids.add(src)
            etype = ev.get("event_type", "")
            event_types.add(etype)

            if "automation" in etype.lower():
                automation_count += 1
            if "state_change" in etype.lower() or "set_state" in etype.lower():
                state_changes += 1

        features["active_device_count"] = float(len(source_ids))
        features["event_count_in_window"] = float(len(events))
        features["unique_event_types"] = float(len(event_types))
        features["automation_triggers"] = float(automation_count)
        features["device_state_changes"] = float(state_changes)

        # ── Temporal context ─────────────────────────────────────────
        import datetime
        mid_time = (window_start + window_end) / 2
        dt = datetime.datetime.fromtimestamp(mid_time)
        features["hour_of_day"] = float(dt.hour)
        features["day_of_week"] = float(dt.weekday())
        features["activity_phase"] = float(_activity_phase(dt.hour))

        # ── Occupancy features ───────────────────────────────────────
        occupied_rooms = set()
        motion_count = 0
        for ev in events:
            payload = ev.get("payload", {})
            room = payload.get("room")
            if room:
                occupied_rooms.add(room)
            if ev.get("event_type") == "motion_detected":
                motion_count += 1

        features["rooms_occupied"] = float(len(occupied_rooms))
        features["movement_events"] = float(motion_count)

        return features

    def _load_events_in_window(
        self,
        event_log: str,
        window_start: float,
        window_end: float,
    ) -> List[Dict]:
        """Load events from JSONL file within the time window."""
        events = []
        path = Path(event_log)
        if not path.exists():
            logger.warning(f"Event log not found: {event_log}")
            return events

        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    ts = ev.get("timestamp", 0)
                    if window_start <= ts <= window_end:
                        events.append(ev)
                except json.JSONDecodeError:
                    continue

        return events

    def extract_all_windows(
        self,
        event_log: str,
        window_size_s: float = 10.0,
        stride_s: float = 10.0,
    ) -> List[Dict[str, float]]:
        """
        Extract context features for all windows in the event log.

        Args:
            event_log: Path to JSONL event log
            window_size_s: Window duration in seconds
            stride_s: Step between window starts in seconds

        Returns:
            List of feature dicts, one per window.
        """
        path = Path(event_log)
        if not path.exists():
            return []

        # Find time range
        first_ts = float("inf")
        last_ts = 0.0
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    ts = ev.get("timestamp", 0)
                    if ts > 0:
                        first_ts = min(first_ts, ts)
                        last_ts = max(last_ts, ts)
                except json.JSONDecodeError:
                    continue

        if first_ts >= last_ts:
            return []

        windows = []
        t = first_ts
        while t < last_ts:
            features = self.extract_window(event_log, t, t + window_size_s)
            features["window_start"] = t
            features["window_end"] = t + window_size_s
            windows.append(features)
            t += stride_s

        return windows

    @staticmethod
    def feature_names() -> List[str]:
        return list(ALL_CONTEXT_FEATURE_NAMES)
