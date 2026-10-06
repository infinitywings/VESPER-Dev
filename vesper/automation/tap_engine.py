"""
TAP Rule Engine — Trigger-Action Programming for VESPER Smart Home.

Implements a full TAP (Trigger-Action Programming) rule engine that models
real smart-home automation platforms (SmartThings Automations, IFTTT Applets,
Home Assistant Automations).

Architecture:
    EventBus ──▶ TAPRuleEngine ──▶ IoTBridge / SmartThings / WiFi Bridge
                      │
                      ▼
               TAPRuleMetrics  ← attack-impact measurement

Rule model (IF-AND-THEN):
    IF    <trigger>       (event-based: motion, door open, temp threshold, time)
    AND   <condition>*    (state guard: light is off, time is 18:00-06:00, temp > 75)
    THEN  <action>+       (set device state, send notification, run scene)

This directly maps to the SmartThings Rules API:
    https://developer.smartthings.com/docs/automations/rules
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from vesper.core.event_bus import Event, EventBus, EventPriority

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# ENUMS
# ══════════════════════════════════════════════════════════════════════════

class TriggerType(Enum):
    """What can trigger a rule."""
    DEVICE_EVENT = "device_event"         # motion_detected, door_opened, etc.
    DEVICE_STATE = "device_state_change"  # any device state transition
    THRESHOLD = "threshold"               # sensor value crosses a threshold
    TIME_OF_DAY = "time_of_day"           # simulation clock reaches a time
    SCENE_ENTER = "scene_enter"           # agent enters a room / zone
    SCENE_LEAVE = "scene_leave"           # agent leaves a room / zone


class ConditionOperator(Enum):
    """Comparison operators for conditions."""
    EQUALS = "eq"
    NOT_EQUALS = "ne"
    GREATER_THAN = "gt"
    LESS_THAN = "lt"
    GREATER_EQUAL = "ge"
    LESS_EQUAL = "le"
    BETWEEN = "between"     # value in [lo, hi]
    IN_SET = "in"           # value in a set
    IS_TRUE = "is_true"
    IS_FALSE = "is_false"


class ActionType(Enum):
    """What a rule can do."""
    SET_DEVICE_STATE = "set_device_state"
    TOGGLE_DEVICE = "toggle_device"
    SEND_NOTIFICATION = "send_notification"
    RUN_SCENE = "run_scene"           # activate a group of device states
    DELAY_THEN = "delay_then"         # wait N seconds then execute sub-actions
    LOG_EVENT = "log_event"


class RuleStatus(Enum):
    """Lifecycle status of a rule."""
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"
    ERROR = "error"


# ══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class TAPTrigger:
    """
    IF component of a TAP rule.

    Examples:
        # Motion in kitchen
        TAPTrigger(event_type="motion_detected", room="kitchen")

        # Temperature exceeds 80°F
        TAPTrigger(trigger_type=TriggerType.THRESHOLD,
                   device_id="kitchen_temperature_sensor",
                   attribute="temperature", threshold=80.0,
                   threshold_op=ConditionOperator.GREATER_THAN)

        # Agent enters bedroom
        TAPTrigger(trigger_type=TriggerType.SCENE_ENTER, room="bedroom")
    """
    trigger_type: TriggerType = TriggerType.DEVICE_EVENT
    event_type: Optional[str] = None       # e.g., "motion_detected"
    device_id: Optional[str] = None        # specific device
    device_type: Optional[str] = None      # e.g., "motion_sensor"
    room: Optional[str] = None             # room filter
    attribute: Optional[str] = None        # for THRESHOLD triggers
    threshold: Optional[float] = None      # threshold value
    threshold_op: ConditionOperator = ConditionOperator.GREATER_THAN
    # Time-of-day trigger (sim time, 0–24 hours)
    trigger_hour: Optional[float] = None
    trigger_minute: Optional[float] = None

    def matches(self, event: Event, context: Dict[str, Any]) -> bool:
        """Check if this trigger matches an incoming event."""
        payload = event.payload or {}

        if self.trigger_type == TriggerType.DEVICE_EVENT:
            if self.event_type and event.event_type != self.event_type:
                return False
            if self.room and payload.get("room", "").lower() != self.room.lower():
                return False
            if self.device_id and event.source_id != self.device_id:
                return False
            if self.device_type:
                src = event.source_id or ""
                if self.device_type not in src and payload.get("device_type") != self.device_type:
                    return False
            return True

        elif self.trigger_type == TriggerType.THRESHOLD:
            val = payload.get(self.attribute or "value")
            if val is None:
                return False
            try:
                val = float(val)
            except (ValueError, TypeError):
                return False
            return _compare(val, self.threshold_op, self.threshold)

        elif self.trigger_type in (TriggerType.SCENE_ENTER, TriggerType.SCENE_LEAVE):
            expected_evt = ("agent_entered_room" if self.trigger_type == TriggerType.SCENE_ENTER
                            else "agent_left_room")
            if event.event_type != expected_evt:
                return False
            if self.room and payload.get("room", "").lower() != self.room.lower():
                return False
            return True

        elif self.trigger_type == TriggerType.TIME_OF_DAY:
            sim_hour = context.get("sim_hour", 0)
            if self.trigger_hour is not None and abs(sim_hour - self.trigger_hour) > 0.5:
                return False
            return True

        elif self.trigger_type == TriggerType.DEVICE_STATE:
            return event.event_type in ("state_change", "device_state_changed",
                                        "firmware_state_update")

        return False


@dataclass
class TAPCondition:
    """
    AND component — a guard that must be true for the rule to fire.

    Examples:
        # Light must be off
        TAPCondition(device_id="kitchen_smart_light", attribute="state",
                     operator=ConditionOperator.EQUALS, value="off")

        # Time must be between 18:00 and 06:00 (night)
        TAPCondition(attribute="sim_hour",
                     operator=ConditionOperator.BETWEEN, value=[18, 30])
                     # 30 = 6:00 next day in 24-hr wrap

        # Temperature below 72
        TAPCondition(device_id="living_room_temperature_sensor",
                     attribute="temperature",
                     operator=ConditionOperator.LESS_THAN, value=72.0)
    """
    device_id: Optional[str] = None
    device_type: Optional[str] = None
    room: Optional[str] = None
    attribute: str = "state"
    operator: ConditionOperator = ConditionOperator.EQUALS
    value: Any = None

    def evaluate(self, device_states: Dict[str, Dict[str, Any]],
                 context: Dict[str, Any]) -> bool:
        """Evaluate this condition against current device states."""
        # Context-based condition (e.g., time of day)
        if self.device_id is None and self.device_type is None:
            actual = context.get(self.attribute)
            if actual is None:
                return False
            return _compare(actual, self.operator, self.value)

        # Device-based condition
        if self.device_id:
            dev = device_states.get(self.device_id)
            if dev is None:
                return False
            actual = dev.get(self.attribute, dev.get("state"))
        elif self.device_type and self.room:
            # Find device by type + room
            actual = None
            for did, dev in device_states.items():
                if (self.device_type in did or dev.get("device_type") == self.device_type):
                    if dev.get("room", "").lower() == self.room.lower():
                        actual = dev.get(self.attribute, dev.get("state"))
                        break
            if actual is None:
                return False
        else:
            return False

        return _compare(actual, self.operator, self.value)


@dataclass
class TAPAction:
    """
    THEN component — what happens when the rule fires.

    Examples:
        # Turn on kitchen light
        TAPAction(action_type=ActionType.SET_DEVICE_STATE,
                  target_device_id="kitchen_smart_light",
                  params={"state": "on"})

        # Lock front door
        TAPAction(action_type=ActionType.SET_DEVICE_STATE,
                  target_device_type="smart_door_lock",
                  target_room="entryway",
                  params={"state": "locked"})

        # Send notification
        TAPAction(action_type=ActionType.SEND_NOTIFICATION,
                  params={"message": "Motion in kitchen!", "severity": "info"})
    """
    action_type: ActionType = ActionType.SET_DEVICE_STATE
    target_device_id: Optional[str] = None
    target_device_type: Optional[str] = None
    target_room: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    delay_seconds: float = 0.0  # for DELAY_THEN actions


@dataclass
class RuleExecutionRecord:
    """Detailed record of a single rule execution (for paper evidence)."""
    rule_id: str
    rule_name: str
    timestamp: float
    trigger_event_type: str
    trigger_room: str
    conditions_checked: int
    conditions_passed: int
    actions_executed: int
    actions_succeeded: int
    actions_failed: int
    blocked_by_condition: bool = False
    blocked_by_cooldown: bool = False
    error: Optional[str] = None
    # Attack context — was an attack running when this rule fired?
    attack_active: bool = False
    attack_name: Optional[str] = None
    # Latency
    execution_time_ms: float = 0.0
    # Action details
    action_details: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TAPRuleMetrics:
    """Aggregate metrics for TAP rule execution (for MobiCom paper tables).

    Key metrics:
      - Rule fire rate before/during/after attack
      - Action success rate before/during/after attack
      - Condition evaluation accuracy under attack
      - Automation latency degradation
    """
    total_evaluations: int = 0
    total_fires: int = 0
    total_actions_executed: int = 0
    total_actions_succeeded: int = 0
    total_actions_failed: int = 0
    total_blocked_by_condition: int = 0
    total_blocked_by_cooldown: int = 0
    total_errors: int = 0

    # Latency tracking
    execution_times_ms: List[float] = field(default_factory=list)

    # Per-phase counters (baseline / under_attack / post_attack)
    phase_fires: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    phase_action_success: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    phase_action_fail: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Per-rule counters
    rule_fire_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    rule_fail_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Execution log (all records)
    execution_log: List[RuleExecutionRecord] = field(default_factory=list)

    @property
    def fire_rate(self) -> float:
        return self.total_fires / self.total_evaluations if self.total_evaluations > 0 else 0.0

    @property
    def action_success_rate(self) -> float:
        total = self.total_actions_succeeded + self.total_actions_failed
        return self.total_actions_succeeded / total if total > 0 else 0.0

    @property
    def mean_latency_ms(self) -> float:
        return (sum(self.execution_times_ms) / len(self.execution_times_ms)
                if self.execution_times_ms else 0.0)

    @property
    def p95_latency_ms(self) -> float:
        if not self.execution_times_ms:
            return 0.0
        sorted_t = sorted(self.execution_times_ms)
        idx = int(len(sorted_t) * 0.95)
        return sorted_t[min(idx, len(sorted_t) - 1)]

    def phase_summary(self, phase: str) -> Dict[str, Any]:
        """Get summary for one phase."""
        fires = self.phase_fires.get(phase, 0)
        ok = self.phase_action_success.get(phase, 0)
        fail = self.phase_action_fail.get(phase, 0)
        return {
            "fires": fires,
            "actions_succeeded": ok,
            "actions_failed": fail,
            "action_success_rate": ok / (ok + fail) if (ok + fail) > 0 else 0.0,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON output."""
        return {
            "total_evaluations": self.total_evaluations,
            "total_fires": self.total_fires,
            "total_actions_executed": self.total_actions_executed,
            "total_actions_succeeded": self.total_actions_succeeded,
            "total_actions_failed": self.total_actions_failed,
            "total_blocked_by_condition": self.total_blocked_by_condition,
            "total_blocked_by_cooldown": self.total_blocked_by_cooldown,
            "total_errors": self.total_errors,
            "fire_rate": self.fire_rate,
            "action_success_rate": self.action_success_rate,
            "mean_latency_ms": self.mean_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "phase_baseline": self.phase_summary("baseline"),
            "phase_under_attack": self.phase_summary("under_attack"),
            "phase_post_attack": self.phase_summary("post_attack"),
            "per_rule_fires": dict(self.rule_fire_counts),
            "per_rule_fails": dict(self.rule_fail_counts),
            "execution_log_size": len(self.execution_log),
        }


