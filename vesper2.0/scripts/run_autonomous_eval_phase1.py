#!/usr/bin/env python3
"""
VESPER Autonomous Full-Pipeline Evaluation

Runs the FULL VESPER pipeline (Pygame 3D, humanoid, IoT, sensors,
SmartThings Docker firmware) **autonomously** — no manual interaction —
and collects conference-quality evaluation data.

This is a copy of vesper_smartthings.py with:
  - Autonomous daily-schedule navigation (LLM-generated)
  - Evaluation data collection at every layer
  - Auto-quit after N simulated days
  - JSON + LaTeX results output

Usage:
    conda activate vesper
    # Make sure LMStudio is running with a model loaded
    python scripts/run_autonomous_eval.py
    python scripts/run_autonomous_eval.py --num-days 2 --with-smartthings
    python scripts/run_autonomous_eval.py --num-scenes 3 --num-days 1

Output:
    results/vesper_autonomous_eval/
        eval_results.json
        eval_summary.txt
"""

import os
import sys
import logging
import argparse
import math
import time as time_module
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Override LLM defaults BEFORE any VESPER module creates an LLMConfig
# ---------------------------------------------------------------------------
os.environ.setdefault("OPENWEBUI_URL", "http://localhost:1234/v1/chat/completions")
os.environ.setdefault("OPENWEBUI_API_KEY", "lm-studio")

# Set up paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "habitat-lab-official"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "habitat-lab-official", "habitat-lab"))

# Set up logging to file
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Evaluation results directory
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results", "vesper_autonomous_eval")
os.makedirs(RESULTS_DIR, exist_ok=True)

# Create log filename with timestamp
log_filename = f"vesper_objectnav_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
log_filepath = os.path.join(LOGS_DIR, log_filename)

# Configure logging to write to both file and console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(log_filepath),
        logging.StreamHandler(sys.stdout)
    ]
)

# Create logger for this module
logger = logging.getLogger(__name__)
logger.info(f"=" * 80)
logger.info(f"VESPER ObjectNav Demo - Session Started")
logger.info(f"Log file: {log_filepath}")
logger.info(f"=" * 80)

import numpy as np
import magnum as mn
import habitat_sim
from habitat_sim.utils.common import quat_from_angle_axis, quat_to_angle_axis
from habitat_sim.errors import GreedyFollowerError
try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False
    pygame = None
import random
from typing import Optional, Dict, List, Tuple
import json
from omegaconf import DictConfig

# Import Habitat humanoid components
try:
    from habitat.articulated_agents.humanoids.kinematic_humanoid import KinematicHumanoid
    from habitat.articulated_agent_controllers import HumanoidRearrangeController
    HABITAT_HUMANOID_AVAILABLE = True
except ImportError:
    HABITAT_HUMANOID_AVAILABLE = False
    print("[WARNING] Habitat humanoid modules not available, will use fallback")

# Import VESPER LLM client
sys.path.insert(0, os.path.join(PROJECT_ROOT, "vesper"))
from vesper.agents.llm_client import LLMClient, LLMConfig, LLMMessage

# Import VESPER modular components
from vesper.habitat.iot_overlay import IoTDeviceManager, IoTOverlayRenderer
from vesper.habitat.humanoid import HumanoidController, HumanoidRenderer
from vesper.habitat.vesper_integration import VesperIntegration, VesperConfig
from vesper.habitat.sensors import (
    PIRMotionSensor,
    MotionSensorConfig,
    SensitivityLevel,
    SecurityCamera,
    CameraConfig,
)
from vesper.habitat.sensor_bridge import (
    Sensor3DBridge,
    SensorBridgeConfig,
    create_sensor_bridge_for_scene,
)
from vesper.simulation import (
    AutonomousSimulation,
    HumanoidPersona,
    TimeManager,
    TimeConfig,
    TaskDatabase,
)

# Import SmartThings + Docker firmware integration
import asyncio
import threading
import socket
from vesper.integrations import (
    SmartThingsSchemaConnector,
    SchemaConnectorConfig,
    VirtualDeviceDefinition,
    DeviceHandlerType,
)

# SmartThings credentials
# These are OUR OAuth credentials for account linking
SMARTTHINGS_CLIENT_ID = os.getenv(
    "SMARTTHINGS_CLIENT_ID",
    "vesper-smart-home-2025",
)
SMARTTHINGS_CLIENT_SECRET = os.getenv(
    "SMARTTHINGS_CLIENT_SECRET",
    "VESPER_SmartHome_Secret_2025_SecureKey_AbC123XyZ789",
)
# This is the SmartThings App Client Secret from the Developer Portal.
# Required for proactive state updates (3D → ST sync).
# Find it: developer.smartthings.com > Your Project > App Credentials > Client Secret
ST_APP_CLIENT_SECRET = os.getenv(
    "ST_APP_CLIENT_SECRET",
    "5119aeaec7780c102baa855de44b85b49351ebffd0604f892651d5ed76e1d29af291ba5206a052bf01b6ea2fdf0dafcc1e7ebbb380697ac6709d9088daee664ed3c688325b6d9ba796362f45dbd6bf657daf3b0ac8f4cfa6ccb898e102e9abaa581ffafb7df47c00d701fba6599ed6508a64e67522c55998521f555aa9a414344bfcb140253385708b3c33f746a9e2de704e88fd6146666495460e440fd9213255dcb8b2009ead2f3b1cb90eb93ac2c5c98b321eb0a81f80439fe5467211e606d10e5f05229dc669d06d7f5e0ed4ee2f473b4bb1bd6c61f2eaa80c50290163eef9f407114962cad4f9269651c7f3e071b330af5b411dad37d9712180bc85a489",
)
DOCKER_IMAGE = "vesper-qemu-arm:latest"
BASE_HOST_PORT = 15001
SMARTTHINGS_PORT = 8443

# Proximity interaction threshold (meters)
INTERACTION_DISTANCE = 2.0
INTERACTION_COOLDOWN = 3.0  # seconds between auto-interactions

# High-quality rendering config
RESOLUTION = (1280, 720)  # HD resolution
SENSOR_HEIGHT = 1.5  # Human eye height
MOVE_SPEED = 0.15  # m per step
TURN_SPEED = 5.0  # degrees per step

# Third-person camera settings (bird's-eye view directly above humanoid)
THIRD_PERSON_DISTANCE = 0.0  # No horizontal offset - directly above
THIRD_PERSON_HEIGHT = 5.0  # 5m above humanoid for bird's-eye view


# ────────────────────────────────────────────────────────────────────
# SmartThings ↔ 3D Bridge
# ────────────────────────────────────────────────────────────────────

