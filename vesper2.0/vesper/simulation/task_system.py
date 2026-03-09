"""
Task System for humanoid daily activities.

Defines tasks, activities, and routines that humanoids can perform
throughout their simulated day.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TaskPriority(Enum):
    """Task priority levels."""
    CRITICAL = 0   # Must do (e.g., sleep, eat)
    HIGH = 1       # Important (e.g., work, appointments)
    MEDIUM = 2     # Normal activities
    LOW = 3        # Optional/leisure
    BACKGROUND = 4 # Can be interrupted anytime


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCategory(Enum):
    """Categories of daily tasks."""
    SLEEP = "sleep"
    HYGIENE = "hygiene"
    EATING = "eating"
    WORK = "work"
    EXERCISE = "exercise"
    LEISURE = "leisure"
    SOCIAL = "social"
    HOUSEHOLD = "household"
    ERRANDS = "errands"
    IDLE = "idle"


@dataclass
class TaskLocation:
    """Location specification for a task."""
    room_name: str
    object_name: Optional[str] = None  # Specific object to interact with
    position: Optional[Tuple[float, float, float]] = None  # Specific position


@dataclass
class TaskAction:
    """A single action within a task."""
    action_type: str  # e.g., "walk", "interact", "wait", "animation"
    target: Optional[str] = None  # Target object/location
    duration: Optional[float] = None  # Duration in seconds
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    """
    A task that a humanoid can perform.
    
    Contains all information needed to execute an activity,
    including location, actions, and timing.
    """
    task_id: str
    name: str
    category: TaskCategory
    priority: TaskPriority = TaskPriority.MEDIUM
    
    # Timing
    duration: timedelta = field(default_factory=lambda: timedelta(minutes=15))
    min_duration: Optional[timedelta] = None  # Minimum if interrupted
    scheduled_time: Optional[datetime] = None
    deadline: Optional[datetime] = None
    
    # Location
    location: Optional[TaskLocation] = None
    
    # Actions to perform
    actions: List[TaskAction] = field(default_factory=list)
    
    # Execution state
    status: TaskStatus = TaskStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress: float = 0.0  # 0.0 to 1.0
    
    # Dependencies and conflicts
    requires_tasks: List[str] = field(default_factory=list)
    conflicts_with: List[TaskCategory] = field(default_factory=list)
    
    # Metadata
    description: Optional[str] = None
    iot_triggers: Dict[str, Any] = field(default_factory=dict)  # IoT events to trigger
    
    def can_interrupt(self) -> bool:
        """Check if task can be interrupted."""
        return self.priority.value >= TaskPriority.MEDIUM.value
    
    def is_active(self) -> bool:
        """Check if task is currently active."""
        return self.status == TaskStatus.IN_PROGRESS
    
    def is_complete(self) -> bool:
        """Check if task is finished."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
    
    def start(self, current_time: datetime):
        """Start the task."""
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = current_time
        self.progress = 0.0
        logger.info(f"Task started: {self.name}")
    
    def update_progress(self, elapsed: timedelta):
        """Update task progress based on elapsed time."""
        if self.duration.total_seconds() > 0:
            self.progress = min(1.0, elapsed.total_seconds() / self.duration.total_seconds())
    
    def complete(self, current_time: datetime):
        """Mark task as completed."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = current_time
        self.progress = 1.0
        logger.info(f"Task completed: {self.name}")
    
    def interrupt(self, current_time: datetime) -> bool:
        """Attempt to interrupt the task."""
        if not self.can_interrupt():
            return False
        
        self.status = TaskStatus.INTERRUPTED
        self.completed_at = current_time
        logger.info(f"Task interrupted: {self.name} at {self.progress:.1%}")
        return True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "category": self.category.value,
            "priority": self.priority.name,
            "status": self.status.value,
            "progress": self.progress,
            "duration_minutes": self.duration.total_seconds() / 60,
            "location": self.location.room_name if self.location else None,
        }


@dataclass
class DailySchedule:
    """A day's schedule of tasks."""
    date: datetime
    tasks: List[Task] = field(default_factory=list)
    
    def get_tasks_at(self, time: datetime) -> List[Task]:
        """Get tasks scheduled for a specific time."""
        return [
            task for task in self.tasks
            if task.scheduled_time and 
            task.scheduled_time <= time < task.scheduled_time + task.duration
        ]
    
    def get_next_task(self, after: datetime) -> Optional[Task]:
        """Get the next pending task after a time."""
        pending = [
            task for task in self.tasks
            if task.status == TaskStatus.PENDING and
            task.scheduled_time and task.scheduled_time > after
        ]
        if pending:
            return min(pending, key=lambda t: t.scheduled_time)
        return None
    
    def add_task(self, task: Task):
        """Add a task to the schedule."""
        self.tasks.append(task)
        self.tasks.sort(key=lambda t: t.scheduled_time or datetime.max)