@dataclass
class TAPRule:
    """
    A complete TAP automation rule: IF trigger AND conditions THEN actions.

    This mirrors the SmartThings Rules API structure:
      - Trigger → SmartThings "if" clause
      - Conditions → SmartThings "and" clauses
      - Actions → SmartThings "then" commands
    """
    rule_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    trigger: TAPTrigger = field(default_factory=TAPTrigger)
    conditions: List[TAPCondition] = field(default_factory=list)
    actions: List[TAPAction] = field(default_factory=list)
    status: RuleStatus = RuleStatus.ACTIVE
    cooldown_sec: float = 5.0
    last_fired: float = 0.0
    # SmartThings mapping
    smartthings_rule_id: Optional[str] = None
    # Tags for categorization
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for display / JSON export."""
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "description": self.description,
            "trigger_type": self.trigger.trigger_type.value,
            "trigger_event": self.trigger.event_type,
            "trigger_room": self.trigger.room,
            "num_conditions": len(self.conditions),
            "num_actions": len(self.actions),
            "status": self.status.value,
            "cooldown_sec": self.cooldown_sec,
            "tags": self.tags,
        }


# ══════════════════════════════════════════════════════════════════════════
# TAP RULE ENGINE
# ══════════════════════════════════════════════════════════════════════════

class TAPRuleEngine:
    """
    Central automation engine that evaluates TAP rules against events.

    Hooks into the VESPER EventBus as a wildcard subscriber ("*") and
    evaluates all active rules against every incoming event.

    Usage:
        event_bus = EventBus()
        iot_bridge = IoTBridge()
        engine = TAPRuleEngine(event_bus, iot_bridge)
        engine.generate_default_rules(rooms, room_positions)
        # engine now auto-evaluates rules on every event
    """

    def __init__(
        self,
        event_bus: EventBus,
        iot_bridge: Any = None,       # vesper.habitat.iot_bridge.IoTBridge
        smartthings_bridge: Any = None,  # SmartThings3DBridge from eval script
    ):
        self._event_bus = event_bus
        self._iot_bridge = iot_bridge
        self._smartthings_bridge = smartthings_bridge

        self.rules: List[TAPRule] = []
        self.metrics = TAPRuleMetrics()

        # Current phase for metric bucketing
        self._current_phase: str = "baseline"

        # Current attack context
        self._attack_active: bool = False
        self._attack_name: Optional[str] = None

        # Simulation time context
        self._sim_hour: float = 0.0
        self._sim_day: int = 0

        # Notification log (for rules that send notifications)
        self.notifications: List[Dict[str, Any]] = []
        self._max_notifications = 200

        # Action callback — called for every action so external systems
        # (SmartThings, WiFi bridge) can react
        self._action_callbacks: List[Callable[[TAPRule, TAPAction, Dict], None]] = []

        # Subscribe to ALL events on the bus
        self._event_bus.subscribe("*", self._on_event)
        self._event_count = 0  # debug counter for received events
        logger.info("TAPRuleEngine initialized, subscribed to EventBus (wildcard)")

    # ── Configuration ──────────────────────────────────────────────────

    def set_phase(self, phase: str):
        """Set current evaluation phase (baseline / under_attack / post_attack)."""
        self._current_phase = phase
        logger.info(f"TAP engine phase → {phase}")

    def set_attack_context(self, active: bool, name: Optional[str] = None):
        """Signal whether an attack is currently running."""
        self._attack_active = active
        self._attack_name = name if active else None

    def update_sim_time(self, sim_hour: float, sim_day: int = 0):
        """Update simulation time context (for time-based rules)."""
        self._sim_hour = sim_hour
        self._sim_day = sim_day

    def add_action_callback(self, cb: Callable[[TAPRule, TAPAction, Dict], None]):
        """Register a callback for action execution (e.g., SmartThings sync)."""
        self._action_callbacks.append(cb)

    # ── Rule Management ────────────────────────────────────────────────

    def add_rule(self, rule: TAPRule) -> str:
        """Add a rule. Returns rule_id."""
        self.rules.append(rule)
        logger.info(f"Added TAP rule: {rule.name} ({rule.rule_id})")
        return rule.rule_id

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID."""
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.rule_id != rule_id]
        return len(self.rules) < before

    def get_rule(self, rule_id: str) -> Optional[TAPRule]:
        """Get a rule by ID."""
        for r in self.rules:
            if r.rule_id == rule_id:
                return r
        return None

    def pause_rule(self, rule_id: str):
        """Pause a rule."""
        r = self.get_rule(rule_id)
        if r:
            r.status = RuleStatus.PAUSED

    def resume_rule(self, rule_id: str):
        """Resume a paused rule."""
        r = self.get_rule(rule_id)
        if r and r.status == RuleStatus.PAUSED:
            r.status = RuleStatus.ACTIVE

    # ── Default Rule Generation ────────────────────────────────────────

    def generate_default_rules(
        self,
        rooms: List[str],
        room_positions: Optional[Dict[str, Tuple[float, float, float]]] = None,
    ) -> int:
        """Generate realistic TAP rules for a scene.

        Creates rules that mirror common SmartThings / Home Assistant setups:
          1. Motion → Lights ON (per room)
          2. No motion for 5 min → Lights OFF (energy saving)
          3. Door opened at night → Turn on hallway light
          4. Agent leaves room → Turn off lights (occupancy-based)
          5. Temperature too high → Send notification
          6. Door sensor opened → Lock reminder notification
          7. Night mode → Lock all doors at 22:00
          8. Morning mode → Unlock doors at 07:00
          9. Water leak detected → Send critical notification
         10. Motion in entryway → Unlock door + turn on light

        Args:
            rooms: List of room names in the scene
            room_positions: Optional room→position map

        Returns:
            Number of rules created
        """
        created = 0
        rooms_lower = [r.lower() for r in rooms]

        for room in rooms_lower:
            # ── 1. Motion → Lights ON ──────────────────────────────
            if self._room_has_device(room, "motion_sensor") and self._room_has_device(room, "smart_light"):
                self.add_rule(TAPRule(
                    name=f"motion_lights_on_{room}",
                    description=f"Turn on {room} lights when motion detected",
                    trigger=TAPTrigger(
                        event_type="motion_detected",
                        room=room,
                    ),
                    conditions=[
                        TAPCondition(
                            device_type="smart_light", room=room,
                            attribute="state", operator=ConditionOperator.EQUALS,
                            value="off",
                        ),
                    ],
                    actions=[
                        TAPAction(
                            action_type=ActionType.SET_DEVICE_STATE,
                            target_device_type="smart_light",
                            target_room=room,
                            params={"state": "on"},
                        ),
                    ],
                    cooldown_sec=10.0,
                    tags=["lighting", "occupancy"],
                ))
                created += 1

            # ── 2. Agent leaves → Lights OFF (occupancy) ──────────
            if self._room_has_device(room, "smart_light"):
                self.add_rule(TAPRule(
                    name=f"leave_lights_off_{room}",
                    description=f"Turn off {room} lights when agent leaves",
                    trigger=TAPTrigger(
                        trigger_type=TriggerType.SCENE_LEAVE,
                        room=room,
                    ),
                    actions=[
                        TAPAction(
                            action_type=ActionType.SET_DEVICE_STATE,
                            target_device_type="smart_light",
                            target_room=room,
                            params={"state": "off"},
                        ),
                    ],
                    cooldown_sec=15.0,
                    tags=["lighting", "energy_saving"],
                ))
                created += 1

            # ── 3. Door opened at night → Hallway light ───────────
            if "door_sensor" in room or "entryway" in room:
                hallway = next((r for r in rooms_lower if "hallway" in r), None)
                if hallway:
                    self.add_rule(TAPRule(
                        name=f"door_night_light_{room}",
                        description=f"Turn on hallway light when {room} door opens at night",
                        trigger=TAPTrigger(
                            event_type="door_opened",
                            room=room,
                        ),
                        conditions=[
                            TAPCondition(
                                attribute="sim_hour",
                                operator=ConditionOperator.BETWEEN,
                                value=[18.0, 30.0],  # 6pm–6am (wraps)
                            ),
                        ],
                        actions=[
                            TAPAction(
                                action_type=ActionType.SET_DEVICE_STATE,
                                target_device_type="smart_light",
                                target_room=hallway,
                                params={"state": "on"},
                            ),
                            TAPAction(
                                action_type=ActionType.SEND_NOTIFICATION,
                                params={"message": f"Door opened in {room}",
                                        "severity": "info"},
                            ),
                        ],
                        cooldown_sec=30.0,
                        tags=["security", "lighting"],
                    ))
                    created += 1

            # ── 4. Temperature alert ──────────────────────────────
            if self._room_has_device(room, "temperature_sensor"):
                self.add_rule(TAPRule(
                    name=f"temp_high_alert_{room}",
                    description=f"Alert when {room} temperature exceeds 80°F",
                    trigger=TAPTrigger(
                        trigger_type=TriggerType.THRESHOLD,
                        device_type="temperature_sensor",
                        room=room,
                        attribute="temperature",
                        threshold=80.0,
                        threshold_op=ConditionOperator.GREATER_THAN,
                    ),
                    actions=[
                        TAPAction(
                            action_type=ActionType.SEND_NOTIFICATION,
                            params={"message": f"High temperature in {room}!",
                                    "severity": "warning"},
                        ),
                    ],
                    cooldown_sec=60.0,
                    tags=["climate", "safety"],
                ))
                created += 1

            # ── 5. Water leak alert ───────────────────────────────
            if self._room_has_device(room, "water_leak_sensor"):
                self.add_rule(TAPRule(
                    name=f"water_leak_alert_{room}",
                    description=f"Critical alert on water leak in {room}",
                    trigger=TAPTrigger(
                        event_type="water_leak_detected",
                        room=room,
                    ),
                    actions=[
                        TAPAction(
                            action_type=ActionType.SEND_NOTIFICATION,
                            params={"message": f"WATER LEAK in {room}!",
                                    "severity": "critical"},
                        ),
                    ],
                    cooldown_sec=120.0,
                    tags=["safety", "water"],
                ))
                created += 1

            # ── 6. Entryway motion → Unlock + Light ───────────────
            if "entryway" in room or "entrance" in room:
                self.add_rule(TAPRule(
                    name=f"entryway_welcome_{room}",
                    description=f"Unlock door and turn on light when motion in {room}",
                    trigger=TAPTrigger(
                        event_type="motion_detected",
                        room=room,
                    ),
                    actions=[
                        TAPAction(
                            action_type=ActionType.SET_DEVICE_STATE,
                            target_device_type="smart_door_lock",
                            target_room=room,
                            params={"state": "unlocked"},
                        ),
                        TAPAction(
                            action_type=ActionType.SET_DEVICE_STATE,
                            target_device_type="smart_light",
                            target_room=room,
                            params={"state": "on"},
                        ),
                    ],
                    cooldown_sec=30.0,
                    tags=["security", "welcome"],
                ))
                created += 1

            # ── 7. Humidity alert in bathroom ─────────────────────
            if "bathroom" in room and self._room_has_device(room, "humidity_sensor"):
                self.add_rule(TAPRule(
                    name=f"humidity_alert_{room}",
                    description=f"Alert when {room} humidity exceeds 70%",
                    trigger=TAPTrigger(
                        trigger_type=TriggerType.THRESHOLD,
                        device_type="humidity_sensor",
                        room=room,
                        attribute="humidity",
                        threshold=70.0,
                        threshold_op=ConditionOperator.GREATER_THAN,
                    ),
                    actions=[
                        TAPAction(
                            action_type=ActionType.SEND_NOTIFICATION,
                            params={"message": f"High humidity in {room}!",
                                    "severity": "info"},
                        ),
                    ],
                    cooldown_sec=120.0,
                    tags=["climate"],
                ))
                created += 1

        # ── 8. Global: Night lock (22:00) ─────────────────────────
        entryway = next((r for r in rooms_lower if "entryway" in r or "entrance" in r), None)
        if entryway:
            self.add_rule(TAPRule(
                name="night_lock_doors",
                description="Lock all doors at 10 PM",
                trigger=TAPTrigger(
                    trigger_type=TriggerType.TIME_OF_DAY,
                    trigger_hour=22.0,
                ),
                actions=[
                    TAPAction(
                        action_type=ActionType.SET_DEVICE_STATE,
                        target_device_type="smart_door_lock",
                        target_room=entryway,
                        params={"state": "locked"},
                    ),
                    TAPAction(
                        action_type=ActionType.SEND_NOTIFICATION,
                        params={"message": "Night mode: doors locked",
                                "severity": "info"},
                    ),
                ],
                cooldown_sec=3600.0,  # once per hour
                tags=["security", "schedule"],
            ))
            created += 1

        # ── 9. Global: Morning unlock (07:00) ─────────────────────
        if entryway:
            self.add_rule(TAPRule(
                name="morning_unlock_doors",
                description="Unlock doors at 7 AM",
                trigger=TAPTrigger(
                    trigger_type=TriggerType.TIME_OF_DAY,
                    trigger_hour=7.0,
                ),
                actions=[
                    TAPAction(
                        action_type=ActionType.SET_DEVICE_STATE,
                        target_device_type="smart_door_lock",
                        target_room=entryway,
                        params={"state": "unlocked"},
                    ),
                ],
                cooldown_sec=3600.0,
                tags=["security", "schedule"],
            ))
            created += 1

        logger.info(f"Generated {created} default TAP rules for {len(rooms)} rooms")
        return created

    def _room_has_device(self, room: str, device_type: str) -> bool:
        """Check if a room would have a device type based on IoT bridge config."""
        if self._iot_bridge:
            for did in self._iot_bridge.rooms.get(room, []):
                if device_type in did:
                    return True
        # Fallback: check known room→device patterns
        from vesper.habitat.iot_bridge import IoTBridge
        for pattern, devices in IoTBridge.ROOM_DEVICES.items():
            if pattern in room:
                for dt, _ in devices:
                    if dt.value == device_type:
                        return True
        return False

    # ── Event Processing ───────────────────────────────────────────────

    def _on_event(self, event: Event):
        """EventBus wildcard handler — evaluate all rules."""
        # Skip our own actions to prevent infinite loops
        if event.source_id == "tap_engine":
            return

        self._event_count += 1
        if self._event_count <= 10:
            print(
                f"[TAP] _on_event #{self._event_count}: type={event.event_type} "
                f"room={event.payload.get('room', '?')} src={event.source_id} "
                f"(evaluating {len(self.rules)} rules)"
            )
        elif self._event_count == 11:
            print(f"[TAP] (silencing further _on_event debug prints — {len(self.rules)} rules active)")

        # Use logger.info for first 5 events so they appear in log file
        if self._event_count <= 5:
            logger.info(
                f"[TAP] _on_event #{self._event_count}: type={event.event_type} "
                f"room={event.payload.get('room', '?')} src={event.source_id} "
                f"(evaluating {len(self.rules)} rules)"
            )

        context = {
            "sim_hour": self._sim_hour,
            "sim_day": self._sim_day,
            "phase": self._current_phase,
        }

        for rule in self.rules:
            self._evaluate_rule(rule, event, context)

    def _evaluate_rule(self, rule: TAPRule, event: Event, context: Dict[str, Any]):
        """Evaluate a single rule against an event."""
        self.metrics.total_evaluations += 1

        # Skip inactive rules
        if rule.status != RuleStatus.ACTIVE:
            return

        # Check trigger
        if not rule.trigger.matches(event, context):
            return

        logger.info(
            f"[TAP] ✓ Trigger matched: rule={rule.name} "
            f"event={event.event_type} room={event.payload.get('room', '?')}"
        )

        # Check cooldown
        now = time.time()
        if now - rule.last_fired < rule.cooldown_sec:
            self.metrics.total_blocked_by_cooldown += 1
            logger.debug(f"[TAP] ✗ Cooldown blocked: rule={rule.name}")
            return

        # Check conditions
        device_states = self._get_device_states()
        conditions_checked = len(rule.conditions)
        conditions_passed = 0

        for cond in rule.conditions:
            if cond.evaluate(device_states, context):
                conditions_passed += 1
            else:
                # Condition failed — log details for debugging
                logger.info(
                    f"[TAP] ✗ Condition failed: rule={rule.name} "
                    f"cond(dev_id={cond.device_id}, dev_type={cond.device_type}, "
                    f"room={cond.room}, attr={cond.attribute}, "
                    f"op={cond.operator.name}, value={cond.value})"
                )
                # Condition failed — log and return
                record = RuleExecutionRecord(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    timestamp=now,
                    trigger_event_type=event.event_type,
                    trigger_room=event.payload.get("room", ""),
                    conditions_checked=conditions_checked,
                    conditions_passed=conditions_passed,
                    actions_executed=0,
                    actions_succeeded=0,
                    actions_failed=0,
                    blocked_by_condition=True,
                    attack_active=self._attack_active,
                    attack_name=self._attack_name,
                )
                self.metrics.total_blocked_by_condition += 1
                self.metrics.execution_log.append(record)
                return

        # All conditions passed — FIRE the rule!
        print(
            f"[TAP] 🔥 FIRING rule: {rule.name} "
            f"(trigger: {event.event_type} in {event.payload.get('room', '?')})"
        )
        t_start = time.time()
        actions_ok = 0
        actions_fail = 0
        action_details = []

        for action in rule.actions:
            try:
                detail = self._execute_action(rule, action, event)
                action_details.append(detail)
                if detail.get("success", False):
                    actions_ok += 1
                else:
                    actions_fail += 1
            except Exception as e:
                actions_fail += 1
                action_details.append({"error": str(e), "success": False})
                logger.error(f"TAP action failed ({rule.name}): {e}")

        t_end = time.time()
        exec_ms = (t_end - t_start) * 1000
        rule.last_fired = now

        # Record metrics
        record = RuleExecutionRecord(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            timestamp=now,
            trigger_event_type=event.event_type,
            trigger_room=event.payload.get("room", ""),
            conditions_checked=conditions_checked,
            conditions_passed=conditions_passed,
            actions_executed=len(rule.actions),
            actions_succeeded=actions_ok,
            actions_failed=actions_fail,
            attack_active=self._attack_active,
            attack_name=self._attack_name,
            execution_time_ms=exec_ms,
            action_details=action_details,
        )

        self.metrics.total_fires += 1
        self.metrics.total_actions_executed += len(rule.actions)
        self.metrics.total_actions_succeeded += actions_ok
        self.metrics.total_actions_failed += actions_fail
        self.metrics.execution_times_ms.append(exec_ms)
        self.metrics.phase_fires[self._current_phase] += 1
        self.metrics.phase_action_success[self._current_phase] += actions_ok
        self.metrics.phase_action_fail[self._current_phase] += actions_fail
        self.metrics.rule_fire_counts[rule.name] += 1
        if actions_fail > 0:
            self.metrics.rule_fail_counts[rule.name] += 1
        self.metrics.execution_log.append(record)

        logger.debug(f"TAP rule fired: {rule.name} "
                     f"({actions_ok}/{len(rule.actions)} actions OK, {exec_ms:.1f}ms)")

    def _execute_action(
        self,
        rule: TAPRule,
        action: TAPAction,
        trigger_event: Event,
    ) -> Dict[str, Any]:
        """Execute a single TAP action."""
        detail: Dict[str, Any] = {
            "action_type": action.action_type.value,
            "target": action.target_device_id or action.target_device_type,
            "room": action.target_room,
            "params": action.params,
        }

        if action.action_type == ActionType.SET_DEVICE_STATE:
            new_state = action.params.get("state", "on")
            success = self._set_device_state(
                action.target_device_id,
                action.target_device_type,
                action.target_room,
                new_state,
            )
            detail["success"] = success
            detail["new_state"] = new_state

            # Notify callbacks (SmartThings sync, WiFi bridge, etc.)
            for cb in self._action_callbacks:
                try:
                    cb(rule, action, detail)
                except Exception as e:
                    logger.warning(f"Action callback error: {e}")

            # Emit action event on EventBus
            self._event_bus.publish(Event.create(
                event_type="automation_triggered",
                payload={
                    "rule_name": rule.name,
                    "rule_id": rule.rule_id,
                    "action": action.action_type.value,
                    "target_device": action.target_device_id or action.target_device_type,
                    "target_room": action.target_room,
                    "new_state": new_state,
                    "success": success,
                    "phase": self._current_phase,
                    "attack_active": self._attack_active,
                },
                source_id="tap_engine",
                priority=EventPriority.HIGH,
            ))

        elif action.action_type == ActionType.TOGGLE_DEVICE:
            success = self._toggle_device(
                action.target_device_id,
                action.target_device_type,
                action.target_room,
            )
            detail["success"] = success

        elif action.action_type == ActionType.SEND_NOTIFICATION:
            self._send_notification(
                action.params.get("message", ""),
                action.params.get("severity", "info"),
                rule_name=rule.name,
            )
            detail["success"] = True

        elif action.action_type == ActionType.LOG_EVENT:
            logger.info(f"[TAP LOG] {rule.name}: {action.params}")
            detail["success"] = True

        else:
            detail["success"] = False
            detail["error"] = f"Unknown action type: {action.action_type}"

        return detail

    # ── Device State Management ────────────────────────────────────────

    def _get_device_states(self) -> Dict[str, Dict[str, Any]]:
        """Get current device states from IoT bridge."""
        if self._iot_bridge:
            return {
                did: {
                    "state": dev.state,
                    "device_type": dev.device_type,
                    "room": dev.room,
                    "is_triggered": dev.is_triggered,
                    **dev.properties,
                }
                for did, dev in self._iot_bridge.devices.items()
            }
        return {}

    def _set_device_state(
        self,
        device_id: Optional[str],
        device_type: Optional[str],
        room: Optional[str],
        state: str,
    ) -> bool:
        """Set device state via IoT bridge."""
        if not self._iot_bridge:
            return False

        targets = self._find_target_devices(device_id, device_type, room)
        if not targets:
            return False

        success = False
        for did in targets:
            if self._iot_bridge.set_device_state(did, state, source="tap_engine"):
                success = True
                logger.info(f"[TAP] {did} → {state}")

                # Also sync to SmartThings if bridge available
                if self._smartthings_bridge:
                    try:
                        self._sync_to_smartthings(did, state)
                    except Exception as e:
                        logger.warning(f"SmartThings sync failed: {e}")

        return success

    def _toggle_device(
        self,
        device_id: Optional[str],
        device_type: Optional[str],
        room: Optional[str],
    ) -> bool:
        """Toggle device via IoT bridge."""
        if not self._iot_bridge:
            return False

        targets = self._find_target_devices(device_id, device_type, room)
        success = False
        for did in targets:
            new_state = self._iot_bridge.toggle_device(did)
            if new_state:
                success = True
        return success

    def _find_target_devices(
        self,
        device_id: Optional[str],
        device_type: Optional[str],
        room: Optional[str],
    ) -> List[str]:
        """Find matching devices."""
        if device_id:
            return [device_id] if device_id in self._iot_bridge.devices else []

        results = []
        for did, dev in self._iot_bridge.devices.items():
            if device_type and device_type not in did and dev.device_type != device_type:
                continue
            if room and dev.room.lower() != room.lower():
                continue
            results.append(did)
        return results

    def _send_notification(self, message: str, severity: str, rule_name: str = ""):
        """Log a notification (would go to SmartThings in production)."""
        notif = {
            "timestamp": time.time(),
            "message": message,
            "severity": severity,
            "rule_name": rule_name,
            "attack_active": self._attack_active,
            "attack_name": self._attack_name,
            "phase": self._current_phase,
        }
        self.notifications.append(notif)
        if len(self.notifications) > self._max_notifications:
            self.notifications = self.notifications[-self._max_notifications:]

        icon = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(severity, "📢")
        print(f"[TAP] {icon} {message} (rule: {rule_name})")

    def _sync_to_smartthings(self, device_id: str, state: str):
        """Push state to SmartThings bridge (if available)."""
        if not self._smartthings_bridge:
            return
        # SmartThings3DBridge tracks firmware devices by room
        # Map device_id back to firmware device and push state
        dev = self._iot_bridge.devices.get(device_id) if self._iot_bridge else None
        if dev and hasattr(self._smartthings_bridge, 'firmware_devices'):
            fw_dev = self._smartthings_bridge.firmware_devices.get(dev.room)
            if fw_dev and hasattr(fw_dev, 'update_state'):
                try:
                    fw_dev.update_state(state)
                except Exception:
                    pass

    # ── Time-based Rule Evaluation ─────────────────────────────────────

    def check_time_rules(self):
        """Manually check time-of-day rules.

        Call this periodically (e.g., every sim-minute) to fire time-based rules.
        Creates a synthetic time event and evaluates against it.
        """
        time_event = Event.create(
            event_type="time_tick",
            payload={"sim_hour": self._sim_hour, "sim_day": self._sim_day},
            source_id="sim_clock",
        )
        context = {
            "sim_hour": self._sim_hour,
            "sim_day": self._sim_day,
            "phase": self._current_phase,
        }
        for rule in self.rules:
            if rule.trigger.trigger_type == TriggerType.TIME_OF_DAY:
                self._evaluate_rule(rule, time_event, context)

    # ── SmartThings Rules API Mapping ──────────────────────────────────

    def export_smartthings_rules(self) -> List[Dict[str, Any]]:
        """Export rules in SmartThings Rules API format.

        This generates JSON that could be POSTed to:
            POST https://api.smartthings.com/v1/rules

        Useful for demonstrating that our TAP rules are realistic
        and could run on actual SmartThings infrastructure.
        """
        st_rules = []
        for rule in self.rules:
            st_rule = {
                "name": rule.name,
                "actions": [],
            }

            # Map trigger
            if_clause: Dict[str, Any] = {}
            if rule.trigger.event_type:
                if_clause = {
                    "if": {
                        "equals": {
                            "left": {
                                "device": {
                                    "devices": [rule.trigger.device_id or "*"],
                                    "component": "main",
                                    "capability": _event_to_capability(rule.trigger.event_type),
                                    "attribute": _event_to_attribute(rule.trigger.event_type),
                                }
                            },
                            "right": {"string": "active"},
                        },
                        "then": [],
                    }
                }

            # Map actions
            then_cmds = []
            for act in rule.actions:
                if act.action_type == ActionType.SET_DEVICE_STATE:
                    then_cmds.append({
                        "command": {
                            "devices": [act.target_device_id or "*"],
                            "commands": [{
                                "component": "main",
                                "capability": _device_type_to_capability(act.target_device_type),
                                "command": _state_to_command(act.params.get("state", "on")),
                            }],
                        }
                    })
                elif act.action_type == ActionType.SEND_NOTIFICATION:
                    then_cmds.append({
                        "notify": {
                            "message": act.params.get("message", ""),
                        }
                    })

            if if_clause and "if" in if_clause:
                if_clause["if"]["then"] = then_cmds
                st_rule["actions"] = [if_clause]

            st_rules.append(st_rule)

        return st_rules

    # ── Summary / Export ───────────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        """Get engine summary for display."""
        active = sum(1 for r in self.rules if r.status == RuleStatus.ACTIVE)
        return {
            "total_rules": len(self.rules),
            "active_rules": active,
            "total_fires": self.metrics.total_fires,
            "action_success_rate": f"{self.metrics.action_success_rate:.0%}",
            "mean_latency_ms": f"{self.metrics.mean_latency_ms:.1f}",
            "notifications": len(self.notifications),
            "current_phase": self._current_phase,
            "attack_active": self._attack_active,
        }

    def print_summary(self):
        """Print human-readable summary."""
        s = self.get_summary()
        print(f"\n  {'─'*50}")
        print(f"  📋 TAP Rule Engine Summary")
        print(f"  {'─'*50}")
        print(f"  Rules: {s['total_rules']} ({s['active_rules']} active)")
        print(f"  Fires: {s['total_fires']}")
        print(f"  Action SR: {s['action_success_rate']}")
        print(f"  Mean latency: {s['mean_latency_ms']}ms")
        print(f"  Notifications: {s['notifications']}")
        print(f"  Phase: {s['current_phase']}")
        if self._attack_active:
            print(f"  ⚠️  Attack active: {self._attack_name}")

        # Phase comparison
        for phase in ["baseline", "under_attack", "post_attack"]:
            ps = self.metrics.phase_summary(phase)
            if ps["fires"] > 0:
                print(f"  [{phase}] fires={ps['fires']}, "
                      f"action_sr={ps['action_success_rate']:.0%}")
        print(f"  {'─'*50}")


