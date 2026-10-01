"""
VESPER Platform Orchestrator — Ties together Hub, Matter, Dashboard.

This module provides a single entry-point to start the full VESPER platform:
1. EventBus for inter-module communication
2. Hub layer (virtual/physical) for device traffic routing
3. MatterAdapter for direct Matter device integration via python-matter-server
4. Dashboard server for real-time Web UI

Usage:
    from vesper.platform import VesperPlatform

    platform = VesperPlatform.from_config("configs/default.yaml")
    await platform.start()
    # ... platform is running ...
    await platform.stop()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from vesper.config import Config, load_config

logger = logging.getLogger(__name__)


class VesperPlatform:
    """
    Top-level orchestrator for the VESPER platform.

    Manages the lifecycle of:
    - EventBus  (pub/sub backbone)
    - HubManager (device traffic routing)
    - MatterAdapter (direct Matter device integration via python-matter-server)
    - DashboardServer (Web UI)
    """

    def __init__(self, config: Config, engine=None):
        self.config = config
        self._engine = engine
        self._event_bus = engine.event_bus if engine else None
        self._hub_manager = None
        self._matter_client = None
        self._matter_adapter = None
        self._dashboard = None
        self._running = False

    @classmethod
    def from_config(cls, config_path: Optional[str] = None) -> "VesperPlatform":
        """Create a platform instance from a YAML config file."""
        cfg = load_config(config_path)
        return cls(cfg)

    async def start(self) -> None:
        """Start all platform subsystems."""
        logger.info("═" * 60)
        logger.info("  VESPER Platform v2.0 — Starting")
        logger.info("═" * 60)

        # ── 1. EventBus ────────────────────────────────────────────
        if self._event_bus is None:
            from vesper.core.event_bus import EventBus

            self._event_bus = EventBus(
                max_queue_size=self.config.event_bus.max_queue_size,
            )
            logger.info("✓ EventBus created")
        else:
            logger.info("✓ EventBus shared from VesperEngine")
        # No start() call — EventBus is pull-only via process_events()

        # ── 2. Hub Layer ───────────────────────────────────────────
        if self.config.hub.enabled:
            from vesper.hub.manager import HubManager, HubConfig

            hub_cfg = HubConfig(
                hub_type=self.config.hub.hub_type,
                matter_bridge_url=self.config.hub.matter_bridge_url,
                ha_url=self.config.hub.ha_url,
                ha_token=self.config.hub.ha_token,
                smartthings_token=self.config.hub.smartthings_token,
                smartthings_location_id=self.config.hub.smartthings_location_id,
                poll_interval=self.config.hub.poll_interval,
            )
            self._hub_manager = HubManager(default_config=hub_cfg)
            self._hub_manager.set_event_bus(self._event_bus)
            await self._hub_manager.start()
            logger.info(
                f"✓ Hub layer started ({self.config.hub.hub_type})"
            )

        # ── 3. Matter Integration (python-matter-server) ──────────
        if self.config.matter.enabled:
            from vesper.matter.client import VesperMatterClient, matter_sdk_available
            from vesper.matter.adapter import MatterAdapter

            if not matter_sdk_available():
                logger.warning(
                    "⚠ Matter SDK not installed. Install with: "
                    "pip install 'python-matter-server[client]' "
                    "home-assistant-chip-clusters"
                )
            else:
                self._matter_client = VesperMatterClient(
                    url=self.config.matter.matter_server_url,
                )
                connected = await self._matter_client.connect()
                if connected:
                    self._matter_adapter = MatterAdapter(
                        client=self._matter_client,
                        event_bus=self._event_bus,
                        hub=self._hub_manager.active_hub if self._hub_manager else None,
                    )
                    await self._matter_adapter.setup()
                    n_devices = len(self._matter_adapter.devices)
                    logger.info(
                        f"✓ Matter adapter connected — {n_devices} device(s)"
                    )
                else:
                    logger.warning(
                        "⚠ Matter: could not connect to "
                        f"{self.config.matter.matter_server_url}"
                    )

        # ── 4. Dashboard ──────────────────────────────────────────
        if self.config.dashboard.enabled:
            from vesper.dashboard.app import DashboardServer

            self._dashboard = DashboardServer(
                host=self.config.dashboard.host,
                port=self.config.dashboard.port,
            )
            await self._dashboard.start(
                hub_manager=self._hub_manager,
                matter_adapter=self._matter_adapter,
                event_bus=self._event_bus,
            )
            logger.info(
                f"✓ Dashboard at http://{self.config.dashboard.host}"
                f":{self.config.dashboard.port}"
            )

        self._running = True
        logger.info("═" * 60)
        logger.info("  VESPER Platform — All systems running")
        logger.info("═" * 60)

    async def stop(self) -> None:
        """Gracefully shut down all subsystems."""
        logger.info("VESPER Platform shutting down...")

        if self._dashboard:
            await self._dashboard.stop()

        if self._matter_adapter:
            await self._matter_adapter.shutdown()

        if self._matter_client:
            await self._matter_client.disconnect()

        if self._hub_manager:
            await self._hub_manager.stop()

        # EventBus is pull-only — no stop() needed.
        # Only release our reference if we created it (not shared from engine).
        if self._event_bus and not self._engine:
            self._event_bus = None

        self._running = False
        logger.info("VESPER Platform stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def event_bus(self):
        return self._event_bus

    @property
    def hub_manager(self):
        return self._hub_manager

    @property
    def matter_adapter(self):
        return self._matter_adapter


async def main():
    """CLI entry point for running the platform."""
    import argparse

    parser = argparse.ArgumentParser(description="VESPER IoT Security Testbed")
    parser.add_argument(
        "-c", "--config", default=None, help="Path to config YAML"
    )
    parser.add_argument(
        "--no-dashboard", action="store_true",
        help="Disable the Web UI dashboard"
    )
    parser.add_argument(
        "--no-matter", action="store_true",
        help="Disable Matter integration"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    config = load_config(args.config)
    if args.no_dashboard:
        config.dashboard.enabled = False
    if args.no_matter:
        config.matter.enabled = False

    platform = VesperPlatform(config)
    await platform.start()

    try:
        # Keep running until interrupted
        while platform.is_running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await platform.stop()


if __name__ == "__main__":
    asyncio.run(main())
