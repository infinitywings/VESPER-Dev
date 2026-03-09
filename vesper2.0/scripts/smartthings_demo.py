#!/usr/bin/env python3
"""
SmartThings Integration Demo for VESPER.

This script demonstrates the bi-directional synchronization between
VESPER virtual devices and SmartThings. It creates virtual devices
that appear in the SmartThings app and can be controlled from there.

Prerequisites:
1. SmartThings Developer Account: https://developer.smartthings.com/
2. Register a Schema App in SmartThings Developer Workspace
3. Set environment variables:
   - SMARTTHINGS_CLIENT_ID: Your SmartThings client ID
   - SMARTTHINGS_CLIENT_SECRET: Your SmartThings client secret
   - VESPER_OAUTH_CLIENT_ID: Your OAuth client ID (for user auth)
   - VESPER_OAUTH_CLIENT_SECRET: Your OAuth client secret

Usage:
    python scripts/smartthings_demo.py [--port PORT] [--no-docker]

The script will:
1. Start a Schema Connector webhook server
2. Create virtual devices in the registry
3. Register devices with SmartThings (requires linking in app)
4. Demonstrate bi-directional state synchronization
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vesper.integrations import (
    # Registry
    DeviceRegistry,
    DeviceMetadata,
    DeviceCategory,
    
    # Schema Connector
    SmartThingsSchemaConnector,
    SchemaConnectorConfig,
    VirtualDeviceDefinition,
    DeviceHandlerType,
    create_switch_device,
    create_dimmer_device,
    create_motion_sensor_device,
    
    # Docker (optional)
    DockerDeviceManager,
    DeviceManagerConfig,
    DeviceType,
    
    # Sync Bridge
    SmartThingsSyncBridge,
    SyncBridgeConfig,
    create_smartthings_bridge,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/smartthings_demo_{datetime.now():%Y%m%d_%H%M%S}.log"),
    ],
)
logger = logging.getLogger(__name__)


class SmartThingsDemo:
    """
    Interactive demo for SmartThings integration.
    """
    
    def __init__(
        self,
        port: int = 8443,
        enable_docker: bool = False,
    ):
        self.port = port
        self.enable_docker = enable_docker
        
        self.bridge: SmartThingsSyncBridge = None
        self._running = False
    
    async def setup(self):
        """Initialize all components."""
        logger.info("=" * 60)
        logger.info("VESPER SmartThings Integration Demo")
        logger.info("=" * 60)
        
        # Check environment
        self._check_environment()
        
        # Create sync bridge (this creates all components)
        logger.info("\n[1/4] Creating sync bridge...")
        self.bridge = await create_smartthings_bridge(
            db_path="data/smartthings_demo.db",
            enable_docker=self.enable_docker,
            schema_host="0.0.0.0",
            schema_port=self.port,
        )
        
        # Start bridge
        logger.info("\n[2/4] Starting sync bridge...")
        await self.bridge.start()
        
        # Create demo devices
        logger.info("\n[3/4] Creating demo devices...")
        await self._create_demo_devices()
        
        logger.info("\n[4/4] Setup complete!")
        self._print_status()
    
    def _check_environment(self):
        """Check required environment variables."""
        logger.info("\nChecking environment configuration...")
        
        required = [
            ("SMARTTHINGS_CLIENT_ID", "SmartThings client ID"),
            ("SMARTTHINGS_CLIENT_SECRET", "SmartThings client secret"),
        ]
        
        optional = [
            ("VESPER_OAUTH_CLIENT_ID", "OAuth client ID"),
            ("VESPER_OAUTH_CLIENT_SECRET", "OAuth client secret"),
        ]
        
        missing = []
        for var, desc in required:
            value = os.getenv(var)
            if value:
                logger.info(f"  ✓ {var}: {'*' * min(len(value), 8)}...")
            else:
                logger.warning(f"  ✗ {var}: Not set ({desc})")
                missing.append(var)
        
        for var, desc in optional:
            value = os.getenv(var)
            if value:
                logger.info(f"  ✓ {var}: {'*' * min(len(value), 8)}...")
            else:
                logger.info(f"  ○ {var}: Not set (optional)")
        
        if missing:
            logger.warning(
                "\nSome environment variables are missing. "
                "The demo will run but SmartThings callbacks won't work."
            )
            logger.info("To set them, run:")
            for var in missing:
                logger.info(f"  export {var}='your_value'")
    
    async def _create_demo_devices(self):
        """Create demo virtual devices."""
        demo_devices = [
            {
                "device_id": "vesper-kitchen-light",
                "device_type": DeviceType.SWITCH,
                "friendly_name": "Kitchen Light",
                "room": "Kitchen",
                "initial_state": {"switch": False, "on": False},
            },
            {
                "device_id": "vesper-living-room-dimmer",
                "device_type": DeviceType.DIMMER,
                "friendly_name": "Living Room Dimmer",
                "room": "Living Room",
                "initial_state": {"switch": False, "on": False, "level": 100},
            },
            {
                "device_id": "vesper-hallway-motion",
                "device_type": DeviceType.MOTION_SENSOR,
                "friendly_name": "Hallway Motion Sensor",
                "room": "Hallway",
                "initial_state": {"motion": False},
            },
            {
                "device_id": "vesper-front-door",
                "device_type": DeviceType.CONTACT_SENSOR,
                "friendly_name": "Front Door Sensor",
                "room": "Entrance",
                "initial_state": {"contact": False},  # False = closed
            },
            {
                "device_id": "vesper-bedroom-light",
                "device_type": DeviceType.RGB_LIGHT,
                "friendly_name": "Bedroom Light",
                "room": "Bedroom",
                "initial_state": {
                    "switch": False,
                    "on": False,
                    "level": 100,
                    "hue": 0,
                    "saturation": 100,
                },
            },
        ]
        
        for device_config in demo_devices:
            existing = self.bridge.registry.get_device(device_config["device_id"])
            if existing:
                logger.info(f"  Device exists: {device_config['friendly_name']}")
                continue
            
            device = await self.bridge.create_device(
                device_id=device_config["device_id"],
                device_type=device_config["device_type"],
                friendly_name=device_config["friendly_name"],
                room=device_config.get("room"),
                initial_state=device_config.get("initial_state"),
            )
            
            if device:
                logger.info(f"  ✓ Created: {device_config['friendly_name']}")
            else:
                logger.error(f"  ✗ Failed: {device_config['friendly_name']}")
    
    def _print_status(self):
        """Print current status and instructions."""
        logger.info("\n" + "=" * 60)
        logger.info("DEMO STATUS")
        logger.info("=" * 60)
        
        # Print webhook URL
        webhook_url = f"http://localhost:{self.port}/smartthings"
        logger.info(f"\nSchema Connector Webhook: {webhook_url}")
        logger.info(f"Health Check: http://localhost:{self.port}/health")
        
        # Print registered devices
        devices = self.bridge.registry.list_devices()
        logger.info(f"\nRegistered Devices ({len(devices)}):")
        for device in devices:
            state = self.bridge.registry._state_cache.get(device.device_id)
            state_str = str(state.state) if state else "No state"
            logger.info(f"  - {device.friendly_name} ({device.device_id})")
            logger.info(f"    Room: {device.room or 'N/A'}, State: {state_str}")
        
        # Print instructions
        logger.info("\n" + "-" * 60)
        logger.info("NEXT STEPS")
        logger.info("-" * 60)
        logger.info("""
