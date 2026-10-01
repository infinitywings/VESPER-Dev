"""
Configuration management for Vesper.

Loads YAML configuration files and provides typed access to settings.
"""

from pathlib import Path
import os
from typing import Any, Literal, Optional, Union
import yaml
from pydantic import BaseModel, Field


class MotionSensorConfig(BaseModel):
    """Configuration for motion sensors."""
    enabled: bool = Field(default=True, description="Enable this device type")
    detection_radius: float = Field(default=3.0, description="Detection radius in meters")
    cooldown: float = Field(default=2.0, description="Cooldown between triggers in seconds")
    fov_vertical: float = Field(default=90.0, description="Vertical FOV in degrees")


class ContactSensorConfig(BaseModel):
    """Configuration for contact sensors."""
    enabled: bool = Field(default=True, description="Enable this device type")
    debounce: float = Field(default=0.1, description="Debounce time in seconds")


class SmartDoorConfig(BaseModel):
    """Configuration for smart doors."""
    enabled: bool = Field(default=True, description="Enable this device type")
    transition_time: float = Field(default=1.5, description="Time to open/close in seconds")
    auto_close: float = Field(default=0, description="Auto-close delay (0 = disabled)")


class LightSensorConfig(BaseModel):
    """Configuration for light sensors."""
    enabled: bool = Field(default=True, description="Enable this device type")
    sample_rate: float = Field(default=10.0, description="Sample rate in Hz")
    threshold: float = Field(default=5.0, description="Minimum lux change to trigger event")


class DevicesConfig(BaseModel):
    """Configuration for all IoT devices."""
    motion_sensor: MotionSensorConfig = Field(default_factory=MotionSensorConfig)
    contact_sensor: ContactSensorConfig = Field(default_factory=ContactSensorConfig)
    smart_door: SmartDoorConfig = Field(default_factory=SmartDoorConfig)
    light_sensor: LightSensorConfig = Field(default_factory=LightSensorConfig)


class SimulationConfig(BaseModel):
    """Configuration for the simulation engine."""
    tick_rate: int = Field(default=30, description="Simulation tick rate in Hz")
    max_agents: int = Field(default=2, description="Maximum number of humanoid agents")
    headless: bool = Field(default=False, description="Run without rendering")
    seed: int = Field(default=42, description="Random seed for reproducibility")
    strict_mode: bool = Field(default=True, description="Strict architecture invariant enforcement (Section 2.5)")


class EnvironmentConfig(BaseModel):
    """Configuration for the environment."""
    dataset: str = Field(default="hssd-hab", description="Dataset to use")
    scene: str = Field(default="default", description="Scene identifier")
    physics: str = Field(default="bullet", description="Physics engine")


class EventBusConfig(BaseModel):
    """Configuration for the event bus."""
    max_queue_size: int = Field(default=1000, description="Maximum queue size")
    logging: bool = Field(default=True, description="Enable event logging")
    log_file: str = Field(default="logs/events.jsonl", description="Log file path")


class LoggingConfig(BaseModel):
    """Configuration for logging."""
    level: str = Field(default="INFO", description="Log level")
    format: str = Field(
        default="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        description="Log format"
    )


class HubConfig(BaseModel):
    """Configuration for the Hub layer."""
    enabled: bool = Field(default=True, description="Enable the hub subsystem")
    hub_type: Literal["virtual", "physical"] = Field(
        default="virtual", description="Hub implementation type"
    )
    matter_bridge_url: str = Field(
        default="http://localhost:8484",
        description="Matter bridge REST API URL"
    )
    ha_url: str = Field(
        default="http://localhost:8123",
        description="Home Assistant URL"
    )
    ha_token: Optional[str] = Field(
        default_factory=lambda: os.getenv("HA_TOKEN") or None,
        description="Home Assistant long-lived access token"
    )
    smartthings_token: Optional[str] = Field(
        default_factory=lambda: os.getenv("SMARTTHINGS_TOKEN") or None,
        description="SmartThings Personal Access Token"
    )
    smartthings_location_id: Optional[str] = Field(
        default=None, description="SmartThings location ID (physical hub)"
    )
    poll_interval: float = Field(
        default=5.0, description="State polling interval in seconds"
    )


class MatterConfig(BaseModel):
    """Configuration for Matter device integration."""
    enabled: bool = Field(default=False, description="Enable Matter integration")
    matter_server_url: str = Field(
        default="ws://localhost:5580/ws",
        description="python-matter-server WebSocket URL"
    )
    auto_discover: bool = Field(
        default=True, description="Discover Matter devices on startup"
    )


class DashboardConfig(BaseModel):
    """Configuration for the Web UI dashboard."""
    enabled: bool = Field(default=True, description="Enable the dashboard")
    host: str = Field(default="127.0.0.1", description="Dashboard bind address")
    port: int = Field(default=8080, description="Dashboard port")


# ── Network Config (NEW — parses YAML `network:` section) ─────────────────

class WiFiNetworkConfig(BaseModel):
    """WiFi network parameters for the emulated network."""
    enabled: bool = Field(default=True, description="Enable WiFi emulation")
    ssid: str = Field(default="VESPER-IoT-Network", description="WiFi SSID")
    password: str = Field(default="vesper-secure-2026", description="WiFi password")
    channel: int = Field(default=6, description="WiFi channel")
    mode: str = Field(default="g", description="802.11 mode (g/n)")
    encrypt: str = Field(default="wpa2", description="Encryption type")