class TaskExecutor:
    """
    Executes tasks by coordinating with the humanoid agent.
    """
    
    def __init__(self):
        """Initialize the task executor."""
        self.current_task: Optional[Task] = None
        self.action_index = 0
        self._action_start_time: Optional[datetime] = None
        
        # Callbacks
        self._on_task_complete: List[Callable[[Task], None]] = []
        self._on_action_complete: List[Callable[[Task, TaskAction], None]] = []
        self._on_iot_trigger: List[Callable[[str, Any], None]] = []
    
    def start_task(self, task: Task, current_time: datetime):
        """Start executing a task."""
        if self.current_task and self.current_task.is_active():
            if not self.current_task.interrupt(current_time):
                logger.warning(f"Cannot interrupt current task: {self.current_task.name}")
                return False
        
        self.current_task = task
        self.action_index = 0
        task.start(current_time)
        
        # Start first action
        if task.actions:
            self._action_start_time = current_time
        
        return True
    
    def update(self, current_time: datetime, dt: float) -> Optional[TaskAction]:
        """
        Update task execution.
        
        Args:
            current_time: Current simulation time
            dt: Delta time in seconds
            
        Returns:
            Current action if any
        """
        if not self.current_task or not self.current_task.is_active():
            return None
        
        task = self.current_task
        
        # Update overall progress
        if task.started_at:
            task.update_progress(current_time - task.started_at)
        
        # Check if task should complete
        if task.progress >= 1.0:
            self._complete_task(current_time)
            return None
        
        # Process current action
        if task.actions and self.action_index < len(task.actions):
            action = task.actions[self.action_index]
            
            # Check if action is complete
            if action.duration and self._action_start_time:
                action_elapsed = (current_time - self._action_start_time).total_seconds()
                if action_elapsed >= action.duration:
                    self._complete_action(action, current_time)
                    return self._get_current_action()
            
            return action
        
        return None
    
    def _get_current_action(self) -> Optional[TaskAction]:
        """Get current action."""
        if (self.current_task and 
            self.current_task.actions and 
            self.action_index < len(self.current_task.actions)):
            return self.current_task.actions[self.action_index]
        return None
    
    def _complete_action(self, action: TaskAction, current_time: datetime):
        """Complete an action and move to next."""
        for callback in self._on_action_complete:
            try:
                callback(self.current_task, action)
            except Exception as e:
                logger.error(f"Action complete callback error: {e}")
        
        self.action_index += 1
        self._action_start_time = current_time
    
    def _complete_task(self, current_time: datetime):
        """Complete the current task."""
        if not self.current_task:
            return
        
        task = self.current_task
        task.complete(current_time)
        
        # Trigger IoT events
        for device_id, event in task.iot_triggers.items():
            for callback in self._on_iot_trigger:
                try:
                    callback(device_id, event)
                except Exception as e:
                    logger.error(f"IoT trigger callback error: {e}")
        
        # Notify callbacks
        for callback in self._on_task_complete:
            try:
                callback(task)
            except Exception as e:
                logger.error(f"Task complete callback error: {e}")
        
        self.current_task = None
        self.action_index = 0
    
    def on_task_complete(self, callback: Callable[[Task], None]):
        """Register callback for task completion."""
        self._on_task_complete.append(callback)
    
    def on_action_complete(self, callback: Callable[[Task, TaskAction], None]):
        """Register callback for action completion."""
        self._on_action_complete.append(callback)
    
    def on_iot_trigger(self, callback: Callable[[str, Any], None]):
        """Register callback for IoT triggers."""
        self._on_iot_trigger.append(callback)