# ══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════

def _compare(actual: Any, op: ConditionOperator, expected: Any) -> bool:
    """Compare a value using a ConditionOperator."""
    try:
        if op == ConditionOperator.EQUALS:
            return str(actual).lower() == str(expected).lower()
        elif op == ConditionOperator.NOT_EQUALS:
            return str(actual).lower() != str(expected).lower()
        elif op == ConditionOperator.GREATER_THAN:
            return float(actual) > float(expected)
        elif op == ConditionOperator.LESS_THAN:
            return float(actual) < float(expected)
        elif op == ConditionOperator.GREATER_EQUAL:
            return float(actual) >= float(expected)
        elif op == ConditionOperator.LESS_EQUAL:
            return float(actual) <= float(expected)
        elif op == ConditionOperator.BETWEEN:
            lo, hi = expected
            val = float(actual)
            if hi > 24 and val < 12:
                val += 24  # handle midnight wrap for time-of-day
            return lo <= val <= hi
        elif op == ConditionOperator.IN_SET:
            return actual in expected
        elif op == ConditionOperator.IS_TRUE:
            return bool(actual)
        elif op == ConditionOperator.IS_FALSE:
            return not bool(actual)
    except (ValueError, TypeError):
        return False
    return False


def _event_to_capability(event_type: str) -> str:
    """Map event type to SmartThings capability."""
    mapping = {
        "motion_detected": "motionSensor",
        "door_opened": "contactSensor",
        "door_closed": "contactSensor",
        "temperature_reading": "temperatureMeasurement",
        "humidity_reading": "relativeHumidityMeasurement",
        "water_leak_detected": "waterSensor",
    }
    return mapping.get(event_type, "switch")


