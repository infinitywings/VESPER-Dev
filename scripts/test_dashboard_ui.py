#!/usr/bin/env python3
"""
Standalone VESPER Dashboard UI tester.

Starts the dashboard with mock hubs, devices, and traffic so every UI
tab can be verified without Habitat-Sim, LLM Studio, or Docker.

Usage:
    conda activate vesper
    python scripts/test_dashboard_ui.py          # opens http://localhost:8080
    python scripts/test_dashboard_ui.py --port 9090
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("test_dashboard_ui")


# ── Lightweight stubs so we don't need the full vesper package ────────

class HubState(Enum):
    INITIALIZING = auto()
    READY = auto()
    RUNNING = auto()
    PAUSED = auto()
    ERROR = auto()
    STOPPED = auto()


class HubCapability(Enum):
    MATTER_BRIDGE = "matter_bridge"
    MATTER = "matter"
    ZIGBEE = "zigbee"
    ZWAVE = "zwave"
    WIFI = "wifi"
    BLUETOOTH = "bluetooth"
    SMARTTHINGS = "smartthings"
    HOMEASSISTANT = "homeassistant"


@dataclass
class DeviceRecord:
    device_id: str
    device_type: str
    protocol: str
    name: str = ""
    room: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    manufacturer: str = ""
    model: str = ""
    firmware_version: str = ""
    last_seen: float = 0.0
    online: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrafficRecord:
    timestamp: float
    source_id: str
    target_id: str
    protocol: str
    direction: str
    topic: str = ""
    payload_size: int = 0
    payload_summary: str = ""
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Mock Hub ──────────────────────────────────────────────────────────

class MockHub:
    """Minimal hub that satisfies the dashboard API."""

    def __init__(self, hub_id: str, name: str):
        self.hub_id = hub_id
        self.name = name
        self.state = HubState.RUNNING
        self._devices: dict[str, DeviceRecord] = {}
        self._traffic_log: list[TrafficRecord] = []
        self._capabilities: set[HubCapability] = set()
        self._start_time = time.time()

    @property
    def capabilities(self) -> set[HubCapability]:
        return self._capabilities

    def register_device(self, d: DeviceRecord) -> None:
        d.last_seen = time.time()
        self._devices[d.device_id] = d

    def record_traffic(self, r: TrafficRecord) -> None:
        self._traffic_log.append(r)
        if len(self._traffic_log) > 5000:
            self._traffic_log = self._traffic_log[-5000:]

    async def send_command(self, device_id, command, params=None):
        log.info(f"MockHub cmd  {device_id}  {command}  {params}")
        return {"ok": True}

    def get_stats(self) -> dict:
        now = time.time()
        online = sum(1 for d in self._devices.values() if d.online)
        protos: dict[str, int] = {}
        for d in self._devices.values():
            protos[d.protocol] = protos.get(d.protocol, 0) + 1
        return {
            "hub_id": self.hub_id,
            "name": self.name,
            "state": self.state.name,
            "uptime_seconds": now - self._start_time,
            "total_devices": len(self._devices),
            "online_devices": online,
            "offline_devices": len(self._devices) - online,
            "protocols": protos,
            "capabilities": [c.value for c in self._capabilities],
            "traffic_records": len(self._traffic_log),
        }


class MockHubManager:
    """Quacks like HubManager for the dashboard."""

    def __init__(self) -> None:
        self._hubs: dict[str, MockHub] = {}
        self._primary_hub_id: Optional[str] = None

    @property
    def primary_hub(self) -> Optional[MockHub]:
        if self._primary_hub_id:
            return self._hubs.get(self._primary_hub_id)
        return next(iter(self._hubs.values()), None)

    def add_hub(self, hub: MockHub) -> None:
        self._hubs[hub.hub_id] = hub
        if self._primary_hub_id is None:
            self._primary_hub_id = hub.hub_id


class MockEventBus:
    """Minimal EventBus stub."""

    def __init__(self) -> None:
        self._subs: list[Callable] = []

    def subscribe(self, topic: str, callback: Callable) -> None:
        self._subs.append(callback)

    def publish(self, event: Any) -> None:
        for cb in self._subs:
            try:
                cb(event)
            except Exception:
                pass


@dataclass
class FakeEvent:
    topic: str = ""
    data: Any = None
    priority: Any = None
    timestamp: float = 0.0

    class _P:
        name = "INFO"

    def __post_init__(self):
        if self.priority is None:
            self.priority = self._P()
        if not self.timestamp:
            self.timestamp = time.time()


# ── Realistic device fixtures ─────────────────────────────────────────

DEVICES = [
    DeviceRecord(
        device_id="kitchen-light-01",
        device_type="smart_light",
        protocol="matter",
        name="Kitchen Ceiling Light",
        room="kitchen",
        state={"on": True, "brightness": 80, "color_temp": 4000, "firmware_version": "2.1.4", "online": True},
        capabilities=["on_off", "brightness", "color_temp"],
        ip_address="192.168.1.101",
        mac_address="AA:BB:CC:DD:01:01",
        manufacturer="VESPER",
        model="VL-100",
        firmware_version="2.1.4",
    ),
    DeviceRecord(
        device_id="living-room-light-01",
        device_type="smart_light",
        protocol="matter",
        name="Living Room Lamp",
        room="living_room",
        state={"on": False, "brightness": 0, "firmware_version": "1.0.3", "online": True},
        capabilities=["on_off", "brightness"],
        ip_address="192.168.1.102",
        mac_address="AA:BB:CC:DD:01:02",
        manufacturer="VESPER",
        model="VL-200",
        firmware_version="1.0.3",
    ),
    DeviceRecord(
        device_id="front-door-lock-01",
        device_type="smart_lock",
        protocol="zigbee",
        name="Front Door Lock",
        room="hallway",
        state={"locked": True, "battery": 78, "firmware_version": "3.0.1", "online": True},
        capabilities=["lock_unlock", "battery"],
        ip_address="192.168.1.103",
        mac_address="AA:BB:CC:DD:02:01",
        manufacturer="VESPER",
        model="VK-300",
        firmware_version="3.0.1",
    ),
    DeviceRecord(
        device_id="thermostat-01",
        device_type="thermostat",
        protocol="matter",
        name="Main Thermostat",
        room="living_room",
        state={"mode": "heat", "target_temp": 72, "current_temp": 69.5, "humidity": 42, "firmware_version": "1.8.0", "online": True},
        capabilities=["temperature", "humidity", "hvac_mode"],
        ip_address="192.168.1.104",
        mac_address="AA:BB:CC:DD:03:01",
        manufacturer="VESPER",
        model="VT-400",
        firmware_version="1.8.0",
    ),
    DeviceRecord(
        device_id="motion-sensor-01",
        device_type="motion_sensor",
        protocol="zigbee",
        name="Hallway Motion Sensor",
        room="hallway",
        state={"motion": False, "battery": 92, "lux": 150, "firmware_version": "1.2.0", "online": True},
        capabilities=["motion", "battery", "illuminance"],
        ip_address="192.168.1.105",
        mac_address="AA:BB:CC:DD:04:01",
        manufacturer="VESPER",
        model="VM-500",
        firmware_version="1.2.0",
    ),
    DeviceRecord(
        device_id="camera-01",
        device_type="camera",
        protocol="wifi",
        name="Front Porch Camera",
        room="porch",
        state={"recording": True, "resolution": "1080p", "night_vision": True, "firmware_version": "4.5.2", "online": True},
        capabilities=["video", "motion_detection", "night_vision"],
        ip_address="192.168.1.106",
        mac_address="AA:BB:CC:DD:05:01",
        manufacturer="VESPER",
        model="VC-600",
        firmware_version="4.5.2",
    ),
    DeviceRecord(
        device_id="smart-plug-01",
        device_type="smart_plug",
        protocol="smartthings",
        name="Office Desk Plug",
        room="office",
        state={"on": True, "power_w": 45.2, "energy_kwh": 12.3, "firmware_version": "1.0.0", "online": True},
        capabilities=["on_off", "power_meter", "energy_meter"],
        ip_address="192.168.1.107",
        mac_address="AA:BB:CC:DD:06:01",
        manufacturer="VESPER",
        model="VP-700",
        firmware_version="1.0.0",
    ),
    DeviceRecord(
        device_id="door-sensor-01",
        device_type="contact_sensor",
        protocol="zigbee",
        name="Back Door Sensor",
        room="kitchen",
        state={"open": False, "battery": 85, "firmware_version": "1.1.0", "online": True},
        capabilities=["contact", "battery"],
        ip_address=None,
        mac_address="AA:BB:CC:DD:07:01",
        manufacturer="VESPER",
        model="VD-800",
        firmware_version="1.1.0",
    ),
    DeviceRecord(
        device_id="speaker-01",
        device_type="smart_speaker",
        protocol="wifi",
        name="Kitchen Speaker",
        room="kitchen",
        state={"playing": False, "volume": 30, "firmware_version": "2.3.1", "online": True},
        capabilities=["media_playback", "volume"],
        ip_address="192.168.1.108",
        mac_address="AA:BB:CC:DD:08:01",
        manufacturer="VESPER",
        model="VS-900",
        firmware_version="2.3.1",
    ),
    DeviceRecord(
        device_id="leak-sensor-01",
        device_type="leak_sensor",
        protocol="zwave",
        name="Basement Leak Sensor",
        room="basement",
        state={"leak": False, "battery": 96, "firmware_version": "1.0.5", "online": True},
        capabilities=["water_leak", "battery"],
        ip_address=None,
        mac_address="AA:BB:CC:DD:09:01",
        manufacturer="VESPER",
        model="VW-1000",
        firmware_version="1.0.5",
    ),
]


# ── Traffic / event generators ────────────────────────────────────────

PROTOCOLS = ["matter", "smartthings", "zigbee", "zwave", "wifi", "bluetooth"]
TOPICS = [
    "home/kitchen/light/state", "home/living_room/lamp/state",
    "home/hallway/lock/cmd", "home/living_room/thermostat/state",
    "home/hallway/motion", "home/porch/camera/stream",
    "home/office/plug/power", "home/kitchen/door/state",
    "home/kitchen/speaker/cmd", "home/basement/leak",
]


def _rand_traffic(devices: list[DeviceRecord]) -> TrafficRecord:
    src = random.choice(devices)
    tgt = random.choice(devices)
    return TrafficRecord(
        timestamp=time.time(),
        source_id=src.device_id,
        target_id=tgt.device_id,
        protocol=src.protocol,
        direction=random.choice(["inbound", "outbound", "internal"]),
        topic=random.choice(TOPICS),
        payload_size=random.randint(16, 1024),
        latency_ms=round(random.uniform(0.5, 25.0), 2),
    )


async def _traffic_generator(hub: MockHub, interval: float = 1.5):
    """Periodically inject fake traffic so the Traffic tab is alive."""
    devs = list(hub._devices.values())
    while True:
        for _ in range(random.randint(1, 4)):
            hub.record_traffic(_rand_traffic(devs))
        await asyncio.sleep(interval)


async def _event_generator(event_bus: MockEventBus, interval: float = 3.0):
    """Periodically fire fake events so the Events tab is alive."""
    topics = [
        "motion.detected", "device.state_changed", "attack.started",
        "attack.completed", "automation.triggered", "system.health",
        "traffic.anomaly", "firmware.update_available",
    ]
    payloads = [
        {"device": "motion-sensor-01", "room": "hallway", "value": True},
        {"device": "thermostat-01", "temp": 72, "mode": "heat"},
        {"attack": "matter_spoof", "target": "smart-plug-01"},
        {"automation": "night_mode", "triggered_by": "motion-sensor-01"},
        {"level": "warning", "msg": "High traffic on matter bus"},
    ]
    while True:
        ev = FakeEvent(
            topic=random.choice(topics),
            data=random.choice(payloads),
        )
        event_bus.publish(ev)
        await asyncio.sleep(interval)


async def _device_state_changer(hub: MockHub, interval: float = 5.0):
    """Randomly toggle device states so the UI updates."""
    while True:
        devs = list(hub._devices.values())
        d = random.choice(devs)
        if d.device_type == "smart_light":
            d.state["on"] = not d.state.get("on", False)
            d.state["brightness"] = random.randint(0, 100)
        elif d.device_type == "motion_sensor":
            d.state["motion"] = not d.state.get("motion", False)
            d.state["lux"] = random.randint(0, 500)
        elif d.device_type == "thermostat":
            d.state["current_temp"] = round(random.uniform(65, 78), 1)
        elif d.device_type == "smart_plug":
            d.state["power_w"] = round(random.uniform(0, 120), 1)
        elif d.device_type == "contact_sensor":
            d.state["open"] = not d.state.get("open", False)
        d.last_seen = time.time()
        await asyncio.sleep(interval)


async def _eval_faker(app, interval: float = 8.0):
    """Populate eval metrics so the Evaluation tab isn't empty."""
    day = 0
    while True:
        day = min(day + 1, 3)
        app.state.eval_active = True
        app.state.eval_scene_id = "102344280"
        app.state.eval_day = day
        app.state.eval_total_days = 3
        app.state.eval_nav_trials = random.randint(5, 30) * day
        app.state.eval_nav_success = int(app.state.eval_nav_trials * random.uniform(0.6, 0.95))
        app.state.eval_motion_events = random.randint(20, 100) * day
        app.state.eval_automations = random.randint(5, 30) * day
        app.state.eval_proximity_toggles = random.randint(10, 50) * day
        app.state.eval_articulated = random.randint(2, 15) * day
        app.state.eval_sensor_detections = random.randint(15, 80) * day
        app.state.eval_attacks_run = random.randint(3, 12) * day
        app.state.eval_attacks_exploitable = random.randint(0, 4) * day
        await asyncio.sleep(interval)


