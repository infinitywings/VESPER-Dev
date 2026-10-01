"""
Task Generator using LLM for realistic daily schedule creation.

Generates contextually appropriate daily schedules based on:
- Time of day
- Day of week (weekday vs weekend)
- Humanoid persona/preferences
- Available rooms and objects
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .task_system import (
    DailySchedule,
    Task,
    TaskCategory,
    TaskFactory,
    TaskLocation,
    TaskPriority,
)
from .time_manager import TimeOfDay

logger = logging.getLogger(__name__)


@dataclass
class HumanoidPersona:
    """
    Persona defining humanoid characteristics and preferences.

    Influences daily schedule generation.
    """
    name: str = "Alex"
    age: int = 30
    occupation: str = "Remote Worker"

    # Sleep preferences
    wake_time: str = "07:00"
    sleep_time: str = "23:00"

    # Work schedule
    works_from_home: bool = True
    work_start: str = "09:00"
    work_end: str = "17:00"
    work_days: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon-Fri

    # Activity preferences (0-1 scale)
    exercise_frequency: float = 0.5  # How often they exercise
    social_frequency: float = 0.3   # How often they have social activities
    tv_time: float = 0.6           # Tendency to watch TV

    # Meal preferences
    breakfast_time: str = "07:30"
    lunch_time: str = "12:30"
    dinner_time: str = "19:00"

    def to_prompt(self) -> str:
        """Convert persona to LLM prompt context."""
        return f"""