def _event_to_attribute(event_type: str) -> str:
    """Map event type to SmartThings attribute."""
    mapping = {
        "motion_detected": "motion",
        "door_opened": "contact",
        "door_closed": "contact",
        "temperature_reading": "temperature",
        "humidity_reading": "humidity",
        "water_leak_detected": "water",
    }
    return mapping.get(event_type, "switch")


def _device_type_to_capability(device_type: Optional[str]) -> str:
    """Map device type to SmartThings capability."""
    mapping = {
        "smart_light": "switch",
        "smart_door_lock": "lock",
        "thermostat": "thermostatMode",
        "motion_sensor": "motionSensor",
        "door_sensor": "contactSensor",
    }
    return mapping.get(device_type or "", "switch")


def _state_to_command(state: str) -> str:
    """Map state to SmartThings command."""
    mapping = {
        "on": "on",
        "off": "off",
        "locked": "lock",
        "unlocked": "unlock",
        "open": "open",
        "closed": "close",
    }
    return mapping.get(state, state)


# ══════════════════════════════════════════════════════════════════════════
# LLM-BASED TAP RULE GENERATOR
# ══════════════════════════════════════════════════════════════════════════

# IoT Bridge device-type → room mapping (same as IoTBridge.ROOM_DEVICES)
_ROOM_DEVICE_DEFAULTS = {
    "living room": ["motion_sensor", "smart_light", "temperature_sensor"],
    "bedroom":     ["motion_sensor", "smart_light", "temperature_sensor"],
    "kitchen":     ["motion_sensor", "smart_light", "water_leak_sensor"],
    "bathroom":    ["motion_sensor", "water_leak_sensor", "humidity_sensor"],
    "office":      ["motion_sensor", "smart_light"],
    "hallway":     ["motion_sensor", "smart_light"],
    "entryway":    ["motion_sensor", "smart_door_lock", "door_sensor"],
    "closet":      ["door_sensor"],
    "garage":      ["motion_sensor", "door_sensor"],
    "laundry":     ["motion_sensor", "water_leak_sensor"],
    "dining room": ["motion_sensor", "smart_light"],
}