class MatterBridgeNetConfig(BaseModel):
    """Matter bridge network settings."""
    host: str = Field(default="192.168.4.1", description="Bridge host IP")
    port: int = Field(default=8484, description="Bridge REST API port")
    commissioning_port: int = Field(default=5540, description="Matter commissioning port")
    tls_enabled: bool = Field(default=False, description="Enable TLS on bridge")


class FirewallConfig(BaseModel):
    """Firewall configuration for the emulated network."""
    enabled: bool = Field(default=True, description="Enable firewall rules")
    ap_isolation: bool = Field(default=False, description="Isolate WiFi stations")
    syn_flood_protection: bool = Field(default=True, description="Enable SYN flood protection")
    syn_rate_limit: str = Field(default="25/sec", description="SYN rate limit")
    syn_burst: int = Field(default=50, description="SYN burst limit")
    icmp_rate_limit: str = Field(default="10/sec", description="ICMP rate limit")
    allowed_services: list = Field(
        default_factory=lambda: [53, 67, 123, 8484, 443, 5540],
        description="Allowed service ports",
    )
    drop_invalid: bool = Field(default=True, description="Drop invalid packets")
    log_dropped: bool = Field(default=True, description="Log dropped packets")
    log_prefix: str = Field(default="VESPER-FW-DROP: ", description="Log prefix")


class WiresharkConfig(BaseModel):
    """Packet capture configuration."""
    enabled: bool = Field(default=False, description="Enable packet capture")
    capture_interface: str = Field(default="ap1-wlan1", description="Capture interface")
    capture_filter: str = Field(default="", description="BPF capture filter")
    pcap_dir: str = Field(default="results/pcap", description="Directory for pcap files")


class NetworkConfig(BaseModel):
    """Configuration for the emulated network layer."""
    wifi: WiFiNetworkConfig = Field(default_factory=WiFiNetworkConfig)
    subnet: str = Field(default="192.168.4.0/24", description="Network subnet")
    gateway: str = Field(default="192.168.4.1", description="Gateway IP")
    matter_bridge: MatterBridgeNetConfig = Field(default_factory=MatterBridgeNetConfig)
    firewall: FirewallConfig = Field(default_factory=FirewallConfig)
    wireshark: WiresharkConfig = Field(default_factory=WiresharkConfig)


# ── Firmware Config (NEW — parses YAML `firmware:` section) ────────────────

class FreeRTOSConfig(BaseModel):
    """FreeRTOS configuration for ESP32 firmware."""
    unicore: bool = Field(default=True, description="Run on single core")
    tick_rate_hz: int = Field(default=1000, description="FreeRTOS tick rate")


class FirmwareConfig(BaseModel):
    """Configuration for ESP32 firmware emulation."""
    backend: str = Field(default="esp32", description="Firmware backend")
    qemu_binary: str = Field(default="qemu-system-xtensa", description="QEMU binary path")
    esp_idf_version: str = Field(default="v5.2", description="ESP-IDF version")
    serial_port_base: int = Field(default=5561, description="Base serial TCP port")
    smartthings_sdk: bool = Field(default=True, description="Enable SmartThings SDK")
    freertos: FreeRTOSConfig = Field(default_factory=FreeRTOSConfig)


class Config(BaseModel):
    """Root configuration for Vesper."""
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    devices: DevicesConfig = Field(default_factory=DevicesConfig)
    event_bus: EventBusConfig = Field(default_factory=EventBusConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    hub: HubConfig = Field(default_factory=HubConfig)
    matter: MatterConfig = Field(default_factory=MatterConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    firmware: FirmwareConfig = Field(default_factory=FirmwareConfig)


def load_config(config_path: Optional[Union[str, Path]] = None) -> Config:
    """
    Load configuration from a YAML file.

    In strict mode (simulation.strict_mode=True, the default), validates
    that required YAML sections (network, firmware) are explicitly present.

    Args:
        config_path: Path to the YAML config file. If None, uses default config.

    Returns:
        Config object with loaded settings.
    """
    if config_path is None:
        # Use default config from package
        config_path = Path(__file__).parent.parent / "configs" / "default.yaml"

    config_path = Path(config_path)

    if not config_path.exists():
        # Return default config if file doesn't exist
        return Config()

    with open(config_path, "r") as f:
        data = yaml.safe_load(f) or {}
    # Credentials come from the process environment, never a public YAML file.
    hub = data.setdefault("hub", {})
    for variable, field in (("HA_TOKEN", "ha_token"), ("SMARTTHINGS_TOKEN", "smartthings_token")):
        if os.getenv(variable):
            hub[field] = os.environ[variable]

    # Strict-mode validation: ensure required YAML sections are present
    strict = data.get("simulation", {}).get("strict_mode", True)
    if strict:
        missing = [key for key in ("network", "firmware") if key not in data]
        if missing:
            raise ValueError(
                f"STRICT MODE: missing required YAML sections: {', '.join(missing)}"
            )

    return Config(**data)


def save_config(config: Config, config_path: Union[str, Path]) -> None:
    """
    Save configuration to a YAML file.

    Args:
        config: Config object to save.
        config_path: Path to save the YAML file.
    """
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, sort_keys=False)