1. Register Schema App in SmartThings Developer Workspace:
   - Go to: https://developer.smartthings.com/
   - Create new project > Cloud Connector
   - Set webhook URL to your public endpoint
   - (Use ngrok for local testing: ngrok http {port})

2. Link service in SmartThings App:
   - Open SmartThings app on your phone
   - Go to: Devices > Add (+) > Add device
   - Scroll to bottom > My Testing Devices
   - Select your Schema App
   - Follow OAuth flow

3. Control devices:
   - Devices will appear in SmartThings app
   - Changes sync bi-directionally

DEMO COMMANDS (press Enter for menu):
  toggle <device_id>  - Toggle a switch device
  level <device_id> <0-100> - Set dimmer level
  motion <device_id>  - Trigger motion sensor
  status             - Show current device states
  stats              - Show sync statistics
  quit               - Exit demo
""".format(port=self.port))
    
    async def run_interactive(self):
        """Run interactive command loop."""
        self._running = True
        
        logger.info("\nDemo running. Type 'help' for commands.")
        
        while self._running:
            try:
                # Non-blocking input with asyncio
                line = await asyncio.get_event_loop().run_in_executor(
                    None, input, "> "
                )
                await self._handle_command(line.strip())
            except EOFError:
                break
            except KeyboardInterrupt:
                break
    
    async def _handle_command(self, line: str):
        """Handle a command."""
        if not line:
            return
        
        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]
        
        if cmd in ("quit", "exit", "q"):
            self._running = False
            
        elif cmd == "help":
            self._print_help()
            
        elif cmd == "status":
            await self._show_status()
            
        elif cmd == "stats":
            self._show_stats()
            
        elif cmd == "toggle" and args:
            await self._toggle_device(args[0])
            
        elif cmd == "level" and len(args) >= 2:
            await self._set_level(args[0], int(args[1]))
            
        elif cmd == "motion" and args:
            await self._trigger_motion(args[0])
            
        elif cmd == "door" and args:
            await self._toggle_door(args[0])
            
        else:
            logger.info(f"Unknown command: {cmd}. Type 'help' for commands.")
    
    def _print_help(self):
        """Print help message."""
        print("""
