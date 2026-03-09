"""
Vesper: Habitat 3.0 + IoT Interactive Simulation Testbed.

A framework for simulating IoT devices in 3D environments with
LLM-controlled agents and network protocol simulation.

Modules:
- habitat.sensors: Realistic IoT sensor models (PIR motion, cameras)
- simulation: Time management, task system, event streaming
- integrations: External platform integrations (SmartThings)
"""

__version__ = "0.2.0"

from vesper.config import Config, load_config

__all__ = [
    "__version__",
    "Config",
    "load_config",
]
