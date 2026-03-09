#!/usr/bin/env python3
"""
VESPER SmartThings Schema Server.

This script starts the SmartThings Schema webhook server that allows
virtual devices to appear in the SmartThings app.

Usage:
    python scripts/smartthings_server.py

The server will:
1. Start a webhook server on port 8443
2. Register demo virtual devices
3. Handle SmartThings Schema interactions

You need to expose this server via ngrok:
    ngrok http 8443

Then update your SmartThings Schema App's Target URL to:
    https://YOUR_NGROK_URL/schema
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vesper.integrations import (
    DeviceRegistry,
    DeviceMetadata,
    DeviceCategory,
    SmartThingsSchemaConnector,
    SchemaConnectorConfig,
    DeviceHandlerType,
    create_switch_device,
    create_dimmer_device,
    create_motion_sensor_device,
    create_contact_sensor_device,
    VirtualDeviceDefinition,
)

# Configure logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,  # Debug level to see all interactions
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/smartthings_server_{datetime.now():%Y%m%d_%H%M%S}.log"),
    ],
)
logger = logging.getLogger(__name__)

# SmartThings credentials from your Schema App registration
SMARTTHINGS_CLIENT_ID = os.getenv(
    "SMARTTHINGS_CLIENT_ID", 
    "vesper-smart-home-2025"
)
SMARTTHINGS_CLIENT_SECRET = os.getenv(
    "SMARTTHINGS_CLIENT_SECRET",
    "VESPER_SmartHome_Secret_2025_SecureKey_AbC123XyZ789"
)


class SmartThingsServer:
    """SmartThings Schema webhook server."""
    
    def __init__(self, port: int = 8443):
        self.port = port
        self.connector: SmartThingsSchemaConnector = None
        self.registry: DeviceRegistry = None
        self._running = False
    
    async def setup(self):
        """Initialize the server."""
        logger.info("=" * 60)
        logger.info("VESPER SmartThings Schema Server")
        logger.info("=" * 60)
        
        # Create registry for state persistence
        logger.info("\n[1/3] Initializing device registry...")
        self.registry = DeviceRegistry(db_path="data/smartthings_devices.db")
        await self.registry.initialize()
        
        # Create Schema connector
        logger.info("\n[2/3] Creating Schema connector...")
        config = SchemaConnectorConfig(
            host="0.0.0.0",
            port=self.port,
            webhook_path="/schema",
            smartthings_client_id=SMARTTHINGS_CLIENT_ID,
            smartthings_client_secret=SMARTTHINGS_CLIENT_SECRET,
        )
        self.connector = SmartThingsSchemaConnector(config)
        
        # Register command handler
        self.connector.on_command(self._handle_command)
        
        # Create demo devices
        logger.info("\n[3/3] Registering virtual devices...")
        await self._create_demo_devices()
        
        logger.info("\nSetup complete!")
    
    async def _create_demo_devices(self):
        """Create demo virtual devices."""
        demo_devices = [
            # Switches
            {
                "device_id": "vesper-kitchen-light",
                "name": "Kitchen Light",
                "handler": DeviceHandlerType.SWITCH,
                "room": "Kitchen",
                "state": {"st.switch.switch": "off", "st.healthCheck.healthStatus": "online"},
            },
            {
                "device_id": "vesper-living-room-light",
                "name": "Living Room Light", 
                "handler": DeviceHandlerType.SWITCH,
                "room": "Living Room",
                "state": {"st.switch.switch": "off", "st.healthCheck.healthStatus": "online"},
            },
            # Dimmer
            {
                "device_id": "vesper-bedroom-dimmer",
                "name": "Bedroom Dimmer",
                "handler": DeviceHandlerType.DIMMER,
                "room": "Bedroom",
                "state": {
                    "st.switch.switch": "off",
                    "st.switchLevel.level": 100,
                    "st.healthCheck.healthStatus": "online",
                },
            },
            # Motion Sensor
            {
                "device_id": "vesper-hallway-motion",
                "name": "Hallway Motion",
                "handler": DeviceHandlerType.MOTION_SENSOR,
                "room": "Hallway",
                "state": {
                    "st.motionSensor.motion": "inactive",
                    "st.healthCheck.healthStatus": "online",
                },
            },
            # Contact Sensor
            {
                "device_id": "vesper-front-door",
                "name": "Front Door",
                "handler": DeviceHandlerType.CONTACT_SENSOR,
                "room": "Entrance",
                "state": {
                    "st.contactSensor.contact": "closed",
                    "st.healthCheck.healthStatus": "online",
                },
            },
        ]
        
        for device_config in demo_devices:
            device = VirtualDeviceDefinition(
                external_device_id=device_config["device_id"],
                friendly_name=device_config["name"],
                device_handler_type=device_config["handler"],
                manufacturer_name="VESPER",
                model_name="Virtual Device",
                sw_version="1.0.0",
                room_name=device_config.get("room"),
            )
            device.state = device_config.get("state", {})
            
            self.connector.register_device(device)
            logger.info(f"  ✓ {device_config['name']} ({device_config['device_id']})")
    
    async def _handle_command(
        self,
        device_id: str,
        capability: str,
        command: str,
        arguments: list,
    ) -> bool:
        """Handle commands from SmartThings app."""
        logger.info(f"📱 Command received: {device_id} -> {capability}.{command}({arguments})")
        
        # Get device
        device = self.connector.get_device(device_id)
        if not device:
            logger.error(f"Device not found: {device_id}")
            return False
        
        # Process command and update state
        if capability == "st.switch":
            if command == "on":
                device.state["st.switch.switch"] = "on"
                logger.info(f"  💡 {device.friendly_name} turned ON")
            elif command == "off":
                device.state["st.switch.switch"] = "off"
                logger.info(f"  💡 {device.friendly_name} turned OFF")
                
        elif capability == "st.switchLevel":
            if command == "setLevel" and arguments:
                level = arguments[0]
                device.state["st.switchLevel.level"] = level
                logger.info(f"  🔆 {device.friendly_name} level set to {level}%")
        
        return True
    
    async def start(self):
        """Start the server."""
        await self.connector.start()
        self._running = True
        
        self._print_instructions()
        
        # Keep running
        while self._running:
            await asyncio.sleep(1)
    
    def _print_instructions(self):
        """Print setup instructions."""
        print("\n" + "=" * 60)
        print("SERVER RUNNING")
        print("=" * 60)
        print(f"""
