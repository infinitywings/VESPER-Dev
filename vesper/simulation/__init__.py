"""
Simulation module for autonomous daily life simulation.

Provides:
- Time management with real-world synchronization
- Task system for humanoid activities
- Task generation with LLM support
- Task database for history and analytics
- Event stream for coordination
"""

from vesper.simulation.time_manager import (
    TimeManager,
    TimeConfig,
    TimeOfDay,
    DayOfWeek,
    ScheduledEvent,
)

from vesper.simulation.task_system import (
    Task,
    TaskAction,
    TaskCategory,
    TaskExecutor,
    TaskFactory,
    TaskLocation,
    TaskPriority,
    TaskStatus,
    DailySchedule,
)

from vesper.simulation.task_generator import (
    TaskGenerator,
    HumanoidPersona,
    RoomInfo,
)

from vesper.simulation.task_database import TaskDatabase

from vesper.simulation.event_stream import (
    Event,
    EventType,
    EventStream,
    EventLogger,
    SimulationCoordinator,
)

from vesper.simulation.autonomous_simulation import AutonomousSimulation

# Re-export the top-level Simulation runner as an alias for VesperEngine.
# The original class lived in vesper/simulation.py which has been replaced
# by vesper/engine.py.  The alias preserves backward compatibility.
from vesper.engine import VesperEngine as Simulation, EngineStats as SimulationStats

__all__ = [
    # Time management
    "TimeManager",
    "TimeConfig",
    "TimeOfDay",
    "DayOfWeek",
    "ScheduledEvent",
    # Task system
    "Task",
    "TaskAction",
    "TaskCategory",
    "TaskExecutor",
    "TaskFactory",
    "TaskLocation",
    "TaskPriority",
    "TaskStatus",
    "DailySchedule",
    # Task generation
    "TaskGenerator",
    "HumanoidPersona",
    "RoomInfo",
    # Database
    "TaskDatabase",
    # Event stream
    "Event",
    "EventType",
    "EventStream",
    "EventLogger",
    "SimulationCoordinator",
    # Autonomous simulation
    "AutonomousSimulation",
    # Top-level simulation runner
    "Simulation",
    "SimulationStats",
]
