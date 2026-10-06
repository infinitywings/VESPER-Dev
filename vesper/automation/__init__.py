"""
VESPER Automation Engine — TAP (Trigger-Action Programming) Rules.

Provides a realistic smart-home automation system that:
- Models real SmartThings / IFTTT / Home Assistant style rules
- Integrates with the 3D Habitat simulation via EventBus
- Tracks rule execution metrics for attack-impact analysis
- Supports conditions/guards (time-of-day, device state, thresholds)
- Generates default rules per scene that mirror real-world deployments

For MobiCom: We measure how attacks disrupt these automation rules,
causing lights to stay off, doors to remain unlocked, temperatures
to go uncontrolled, etc. — directly impacting humanoid task completion.
"""

from vesper.automation.tap_engine import (
    TAPRule,
    TAPRuleEngine,
    TAPTrigger,
    TAPCondition,
    TAPAction,
    TAPRuleMetrics,
    RuleExecutionRecord,
    ConditionOperator,
    ActionType,
    TriggerType,
    TAPRuleGenerator,
)

__all__ = [
    "TAPRule",
    "TAPRuleEngine",
    "TAPTrigger",
    "TAPCondition",
    "TAPAction",
    "TAPRuleMetrics",
    "RuleExecutionRecord",
    "ConditionOperator",
    "ActionType",
    "TriggerType",
    "TAPRuleGenerator",
]