class TaskFactory:
    """
    Factory for creating common tasks.
    """
    
    _task_counter = 0
    
    @classmethod
    def _next_id(cls) -> str:
        cls._task_counter += 1
        return f"task_{cls._task_counter}"
    
    @classmethod
    def wake_up(cls, room: str = "bedroom") -> Task:
        """Create a wake up task."""
        return Task(
            task_id=cls._next_id(),
            name="Wake Up",
            category=TaskCategory.SLEEP,
            priority=TaskPriority.CRITICAL,
            duration=timedelta(minutes=5),
            location=TaskLocation(room_name=room),
            actions=[
                TaskAction("animation", "wake_up", duration=3.0),
                TaskAction("wait", duration=2.0),
            ],
            description="Wake up and get out of bed",
        )
    
    @classmethod
    def sleep(cls, room: str = "bedroom", duration_hours: float = 8.0) -> Task:
        """Create a sleep task."""
        return Task(
            task_id=cls._next_id(),
            name="Sleep",
            category=TaskCategory.SLEEP,
            priority=TaskPriority.CRITICAL,
            duration=timedelta(hours=duration_hours),
            location=TaskLocation(room_name=room, object_name="bed"),
            actions=[
                TaskAction("walk", room),
                TaskAction("interact", "bed"),
                TaskAction("animation", "sleep", duration=duration_hours * 3600),
            ],
            description="Sleep in bed",
            iot_triggers={"bedroom_light": {"action": "off"}},
        )
    
    @classmethod
    def eat_meal(cls, meal_type: str = "breakfast", room: str = "kitchen") -> Task:
        """Create a meal task."""
        durations = {"breakfast": 20, "lunch": 30, "dinner": 45, "snack": 10}
        duration = durations.get(meal_type, 20)
        
        return Task(
            task_id=cls._next_id(),
            name=f"Eat {meal_type.title()}",
            category=TaskCategory.EATING,
            priority=TaskPriority.HIGH,
            duration=timedelta(minutes=duration),
            location=TaskLocation(room_name=room),
            actions=[
                TaskAction("walk", room),
                TaskAction("interact", "refrigerator", duration=5.0),
                TaskAction("wait", duration=duration * 60 - 5),
            ],
            description=f"Eat {meal_type}",
        )
    
    @classmethod
    def work(cls, room: str = "office", duration_hours: float = 4.0) -> Task:
        """Create a work task."""
        return Task(
            task_id=cls._next_id(),
            name="Work",
            category=TaskCategory.WORK,
            priority=TaskPriority.HIGH,
            duration=timedelta(hours=duration_hours),
            location=TaskLocation(room_name=room, object_name="desk"),
            actions=[
                TaskAction("walk", room),
                TaskAction("sit", "chair"),
                TaskAction("work", duration=duration_hours * 3600),
            ],
            description="Work at desk",
        )
    
    @classmethod
    def watch_tv(cls, room: str = "living room", duration_minutes: float = 60.0) -> Task:
        """Create a TV watching task."""
        return Task(
            task_id=cls._next_id(),
            name="Watch TV",
            category=TaskCategory.LEISURE,
            priority=TaskPriority.LOW,
            duration=timedelta(minutes=duration_minutes),
            location=TaskLocation(room_name=room, object_name="sofa"),
            actions=[
                TaskAction("walk", room),
                TaskAction("sit", "sofa"),
                TaskAction("watch", "tv", duration=duration_minutes * 60),
            ],
            description="Watch TV",
            iot_triggers={"living_room_light": {"action": "dim", "level": 50}},
        )
    
    @classmethod
    def use_bathroom(cls, room: str = "bathroom", duration_minutes: float = 10.0) -> Task:
        """Create a bathroom task."""
        return Task(
            task_id=cls._next_id(),
            name="Use Bathroom",
            category=TaskCategory.HYGIENE,
            priority=TaskPriority.HIGH,
            duration=timedelta(minutes=duration_minutes),
            location=TaskLocation(room_name=room),
            actions=[
                TaskAction("walk", room),
                TaskAction("interact", "toilet", duration=duration_minutes * 60),
            ],
            description="Use bathroom",
            iot_triggers={"bathroom_light": {"action": "on"}},
        )
    
    @classmethod
    def shower(cls, room: str = "bathroom") -> Task:
        """Create a shower task."""
        return Task(
            task_id=cls._next_id(),
            name="Take Shower",
            category=TaskCategory.HYGIENE,
            priority=TaskPriority.HIGH,
            duration=timedelta(minutes=15),
            location=TaskLocation(room_name=room, object_name="shower"),
            actions=[
                TaskAction("walk", room),
                TaskAction("interact", "shower", duration=900),
            ],
            description="Take a shower",
            iot_triggers={"bathroom_light": {"action": "on"}},
        )
    
    @classmethod
    def exercise(cls, room: str = "living room", duration_minutes: float = 30.0) -> Task:
        """Create an exercise task."""
        return Task(
            task_id=cls._next_id(),
            name="Exercise",
            category=TaskCategory.EXERCISE,
            priority=TaskPriority.MEDIUM,
            duration=timedelta(minutes=duration_minutes),
            location=TaskLocation(room_name=room),
            actions=[
                TaskAction("walk", room),
                TaskAction("animation", "exercise", duration=duration_minutes * 60),
            ],
            description="Do exercise",
        )
    
    @classmethod
    def idle(cls, room: str = "living room", duration_minutes: float = 15.0) -> Task:
        """Create an idle/waiting task."""
        return Task(
            task_id=cls._next_id(),
            name="Idle",
            category=TaskCategory.IDLE,
            priority=TaskPriority.BACKGROUND,
            duration=timedelta(minutes=duration_minutes),
            location=TaskLocation(room_name=room),
            actions=[
                TaskAction("wait", duration=duration_minutes * 60),
            ],
            description="Idle time",
        )
    
    @classmethod
    def walk_to(cls, room: str, duration_seconds: float = 30.0) -> Task:
        """Create a simple walk task."""
        return Task(
            task_id=cls._next_id(),
            name=f"Walk to {room}",
            category=TaskCategory.IDLE,
            priority=TaskPriority.BACKGROUND,
            duration=timedelta(seconds=duration_seconds),
            location=TaskLocation(room_name=room),
            actions=[
                TaskAction("walk", room, duration=duration_seconds),
            ],
            description=f"Walk to {room}",
        )
