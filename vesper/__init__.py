"""
VESPER: activity-conditioned smart-home experimentation.

A platform with stateful device models, optional Habitat/LLM workflows,
platform connectors, and optional Linux network emulation. The core demo
uses a mock environment and does not exercise these external services.

Modules:
- hub: Centralized device routing (VirtualHub, PhysicalHub)
- matter: Matter/CHIP device integration via Home Assistant
- dashboard: Real-time Web UI for monitoring & control
- habitat: 3D environment simulation (Habitat 3.0)
- simulation: Time management, task system, event streaming
- integrations: External platform integrations (SmartThings)
"""

__version__ = "0.1.0"

from vesper.config import Config, load_config

def __getattr__(name):
    # Importing configuration or numerical sensor models must not initialize
    # the optional Habitat/platform integration tree.
    if name == "VesperEngine":
        from vesper.engine import VesperEngine
        globals()[name] = VesperEngine
        return VesperEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "__version__",
    "Config",
    "load_config",
    "VesperEngine",
]