Persona: {self.name}
- Age: {self.age}
- Occupation: {self.occupation}
- {'Works from home' if self.works_from_home else 'Commutes to work'}
- Work hours: {self.work_start} to {self.work_end}
- Wake up: {self.wake_time}, Sleep: {self.sleep_time}
- Exercise frequency: {'Often' if self.exercise_frequency > 0.5 else 'Sometimes' if self.exercise_frequency > 0.2 else 'Rarely'}
- Social activities: {'Frequent' if self.social_frequency > 0.5 else 'Occasional' if self.social_frequency > 0.2 else 'Rare'}
"""


@dataclass
class RoomInfo:
    """Information about available rooms."""
    name: str
    objects: List[str] = field(default_factory=list)
    activities: List[TaskCategory] = field(default_factory=list)


class TaskGenerator:
    """
    Generates daily schedules using templates and optional LLM enhancement.

    Adapts to the actual house layout by using only available rooms.
    """

    def __init__(
        self,
        persona: Optional[HumanoidPersona] = None,
        rooms: Optional[List[RoomInfo]] = None,
        llm_client: Optional[Any] = None,
        available_rooms: Optional[List[str]] = None,
    ):
        """
        Initialize the task generator.

        Args:
            persona: Humanoid persona for schedule customization
            rooms: Available rooms in the environment (RoomInfo objects)
            llm_client: Optional LLM client for enhanced generation
            available_rooms: List of room names from the house layout
        """
        self.persona = persona or HumanoidPersona()
        self.llm_client = llm_client

        # Store available room names from the house layout
        self.available_room_names = [r.lower() for r in (available_rooms or [])]

        # Create room info from available rooms or use defaults
        if available_rooms:
            self.rooms = self._create_rooms_from_layout(available_rooms)
            logger.info(f"TaskGenerator using house layout: {', '.join(available_rooms)}")
        elif rooms:
            self.rooms = rooms
        else:
            self.rooms = self._default_rooms()

        # Room lookup
        self._room_map = {room.name.lower(): room for room in self.rooms}

        # Create activity-to-room mapping for quick lookup
        self._activity_room_map = self._build_activity_room_map()

    def _create_rooms_from_layout(self, room_names: List[str]) -> List[RoomInfo]:
        """Create RoomInfo objects from room names in the house layout.

        Handles Habitat-style room names like 'bedroom.004', 'rec/game',
        'laundryroom/mudroom', 'other room.001', 'bathroom.001', etc.
        """
        rooms = []

        # Define activity mappings for common room types
        # Each key is a substring to match against
        room_activity_map = {
            "bedroom": ([TaskCategory.SLEEP, TaskCategory.IDLE], ["bed", "closet"]),
            "bathroom": ([TaskCategory.HYGIENE], ["toilet", "shower", "sink"]),
            "kitchen": ([TaskCategory.EATING, TaskCategory.HOUSEHOLD], ["refrigerator", "stove", "table"]),
            "living room": ([TaskCategory.LEISURE, TaskCategory.SOCIAL, TaskCategory.EXERCISE, TaskCategory.IDLE], ["sofa", "tv"]),
            "living_room": ([TaskCategory.LEISURE, TaskCategory.SOCIAL, TaskCategory.EXERCISE, TaskCategory.IDLE], ["sofa", "tv"]),
            "office": ([TaskCategory.WORK], ["desk", "computer"]),
            "study": ([TaskCategory.WORK], ["desk", "books"]),
            "dining room": ([TaskCategory.EATING, TaskCategory.SOCIAL], ["table", "chairs"]),
            "dining_room": ([TaskCategory.EATING, TaskCategory.SOCIAL], ["table", "chairs"]),
            "dining": ([TaskCategory.EATING, TaskCategory.SOCIAL], ["table", "chairs"]),
            "hallway": ([TaskCategory.IDLE], []),
            "corridor": ([TaskCategory.IDLE], []),
            "garage": ([TaskCategory.HOUSEHOLD], ["tools"]),
            "outdoor": ([TaskCategory.LEISURE, TaskCategory.EXERCISE], []),
            "patio": ([TaskCategory.LEISURE, TaskCategory.SOCIAL], []),
            "laundry": ([TaskCategory.HOUSEHOLD], ["washer", "dryer"]),
            "mudroom": ([TaskCategory.HOUSEHOLD], []),
            "closet": ([TaskCategory.IDLE], ["closet"]),
            "toilet": ([TaskCategory.HYGIENE], ["toilet"]),
            # Habitat-specific room names
            "rec": ([TaskCategory.LEISURE, TaskCategory.SOCIAL, TaskCategory.EXERCISE], ["sofa", "tv"]),
            "game": ([TaskCategory.LEISURE, TaskCategory.SOCIAL], ["sofa", "tv"]),
            "lounge": ([TaskCategory.LEISURE, TaskCategory.SOCIAL], ["sofa", "tv"]),
            "den": ([TaskCategory.LEISURE, TaskCategory.WORK], ["sofa", "desk"]),
            "family": ([TaskCategory.LEISURE, TaskCategory.SOCIAL], ["sofa", "tv"]),
            "nursery": ([TaskCategory.IDLE], ["bed"]),
            "pantry": ([TaskCategory.EATING], []),
            "utility": ([TaskCategory.HOUSEHOLD], []),
            "balcony": ([TaskCategory.LEISURE], []),
            "foyer": ([TaskCategory.IDLE], []),
            "entryway": ([TaskCategory.IDLE], []),
        }

        for room_name in room_names:
            room_lower = room_name.lower()

            # Try exact match first
            if room_lower in room_activity_map:
                activities, objects = room_activity_map[room_lower]
                rooms.append(RoomInfo(room_name, objects, list(activities)))
                continue

            # Try matching any key as a substring of the room name
            # This handles 'bedroom.004', 'rec/game', 'laundryroom/mudroom',
            # 'bathroom.001', 'other room.001', etc.
            matched = False
            best_key = None
            best_len = 0
            for key in room_activity_map:
                # Check if key appears anywhere in room name
                if key in room_lower:
                    # Prefer longest match ("living room" over "room")
                    if len(key) > best_len:
                        best_key = key
                        best_len = len(key)

            # Also check slash-separated parts (e.g., 'rec/game' → 'rec', 'game')
            if best_key is None:
                parts = room_lower.replace('/', ' ').replace('_', ' ').split()
                for part in parts:
                    # Strip numeric suffixes like '.004'
                    base_part = part.split('.')[0]
                    if base_part in room_activity_map and len(base_part) > best_len:
                        best_key = base_part
                        best_len = len(base_part)

            if best_key is not None:
                activities, objects = room_activity_map[best_key]
                rooms.append(RoomInfo(room_name, objects, list(activities)))
                matched = True

            if not matched:
                # Default - general purpose room
                rooms.append(RoomInfo(room_name, [], [TaskCategory.IDLE, TaskCategory.LEISURE]))

        return rooms

    def _build_activity_room_map(self) -> Dict[TaskCategory, List[str]]:
        """Build a map from activity category to available rooms."""
        activity_map = {}
        for room in self.rooms:
            for activity in room.activities:
                if activity not in activity_map:
                    activity_map[activity] = []
                activity_map[activity].append(room.name)
        return activity_map

    def _find_room_for_category(self, category: TaskCategory) -> str:
        """Find an available room for a given activity category."""
        if category in self._activity_room_map and self._activity_room_map[category]:
            return self._activity_room_map[category][0]

        # Fallback: find any room that could work
        if self.rooms:
            return self.rooms[0].name
        return "living room"

    def _default_rooms(self) -> List[RoomInfo]:
        """Get default room configuration."""
        return [
            RoomInfo("bedroom", ["bed", "closet", "nightstand"],
                    [TaskCategory.SLEEP, TaskCategory.IDLE]),
            RoomInfo("bathroom", ["toilet", "shower", "sink"],
                    [TaskCategory.HYGIENE]),
            RoomInfo("kitchen", ["refrigerator", "stove", "sink", "table"],
                    [TaskCategory.EATING]),
            RoomInfo("living room", ["sofa", "tv", "coffee_table"],
                    [TaskCategory.LEISURE, TaskCategory.SOCIAL, TaskCategory.EXERCISE]),
            RoomInfo("office", ["desk", "chair", "computer"],
                    [TaskCategory.WORK]),
        ]

    def _parse_time(self, time_str: str, base_date: datetime) -> datetime:
        """Parse time string to datetime."""
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _find_room_for_activity(self, category: TaskCategory) -> str:
        """Find appropriate room for an activity category."""
        for room in self.rooms:
            if category in room.activities:
                return room.name
        return "living room"  # Default fallback

    def generate_daily_schedule(
        self,
        date: Optional[datetime] = None,
        use_llm: bool = True,
        start_time: Optional[datetime] = None,
    ) -> DailySchedule:
        """
        Generate a complete daily schedule using LLM.

        Args:
            date: Date to generate schedule for (defaults to today)
            use_llm: Ignored (always uses LLM)
            start_time: Current time to start generating tasks from

        Returns:
            Complete daily schedule
        """
        if date is None:
            date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        is_workday = date.weekday() in self.persona.work_days

        if not self.llm_client:
            raise ValueError("LLM client is required for task generation")

        return self._generate_with_llm(date, is_workday, start_time)

    def _generate_with_llm(
        self,
        date: datetime,
        is_workday: bool,
        start_time: Optional[datetime] = None,
    ) -> DailySchedule:
        """Generate schedule using LLM."""
        prompt = self._build_llm_prompt(date, is_workday, start_time)

        try:
            # Import LLMMessage here to avoid circular dependency
            from vesper.agents.llm_client import LLMMessage

            # Request schedule from LLM with increased token limit
            response = self.llm_client.chat([
                LLMMessage("system", "You are a daily schedule planner. Generate realistic, detailed daily schedules in JSON format. Output ONLY valid JSON array, no other text. DO NOT output explanations, markdown formatting, or code blocks - just the raw JSON array."),
                LLMMessage("user", prompt)
            ], max_tokens=4096)  # Increased for longer schedules

            # Log first 500 chars of response for debugging
            logger.debug(f"LLM raw response: {response.content[:500]}...")

            schedule = self._parse_llm_response(response.content, date)

            if schedule and len(schedule.tasks) >= 3:
                logger.info(f"✅ LLM generated {len(schedule.tasks)} tasks successfully")
                return schedule
            else:
                logger.warning(f"⚠️  LLM response had only {len(schedule.tasks) if schedule else 0} tasks (minimum: 3)")
                logger.warning(f"LLM response preview: {response.content[:200]}")

                # Retry with higher temperature for more creativity
                logger.info("Retrying with higher temperature...")
                response = self.llm_client.chat([
                    LLMMessage("system", "You are a daily schedule planner. Generate realistic, detailed daily schedules in JSON format. Output ONLY valid JSON array, no other text."),
                    LLMMessage("user", prompt)
                ], max_tokens=4096, temperature=0.9)

                schedule = self._parse_llm_response(response.content, date)
                if schedule and len(schedule.tasks) >= 3:
                    logger.info(f"✅ Retry successful: {len(schedule.tasks)} tasks")
                    return schedule
                else:
                    logger.warning(f"Retry failed: only {len(schedule.tasks) if schedule else 0} tasks, using emergency schedule")
                    return self._create_emergency_schedule(date, start_time)

        except Exception as e:
            logger.error(f"LLM generation failed: {e}, using emergency schedule")
            return self._create_emergency_schedule(date, start_time)

    def _create_emergency_schedule(self, date: datetime, start_time: Optional[datetime] = None) -> DailySchedule:
        """Create a simple emergency schedule when LLM fails."""
        schedule = DailySchedule(date=date)

        current_time = start_time if start_time else date.replace(hour=7, minute=0)
        current_hour = current_time.hour

        # Find rooms
        bedroom = self._find_room_for_category(TaskCategory.SLEEP)
        kitchen = self._find_room_for_category(TaskCategory.EATING)
        living_room = self._find_room_for_category(TaskCategory.LEISURE)
        bathroom = self._find_room_for_category(TaskCategory.HYGIENE)

        # Generate appropriate tasks based on time of day
        if current_hour < 12:
            # Morning
            tasks_to_add = [
                ("Morning Routine", TaskCategory.HYGIENE, bathroom, 15),
                ("Breakfast", TaskCategory.EATING, kitchen, 20),
                ("Morning Activity", TaskCategory.LEISURE, living_room, 60),
            ]
        elif current_hour < 17:
            # Afternoon
            tasks_to_add = [
                ("Afternoon Activity", TaskCategory.LEISURE, living_room, 60),
                ("Snack Break", TaskCategory.EATING, kitchen, 15),
                ("Relaxation", TaskCategory.LEISURE, living_room, 45),
            ]
        elif current_hour < 21:
            # Evening
            tasks_to_add = [
                ("Evening Relaxation", TaskCategory.LEISURE, living_room, 30),
                ("Dinner", TaskCategory.EATING, kitchen, 45),
                ("Watch TV", TaskCategory.LEISURE, living_room, 90),
            ]
        else:
            # Night
            tasks_to_add = [
                ("Night Routine", TaskCategory.HYGIENE, bathroom, 15),
                ("Reading", TaskCategory.LEISURE, bedroom, 30),
                ("Sleep", TaskCategory.SLEEP, bedroom, 420),
            ]

        for name, category, room, duration in tasks_to_add:
            task = Task(
                task_id=f"emergency_{name.lower().replace(' ', '_')}",
                name=name,
                category=category,
                priority=TaskPriority.MEDIUM,
                duration=timedelta(minutes=duration),
                location=TaskLocation(room_name=room),
                scheduled_time=current_time,
                description=f"Emergency schedule: {name}",
            )
            schedule.add_task(task)
            current_time += task.duration

        logger.info(f"Created emergency schedule with {len(schedule.tasks)} tasks")
        return schedule

    def _build_llm_prompt(self, date: datetime, is_workday: bool, start_time: Optional[datetime] = None) -> str:
        """Build prompt for LLM schedule generation with house layout info."""
        day_name = date.strftime("%A")
        day_type = "workday" if is_workday else "weekend day"

        # Add current time context and instruction
        time_context = ""
        time_instruction = "Output only valid JSON array, no other text. Generate 15-25 tasks covering the full day."

        if start_time and (start_time.hour > 0 or start_time.minute > 0):
            time_context = f"\n\nCurrent time: {start_time.strftime('%I:%M %p')}. Generate tasks starting from this time only."
            time_instruction = f"Output only valid JSON array, no other text. Generate 5-15 tasks starting from {start_time.strftime('%I:%M %p')} until bedtime. Do NOT include tasks for earlier times."

        # Build detailed room information
        rooms_list = []
        for room in self.rooms:
            activities = [cat.value for cat in room.activities] if room.activities else ["general"]
            rooms_list.append(f"  - {room.name}: suitable for {', '.join(activities)}")
        rooms_info = "\n".join(rooms_list)

        # Build room suggestions for each activity type
        activity_suggestions = []
        for category, rooms in self._activity_room_map.items():
            activity_suggestions.append(f"  - {category.value}: use {rooms[0]}")
        suggestions_info = "\n".join(activity_suggestions)

        # Build quoted list of room names for explicit constraint
        room_names_quoted = ", ".join([f'"{room.name}"' for room in self.rooms])

        # Build example JSON using actual scene room names
        example_tasks = []
        sleep_room = self._find_room_for_category(TaskCategory.SLEEP)
        hygiene_room = self._find_room_for_category(TaskCategory.HYGIENE)
        eating_room = self._find_room_for_category(TaskCategory.EATING)
        example_tasks.append(f'  {{"time": "07:00", "task": "Wake Up", "category": "sleep", "room": "{sleep_room}", "duration_minutes": 5, "description": "Wake up and get out of bed"}}')
        example_tasks.append(f'  {{"time": "07:15", "task": "Morning Shower", "category": "hygiene", "room": "{hygiene_room}", "duration_minutes": 20, "description": "Take morning shower"}}')
        example_tasks.append(f'  {{"time": "07:45", "task": "Breakfast", "category": "eating", "room": "{eating_room}", "duration_minutes": 30, "description": "Eat breakfast"}}')
        example_json = "Example (generate 8-12 similar tasks):\n[\n" + ",\n".join(example_tasks) + "\n]"

        return f"""Generate a realistic daily schedule for a person with the following characteristics:

