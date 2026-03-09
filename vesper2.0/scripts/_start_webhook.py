#!/usr/bin/env python3
"""Standalone SmartThings webhook server for account (re-)linking."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load .env
from pathlib import Path
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from vesper.integrations.schema_connector import (
    SmartThingsSchemaConnector,
    SchemaConnectorConfig,
)

async def main():
    config = SchemaConnectorConfig(
        host="0.0.0.0",
        port=8443,
        webhook_path="/schema",
        smartthings_client_id=os.environ.get(
            "SMARTTHINGS_CLIENT_ID", "vesper-smartthings-integration"
        ),
        smartthings_client_secret=os.environ.get("ST_APP_CLIENT_SECRET", ""),
    )
    connector = SmartThingsSchemaConnector(config)
    await connector.start()
    print("\n✅ Schema Connector listening on :8443/schema")
    print("   Ready for SmartThings linking on your phone.")
    print("   Press Ctrl+C when done.\n")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await connector.stop()
        print("\nStopped.")

if __name__ == "__main__":
    asyncio.run(main())
