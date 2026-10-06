#!/usr/bin/env python3
"""
Configure Home Assistant to connect to the Matter server and verify integration.
"""

import asyncio
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("setup_ha_matter")


async def main():
    """Connect HA to Matter server and verify."""

    log.info("=" * 60)
    log.info("Setting up Home Assistant Matter Integration")
    log.info("=" * 60)

    # 1. Verify Matter server is reachable
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:5580/") as resp:
                if resp.status == 200:
                    log.info("✅ Matter server is running on port 5580")
                else:
                    log.error(f"❌ Matter server returned {resp.status}")
                    return
    except Exception as e:
        log.error(f"❌ Cannot reach Matter server: {e}")
        return

    # 2. Check HA API
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:8123/api/") as resp:
                log.info(f"HA API status: {resp.status}")
                if resp.status == 401:
                    log.info("✅ HA is running (requires auth)")
                elif resp.status == 200:
                    log.info("✅ HA is running")
    except Exception as e:
        log.error(f"❌ Cannot reach HA: {e}")
        return

    log.info("")
    log.info("Matter Server Status:")
    log.info("  URL: ws://localhost:5580/ws")
    log.info("  WebUI: http://localhost:5580")
    log.info("")
    log.info("Home Assistant Status:")
    log.info("  URL: http://localhost:8123")
    log.info("  User: You need to complete onboarding in browser first")
    log.info("")
    log.info("Next Steps:")
    log.info("  1. Open http://localhost:8123 and complete HA setup")
    log.info("  2. Go to Settings → Devices & Services → Add Integration")
    log.info("  3. Search for 'Matter' and configure:")
    log.info("     - Server URL: ws://vesper-matter-server:5580/ws")
    log.info("  4. The VESPER dashboard will then show Matter devices")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