class DockerFirmwareDevice:
    """Single Docker container running QEMU ARM firmware, controlled via TCP."""

    def __init__(self, device_id: str, name: str, room: str,
                 device_type: DeviceHandlerType = DeviceHandlerType.DIMMER,
                 host_port: int = 15001):
        self.device_id = device_id
        self.name = name
        self.room = room
        self.device_type = device_type
        self.host_port = host_port
        self.container_name = f"vesper-{device_id}"
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._running = False
        self._read_task: Optional[asyncio.Task] = None

        # Debounce: after sending a command (cloud OR local), ignore firmware
        # echoes for this many seconds so the old state doesn't overwrite.
        self._last_command_time: float = 0.0
        self._command_debounce: float = 2.0

        # SmartThings state
        self.state = {
            "st.switch.switch": "off",
            "st.switchLevel.level": 100,
            "st.healthCheck.healthStatus": "online",
        }

    # ---- lifecycle ----

    async def start(self) -> bool:
        """Start Docker container and connect via TCP serial."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", self.container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        docker_cmd = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "-e", f"DEVICE_ID={self.device_id}",
            "-e", f"DEVICE_NAME={self.name}",
            "-e", "SERIAL_PORT=5555",
            "-p", f"{self.host_port}:5555",
            DOCKER_IMAGE,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(f"Docker run failed for {self.device_id}: {stderr.decode().strip()}")
                return False
            cid = stdout.decode().strip()[:12]
            logger.info(f"🐳 Container started: {self.container_name} ({cid})")
        except FileNotFoundError:
            logger.error("Docker is not installed or not in PATH")
            return False

        connected = await self._connect_tcp(retries=15, delay=0.5)
        if not connected:
            logger.error(f"TCP connect failed: {self.container_name} port {self.host_port}")
            return False

        self._running = True
        self._read_task = asyncio.create_task(self._read_loop())
        await asyncio.sleep(0.3)
        await self._send("IDENTIFY")
        logger.info(f"✅ Firmware device started: {self.name}")
        return True

    async def stop(self):
        self._running = False
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", self.container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            pass
        logger.info(f"🛑 Stopped: {self.name}")

    # ---- TCP serial ----

    async def _connect_tcp(self, retries=15, delay=0.5) -> bool:
        for attempt in range(retries):
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    "127.0.0.1", self.host_port)
                logger.info(f"🔗 TCP connected: {self.name} -> localhost:{self.host_port}")
                return True
            except (ConnectionRefusedError, OSError):
                if attempt < retries - 1:
                    await asyncio.sleep(delay)
        return False

    async def _send(self, command: str):
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write((command + "\n").encode())
                await self._writer.drain()
            except Exception as e:
                logger.error(f"TCP send error ({self.device_id}): {e}")

    async def _read_loop(self):
        while self._running and self._reader:
            try:
                line = await asyncio.wait_for(self._reader.readline(), timeout=1.0)
                if line:
                    text = line.decode("utf-8", errors="replace").strip()
                    self._process_line(text)
                elif line == b"":
                    break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                break

    def _process_line(self, line: str):
        if not line:
            return
        logger.debug(f"[{self.device_id}] RX: {line}")
        if line.startswith("SWITCH:"):
            import time as _t
            new_val = line.split(":")[1].strip().lower()
            # Ignore firmware echoes during debounce window after ANY
            # command (local toggle, proximity, or cloud).  The command
            # already set the canonical state; the firmware may echo
            # the OLD value before it finishes processing.
            if (_t.time() - self._last_command_time) < self._command_debounce:
                # Accept echoes that CONFIRM the expected state
                if new_val == self.state.get("st.switch.switch"):
                    logger.debug(f"[{self.device_id}] Firmware confirms: SWITCH:{new_val}")
                else:
                    logger.debug(f"[{self.device_id}] Ignoring stale firmware echo SWITCH:{new_val} (expected {self.state.get('st.switch.switch')})")
                return
            old_val = self.state.get("st.switch.switch")
            if new_val != old_val:
                self.state["st.switch.switch"] = new_val
                logger.info(f"🐳 [{self.device_id}] Docker firmware state: {old_val} → {new_val}")

    # ---- SmartThings command handler ----

    async def handle_command(self, capability: str, command: str, args: list) -> bool:
        """Handle a command from ANY source (cloud or local).

        Sets state optimistically and debounces firmware echoes so the
        old state doesn't overwrite the new one before the Docker
        container finishes processing.
        """
        import time as _t
        self._last_command_time = _t.time()  # debounce firmware echo

        if capability == "st.switch":
            if command == "on":
                await self._send("ON")
                self.state["st.switch.switch"] = "on"
                logger.info(f"💡 [{self.name}] → ON  (Docker container: {self.container_name})")
            elif command == "off":
                await self._send("OFF")
                self.state["st.switch.switch"] = "off"
                logger.info(f"💡 [{self.name}] → OFF (Docker container: {self.container_name})")
        elif capability == "st.switchLevel" and command == "setLevel":
            level = args[0] if args else 100
            self.state["st.switchLevel.level"] = level
            if level > 0:
                await self._send("ON")
                self.state["st.switch.switch"] = "on"
                logger.info(f"💡 [{self.name}] → ON level={level} (Docker: {self.container_name})")
            else:
                await self._send("OFF")
                self.state["st.switch.switch"] = "off"
                logger.info(f"💡 [{self.name}] → OFF level=0 (Docker: {self.container_name})")
        return True

    @property
    def is_on(self) -> bool:
        return self.state.get("st.switch.switch") == "on"


class SmartThings3DBridge:
    """
    Bi-directional bridge between the 3D Habitat scene and SmartThings cloud.

    Responsibilities:
    - Start/stop Docker firmware containers for each room
    - Run SmartThings Schema Connector in a background thread
    - Map 3D room positions to firmware devices
    - Detect humanoid proximity → trigger device interactions
    - Sync phone app commands back to the 3D scene
    """

    def __init__(self, port: int = SMARTTHINGS_PORT):
        self.port = port
        self.connector: Optional[SmartThingsSchemaConnector] = None
        self.firmware_devices: Dict[str, DockerFirmwareDevice] = {}
        self.room_device_map: Dict[str, str] = {}      # room_name -> device_id
        self.device_positions: Dict[str, Tuple[float, float, float]] = {}  # device_id -> 3D pos

        # Background asyncio loop for SmartThings webhook + Docker
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # ngrok public URL (auto-detected)
        self.ngrok_url: Optional[str] = None

        # Interaction tracking
        self._last_interaction_time: Dict[str, float] = {}   # device_id -> timestamp
        self._interaction_log: List[Dict] = []

    # ---- startup / shutdown ----

    def start(self, room_positions: Dict[str, Tuple[float, float, float]]):
        """Start the SmartThings bridge in a background thread.

        Args:
            room_positions: room_name -> (x, y, z) navigable position
        """
        if self._running:
            logger.warning("[ST-3D] Bridge already running")
            return

        # Check if port is available
        if not self._port_available(self.port):
            logger.error(f"[ST-3D] Port {self.port} is already in use!")
            logger.error(f"[ST-3D] Please run: lsof -ti:{self.port} | xargs kill -9")
            logger.error("[ST-3D] Or wait for previous process to finish")
            raise RuntimeError(f"Port {self.port} is not available")

        self._running = True
        self._thread = threading.Thread(
            target=self._run_background_loop,
            args=(room_positions,),
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        """Stop the bridge and clean up Docker containers."""
        if not self._running:
            return
        self._running = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_stop(), self._loop)
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("[ST-3D] Bridge stopped")

    def _port_available(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) != 0

    # ---- background asyncio loop ----

    def _run_background_loop(self, room_positions: Dict[str, Tuple[float, float, float]]):
        """Run the SmartThings connector + Docker devices in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_start(room_positions))
            self._loop.run_forever()
        except Exception as e:
            logger.error(f"[ST-3D] Background loop error: {e}")
        finally:
            self._loop.close()

    async def _async_start(self, room_positions: Dict[str, Tuple[float, float, float]]):
        """Async initialization: Docker containers + Schema connector."""
        logger.info("[ST-3D] Starting SmartThings + Docker bridge …")

        # 1) SmartThings Schema Connector
        config = SchemaConnectorConfig(
            host="0.0.0.0",
            port=self.port,
            webhook_path="/schema",
            smartthings_client_id=SMARTTHINGS_CLIENT_ID,
            smartthings_client_secret=ST_APP_CLIENT_SECRET,  # Portal secret for callback token exchange
        )
        self.connector = SmartThingsSchemaConnector(config)
        self.connector.on_command(self._handle_cloud_command)

        # 2) Create one Docker firmware device per room (up to 6 rooms)
        rooms = list(room_positions.keys())[:6]
        for idx, room_name in enumerate(rooms):
            dev_id = f"vesper-3d-{room_name.replace(' ', '-').replace('.', '-').replace('/', '-')}"
            friendly = f"{room_name.title()} Light"
            port = BASE_HOST_PORT + 10 + idx  # 15011, 15012, …

            fw = DockerFirmwareDevice(
                device_id=dev_id,
                name=friendly,
                room=room_name,
                device_type=DeviceHandlerType.DIMMER,
                host_port=port,
            )

            if await fw.start():
                self.firmware_devices[dev_id] = fw
                self.room_device_map[room_name] = dev_id
                self.device_positions[dev_id] = room_positions[room_name]
                logger.info(f"  ✅ {friendly} (port {port})")
            else:
                logger.warning(f"  ❌ {friendly} — container failed")

            # Register with SmartThings
            st_dev = VirtualDeviceDefinition(
                external_device_id=dev_id,
                friendly_name=friendly,
                device_handler_type=DeviceHandlerType.DIMMER,
                manufacturer_name="VESPER",
                model_name="3D Environment Light",
                sw_version="2.0.0",
                room_name=room_name.title(),
            )
            st_dev.state = fw.state.copy()
            self.connector.register_device(st_dev)

        # 3) Start the webhook server
        await self.connector.start()
        logger.info(f"[ST-3D] SmartThings webhook listening on :{self.port}/schema")
        logger.info(f"[ST-3D] {len(self.firmware_devices)} Docker firmware devices running")

        # 4) Detect ngrok URL
        self.ngrok_url = await self._detect_ngrok_url()
        if self.ngrok_url:
            logger.info(f"[ST-3D] ngrok detected: {self.ngrok_url}")
            logger.info(f"[ST-3D] SmartThings Target URL: {self.ngrok_url}/schema")
        else:
            logger.warning("[ST-3D] ngrok NOT detected — run 'ngrok http 8443' for phone control")

        # 5) Trigger discovery callback so SmartThings finds the new devices
        try:
            discovered = await self.connector.trigger_discovery_callback()
            if discovered:
                logger.info("[ST-3D] ✅ Sent discovery callback to SmartThings")
            else:
                logger.info("[ST-3D] No stored callback credentials — user must link in SmartThings app")
        except Exception as e:
            logger.debug(f"[ST-3D] Discovery callback skipped: {e}")

        # 6) Start periodic Docker state sync
        self._sync_task = asyncio.create_task(self._docker_state_sync_loop())

    async def _docker_state_sync_loop(self):
        """Periodically sync connector device state from Docker firmware state.

        This ensures the connector's VirtualDeviceDefinition always matches
        the Docker firmware state, so when SmartThings sends a
        stateRefreshRequest the response is accurate.

        If the state drifted (e.g. due to a 3D toggle), this also sends
        a stateCallback to push the update to SmartThings immediately.
        """
        while True:
            try:
                await asyncio.sleep(3.0)
                for dev_id, fw in self.firmware_devices.items():
                    if not self.connector:
                        continue
                    st_dev = self.connector.get_device(dev_id)
                    if not st_dev:
                        continue
                    fw_state = fw.state.get("st.switch.switch", "off")
                    st_state = st_dev.state.get("st.switch.switch", "off")
                    if fw_state != st_state:
                        logger.info(
                            f"🔄 [sync] {fw.name}: connector={st_state} "
                            f"→ firmware={fw_state} — pushing to SmartThings"
                        )
                        await self._push_state_to_cloud(dev_id, fw_state)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[sync] Error: {e}")

    async def _detect_ngrok_url(self) -> Optional[str]:
        """Auto-detect ngrok public URL via the local ngrok API."""
        try:
            import aiohttp as _aiohttp
            async with _aiohttp.ClientSession() as session:
                async with session.get(
                    "http://127.0.0.1:4040/api/tunnels", timeout=_aiohttp.ClientTimeout(total=2)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for tunnel in data.get("tunnels", []):
                            public_url = tunnel.get("public_url", "")
                            if public_url.startswith("https://"):
                                return public_url
                            if public_url.startswith("http://"):
                                # prefer https but use http if that's all we have
                                self.ngrok_url = public_url
                        return self.ngrok_url
        except Exception:
            pass
        return None

    async def _async_stop(self):
        """Async cleanup."""
        if hasattr(self, '_sync_task') and self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        for fw in self.firmware_devices.values():
            await fw.stop()
        if self.connector:
            await self.connector.stop()
        self._loop.stop()

    # ---- state push helper ----

    async def _push_state_to_cloud(self, dev_id: str, new_state: str):
        """Push device state change to SmartThings cloud.

        1. Updates the connector's internal device state so
           stateRefreshRequest always returns the correct value.
        2. Sends a stateCallback if callback credentials are available
           (for instant update in the SmartThings app).
        """
        if not self.connector:
            return

        # Step 1: Sync the connector's VirtualDeviceDefinition.state
        st_dev = self.connector.get_device(dev_id)
        if st_dev:
            st_dev.state["st.switch.switch"] = new_state
            logger.debug(f"[push] Synced connector state for {dev_id} → {new_state}")

        # Step 2: Try proactive stateCallback (requires grantCallbackAccess)
        sent = await self.connector.update_device_state(
            dev_id,
            {"st.switch.switch": new_state},
            trigger_callback=True,
        )
        if sent:
            logger.info(f"☁️  [3D→ST] stateCallback sent for {dev_id} → {new_state}")
        else:
            logger.debug(
                f"[push] stateCallback not sent for {dev_id} "
                f"(no callback creds — ST will poll via stateRefreshRequest)"
            )

    # ---- cloud → 3D (SmartThings app command) ----

    async def _handle_cloud_command(self, device_id: str, capability: str,
                                     command: str, arguments: list) -> bool:
        """Handle a command from the SmartThings phone app → Docker firmware → 3D.

        Flow: SmartThings Cloud → ngrok → webhook → this handler
              → Docker container (QEMU ARM firmware) → TCP serial
              → state update visible in 3D panel
        """
        logger.info(f"📱 [ST→3D] {device_id}: {capability}.{command}({arguments})")

        fw = self.firmware_devices.get(device_id)
        if fw:
            # Send to Docker container — handle_command sets debounce
            # so firmware echoes of the old state don't overwrite
            result = await fw.handle_command(capability, command, arguments)

            # Sync state back to connector so stateRefreshRequest returns
            # the correct value
            if self.connector:
                st_dev = self.connector.get_device(device_id)
                if st_dev:
                    st_dev.state = fw.state.copy()

            new_state = fw.state.get("st.switch.switch", "?")
            logger.info(
                f"📱 [ST→3D] ✅ {fw.name} in {fw.room} → {new_state.upper()}"
                f"  (Docker: {fw.container_name})"
            )

            self._interaction_log.append({
                "direction": "cloud→3D",
                "device_id": device_id,
                "room": fw.room,
                "command": f"{capability}.{command}",
                "new_state": new_state,
            })
            return result

        logger.warning(f"📱 [ST→3D] ❌ Device {device_id} not found in firmware_devices")
        return False

    # ---- 3D → cloud (proximity interaction) ----

    def check_proximity_interaction(
        self,
        humanoid_pos: Tuple[float, float, float],
        current_time: float,
    ) -> List[Dict]:
        """
        Check if the humanoid is close enough to any device to interact.

        Returns list of interaction events (newly triggered devices).
        """
        import time as time_mod
        events = []

        for dev_id, dev_pos in self.device_positions.items():
            dx = humanoid_pos[0] - dev_pos[0]
            dy = humanoid_pos[1] - dev_pos[1]
            dz = humanoid_pos[2] - dev_pos[2]
            dist = (dx*dx + dy*dy + dz*dz) ** 0.5

            if dist > INTERACTION_DISTANCE:
                continue

            # Cooldown check
            last_t = self._last_interaction_time.get(dev_id, 0)
            if (current_time - last_t) < INTERACTION_COOLDOWN:
                continue

            # Toggle the device
            fw = self.firmware_devices.get(dev_id)
            if not fw:
                continue

            new_state = "off" if fw.is_on else "on"
            self._last_interaction_time[dev_id] = current_time

            # Route through handle_command → Docker container → debounce
            if self._loop and self._loop.is_running():
                cap = "st.switch"
                cmd = "on" if new_state == "on" else "off"
                asyncio.run_coroutine_threadsafe(
                    fw.handle_command(cap, cmd, []),
                    self._loop,
                )
                # Push state to SmartThings cloud (sync + stateCallback)
                asyncio.run_coroutine_threadsafe(
                    self._push_state_to_cloud(dev_id, new_state),
                    self._loop,
                )

            event = {
                "direction": "3D→cloud",
                "device_id": dev_id,
                "room": fw.room,
                "new_state": new_state,
                "distance": dist,
            }
            self._interaction_log.append(event)
            events.append(event)
            logger.info(
                f"🏠 [3D→ST] Humanoid near {fw.room} ({dist:.1f}m) → "
                f"toggled {fw.name} {new_state.upper()}"
            )

        return events

    def toggle_device_in_room(self, room_name: str) -> Optional[Dict]:
        """Manually toggle a device in the given room (called from keybind)."""
        import time as time_mod
        dev_id = self.room_device_map.get(room_name)
        if not dev_id:
            # Try partial match
            for rn, did in self.room_device_map.items():
                if room_name.lower() in rn.lower():
                    dev_id = did
                    break
        if not dev_id:
            return None

        fw = self.firmware_devices.get(dev_id)
        if not fw:
            return None

        new_state = "off" if fw.is_on else "on"
        self._last_interaction_time[dev_id] = time_mod.time()

        # Route through handle_command → Docker container → debounce
        if self._loop and self._loop.is_running():
            cap = "st.switch"
            cmd = "on" if new_state == "on" else "off"
            asyncio.run_coroutine_threadsafe(
                fw.handle_command(cap, cmd, []),
                self._loop,
            )
            # Push state to SmartThings cloud (sync + stateCallback)
            asyncio.run_coroutine_threadsafe(
                self._push_state_to_cloud(dev_id, new_state),
                self._loop,
            )

        event = {
            "direction": "manual",
            "device_id": dev_id,
            "room": fw.room,
            "new_state": new_state,
        }
        self._interaction_log.append(event)
        return event

    # ---- state queries ----

    def get_device_states(self) -> Dict[str, Dict]:
        """Get current state of all firmware devices for UI rendering."""
        states = {}
        for dev_id, fw in self.firmware_devices.items():
            states[dev_id] = {
                "device_id": dev_id,
                "name": fw.name,
                "room": fw.room,
                "is_on": fw.is_on,
                "state": fw.state.get("st.switch.switch", "off"),
                "position": self.device_positions.get(dev_id),
            }
        return states

    def get_recent_interactions(self, count: int = 10) -> List[Dict]:
        return self._interaction_log[-count:]

    def get_closest_device(self, pos: Tuple[float, float, float]) -> Optional[Tuple[str, float]]:
        """Return (device_id, distance) of the closest device to pos."""
        closest_id, closest_dist = None, float("inf")
        for dev_id, dev_pos in self.device_positions.items():
            dx = pos[0] - dev_pos[0]
            dy = pos[1] - dev_pos[1]
            dz = pos[2] - dev_pos[2]
            d = (dx*dx + dy*dy + dz*dz) ** 0.5
            if d < closest_dist:
                closest_dist = d
                closest_id = dev_id
        if closest_id:
            return closest_id, closest_dist
        return None


# ────────────────────────────────────────────────────────────────────
# Articulated Device Bridge — links 3D articulated objects ↔ IoT devices
# ────────────────────────────────────────────────────────────────────

# Map URDF robot name patterns → IoT device category
_ARTICULAT_TO_IOT_TYPE: Dict[str, str] = {
    "refrigerator": "fridge",
    "fridge":       "fridge",
    "microwave":    "microwave",
    "oven":         "oven",
    "stove":        "oven",
    "dishwasher":   "dishwasher",
    "washing":      "washer",
    "cabinet":      "cabinet",
    "kitchen_cabinet": "cabinet",
    "wardrobe":     "wardrobe",
    "dresser":      "dresser",
    "drawer":       "drawer",
    "chest":        "drawer",
    "display_cabinet": "cabinet",
    "door":         "door",
    "barbecue":     "appliance",
    "bench":        "furniture",
    "locker":       "cabinet",
    "nightstand":   "furniture",
}


@dataclass
class ArticulatedDevice:
    """An articulated 3D object linked to an IoT device."""
    obj_handle: str               # habitat-sim object handle
    obj_id: int                   # habitat-sim object id
    device_type: str              # e.g. "fridge", "cabinet", "wardrobe"
    robot_name: str               # URDF robot name (e.g. "Refrigerator0001_ARMATURE")
    position: Tuple[float, float, float]   # world position
    room_name: str = "unknown"    # assigned room
    num_joints: int = 0
    joint_limits: Optional[List[Tuple[float, float]]] = None  # (min, max) per joint
    is_open: bool = False         # current state (open/closed)
    iot_device_id: Optional[str] = None   # linked VESPER IoT device ID
    last_interaction_time: float = 0.0


class ArticulatedDeviceBridge:
    """
    Discovers articulated objects in a loaded Habitat scene and links them
    to VESPER IoT devices.  When the humanoid is near an articulated object
    it can open/close doors, drawers, fridges, etc. — both visually in 3D
    AND through the IoT pipeline (MQTT → firmware → SmartThings).
    """

    # Proximity thresholds
    INTERACT_DIST = 1.8        # metres — must be within this to interact
    INTERACT_COOLDOWN = 5.0    # seconds between interactions on same object
    JOINT_ANIM_SPEED = 0.04    # radians per sim-step for smooth animation

    def __init__(self, sim: habitat_sim.Simulator):
        self.sim = sim
        self.ao_mgr = sim.get_articulated_object_manager()
        self.devices: Dict[str, ArticulatedDevice] = {}   # handle → device
        self._anim_queue: List[dict] = []  # in-progress joint animations

    # ------------------------------------------------------------------
    # Discovery — call once after scene is loaded
    # ------------------------------------------------------------------
    def discover_devices(
        self,
        room_positions: Dict[str, Tuple[float, float, float]],
    ) -> int:
        """
        Scan every articulated object in the loaded scene, classify it by
        URDF name, and assign it to the nearest room.

        Returns:
            Number of articulated devices discovered.
        """
        import re

        handles = self.ao_mgr.get_object_handles()
        logger.info(f"[ART-BRIDGE] Scanning {len(handles)} articulated objects …")

        for handle in handles:
            ao = self.ao_mgr.get_object_by_handle(handle)
            if ao is None or not ao.is_alive:
                continue

            # --- Classify by creation-attributes template name ---
            tmpl = ao.creation_attributes.handle if ao.creation_attributes else ""

            # HSSD uses SHA-hash directory names; the real object name lives
            # in the .glb files inside that directory (e.g. "Dresser0012.glb").
            robot_name = ""
            if tmpl:
                urdf_dir = os.path.dirname(tmpl)
                try:
                    if os.path.isdir(urdf_dir):
                        glbs = [f for f in os.listdir(urdf_dir)
                                if f.endswith(".glb") and "receptacle" not in f.lower() and "_" not in f]
                        if glbs:
                            # Take the first main .glb file (e.g., "Dresser0012.glb")
                            robot_name = glbs[0].replace(".glb", "")
                except Exception as e:
                    logger.debug(f"[ART-BRIDGE] Error reading urdf dir: {e}")
            
            if not robot_name:
                robot_name = os.path.basename(tmpl).replace(".ao_config.json", "").replace(".urdf", "") if tmpl else handle

            # Normalise:  "Kitchen_Cabinet0052_ARMATURE" → "kitchen_cabinet"
            base = re.sub(r"\d+.*", "", robot_name).strip("_").lower()

            logger.info(f"[ART-BRIDGE]   {robot_name} → base='{base}'")

            # Skip the humanoid itself
            if "humanoid" in base or "female" in base or "male" in base:
                logger.info(f"[ART-BRIDGE]     → skipping humanoid")
                continue

            device_type = None
            for pattern, dtype in _ARTICULAT_TO_IOT_TYPE.items():
                if pattern in base:
                    device_type = dtype
                    break
            if device_type is None:
                logger.info(f"[ART-BRIDGE]     → no match in device types, skipping")
                continue
            
            logger.info(f"[ART-BRIDGE]     ✓ matched as {device_type}")

            # Position
            pos = ao.translation
            pos_tuple = (float(pos[0]), float(pos[1]), float(pos[2]))

            # Assign to nearest room
            room = self._nearest_room(pos_tuple, room_positions)

            # Joint limits
            n_joints = len(ao.joint_positions)
            limits = None
            try:
                raw = ao.joint_position_limits
                limits = list(zip(raw[0], raw[1]))   # (lower, upper)
            except Exception:
                pass

            dev = ArticulatedDevice(
                obj_handle=handle,
                obj_id=ao.object_id,
                device_type=device_type,
                robot_name=robot_name,
                position=pos_tuple,
                room_name=room,
                num_joints=n_joints,
                joint_limits=limits,
            )
            self.devices[handle] = dev

        logger.info(f"[ART-BRIDGE] Discovered {len(self.devices)} interactive devices:")
        # Summary by type
        by_type: Dict[str, int] = {}
        for d in self.devices.values():
            by_type[d.device_type] = by_type.get(d.device_type, 0) + 1
        for dtype, cnt in sorted(by_type.items()):
            logger.info(f"  {dtype:15s}  ×{cnt}")

        return len(self.devices)

    # ------------------------------------------------------------------
    # Proximity check — call every frame
    # ------------------------------------------------------------------
    def check_interaction(
        self,
        humanoid_pos: Tuple[float, float, float],
        current_time: float,
    ) -> List[Dict]:
        """
        Check if the humanoid is close to any articulated device and trigger
        open/close animation.

        Returns list of interaction event dicts.
        """
        events: List[Dict] = []

        for handle, dev in self.devices.items():
            dx = humanoid_pos[0] - dev.position[0]
            dy = humanoid_pos[1] - dev.position[1]
            dz = humanoid_pos[2] - dev.position[2]
            dist = (dx*dx + dy*dy + dz*dz) ** 0.5

            if dist > self.INTERACT_DIST:
                continue

            # Cooldown
            if (current_time - dev.last_interaction_time) < self.INTERACT_COOLDOWN:
                continue

            dev.last_interaction_time = current_time

            # Toggle open ↔ closed
            dev.is_open = not dev.is_open
            target_frac = 0.9 if dev.is_open else 0.0   # 90 % open or closed
            action = "open" if dev.is_open else "close"

            # Queue smooth animation for ALL joints
            self._queue_animation(handle, target_frac)

            event = {
                "type": "articulated_interaction",
                "device_type": dev.device_type,
                "robot_name": dev.robot_name,
                "room": dev.room_name,
                "action": action,
                "distance": round(dist, 2),
            }
            events.append(event)
            logger.info(
                f"🔧 [ART] Humanoid near {dev.device_type} in {dev.room_name} "
                f"({dist:.1f}m) → {action} {dev.robot_name}"
            )

        return events

    # ------------------------------------------------------------------
    # Smooth joint animation — call every frame after check_interaction
    # ------------------------------------------------------------------
    def step_animations(self):
        """Advance all queued joint animations by one step."""
        finished = []
        for anim in self._anim_queue:
            ao = self.ao_mgr.get_object_by_handle(anim["handle"])
            if ao is None or not ao.is_alive:
                finished.append(anim)
                continue

            current = list(ao.joint_positions)
            done = True
            for i, target in enumerate(anim["targets"]):
                if i >= len(current):
                    break
                diff = target - current[i]
                if abs(diff) < 0.01:
                    current[i] = target
                else:
                    done = False
                    step = self.JOINT_ANIM_SPEED if diff > 0 else -self.JOINT_ANIM_SPEED
                    current[i] += step
                    # Clamp to not overshoot
                    if (step > 0 and current[i] > target) or (step < 0 and current[i] < target):
                        current[i] = target
            ao.joint_positions = current
            if done:
                finished.append(anim)

        for anim in finished:
            self._anim_queue.remove(anim)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _queue_animation(self, handle: str, target_fraction: float):
        """Enqueue a smooth animation that moves all joints to target_fraction of their range."""
        ao = self.ao_mgr.get_object_by_handle(handle)
        if ao is None:
            return
        dev = self.devices[handle]
        n = len(ao.joint_positions)
        targets = []
        for i in range(n):
            lo, hi = 0.0, 1.5  # sensible default radians
            if dev.joint_limits and i < len(dev.joint_limits):
                lo, hi = dev.joint_limits[i]
            targets.append(lo + (hi - lo) * target_fraction)

        # Remove any existing animation for this handle
        self._anim_queue = [a for a in self._anim_queue if a["handle"] != handle]
        self._anim_queue.append({"handle": handle, "targets": targets})

    @staticmethod
    def _nearest_room(
        pos: Tuple[float, float, float],
        room_positions: Dict[str, Tuple[float, float, float]],
    ) -> str:
        best_room = "unknown"
        best_dist = float("inf")
        for room, rpos in room_positions.items():
            d = ((pos[0]-rpos[0])**2 + (pos[1]-rpos[1])**2 + (pos[2]-rpos[2])**2) ** 0.5
            if d < best_dist:
                best_dist = d
                best_room = room
        return best_room

    def get_stats(self) -> Dict:
        """Return summary stats for evaluation."""
        by_type: Dict[str, int] = {}
        open_count = 0
        for d in self.devices.values():
            by_type[d.device_type] = by_type.get(d.device_type, 0) + 1
            if d.is_open:
                open_count += 1
        return {
            "total_devices": len(self.devices),
            "by_type": by_type,
            "currently_open": open_count,
            "pending_animations": len(self._anim_queue),
        }


class ObjectNavDemo:
    """First-person navigation demo with ARTICULATED HUMANOID avatar."""
    
    def __init__(self):
        self.sim: Optional[habitat_sim.Simulator] = None
        self.agent = None
        self.path_follower = None  # GreedyGeodesicFollower
        self.current_goal = None
        self.current_goal_name = None  # Name of current goal (room name)
        self.current_task = None  # LLM-generated task description
        self.objects_in_scene: Dict[str, List[mn.Vector3]] = {}
        self.humanoid = None  # Articulated humanoid (KinematicHumanoid)
        self.humanoid_controller = None  # HumanoidRearrangeController for walking
        self.use_third_person = False  # Start in first-person view
        
        # Humanoid data paths
        self.humanoid_urdf = None
        self.humanoid_motion_data = None
        
        # LLM client for task generation
        self.llm_client = None
        self.use_llm = False  # Enable LLM task generation
        
        # VESPER integration (modular components)
        self.vesper: Optional[VesperIntegration] = None
        
        # Sensors - one per room
        self.motion_sensors: List[PIRMotionSensor] = []
        self.security_cameras: List[SecurityCamera] = []
        
        # Sensor bridge - connects 3D sensors to firmware simulation
        self.sensor_bridge: Optional[Sensor3DBridge] = None
        
        # SmartThings ↔ 3D Bridge (Docker + cloud sync)
        self.smartthings_bridge: Optional[SmartThings3DBridge] = None
        
        # Articulated device bridge (3D interactive objects ↔ IoT)
        self.articulated_bridge: Optional[ArticulatedDeviceBridge] = None
        
        # Simulation time manager (60x speed)
        self.time_manager = TimeManager(TimeConfig(
            sync_to_real_time=True,
            time_scale=60.0,  # 60x: 1 real second = 1 simulated minute
        ))
        self.autonomous_sim = None  # Will be initialized with persona
        self.current_scheduled_task = None
        self.task_database = TaskDatabase()
        
        # Navigation target types (rooms/objects)
        self.target_types = [
            "toilet", "bed", "couch", "chair", "dining table",
            "refrigerator", "tv", "sink", "bathtub", "kitchen"
        ]
        
    def find_scene(self) -> Tuple[str, Optional[str]]:
        """Find a scene file to load. Returns (scene_path, config_path)."""
        data_path = os.path.join(PROJECT_ROOT, "data")
        
        # Try HSSD-hab articulated scenes first (interactive objects with joints)
        hssd_artic_path = os.path.join(data_path, "scene_datasets", "hssd-hab", "scenes-articulated")
        hssd_static_path = os.path.join(data_path, "scene_datasets", "hssd-hab", "scenes")
        hssd_path = hssd_artic_path if os.path.exists(hssd_artic_path) else hssd_static_path
        if os.path.exists(hssd_path):
            scenes = [f for f in os.listdir(hssd_path) if f.endswith(".scene_instance.json")]
            if scenes:
                scene = random.choice(scenes[:10])
                # Use articulated config if using articulated scenes
                if hssd_path == hssd_artic_path:
                    config_path = os.path.join(os.path.dirname(hssd_path), "hssd-hab-articulated.scene_dataset_config.json")
                else:
                    config_path = os.path.join(os.path.dirname(hssd_path), "hssd-hab.scene_dataset_config.json")
                return os.path.join(hssd_path, scene), config_path
        
        # Try HM3D scenes with semantic annotations
        hm3d_path = os.path.join(data_path, "scene_datasets", "hm3d", "example")
        if os.path.exists(hm3d_path):
            config_path = os.path.join(hm3d_path, "hm3d_annotated_example_basis.scene_dataset_config.json")
            if not os.path.exists(config_path):
                config_path = os.path.join(hm3d_path, "hm3d_annotated_basis.scene_dataset_config.json")
            
            for subdir in os.listdir(hm3d_path):
                scene_dir = os.path.join(hm3d_path, subdir)
                if os.path.isdir(scene_dir):
                    for f in os.listdir(scene_dir):
                        if f.endswith(".basis.glb"):
                            scene_file = os.path.join(scene_dir, f)
                            return scene_file, config_path if os.path.exists(config_path) else None
        
        # Try ReplicaCAD
        replica_path = os.path.join(data_path, "replica_cad", "configs", "scenes")
        if os.path.exists(replica_path):
            scenes = [f for f in os.listdir(replica_path) if f.endswith(".scene_instance.json")]
            if scenes:
                config = os.path.join(data_path, "replica_cad", "replicaCAD.scene_dataset_config.json")
                return os.path.join(replica_path, random.choice(scenes)), config
                
        raise FileNotFoundError("No scene files found! Please download a dataset.")
    
    def create_simulator(self, scene_path: str, config_path: Optional[str] = None) -> habitat_sim.Simulator:
        """Create simulator with high-quality rendering settings."""
        
        # Backend configuration
        backend_cfg = habitat_sim.SimulatorConfiguration()
        backend_cfg.scene_id = scene_path
        backend_cfg.enable_physics = True
        backend_cfg.physics_config_file = os.path.join(
            PROJECT_ROOT, "data", "default.physics_config.json"
        )
        
        # Use provided config or search for one
        if config_path and os.path.exists(config_path):
            backend_cfg.scene_dataset_config_file = config_path
            print(f"Using scene dataset config: {os.path.basename(config_path)}")
        
        # Agent configuration with high-res sensors
        agent_cfg = habitat_sim.agent.AgentConfiguration()
        agent_cfg.height = SENSOR_HEIGHT
        agent_cfg.radius = 0.1
        
        # RGB sensor (first-person view)
        rgb_sensor = habitat_sim.CameraSensorSpec()
        rgb_sensor.uuid = "rgb"
        rgb_sensor.sensor_type = habitat_sim.SensorType.COLOR
        rgb_sensor.resolution = [RESOLUTION[1], RESOLUTION[0]]
        rgb_sensor.position = [0.0, SENSOR_HEIGHT, 0.0]
        rgb_sensor.hfov = 90  # Wide FOV for better spatial awareness
        
        # Depth sensor for obstacle awareness
        depth_sensor = habitat_sim.CameraSensorSpec()
        depth_sensor.uuid = "depth"
        depth_sensor.sensor_type = habitat_sim.SensorType.DEPTH
        depth_sensor.resolution = [RESOLUTION[1], RESOLUTION[0]]
        depth_sensor.position = [0.0, SENSOR_HEIGHT, 0.0]
        depth_sensor.hfov = 90
        
        # Semantic sensor for object detection
        semantic_sensor = habitat_sim.CameraSensorSpec()
        semantic_sensor.uuid = "semantic"
        semantic_sensor.sensor_type = habitat_sim.SensorType.SEMANTIC
        semantic_sensor.resolution = [RESOLUTION[1] // 4, RESOLUTION[0] // 4]  # Lower res
        semantic_sensor.position = [0.0, SENSOR_HEIGHT, 0.0]
        semantic_sensor.hfov = 90
        
        # Third-person camera (bird's-eye view directly above humanoid)
        third_person_sensor = habitat_sim.CameraSensorSpec()
        third_person_sensor.uuid = "third_rgb"
        third_person_sensor.sensor_type = habitat_sim.SensorType.COLOR
        third_person_sensor.resolution = [RESOLUTION[1], RESOLUTION[0]]
        third_person_sensor.position = [0.0, THIRD_PERSON_HEIGHT, THIRD_PERSON_DISTANCE]
        # Look straight down (-90 degrees = -pi/2 radians on X axis)
        third_person_sensor.orientation = [-1.5708, 0.0, 0.0]  # -90 degrees = look straight down
        third_person_sensor.hfov = 90
        
        agent_cfg.sensor_specifications = [rgb_sensor, depth_sensor, semantic_sensor, third_person_sensor]
        
        # Action space
        agent_cfg.action_space = {
            "move_forward": habitat_sim.agent.ActionSpec(
                "move_forward", habitat_sim.agent.ActuationSpec(amount=MOVE_SPEED)
            ),
            "move_backward": habitat_sim.agent.ActionSpec(
                "move_backward", habitat_sim.agent.ActuationSpec(amount=MOVE_SPEED)
            ),
            "turn_left": habitat_sim.agent.ActionSpec(
                "turn_left", habitat_sim.agent.ActuationSpec(amount=TURN_SPEED)
            ),
            "turn_right": habitat_sim.agent.ActionSpec(
                "turn_right", habitat_sim.agent.ActuationSpec(amount=TURN_SPEED)
            ),
            "look_up": habitat_sim.agent.ActionSpec(
                "look_up", habitat_sim.agent.ActuationSpec(amount=TURN_SPEED)
            ),
            "look_down": habitat_sim.agent.ActionSpec(
                "look_down", habitat_sim.agent.ActuationSpec(amount=TURN_SPEED)
            ),
        }
        
        cfg = habitat_sim.Configuration(backend_cfg, [agent_cfg])
        
        sim = habitat_sim.Simulator(cfg)
        
        # Initialize navmesh for pathfinding
        if not sim.pathfinder.is_loaded:
            print("Computing navmesh...")
            navmesh_settings = habitat_sim.NavMeshSettings()
            navmesh_settings.set_defaults()
            navmesh_settings.agent_height = SENSOR_HEIGHT
            navmesh_settings.agent_radius = 0.1
            sim.recompute_navmesh(sim.pathfinder, navmesh_settings)
        
        return sim
    
    def init_path_follower(self):
        """Initialize the GreedyGeodesicFollower for proper pathfinding navigation."""
        # This is what Habitat actually uses for ShortestPathFollower
        self.path_follower = self.sim.make_greedy_follower(
            agent_id=0,
            goal_radius=0.5,  # Stop when within 0.5m of goal
            stop_key="stop",
            forward_key="move_forward",
            left_key="turn_left",
            right_key="turn_right",
        )
        print("Initialized GreedyGeodesicFollower for navigation")
    
    def get_random_navigable_point(self) -> mn.Vector3:
        """Get a random navigable point in the scene."""
        return self.sim.pathfinder.get_random_navigable_point()
    
    def _find_humanoid_data(self) -> Tuple[Optional[str], Optional[str]]:
        """Find available humanoid URDF and motion data files."""
        data_path = os.path.join(PROJECT_ROOT, "data")
        
        # Search paths for humanoid data
        search_paths = [
            # Versioned data paths
            os.path.join(data_path, "versioned_data", "habitat_humanoids"),
            os.path.join(data_path, "versioned_data", "hab3_bench_assets", "humanoids"),
            # Direct paths
            os.path.join(data_path, "humanoids", "humanoid_data"),
            os.path.join(PROJECT_ROOT, "habitat-lab-official", "data", "humanoids", "humanoid_data"),
        ]
        
        # Prefer female_0 or female_1 for consistent appearance
        humanoid_variants = ["female_0", "female_1", "male_2", "neutral_2"]
        
        for base_path in search_paths:
            if not os.path.exists(base_path):
                continue
            
            for variant in humanoid_variants:
                variant_path = os.path.join(base_path, variant)
                if not os.path.exists(variant_path):
                    continue
                
                urdf_file = os.path.join(variant_path, f"{variant}.urdf")
                motion_file = os.path.join(variant_path, f"{variant}_motion_data_smplx.pkl")
                
                if os.path.exists(urdf_file) and os.path.exists(motion_file):
                    print(f"[HUMANOID] Found humanoid data: {variant}")
                    return urdf_file, motion_file
        
        return None, None
    
    def load_humanoid(self):
        """
        Load articulated humanoid using Habitat 3.0 KinematicHumanoid.
        
        Uses URDF-based humanoid with proper joint articulation and
        HumanoidRearrangeController for realistic walking animations.
        """
        # First, find humanoid data files
        urdf_path, motion_path = self._find_humanoid_data()
        
        if urdf_path is None or motion_path is None:
            print("[HUMANOID] No humanoid data found, using fallback")
            return self._load_primitive_humanoid()
        
        self.humanoid_urdf = urdf_path
        self.humanoid_motion_data = motion_path
        
        # Try to load articulated humanoid
        if HABITAT_HUMANOID_AVAILABLE:
            return self._load_articulated_humanoid(urdf_path, motion_path)
        else:
            print("[HUMANOID] Habitat humanoid modules not available, using GLB fallback")
            return self._load_glb_humanoid()
    
    def _load_articulated_humanoid(self, urdf_path: str, motion_path: str):
        """Load fully articulated humanoid with walking animations."""
        try:
            print(f"[HUMANOID] Loading articulated humanoid from: {urdf_path}", flush=True)
            
            # Create agent config for humanoid
            agent_config = DictConfig({
                "articulated_agent_urdf": urdf_path,
                "motion_data_path": motion_path,
                "auto_update_sensor_transform": False,  # We control transforms manually
            })
            
            # Create the KinematicHumanoid
            print("[HUMANOID] Creating KinematicHumanoid...", flush=True)
            self.humanoid = KinematicHumanoid(agent_config, self.sim)
            print("[HUMANOID] Calling reconfigure...", flush=True)
            self.humanoid.reconfigure()
            print("[HUMANOID] Calling update...", flush=True)
            self.humanoid.update()
            
            # Initialize the walking controller
            print("[HUMANOID] Initializing HumanoidRearrangeController...", flush=True)
            self.humanoid_controller = HumanoidRearrangeController(motion_path)
            self.humanoid_controller.reset(self.humanoid.base_transformation)
            
            # Get agent position to place humanoid at same location
            agent_state = self.agent.get_state()
            agent_pos = agent_state.position
            self.humanoid.base_pos = mn.Vector3(agent_pos[0], agent_pos[1], agent_pos[2])
            
            print(f"[HUMANOID] ✓ Loaded ARTICULATED humanoid with walking animations!")
            print(f"[HUMANOID]   URDF: {os.path.basename(urdf_path)}")
            print(f"[HUMANOID]   Motion: {os.path.basename(motion_path)}")
            print(f"[HUMANOID]   Initial pos: {agent_pos}")
            
            # Start hidden in first-person view
            self._humanoid_hidden = True
            self.humanoid.base_pos = mn.Vector3(10000, 10000, 10000)  # Hide initially
            return self.humanoid
            
        except Exception as e:
            print(f"[HUMANOID] Articulated humanoid failed: {e}")
            import traceback
            traceback.print_exc()
            return self._load_glb_humanoid()
    
    def _load_glb_humanoid(self):
        """Fallback: Load humanoid as GLB rigid object (no animations)."""
        try:
            # Path to the GLB humanoid model
            humanoid_glb = os.path.join(
                PROJECT_ROOT, "data", "versioned_data", "habitat_humanoids",
                "female_0", "female_0.glb"
            )
            
            # Alternative path
            if not os.path.exists(humanoid_glb):
                humanoid_glb = os.path.join(
                    PROJECT_ROOT, "habitat-lab-official", "data", "humanoids",
                    "humanoid_data", "female_0", "female_0.glb"
                )
            
            if os.path.exists(humanoid_glb):
                print(f"[HUMANOID] Loading GLB model: {humanoid_glb}")
                
                rigid_obj_mgr = self.sim.get_rigid_object_manager()
                obj_template_mgr = self.sim.get_object_template_manager()
                
                # Load the GLB as an object template
                template_ids = obj_template_mgr.load_object_configs(humanoid_glb)
                
                # Get all templates to find the one we just loaded
                all_templates = obj_template_mgr.get_template_handles()
                humanoid_template = None
                
                for tmpl in all_templates:
                    if 'female' in tmpl.lower():
                        humanoid_template = tmpl
                        break
                
                if humanoid_template:
                    self.humanoid = rigid_obj_mgr.add_object_by_template_handle(humanoid_template)
                    
                    if self.humanoid:
                        self.humanoid.motion_type = habitat_sim.physics.MotionType.KINEMATIC
                        self._humanoid_hidden = True
                        self.humanoid.translation = mn.Vector3(10000, 10000, 10000)
                        
                        print(f"[HUMANOID] ✓ Loaded GLB humanoid (no animations)")
                        return self.humanoid
            
            return self._load_primitive_humanoid()
            
        except Exception as e:
            print(f"[HUMANOID] GLB loading failed: {e}")
            return self._load_primitive_humanoid()
    
    def _load_primitive_humanoid(self):
        """Fallback: create a simple cube humanoid."""
        try:
            rigid_obj_mgr = self.sim.get_rigid_object_manager()
            prim_mgr = self.sim.get_asset_template_manager()
            
            templates = prim_mgr.get_template_handles()
            cube_handle = [t for t in templates if 'cube' in t.lower() and 'solid' in t.lower()]
            
            if not cube_handle:
                cube_handle = [t for t in templates if 'cube' in t.lower()]
            
            if cube_handle:
                self.humanoid = rigid_obj_mgr.add_object_by_template_handle(cube_handle[0])
                
                if self.humanoid:
                    self.humanoid.motion_type = habitat_sim.physics.MotionType.KINEMATIC
                    self._humanoid_hidden = True
                    self.humanoid.translation = mn.Vector3(10000, 10000, 10000)
                    print(f"[HUMANOID] ✓ Created primitive cube proxy")
                    return self.humanoid
            
            return None
        except Exception as e:
            print(f"[HUMANOID] Primitive creation failed: {e}")
            return None
    
    def update_humanoid_position(self, is_moving: bool = False, target_pos: Optional[mn.Vector3] = None):
        """Update humanoid position to follow the agent with walking animation."""
        if self.humanoid is None:
            return
            
        # If hidden, don't update position (keep it far away)
        if hasattr(self, '_humanoid_hidden') and self._humanoid_hidden:
            return
        
        # Get agent state
        agent_state = self.agent.get_state()
        pos = agent_state.position
        
        # For articulated humanoid with walking controller
        if HABITAT_HUMANOID_AVAILABLE and isinstance(self.humanoid, KinematicHumanoid) and self.humanoid_controller:
            # Calculate relative position to move towards
            if is_moving and target_pos is not None:
                # Calculate direction from humanoid to target
                humanoid_pos = self.humanoid_controller.obj_transform_base.translation
                relative_target = target_pos - humanoid_pos
                
                # Only use horizontal movement (y=0)
                relative_target = mn.Vector3(relative_target.x, 0, relative_target.z)
                
                # Calculate walking pose (this advances the mocap animation)
                self.humanoid_controller.calculate_walk_pose(relative_target)
            else:
                # Standing still - use stop pose
                self.humanoid_controller.calculate_stop_pose()
            
            # Get the pose from controller
            pose = self.humanoid_controller.get_pose()
            
            # Parse the pose (joint_positions + offset_transform + base_transform)
            num_joints = len(self.humanoid.sim_obj.joint_positions)
            joint_positions = pose[:num_joints]
            offset_transform = mn.Matrix4(np.array(pose[num_joints:num_joints+16]).reshape(4, 4).T)
            base_transform = mn.Matrix4(np.array(pose[num_joints+16:num_joints+32]).reshape(4, 4).T)
            
            # Apply the pose to the humanoid
            self.humanoid.set_joint_transform(joint_positions, offset_transform, base_transform)
            
        elif HABITAT_HUMANOID_AVAILABLE and isinstance(self.humanoid, KinematicHumanoid):
            # Fallback: just set position/rotation without animation
            rot = agent_state.rotation
            self.humanoid.base_pos = mn.Vector3(pos[0], pos[1], pos[2])
            yaw = float(np.arctan2(2.0 * (rot.w * rot.y + rot.x * rot.z), 
                                   1.0 - 2.0 * (rot.y * rot.y + rot.z * rot.z)))
            self.humanoid.base_rot = yaw + np.pi
            self.humanoid.update()
        else:
            # Simple position/rotation update for GLB or primitive
            rot = agent_state.rotation
            self.humanoid.translation = mn.Vector3(pos[0], pos[1], pos[2])
            self.humanoid.rotation = mn.Quaternion(
                mn.Vector3(rot.x, rot.y, rot.z), rot.w
            )
    
    def set_humanoid_visible(self, visible: bool):
        """Show or hide the humanoid model."""
        if self.humanoid is None:
            logger.warning("[HUMANOID] No humanoid to show/hide!")
            return
            
        if visible:
            # Make visible by restoring to agent position
            logger.info("[HUMANOID] Showing humanoid in third-person view")
            self._humanoid_hidden = False
            # Immediately update to current agent position
            agent_state = self.agent.get_state()
            pos = agent_state.position
            rot = agent_state.rotation
            
            if HABITAT_HUMANOID_AVAILABLE and isinstance(self.humanoid, KinematicHumanoid):
                logger.debug(f"[HUMANOID] Setting articulated humanoid base_pos to: {pos}")
                self.humanoid.base_pos = mn.Vector3(pos[0], pos[1], pos[2])
                # Extract yaw from quaternion and add 180° so humanoid faces away from camera
                yaw = float(np.arctan2(2.0 * (rot.w * rot.y + rot.x * rot.z), 
                                       1.0 - 2.0 * (rot.y * rot.y + rot.z * rot.z)))
                self.humanoid.base_rot = yaw + np.pi
                # Reset controller for new position
                if self.humanoid_controller:
                    self.humanoid_controller.reset(self.humanoid.base_transformation)
                logger.info(f"[HUMANOID] ✓ Articulated humanoid visible at: {self.humanoid.base_pos}")
            else:
                # GLB or primitive
                self.humanoid.translation = mn.Vector3(pos[0], pos[1], pos[2])
                self.humanoid.rotation = mn.Quaternion(
                    mn.Vector3(rot.x, rot.y, rot.z), rot.w
                )
                logger.info(f"[HUMANOID] Placed at: {pos}")
        else:
            # Hide by moving far away
            logger.info("[HUMANOID] Hiding humanoid in first-person view")
            self._humanoid_hidden = True
            if HABITAT_HUMANOID_AVAILABLE and isinstance(self.humanoid, KinematicHumanoid):
                self.humanoid.base_pos = mn.Vector3(10000, 10000, 10000)
            else:
                self.humanoid.translation = mn.Vector3(10000, 10000, 10000)
            self._humanoid_hidden = True
    
    def render_from_camera(self, camera_pos: Tuple[float, float, float], 
                           camera_pan: float, camera_tilt: float,
                           resolution: Tuple[int, int] = (320, 180)) -> Optional[np.ndarray]:
        """
        Render the scene from a security camera's viewpoint.
        
        Args:
            camera_pos: (x, y, z) position of the camera
            camera_pan: Pan angle in DEGREES (rotation around Y axis, 0 = facing +Z)
            camera_tilt: Tilt angle in DEGREES (negative = looking down)
            resolution: Output resolution (width, height)
            
        Returns:
            RGB numpy array or None if rendering fails
        """
        try:
            from scipy.spatial.transform import Rotation as R
            import math
            import numpy as np
            
            # If humanoid is hidden (first-person view), temporarily show it for camera
            humanoid_was_hidden = getattr(self, '_humanoid_hidden', False)
            original_humanoid_pos = None
            
            if humanoid_was_hidden and self.humanoid is not None:
                # Get the agent's current position to place humanoid there
                agent_state = self.agent.get_state()
                agent_pos = agent_state.position
                agent_rot = agent_state.rotation
                
                # Calculate yaw from agent rotation
                yaw = 2 * math.atan2(agent_rot.y, agent_rot.w)
                
                # Temporarily show humanoid at agent position for camera render
                if HABITAT_HUMANOID_AVAILABLE and isinstance(self.humanoid, KinematicHumanoid):
                    original_humanoid_pos = self.humanoid.base_pos
                    self.humanoid.base_pos = mn.Vector3(agent_pos[0], agent_pos[1], agent_pos[2])
                    self.humanoid.base_rot = yaw + math.pi
                    self.humanoid.update()
                    logger.debug(f"[CAMERA] Showing humanoid at {agent_pos} for camera render")
            
            # Convert degrees to radians
            pan_rad = math.radians(camera_pan)
            tilt_rad = math.radians(camera_tilt)
            
            # Compute the target forward direction
            # Pan angle: 0 = +Z, 90 = +X, -90 = -X, 180/-180 = -Z
            # Tilt: negative = looking down
            
            # Horizontal direction from pan
            fx = math.sin(pan_rad)
            fz = math.cos(pan_rad)
            
            # Vertical component from tilt
            # When tilt is negative (looking down), fy should be negative
            fy = math.sin(tilt_rad)
            
            # Scale horizontal components by cos(tilt) to maintain unit vector
            cos_tilt = math.cos(tilt_rad)
            fx *= cos_tilt
            fz *= cos_tilt
            
            # This is where we want to look
            forward_target = np.array([fx, fy, fz])
            forward_target = forward_target / np.linalg.norm(forward_target)
            
            # Habitat agent default looks at -Z, so we need rotation from (0,0,-1) to target
            rot, _ = R.align_vectors([forward_target], [[0, 0, -1]])
            quat = rot.as_quat()  # Returns [x, y, z, w]
            
            # Save current agent state
            original_state = self.agent.get_state()
            
            # Create new state at camera position with camera orientation
            camera_state = habitat_sim.agent.AgentState()
            camera_state.position = mn.Vector3(camera_pos[0], camera_pos[1], camera_pos[2])
            camera_state.rotation = quat
            
            # Temporarily move agent to camera position
            self.agent.set_state(camera_state)
            
            # Get the observation from this position
            obs = self.sim.get_sensor_observations()
            camera_rgb = obs.get("rgb", None)
            
            # Restore original agent state
            self.agent.set_state(original_state)
            
            # Restore humanoid hidden state if it was hidden before
            if humanoid_was_hidden and self.humanoid is not None and original_humanoid_pos is not None:
                if HABITAT_HUMANOID_AVAILABLE and isinstance(self.humanoid, KinematicHumanoid):
                    self.humanoid.base_pos = mn.Vector3(10000, 10000, 10000)
                    logger.debug("[CAMERA] Humanoid hidden again after camera render")
            
            if camera_rgb is not None:
                # Return RGB (remove alpha if present)
                if camera_rgb.shape[2] == 4:
                    camera_rgb = camera_rgb[:, :, :3]
                return camera_rgb
            else:
                logger.warning("[CAMERA] No RGB observation returned from camera render")
            return None
            
        except Exception as e:
            logger.error(f"[CAMERA] Render error: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def find_path_to_goal(self, goal_pos: mn.Vector3) -> Optional[habitat_sim.ShortestPath]:
        """Find shortest path to goal."""
        agent_state = self.agent.get_state()
        start_pos = agent_state.position
        
        path = habitat_sim.ShortestPath()
        path.requested_start = start_pos
        path.requested_end = goal_pos
        
        if self.sim.pathfinder.find_path(path):
            return path
        return None
    
    def get_action_to_goal(self, goal_pos: mn.Vector3) -> Tuple[Optional[str], bool]:
        """Get the next action to move toward goal using GreedyGeodesicFollower.
        
        This is the same approach Habitat uses in ShortestPathFollower.
        It uses navmesh pathfinding for proper obstacle avoidance.
        
        Returns:
            (action, goal_reached): action is None when done, goal_reached=True only if within 0.5m
        """
        if self.path_follower is None:
            print("[NAV] Path follower not initialized!")
            return (None, False)
        
        # Check distance to goal first
        agent_state = self.agent.get_state()
        current_pos = np.array(agent_state.position)
        goal = np.array(goal_pos)
        distance = np.linalg.norm(goal - current_pos)
        
        print(f"[NAV] Distance to goal: {distance:.2f}m")
        
        if distance < 0.5:
            print("[NAV] Goal reached!")
            return (None, True)
        
        try:
            # Use GreedyGeodesicFollower to get next action
            # This handles all the navmesh pathfinding automatically
            next_action = self.path_follower.next_action_along(goal_pos)
            
            if next_action == "stop" or next_action is None:
                print("[NAV] Follower says stop/None - goal unreachable")
                return (None, False)
            
            print(f"[NAV] GreedyFollower action: {next_action}")
            return (next_action, False)
            
        except GreedyFollowerError as e:
            print(f"[NAV] GreedyFollowerError: {e} - goal unreachable")
            return (None, False)
        except Exception as e:
            print(f"[NAV] Error getting action: {e}")
            return (None, False)
    
    def get_semantic_objects(self) -> Dict[str, List[Tuple[int, str]]]:
        """Get list of objects in scene with their semantic IDs."""
        objects = {}
        scene = self.sim.semantic_scene
        
        if scene is None:
            return objects
        
        for obj in scene.objects:
            if obj is None:
                continue
            category = obj.category
            if category is None:
                continue
            name = category.name().lower()
            if name not in objects:
                objects[name] = []
            objects[name].append((obj.semantic_id, name))
        
        return objects

    def get_object_positions(self) -> Dict[str, List[mn.Vector3]]:
        """Get positions of semantic objects in the scene."""
        positions = {}
        scene = self.sim.semantic_scene
        
        if scene is None:
            print("No semantic scene available")
            return positions
        
        for obj in scene.objects:
            if obj is None:
                continue
            category = obj.category
            if category is None:
                continue
            name = category.name().lower()
            
            # Get object center (AABB center)
            aabb = obj.aabb
            if aabb is not None:
                center = aabb.center()
                # Snap to navmesh if possible
                nav_point = self.sim.pathfinder.snap_point(center)
                if not np.isnan(nav_point[0]):
                    if name not in positions:
                        positions[name] = []
                    positions[name].append(mn.Vector3(nav_point))
        
        return positions
    
    def get_room_positions(self) -> Dict[str, List[mn.Vector3]]:
        """Get navigable positions in different rooms."""
        rooms = {}
        scene = self.sim.semantic_scene
        
        if scene is None:
            return rooms
        
        for region in scene.regions:
            if region is None:
                continue
            category = region.category
            if category is None:
                continue
            name = category.name().lower()
            
            # Get region center
            aabb = region.aabb
            if aabb is not None:
                center = aabb.center()
                nav_point = self.sim.pathfinder.snap_point(center)
                if not np.isnan(nav_point[0]):
                    if name not in rooms:
                        rooms[name] = []
                    rooms[name].append(mn.Vector3(nav_point))
        
        return rooms
    
    def load_hssd_semantics(self, scene_path: str) -> Tuple[Dict[str, List[mn.Vector3]], Dict[str, List[mn.Vector3]]]:
        """Load semantics from HSSD semantic config files."""
        rooms = {}
        objects = {}
        
        # Get scene ID from path
        scene_name = os.path.basename(scene_path)
        scene_id = scene_name.split('.')[0]  # e.g., "102343992"
        
        # Look for semantic config
        data_path = os.path.join(PROJECT_ROOT, "data", "scene_datasets", "hssd-hab")
        semantic_config_path = os.path.join(data_path, "semantics", "scenes", f"{scene_id}.semantic_config.json")
        
        if not os.path.exists(semantic_config_path):
            print(f"No semantic config found at {semantic_config_path}")
            return rooms, objects
        
        try:
            with open(semantic_config_path, 'r') as f:
                semantic_data = json.load(f)
        except Exception as e:
            print(f"Error loading semantic config: {e}")
            return rooms, objects
        
        # Extract room/region annotations
        if "region_annotations" in semantic_data:
            for region in semantic_data["region_annotations"]:
                name = region.get("name", "unknown").lower()
                
                # Get center of region from bounds
                min_bounds = region.get("min_bounds")
                max_bounds = region.get("max_bounds")
                
                if min_bounds and max_bounds:
                    center = [
                        (min_bounds[0] + max_bounds[0]) / 2,
                        (min_bounds[1] + max_bounds[1]) / 2,
                        (min_bounds[2] + max_bounds[2]) / 2
                    ]
                    
                    # Snap to navmesh
                    nav_point = self.sim.pathfinder.snap_point(center)
                    if not np.isnan(nav_point[0]):
                        if name not in rooms:
                            rooms[name] = []
                        rooms[name].append(mn.Vector3(nav_point))
                        print(f"  Found room: {name}")
        
        print(f"Loaded {len(rooms)} room types from semantic config")
        return rooms, objects
    
    def get_scene_objects_from_rigid_objects(self) -> Dict[str, List[mn.Vector3]]:
        """Get objects from the simulator's rigid object manager."""
        objects = {}
        
        rom = self.sim.get_rigid_object_manager()
        
        for obj_id in rom.get_object_handles():
            obj = rom.get_object_by_handle(obj_id)
            if obj is not None:
                # Get object name from handle
                handle_name = obj_id.split('/')[-1].split('.')[0].lower()
                
                # Only include recognizable furniture/appliance keywords
                recognized_name = None
                for keyword in ['chair', 'table', 'sofa', 'couch', 'bed', 'desk', 'cabinet', 
                               'refrigerator', 'fridge', 'tv', 'television', 'lamp', 
                               'toilet', 'sink', 'bathtub', 'shower', 'oven', 'microwave']:
                    if keyword in handle_name:
                        recognized_name = keyword
                        break
                
                # Skip unrecognized objects (like random asset IDs)
                if recognized_name is None:
                    continue
                
                pos = obj.translation
                nav_point = self.sim.pathfinder.snap_point(pos)
                
                if not np.isnan(nav_point[0]):
                    if recognized_name not in objects:
                        objects[recognized_name] = []
                    objects[recognized_name].append(mn.Vector3(nav_point))
        
        return objects
    
    def init_llm_client(self):
        """Initialize LLM client for task generation."""
        try:
            config = LLMConfig()
            if config.validate():
                self.llm_client = LLMClient(config)
                self.use_llm = True
                print("LLM client initialized for task generation")
            else:
                print("LLM config invalid, using random task selection")
                self.use_llm = False
        except Exception as e:
            print(f"Failed to init LLM: {e}, using random task selection")
            self.use_llm = False
    
    def init_vesper_integration(self, rooms: List[str], room_positions: Optional[Dict[str, Tuple[float, float, float]]] = None):
        """Initialize VESPER integration with IoT devices and humanoid.
        
        Args:
            rooms: List of room names detected in the scene
            room_positions: Optional dict of room name -> navigable position (x, y, z)
        """
        try:
            # Store room positions for navigation
            self.room_positions = room_positions or {}
            
            scene_id = "unknown"
            if self.sim and hasattr(self.sim, 'config') and hasattr(self.sim.config, 'scene_id'):
                scene_id = os.path.basename(self.sim.config.scene_id).split('.')[0]
            
            config = VesperConfig(
                enable_iot=True,
                enable_humanoid=True,
                enable_llm=self.use_llm,
            )
            
            self.vesper = VesperIntegration(config)
            self.vesper.scene_id = scene_id
            
            # Initialize IoT devices
            if rooms:
                self.vesper.init_iot(rooms, room_positions)
                
                # Set room positions in IoT bridge for motion detection
                if room_positions and self.vesper.iot_bridge:
                    self.vesper.iot_bridge.room_positions = room_positions
                    print(f"[VESPER] Set positions for {len(room_positions)} rooms")
                
                print(f"[VESPER] IoT initialized with {len(rooms)} rooms")
                
                # Print device summary for confirmation
                room_devices = self.vesper.iot_manager.get_room_devices_summary()
                total_devices = len(self.vesper.iot_manager.devices)
                print(f"[VESPER] Created {total_devices} IoT devices:")
                for room, devices in list(room_devices.items())[:5]:
                    print(f"  {room}: {', '.join(devices)}")
                if len(room_devices) > 5:
                    print(f"  ... and {len(room_devices) - 5} more rooms")
                
                # Print automation rules
                if self.vesper.iot_bridge:
                    automations = len(self.vesper.iot_bridge.automation_rules)
                    print(f"[VESPER] Created {automations} automation rules (motion -> lights)")
            
            # Initialize humanoid (tracks position)
            agent_state = self.agent.get_state()
            self.vesper.init_humanoid(
                sim=self.sim,
                initial_position=tuple(agent_state.position),
            )
            print("[VESPER] Humanoid controller initialized")
            
            # Share LLM client if available
            if self.use_llm and self.llm_client:
                self.vesper._llm_client = self.llm_client
            
            # Initialize sensors (1 per room with paired cameras)
            self._setup_sensors(rooms, room_positions)
            
            print(f"[VESPER] Integration initialized for scene: {scene_id}")
            
            # Initialize autonomous simulation for task scheduling
            self._init_autonomous_simulation()
            
        except Exception as e:
            print(f"[VESPER] Failed to initialize: {e}")
            import traceback
            traceback.print_exc()
            self.vesper = None
    
    def init_smartthings(self, room_positions: Dict[str, Tuple[float, float, float]]):
        """
        Initialize SmartThings ↔ 3D Bridge with Docker firmware devices.
        
        Each room gets a Docker container running real ARM firmware in QEMU.
        The SmartThings Schema Connector runs in a background thread so
        the phone app can control devices while the 3D simulation runs.
        
        Args:
            room_positions: room_name -> (x, y, z) navigable position
        """
        try:
            print("\n" + "=" * 60)
            print("  SmartThings ↔ 3D Bridge Initialization")
            print("=" * 60)
            
            self.smartthings_bridge = SmartThings3DBridge(port=SMARTTHINGS_PORT)
            self.smartthings_bridge.start(room_positions)
            
            # Wait a moment for Docker containers to spin up
            import time
            print("[ST-3D] Waiting for Docker containers to start …")
            time.sleep(5)
            
            n_devices = len(self.smartthings_bridge.firmware_devices)
            n_rooms = len(self.smartthings_bridge.room_device_map)
            ngrok_url = self.smartthings_bridge.ngrok_url
            
            print(f"\n[ST-3D] ✅ Bridge active:")
            print(f"  Firmware devices : {n_devices} Docker containers")
            print(f"  Rooms mapped     : {n_rooms}")
            print(f"  Webhook          : http://localhost:{SMARTTHINGS_PORT}/schema")
            print(f"  Proximity radius : {INTERACTION_DISTANCE}m")
            print(f"  Interaction cool.: {INTERACTION_COOLDOWN}s")
            print()
            print("  🐳 Docker Containers (QEMU ARM Firmware):")
            for dev_id, fw in self.smartthings_bridge.firmware_devices.items():
                status = "ON " if fw.is_on else "OFF"
                print(f"     {fw.container_name:30s}  port {fw.host_port}  [{status}]  {fw.room}")
            print()
            print("  Bi-directional Sync Flow:")
            print("    3D→ST : F key / proximity → handle_command → Docker → SmartThings cloud")
            print("    ST→3D : SmartThings app → cloud → webhook → handle_command → Docker → 3D")
            print()

            # Check if callback credentials are available for proactive state push
            has_creds = (
                self.smartthings_bridge.connector
                and self.smartthings_bridge.connector._callback_urls
                and self.smartthings_bridge.connector._callback_tokens
            )
            if has_creds:
                print("  ✅ Callback credentials: FOUND (proactive state push enabled)")
            else:
                print("  ⚠️  Callback credentials: NOT FOUND")
                print("     3D→ST sync requires re-linking to get callback access:")
                print("       1. Open SmartThings app on your phone")
                print("       2. Remove the VESPER integration")
                print("       3. Re-add: + > Device > My Testing Devices > VESPER Smart Home")
                print("       4. Authorize when prompted")
                print()
                print("     ⚠️  IMPORTANT: Set the App Client Secret from the Developer Portal:")
                print("       developer.smartthings.com > Your Project > App Credentials")
                print(f"       export ST_APP_CLIENT_SECRET=\"<your-app-client-secret>\"")
                print(f"       (current: {ST_APP_CLIENT_SECRET[:20]}...)")
            print()
            if ngrok_url:
                print(f"  ngrok URL        : {ngrok_url}")
                print(f"  Target URL       : {ngrok_url}/schema")
                print(f"  OAuth URL        : {ngrok_url}/oauth/authorize")
                print(f"  Token URL        : {ngrok_url}/oauth/token")
                print()
                print("  ╔══════════════════════════════════════════════════╗")
                print("  ║  SmartThings Developer Portal Setup:            ║")
                print("  ║  1. Go to developer.smartthings.com             ║")
                print("  ║  2. Open your VESPER project                    ║")
                print(f"  ║  3. Set Target URL:                             ║")
                print(f"  ║     {ngrok_url}/schema")
                print(f"  ║  4. Set OAuth Authorization URI:                ║")
                print(f"  ║     {ngrok_url}/oauth/authorize")
                print(f"  ║  5. Set OAuth Token URI:                        ║")
                print(f"  ║     {ngrok_url}/oauth/token")
                print("  ║  6. In SmartThings app: + > Device >             ║")
                print("  ║     My Testing Devices > VESPER Smart Home       ║")
                print("  ╚══════════════════════════════════════════════════╝")
            else:
                print("  ⚠️  ngrok NOT detected — start it for phone access:")
                print(f"    ngrok http {SMARTTHINGS_PORT}")
                print("  Then re-run this script so it can detect the URL.")
            print("=" * 60 + "\n")
            
        except Exception as e:
            print(f"[ST-3D] ❌ Failed to initialize SmartThings bridge: {e}")
            import traceback
            traceback.print_exc()
            self.smartthings_bridge = None

    def init_articulated_devices(self, room_positions: Dict[str, Tuple[float, float, float]]):
        """
        Initialize the articulated device bridge — discovers 3D interactive
        objects (fridges, cabinets, drawers, etc.) in the loaded scene and
        links them to IoT device states.
        """
        try:
            self.articulated_bridge = ArticulatedDeviceBridge(self.sim)
            count = self.articulated_bridge.discover_devices(room_positions)
            if count > 0:
                print(f"[ART-BRIDGE] ✅ {count} articulated 3D devices ready for interaction")
                stats = self.articulated_bridge.get_stats()
                for dtype, cnt in stats["by_type"].items():
                    print(f"  {dtype:15s}  ×{cnt}")
            else:
                print("[ART-BRIDGE] No interactive articulated objects found in this scene")
                self.articulated_bridge = None
        except Exception as e:
            logger.warning(f"[ART-BRIDGE] Failed to init articulated devices: {e}")
            self.articulated_bridge = None

    def _init_autonomous_simulation(self):
        """Initialize autonomous simulation for daily task scheduling."""
        try:
            from datetime import datetime
            
            # Get available rooms from the house layout
            available_rooms = []
            if hasattr(self, 'vesper') and self.vesper and hasattr(self.vesper, 'iot_bridge'):
                if hasattr(self.vesper.iot_bridge, 'room_positions'):
                    available_rooms = list(self.vesper.iot_bridge.room_positions.keys())
            
            if available_rooms:
                print(f"[VESPER] House layout rooms: {', '.join(available_rooms)}")
            else:
                print(f"[VESPER] Warning: No rooms detected, using default layout")
            
            # Create persona for the humanoid
            persona = HumanoidPersona(
                name="Alex",
                age=30,
                occupation="Remote Worker",
                works_from_home=True,
                wake_time="07:00",
                sleep_time="23:00",
                work_start="09:00",
                work_end="17:00",
                exercise_frequency=0.5,
            )
            
            # Create autonomous simulation with house layout
            self.autonomous_sim = AutonomousSimulation(
                persona=persona,
                time_scale=60.0,  # 60x: 1 real second = 1 simulated minute
                use_llm=self.use_llm,
                available_rooms=available_rooms,
            )
            
            # Use current time of day for realistic task scheduling
            start_time = datetime.now()
            self.autonomous_sim.time_manager._simulation_time = start_time
            self.time_manager._simulation_time = start_time
            
            # Generate daily schedule starting from current time
            self.autonomous_sim.start_new_day(date=start_time)
            
            print(f"[VESPER] Autonomous simulation initialized")
            print(f"[VESPER] Daily schedule: {len(self.autonomous_sim.current_schedule.tasks)} tasks")
            print(f"[VESPER] Time: {start_time.strftime('%H:%M')} (60x speed)")
            
        except Exception as e:
            print(f"[VESPER] Autonomous simulation failed: {e}")
            import traceback
            traceback.print_exc()
            self.autonomous_sim = None
    
    def update_simulation_time(self) -> dict:
        """
        Update simulation time and check for scheduled tasks.
        Also navigates agent to current task location.
        
        Returns:
            dict with current time info and any task updates
        """
        result = {
            "current_time": None,
            "current_task": None,
            "task_started": None,
            "task_completed": None,
            "navigating": False,
        }
        
        if not self.autonomous_sim:
            return result
        
        # Store previous task to detect changes
        prev_task = self.autonomous_sim.current_task
        prev_task_id = prev_task.task_id if prev_task else None
        
        # Update time
        self.time_manager.update()
        self.autonomous_sim.time_manager._simulation_time = self.time_manager.current_time
        
        result["current_time"] = self.time_manager.current_time
        
        # Check for task updates
        day_complete = self.autonomous_sim.update()
        
        current_task = self.autonomous_sim.current_task
        if current_task:
            result["current_task"] = current_task
            
            # Check if task changed - need to navigate to new location
            if current_task.task_id != prev_task_id:
                result["task_started"] = current_task
                target_room = current_task.location.room_name
                print(f"\n[NAV] New task: {current_task.name} in {target_room}")
                
                # Set navigation goal
                self._navigate_to_room(target_room)
                result["navigating"] = True
        
        # If day is complete, start new day
        if day_complete:
            from datetime import timedelta
            next_day = self.time_manager.current_time + timedelta(days=1)
            next_day = next_day.replace(hour=7, minute=0, second=0, microsecond=0)
            self.time_manager._simulation_time = next_day
            self.autonomous_sim.start_new_day(date=next_day)
            print(f"[VESPER] New day started: {next_day.strftime('%A, %B %d, %Y')}")
        
        return result
    
    def _navigate_to_room(self, room_name: str):
        """Navigate agent to specified room."""
        # Normalize room name
        room_name_lower = room_name.lower()
        
        # Try to find room position using stored room_positions
        room_pos = None
        positions = getattr(self, 'room_positions', {})
        
        if not positions:
            print(f"[NAV] No room positions available")
            return
        
        # Try exact match first
        if room_name_lower in positions:
            room_pos = positions[room_name_lower]
        else:
            # Try partial match
            for rname, pos in positions.items():
                if room_name_lower in rname.lower() or rname.lower() in room_name_lower:
                    room_pos = pos
                    print(f"[NAV] Matched '{room_name}' to '{rname}'")
                    break
        
        if room_pos is None:
            print(f"[NAV] Cannot find position for room: '{room_name}'")
            print(f"[NAV] Available rooms: {', '.join(positions.keys())}")
            return
        
        # Convert to Vector3 and snap to navmesh to ensure navigability
        candidate_goal = mn.Vector3(room_pos[0], room_pos[1], room_pos[2])
        
        # Snap to navmesh - critical for GreedyGeodesicFollower to work
        if self.sim and self.sim.pathfinder.is_loaded:
            snapped_goal = self.sim.pathfinder.snap_point(candidate_goal)
            print(f"[NAV] Goal snapped from {candidate_goal} to {snapped_goal}")
            self.current_goal = snapped_goal
        else:
            print(f"[NAV] Warning: pathfinder not available, using unsnapped goal")
            self.current_goal = candidate_goal
        
        self.current_goal_name = room_name
        self.auto_navigating = True
        
        print(f"[NAV] Navigating to {room_name} at {self.current_goal}")
    
    def _setup_sensors(self, rooms: List[str], room_positions: Optional[Dict[str, Tuple[float, float, float]]] = None):
        """
        Setup motion sensors and cameras in the scene using scene-aware placement.
        Uses the SceneDevicePlacer for optimal positioning based on room layout.
        
        Args:
            rooms: List of room names
            room_positions: Dict of room name -> (x, y, z) position
        """
        if not room_positions:
            logger.warning("[SENSORS] No room positions available")
            return
        
        logger.info("[SENSORS] Setting up motion sensors and cameras with scene-aware placement...")
        
        # Import the scene-aware device placer
        from vesper.devices import (
            SceneDevicePlacer,
            get_layout_for_scene,
            get_coverage_requirement,
        )
        
        # Detect scene type and get appropriate layout config
        scene_path = getattr(self, '_current_scene_path', '')
        layout = get_layout_for_scene(scene_path)
        
        logger.info(f"[SENSORS] Using layout config for scene type: {layout.scene_type.value}")
        
        # Create scene device placer
        placer = SceneDevicePlacer(scene_type=layout.scene_type)
        
        # Add rooms with position data
        placer.add_rooms_from_positions(
            room_positions,
            room_size_estimate=layout.typical_room_size,
        )
        
        # Compute optimal placements
        placements = placer.compute_device_placements()
        
        # Create the actual devices
        # Note: We use the habitat sensors for simulation, not the core devices
        # The placer computes positions/orientations, we create habitat sensors
        
        self.motion_sensors = []
        self.security_cameras = []
        self.room_sensor_state: Dict[str, Dict] = {}
        
        for placement in placements:
            room = placement.room_name
            pos = placement.position
            pan, tilt = placement.orientation
            room_id = room.replace(' ', '_').replace('.', '_')
            
            if placement.device_type == "motion_sensor":
                # Create PIR motion sensor with computed orientation
                motion_config = MotionSensorConfig(
                    device_id=placement.device_id,
                    position=pos,
                    room=room,
                    detection_range=layout.motion_sensor_range,
                    detection_angle=layout.motion_sensor_fov,
                    orientation=pan,  # Pan angle (horizontal)
                    tilt=tilt,        # Tilt angle (vertical)
                    sensitivity=SensitivityLevel.HIGH,
                    cooldown=2.0,
                )
                motion_sensor = PIRMotionSensor(motion_config)
                self.motion_sensors.append(motion_sensor)
                
                # Initialize room state if needed
                if room not in self.room_sensor_state:
                    self.room_sensor_state[room] = {
                        "motion_sensor": None,
                        "camera": None,
                        "last_detection": None,
                        "is_detecting": False,
                        "tracking_target": None,
                    }
                self.room_sensor_state[room]["motion_sensor"] = motion_sensor
                
            elif placement.device_type == "security_camera":
                # Create security camera with computed orientation
                # The orientation is computed to point toward room center
                # NOTE: CameraConfig from habitat.sensors expects DEGREES, not radians!
                import math as m
                pan_deg = m.degrees(pan)
                tilt_deg = m.degrees(tilt)
                
                # Ensure tilt looks down at the floor (should be -30 to -45 degrees typically)
                # If tilt is too shallow, use a better default
                if tilt_deg > -20:
                    tilt_deg = -35.0  # Force good downward angle
                
                camera_config = CameraConfig(
                    device_id=placement.device_id,
                    position=pos,
                    room=room,
                    horizontal_fov=layout.camera_horizontal_fov,
                    vertical_fov=layout.camera_vertical_fov,
                    pan=pan_deg,   # DEGREES - computed to face room center
                    tilt=tilt_deg, # DEGREES - computed for floor visibility
                    max_range=15.0,
                    pan_speed=45.0,  # degrees/sec
                    tilt_speed=30.0, # degrees/sec
                )
                camera = SecurityCamera(camera_config)
                self.security_cameras.append(camera)
                
                # Debug: log camera orientation (final values in degrees)
                logger.debug(f"[CAMERA] {room}: pan={pan_deg:.1f}°, tilt={tilt_deg:.1f}°")
                
                # Initialize room state if needed
                if room not in self.room_sensor_state:
                    self.room_sensor_state[room] = {
                        "motion_sensor": None,
                        "camera": None,
                        "last_detection": None,
                        "is_detecting": False,
                        "tracking_target": None,
                    }
                self.room_sensor_state[room]["camera"] = camera
        
        # Get placement summary
        summary = placer.get_placement_summary()
        logger.info(f"[SENSORS] Created {summary['cameras']} cameras and {summary['motion_sensors']} motion sensors")
        logger.info(f"[SENSORS] Covering {len(summary['rooms_covered'])} rooms: {', '.join(summary['rooms_covered'][:5])}" + 
              (f" (+{len(summary['rooms_covered'])-5} more)" if len(summary['rooms_covered']) > 5 else ""))
        
        # Create sensor bridge to connect 3D sensors to firmware simulation
        logger.info("[BRIDGE] Creating 3D-to-firmware sensor bridge...")
        # Create event bus for sensor events if not already available
        from vesper.core.event_bus import EventBus
        if not hasattr(self, 'sensor_event_bus'):
            self.sensor_event_bus = EventBus()
        
        self.sensor_bridge = create_sensor_bridge_for_scene(
            motion_sensors_3d=self.motion_sensors,
            cameras_3d=self.security_cameras,
            room_sensor_state=self.room_sensor_state,
            event_bus=self.sensor_event_bus,
            config=SensorBridgeConfig(
                enable_motion_sensors=True,
                enable_cameras=True,
                enable_environmental=True,
                occupancy_temp_increase=1.5,
                occupancy_humidity_increase=5.0,
            ),
        )
        
        # Start the firmware sensor network (async)
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        if not loop.is_running():
            # If no loop is running, use run_until_complete
            loop.run_until_complete(self.sensor_bridge.start())
        else:
            # If loop is already running, create task
            asyncio.create_task(self.sensor_bridge.start())
        
        bridge_stats = self.sensor_bridge.get_sensor_stats()
        print(f"[BRIDGE] Firmware sensors active: {bridge_stats['total_firmware_sensors']}")
        print(f"[BRIDGE] Rooms with environmental sensors: {bridge_stats['rooms_with_sensors']}")
    
    def update_sensors(self, humanoid_position: Tuple[float, float, float], dt: float = 0.016) -> List[Dict]:
        """
        Update all motion sensors and cameras with humanoid position.
        Returns list of new detections for UI feedback.
        
        Args:
            humanoid_position: (x, y, z) position of humanoid
            dt: Time delta in seconds
            
        Returns:
            List of detection events with room, sensor info
        """
        if not hasattr(self, 'room_sensor_state'):
            return []
        
        new_detections = []
        # Motion sensor expects Dict[str, Tuple[float, float, float]] - just positions
        targets = {"humanoid": humanoid_position}
        camera_targets = {"humanoid": humanoid_position}
        
        import time as time_module
        current_time = time_module.time()
        
        for room, state in self.room_sensor_state.items():
            motion_sensor = state["motion_sensor"]
            camera = state["camera"]
            
            # Skip if no motion sensor for this room
            if motion_sensor is None:
                continue
            
            # Update motion sensor - returns List[DetectionEvent]
            detection_events = motion_sensor.update(targets, current_time)
            was_detecting = state["is_detecting"]
            is_detecting = len(detection_events) > 0
            state["is_detecting"] = is_detecting
            
            # New detection event
            if is_detecting and not was_detecting:
                detection = detection_events[0]  # Get first detection
                state["last_detection"] = detection
                camera_id = camera.config.device_id if camera else "no_camera"
                new_detections.append({
                    "room": room,
                    "sensor_id": motion_sensor.config.device_id,
                    "camera_id": camera_id,
                    "target_position": detection.target_position,
                    "distance": detection.distance,
                })
                print(f"[SENSOR] 🔴 Motion detected in {room}! (distance: {detection.distance:.1f}m)")
                
                # Trigger firmware sensor via bridge
                if self.sensor_bridge:
                    pos_array = np.array([
                        detection.target_position[0],
                        detection.target_position[1],
                        detection.target_position[2],
                    ])
                    self.sensor_bridge.on_3d_motion_detected(
                        motion_sensor.config.device_id,
                        pos_array,
                        room,
                    )
            
            # Motion cleared
            elif not is_detecting and was_detecting:
                if self.sensor_bridge:
                    self.sensor_bridge.on_3d_motion_cleared(
                        motion_sensor.config.device_id,
                        room,
                    )
            
            # Update camera - track humanoid if motion detected (only if camera exists)
            if camera is not None:
                if is_detecting:
                    # Camera tracks the humanoid
                    camera_frame = camera.update(camera_targets, dt)
                    state["tracking_target"] = humanoid_position
                    
                    if camera_frame.targets_in_view:
                        if not was_detecting:
                            logger.info(f"[CAMERA] 📹 {room} camera tracking humanoid")
                else:
                    # Camera returns to default position when no motion
                    camera.update({}, dt)
                    state["tracking_target"] = None
        
        return new_detections
    
    def get_sensor_status(self) -> Dict[str, Dict]:
        """Get current status of all sensors for UI display."""
        if not hasattr(self, 'room_sensor_state'):
            return {}
        
        status = {}
        for room, state in self.room_sensor_state.items():
            motion_sensor = state["motion_sensor"]
            camera = state["camera"]
            
            status[room] = {
                "motion_detected": state["is_detecting"],
                "camera_tracking": state["tracking_target"] is not None,
                "sensor_id": motion_sensor.config.device_id if motion_sensor else None,
                "camera_id": camera.config.device_id if camera else None,
                "has_camera": camera is not None,
            }
        return status
    
    def get_room_devices_from_vesper(self) -> Dict[str, List[str]]:
        """Get room device summary from VESPER IoT manager."""
        if self.vesper and self.vesper.iot_manager:
            return self.vesper.iot_manager.get_room_devices_summary()
        return {}
    
    def print_iot_status(self):
        """Print current IoT device status."""
        if not self.vesper or not self.vesper.iot_manager:
            print("[VESPER] IoT not initialized")
            return
        
        print("\n=== VESPER IoT Device Status ===")
        for device_id, device in self.vesper.iot_manager.devices.items():
            state_icon = "🟢" if device.state == "on" else "⚪"
            print(f"  {state_icon} {device_id}: {device.device_type} in {device.room} [{device.state}]")
        print(f"Total: {len(self.vesper.iot_manager.devices)} devices")
        print("================================\n")
    
    def generate_llm_task(self, available_rooms: List[str], room_devices: Optional[Dict[str, List[str]]] = None) -> Tuple[str, str]:
        """Generate a navigation task using LLM with scene context.
        
        Args:
            available_rooms: List of room names in the current scene
            room_devices: Optional dict of room -> list of devices in that room
        
        Returns:
            (task_description, target_room): e.g. ("Go check the kitchen", "kitchen")
        """
        if not self.use_llm or not self.llm_client:
            # Fallback to random selection
            target = random.choice(available_rooms)
            task = f"Navigate to the {target}"
            return task, target
        
        # Build context about the house
        scene_id = "unknown"
        if self.sim and hasattr(self.sim, 'config') and hasattr(self.sim.config, 'scene_id'):
            scene_id = os.path.basename(self.sim.config.scene_id).split('.')[0]
        
        # Prepare room list with devices if available
        if room_devices:
            room_info = []
            for room in available_rooms:
                devices = room_devices.get(room, [])
                if devices:
                    device_str = ", ".join(devices)
                    room_info.append(f"{room} (devices: {device_str})")
                else:
                    room_info.append(room)
            rooms_description = "\n".join([f"- {info}" for info in room_info])
        else:
            rooms_description = "\n".join([f"- {room}" for room in available_rooms])
        
        # Prepare prompt for LLM with scene context
        prompt = f"""You are a smart home assistant helping a user navigate their home.

Scene: {scene_id}
Available rooms and devices:
{rooms_description}

Generate ONE realistic navigation command that a user might give you. Examples:
- "Go to the toilet"
- "Check the kitchen"
- "Navigate to the bedroom" 
- "Head to the living room"
- "Check if the motion sensor in the bathroom is working"
- "Go see the smart lights in the bedroom"

Choose ONE room from the available list and create a natural, conversational command.

Respond ONLY with JSON in this exact format:
{{"command": "your command here", "target": "room_name"}}

The target MUST be exactly one of: {", ".join(available_rooms)}"""

        try:
            # Create proper LLMMessage object (it's a dataclass)
            message = LLMMessage(role="user", content=prompt)
            
            print(f"[LLM] Sending request to LLM...")
            response = self.llm_client.chat([message], temperature=0.8, max_tokens=100)
            print(f"[LLM] Raw response: {response.content}")
            
            # Parse JSON from response content
            import re
            json_match = re.search(r'\{[^}]+\}', response.content)
            if json_match:
                json_str = json_match.group()
                print(f"[LLM] Extracted JSON: {json_str}")
                result = json.loads(json_str)
                command = result.get("command", "Navigate to location")
                target = result.get("target", available_rooms[0])
                
                print(f"[LLM] Parsed - command: '{command}', target: '{target}'")
                
                # Validate target is in available rooms
                if target not in available_rooms:
                    print(f"[LLM] Target '{target}' not in available rooms, finding closest match...")
                    # Try to find closest match
                    for room in available_rooms:
                        if room in target.lower() or target.lower() in room:
                            print(f"[LLM] Matched '{target}' to '{room}'")
                            target = room
                            break
                    else:
                        print(f"[LLM] No match found, using first room: {available_rooms[0]}")
                        target = available_rooms[0]
                
                print(f"[LLM Task] '{command}' -> {target}")
                return command, target
            else:
                print(f"[LLM] No JSON found in response")
                raise ValueError("No JSON in response")
                
        except Exception as e:
            print(f"LLM task generation failed: {e}, using fallback")
            target = random.choice(available_rooms)
            task = f"Navigate to the {target}"
            return task, target


class GameUI:
    """Pygame UI for ObjectNav demo. Supports headless mode (no display)."""
    
    def __init__(self, nav_demo: ObjectNavDemo, headless: bool = False):
        self.nav_demo = nav_demo
        self.headless = headless
        self.screen = None
        self.clock = None if headless else (pygame.time.Clock() if HAS_PYGAME else None)
        self.font = None
        self.small_font = None
        
        # UI State
        self.show_help = True
        self.show_map = False
        self.auto_navigate = False
        self.goal_name = None
        self.goal_pos = None
        self.status_message = "Press T to set room goal, N to auto-navigate"
        self.available_targets = []
        self.third_person = False  # Start in first-person view
        self.show_iot_panel = False  # Toggle IoT device panel
        self.show_camera_view = False  # Toggle camera view (K key)
        self.current_camera_index = 0  # Which camera to view
        self.show_smartthings_panel = False  # Toggle SmartThings panel (P key)
        self.st_proximity_msg = ""  # Proximity status message
        self.st_proximity_timer = 0.0
        
    def init_pygame(self):
        if self.headless:
            logger.info("Headless mode — Pygame display DISABLED")
            return
        if not HAS_PYGAME:
            logger.warning("Pygame not installed — running headless")
            self.headless = True
            return
        pygame.init()
        pygame.display.set_caption("VESPER ObjectNav - Navigate to Rooms")
        self.screen = pygame.display.set_mode(RESOLUTION)
        self.font = pygame.font.Font(None, 36)
        self.small_font = pygame.font.Font(None, 24)
        self.clock = pygame.time.Clock()
        
    def render_frame(self, observations: dict):
        """Render the current frame (no-op when headless)."""
        if self.headless:
            return
        # Get RGB observation (first or third person)
        if self.third_person and "third_rgb" in observations:
            rgb = observations["third_rgb"]
        else:
            rgb = observations["rgb"]
        
        # Convert to pygame surface
        # RGB is (H, W, 4) with RGBA
        rgb = rgb[:, :, :3]  # Remove alpha
        rgb = np.ascontiguousarray(rgb)
        surface = pygame.surfarray.make_surface(rgb.swapaxes(0, 1))
        
        # Blit to screen
        self.screen.blit(surface, (0, 0))
        
        # Draw camera view if active
        if hasattr(self, 'show_camera_view') and self.show_camera_view:
            self._draw_camera_view()
        
        # Draw UI overlay
        self._draw_overlay()
        
        pygame.display.flip()
        self.clock.tick(60)
    
    def _draw_camera_view(self):
        """Draw actual camera feed by rendering from camera's viewpoint."""
        if not hasattr(self.nav_demo, 'room_sensor_state') or not self.nav_demo.room_sensor_state:
            return
        
        # Get list of cameras - filter out rooms without cameras (bathrooms, closets)
        cameras = [(room, state["camera"]) for room, state in self.nav_demo.room_sensor_state.items() 
                   if state["camera"] is not None]
        if not cameras:
            return
        
        # Wrap camera index
        self.current_camera_index = self.current_camera_index % len(cameras)
        room_name, camera = cameras[self.current_camera_index]
        
        # Camera view panel dimensions (top-right corner)
        panel_width = 400
        panel_height = 260
        view_width = panel_width - 20
        view_height = panel_height - 60
        panel_x = RESOLUTION[0] - panel_width - 10
        panel_y = 90  # Below the header
        
        # Camera status
        is_tracking = self.nav_demo.room_sensor_state[room_name].get("tracking_target") is not None
        status_color = (0, 255, 0) if is_tracking else (150, 150, 150)
        status_text = "● LIVE" if is_tracking else "IDLE"
        
        # Get camera parameters
        import math
        camera_pos = camera.position
        camera_pan = camera.current_pan
        camera_tilt = camera.current_tilt
        
        # Create the panel
        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((20, 20, 30, 250))
        
        # Render from camera viewpoint using our helper method
        camera_rgb = self.nav_demo.render_from_camera(
            camera_pos, camera_pan, camera_tilt,
            resolution=(view_width, view_height)
        )
        
        if camera_rgb is not None:
            # Convert to pygame surface and display
            cam_rgb = np.ascontiguousarray(camera_rgb)
            cam_surface = pygame.surfarray.make_surface(cam_rgb.swapaxes(0, 1))
            # Scale to fit the view area
            cam_surface = pygame.transform.scale(cam_surface, (view_width, view_height))
            panel.blit(cam_surface, (10, 45))
        else:
            # Fallback - show "No Feed" message
            pygame.draw.rect(panel, (30, 30, 50), (10, 45, view_width, view_height))
            no_feed = self.small_font.render("No Camera Feed", True, (100, 100, 100))
            panel.blit(no_feed, (panel_width // 2 - 50, panel_height // 2))
        
        # Header background
        pygame.draw.rect(panel, (30, 30, 40), (0, 0, panel_width, 42))
        
        # Border
        border_color = (0, 255, 0) if is_tracking else (80, 80, 80)
        pygame.draw.rect(panel, border_color, (0, 0, panel_width, panel_height), 2)
        
        # Header text
        header = self.small_font.render(f"📹 Camera: {room_name}", True, (255, 255, 255))
        panel.blit(header, (10, 8))
        
        # Camera number
        cam_num = f"[{self.current_camera_index + 1}/{len(cameras)}]"
        cam_num_render = self.small_font.render(cam_num, True, (150, 150, 150))
        panel.blit(cam_num_render, (10, 25))
        
        # Status indicator
        status_render = self.small_font.render(status_text, True, status_color)
        panel.blit(status_render, (panel_width - 70, 8))
        
        # Pan/Tilt info (already in degrees from habitat SecurityCamera)
        pan_deg = camera_pan  # Already degrees
        tilt_deg = camera_tilt  # Already degrees
        angle_text = f"Pan:{pan_deg:.0f}° Tilt:{tilt_deg:.0f}°"
        angle_render = self.small_font.render(angle_text, True, (120, 120, 120))
        panel.blit(angle_render, (panel_width - 130, 25))
        
        # Navigation hint at bottom
        nav_hint = self.small_font.render("[ ] switch cameras | K close", True, (100, 100, 100))
        panel.blit(nav_hint, (panel_width // 2 - 80, panel_height - 15))
        
        self.screen.blit(panel, (panel_x, panel_y))
    
    def _draw_humanoid_marker(self):
        """Draw a humanoid marker in third-person view."""
        # Get agent position and draw a marker at screen center-bottom
        # This provides visual feedback that we're in third-person mode
        
        # Draw a simple humanoid silhouette at the bottom center
        center_x = RESOLUTION[0] // 2
        bottom_y = RESOLUTION[1] - 50
        
        # Body (rectangle)
        body_color = (0, 200, 255)
        pygame.draw.rect(self.screen, body_color, (center_x - 15, bottom_y - 60, 30, 40), border_radius=5)
        
        # Head (circle)
        pygame.draw.circle(self.screen, body_color, (center_x, bottom_y - 75), 15)
        
        # Legs
        pygame.draw.rect(self.screen, body_color, (center_x - 12, bottom_y - 20, 10, 25))
        pygame.draw.rect(self.screen, body_color, (center_x + 2, bottom_y - 20, 10, 25))
        
        # Label
        label = self.small_font.render("Humanoid (3rd Person)", True, (0, 200, 255))
        label_rect = label.get_rect(center=(center_x, bottom_y + 10))
        self.screen.blit(label, label_rect)
    
    def _draw_overlay(self):
        """Draw UI overlay."""
        # Semi-transparent panel at top
        panel = pygame.Surface((RESOLUTION[0], 80), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 150))
        self.screen.blit(panel, (0, 0))
        
        # Title
        title = self.font.render("VESPER ObjectNav + LLM", True, (0, 255, 255))
        self.screen.blit(title, (20, 10))
        
        # View mode indicator
        view_mode = "Third-person view" if self.third_person else "First-person view"
        view_text = self.small_font.render(view_mode, True, (255, 255, 255))
        self.screen.blit(view_text, (20, 35))
        
        # Status message
        status = self.small_font.render(self.status_message, True, (200, 200, 200))
        self.screen.blit(status, (20, 55))
        
        # ===== SIMULATION CLOCK (Center Top) =====
        self._draw_simulation_clock()
        
        # ===== CURRENT SCHEDULED TASK (Below clock) =====
        self._draw_current_task()
        
        # Goal indicator
        if self.goal_name:
            goal_text = self.font.render(f"Goal: {self.goal_name}", True, (0, 255, 0))
            self.screen.blit(goal_text, (RESOLUTION[0] - 300, 10))
        
        # Auto-nav indicator
        if self.auto_navigate:
            auto_text = self.small_font.render("[AUTO-NAV ON]", True, (255, 200, 0))
            self.screen.blit(auto_text, (RESOLUTION[0] - 300, 35))
        
        # Help panel
        if self.show_help:
            self._draw_help()
        
        # IoT device panel
        if self.show_iot_panel:
            self._draw_iot_panel()
        
        # SmartThings panel
        if self.show_smartthings_panel:
            self._draw_smartthings_panel()
        
        # Proximity indicator (always shown when SmartThings bridge is active)
        if self.nav_demo.smartthings_bridge:
            self._draw_proximity_indicator()
        
        # Config menu (render on top)
        if self.nav_demo.vesper and self.nav_demo.vesper.config_menu:
            config_menu = self.nav_demo.vesper.config_menu
            if config_menu.is_visible:
                config_menu.render(self.screen, RESOLUTION[0], RESOLUTION[1])
    
    def _draw_simulation_clock(self):
        """Draw simulation clock in top center."""
        if not hasattr(self.nav_demo, 'time_manager') or not self.nav_demo.time_manager:
            return
        
        current_time = self.nav_demo.time_manager.current_time
        
        # Clock panel (center top)
        clock_width = 200
        clock_height = 60
        clock_x = (RESOLUTION[0] - clock_width) // 2
        clock_y = 5
        
        clock_panel = pygame.Surface((clock_width, clock_height), pygame.SRCALPHA)
        clock_panel.fill((0, 50, 100, 220))
        
        # Draw border
        pygame.draw.rect(clock_panel, (0, 200, 255), (0, 0, clock_width, clock_height), 2, border_radius=5)
        
        # Time display (large)
        time_str = current_time.strftime("%H:%M:%S")
        time_font = pygame.font.Font(None, 42)
        time_text = time_font.render(time_str, True, (0, 255, 200))
        time_rect = time_text.get_rect(center=(clock_width // 2, 22))
        clock_panel.blit(time_text, time_rect)
        
        # Date display (small)
        date_str = current_time.strftime("%A, %b %d")
        date_text = self.small_font.render(date_str, True, (150, 200, 255))
        date_rect = date_text.get_rect(center=(clock_width // 2, 45))
        clock_panel.blit(date_text, date_rect)
        
        # Speed indicator
        speed_text = self.small_font.render("60x", True, (255, 200, 0))
        clock_panel.blit(speed_text, (clock_width - 35, 5))
        
        self.screen.blit(clock_panel, (clock_x, clock_y))
    
    def _draw_current_task(self):
        """Draw current scheduled task panel below the clock."""
        if not hasattr(self.nav_demo, 'autonomous_sim') or not self.nav_demo.autonomous_sim:
            return
        
        sim = self.nav_demo.autonomous_sim
        current_task = sim.current_task
        
        if not current_task:
            return
        
        # Task panel (center, below clock)
        panel_width = 400
        panel_height = 70
        panel_x = (RESOLUTION[0] - panel_width) // 2
        panel_y = 70
        
        task_panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        task_panel.fill((50, 30, 0, 220))
        pygame.draw.rect(task_panel, (255, 150, 0), (0, 0, panel_width, panel_height), 2, border_radius=5)
        
        # Task icon and name
        icon = "🎯"
        task_name = current_task.name
        task_text = self.font.render(f"{icon} {task_name}", True, (255, 200, 100))
        task_panel.blit(task_text, (10, 8))
        
        # Room and progress
        room = current_task.location.room_name if current_task.location else "unknown"
        duration_min = int(current_task.duration.total_seconds() / 60)
        progress = getattr(current_task, 'progress', 0.0) * 100
        
        details = f"📍 {room} | ⏱ {duration_min} min | {progress:.0f}%"
        details_text = self.small_font.render(details, True, (200, 180, 100))
        task_panel.blit(details_text, (10, 40))
        
        # Progress bar
        bar_width = panel_width - 20
        bar_height = 8
        bar_x = 10
        bar_y = panel_height - 12
        
        # Background
        pygame.draw.rect(task_panel, (80, 60, 0), (bar_x, bar_y, bar_width, bar_height), border_radius=3)
        # Progress
        progress_width = int(bar_width * (progress / 100))
        if progress_width > 0:
            pygame.draw.rect(task_panel, (255, 180, 0), (bar_x, bar_y, progress_width, bar_height), border_radius=3)
        
        self.screen.blit(task_panel, (panel_x, panel_y))
    
    def _draw_iot_panel(self):
        """Draw IoT device status panel with live states and events."""
        if not self.nav_demo.vesper:
            return
        
        vesper = self.nav_demo.vesper
        manager = vesper.iot_manager
        bridge = vesper.iot_bridge
        
        if not manager and not bridge:
            return
        
        # Calculate panel size - larger for events
        panel_height = 550
        panel_width = 340
        
        iot_panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        iot_panel.fill((0, 0, 50, 230))
        
        # Title with stats
        title = self.font.render("IoT Devices", True, (0, 255, 255))
        iot_panel.blit(title, (10, 10))
        
        # Stats from bridge
        y = 40
        if bridge:
            stats = bridge.stats
            stats_text = f"Motion: {stats['motion_events']} | Auto: {stats['automation_triggers']}"
            stats_render = self.small_font.render(stats_text, True, (100, 200, 255))
            iot_panel.blit(stats_render, (10, y))
            y += 20
            
            if stats.get('current_room'):
                room_text = self.small_font.render(f"Current room: {stats['current_room']}", True, (255, 200, 0))
                iot_panel.blit(room_text, (10, y))
                y += 20
        
        # Separator
        pygame.draw.line(iot_panel, (100, 100, 100), (10, y), (panel_width - 10, y), 1)
        y += 10
        
        # Room-by-room device list with live states from bridge
        devices_to_show = bridge.devices if bridge else (manager.devices if manager else {})
        rooms_shown = {}
        
        for device_id, device in devices_to_show.items():
            if hasattr(device, 'room'):
                room = device.room
            else:
                room = device_id.rsplit('_', 1)[0]
            
            if room not in rooms_shown:
                rooms_shown[room] = []
            rooms_shown[room].append(device)
        
        for room, devices in list(rooms_shown.items())[:5]:
            # Room name
            room_text = self.small_font.render(f"[{room.title()}]", True, (255, 200, 0))
            iot_panel.blit(room_text, (10, y))
            y += 20
            
            # Devices in this room
            for device in devices[:4]:
                if hasattr(device, 'device_type'):
                    dev_type = device.device_type.replace('_', ' ')
                    state = device.state if hasattr(device, 'state') else "off"
                    is_triggered = getattr(device, 'is_triggered', False)
                else:
                    dev_type = "device"
                    state = "off"
                    is_triggered = False
                
                # Color based on state
                if is_triggered or state in ["on", "triggered"]:
                    state_color = (0, 255, 0)
                    state_icon = "*"
                else:
                    state_color = (120, 120, 120)
                    state_icon = "o"
                
                dev_text = self.small_font.render(f"  {state_icon} {dev_type}", True, state_color)
                iot_panel.blit(dev_text, (15, y))
                y += 18
            
            y += 5
            if y > panel_height - 150:
                break
        
        # Recent events section
        pygame.draw.line(iot_panel, (100, 100, 100), (10, y), (panel_width - 10, y), 1)
        y += 5
        
        events_title = self.small_font.render("Recent Events:", True, (255, 150, 0))
        iot_panel.blit(events_title, (10, y))
        y += 20
        
        if bridge:
            recent_events = bridge.get_recent_events(5)
            for event in reversed(recent_events):
                event_type = event.get('type', 'unknown')
                room = event.get('room', '')
                
                # Format event
                if event_type == "motion_detected":
                    event_text = f"! Motion in {room}"
                    color = (255, 100, 100)
                elif event_type == "automation_triggered":
                    rule = event.get('data', {}).get('rule', 'unknown')
                    event_text = f"# {rule}"
                    color = (100, 255, 100)
                elif event_type == "room_enter":
                    event_text = f"> Entered {room}"
                    color = (100, 200, 255)
                else:
                    event_text = f"{event_type} in {room}"
                    color = (180, 180, 180)
                
                event_render = self.small_font.render(event_text[:35], True, color)
                iot_panel.blit(event_render, (15, y))
                y += 18
                
                if y > panel_height - 30:
                    break
        
        # Hint to close
        hint = self.small_font.render("Press I to close", True, (150, 150, 150))
        iot_panel.blit(hint, (10, panel_height - 25))
        
        # Position on left side of screen
        self.screen.blit(iot_panel, (10, 90))
    
    def _draw_help(self):
        """Draw help panel."""
        help_panel = pygame.Surface((280, 360), pygame.SRCALPHA)
        help_panel.fill((0, 0, 0, 180))
        
        help_lines = [
            "Controls:",
            "G - Set random goal",
            "T - Generate LLM task",
            "N - Auto-navigate to goal",
            "I - Show IoT devices",
            "P - SmartThings panel",
            "F - Toggle nearest light",
            "C - Config menu",
            "L - Print event log",
            "V - Toggle 1st/3rd person",
            "K - Toggle camera view",
            "[ ] - Prev/next camera",
            "H - Toggle help",
            "ESC - Quit"
        ]
        
        y = 10
        for line in help_lines:
            text = self.small_font.render(line, True, (255, 255, 255))
            help_panel.blit(text, (10, y))
            y += 22
        
        self.screen.blit(help_panel, (RESOLUTION[0] - 290, 70))
    
    def _draw_smartthings_panel(self):
        """Draw SmartThings device status panel on the right side."""
        bridge = self.nav_demo.smartthings_bridge
        if not bridge:
            return

        device_states = bridge.get_device_states()
        if not device_states:
            return

        panel_width = 320
        panel_height = 100 + len(device_states) * 45 + 160
        panel_height = min(panel_height, RESOLUTION[1] - 100)

        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((10, 10, 40, 240))
        pygame.draw.rect(panel, (0, 150, 255), (0, 0, panel_width, panel_height), 2, border_radius=5)

        # Header
        header_bg = pygame.Surface((panel_width, 36), pygame.SRCALPHA)
        header_bg.fill((0, 80, 160, 200))
        panel.blit(header_bg, (0, 0))

        title = self.font.render("SmartThings Bridge", True, (0, 200, 255))
        panel.blit(title, (10, 6))

        n_on = sum(1 for s in device_states.values() if s["is_on"])
        status_text = self.small_font.render(
            f"{len(device_states)} devices | {n_on} on", True, (150, 200, 255)
        )
        panel.blit(status_text, (10, 40))

        y = 62
        # Device list
        for dev_id, info in device_states.items():
            is_on = info["is_on"]
            room = info["room"]

            # Background bar
            bar_color = (30, 80, 30, 180) if is_on else (40, 40, 60, 180)
            bar = pygame.Surface((panel_width - 20, 38), pygame.SRCALPHA)
            bar.fill(bar_color)
            panel.blit(bar, (10, y))

            # On/Off indicator
            indicator_color = (0, 255, 100) if is_on else (100, 100, 100)
            pygame.draw.circle(panel, indicator_color, (25, y + 19), 8)

            # Device name
            name_text = self.small_font.render(
                f"{room.title()} Light", True, (255, 255, 255)
            )
            panel.blit(name_text, (40, y + 3))

            # State label
            state_label = "ON" if is_on else "OFF"
            state_color = (0, 255, 100) if is_on else (150, 150, 150)
            state_text = self.small_font.render(state_label, True, state_color)
            panel.blit(state_text, (panel_width - 50, y + 3))

            # Docker container indicator
            docker_text = self.small_font.render("🐳 QEMU", True, (100, 150, 200))
            panel.blit(docker_text, (40, y + 20))

            y += 42

        # Separator
        pygame.draw.line(panel, (80, 80, 120), (10, y), (panel_width - 10, y))
        y += 8

        # Proximity indicator
        agent_state = self.nav_demo.agent.get_state()
        agent_pos = tuple(agent_state.position)
        closest = bridge.get_closest_device(agent_pos)
        if closest:
            dev_id, dist = closest
            fw = bridge.firmware_devices.get(dev_id)
            room = fw.room if fw else "?"
            prox_color = (0, 255, 100) if dist < INTERACTION_DISTANCE else (200, 200, 200)
            prox_text = f"Nearest: {room.title()} ({dist:.1f}m)"
            if dist < INTERACTION_DISTANCE:
                prox_text += " ← INTERACT"
            prox_render = self.small_font.render(prox_text, True, prox_color)
            panel.blit(prox_render, (10, y))
            y += 22

        # Recent interactions
        interactions = bridge.get_recent_interactions(4)
        if interactions:
            int_title = self.small_font.render("Recent:", True, (200, 200, 255))
            panel.blit(int_title, (10, y))
            y += 18

            for evt in reversed(interactions):
                direction = evt.get("direction", "?")
                room = evt.get("room", "?")
                new_st = evt.get("new_state", "?")
                if direction == "cloud→3D":
                    icon = "📱→"
                elif direction == "3D→cloud":
                    icon = "🏠→"
                else:
                    icon = "⚡"
                line = f"{icon} {room}: {new_st.upper()}"
                line_render = self.small_font.render(line[:30], True, (180, 180, 220))
                panel.blit(line_render, (15, y))
                y += 16
                if y > panel_height - 30:
                    break

        # Close hint
        hint = self.small_font.render("P close | F toggle light", True, (120, 120, 150))
        panel.blit(hint, (10, panel_height - 22))

        # Position on right side, below header
        self.screen.blit(panel, (RESOLUTION[0] - panel_width - 10, 90))

    def _draw_proximity_indicator(self):
        """Draw a small proximity indicator at the bottom of the screen."""
        bridge = self.nav_demo.smartthings_bridge
        if not bridge:
            return

        agent_state = self.nav_demo.agent.get_state()
        agent_pos = tuple(agent_state.position)
        closest = bridge.get_closest_device(agent_pos)
        if not closest:
            return

        dev_id, dist = closest
        fw = bridge.firmware_devices.get(dev_id)
        if not fw:
            return

        if dist > INTERACTION_DISTANCE * 2:
            return  # Too far, don't show

        # Draw at bottom center
        bar_width = 300
        bar_height = 30
        bar_x = (RESOLUTION[0] - bar_width) // 2
        bar_y = RESOLUTION[1] - bar_height - 10

        bar = pygame.Surface((bar_width, bar_height), pygame.SRCALPHA)

        if dist < INTERACTION_DISTANCE:
            bar.fill((0, 80, 0, 200))
            pygame.draw.rect(bar, (0, 255, 100), (0, 0, bar_width, bar_height), 2, border_radius=5)
            icon = "💡" if fw.is_on else "🔌"
            text = f"{icon} {fw.room.title()} Light: {'ON' if fw.is_on else 'OFF'} — F to toggle"
            color = (0, 255, 100)
        else:
            ratio = max(0, 1.0 - (dist - INTERACTION_DISTANCE) / INTERACTION_DISTANCE)
            alpha = int(100 + 100 * ratio)
            bar.fill((40, 40, 60, alpha))
            pygame.draw.rect(bar, (100, 100, 150), (0, 0, bar_width, bar_height), 1, border_radius=5)
            text = f"🏠 {fw.room.title()} Light ({dist:.1f}m)"
            color = (180, 180, 200)

        text_render = self.small_font.render(text, True, color)
        text_rect = text_render.get_rect(center=(bar_width // 2, bar_height // 2))
        bar.blit(text_render, text_rect)

        self.screen.blit(bar, (bar_x, bar_y))

    def handle_events(self) -> Optional[str]:
        """Handle pygame events. Returns action or None."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return "quit"
            elif event.type == pygame.MOUSEBUTTONDOWN:
                # Handle mouse clicks for config menu
                if event.button == 1:  # Left click
                    mouse_x, mouse_y = event.pos
                    if hasattr(self, 'nav_demo') and self.nav_demo.vesper:
                        config_menu = self.nav_demo.vesper.config_menu
                        if config_menu and config_menu.is_visible:
                            config_menu.handle_click(mouse_x, mouse_y)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return "quit"
                elif event.key == pygame.K_h:
                    self.show_help = not self.show_help
                elif event.key == pygame.K_g:
                    return "set_goal"
                elif event.key == pygame.K_t:
                    return "set_object_goal"
                elif event.key == pygame.K_n:
                    self.auto_navigate = not self.auto_navigate
                    if self.auto_navigate and self.goal_pos is None:
                        self.status_message = "Set a goal first with T"
                        self.auto_navigate = False
                elif event.key == pygame.K_i:
                    return "show_iot"
                elif event.key == pygame.K_p:
                    self.show_smartthings_panel = not self.show_smartthings_panel
                elif event.key == pygame.K_f:
                    return "toggle_nearest_light"
                elif event.key == pygame.K_l:
                    return "print_log"
                elif event.key == pygame.K_c:
                    return "config_menu"
                elif event.key == pygame.K_v:
                    self.third_person = not self.third_person
                    # Toggle humanoid visibility: show in 3rd person, hide in 1st person
                    if hasattr(self, 'nav_demo') and self.nav_demo:
                        if self.third_person:
                            # Third person - show humanoid
                            if self.nav_demo.humanoid:
                                self.nav_demo.set_humanoid_visible(True)
                        else:
                            # First person - hide humanoid (don't see your own body from eyes)
                            if self.nav_demo.humanoid:
                                self.nav_demo.set_humanoid_visible(False)
                elif event.key == pygame.K_k:
                    self.show_camera_view = not self.show_camera_view
                    if self.show_camera_view:
                        self.status_message = "Camera view ON - use [ ] to switch cameras"
                    else:
                        self.status_message = "Camera view OFF"
                elif event.key == pygame.K_LEFTBRACKET:
                    # Previous camera
                    if self.show_camera_view:
                        self.current_camera_index -= 1
                        self.status_message = f"Switched to camera {self.current_camera_index + 1}"
                elif event.key == pygame.K_RIGHTBRACKET:
                    # Next camera
                    if self.show_camera_view:
                        self.current_camera_index += 1
                        self.status_message = f"Switched to camera {self.current_camera_index + 1}"
                else:
                    # Pass other keys to config menu if it's open
                    if hasattr(self, 'nav_demo') and self.nav_demo.vesper:
                        config_menu = self.nav_demo.vesper.config_menu
                        if config_menu and config_menu.is_visible:
                            config_menu.handle_keypress(event.key)
        
        # Continuous key presses
        keys = pygame.key.get_pressed()
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            return "move_forward"
        elif keys[pygame.K_s] or keys[pygame.K_DOWN]:
            return "move_backward"
        elif keys[pygame.K_a] or keys[pygame.K_LEFT]:
            return "turn_left"
        elif keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            return "turn_right"
        elif keys[pygame.K_q]:
            return "look_up"
        elif keys[pygame.K_e]:
            return "look_down"
        
        return None
    
    def set_goal(self, name: str, pos: mn.Vector3, distance: float):
        """Set current navigation goal."""
        self.goal_name = name
        self.goal_pos = pos
        self.status_message = f"Navigate to {name} ({distance:.1f}m away)"
        
    def clear_goal(self):
        """Clear current goal."""
        self.goal_name = None
        self.goal_pos = None
        self.auto_navigate = False
        self.status_message = "Goal reached! Press G for new goal"


# ============================================================================
# EVALUATION DATA STRUCTURES
# ============================================================================

@dataclass
class NavigationTrial:
    """A single room-to-room navigation attempt."""
    trial_id: int = 0
    task_name: str = ""
    source_room: str = ""
    target_room: str = ""
    success: bool = False
    geodesic_distance: float = 0.0
    actual_distance: float = 0.0
    spl: float = 0.0
    num_steps: int = 0
    navigation_time_sec: float = 0.0
    motion_detected_on_arrival: bool = False
    automation_triggered: bool = False


@dataclass
class SceneEvalResult:
    """Full evaluation result for one scene."""
    scene_id: str = ""
    scene_path: str = ""
    num_rooms: int = 0
    room_names: list = field(default_factory=list)
    num_devices: int = 0
    num_automations: int = 0
    num_firmware_sensors: int = 0
    navmesh_area_m2: float = 0.0
    # Navigation aggregate
    nav_trials: list = field(default_factory=list)
    nav_success_rate: float = 0.0
    mean_spl: float = 0.0
    # Sensor aggregate
    total_room_entries: int = 0
    motion_detections: int = 0
    sensor_detection_rate: float = 0.0
    camera_tracking_events: int = 0
    # Automation aggregate
    total_motion_events: int = 0
    automations_triggered: int = 0
    automation_trigger_rate: float = 0.0
    # SmartThings — local
    st_devices_created: int = 0
    st_docker_containers: int = 0
    st_proximity_toggles: int = 0
    # SmartThings — cloud sync
    st_ngrok_connected: bool = False
    st_cloud_pushes: int = 0          # 3D → SmartThings cloud (stateCallback)
    st_cloud_commands: int = 0        # SmartThings app → 3D (phone commands)
    st_cloud_sync_cycles: int = 0     # background sync loop iterations
    st_interaction_log: list = field(default_factory=list)  # full event log
    # Articulated 3D objects
    num_articulated_objects: int = 0     # fridges, cabinets, drawers, etc.
    articulated_interactions: int = 0    # open/close events
    # Schedule
    tasks_scheduled: int = 0
    tasks_navigated: int = 0
    rooms_visited: list = field(default_factory=list)
    room_coverage: float = 0.0
    eval_duration_sec: float = 0.0


# ============================================================================
# CLI
# ============================================================================

def parse_eval_args():
    parser = argparse.ArgumentParser(description="VESPER Autonomous Full-Pipeline Evaluation")
    parser.add_argument("--num-scenes", type=int, default=168,
                        help="Number of HSSD scenes to evaluate (default: 168 = ALL)")
    parser.add_argument("--num-days", type=int, default=1,
                        help="Simulated days per scene (default: 1)")
    parser.add_argument("--with-smartthings", action="store_true",
                        help="Enable SmartThings Docker firmware containers")
    parser.add_argument("--headless", action="store_true",
                        help="Run without Pygame display (no rendering window)")
    parser.add_argument("--display", dest="headless", action="store_false",
                        help="Run WITH Pygame display (default if not --headless)")
    parser.add_argument("--model", type=str, default=None,
                        help="LLM model name override")
    parser.add_argument("--time-scale", type=float, default=60.0,
                        help="Simulation time scale (default: 60x, 1 real sec = 1 sim min)")
    parser.add_argument("--nav-timeout-steps", type=int, default=2000,
                        help="Max steps per navigation before giving up (default: 2000)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--allow-fallback-tasks", action="store_true",
                        help="Allow emergency/hardcoded tasks when LLM fails (default: abort)")
    parser.set_defaults(headless=False)
    return parser.parse_args()


# ============================================================================
# SCENE DISCOVERY
# ============================================================================

def find_scenes(max_scenes: int = 168, random_selection: bool = False):
    """Find available HSSD-HAB scenes. Returns list of (scene_path, config_path).
    
    Set max_scenes=0 or very large number to use ALL available scenes.
    If random_selection=True and max_scenes=1, randomly pick 1 scene.
    """
    data_path = os.path.join(PROJECT_ROOT, "data")
    scenes = []

    # Prefer articulated scenes (interactive objects with openable joints)
    hssd_artic_path = os.path.join(data_path, "scene_datasets", "hssd-hab", "scenes-articulated")
    hssd_static_path = os.path.join(data_path, "scene_datasets", "hssd-hab", "scenes")
    hssd_path = hssd_artic_path if os.path.exists(hssd_artic_path) else hssd_static_path
    config_name = "hssd-hab-articulated.scene_dataset_config.json" if hssd_path == hssd_artic_path else "hssd-hab.scene_dataset_config.json"
    config_path = os.path.join(data_path, "scene_datasets", "hssd-hab", config_name)

    if os.path.exists(hssd_path):
        scene_files = sorted([
            f for f in os.listdir(hssd_path) if f.endswith(".scene_instance.json")
        ])
        total_available = len(scene_files)
        # If max_scenes == 0, use all
        effective_max = total_available if max_scenes <= 0 else min(max_scenes, total_available)
        
        # Random selection for single scene
        if random_selection and max_scenes == 1 and total_available > 1:
            import random as _random
            selected_scene = _random.choice(scene_files)
            scene_files = [selected_scene]
            print(f"🎲 Randomly selected 1 scene out of {total_available} available")
        elif effective_max < total_available:
            import numpy as _np
            indices = _np.linspace(0, total_available - 1, effective_max, dtype=int)
            scene_files = [scene_files[i] for i in indices]
        
        for sf in scene_files:
            cfg = config_path if os.path.exists(config_path) else None
            scenes.append((os.path.join(hssd_path, sf), cfg))

    if not scenes:
        raise FileNotFoundError("No HSSD-HAB scenes found in data/scene_datasets/hssd-hab/scenes/")
    if not (random_selection and max_scenes == 1):
        print(f"🏠 {len(scenes)} / {total_available if os.path.exists(hssd_path) else 0} HSSD-HAB scenes selected")
    return scenes


# ============================================================================
# RESULTS OUTPUT
# ============================================================================

def write_results(all_results: list, results_dir: str):
    """Write JSON and human-readable summary."""
    import json as _json

    # --- JSON ---
    json_path = os.path.join(results_dir, "eval_results.json")
    serialisable = []
    for r in all_results:
        d = asdict(r)
        # Convert NavigationTrial list
        d["nav_trials"] = [asdict(t) if hasattr(t, '__dataclass_fields__') else t
                           for t in d.get("nav_trials", [])]
        serialisable.append(d)

    with open(json_path, "w") as f:
        _json.dump(serialisable, f, indent=2, default=str)
    print(f"\n📄 Results written to {json_path}")

    # --- Summary ---
    txt_path = os.path.join(results_dir, "eval_summary.txt")
    with open(txt_path, "w") as f:
        f.write("VESPER Autonomous Evaluation — Summary\n")
        f.write("=" * 60 + "\n\n")
        for r in all_results:
            f.write(f"Scene: {r.scene_id}\n")
            f.write(f"  Rooms: {r.num_rooms}  Devices: {r.num_devices}  Automations: {r.num_automations}\n")
            f.write(f"  Firmware Sensors: {r.num_firmware_sensors}\n")
            f.write(f"  Navmesh Area: {r.navmesh_area_m2:.1f} m²\n")
            f.write(f"  Nav Trials: {len(r.nav_trials)}  Success Rate: {r.nav_success_rate:.1%}\n")
            f.write(f"  Mean SPL: {r.mean_spl:.3f}\n")
            f.write(f"  Sensor Detection Rate: {r.sensor_detection_rate:.1%}\n")
            f.write(f"  Automation Trigger Rate: {r.automation_trigger_rate:.1%}\n")
            f.write(f"  Articulated Objects: {r.num_articulated_objects}  Interactions: {r.articulated_interactions}\n")
            f.write(f"  SmartThings Containers: {r.st_docker_containers}  Proximity Toggles: {r.st_proximity_toggles}\n")
            f.write(f"  SmartThings Cloud: pushes={r.st_cloud_pushes}  commands={r.st_cloud_commands}  ngrok={'YES' if r.st_ngrok_connected else 'NO'}\n")
            f.write(f"  Tasks Scheduled: {r.tasks_scheduled}  Navigated: {r.tasks_navigated}\n")
            f.write(f"  Room Coverage: {r.room_coverage:.1%}\n")
            f.write(f"  Duration: {r.eval_duration_sec:.1f}s\n\n")

        # Aggregate
        if all_results:
            all_trials = [t for r in all_results for t in r.nav_trials]
            if all_trials:
                agg_sr = sum(1 for t in all_trials if t.success) / len(all_trials)
                spls = [t.spl for t in all_trials if t.spl > 0]
                agg_spl = sum(spls) / len(spls) if spls else 0.0
            else:
                agg_sr = agg_spl = 0.0
            f.write("AGGREGATE\n")
            f.write(f"  Scenes: {len(all_results)}\n")
            f.write(f"  Total Nav Trials: {len(all_trials)}\n")
            f.write(f"  Aggregate Success Rate: {agg_sr:.1%}\n")
            f.write(f"  Aggregate Mean SPL: {agg_spl:.3f}\n")
            # SmartThings cloud aggregate
            total_pushes = sum(r.st_cloud_pushes for r in all_results)
            total_cmds = sum(r.st_cloud_commands for r in all_results)
            total_toggles = sum(r.st_proximity_toggles for r in all_results)
            total_containers = sum(r.st_docker_containers for r in all_results)
            ngrok_scenes = sum(1 for r in all_results if r.st_ngrok_connected)
            total_art_objects = sum(r.num_articulated_objects for r in all_results)
            total_art_interactions = sum(r.articulated_interactions for r in all_results)
            f.write(f"\n  Articulated Objects:\n")
            f.write(f"    Total Discovered: {total_art_objects}\n")
            f.write(f"    Total Interactions: {total_art_interactions}\n")
            f.write(f"\n  SmartThings Cloud Sync:\n")
            f.write(f"    Docker Containers Total: {total_containers}\n")
            f.write(f"    Proximity Toggles Total: {total_toggles}\n")
            f.write(f"    Cloud State Pushes (3D→ST): {total_pushes}\n")
            f.write(f"    Cloud Commands (ST→3D): {total_cmds}\n")
            f.write(f"    Scenes with ngrok: {ngrok_scenes}/{len(all_results)}\n")

    print(f"📄 Summary written to {txt_path}")


# ============================================================================
# MAIN — AUTONOMOUS EVALUATION
# ============================================================================

def main():
    """Run the full VESPER pipeline autonomously with data collection."""
    eval_args = parse_eval_args()

    random.seed(eval_args.seed)
    np.random.seed(eval_args.seed)

    # Patch LLMConfig model default if user specified one
    if eval_args.model:
        from vesper.agents.llm_client import LLMConfig as _LC, LLMProvider as _LP
        _LC.__dataclass_fields__["model"].default = eval_args.model
        _LC.__dataclass_fields__["provider"].default = _LP.LOCAL

    # ---- Pre-flight: verify LLM is reachable ----
    print("\n🔌 Verifying LLM connectivity...")
    from vesper.agents.llm_client import LLMConfig as _LCCheck, LLMClient as _LLMCheck, LLMMessage as _LMMsg
    _test_cfg = _LCCheck()
    if not _test_cfg.validate():
        print("❌ LLM config invalid. Set OPENWEBUI_URL and OPENWEBUI_API_KEY env vars.")
        print(f"   OPENWEBUI_URL  = {os.environ.get('OPENWEBUI_URL', '(not set)')}")
        print(f"   OPENWEBUI_API_KEY = {os.environ.get('OPENWEBUI_API_KEY', '(not set)')}")
        return
    try:
        _test_client = _LLMCheck(_test_cfg)
        _resp = _test_client.chat([_LMMsg("user", "Say OK")])
        print(f"✅ LLM reachable — model: {_test_cfg.model}")
        print(f"   Response: {_resp.content[:80]}..." if len(_resp.content) > 80 else f"   Response: {_resp.content}")
    except Exception as e:
        print(f"❌ LLM connection failed: {e}")
        print("   Make sure LMStudio is running at localhost:1234")
        if not eval_args.allow_fallback_tasks:
            print("   Use --allow-fallback-tasks to continue with hardcoded schedules (not recommended).")
            return
        else:
            print("   ⚠️  Continuing with fallback tasks (--allow-fallback-tasks).")
    print()

    # ---- Pre-flight: verify SmartThings / ngrok if requested ----
    if eval_args.with_smartthings:
        print("🔌 Verifying SmartThings prerequisites...")
        # Check Docker
        import shutil
        if shutil.which("docker"):
            print("  ✅ Docker CLI found")
        else:
            print("  ⚠️  Docker CLI not found — firmware containers may fail")
        # Check ngrok for cloud sync
        try:
            import urllib.request, json as _j
            req = urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2)
            tunnels = _j.loads(req.read()).get("tunnels", [])
            https_tunnels = [t for t in tunnels if t.get("public_url", "").startswith("https://")]
            if https_tunnels:
                ngrok_url = https_tunnels[0]["public_url"]
                print(f"  ✅ ngrok detected: {ngrok_url}")
                print(f"     SmartThings cloud sync ENABLED")
            else:
                print("  ⚠️  ngrok running but no HTTPS tunnel — cloud sync may not work")
        except Exception:
            print("  ⚠️  ngrok NOT detected at localhost:4040")
            print("     Cloud sync will be limited (SmartThings can still poll via stateRefreshRequest)")
            print("     To enable full cloud sync: ngrok http 8443")
        print()

    scenes = find_scenes(eval_args.num_scenes, random_selection=(eval_args.num_scenes == 1))
    print(f"Found {len(scenes)} scenes to evaluate")

    all_results: list = []

    for scene_idx, (scene_path, config_path) in enumerate(scenes):
        print("\n" + "=" * 70)
        print(f"  SCENE {scene_idx + 1}/{len(scenes)}: {os.path.basename(scene_path)}")
        print("=" * 70)

        # Clean up any leftover Docker containers from previous runs
        try:
            import subprocess
            subprocess.run(
                "docker ps -a | grep vesper-3d | awk '{print $1}' | xargs -r docker rm -f 2>/dev/null || true",
                shell=True,
                check=False,
                capture_output=True
            )
            logger.info("Cleaned up leftover Docker containers")
        except Exception as e:
            logger.warning(f"Docker cleanup warning: {e}")

        t0 = time_module.time()
        result = SceneEvalResult()
        result.scene_path = scene_path
        result.scene_id = os.path.basename(scene_path).split(".")[0]

        try:
            _run_scene_evaluation(scene_path, config_path, eval_args, result)
        except Exception as e:
            logger.error(f"Scene {scene_idx + 1} failed: {e}")
            import traceback
            traceback.print_exc()

        result.eval_duration_sec = time_module.time() - t0
        all_results.append(result)

        # Write results after EACH scene so Ctrl+C doesn't lose data
        write_results(all_results, RESULTS_DIR)

    # Final write
    if all_results:
        write_results(all_results, RESULTS_DIR)
        print(f"\n\u2705 Evaluation complete: {len(all_results)} scenes")
    else:
        print("No scenes evaluated successfully!")


def _run_scene_evaluation(scene_path, config_path, eval_args, result: SceneEvalResult):
    """Run the full autonomous evaluation for one scene.

    This is essentially the same as main() in vesper_smartthings.py but:
    - No manual keyboard input (fully autonomous)
    - Collects navigation / sensor / automation metrics
    - Auto-quits after num_days simulated days
    """
    # ---- Create demo (exactly like vesper_smartthings.py) ----
    demo = ObjectNavDemo()
    demo._current_scene_path = scene_path

    demo.sim = demo.create_simulator(scene_path, config_path)
    demo.agent = demo.sim.get_agent(0)
    demo.init_path_follower()
    demo.init_llm_client()

    # Patch model on the demo's LLM client
    if eval_args.model and demo.llm_client:
        demo.llm_client.config.model = eval_args.model

    # Navmesh stats
    pf = demo.sim.pathfinder
    if pf.is_loaded:
        bounds = pf.get_bounds()
        area = (bounds[1][0] - bounds[0][0]) * (bounds[1][2] - bounds[0][2])
        result.navmesh_area_m2 = round(float(area), 1)

    # Get room positions first (needed for humanoid spawn)
    room_positions = demo.get_room_positions()
    if "hssd" in scene_path.lower():
        hssd_rooms, _ = demo.load_hssd_semantics(scene_path)
        room_positions.update(hssd_rooms)

    room_pos_dict = {}
    for room, positions in room_positions.items():
        if positions:
            pos = positions[0]
            room_pos_dict[room] = (float(pos[0]), float(pos[1]), float(pos[2]))

    # Place agent - spawn in a random room for better navigation success
    start_pos = demo.get_random_navigable_point()
    if room_pos_dict:
        # Pick a random room and use its position as spawn point
        import random as _rand
        random_room = _rand.choice(list(room_pos_dict.keys()))
        room_tuple_pos = room_pos_dict[random_room]
        # Convert to Vector3 and snap to navmesh to ensure it's navigable
        candidate_pos = mn.Vector3(*room_tuple_pos)
        # Snap to navmesh - this ensures the position is on a valid navigable point
        start_pos = demo.sim.pathfinder.snap_point(candidate_pos)
        logger.info(f"Spawning humanoid in room: {random_room} at {start_pos}")
    
    agent_state = habitat_sim.AgentState()
    agent_state.position = start_pos
    demo.agent.set_state(agent_state)

    # Filter room positions to only include rooms reachable from agent's spawn position
    # This prevents "goal unreachable" errors in multi-floor or disconnected navmesh scenes
    agent_pos = demo.agent.get_state().position
    filtered_room_dict = {}
    for room_name, room_tuple in room_pos_dict.items():
        goal_pos = mn.Vector3(*room_tuple)
        # Check if path exists from agent to goal
        path = habitat_sim.ShortestPath()
        path.requested_start = agent_pos
        path.requested_end = goal_pos
        if demo.sim.pathfinder.find_path(path):
            filtered_room_dict[room_name] = room_tuple
        else:
            logger.debug(f"Filtering out unreachable room: {room_name}")
    
    logger.info(f"Reachable rooms: {len(filtered_room_dict)}/{len(room_pos_dict)}")
    room_pos_dict = filtered_room_dict  # Replace with filtered dict

    result.num_rooms = len(room_pos_dict)
    result.room_names = list(room_pos_dict.keys())

    if result.num_rooms == 0:
        logger.warning("No rooms found — skipping scene")
        demo.sim.close()
        return

    # Humanoid
    demo.load_humanoid()

    # VESPER IoT
    demo.init_vesper_integration(list(room_pos_dict.keys()), room_pos_dict)
    if demo.vesper and demo.vesper.iot_manager:
        result.num_devices = len(demo.vesper.iot_manager.devices)
    if demo.vesper and demo.vesper.iot_bridge:
        result.num_automations = len(getattr(demo.vesper.iot_bridge, 'automation_rules', []))
    if hasattr(demo, 'sensor_bridge') and demo.sensor_bridge:
        stats = demo.sensor_bridge.get_sensor_stats()
        result.num_firmware_sensors = stats.get('total_firmware_sensors', 0)

    # SmartThings Docker (optional)
    if eval_args.with_smartthings and room_pos_dict:
        logger.info("Starting SmartThings Docker bridge ...")
        demo.init_smartthings(room_pos_dict)
        if demo.smartthings_bridge:
            result.st_devices_created = len(demo.smartthings_bridge.firmware_devices)
            result.st_docker_containers = result.st_devices_created

    # Articulated device bridge — interactive 3D objects (fridges, cabinets, etc.)
    demo.init_articulated_devices(room_pos_dict)
    num_articulated = 0
    if demo.articulated_bridge:
        num_articulated = len(demo.articulated_bridge.devices)

    # Pygame UI (same as vesper_smartthings.py — unless --headless)
    ui = GameUI(demo, headless=eval_args.headless)
    ui.init_pygame()
    ui.available_targets = list(room_pos_dict.keys())

    # ---- DATA COLLECTION ACCUMULATORS ----
    nav_trials = []
    rooms_visited = set()
    trial_counter = 0
    tasks_navigated = 0
    tasks_scheduled = 0
    total_motion_events = 0
    total_automations = 0
    total_room_entries = 0
    total_motion_detections = 0
    camera_tracking_count = 0
    st_proximity_toggles = 0
    articulated_interactions = 0

    # Per-navigation tracking
    nav_start_pos = None
    nav_start_time = None
    nav_steps = 0
    current_trial = None
    prev_task_id = None
    nav_goal_pos = None

    # Override time_scale
    demo.time_manager._time_scale = eval_args.time_scale

    # ---- RUN DAILY SCHEDULES ----
    for day in range(eval_args.num_days):
        logger.info(f"\n--- Day {day + 1}/{eval_args.num_days} ---")

        # Start day at the current real-world time
        demo.time_manager._simulation_time = datetime.now()
        demo._init_autonomous_simulation()

        if demo.autonomous_sim:
            demo.autonomous_sim.time_scale = eval_args.time_scale
            demo.autonomous_sim.time_manager = demo.time_manager
            demo.time_manager._time_scale = eval_args.time_scale

            day_tasks = (demo.autonomous_sim.current_schedule.tasks
                         if demo.autonomous_sim.current_schedule else [])
            tasks_scheduled += len(day_tasks)

            # Check whether tasks are LLM-generated or emergency fallback
            emergency_count = sum(1 for t in day_tasks if t.task_id.startswith("emergency_"))
            llm_count = len(day_tasks) - emergency_count
            if emergency_count > 0:
                logger.warning(f"⚠️  Day {day + 1}: {emergency_count}/{len(day_tasks)} tasks are EMERGENCY FALLBACK (not LLM-generated)")
                if not eval_args.allow_fallback_tasks:
                    logger.error("Aborting — all tasks must be LLM-generated. Use --allow-fallback-tasks to override.")
                    break
            else:
                logger.info(f"✅ Day {day + 1}: All {llm_count} tasks generated by LLM")

            logger.info(f"Schedule has {len(day_tasks)} tasks")
            for i, t in enumerate(day_tasks):
                logger.info(f"  [{i+1}] {t.scheduled_time.strftime('%H:%M')} {t.name} → {t.location.room_name}")
        else:
            logger.warning("Autonomous sim failed to init")
            if not eval_args.allow_fallback_tasks:
                logger.error("Aborting — autonomous sim required for LLM task generation.")
                break
            day_tasks = []

        # ---- Main loop (identical to vesper_smartthings.py) ----
        frame_count = 0
        max_frames = 150_000  # Safety cap
        day_complete = False
        prev_task_id = None
        nav_steps = 0
        current_trial = None

        clock = pygame.time.Clock() if (HAS_PYGAME and not eval_args.headless) else None

        while frame_count < max_frames and not day_complete:
            frame_count += 1

            # ---------- Observations ----------
            obs = demo.sim.get_sensor_observations()

            # ---------- Update simulation time & tasks ----------
            time_info = demo.update_simulation_time()

            # Detect day complete
            if demo.autonomous_sim and demo.autonomous_sim.current_task is None:
                if demo.autonomous_sim.current_task_index >= len(
                    demo.autonomous_sim.current_schedule.tasks
                    if demo.autonomous_sim.current_schedule else []
                ):
                    day_complete = True

            # ---------- Detect new task → start navigation trial ----------
            current_task = time_info.get("current_task")
            current_task_id = current_task.task_id if current_task else None

            if current_task_id and current_task_id != prev_task_id:
                # Finalise previous trial if any
                if current_trial is not None:
                    _finalise_trial(current_trial, demo, nav_start_pos, nav_goal_pos)
                    nav_trials.append(current_trial)

                # Start new trial
                trial_counter += 1
                tasks_navigated += 1
                target_room = current_task.location.room_name
                rooms_visited.add(target_room)

                agent_st = demo.agent.get_state()
                nav_start_pos = tuple(agent_st.position)
                nav_start_time = time_module.time()
                nav_steps = 0
                nav_goal_pos = room_pos_dict.get(target_room)

                current_trial = NavigationTrial(
                    trial_id=trial_counter,
                    task_name=current_task.name,
                    source_room=_guess_current_room(nav_start_pos, room_pos_dict),
                    target_room=target_room,
                )
                prev_task_id = current_task_id

                # Set UI to navigate (same as vesper_smartthings.py)
                if demo.current_goal:
                    ui.goal_pos = demo.current_goal
                    ui.goal_name = demo.current_goal_name
                    ui.auto_navigate = True
                    ui.status_message = f"🚶 Going to {demo.current_goal_name}..."

            # ---------- VESPER system updates (same as vesper_smartthings.py) ----------
            if demo.vesper:
                agent_st = demo.agent.get_state()
                agent_pos = tuple(agent_st.position)

                # Update humanoid
                if demo.vesper.humanoid:
                    quat = agent_st.rotation
                    demo.vesper.update_humanoid(
                        agent_position=agent_pos,
                        agent_rotation=(quat.x, quat.y, quat.z, quat.w),
                    )

                # Update IoT bridge → motion sensors / automations
                iot_events = demo.vesper.update_agent_position(agent_pos)
                for event in iot_events:
                    etype = event.get("event_type", "")
                    if etype == "motion_detected":
                        total_motion_events += 1
                        room = event.get("room", "unknown")
                        ui.status_message = f"🔴 Motion detected in {room}!"
                    elif etype == "room_enter":
                        total_room_entries += 1
                    elif etype == "automation_triggered":
                        total_automations += 1

            # ---------- Update sensors ----------
            if hasattr(demo, 'room_sensor_state') and demo.room_sensor_state:
                agent_st = demo.agent.get_state()
                humanoid_pos = tuple(agent_st.position)
                sensor_detections = demo.update_sensors(humanoid_pos, dt=0.016)
                total_motion_detections += len(sensor_detections)
                for det in sensor_detections:
                    ui.status_message = f"🔴 Motion in {det['room']}! Camera tracking..."
                    camera_tracking_count += 1

            # ---------- SmartThings proximity (same as vesper_smartthings.py) ----------
            if demo.smartthings_bridge:
                agent_st = demo.agent.get_state()
                humanoid_pos = tuple(agent_st.position)
                st_events = demo.smartthings_bridge.check_proximity_interaction(
                    humanoid_pos, time_module.time()
                )
                st_proximity_toggles += len(st_events)
                for evt in st_events:
                    room = evt["room"]
                    new_state = evt["new_state"]
                    ui.status_message = f"💡 {room.title()} Light → {new_state.upper()} (proximity)"

            # ---------- Articulated 3D device interaction ----------
            if demo.articulated_bridge:
                agent_st = demo.agent.get_state()
                humanoid_pos = tuple(agent_st.position)
                art_events = demo.articulated_bridge.check_interaction(
                    humanoid_pos, time_module.time()
                )
                articulated_interactions += len(art_events)
                for evt in art_events:
                    dtype = evt["device_type"]
                    action_str = evt["action"]
                    room = evt["room"]
                    ui.status_message = f"🔧 {action_str.upper()} {dtype} in {room} (3D interaction)"

                # Step joint animations (smooth open/close)
                demo.articulated_bridge.step_animations()

            # ---------- Render ----------
            ui.render_frame(obs)

            # ---------- Handle events (full keyboard controls) ----------
            if not eval_args.headless and HAS_PYGAME:
                action = ui.handle_events()
                if action == "quit":
                    day_complete = True
                elif action == "print_log":
                    if demo.vesper and demo.vesper.iot_bridge:
                        demo.vesper.iot_bridge.print_event_log(30)
                        ui.status_message = "Event log printed to terminal"
                elif action == "show_iot":
                    ui.show_iot_panel = not ui.show_iot_panel
                    if ui.show_iot_panel and demo.vesper and demo.vesper.iot_manager:
                        ui.status_message = f"IoT Panel: {len(demo.vesper.iot_manager.devices)} devices"
                    else:
                        ui.status_message = "IoT panel closed"
                elif action == "toggle_nearest_light":
                    if demo.smartthings_bridge:
                        agent_st = demo.agent.get_state()
                        agent_pos = tuple(agent_st.position)
                        closest = demo.smartthings_bridge.get_closest_device(agent_pos)
                        if closest:
                            dev_id, dist = closest
                            fw = demo.smartthings_bridge.firmware_devices.get(dev_id)
                            if fw:
                                evt = demo.smartthings_bridge.toggle_device_in_room(fw.room)
                                if evt:
                                    ui.status_message = f"\ud83d\udca1 {fw.room.title()} Light \u2192 {evt['new_state'].upper()} ({dist:.1f}m)"
                elif action == "config_menu":
                    if demo.vesper and demo.vesper.config_menu:
                        demo.vesper.config_menu.toggle_visibility()
                elif action in ["move_forward", "move_backward", "turn_left", "turn_right",
                                "look_up", "look_down"]:
                    demo.agent.act(action)
                    is_moving = action in ["move_forward", "move_backward"]
                    if is_moving and ui.goal_pos is not None:
                        demo.update_humanoid_position(is_moving=True, target_pos=mn.Vector3(*ui.goal_pos))
                    elif is_moving:
                        agent_st = demo.agent.get_state()
                        fwd = mn.Vector3(agent_st.position[0], 0, agent_st.position[2] - 1)
                        demo.update_humanoid_position(is_moving=True, target_pos=fwd)
                    else:
                        demo.update_humanoid_position(is_moving=False)
            else:
                action = None

            # ---------- Auto-navigation (same as vesper_smartthings.py) ----------
            if ui.auto_navigate and ui.goal_pos is not None and action is None:
                auto_action, goal_reached = demo.get_action_to_goal(ui.goal_pos)
                if auto_action:
                    demo.agent.act(auto_action)
                    nav_steps += 1
                    is_moving = auto_action in ["move_forward", "move_backward"]
                    demo.update_humanoid_position(
                        is_moving=is_moving,
                        target_pos=mn.Vector3(*ui.goal_pos),
                    )
                    agent_st = demo.agent.get_state()
                    distance = np.linalg.norm(
                        np.array(ui.goal_pos) - np.array(agent_st.position)
                    )
                    ui.status_message = f"Navigating... {distance:.1f}m remaining"
                else:
                    # Navigation done - check if reached or failed
                    if current_trial is not None:
                        current_trial.success = goal_reached
                        current_trial.num_steps = nav_steps
                        current_trial.navigation_time_sec = time_module.time() - (nav_start_time or time_module.time())
                    ui.clear_goal()
                    if goal_reached:
                        print("[ObjectNav] Goal reached!")
                    else:
                        print("[ObjectNav] Navigation failed - goal unreachable")

                # Timeout guard
                if nav_steps >= eval_args.nav_timeout_steps:
                    if current_trial is not None:
                        current_trial.success = False
                        current_trial.num_steps = nav_steps
                        current_trial.navigation_time_sec = time_module.time() - (nav_start_time or time_module.time())
                    ui.clear_goal()
                    print(f"[NAV] Timeout after {nav_steps} steps")

            if not eval_args.headless and ui.clock:
                clock.tick(60)
            # else: headless → run as fast as possible

        # Finalise last trial of the day
        if current_trial is not None:
            _finalise_trial(current_trial, demo, nav_start_pos, nav_goal_pos)
            nav_trials.append(current_trial)
            current_trial = None

        logger.info(f"Day {day + 1} complete. Frames: {frame_count}")

    # ---- Aggregate results ----
    result.nav_trials = nav_trials
    if nav_trials and len(nav_trials) > 0:
        result.nav_success_rate = sum(1 for t in nav_trials if t.success) / len(nav_trials)
        spls = [t.spl for t in nav_trials if t.spl > 0]
        result.mean_spl = sum(spls) / len(spls) if spls else 0.0
    result.tasks_scheduled = tasks_scheduled
    result.tasks_navigated = tasks_navigated
    result.rooms_visited = sorted(rooms_visited)
    result.room_coverage = len(rooms_visited) / result.num_rooms if result.num_rooms > 0 else 0.0
    result.total_room_entries = total_room_entries
    result.motion_detections = total_motion_detections
    result.sensor_detection_rate = total_motion_detections / total_room_entries if total_room_entries > 0 else 0.0
    result.total_motion_events = total_motion_events
    result.automations_triggered = total_automations
    result.automation_trigger_rate = total_automations / total_motion_events if total_motion_events > 0 else 0.0
    result.camera_tracking_events = camera_tracking_count
    result.st_proximity_toggles = st_proximity_toggles
    result.articulated_interactions = articulated_interactions
    if demo.articulated_bridge:
        result.num_articulated_objects = len(demo.articulated_bridge.devices)

    # ---- Harvest SmartThings cloud sync metrics ----
    if demo.smartthings_bridge:
        bridge = demo.smartthings_bridge
        result.st_ngrok_connected = bridge.ngrok_url is not None
        # Parse interaction log for cloud-direction events
        ilog = bridge._interaction_log if hasattr(bridge, '_interaction_log') else []
        result.st_interaction_log = list(ilog)
        result.st_cloud_pushes = sum(1 for e in ilog if e.get("direction") == "3D→cloud")
        result.st_cloud_commands = sum(1 for e in ilog if e.get("direction") == "cloud→3D")
        logger.info(f"[ST] Cloud sync stats: pushes={result.st_cloud_pushes}, "
                    f"commands={result.st_cloud_commands}, "
                    f"proximity_toggles={result.st_proximity_toggles}, "
                    f"ngrok={'YES' if result.st_ngrok_connected else 'NO'}")

    # ---- Cleanup ----
    print("\nShutting down …")
    if demo.smartthings_bridge:
        print("[ST-3D] Stopping SmartThings bridge and Docker containers …")
        demo.smartthings_bridge.stop()
    
    # Clean up Docker containers for this scene
    try:
        import subprocess
        result_cleanup = subprocess.run(
            "docker ps -a | grep vesper-3d | awk '{print $1}' | xargs -r docker rm -f 2>/dev/null || true",
            shell=True,
            check=False,
            capture_output=True
        )
        logger.info("Cleaned up Docker containers for this scene")
    except Exception as e:
        logger.warning(f"Docker cleanup warning: {e}")
    
    demo.sim.close()
    if not eval_args.headless and HAS_PYGAME:
        pygame.quit()
    print("Scene evaluation complete.")


# ============================================================================
# HELPERS
# ============================================================================

def _finalise_trial(trial: NavigationTrial, demo: ObjectNavDemo,
                    start_pos, goal_pos):
    """Compute geodesic distance and SPL for a completed trial."""
    if start_pos is None or goal_pos is None:
        return
    try:
        agent_st = demo.agent.get_state()
        end_pos = tuple(agent_st.position)

        # Actual distance walked
        trial.actual_distance = float(np.linalg.norm(
            np.array(end_pos) - np.array(start_pos)
        ))

        # Geodesic distance (shortest path)
        path = habitat_sim.ShortestPath()
        path.requested_start = mn.Vector3(*start_pos)
        path.requested_end = mn.Vector3(*goal_pos)
        found = demo.sim.pathfinder.find_path(path)
        if found and not math.isinf(path.geodesic_distance) and not math.isnan(path.geodesic_distance):
            trial.geodesic_distance = float(path.geodesic_distance)

        # SPL = S_i * (l_i / max(p_i, l_i))
        if trial.success and trial.geodesic_distance > 0 and trial.actual_distance > 0:
            trial.spl = trial.geodesic_distance / max(trial.actual_distance, trial.geodesic_distance)
        else:
            trial.spl = 0.0

    except Exception as e:
        logger.warning(f"_finalise_trial error: {e}")


def _guess_current_room(pos, room_pos_dict):
    """Return the name of the closest room to the given position."""
    best_room, best_dist = "unknown", float("inf")
    for room, rpos in room_pos_dict.items():
        d = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos, rpos)))
        if d < best_dist:
            best_dist = d
            best_room = room
    return best_room


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error("=" * 80)
        logger.error("FATAL ERROR - Evaluation crashed!")
        logger.exception(f"Exception: {e}")
        logger.error("=" * 80)
        raise