{self.persona.to_prompt()}

Today is {day_name}, a {day_type}.{time_context}

=== HOUSE LAYOUT ===
This house has the following rooms (ONLY use these exact room names):
{rooms_info}

Suggested room for each activity:
{suggestions_info}

IMPORTANT CONSTRAINTS:
- ONLY use room names from the list above (exact spelling)
- If no office/study room exists, use living room for work
- If no specific room exists for an activity, use the closest alternative
- Generate AT LEAST 8-12 tasks to fill the day
- Tasks should span from wake-up to bedtime

=== OUTPUT FORMAT ===
Generate a JSON array (not an object, not markdown) with 8-12 tasks.
Each task MUST have these exact fields:
- "time": "HH:MM" format (24-hour, e.g., "07:00" or "14:30")
- "task": task name (e.g., "Wake Up", "Eat Breakfast", "Work Session")
- "category": MUST be one of: sleep, hygiene, eating, work, exercise, leisure, social, household, idle
- "room": MUST be EXACTLY one of the room names listed above — copy the room name character-for-character
- "duration_minutes": integer (minimum 5, maximum 180)
- "description": brief description of the activity

CRITICAL: The "room" field MUST be one of these EXACT strings (copy verbatim): {room_names_quoted}
Do NOT use generic room names like "living room", "kitchen", "bedroom" unless they appear EXACTLY in the list above.