Commands:
  toggle <device_id>       - Toggle a switch device on/off
  level <device_id> <0-100> - Set dimmer level
  motion <device_id>       - Trigger motion sensor (auto-clears after 30s)
  door <device_id>         - Toggle door sensor open/closed
  status                   - Show current device states
  stats                    - Show sync statistics
  help                     - Show this help
  quit                     - Exit demo
        """)
    
    async def _show_status(self):
        """Show current device states."""
        devices = self.bridge.registry.list_devices()
        print(f"\nDevice States ({len(devices)} devices):")
        print("-" * 50)
        
        for device in devices:
            state = await self.bridge.get_device_state(device.device_id)
            state_str = ", ".join(f"{k}={v}" for k, v in (state or {}).items())
            online = "🟢" if device.is_online else "🔴"
            print(f"{online} {device.friendly_name}")
            print(f"   ID: {device.device_id}")
            print(f"   State: {state_str or 'No state'}")
    
    def _show_stats(self):
        """Show sync statistics."""
        stats = self.bridge.get_statistics()
        print("\nSync Statistics:")
        print("-" * 40)
        for key, value in stats.items():
            print(f"  {key}: {value}")
    
    async def _toggle_device(self, device_id: str):
        """Toggle a switch device."""
        state = await self.bridge.get_device_state(device_id)
        if state is None:
            print(f"Device not found: {device_id}")
            return
        
        current = state.get("switch", state.get("on", False))
        new_state = not current
        
        await self.bridge.update_device_state(device_id, {
            "switch": new_state,
            "on": new_state,
        })
        
        print(f"Toggled {device_id}: {'ON' if new_state else 'OFF'}")
    
    async def _set_level(self, device_id: str, level: int):
        """Set dimmer level."""
        level = max(0, min(100, level))
        
        await self.bridge.update_device_state(device_id, {
            "level": level,
            "brightness": level,
        })
        
        print(f"Set {device_id} level to {level}%")
    
    async def _trigger_motion(self, device_id: str):
        """Trigger motion sensor."""
        # Set motion active
        await self.bridge.update_device_state(device_id, {
            "motion": True,
        })
        print(f"Motion detected on {device_id}")
        
        # Schedule clearing motion after 30 seconds
        async def clear_motion():
            await asyncio.sleep(30)
            await self.bridge.update_device_state(device_id, {
                "motion": False,
            })
            print(f"Motion cleared on {device_id}")
        
        asyncio.create_task(clear_motion())
    
    async def _toggle_door(self, device_id: str):
        """Toggle door sensor."""
        state = await self.bridge.get_device_state(device_id)
        if state is None:
            print(f"Device not found: {device_id}")
            return
        
        current = state.get("contact", False)
        new_state = not current
        
        await self.bridge.update_device_state(device_id, {
            "contact": new_state,
        })
        
        print(f"Door {device_id}: {'OPEN' if new_state else 'CLOSED'}")
    
    async def cleanup(self):
        """Clean up resources."""
        logger.info("\nShutting down...")
        
        if self.bridge:
            await self.bridge.stop()
        
        logger.info("Demo stopped.")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="VESPER SmartThings Integration Demo"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8443,
        help="Port for Schema Connector webhook (default: 8443)",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Enable Docker container support for devices",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Run without interactive command loop",
    )
    args = parser.parse_args()
    
    # Ensure logs directory exists
    os.makedirs("logs", exist_ok=True)
    
    # Create demo
    demo = SmartThingsDemo(
        port=args.port,
        enable_docker=args.docker,
    )
    
    # Handle signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(demo.cleanup())
        )
    
    try:
        await demo.setup()
        
        if not args.no_interactive:
            await demo.run_interactive()
        else:
            # Run until interrupted
            while True:
                await asyncio.sleep(1)
                
    except Exception as e:
        logger.error(f"Demo error: {e}")
        raise
    finally:
        await demo.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
