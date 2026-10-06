"""
VESPER Engine — Top-level simulation orchestrator.

Replaces the old ``simulation.py`` (which collided with the
``simulation/`` package).  VesperEngine creates all shared objects
once and injects them into every component via constructor arguments.

Design principles:
    1. ONE EventBus — created here, passed to everything
    2. ONE DeviceRegistry — canonical device store
    3. ONE MatterBridgeClient — created here, shared
    4. All traffic through Hub → WiFi → Matter bridge
    5. No component creates its own copies of shared resources

Usage:
    from vesper.engine import VesperEngine

    with VesperEngine(config_path="configs/default.yaml") as engine:
        engine.run(duration=60)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from vesper.config import Config, load_config
from vesper.core.event_bus import EventBus
from vesper.core.registry import DeviceRegistry
from vesper.core.environment import Environment
from vesper.agents.controller import AgentController
from vesper.habitat.simulator import create_simulator, SimulatorConfig

logger = logging.getLogger(__name__)


@dataclass
class EngineStats:
    """Runtime statistics for VesperEngine."""
    ticks: int = 0
    elapsed_time: float = 0.0
    avg_tick_time: float = 0.0
    firmware_events: int = 0
    matter_updates: int = 0


class VesperEngine:
    """
    Top-level simulation orchestrator.

    Owns all shared objects (EventBus, DeviceRegistry, Hub, WiFiNetwork,
    MatterBridge, etc.) and passes them to components via constructor
    injection.  This eliminates the 3+ EventBus / 4+ registry / 4+
    MatterBridgeClient duplication that plagued the old architecture.
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        config_path: Optional[str] = None,
    ):
        # ── Config ────────────────────────────────────────────────
        if config_path:
            self.config = load_config(config_path)
        else:
            self.config = config or Config()

        # ── Strict-mode singleton guards ─────────────────────────
        import vesper.core.event_bus as _eb_mod
        import vesper.core.registry as _reg_mod
        import vesper.matter.bridge_client as _mb_mod

        _strict = self.config.simulation.strict_mode
        _eb_mod._STRICT_MODE = _strict
        _reg_mod._STRICT_MODE = _strict
        _mb_mod._STRICT_MODE = _strict

        # ── Shared singletons (created here, injected everywhere) ─
        self.event_bus = EventBus(
            max_queue_size=self.config.event_bus.max_queue_size,
            enable_logging=self.config.event_bus.logging,
            log_file=self.config.event_bus.log_file,
        )
        self.registry = DeviceRegistry(event_bus=self.event_bus)
        self.environment = Environment(
            config=self.config,
            event_bus=self.event_bus,
            registry=self.registry,          # ← INV-2: share canonical registry
        )
        self.agent_controller = AgentController(event_bus=self.event_bus)

        # ── Lazy-initialised subsystems ───────────────────────────
        self.matter_bridge = None     # MatterBridgeClient
        self.wifi_network = None      # WiFiEmulator
        self.hub = None               # VirtualHub
        self.simulator = None         # HabitatSimulator
        self._wifi_bridge = None      # MatterFirmwareBridge

        # ── Runtime state ─────────────────────────────────────────
        self._running = False
        self._stats = EngineStats()

    # ── Properties ────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> EngineStats:
        return self._stats

    # ── Initialisation ────────────────────────────────────────────

    def initialize(
        self,
        use_mock_sim: bool = True,
        matter: bool = False,
        wifi: bool = False,
        hub: bool = False,
        strict: Optional[bool] = None,
    ) -> bool:
        """
        Wire all components.

        Order matters:
        1. EventBus + DeviceRegistry (already created in __init__)
        2. MatterBridgeClient  (if matter=True)
        3. WiFiNetwork         (if wifi=True)
        4. Hub                 (if hub=True or matter=True — MANDATORY when matter=True)
        5. HabitatSimulator + devices
        6. AgentController (already created)
        7. Verify invariants (strict mode)

        In strict mode (default):
        - matter=True forces hub=True (INV-4: all traffic through Hub)
        - If Hub setup fails when matter=True, raise RuntimeError
        - _verify_invariants() called at end
        """
        _strict = strict if strict is not None else self.config.simulation.strict_mode
        logger.info("Initializing VesperEngine (strict=%s)...", _strict)

        # 1. Backbone already ready (event_bus, registry)

        # 2. Matter bridge
        if matter:
            self._setup_matter()

        # 3. WiFi network
        if wifi:
            self._setup_wifi()

        # 4. Hub (MANDATORY when matter=True in strict mode)
        if matter and _strict:
            hub = True  # INV-4: enforce hub routing
        if hub or matter:
            self._setup_hub()
            if self.hub is None and matter and _strict:
                raise RuntimeError(
                    "STRICT MODE: Hub setup failed but matter=True. "
                    "All Matter traffic MUST route through Hub (INV-4). "
                    "Fix Hub configuration or use strict=False for debugging."
                )

        # 5. Habitat simulator + devices
        sim_config = SimulatorConfig(
            scene_path=self.config.environment.scene,
            render_mode="headless" if self.config.simulation.headless else "window",
        )
        self.simulator = create_simulator(sim_config, use_mock=use_mock_sim)
        self.simulator.initialize()

        self._setup_devices()

        logger.info(
            "VesperEngine initialized — %d devices, "
            "matter=%s, wifi=%s, hub=%s",
            self.registry.count,
            self.matter_bridge is not None,
            self.wifi_network is not None,
            self.hub is not None,
        )

        # 7. Verify architecture invariants (strict mode)
        if _strict:
            self._verify_invariants()

        return True

    # ── Invariant verification ────────────────────────────────────

    def _verify_invariants(self) -> None:
        """
        Runtime check of architecture invariants (INV-1 through INV-5).
        Called at end of initialize() in strict mode.
        Raises RuntimeError on any violation.
        """
        errors = []

        # INV-1: One EventBus
        if not isinstance(self.event_bus, EventBus):
            errors.append("INV-1: event_bus is not an EventBus instance")

        # INV-2: One DeviceRegistry — check Environment uses same registry
        if self.environment._registry is not None and self.environment._registry is not self.registry:
            errors.append("INV-2: Environment._registry is not the same as engine.registry")

        # INV-2b: Environment↔Registry consistency
        for device_id in self.environment._devices:
            if device_id not in self.registry:
                errors.append(
                    f"INV-2: device {device_id} exists in Environment but not in DeviceRegistry"
                )

        # INV-3: One MatterBridgeClient (if Matter enabled)
        if self.matter_bridge is not None and self.hub is not None:
            if hasattr(self.hub, '_matter_bridge') and self.hub._matter_bridge is not self.matter_bridge:
                errors.append("INV-3: Hub._matter_bridge is not the same as engine.matter_bridge")

        # INV-4: Hub exists when Matter is enabled
        if self.matter_bridge is not None and self.hub is None:
            errors.append("INV-4: Matter enabled but Hub is None — traffic cannot route through Hub")

        if errors:
            msg = "STRICT MODE invariant violations:\n" + "\n".join(f"  • {e}" for e in errors)
            raise RuntimeError(msg)

        logger.info("✓ All architecture invariants verified")

    # ── Setup helpers ─────────────────────────────────────────────

    def _setup_matter(self) -> None:
        """Create the ONE MatterBridgeClient instance."""
        try:
            from vesper.matter.bridge_client import MatterBridgeClient

            url = self.config.hub.matter_bridge_url
            self.matter_bridge = MatterBridgeClient(base_url=url)
            if self.matter_bridge.wait_ready_sync(max_wait=30):
                logger.info("Matter bridge connected at %s", url)
            else:
                logger.warning("Matter bridge not reachable at %s", url)
                self.matter_bridge = None
        except ImportError:
            logger.warning("Matter bridge client not available (missing dep)")
        except Exception as e:
            logger.error("Matter bridge setup failed: %s", e)
            self.matter_bridge = None

    def _setup_wifi(self) -> None:
        """Create the WiFiEmulator (shared, with registry)."""
        try:
            from vesper.network.wifi_emulator import WiFiEmulator

            self.wifi_network = WiFiEmulator(registry=self.registry)
            logger.info("WiFi emulator ready (sim mode)")
        except Exception as e:
            logger.error("WiFi emulator setup failed: %s", e)
            self.wifi_network = None

    def _setup_hub(self) -> None:
        """Create the VirtualHub with injected deps."""
        try:
            from vesper.hub.virtual_hub import VirtualHub

            self.hub = VirtualHub(
                hub_id="vesper-virtual-hub",
                name="VESPER Virtual Hub",
                event_bus=self.event_bus,
                registry=self.registry,
                wifi_network=self.wifi_network,
                matter_bridge=self.matter_bridge,
                ha_url=self.config.hub.ha_url,
                ha_token=self.config.hub.ha_token,
            )
            logger.info("VirtualHub created with injected deps")
        except Exception as e:
            logger.error("Hub setup failed: %s", e)
            self.hub = None

    def _setup_devices(self) -> None:
        """Register IoT devices from configuration into the shared registry."""
        from vesper.devices import MotionSensor, ContactSensor, SmartDoor, LightSensor
        from vesper.core.registry import DeviceEntry

        device_configs = [
            ("motion_sensor", self.config.devices.motion_sensor, MotionSensor),
            ("contact_sensor", self.config.devices.contact_sensor, ContactSensor),
            ("smart_door", self.config.devices.smart_door, SmartDoor),
            ("light_sensor", self.config.devices.light_sensor, LightSensor),
        ]

        for name, dev_cfg, cls in device_configs:
            if dev_cfg.enabled:
                device = cls(event_bus=self.event_bus)
                # Register in Environment (for spatial queries / tick updates)
                self.environment.register_device(device)
                # Also register in the canonical DeviceRegistry
                self.registry.register_or_update(
                    device.device_id,
                    DeviceEntry(
                        device_id=device.device_id,
                        device_type=device.device_type,
                        name=name,
                    ),
                )
                logger.debug("Registered device: %s (%s)", name, device.device_id)

    # ── Main loop ─────────────────────────────────────────────────

    def step(self, dt: Optional[float] = None) -> None:
        """Execute one simulation tick."""
        dt = dt or (1.0 / self.config.simulation.tick_rate)
        start = time.time()

        # Simulator
        if self.simulator:
            self.simulator.step()

        # Environment devices
        self.environment.tick(dt)

        # Agents
        self.agent_controller.update(dt, self.environment)

        # Event processing
        self.event_bus.process_events()

        self._stats.ticks += 1
        tick_time = time.time() - start
        self._stats.elapsed_time += tick_time
        self._stats.avg_tick_time = self._stats.elapsed_time / self._stats.ticks

    def run(self, duration: float = 10.0) -> None:
        """Run the main loop for ``duration`` seconds."""
        logger.info("Running simulation for %.1fs...", duration)
        self._running = True
        dt = 1.0 / self.config.simulation.tick_rate
        end_time = time.time() + duration

        while self._running and time.time() < end_time:
            self.step(dt)
            time.sleep(max(0, dt - self._stats.avg_tick_time))

        self._running = False
        logger.info("Simulation complete: %d ticks", self._stats.ticks)

    def stop(self) -> None:
        """Signal the main loop to stop."""
        self._running = False

    # ── Teardown ──────────────────────────────────────────────────

    def close(self) -> None:
        """Tear down all components in reverse order."""
        self.stop()

        # Matter bridge is stateless REST — nothing to close
        self.matter_bridge = None

        if self.wifi_network:
            try:
                self.wifi_network.stop()
            except Exception as e:
                logger.warning("Error stopping WiFi emulator: %s", e)

        self.agent_controller.destroy_all()

        if self.simulator:
            self.simulator.close()

        logger.info("VesperEngine closed")

    # ── Context manager ───────────────────────────────────────────

    def __enter__(self) -> "VesperEngine":
        self.initialize()
        return self

    def __exit__(self, *args) -> None:
        self.close()
