"""
VESPER Autonomous Daily Life Simulation Demo
Simple script demonstrating the autonomous simulation system
"""

import sys
import os
import time
import logging
from datetime import datetime, timedelta

# Add vesper to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from vesper.simulation import AutonomousSimulation, HumanoidPersona

# Configure logging
import os
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
    
    # Configuration
    print("⚙️  Configuration:")
    print(f"   Persona: {persona.name}, {persona.age}, {persona.occupation}")
    print(f"   Time scale: 60x (1 real second = 1 simulated minute)")
    print(f"   Database: logs/vesper_tasks.db")
    print(f"   LLM: Enabled (using LLM for realistic task generation)\n")
    
    # Create simulation
    sim = AutonomousSimulation(
        persona=persona,
        time_scale=60.0,  # 60x speed: 1 real second = 1 simulated minute
        use_llm=True,     # Use LLM for realistic task generation
    )
    
    # Start a new day
    start_time = datetime.now().replace(hour=7, minute=30, second=0, microsecond=0)
    sim.time_manager._simulation_time = start_time
    sim.start_new_day(date=start_time)
    
    # Run simulation
    print("🚀 Starting continuous simulation... (Press Ctrl+C to stop)\n")
    print("📅 Simulation will run across multiple days automatically\n")
    
    try:
        update_count = 0
        current_day = 0
        
        while True:  # Run continuously
            day_complete = sim.update()
            time.sleep(1.0)  # 1 second real-time
            update_count += 1
            
            # Progress indicator
            if update_count % 10 == 0:
                current = sim.time_manager.current_time
                print(f"⏰ [{current.strftime('%H:%M:%S')}] Day {current_day + 1} | Update {update_count}")
            
            # When day completes, start next day
            if day_complete:
                current_day += 1
                
                # Export today's dataset
                dataset_path = sim.export_dataset(f"vesper_dataset_day_{current_day}.json")
                
                print(f"\n{'─'*80}")
                print(f"📊 Day {current_day} Complete!")
                print(f"   Tasks completed: {sim.current_task_index}")
                print(f"   Events recorded: {len(sim.dataset_events)}")
                print(f"   Dataset: {dataset_path}")
                print(f"{'─'*80}\n")
                
                # Start next day
                next_day = sim.time_manager.current_time + timedelta(days=1)
                next_day = next_day.replace(hour=7, minute=30, second=0, microsecond=0)
                sim.time_manager._simulation_time = next_day
                
                print(f"🌅 Starting new day: {next_day.strftime('%A, %B %d, %Y')}\n")
                sim.start_new_day(date=next_day)
        
    except KeyboardInterrupt:
        print(f"\n\n⚠️  Simulation stopped by user after {current_day + 1} day(s)")
    
    # Final export
    print(f"\n{'='*80}")
    print("📊 Exporting final dataset...")
    print(f"{'='*80}")
    
    final_dataset_path = sim.export_dataset("vesper_dataset_final.json")
    
    # Statistics
    print(f"\n📈 Final Statistics:")
    print(f"   Total days simulated: {current_day + 1}")
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
    
    print(f"\n✓ Dataset ready for research at: {final_dataset_path}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