{time_instruction}

{example_json}

OUTPUT ONLY THE JSON ARRAY - NO MARKDOWN, NO CODE BLOCKS, NO EXPLANATIONS.
"""

    def _parse_llm_response(
        self,
        response: str,
        date: datetime,
    ) -> Optional[DailySchedule]:
        """Parse LLM response into schedule."""
        try:
            # Extract JSON from response - handle various markdown formats
            response = response.strip()

            # Remove markdown code blocks
            if response.startswith("```"):
                # Find first { or [ after opening ```
                start_idx = response.find("[")
                if start_idx == -1:
                    start_idx = response.find("{")

                # Find last } or ]
                end_idx = max(response.rfind("]"), response.rfind("}"))

                if start_idx != -1 and end_idx != -1:
                    response = response[start_idx:end_idx+1]

            # Remove any leading/trailing text before/after JSON
            if not response.startswith("["):
                start_idx = response.find("[")
                if start_idx != -1:
                    response = response[start_idx:]

            if not response.endswith("]"):
                end_idx = response.rfind("]")
                if end_idx != -1:
                    response = response[:end_idx+1]

            tasks_data = json.loads(response)
            schedule = DailySchedule(date=date)

            category_map = {
                "sleep": TaskCategory.SLEEP,
                "hygiene": TaskCategory.HYGIENE,
                "eating": TaskCategory.EATING,
                "work": TaskCategory.WORK,
                "exercise": TaskCategory.EXERCISE,
                "leisure": TaskCategory.LEISURE,
                "social": TaskCategory.SOCIAL,
                "household": TaskCategory.HOUSEHOLD,
                "idle": TaskCategory.IDLE,
            }

            for i, task_data in enumerate(tasks_data):
                try:
                    time_str = task_data.get("time", "00:00")
                    # Validate time format
                    parts = time_str.split(":")
                    hour = int(parts[0]) % 24  # Wrap hours
                    minute = int(parts[1]) % 60 if len(parts) > 1 else 0  # Wrap minutes
                    scheduled_time = date.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    category = category_map.get(
                        task_data.get("category", "idle").lower(),
                        TaskCategory.IDLE
                    )

                    # Validate room name against available rooms
                    raw_room = task_data.get("room", "")
                    room_name = self._resolve_room_name(raw_room)

                    task = Task(
                        task_id=f"llm_task_{i}",
                        name=task_data.get("task", "Unknown"),
                        category=category,
                        priority=TaskPriority.MEDIUM,
                        duration=timedelta(minutes=task_data.get("duration_minutes", 15)),
                        location=TaskLocation(room_name=room_name),
                        scheduled_time=scheduled_time,
                        description=task_data.get("description", ""),
                    )
                    schedule.add_task(task)
                except Exception as task_err:
                    logger.warning(f"Skipping invalid task {i}: {task_err}")
                    continue

            return schedule

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to parse LLM response: {e}")
            return None

    def _resolve_room_name(self, raw_room: str) -> str:
        """Resolve an LLM-generated room name to an actual scene room name.

        The LLM may output generic names like 'living room', 'kitchen', 'bedroom'
        but the scene has names like 'rec/game', 'bedroom.004', 'laundryroom/mudroom'.
        This method finds the best match.
        """
        if not raw_room:
            # Fallback to first available room
            return self.rooms[0].name if self.rooms else "unknown"

        raw_lower = raw_room.lower().strip()

        # 1. Exact match
        for room in self.rooms:
            if room.name.lower() == raw_lower:
                return room.name

        # 2. Substring match (scene room contains LLM name or vice versa)
        for room in self.rooms:
            rname = room.name.lower()
            if raw_lower in rname or rname in raw_lower:
                logger.info(f"[ROOM] Matched LLM room '{raw_room}' → '{room.name}' (substring)")
                return room.name

        # 3. Semantic/synonym matching for common mismatches
        # Maps generic LLM room names to keywords that might appear in scene room names
        synonym_map = {
            "living room": ["rec", "game", "lounge", "family", "den", "living"],
            "kitchen": ["kitchen", "pantry", "dining"],
            "bedroom": ["bedroom", "nursery"],
            "bathroom": ["bathroom", "toilet", "restroom", "washroom"],
            "office": ["office", "study", "den", "rec"],
            "laundry room": ["laundry", "mudroom", "utility"],
            "dining room": ["dining", "kitchen"],
            "garage": ["garage"],
            "closet": ["closet"],
            "hallway": ["hallway", "corridor", "foyer", "entryway"],
        }

        synonyms = synonym_map.get(raw_lower, [])
        if not synonyms:
            # Try partial key match (e.g. "master bedroom" → "bedroom" synonyms)
            for key, syns in synonym_map.items():
                if key in raw_lower or raw_lower in key:
                    synonyms = syns
                    break

        for synonym in synonyms:
            for room in self.rooms:
                rname = room.name.lower()
                # Check if synonym appears in the scene room name parts
                parts = rname.replace('/', ' ').replace('_', ' ').replace('.', ' ').split()
                if synonym in parts or synonym in rname:
                    logger.info(f"[ROOM] Matched LLM room '{raw_room}' → '{room.name}' (synonym '{synonym}')")
                    return room.name

        # 4. Activity-based fallback: find a room with matching activity
        category = self._guess_category_for_room(raw_lower)
        if category:
            for room in self.rooms:
                if category in room.activities:
                    logger.info(f"[ROOM] Matched LLM room '{raw_room}' → '{room.name}' (activity {category.value})")
                    return room.name

        # 5. Last resort: use first room
        fallback = self.rooms[0].name if self.rooms else raw_room
        logger.warning(f"[ROOM] No match for LLM room '{raw_room}', using fallback '{fallback}'")
        return fallback

    def _guess_category_for_room(self, room_name: str) -> Optional[TaskCategory]:
        """Guess what activity category a room name implies."""
        category_keywords = {
            TaskCategory.SLEEP: ["bedroom", "nursery"],
            TaskCategory.HYGIENE: ["bathroom", "toilet", "restroom", "washroom"],
            TaskCategory.EATING: ["kitchen", "dining", "pantry"],
            TaskCategory.WORK: ["office", "study"],
            TaskCategory.LEISURE: ["living", "rec", "game", "lounge", "family", "den"],
            TaskCategory.HOUSEHOLD: ["laundry", "garage", "utility", "mudroom"],
            TaskCategory.EXERCISE: ["gym", "exercise"],
        }
        for cat, keywords in category_keywords.items():
            for kw in keywords:
                if kw in room_name:
                    return cat
        return None

    def generate_next_task(
        self,
        current_time: datetime,
        current_location: str,
        recent_tasks: List[Task],
    ) -> Task:
        """
        Generate a single next task based on context.

        Useful for dynamic schedule adjustment.
        """
        hour = current_time.hour

        # Basic logic for next task based on time
        if 6 <= hour < 8:
            # Morning routine
            if not any(t.category == TaskCategory.HYGIENE for t in recent_tasks[-3:]):
                return TaskFactory.use_bathroom()
            elif not any(t.category == TaskCategory.EATING for t in recent_tasks[-5:]):
                return TaskFactory.eat_meal("breakfast")
        elif 12 <= hour < 14:
            # Lunch time
            if not any(t.name == "Eat Lunch" for t in recent_tasks[-3:]):
                return TaskFactory.eat_meal("lunch")
        elif 18 <= hour < 21:
            # Dinner time
            if not any(t.name == "Eat Dinner" for t in recent_tasks[-3:]):
                return TaskFactory.eat_meal("dinner")
        elif hour >= 22 or hour < 6:
            # Night time
            return TaskFactory.sleep()

        # Default: random activity
        activities = [
            TaskFactory.watch_tv(duration_minutes=30),
            TaskFactory.idle(duration_minutes=15),
            TaskFactory.walk_to(self._find_room_for_activity(TaskCategory.LEISURE)),
        ]
        return random.choice(activities)
