"""
Quick test of continuous autonomous simulation (template-based, no LLM delays)
"""

import sys
import os
import time
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from vesper.simulation import AutonomousSimulation, HumanoidPersona

logging.basicConfig(level=logging.INFO, format='%(message)s')


def main():
    print("\n" + "="*80)
    print("VESPER Continuous Simulation Test (Fast Mode - No LLM)")
    print("="*80 + "\n")
    
    persona = HumanoidPersona(name="TestUser", age=30)
    
    # Use template-based generation (fast)
    sim = AutonomousSimulation(
        persona=persona,
        time_scale=600.0,  # 600x speed: 1 real second = 10 simulated minutes!
        use_llm=False,     # Templates are instant
    )
    
    # Start at wake time
    start_time = datetime.now().replace(hour=7, minute=30, second=0)
    sim.time_manager._simulation_time = start_time
    sim.start_new_day(date=start_time)
    
    print("\n🚀 Running continuous simulation (very fast!)...")
    print("📅 Will automatically generate new days when each completes\n")
    
    try:
        update_count = 0
        current_day = 0
        
        while current_day < 3:  # Run for 3 days then stop
            day_complete = sim.update()
            time.sleep(0.1)  # 0.1 second = 60 simulated seconds
            update_count += 1
            
            if update_count % 50 == 0:
                current = sim.time_manager.current_time
                print(f"⏰ Day {current_day + 1} | {current.strftime('%H:%M')} | Updates: {update_count}")
            
            if day_complete:
                current_day += 1
                
                # Save dataset for this day
                dataset_file = f"vesper_day{current_day}.json"
                sim.export_dataset(dataset_file)
                
                print(f"\n{'─'*80}")
                print(f"✓ Day {current_day} Complete!")
                print(f"  Completed {sim.current_task_index} tasks")
                print(f"  Recorded {len(sim.dataset_events)} events")
                print(f"  Dataset: {dataset_file}")
                print(f"{'─'*80}\n")
                
                if current_day < 3:
                    # Start next day
                    next_day = sim.time_manager.current_time + timedelta(days=1)
                    next_day = next_day.replace(hour=7, minute=30)
                    sim.time_manager._simulation_time = next_day
                    
                    print(f"🌅 Day {current_day + 1}: {next_day.strftime('%A, %B %d')}\n")
                    sim.start_new_day(date=next_day)
        
        print(f"\n{'='*80}")
        print(f"✓ 3-Day Simulation Complete!")
        print(f"{'='*80}\n")
        
    except KeyboardInterrupt:
        print(f"\n⚠️  Stopped after {current_day + 1} days")


if __name__ == "__main__":
    main()
