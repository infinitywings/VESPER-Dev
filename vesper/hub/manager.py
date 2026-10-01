"""
Hub Manager — Lifecycle management for VESPER hubs.

Provides a unified interface to create, configure, start, and stop
hubs (virtual or physical). Supports hot-switching between hub types
and running multiple hubs simultaneously.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from vesper.hub.base import BaseHub, HubState
from vesper.hub.virtual_hub import VirtualHub
from vesper.hub.physical_hub import PhysicalHub

logger = logging.getLogger(__name__)


class HubConfig(BaseModel):
    """Configuration for hub setup."""
    hub_type: str = Field(
        default="virtual",
        description="Hub type: 'virtual' or 'physical'",
    )
    hub_id: str = Field(
        default="vesper-hub-01",
        description="Unique hub identifier",
    )
    name: str = Field(
        default="VESPER Hub",
        description="Human-readable hub name",
    )

    # Virtual Hub settings
    matter_bridge_url: str = Field(default="http://localhost:8484", description="Matter bridge REST API URL")
    ha_url: str = Field(
        default="http://localhost:8123",
        description="Home Assistant URL",
    )
    ha_token: Optional[str] = Field(
        default=None, description="Home Assistant Long-Lived Access Token"
    )

    # Physical Hub settings
    smartthings_token: Optional[str] = Field(
        default=None, description="SmartThings Personal Access Token"
    )
    smartthings_location_id: Optional[str] = Field(
        default=None, description="SmartThings Location ID"
    )
    poll_interval: float = Field(
        default=10.0, description="Physical hub poll interval in seconds"
    )


class HubManager:
    """
    Manages the lifecycle of one or more VESPER hubs.

    The HubManager creates and manages hubs based on configuration,
    providing a single entry point for the rest of the VESPER system
    to interact with device management.

    Usage:
        manager = HubManager(config)
        await manager.start()
        hub = manager.primary_hub
        await hub.send_command("light-01", "on")
        await manager.stop()
    """

    def __init__(self, config: Optional[HubConfig] = None, event_bus=None, registry=None, wifi_network=None, matter_bridge=None):
        self._config = config or HubConfig()
        self._hubs: dict[str, BaseHub] = {}
        self._primary_hub_id: Optional[str] = None
        self._event_bus = event_bus
        self._registry = registry
        self._wifi_network = wifi_network
        self._matter_bridge = matter_bridge

    async def start(self) -> BaseHub:
        """Create and start the primary hub based on configuration."""
        hub = self._create_hub(self._config)
        self._hubs[hub.hub_id] = hub
        self._primary_hub_id = hub.hub_id

        # Connect EventBus if available
        if self._event_bus and hasattr(hub, "connect_event_bus"):
            hub.connect_event_bus(self._event_bus)

        await hub.start()
        logger.info(
            f"HubManager: Primary hub '{hub.hub_id}' ({type(hub).__name__}) started"
        )
        return hub

    async def stop(self) -> None:
        """Stop all managed hubs."""
        for hub_id, hub in self._hubs.items():
            try:
                await hub.stop()
                logger.info(f"HubManager: Hub '{hub_id}' stopped")
            except Exception as e:
                logger.error(f"HubManager: Error stopping '{hub_id}': {e}")
        self._hubs.clear()

    async def add_hub(self, config: HubConfig) -> BaseHub:
        """Add and start an additional hub."""
        hub = self._create_hub(config)
        self._hubs[hub.hub_id] = hub

        if self._event_bus and hasattr(hub, "connect_event_bus"):
            hub.connect_event_bus(self._event_bus)

        await hub.start()
        return hub

    async def remove_hub(self, hub_id: str) -> None:
        """Stop and remove a hub."""
        hub = self._hubs.pop(hub_id, None)
        if hub:
            await hub.stop()
            if self._primary_hub_id == hub_id:
                self._primary_hub_id = next(iter(self._hubs), None)

    @property
    def primary_hub(self) -> Optional[BaseHub]:
        """Get the primary hub."""
        if self._primary_hub_id:
            return self._hubs.get(self._primary_hub_id)
        return None

    def get_hub(self, hub_id: str) -> Optional[BaseHub]:
        """Get a specific hub by ID."""
        return self._hubs.get(hub_id)

    def get_all_hubs(self) -> dict[str, BaseHub]:
        """Get all managed hubs."""
        return dict(self._hubs)

    def set_event_bus(self, event_bus) -> None:
        """Set the EventBus for all hubs."""
        self._event_bus = event_bus
        for hub in self._hubs.values():
            if hasattr(hub, "connect_event_bus"):
                hub.connect_event_bus(event_bus)

    async def get_all_devices(self) -> dict[str, Any]:
        """Get all devices across all hubs."""
        all_devices = {}
        for hub_id, hub in self._hubs.items():
            for device_id, device in hub.get_all_devices().items():
                all_devices[device_id] = {
                    "hub_id": hub_id,
                    "device": device,
                }
        return all_devices

    async def health_check_all(self) -> dict[str, Any]:
        """Health check all hubs."""
        results = {}
        for hub_id, hub in self._hubs.items():
            results[hub_id] = await hub.health_check()
        return results

    def _create_hub(self, config: HubConfig) -> BaseHub:
        """Create a hub based on configuration."""
        if config.hub_type == "virtual":
            return VirtualHub(
                hub_id=config.hub_id,
                name=config.name,
                event_bus=self._event_bus,
                registry=self._registry,
                wifi_network=self._wifi_network,
                matter_bridge=self._matter_bridge,
                matter_bridge_url=config.matter_bridge_url,
                ha_url=config.ha_url,
                ha_token=config.ha_token,
            )
        elif config.hub_type == "physical":
            return PhysicalHub(
                hub_id=config.hub_id,
                name=config.name,
                api_token=config.smartthings_token,
                location_id=config.smartthings_location_id,
                poll_interval=config.poll_interval,
            )
        else:
            raise ValueError(f"Unknown hub type: {config.hub_type}")
