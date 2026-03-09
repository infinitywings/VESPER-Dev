"""
QEMU Runner for IoT Firmware Simulation.

Provides managed QEMU instances for running IoT device firmware with
support for multiple MCU architectures and communication protocols.

Supported Architectures:
- ARM Cortex-M (STM32, nRF52, LM3S6965)
- ESP32 (via espressif/qemu)
- RISC-V (SiFive HiFive1)
- AVR (Arduino - limited)

Communication Modes:
- Serial/UART via PTY
- TCP/UDP networking
- Semihosting for debug output
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pty
import queue
import re
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class Architecture(str, Enum):
    """Supported MCU architectures."""
    ARM_CORTEX_M0 = "cortex-m0"
    ARM_CORTEX_M3 = "cortex-m3"
    ARM_CORTEX_M4 = "cortex-m4"
    ARM_CORTEX_M7 = "cortex-m7"
    ESP32 = "esp32"
    ESP32_C3 = "esp32c3"  # RISC-V based
    RISCV32 = "riscv32"
    RISCV64 = "riscv64"
    AVR = "avr"


class BoardType(str, Enum):
    """Predefined board configurations."""
    # ARM Cortex-M boards
    LM3S6965 = "lm3s6965evb"        # Texas Instruments Stellaris (Cortex-M3)
    STM32F4_DISCOVERY = "stm32f4-discovery"  # STMicro Discovery (Cortex-M4)
    STM32_VLDISCOVERY = "stm32vldiscovery"   # STMicro VL Discovery (Cortex-M3)
    NRF52840 = "microbit"           # Nordic nRF52840 (Cortex-M4)
    NETDUINO2 = "netduino2"         # Netduino Plus 2 (Cortex-M4)
    
    # ESP32 boards (requires espressif/qemu)
    ESP32_DEVKIT = "esp32"
    ESP32_C3_DEVKIT = "esp32c3"
    
    # RISC-V boards
    SIFIVE_E = "sifive_e"           # SiFive HiFive1 (RV32)
    SIFIVE_U = "sifive_u"           # SiFive HiFive Unleashed (RV64)
    
    # Generic
    VIRT_ARM = "virt"               # Generic ARM virt machine
    VIRT_RISCV = "virt"             # Generic RISC-V virt machine


@dataclass
class QEMUConfig:
    """Configuration for QEMU instance."""
    # Board selection
    board: BoardType = BoardType.LM3S6965
    architecture: Architecture = Architecture.ARM_CORTEX_M3
    
    # Firmware
    firmware_path: Optional[str] = None
    firmware_format: str = "elf"  # elf, bin, hex
    flash_base: int = 0x00000000  # Flash memory base address
    
    # Memory
    ram_size: str = "128K"        # RAM size (K, M, G)
    flash_size: str = "256K"      # Flash size
    
    # Serial/UART
    enable_serial: bool = True
    serial_port: int = 0          # UART number
    serial_baud: int = 115200
    
    # Networking
    enable_network: bool = False
    network_mode: str = "user"    # user, tap, bridge
    mac_address: Optional[str] = None
    host_port: int = 5555
    guest_port: int = 5555
    
    # Debug
    enable_gdb: bool = False
    gdb_port: int = 1234
    enable_semihosting: bool = True
    trace_file: Optional[str] = None
    
    # Runtime
    timeout: float = 60.0
    auto_restart: bool = False
    headless: bool = True
    
    # Custom QEMU
    qemu_path: Optional[str] = None  # Custom QEMU binary
    extra_args: List[str] = field(default_factory=list)


class QEMUState(str, Enum):
    """QEMU process state."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    CRASHED = "crashed"
    TIMEOUT = "timeout"


@dataclass
class SerialMessage:
    """Message from/to firmware via serial."""
    data: bytes
    timestamp: float
    direction: str  # "rx" or "tx"
    
    def decode(self, encoding: str = "utf-8") -> str:
        """Decode bytes to string."""
        try:
            return self.data.decode(encoding).strip()
        except UnicodeDecodeError:
            return self.data.hex()


