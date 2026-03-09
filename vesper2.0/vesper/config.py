"""
Configuration management for Vesper.

Loads YAML configuration files and provides typed access to settings.
"""

from pathlib import Path
from typing import Any, Optional, Union
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


class Config(BaseModel):
    """Root configuration for Vesper."""
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    devices: DevicesConfig = Field(default_factory=DevicesConfig)
    event_bus: EventBusConfig = Field(default_factory=EventBusConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(config_path: Optional[Union[str, Path]] = None) -> Config:
    """
    Load configuration from a YAML file.
    
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
