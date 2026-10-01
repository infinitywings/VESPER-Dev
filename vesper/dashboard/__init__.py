"""
VESPER Dashboard — Web UI for monitoring and controlling the VESPER platform.

Provides a real-time web interface for:
- Network topology visualization
- Device status monitoring
- Traffic/packet inspection
- Attack management
- Hub and Matter device overview
"""

from vesper.dashboard.app import create_app, DashboardServer

__all__ = ["create_app", "DashboardServer"]