Webhook URL: http://localhost:{self.port}/schema
Health Check: http://localhost:{self.port}/health

REGISTERED DEVICES:
""")
        for device in self.connector.list_devices():
            print(f"  • {device.friendly_name} ({device.external_device_id})")
            print(f"    Type: {device.device_handler_type.value}")
            print(f"    Room: {device.room_name or 'N/A'}")
            print()
        
        print("-" * 60)
        print("""
NGROK SETUP:
If not already running, start ngrok in another terminal:
    ngrok http 8443

Your SmartThings Schema App is configured with:
    Target URL:  https://9104a04a38e2.ngrok-free.app/schema
    OAuth URL:   https://9104a04a38e2.ngrok-free.app/oauth/authorize  
    Token URL:   https://9104a04a38e2.ngrok-free.app/oauth/token

IMPORTANT: Your ngrok URL must match the one registered in SmartThings!
If you get a new ngrok URL, update it in the SmartThings Developer Portal.

ENDPOINTS:
    /schema          - SmartThings Schema webhook (POST)
    /oauth/authorize - OAuth authorization page (GET)
    /oauth/token     - OAuth token exchange (POST)
    /health          - Health check (GET)

TO LINK IN SMARTTHINGS APP:
1. Open SmartThings app on your phone
2. Go to: Menu (☰) > Settings > Linked Services
3. Or: Devices (+) > Add device > Partner devices
4. Search for "VESPER" or look in "My Testing Devices"
5. Tap to link and authorize
6. Devices will appear in your device list!

Press Ctrl+C to stop the server.
""")
        print("=" * 60)
    
    async def stop(self):
        """Stop the server."""
        self._running = False
        if self.connector:
            await self.connector.stop()
        if self.registry:
            await self.registry.close()
        logger.info("Server stopped.")


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="VESPER SmartThings Schema Server")
    parser.add_argument("--port", "-p", type=int, default=8443, help="Server port")
    args = parser.parse_args()
    
    server = SmartThingsServer(port=args.port)
    
    # Handle signals
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        asyncio.create_task(server.stop())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        await server.setup()
        await server.start()
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
