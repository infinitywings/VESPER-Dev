"""
VESPER Autonomous Daily Life Simulation
Real-time task execution with database logging for research datasets

This script demonstrates:
1. Generate daily schedule from LLM
2. Execute tasks in real-time with 3D simulation
3. Record all activities to database
4. Create research dataset of smart home interactions
"""

import sys
import os
from datetime import datetime, timedelta
import time
import json
import logging

# Add vesper to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from vesper.simulation import (
    TimeManager,
    TimeConfig,
    TaskGenerator,
    HumanoidPersona,
    TaskDatabase,
    TaskExecutor,
    TaskStatus,
)
from vesper.agents.llm_client import LLMClient, LLMConfig

# Configure logging
log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'vesper_autonomous_sim.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class AutonomousSimulation:
    """
    Autonomous daily life simulation with real-time execution.
    
    Generates daily schedules and executes them in real-time,
    recording all activities to database for research dataset creation.
    """
    
    def __init__(
        self,
        persona: HumanoidPersona,
        time_scale: float = 1.0,
        use_llm: bool = False,
        db_path: str = None,
    ):
        """
        Initialize autonomous simulation.
        
        Args:
            persona: Humanoid persona for schedule generation
            time_scale: Time acceleration (1.0 = real-time, 60.0 = 1 min = 1 sec)
            use_llm: Whether to use LLM for task generation
            db_path: Database path for task history
        """
        self.persona = persona
        self.time_scale = time_scale
        self.use_llm = use_llm
        
        # Initialize components
        self.time_manager = TimeManager(TimeConfig(
            sync_to_real_time=True,
            time_scale=time_scale,
        ))
        
        self.task_database = TaskDatabase(db_path)
        
        # LLM client (optional)
        self.llm_client = None
        if use_llm:
            config = LLMConfig()
            if config.validate():
                self.llm_client = LLMClient(config)
                logger.info("LLM client initialized")
            else:
                logger.warning("LLM not configured, using template generation")
        
        self.task_generator = TaskGenerator(
            persona=persona,
            llm_client=self.llm_client
        )
        
        # Current state
        self.current_schedule = None
        self.current_task = None
        self.current_task_index = 0
        
        # Dataset recording
        self.dataset_events = []
        
        logger.info(f"Autonomous simulation initialized for {persona.name}")
        logger.info(f"Time scale: {time_scale}x (1 real second = {time_scale} sim seconds)")
    
    def start_new_day(self, date: datetime = None):
        """
        Generate and start a new day's schedule.
        
        Args:
            date: Date for the schedule (defaults to today)
        """
        if date is None:
            date = self.time_manager.current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        
        logger.info(f"{'='*80}")
        logger.info(f"Starting new day: {date.strftime('%A, %B %d, %Y')}")
        logger.info(f"{'='*80}")
        
        # Generate daily schedule
        logger.info("Generating daily schedule...")
        start_gen = time.time()
        
        self.current_schedule = self.task_generator.generate_daily_schedule(
            date=date,
            use_llm=self.use_llm
        )
        
        gen_time = time.time() - start_gen
        logger.info(f"Generated {len(self.current_schedule.tasks)} tasks in {gen_time:.1f}s")
        
        # Save schedule to database
        self.task_database.save_schedule(self.current_schedule)
        logger.info(f"Saved schedule to database")
        
        # Display schedule
        self._display_schedule()
        
        # Reset execution state
        self.current_task_index = 0
        self.current_task = None
    
    def _display_schedule(self):
        """Display the current schedule."""
        logger.info(f"\n{'─'*80}")
        logger.info("📅 DAILY SCHEDULE")
        logger.info(f"{'─'*80}")
        
        for i, task in enumerate(self.current_schedule.tasks, 1):
            time_str = task.scheduled_time.strftime("%H:%M") if task.scheduled_time else "N/A"
            duration_min = int(task.duration.total_seconds() / 60)
            
            logger.info(f"{i:2d}. [{time_str}] {task.name:<30} ({duration_min:3d} min) @ {task.location.room_name}")
            if task.description:
                logger.info(f"      ↳ {task.description[:70]}")
        
        logger.info(f"{'─'*80}\n")
    
    def update(self):
        """
        Update simulation (call this in main loop).
        
        Returns:
            bool: True if day is complete, False otherwise
        """
        # Update time
        self.time_manager.update()
        current_time = self.time_manager.current_time
        
        # Check if we need to start a new task
        if self.current_task is None or self.current_task.status == TaskStatus.COMPLETED:
            next_task = self._get_next_task(current_time)
            
            if next_task is None:
                # No more tasks for today
                return True
            
            # Start next task
            self._start_task(next_task, current_time)
        
        # Update current task
        if self.current_task:
            self._update_current_task(current_time)
        
        return False
    
    def _get_next_task(self, current_time: datetime):
        """Get the next task to execute."""
        if self.current_task_index >= len(self.current_schedule.tasks):
            return None
        
        next_task = self.current_schedule.tasks[self.current_task_index]
        
        # Check if it's time to start this task
        if next_task.scheduled_time and current_time >= next_task.scheduled_time:
            self.current_task_index += 1
            return next_task
        
        return None
    
    def _start_task(self, task, current_time):
        """Start executing a task."""
        self.current_task = task
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = current_time
        
        # Log task start
        logger.info(f"\n▶ [{current_time.strftime('%H:%M:%S')}] STARTING: {task.name}")
        logger.info(f"   Category: {task.category.value}")
        logger.info(f"   Location: {task.location.room_name}")
        logger.info(f"   Duration: {int(task.duration.total_seconds() / 60)} minutes")
        if task.description:
            logger.info(f"   Details: {task.description}")
        
        # Record event to dataset
        self._record_event({
            "timestamp": current_time.isoformat(),
            "event_type": "task_start",
            "task_id": task.task_id,
            "task_name": task.name,
            "category": task.category.value,
            "room": task.location.room_name,
            "scheduled_time": task.scheduled_time.isoformat() if task.scheduled_time else None,
            "actual_start_time": current_time.isoformat(),
            "description": task.description,
        })
    
    def _update_current_task(self, current_time):
        """Update progress of current task."""
        task = self.current_task
        
        if task.started_at is None:
            return
        
        # Calculate progress
        elapsed = (current_time - task.started_at).total_seconds()
        total = task.duration.total_seconds()
        progress = min(elapsed / total, 1.0)
        
        task.progress = progress
        
        # Check if task is complete
        if progress >= 1.0:
            self._complete_task(current_time)
    
    def _complete_task(self, current_time):
        """Complete the current task."""
        task = self.current_task
        task.status = TaskStatus.COMPLETED
        task.completed_at = current_time
        
        actual_duration = (current_time - task.started_at).total_seconds()
        
        # Log completion
        logger.info(f"✓ [{current_time.strftime('%H:%M:%S')}] COMPLETED: {task.name}")
        logger.info(f"   Actual duration: {int(actual_duration / 60)} minutes\n")
        
        # Record completion event
        self._record_event({
            "timestamp": current_time.isoformat(),
            "event_type": "task_complete",
            "task_id": task.task_id,
            "task_name": task.name,
            "category": task.category.value,
            "room": task.location.room_name,
            "started_at": task.started_at.isoformat(),
            "completed_at": current_time.isoformat(),
            "planned_duration_seconds": task.duration.total_seconds(),
            "actual_duration_seconds": actual_duration,
            "progress": task.progress,
        })
        
        # Save to database
        self.task_database.save_task(task)
        
        # Mark as done
        self.current_task = None
    
    def _record_event(self, event_data):
        """Record an event to the dataset."""
        self.dataset_events.append(event_data)
    
    def export_dataset(self, output_path: str = None):
        """
        Export recorded dataset to JSON.
        
        Args:
            output_path: Path to save dataset (default: logs/vesper_dataset_YYYYMMDD.json)
        """
        if output_path is None:
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
            os.makedirs(log_dir, exist_ok=True)
            output_path = os.path.join(log_dir, f"vesper_dataset_{date_str}.json")
        
        dataset = {
            "metadata": {
                "persona": {
                    "name": self.persona.name,
                    "age": self.persona.age,
                    "occupation": self.persona.occupation,
                    "works_from_home": self.persona.works_from_home,
                },
                "time_scale": self.time_scale,
                "generation_method": "llm" if self.use_llm else "template",
                "total_events": len(self.dataset_events),
                "export_time": datetime.now().isoformat(),
            },
            "events": self.dataset_events,
        }
        
        with open(output_path, 'w') as f:
            json.dump(dataset, f, indent=2)
        
        logger.info(f"\n📊 Dataset exported to: {output_path}")
        logger.info(f"   Total events: {len(self.dataset_events)}")
        
        return output_path
    
    def get_history_summary(self, days: int = 7):
        """
        Get summary of recent task history for LLM context.
        
        Args:
            days: Number of days to look back
            
        Returns:
            str: Summary text for LLM prompt
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Query database for recent tasks
        schedules = self.task_database.get_schedules_in_range(
            start_date=start_date,
            end_date=end_date
        )
        
        if not schedules:
            return "No previous task history available."
        
        summary = f"Recent {days}-day task history:\n\n"
        
        for schedule_data in schedules:
            date_str = schedule_data['date']
            task_count = schedule_data['task_count']
            completed = schedule_data['completed_count']
            
            summary += f"📅 {date_str}: {completed}/{task_count} tasks completed\n"
        
        # Get most common activities
        stats = self.task_database.get_activity_statistics(days=days)
        
        summary += f"\nMost common activities:\n"
        for cat, count in list(stats.items())[:5]:
            summary += f"  • {cat}: {count} times\n"
        
        return summary


def main():
    """Run autonomous simulation demo."""
    print("\n" + "╔" + "═"*78 + "╗")
    print("║" + " "*16 + "VESPER Autonomous Daily Life Simulation" + " "*23 + "║")
    print("╚" + "═"*78 + "╝\n")
    
    # Create persona
    persona = HumanoidPersona(
        name="Sarah",
        age=28,
        occupation="UX Designer",
        works_from_home=True,
        wake_time="07:30",
        sleep_time="23:00",
        work_start="09:00",
        work_end="17:00",
        exercise_frequency=0.6,
    )
    
    # Create simulation with time acceleration
    # time_scale=60.0 means 1 real second = 1 simulated minute
    print("⚙️  Configuration:")
    print(f"   Persona: {persona.name}, {persona.age}, {persona.occupation}")
    print(f"   Time scale: 60x (1 real second = 1 simulated minute)")
    print(f"   Database: logs/vesper_tasks.db")
    print(f"   LLM: Enabled (using LLM for realistic task generation)\n")
    
    sim = AutonomousSimulation(
        persona=persona,
        time_scale=60.0,  # Fast simulation: 1 real minute = 1 simulated hour
        use_llm=True,     # Use LLM for realistic, detailed task generation
    )
    
    # Start a new day
    # Set simulation to start at wake-up time
    start_time = datetime.now().replace(hour=7, minute=30, second=0, microsecond=0)
    
    # Override time manager's current time
    sim.time_manager._simulation_time = start_time
    
    sim.start_new_day(date=start_time)
    
    # Simulate until day is complete or user interrupts
    print("🚀 Starting simulation... (Press Ctrl+C to stop)\n")
    
    try:
        day_complete = False
        update_count = 0
        
        while not day_complete:
            day_complete = sim.update()
            
            # Sleep to simulate real-time
            time.sleep(1.0)  # 1 second real-time
            
            update_count += 1
            
            # Progress indicator every 10 updates
            if update_count % 10 == 0:
                current = sim.time_manager.current_time
                print(f"⏰ [{current.strftime('%H:%M:%S')}] Simulation running... ({update_count} updates)")
        
        print(f"\n✓ Day complete!")
        
    except KeyboardInterrupt:
        print(f"\n\n⚠️  Simulation stopped by user")
    
    # Export dataset
    print(f"\n{'='*80}")
    print("📊 Exporting research dataset...")
    print(f"{'='*80}")
    
    dataset_path = sim.export_dataset()
    
    # Show statistics
    print(f"\n📈 Session Statistics:")
    print(f"   Total tasks: {len(sim.current_schedule.tasks) if sim.current_schedule else 0}")
    print(f"   Tasks completed: {sim.current_task_index}")
    print(f"   Events recorded: {len(sim.dataset_events)}")
    
    # Category breakdown
    if sim.dataset_events:
        from collections import Counter
        categories = Counter(
            evt['category'] for evt in sim.dataset_events
            if evt.get('event_type') == 'task_start'
        )
        if categories:
            print(f"\n   Activity breakdown:")
            for cat, count in categories.most_common():
                print(f"     • {cat}: {count} tasks")
    
    print(f"\n✓ Dataset ready for research at: {dataset_path}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