class QEMURunner:
    """
    Managed QEMU instance for running IoT firmware.
    
    Features:
    - Automatic QEMU binary detection
    - Serial communication via PTY
    - Network bridging
    - GDB debugging support
    - Process lifecycle management
    
    Example:
        config = QEMUConfig(
            board=BoardType.STM32F4_DISCOVERY,
            firmware_path="firmware/stm32_sensor.elf",
            enable_serial=True,
        )
        
        runner = QEMURunner(config)
        await runner.start()
        
        # Send command to firmware
        await runner.serial_write(b"GET_TEMP\\n")
        
        # Read response
        response = await runner.serial_read()
        print(f"Temperature: {response}")
        
        await runner.stop()
    """
    
    # QEMU binary names by architecture
    QEMU_BINARIES = {
        Architecture.ARM_CORTEX_M0: "qemu-system-arm",
        Architecture.ARM_CORTEX_M3: "qemu-system-arm",
        Architecture.ARM_CORTEX_M4: "qemu-system-arm",
        Architecture.ARM_CORTEX_M7: "qemu-system-arm",
        Architecture.ESP32: "qemu-system-xtensa",
        Architecture.ESP32_C3: "qemu-system-riscv32",
        Architecture.RISCV32: "qemu-system-riscv32",
        Architecture.RISCV64: "qemu-system-riscv64",
        Architecture.AVR: "qemu-system-avr",
    }
    
    # CPU types by architecture
    CPU_TYPES = {
        Architecture.ARM_CORTEX_M0: "cortex-m0",
        Architecture.ARM_CORTEX_M3: "cortex-m3",
        Architecture.ARM_CORTEX_M4: "cortex-m4f",
        Architecture.ARM_CORTEX_M7: "cortex-m7",
        Architecture.ESP32: "esp32",
        Architecture.ESP32_C3: "esp32c3",
        Architecture.RISCV32: "rv32",
        Architecture.RISCV64: "rv64",
        Architecture.AVR: "avr6",
    }
    
    def __init__(self, config: Optional[QEMUConfig] = None):
        self.config = config or QEMUConfig()
        
        self._state = QEMUState.STOPPED
        self._process: Optional[subprocess.Popen] = None
        
        # Serial communication
        self._master_fd: Optional[int] = None
        self._slave_fd: Optional[int] = None
        self._serial_path: Optional[str] = None
        self._serial_thread: Optional[threading.Thread] = None
        self._rx_queue: queue.Queue = queue.Queue()
        self._tx_queue: queue.Queue = queue.Queue()
        
        # Callbacks
        self._on_serial_rx: List[Callable[[bytes], None]] = []
        self._on_state_change: List[Callable[[QEMUState], None]] = []
        self._on_exit: List[Callable[[int], None]] = []
        
        # Stats
        self._stats = {
            "start_time": 0.0,
            "bytes_rx": 0,
            "bytes_tx": 0,
            "messages_rx": 0,
            "messages_tx": 0,
            "restarts": 0,
        }
        
        # Temp directory for runtime files
        self._temp_dir: Optional[tempfile.TemporaryDirectory] = None
    
    @property
    def state(self) -> QEMUState:
        return self._state
    
    @property
    def is_running(self) -> bool:
        return self._state == QEMUState.RUNNING
    
    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None
    
    @property
    def uptime(self) -> float:
        if self._stats["start_time"] == 0:
            return 0.0
        return time.time() - self._stats["start_time"]
    
    def _find_qemu_binary(self) -> Optional[str]:
        """Find the appropriate QEMU binary."""
        if self.config.qemu_path:
            if os.path.isfile(self.config.qemu_path):
                return self.config.qemu_path
            # Try as command name
            if shutil.which(self.config.qemu_path):
                return self.config.qemu_path
        
        # Get binary name for architecture
        binary_name = self.QEMU_BINARIES.get(self.config.architecture)
        if binary_name:
            path = shutil.which(binary_name)
            if path:
                return path
        
        # Fallback: try common QEMU paths
        common_paths = [
            "/usr/bin",
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/opt/local/bin",
            os.path.expanduser("~/.local/bin"),
        ]
        
        for base in common_paths:
            candidate = os.path.join(base, binary_name or "qemu-system-arm")
            if os.path.isfile(candidate):
                return candidate
        
        return None
    
    def _build_command(self) -> List[str]:
        """Build QEMU command line."""
        qemu = self._find_qemu_binary()
        if not qemu:
            raise RuntimeError(f"QEMU binary not found for {self.config.architecture}")
        
        cmd = [qemu]
        
        # Machine/Board
        cmd.extend(["-machine", self.config.board.value])
        
        # CPU
        cpu = self.CPU_TYPES.get(self.config.architecture)
        if cpu:
            cmd.extend(["-cpu", cpu])
        
        # Memory
        cmd.extend(["-m", self.config.ram_size])
        
        # Headless mode
        if self.config.headless:
            cmd.append("-nographic")
        
        # Firmware
        if self.config.firmware_path:
            firmware = Path(self.config.firmware_path)
            if not firmware.exists():
                raise FileNotFoundError(f"Firmware not found: {firmware}")
            
            if self.config.firmware_format == "elf":
                cmd.extend(["-kernel", str(firmware)])
            elif self.config.firmware_format == "bin":
                cmd.extend(["-device", f"loader,file={firmware},addr={self.config.flash_base:#x}"])
            else:
                cmd.extend(["-kernel", str(firmware)])
        
        # Serial
        if self.config.enable_serial and self._serial_path:
            cmd.extend(["-serial", f"pty"])
            # Will capture PTY path from QEMU output
        
        # Semihosting (for printf debugging)
        if self.config.enable_semihosting:
            cmd.extend(["-semihosting-config", "enable=on,target=native"])
        
        # Network
        if self.config.enable_network:
            mac = self.config.mac_address or self._generate_mac()
            if self.config.network_mode == "user":
                cmd.extend([
                    "-netdev", f"user,id=net0,hostfwd=tcp::{self.config.host_port}-:{self.config.guest_port}",
                    "-device", f"virtio-net-device,netdev=net0,mac={mac}",
                ])
        
        # GDB
        if self.config.enable_gdb:
            cmd.extend(["-gdb", f"tcp::{self.config.gdb_port}"])
            cmd.append("-S")  # Wait for GDB connection
        
        # Trace
        if self.config.trace_file:
            cmd.extend(["-d", "in_asm,cpu", "-D", self.config.trace_file])
        
        # Extra args
        cmd.extend(self.config.extra_args)
        
        return cmd
    
    def _generate_mac(self) -> str:
        """Generate a random MAC address."""
        import random
        mac = [0x52, 0x54, 0x00]  # QEMU OUI
        mac.extend([random.randint(0, 255) for _ in range(3)])
        return ":".join(f"{b:02x}" for b in mac)
    
    def _setup_pty(self) -> None:
        """Set up PTY for serial communication."""
        self._master_fd, self._slave_fd = pty.openpty()
        self._serial_path = os.ttyname(self._slave_fd)
        logger.debug(f"Created PTY: {self._serial_path}")
    
    def _set_state(self, new_state: QEMUState) -> None:
        """Update state and notify callbacks."""
        old_state = self._state
        self._state = new_state
        
        if old_state != new_state:
            logger.info(f"QEMU state: {old_state.value} -> {new_state.value}")
            for callback in self._on_state_change:
                try:
                    callback(new_state)
                except Exception as e:
                    logger.error(f"State callback error: {e}")
    
    async def start(self) -> bool:
        """Start QEMU process."""
        if self.is_running:
            logger.warning("QEMU already running")
            return True
        
        self._set_state(QEMUState.STARTING)
        
        try:
            # Create temp directory
            self._temp_dir = tempfile.TemporaryDirectory(prefix="vesper_qemu_")
            
            # Set up serial if needed
            if self.config.enable_serial:
                self._setup_pty()
            
            # Build command
            cmd = self._build_command()
            logger.info(f"Starting QEMU: {' '.join(cmd)}")
            
            # Start process
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self._temp_dir.name,
            )
            
            # Start serial reader thread
            if self.config.enable_serial:
                self._serial_thread = threading.Thread(
                    target=self._serial_reader_loop,
                    daemon=True,
                )
                self._serial_thread.start()
            
            # Wait a bit for QEMU to start
            await asyncio.sleep(0.5)
            
            # Check if process is still running
            if self._process.poll() is not None:
                logger.error(f"QEMU exited immediately with code {self._process.returncode}")
                self._set_state(QEMUState.CRASHED)
                return False
            
            self._stats["start_time"] = time.time()
            self._set_state(QEMUState.RUNNING)
            
            logger.info(f"QEMU started (PID: {self._process.pid})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start QEMU: {e}")
            self._set_state(QEMUState.CRASHED)
            return False
    
    def start_sync(self) -> bool:
        """Synchronous start (for non-async code)."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.start())
        finally:
            loop.close()
    
    async def stop(self) -> None:
        """Stop QEMU process."""
        if self._state == QEMUState.STOPPED:
            return
        
        logger.info("Stopping QEMU...")
        
        # Stop serial thread
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except:
                pass
            self._master_fd = None
        
        if self._slave_fd is not None:
            try:
                os.close(self._slave_fd)
            except:
                pass
            self._slave_fd = None
        
        # Stop process
        if self._process:
            exit_code = self._process.poll()
            if exit_code is None:
                # Process still running
                self._process.terminate()
                try:
                    await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None, self._process.wait
                        ),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("QEMU did not terminate, killing...")
                    self._process.kill()
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._process.wait
                    )
                exit_code = self._process.returncode
            
            self._process = None
            
            # Notify exit callbacks
            for callback in self._on_exit:
                try:
                    callback(exit_code)
                except Exception as e:
                    logger.error(f"Exit callback error: {e}")
        
        # Cleanup temp directory
        if self._temp_dir:
            self._temp_dir.cleanup()
            self._temp_dir = None
        
        self._set_state(QEMUState.STOPPED)
        logger.info("QEMU stopped")
    
    def stop_sync(self) -> None:
        """Synchronous stop."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.stop())
        finally:
            loop.close()
    
    def _serial_reader_loop(self) -> None:
        """Read serial data from QEMU."""
        while self._master_fd is not None and self.is_running:
            try:
                # Use select to avoid blocking indefinitely
                readable, _, _ = select.select([self._master_fd], [], [], 0.1)
                if readable:
                    data = os.read(self._master_fd, 1024)
                    if data:
                        self._stats["bytes_rx"] += len(data)
                        self._stats["messages_rx"] += 1
                        
                        # Put in queue
                        self._rx_queue.put(SerialMessage(
                            data=data,
                            timestamp=time.time(),
                            direction="rx",
                        ))
                        
                        # Call callbacks
                        for callback in self._on_serial_rx:
                            try:
                                callback(data)
                            except Exception as e:
                                logger.error(f"Serial RX callback error: {e}")
                                
            except OSError:
                # PTY closed
                break
            except Exception as e:
                logger.error(f"Serial read error: {e}")
                break
    
    async def serial_write(self, data: bytes) -> bool:
        """Write data to firmware via serial."""
        if not self.is_running or self._master_fd is None:
            return False
        
        try:
            os.write(self._master_fd, data)
            self._stats["bytes_tx"] += len(data)
            self._stats["messages_tx"] += 1
            return True
        except Exception as e:
            logger.error(f"Serial write error: {e}")
            return False
    
    def serial_write_sync(self, data: bytes) -> bool:
        """Synchronous serial write."""
        if not self.is_running or self._master_fd is None:
            return False
        
        try:
            os.write(self._master_fd, data)
            self._stats["bytes_tx"] += len(data)
            self._stats["messages_tx"] += 1
            return True
        except Exception as e:
            logger.error(f"Serial write error: {e}")
            return False
    
    async def serial_read(self, timeout: float = 1.0) -> Optional[bytes]:
        """Read data from firmware via serial."""
        try:
            msg = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._rx_queue.get(timeout=timeout)
                ),
                timeout=timeout,
            )
            return msg.data if msg else None
        except (asyncio.TimeoutError, queue.Empty):
            return None
    
    def serial_read_sync(self, timeout: float = 1.0) -> Optional[bytes]:
        """Synchronous serial read."""
        try:
            msg = self._rx_queue.get(timeout=timeout)
            return msg.data if msg else None
        except queue.Empty:
            return None
    
    async def serial_readline(self, timeout: float = 1.0) -> Optional[str]:
        """Read a line from serial."""
        buffer = b""
        start = time.time()
        
        while time.time() - start < timeout:
            data = await self.serial_read(timeout=0.1)
            if data:
                buffer += data
                if b"\n" in buffer:
                    line, _ = buffer.split(b"\n", 1)
                    try:
                        return line.decode("utf-8").strip()
                    except:
                        return line.hex()
        
        return None
    
    async def send_command(self, cmd: str, timeout: float = 2.0) -> Optional[str]:
        """Send command and wait for response."""
        if not cmd.endswith("\n"):
            cmd += "\n"
        
        # Clear RX queue
        while not self._rx_queue.empty():
            try:
                self._rx_queue.get_nowait()
            except:
                break
        
        # Send command
        await self.serial_write(cmd.encode())
        
        # Wait for response
        return await self.serial_readline(timeout=timeout)
    
    def on_serial_rx(self, callback: Callable[[bytes], None]) -> None:
        """Register serial RX callback."""
        self._on_serial_rx.append(callback)
    
    def on_state_change(self, callback: Callable[[QEMUState], None]) -> None:
        """Register state change callback."""
        self._on_state_change.append(callback)
    
    def on_exit(self, callback: Callable[[int], None]) -> None:
        """Register exit callback."""
        self._on_exit.append(callback)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get runtime statistics."""
        return {
            "state": self._state.value,
            "pid": self.pid,
            "uptime": self.uptime,
            **self._stats,
        }
    
    async def __aenter__(self) -> "QEMURunner":
        await self.start()
        return self
    
    async def __aexit__(self, *args) -> None:
        await self.stop()
    
    def __enter__(self) -> "QEMURunner":
        self.start_sync()
        return self
    
    def __exit__(self, *args) -> None:
        self.stop_sync()


# Convenience functions for common boards
def create_stm32_runner(firmware_path: str, **kwargs) -> QEMURunner:
    """Create runner for STM32F4 firmware."""
    config = QEMUConfig(
        board=BoardType.STM32F4_DISCOVERY,
        architecture=Architecture.ARM_CORTEX_M4,
        firmware_path=firmware_path,
        ram_size="128K",
        **kwargs,
    )
    return QEMURunner(config)


def create_nrf52_runner(firmware_path: str, **kwargs) -> QEMURunner:
    """Create runner for nRF52 firmware."""
    config = QEMUConfig(
        board=BoardType.NRF52840,
        architecture=Architecture.ARM_CORTEX_M4,
        firmware_path=firmware_path,
        ram_size="256K",
        **kwargs,
    )
    return QEMURunner(config)


def create_esp32_runner(firmware_path: str, **kwargs) -> QEMURunner:
    """Create runner for ESP32 firmware (requires espressif/qemu)."""
    config = QEMUConfig(
        board=BoardType.ESP32_DEVKIT,
        architecture=Architecture.ESP32,
        firmware_path=firmware_path,
        qemu_path="qemu-system-xtensa",  # Espressif QEMU
        **kwargs,
    )
    return QEMURunner(config)


def create_riscv_runner(firmware_path: str, **kwargs) -> QEMURunner:
    """Create runner for RISC-V firmware."""
    config = QEMUConfig(
        board=BoardType.SIFIVE_E,
        architecture=Architecture.RISCV32,
        firmware_path=firmware_path,
        **kwargs,
    )
    return QEMURunner(config)