def _infer_devices_for_room(room_name: str) -> List[str]:
    """Infer device types for a room based on name patterns."""
    room_lower = room_name.lower()
    for pattern, devices in _ROOM_DEVICE_DEFAULTS.items():
        if pattern in room_lower or room_lower.startswith(pattern.split()[0]):
            return devices
    return ["motion_sensor"]  # fallback


class TAPRuleGenerator:
    """
    LLM-powered TAP automation rule generator.

    Given a scene's room list + device inventory, asks the LLM to produce
    realistic SmartThings-style automation rules in JSON, then parses them
    into TAPRule objects that the TAPRuleEngine can execute.

    Mirrors the pattern used by ``vesper.simulation.task_generator.TaskGenerator``
    for daily schedule generation.

    Usage::

        from vesper.agents.llm_client import LLMClient, LLMConfig
        from vesper.automation.tap_engine import TAPRuleGenerator, TAPRuleEngine

        llm = LLMClient(LLMConfig())
        gen = TAPRuleGenerator(llm_client=llm)
        rules = gen.generate_rules(
            rooms=["living room", "kitchen", "bedroom", "bathroom", "hallway", "entryway"],
        )
        engine = TAPRuleEngine(event_bus=eb, iot_bridge=bridge)
        for r in rules:
            engine.add_rule(r)
    """

    # ── Recognised trigger event types ────────────────────────────────
    KNOWN_TRIGGERS = {
        "motion_detected", "motion_cleared",
        "door_opened", "door_closed",
        "temperature_reading", "humidity_reading",
        "water_leak_detected",
        "agent_entered_room", "agent_left_room",
        "device_state_changed",
    }

    # ── Recognised device types ───────────────────────────────────────
    KNOWN_DEVICE_TYPES = {
        "motion_sensor", "smart_light", "temperature_sensor",
        "humidity_sensor", "door_sensor", "water_leak_sensor",
        "smart_door_lock", "thermostat", "smart_plug",
    }

    KNOWN_ACTIONS = {"set_device_state", "toggle_device", "send_notification"}
    KNOWN_STATES  = {"on", "off", "locked", "unlocked", "open", "closed", "triggered"}

    def __init__(self, llm_client: Any = None):
        """
        Args:
            llm_client: An instance of ``vesper.agents.llm_client.LLMClient``.
        """
        self.llm_client = llm_client

    # ── Public API ─────────────────────────────────────────────────────

    def generate_rules(
        self,
        rooms: List[str],
        room_devices: Optional[Dict[str, List[str]]] = None,
        num_rules: int = 15,
        retries: int = 2,
    ) -> List[TAPRule]:
        """Generate TAP rules using the LLM.

        Args:
            rooms: Room names in the scene.
            room_devices: Optional mapping ``room → [device_type, …]``.
                          If *None*, devices are inferred from room names.
            num_rules: Target number of rules to generate.
            retries: Number of LLM retries on failure.

        Returns:
            List of ``TAPRule`` objects ready for the engine.
        """
        if not self.llm_client:
            logger.warning("No LLM client — falling back to template rules")
            return self._fallback_rules(rooms, room_devices)

        # Build device inventory
        if room_devices is None:
            room_devices = {r: _infer_devices_for_room(r) for r in rooms}

        prompt = self._build_prompt(rooms, room_devices, num_rules)

        for attempt in range(retries + 1):
            try:
                from vesper.agents.llm_client import LLMMessage

                temp = 0.7 if attempt == 0 else 0.9
                response = self.llm_client.chat(
                    [
                        LLMMessage(
                            "system",
                            "You are a smart-home automation expert. "
                            "Generate realistic SmartThings / Home Assistant style "
                            "automation rules in JSON format. "
                            "Output ONLY a valid JSON array, no other text. "
                            "DO NOT output explanations, markdown formatting, or code blocks.",
                        ),
                        LLMMessage("user", prompt),
                    ],
                    max_tokens=4096,
                    temperature=temp,
                )

                rules = self._parse_response(response.content, rooms, room_devices)
                if rules and len(rules) >= 3:
                    logger.info(
                        f"✅ LLM generated {len(rules)} TAP rules "
                        f"(attempt {attempt + 1})"
                    )
                    return rules

                logger.warning(
                    f"LLM returned only {len(rules) if rules else 0} valid rules "
                    f"(attempt {attempt + 1})"
                )
            except Exception as e:
                logger.error(f"LLM TAP generation attempt {attempt + 1} failed: {e}")

        # All retries exhausted — use template fallback
        logger.warning("LLM TAP generation failed — using template fallback")
        return self._fallback_rules(rooms, room_devices)

    # ── Prompt Construction ────────────────────────────────────────────

    def _build_prompt(
        self,
        rooms: List[str],
        room_devices: Dict[str, List[str]],
        num_rules: int,
    ) -> str:
        # Build room-device inventory
        inventory_lines = []
        for room in rooms:
            devs = room_devices.get(room, [])
            inventory_lines.append(f"  - {room}: {', '.join(devs)}")
        inventory = "\n".join(inventory_lines)

        return f"""Generate {num_rules} realistic smart-home automation rules for this house.

=== HOUSE LAYOUT & DEVICES ===
{inventory}

=== AVAILABLE TRIGGER EVENTS ===
- motion_detected  (from motion_sensor — payload has "room")
- motion_cleared   (agent leaves sensor range)
- door_opened      (from door_sensor)
- door_closed      (from door_sensor)
- temperature_reading  (from temperature_sensor — payload has "temperature" float)
- humidity_reading     (from humidity_sensor — payload has "humidity" float)
- water_leak_detected  (from water_leak_sensor)
- agent_entered_room   (agent walks into a room — payload has "room")
- agent_left_room      (agent walks out of a room — payload has "room")

=== AVAILABLE ACTIONS ===
- set_device_state: set a device to a state ("on", "off", "locked", "unlocked")
- send_notification: send an alert with "message" and "severity" (info / warning / critical)

=== RULES TO GENERATE ===
Create a mix of these rule categories (aim for realistic coverage):
1. **Occupancy lighting** — motion/enter/leave → lights on/off
2. **Security** — door open at night → alert, auto-lock at bedtime
3. **Safety** — water leak / high temp / high humidity → critical alert
4. **Energy saving** — turn off lights when room vacated
5. **Convenience** — entryway motion → unlock door + light on
6. **Climate** — temperature thresholds → notifications or thermostat control
7. **Scheduled** — time-based rules (e.g., lock doors at 22:00, unlock at 07:00)

IMPORTANT CONSTRAINTS:
- ONLY use room names EXACTLY as listed above
- ONLY use device types that exist in each room
- trigger_event MUST be one of the trigger events listed above
- action_type MUST be "set_device_state" or "send_notification"
- Each rule MUST have at least one action
- Generate AT LEAST {num_rules} rules
- Ensure every room with a motion_sensor has at least one rule

=== OUTPUT FORMAT ===
Output a JSON array where each rule has these exact fields:
- "name": short descriptive name (e.g., "kitchen_motion_light")
- "description": one-sentence description
- "trigger_event": one of the trigger events above
- "trigger_room": room name (must match house layout exactly)
- "conditions": array of condition objects, each with:
    - "attribute": "state" | "sim_hour" | "temperature" | "humidity"
    - "operator": "eq" | "ne" | "gt" | "lt" | "between"
    - "value": string/number/array (for "between" use [lo, hi])
    - "device_type": (optional) device type to check
    - "room": (optional) room of the device to check
- "actions": array of action objects, each with:
    - "action_type": "set_device_state" | "send_notification"
    - "target_device_type": device type (for set_device_state)
    - "target_room": room name (for set_device_state)
    - "params": {{"state": "on"}} or {{"message": "...", "severity": "info"}}
- "cooldown_sec": number (seconds between re-triggers, usually 5-60)
- "tags": array of category tags (e.g., ["lighting", "occupancy"])

OUTPUT ONLY THE JSON ARRAY:
[
  {{
    "name": "kitchen_motion_light",
    "description": "Turn on kitchen light when motion detected",
    "trigger_event": "motion_detected",
    "trigger_room": "kitchen",
    "conditions": [{{"attribute": "state", "operator": "eq", "value": "off", "device_type": "smart_light", "room": "kitchen"}}],
    "actions": [{{"action_type": "set_device_state", "target_device_type": "smart_light", "target_room": "kitchen", "params": {{"state": "on"}}}}],
    "cooldown_sec": 10,
    "tags": ["lighting", "occupancy"]
  }}
]
"""

    # ── Response Parsing ───────────────────────────────────────────────

    def _parse_response(
        self,
        text: str,
        rooms: List[str],
        room_devices: Dict[str, List[str]],
    ) -> List[TAPRule]:
        """Parse LLM JSON response into validated TAPRule objects."""
        import json as _json

        text = text.strip()

        # Strip markdown wrappers
        if text.startswith("```"):
            start = text.find("[")
            if start == -1:
                start = text.find("{")
            end = max(text.rfind("]"), text.rfind("}"))
            if start != -1 and end != -1:
                text = text[start : end + 1]

        if not text.startswith("["):
            start = text.find("[")
            if start != -1:
                text = text[start:]
        if not text.endswith("]"):
            end = text.rfind("]")
            if end != -1:
                text = text[: end + 1]

        try:
            raw_rules = _json.loads(text)
        except _json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return []

        if not isinstance(raw_rules, list):
            return []

        rooms_lower = {r.lower() for r in rooms}
        rules: List[TAPRule] = []

        for i, rd in enumerate(raw_rules):
            try:
                rule = self._parse_one_rule(rd, i, rooms_lower, room_devices)
                if rule:
                    rules.append(rule)
            except Exception as e:
                logger.warning(f"Skipping invalid rule {i}: {e}")

        return rules

    def _parse_one_rule(
        self,
        rd: Dict[str, Any],
        idx: int,
        rooms_lower: set,
        room_devices: Dict[str, List[str]],
    ) -> Optional[TAPRule]:
        """Parse and validate a single rule dict."""

        name = rd.get("name", f"llm_rule_{idx}")
        desc = rd.get("description", "")
        trig_event = rd.get("trigger_event", "")
        trig_room = rd.get("trigger_room", "")
        cooldown = rd.get("cooldown_sec", 10.0)
        tags = rd.get("tags", [])

        # ── Validate trigger ──────────────────────────────────────
        if trig_event and trig_event not in self.KNOWN_TRIGGERS:
            # Try to fuzzy-match
            for kt in self.KNOWN_TRIGGERS:
                if trig_event.replace("-", "_").replace(" ", "_") == kt:
                    trig_event = kt
                    break
            else:
                logger.debug(f"Unknown trigger '{trig_event}' in rule {name}")
                # Still allow it — new event types may appear

        # Validate room
        if trig_room and trig_room.lower() not in rooms_lower:
            # Try fuzzy match
            for rl in rooms_lower:
                if trig_room.lower() in rl or rl in trig_room.lower():
                    trig_room = rl
                    break
            else:
                logger.debug(f"Room '{trig_room}' not in scene — adjusting to closest")
                trig_room = list(rooms_lower)[0] if rooms_lower else ""

        # Build trigger
        trigger_type = TriggerType.DEVICE_EVENT
        trig_kwargs: Dict[str, Any] = {"event_type": trig_event, "room": trig_room}

        if trig_event in ("agent_entered_room",):
            trigger_type = TriggerType.SCENE_ENTER
        elif trig_event in ("agent_left_room",):
            trigger_type = TriggerType.SCENE_LEAVE
        elif trig_event in ("temperature_reading", "humidity_reading"):
            # Check if there's a threshold condition that makes this a THRESHOLD trigger
            for cond in rd.get("conditions", []):
                attr = cond.get("attribute", "")
                if attr in ("temperature", "humidity") and cond.get("operator") in ("gt", "lt", "ge", "le"):
                    trigger_type = TriggerType.THRESHOLD
                    trig_kwargs["attribute"] = attr
                    trig_kwargs["threshold"] = float(cond.get("value", 0))
                    op_map = {"gt": ConditionOperator.GREATER_THAN, "lt": ConditionOperator.LESS_THAN,
                              "ge": ConditionOperator.GREATER_EQUAL, "le": ConditionOperator.LESS_EQUAL}
                    trig_kwargs["threshold_op"] = op_map.get(cond["operator"], ConditionOperator.GREATER_THAN)
                    break

        trigger = TAPTrigger(trigger_type=trigger_type, **trig_kwargs)

        # ── Parse conditions ──────────────────────────────────────
        conditions: List[TAPCondition] = []
        for cd in rd.get("conditions", []):
            op_str = cd.get("operator", "eq")
            op_map = {
                "eq": ConditionOperator.EQUALS,
                "ne": ConditionOperator.NOT_EQUALS,
                "gt": ConditionOperator.GREATER_THAN,
                "lt": ConditionOperator.LESS_THAN,
                "ge": ConditionOperator.GREATER_EQUAL,
                "le": ConditionOperator.LESS_EQUAL,
                "between": ConditionOperator.BETWEEN,
                "in": ConditionOperator.IN_SET,
            }
            conditions.append(TAPCondition(
                device_id=cd.get("device_id"),
                device_type=cd.get("device_type"),
                room=cd.get("room"),
                attribute=cd.get("attribute", "state"),
                operator=op_map.get(op_str, ConditionOperator.EQUALS),
                value=cd.get("value"),
            ))

        # ── Parse actions ─────────────────────────────────────────
        actions: List[TAPAction] = []
        for ad in rd.get("actions", []):
            act_type_str = ad.get("action_type", "set_device_state")
            act_type_map = {
                "set_device_state": ActionType.SET_DEVICE_STATE,
                "toggle_device": ActionType.TOGGLE_DEVICE,
                "send_notification": ActionType.SEND_NOTIFICATION,
                "log_event": ActionType.LOG_EVENT,
            }
            act_type = act_type_map.get(act_type_str, ActionType.SET_DEVICE_STATE)

            target_room = ad.get("target_room", "")
            if target_room and target_room.lower() not in rooms_lower:
                # Fuzzy match
                for rl in rooms_lower:
                    if target_room.lower() in rl or rl in target_room.lower():
                        target_room = rl
                        break
                else:
                    target_room = trig_room  # fallback to trigger room

            actions.append(TAPAction(
                action_type=act_type,
                target_device_id=ad.get("target_device_id"),
                target_device_type=ad.get("target_device_type"),
                target_room=target_room,
                params=ad.get("params", {}),
            ))

        if not actions:
            return None  # A rule without actions is useless

        return TAPRule(
            name=name,
            description=desc,
            trigger=trigger,
            conditions=conditions,
            actions=actions,
            cooldown_sec=float(cooldown),
            tags=tags,
        )

    # ── Template Fallback ──────────────────────────────────────────────

    def _fallback_rules(
        self,
        rooms: List[str],
        room_devices: Optional[Dict[str, List[str]]] = None,
    ) -> List[TAPRule]:
        """Generate template-based rules when LLM is unavailable.

        This produces the same rules as ``TAPRuleEngine.generate_default_rules``
        but returns them as a list instead of adding them to the engine.
        """
        if room_devices is None:
            room_devices = {r: _infer_devices_for_room(r) for r in rooms}

        rules: List[TAPRule] = []
        rooms_lower = [r.lower() for r in rooms]

        for room in rooms_lower:
            devs = set(room_devices.get(room, []))

            # Motion → Light ON
            if "motion_sensor" in devs and "smart_light" in devs:
                rules.append(TAPRule(
                    name=f"motion_lights_on_{room}",
                    description=f"Turn on {room} lights when motion detected",
                    trigger=TAPTrigger(event_type="motion_detected", room=room),
                    conditions=[TAPCondition(
                        device_type="smart_light", room=room,
                        attribute="state", operator=ConditionOperator.EQUALS, value="off",
                    )],
                    actions=[TAPAction(
                        action_type=ActionType.SET_DEVICE_STATE,
                        target_device_type="smart_light", target_room=room,
                        params={"state": "on"},
                    )],
                    cooldown_sec=10.0,
                    tags=["lighting", "occupancy"],
                ))

            # Leave → Light OFF
            if "smart_light" in devs:
                rules.append(TAPRule(
                    name=f"leave_lights_off_{room}",
                    description=f"Turn off {room} lights when agent leaves",
                    trigger=TAPTrigger(
                        trigger_type=TriggerType.SCENE_LEAVE, room=room,
                    ),
                    actions=[TAPAction(
                        action_type=ActionType.SET_DEVICE_STATE,
                        target_device_type="smart_light", target_room=room,
                        params={"state": "off"},
                    )],
                    cooldown_sec=15.0,
                    tags=["lighting", "energy_saving"],
                ))

            # Water leak → Critical alert
            if "water_leak_sensor" in devs:
                rules.append(TAPRule(
                    name=f"water_leak_alert_{room}",
                    description=f"Critical alert on water leak in {room}",
                    trigger=TAPTrigger(event_type="water_leak_detected", room=room),
                    actions=[TAPAction(
                        action_type=ActionType.SEND_NOTIFICATION,
                        params={"message": f"WATER LEAK in {room}!", "severity": "critical"},
                    )],
                    cooldown_sec=120.0,
                    tags=["safety", "water"],
                ))

            # Temperature alert
            if "temperature_sensor" in devs:
                rules.append(TAPRule(
                    name=f"temp_high_alert_{room}",
                    description=f"Alert when {room} temperature exceeds 80°F",
                    trigger=TAPTrigger(
                        trigger_type=TriggerType.THRESHOLD,
                        device_type="temperature_sensor", room=room,
                        attribute="temperature", threshold=80.0,
                        threshold_op=ConditionOperator.GREATER_THAN,
                    ),
                    actions=[TAPAction(
                        action_type=ActionType.SEND_NOTIFICATION,
                        params={"message": f"High temperature in {room}!", "severity": "warning"},
                    )],
                    cooldown_sec=60.0,
                    tags=["climate", "safety"],
                ))

            # Entryway: motion → unlock + light
            if "entryway" in room or "entrance" in room:
                rules.append(TAPRule(
                    name=f"entryway_welcome_{room}",
                    description=f"Unlock door and turn on light on motion in {room}",
                    trigger=TAPTrigger(event_type="motion_detected", room=room),
                    actions=[
                        TAPAction(action_type=ActionType.SET_DEVICE_STATE,
                                  target_device_type="smart_door_lock", target_room=room,
                                  params={"state": "unlocked"}),
                        TAPAction(action_type=ActionType.SET_DEVICE_STATE,
                                  target_device_type="smart_light", target_room=room,
                                  params={"state": "on"}),
                    ],
                    cooldown_sec=30.0,
                    tags=["security", "welcome"],
                ))

        # Global: night lock / morning unlock
        entryway = next((r for r in rooms_lower if "entryway" in r or "entrance" in r), None)
        if entryway:
            rules.append(TAPRule(
                name="night_lock_doors",
                description="Lock all doors at 10 PM",
                trigger=TAPTrigger(trigger_type=TriggerType.TIME_OF_DAY, trigger_hour=22.0),
                actions=[
                    TAPAction(action_type=ActionType.SET_DEVICE_STATE,
                              target_device_type="smart_door_lock", target_room=entryway,
                              params={"state": "locked"}),
                    TAPAction(action_type=ActionType.SEND_NOTIFICATION,
                              params={"message": "Night mode: doors locked", "severity": "info"}),
                ],
                cooldown_sec=3600.0,
                tags=["security", "schedule"],
            ))
            rules.append(TAPRule(
                name="morning_unlock_doors",
                description="Unlock doors at 7 AM",
                trigger=TAPTrigger(trigger_type=TriggerType.TIME_OF_DAY, trigger_hour=7.0),
                actions=[TAPAction(
                    action_type=ActionType.SET_DEVICE_STATE,
                    target_device_type="smart_door_lock", target_room=entryway,
                    params={"state": "unlocked"},
                )],
                cooldown_sec=3600.0,
                tags=["security", "schedule"],
            ))

        return rules
