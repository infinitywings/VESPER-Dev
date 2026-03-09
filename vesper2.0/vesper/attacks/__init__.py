"""
VESPER Attack Framework

Security testing modules for IoT firmware, network, and protocol-level attacks.
Includes reproduction of published IoT attacks (Fu et al., DSN 2022).
"""

from vesper.attacks.firmware_attacks import (
    FirmwareAttackFramework,
    FirmwareTarget,
    AttackCategory,
    AttackSeverity,
    AttackResult,
)
from vesper.attacks.network_attacks import (
    NetworkAttackFramework,
    NetworkTarget,
    NetworkAttackCategory,
    NetworkAttackResult,
)
from vesper.attacks.phantom_delay_attack import (
    PhantomDelayAttackSuite,
    PhantomDelayProxy,
    PhantomDelayConfig,
    DelayAttackResult,
    DelayAttackVariant,
    PhantomDelayCategory,
)

__all__ = [
    "FirmwareAttackFramework",
    "FirmwareTarget",
    "AttackCategory",
    "AttackSeverity",
    "AttackResult",
    "NetworkAttackFramework",
    "NetworkTarget",
    "NetworkAttackCategory",
    "NetworkAttackResult",
    "PhantomDelayAttackSuite",
    "PhantomDelayProxy",
    "PhantomDelayConfig",
    "DelayAttackResult",
    "DelayAttackVariant",
    "PhantomDelayCategory",
]