# ── Main ──────────────────────────────────────────────────────────────

async def main(port: int = 8080):
    # 1. Build mock hub + devices
    hub = MockHub("vesper-hub", "VESPER Virtual Hub")
    hub._capabilities = {
        HubCapability.MATTER_BRIDGE, HubCapability.MATTER,
        HubCapability.ZIGBEE, HubCapability.ZWAVE,
        HubCapability.WIFI, HubCapability.SMARTTHINGS,
        HubCapability.HOMEASSISTANT,
    }
    for d in DEVICES:
        hub.register_device(d)

    # Seed some initial traffic
    devs = list(hub._devices.values())
    for _ in range(50):
        hub.record_traffic(_rand_traffic(devs))

    hub_mgr = MockHubManager()
    hub_mgr.add_hub(hub)

    event_bus = MockEventBus()

    # 2. Create and start dashboard
    # We need to add vesper to the path for the import to work
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from vesper.dashboard.app import create_app

    app = create_app(
        hub_manager=hub_mgr,
        matter_adapter=None,
        event_bus=event_bus,
    )

    # Seed some attack history so the Attacks tab has data
    app.state.attack_log = [
        {
            "type": "firmware_tamper",
            "target": "kitchen-light-01",
            "params": {},
            "status": "success",
            "success": True,
            "detail": {"total": 8, "exploitable": 2, "categories": ["buffer_overflow", "hardcoded_creds"]},
            "duration_ms": 4230.5,
            "timestamp": time.time() - 600,
        },
        {
            "type": "matter_spoof",
            "target": "thermostat-01",
            "params": {},
            "status": "completed",
            "success": False,
            "detail": {"total": 5, "exploitable": 0, "results": []},
            "duration_ms": 1520.3,
            "timestamp": time.time() - 300,
        },
        {
            "type": "firmware_tamper",
            "target": "front-door-lock-01",
            "params": {},
            "status": "success",
            "success": True,
            "detail": {"total": 8, "exploitable": 3, "categories": ["replay_attack", "hardcoded_creds", "insecure_update"]},
            "duration_ms": 5120.7,
            "timestamp": time.time() - 120,
        },
    ]

    # 3. Background generators
    import uvicorn

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    # Wire event bus (mimics DashboardServer._wire_event_bus)
    _loop = asyncio.get_running_loop()

    def _forward(event):
        import json as _json
        clients = getattr(app.state, "ws_clients", set())
        if not clients:
            return
        msg = {
            "type": "event",
            "data": {
                "topic": event.topic,
                "payload": event.data if isinstance(event.data, (dict, list, str)) else str(event.data),
                "priority": event.priority.name,
                "timestamp": event.timestamp,
            },
        }

        async def _bcast():
            data = _json.dumps(msg)
            dead = set()
            for ws in clients:
                try:
                    await ws.send_text(data)
                except Exception:
                    dead.add(ws)
            clients -= dead

        try:
            asyncio.run_coroutine_threadsafe(_bcast(), _loop)
        except RuntimeError:
            pass

    event_bus.subscribe("*", _forward)

    # Start background tasks
    tasks = [
        asyncio.create_task(_traffic_generator(hub, 1.5)),
        asyncio.create_task(_event_generator(event_bus, 3.0)),
        asyncio.create_task(_device_state_changer(hub, 5.0)),
        asyncio.create_task(_eval_faker(app, 8.0)),
    ]

    log.info("=" * 60)
    log.info(f"  VESPER Dashboard UI Test — http://localhost:{port}")
    log.info(f"  {len(hub._devices)} mock devices, traffic & events auto-generated")
    log.info(f"  Press Ctrl+C to stop")
    log.info("=" * 60)

    try:
        await server.serve()
    finally:
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VESPER Dashboard UI Tester")
    parser.add_argument("--port", type=int, default=8080, help="Port (default 8080)")
    args = parser.parse_args()

    try:
        asyncio.run(main(port=args.port))
    except KeyboardInterrupt:
        log.info("Stopped.")
