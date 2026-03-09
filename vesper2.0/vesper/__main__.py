"""
VESPER CLI - Main entry point for running simulations.

Usage:
    python -m vesper --config configs/default.yaml --duration 60
    python -m vesper --scene data/scene_datasets/habitat-test-scenes/skokloster-castle.glb
    python -m vesper --demo
"""

import argparse
import logging
import sys
from pathlib import Path


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def run_simulation(args: argparse.Namespace) -> int:
    """Run the main simulation."""
    from vesper.simulation import Simulation
    from vesper.agents import SmartAgent, SmartAgentConfig
    from vesper.habitat.simulator import HabitatSimulator, SimulatorConfig

    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("VESPER - Habitat 3.0 + IoT Interactive Simulation Testbed")
    logger.info("=" * 60)

    try:
        # Create simulation
        config_path = args.config if args.config else None
        
        with Simulation(config_path=config_path) as sim:
            # Use real Habitat-Sim if scene provided
            if args.scene:
                logger.info(f"Loading scene: {args.scene}")
                sim_config = SimulatorConfig(
                    scene_path=args.scene,
                    render_mode="headless" if args.headless else "window",
                )
                sim.simulator = HabitatSimulator(sim_config, event_bus=sim.event_bus)
                if not sim.simulator.initialize():
                    logger.error("Failed to initialize Habitat-Sim")
                    return 1

            # Spawn agents (as per plan.md - two humanoid agents)
            worker = sim.agent_controller.spawn(
                SmartAgent,
                SmartAgentConfig(
                    name="Worker",
                    agent_type="routine_worker",
                    use_llm=args.use_llm,
                ),
            )
            
            resident = sim.agent_controller.spawn(
                SmartAgent,
                SmartAgentConfig(
                    name="Resident",
                    agent_type="resident",
                    use_llm=args.use_llm,
                ),
            )

            logger.info(f"Spawned agents: {[a.name for a in sim.agent_controller.agents]}")
            logger.info(f"IoT devices: {sim.environment.device_count}")

            # Set initial tasks
            worker.set_task("Patrol the house and check all entry points")
            resident.set_task("Go about daily activities in the home")

            # Run simulation
            logger.info(f"Running simulation for {args.duration}s...")
            sim.run(duration=args.duration)

            # Print stats
            logger.info("-" * 40)
            logger.info("Simulation Complete!")
            logger.info(f"  Total ticks: {sim.stats.ticks}")
            logger.info(f"  Elapsed time: {sim.stats.elapsed_time:.2f}s")
            logger.info(f"  Avg tick time: {sim.stats.avg_tick_time*1000:.3f}ms")
            logger.info(f"  Event bus stats: {sim.event_bus.stats}")

        return 0

    except KeyboardInterrupt:
        logger.info("Simulation interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Simulation error: {e}", exc_info=True)
        return 1


def run_demo(args: argparse.Namespace) -> int:
    """Run an interactive demo showcasing VESPER features."""
    from vesper.simulation import Simulation
    from vesper.agents import SmartAgent, SmartAgentConfig
    from vesper.devices import MotionSensor, ContactSensor, SmartDoor
    from vesper.core.event_bus import Event

    setup_logging("INFO")
    logger = logging.getLogger(__name__)

    print("\n" + "=" * 60)
    print("  VESPER Demo - IoT Simulation with LLM Agents")
    print("=" * 60 + "\n")

    with Simulation() as sim:
        # Subscribe to events for demo output
        def on_event(event: Event):
            print(f"  📡 Event: {event.event_type} from {event.source_id[:8]}...")
        
        sim.event_bus.subscribe("device.*", on_event)

        # Show devices
        print("📦 IoT Devices:")
        for device_id, device in sim.environment._devices.items():
            print(f"   - {device}")
        print()

        # Create agents
        print("🤖 Spawning agents...")
        agent1 = sim.agent_controller.spawn(
            SmartAgent,
            SmartAgentConfig(name="SecurityBot", use_llm=False, security_mode=True)
        )
        agent2 = sim.agent_controller.spawn(
            SmartAgent,
            SmartAgentConfig(name="HomeAssistant", use_llm=False)
        )
        print(f"   - {agent1.name}: Security monitoring")
        print(f"   - {agent2.name}: Home automation")
        print()

        # Simulate motion detection
        print("🚶 Simulating motion event...")
        motion_sensor = list(sim.environment._devices.values())[0]
        if hasattr(motion_sensor, 'detect_agent'):
            motion_sensor.detect_agent("test_agent", (1.0, 0.0, 1.0))
        
        sim.environment.tick(0.1)
        print()

        # Run a few ticks
        print("⏱️ Running simulation (2 seconds)...")
        sim.run(duration=2.0)
        print()

        # Show final stats
        print("📊 Stats:")
        print(f"   Ticks: {sim.stats.ticks}")
        print(f"   Events processed: {sim.event_bus.stats['events_processed']}")
        print()

    print("=" * 60)
    print("  Demo complete!")
    print("=" * 60 + "\n")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="vesper",
        description="VESPER - Habitat 3.0 + IoT Interactive Simulation Testbed",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m vesper --demo                   Run interactive demo
    python -m vesper --duration 60            Run for 60 seconds
    python -m vesper --scene path/to/scene.glb  Use specific 3D scene
    python -m vesper --config custom.yaml     Use custom configuration
        """,
    )

    parser.add_argument(
        "--config", "-c",
        type=str,
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--scene", "-s",
        type=str,
        help="Path to 3D scene file (.glb)",
    )
    parser.add_argument(
        "--duration", "-d",
        type=float,
        default=10.0,
        help="Simulation duration in seconds (default: 10)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without rendering",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Enable LLM for agent reasoning",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run interactive demo",
    )

    args = parser.parse_args()

    if args.demo:
        return run_demo(args)
    else:
        return run_simulation(args)


if __name__ == "__main__":
    sys.exit(main())
