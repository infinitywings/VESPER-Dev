"""
VESPER Task Manager for LLM-controlled humanoid agents.

Handles task generation, spatial awareness, and autonomous navigation.
The LLM receives real room/device locations for contextual task generation.
"""

import random
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from vesper.core.event_bus import EventBus

# Try to import LLM client
try:
    from vesper.agents.llm_client import LLMClient, LLMConfig
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False


# Default daily tasks for fallback when LLM is unavailable
DAILY_TASKS = [
    "Walk to the kitchen and check the refrigerator",
    "Go to the living room and sit on the couch",
    "Walk to the bedroom and make the bed",
    "Check if the front door is locked",
    "Walk to the bathroom",
    "Look out the window in the living room",
    "Walk around the house to patrol",
    "Go to the kitchen to prepare breakfast",
    "Sit at the dining table",
    "Walk to the bedroom to rest",
    "Check all the doors in the house",
    "Walk to each room to turn off the lights",
    "Go to the living room to watch TV",
    "Walk to the front door to check for packages",
    "Patrol the perimeter of the house",
]


class TaskManager:
    """
    Manages LLM-generated tasks for humanoid agents with spatial awareness.
    
    The task manager:
    1. Receives room/device layout from SmartHomeIoT
    2. Provides spatial context to LLM for intelligent task generation
    3. Parses tasks to determine navigation targets
    4. Controls autonomous movement toward task locations
    """
    
    def __init__(
        self,
        event_bus: EventBus,
        smart_home,  # SmartHomeIoT instance
        use_llm: bool = True,
    ):
        self.event_bus = event_bus
        self.smart_home = smart_home
        self.use_llm = use_llm and LLM_AVAILABLE
        
        # LLM client
        self.llm_client: Optional[LLMClient] = None
        if self.use_llm:
            try:
                self.llm_client = LLMClient(LLMConfig(
                    model="local-model",
                    max_tokens=150,
                    timeout=10.0,  # Shorter timeout
                ))
                print("[LLM] Task generator ready")
            except Exception as e:
                print(f"[LLM] Failed to initialize: {e}")
                self.use_llm = False
        
        # Task state
        self.current_task: Optional[str] = None
        self.task_history: List[str] = []
        self.task_queue: List[str] = []
        
        # Timing
        self.last_task_time = 0.0
        self.task_interval = 15.0  # Generate new task every 15 seconds
        self.task_duration = 10.0  # Each task lasts 10 seconds
        
        self.is_idle = True
        self.task_start_time = 0.0
        
        # Navigation state
        self.current_target: Optional[Tuple[float, float, float]] = None
        self.execution_state = "idle"  # idle, moving, executing, completed
        self.move_speed = 1.0  # Movement speed (realistic walking speed)
        self.arrival_threshold = 1.0  # Distance to consider "arrived"
        
        # Scene-specific room definitions (can be updated by simulation)
        self.scene_rooms: Optional[Dict] = None
        
        # Agent facing direction tracking for proper turning
        self.agent_facing = 0.0  # radians, 0 = +Z direction
    
    def get_spatial_context(self) -> str:
        """Build LLM context with actual room/device layout."""
        # Get current agent state from sensors
        triggered_rooms = self.smart_home.get_triggered_rooms()
        door_states = self.smart_home.get_door_states()
        room_info = self.smart_home.get_room_info()
        
        current_time = time.strftime('%H:%M')
        
        context = f"""You are controlling a person in a smart home simulation.
Current time: {current_time}

{room_info}

Current sensor readings:
- Rooms with motion detected: {', '.join(triggered_rooms) if triggered_rooms else 'none'}
- Door states: {door_states}

Recent completed tasks: {', '.join(self.task_history[-3:]) if self.task_history else 'none'}

Generate ONE simple task for the person to do. The task should involve walking to a specific room or checking a specific device. Be specific about the location.

Respond with ONLY the task description, nothing else."""
        
        return context
    
    def generate_task(self) -> str:
        """Generate a new task using LLM with spatial context."""
        if self.use_llm and self.llm_client:
            try:
                prompt = self.get_spatial_context()
                response = self.llm_client.complete(prompt)
                task = response.content.strip().strip('"')
                print(f"[LLM] Generated: {task}")
                return task
            except Exception as e:
                print(f"[LLM] Error: {e}")
        
        # Fallback to random task
        return random.choice(DAILY_TASKS)
    
    def _get_room_location(self, room_name: str) -> Optional[Tuple[float, float, float]]:
        """Get room location, preferring scene-specific rooms if available."""
        # First try scene-specific rooms (from navmesh analysis)
        if self.scene_rooms and room_name in self.scene_rooms:
            center = self.scene_rooms[room_name]["center"]
            return center
        
        # Fall back to smart_home's room data
        return self.smart_home.get_room_location(room_name)
    
    def parse_task_location(self, task: str) -> Optional[Tuple[float, float, float]]:
        """Parse task text to determine target location using actual room data."""
        task_lower = task.lower()
        
        # Check for room keywords
        room_keywords = {
            "kitchen": ["kitchen", "cook", "refrigerator", "fridge", "food", "eat", "stove"],
            "bedroom": ["bedroom", "bed", "sleep", "rest", "wake"],
            "bathroom": ["bathroom", "toilet", "shower", "bath"],
            "living_room": ["living", "couch", "tv", "watch", "sofa", "relax"],
            "hallway": ["hallway", "hall", "corridor", "entrance", "front door"],
        }
        
        for room_name, keywords in room_keywords.items():
            if any(kw in task_lower for kw in keywords):
                loc = self._get_room_location(room_name)
                if loc:
                    print(f"[Task] Target room: {room_name} at ({loc[0]:.1f}, {loc[2]:.1f})")
                    return loc
        
        # Check for door keywords
        door_keywords = {
            "front": ["front door", "entrance", "main door", "package"],
            "bedroom": ["bedroom door"],
            "bathroom": ["bathroom door"],
        }
        
        for door_prefix, keywords in door_keywords.items():
            if any(kw in task_lower for kw in keywords):
                loc = self.smart_home.get_door_location(door_prefix)
                if loc:
                    return loc
        
        # Check for patrol/walk around
        if any(word in task_lower for word in ["patrol", "walk around", "check all", "tour"]):
            # Return random room location
            rooms = ["living_room", "kitchen", "bedroom", "bathroom"]
            room_name = random.choice(rooms)
            return self._get_room_location(room_name)
        
        # Default: pick a random room
        rooms = ["living_room", "kitchen", "bedroom"]
        room_name = random.choice(rooms)
        return self._get_room_location(room_name)
    
    def calculate_movement(self, agent_pos: np.ndarray, agent_facing: float = None) -> Tuple[float, float]:
        """
        Calculate movement direction toward target.
        
        Args:
            agent_pos: Current agent position [x, y, z]
            agent_facing: Agent's facing direction in radians (optional)
            
        Returns:
            Tuple of (forward_velocity, turn_velocity)
        """
        if self.current_target is None:
            return (0.0, 0.0)
        
        # Calculate vector to target (in XZ plane)
        dx = self.current_target[0] - agent_pos[0]
        dz = self.current_target[2] - agent_pos[2]
        distance = np.sqrt(dx * dx + dz * dz)
        
        # Debug: print distance periodically
        if hasattr(self, '_debug_counter'):
            self._debug_counter += 1
        else:
            self._debug_counter = 0
        
        if self._debug_counter % 60 == 0:  # Every ~1 second at 60fps
            print(f"[Nav] Agent: ({agent_pos[0]:.1f}, {agent_pos[2]:.1f}) -> Target: ({self.current_target[0]:.1f}, {self.current_target[2]:.1f}) Distance: {distance:.2f}m")
        
        # Check if arrived
        if distance < self.arrival_threshold:
            print(f"[Nav] ARRIVED! Distance: {distance:.2f}m < threshold: {self.arrival_threshold}m")
            return (0.0, 0.0)
        
        # Calculate movement command
        # Habitat uses [forward velocity, angular velocity]
        # We need to turn toward target then move forward
        
        # Calculate angle to target
        angle_to_target = np.arctan2(dx, dz)
        
        # Get agent's current facing direction (approximated)
        # For now, we use a simple proportional controller
        turn = np.clip(angle_to_target * 2.0, -1.0, 1.0) * self.move_speed
        
        # Move forward proportional to how aligned we are
        alignment = np.cos(angle_to_target)
        forward = np.clip(alignment * self.move_speed, -self.move_speed, self.move_speed)
        
        # If we're mostly aligned, move forward; otherwise prioritize turning
        if abs(angle_to_target) > 0.5:  # More than ~30 degrees off
            forward *= 0.3  # Slow down forward motion while turning
        
        # Debug output movement
        if self._debug_counter % 60 == 0:
            print(f"[Nav] Movement: forward={forward:.2f}, turn={turn:.2f}")
        
        return (forward, turn)
    
    def update(self, current_time: float, agent_pos: np.ndarray) -> Tuple[Optional[str], Tuple[float, float]]:
        """
        Update task state and return (new_task, movement_action).
        
        Returns:
            Tuple of (new_task_if_generated, (forward_vel, turn_vel))
        """
        movement = (0.0, 0.0)
        new_task = None
        
        # Handle current task execution
        if self.current_task and not self.is_idle:
            
            if self.execution_state == "idle" and self.current_target is None:
                # Parse task to find target location
                self.current_target = self.parse_task_location(self.current_task)
                if self.current_target:
                    self.execution_state = "moving"
                    print(f"[Task] Navigating to: ({self.current_target[0]:.1f}, {self.current_target[2]:.1f})")
                else:
                    # No location found, just execute in place
                    self.execution_state = "executing"
            
            elif self.execution_state == "moving":
                # Calculate movement toward target
                movement = self.calculate_movement(agent_pos)
                
                # Check if arrived
                if movement == (0.0, 0.0):
                    self.execution_state = "executing"
                    print(f"[Task] Arrived at destination, executing task")
            
            elif self.execution_state == "executing":
                # Execute task for a few seconds then complete
                elapsed = current_time - self.task_start_time
                if elapsed > self.task_duration - 5.0:  # Last 5 seconds of task
                    self.execution_state = "completed"
            
            # Check if task duration exceeded
            if current_time - self.task_start_time > self.task_duration:
                # Complete current task
                self.task_history.append(self.current_task)
                if len(self.task_history) > 10:
                    self.task_history.pop(0)
                
                print(f"[Task] Completed: {self.current_task[:50]}...")
                
                self.current_task = None
                self.current_target = None
                self.execution_state = "idle"
                self.is_idle = True
        
        # Generate new task if idle
        if self.is_idle and current_time - self.last_task_time > self.task_interval:
            if self.task_queue:
                new_task_text = self.task_queue.pop(0)
            else:
                new_task_text = self.generate_task()
            
            self.current_task = new_task_text
            self.task_start_time = current_time
            self.last_task_time = current_time
            self.is_idle = False
            self.execution_state = "idle"
            self.current_target = None
            
            new_task = new_task_text
            print(f"[Task] New: {new_task}")
        
        return new_task, movement
    
    def force_new_task(self, sim_time: float) -> str:
        """Force generate a new task immediately."""
        new_task = self.generate_task()
        self.current_task = new_task
        self.is_idle = False
        self.task_start_time = sim_time
        self.last_task_time = sim_time
        self.execution_state = "idle"
        self.current_target = None
        return new_task
    
    def get_status(self) -> Dict[str, Any]:
        """Get current task status for UI display."""
        return {
            "current_task": self.current_task,
            "is_idle": self.is_idle,
            "tasks_completed": len(self.task_history),
            "queue_length": len(self.task_queue),
            "execution_state": self.execution_state,
            "current_target": self.current_target,
        }
