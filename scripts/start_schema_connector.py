#!/usr/bin/env python3
"""
Standalone SmartThings Schema Connector server.

Keeps the Schema webhook alive on port 8443 so ngrok can forward
SmartThings requests even when run_autonomous_eval.py is not running.

Usage:
    conda activate vesper
    python scripts/start_schema_connector.py
    # Then in SmartThings app: re-link or refresh the VESPER service
"""
import asyncio
import os
import sys
import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Load .env so credentials are available via os.getenv ──
_env_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

from vesper.integrations import (
    SmartThingsSchemaConnector,
    SchemaConnectorConfig,
    VirtualDeviceDefinition,
    DeviceHandlerType,
)

PORT = 8443
CLIENT_ID = os.getenv(
    "SMARTTHINGS_CLIENT_ID",
    "67c773de-021e-418e-afa1-ecac652ce062",
)
APP_CLIENT_SECRET = os.getenv(
    "ST_APP_CLIENT_SECRET",
    "c30878806ab6a319a243501daa7b2536d92ca6e52959b835f8e8258819aa7dd04368c9f81bbbb3697054994e67b49e613d1474cca405ec7ba539d351364d85ca7a03d89f4e59da98d4edad413b15d5413c91a04382e084629c03b66d3b28a739d555f8ea0debf5f902cc028bb97c42812a2013595905e899155aa373491df5be0bce33c6855473b0f7f59eb3700c16703eec1055c91b5158030801d247db94b66c448eaa1dfb3100b6d5ed18671e7d9241e1406b67266af3e3b69d2dbb47874787d01b5b3eaa4388eb9fc38f0505ce50d3964a3e520e893a28a005c0f6c4f4b60f196b178f5d63ae30deb4443dfae888b40f3a06c54a2dc739f687103dff62a5",
)

ROOMS = [
    "Living Room", "Kitchen", "Bedroom", "Bathroom",
    "Hallway", "Entryway",
]


async def main():
    config = SchemaConnectorConfig(
        host="0.0.0.0",
        port=PORT,
        webhook_path="/schema",
        smartthings_client_id=CLIENT_ID,
        smartthings_client_secret=APP_CLIENT_SECRET,
    )
    connector = SmartThingsSchemaConnector(config)

    # Register placeholder devices
    for room in ROOMS:
        dev = VirtualDeviceDefinition(
            external_device_id=f"vesper-3d-{room.lower().replace(' ', '-')}",
            friendly_name=f"{room} Smart Light",
            device_handler_type=DeviceHandlerType.DIMMER,
            manufacturer_name="VESPER",
            model_name="VESPER Smart Light",
            sw_version="2.0.0",
            room_name=room,
        )
        dev.state = {
            "st.switch.switch": "off",
            "st.switchLevel.level": 100,
            "st.healthCheck.healthStatus": "online",
        }
        connector.register_device(dev)

    def _on_command(device_id, command):
        print(f"  📱 Cloud command: {device_id} → {command}")

    connector.on_command(_on_command)
    await connector.start()

    print(f"\n{'='*60}")
    print(f"  ✅ SmartThings Schema Connector running on port {PORT}")
    print(f"  Devices: {len(ROOMS)}")
    print(f"  Endpoint: http://localhost:{PORT}/schema")
    print(f"{'='*60}")
    print(f"  Now go to SmartThings app → Linked Services → VESPER")
    print(f"  and tap 'Reconnect' or 'Refresh'.")
    print(f"  Press Ctrl+C to stop.\n")

    # Keep running forever
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
