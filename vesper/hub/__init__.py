"""
VESPER Hub Layer — Central device management and traffic routing.

The Hub serves as the central point for all device communication in VESPER,
routing traffic between virtual devices, Matter devices, Home Assistant,
and the emulated WiFi network. All traffic passes through the Hub,
enabling packet inspection, attack injection, and protocol translation.

Components:
    - VirtualHub: Software hub that aggregates all protocols (Matter, SmartThings, HTTP)
    - PhysicalHub: Bridge to a physical Aeotec SmartThings hub via the SmartThings API
    - HubManager: Lifecycle management and hub selection
"""

from vesper.hub.base import BaseHub, HubState, HubCapability
from vesper.hub.virtual_hub import VirtualHub
from vesper.hub.physical_hub import PhysicalHub
from vesper.hub.manager import HubManager

__all__ = [
    "BaseHub",
    "HubState",
    "HubCapability",
    "VirtualHub",
    "PhysicalHub",
    "HubManager",
]
