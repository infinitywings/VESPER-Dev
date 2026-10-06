"""
Matter Device — Represents a discovered Matter node in VESPER.

Each ``MatterDeviceNode`` corresponds to a physical or virtual Matter
device (a "node" in Matter terminology). A node contains one or more
*endpoints*, each with its own set of CHIP clusters (OnOff, LevelControl,
DoorLock, TemperatureMeasurement, etc.).

This module extracts the information that VESPER cares about from the raw
``MatterNode`` / ``MatterEndpoint`` objects returned by python-matter-server,
and maps them to VESPER-friendly device categories.

Reference:
    homeassistant/components/matter/adapter.py  → _create_device_registry
    homeassistant/components/matter/discovery.py → async_discover_entities
    homeassistant/components/matter/helpers.py   → get_device_id
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .const import (
    CLUSTER_ID_BASIC_INFORMATION,
    CLUSTER_ID_BOOLEAN_STATE,
    CLUSTER_ID_COLOR_CONTROL,
    CLUSTER_ID_DOOR_LOCK,
    CLUSTER_ID_FAN_CONTROL,
    CLUSTER_ID_ILLUMINANCE_MEASUREMENT,
    CLUSTER_ID_LEVEL_CONTROL,
    CLUSTER_ID_OCCUPANCY_SENSING,
    CLUSTER_ID_ON_OFF,
    CLUSTER_ID_RELATIVE_HUMIDITY_MEASUREMENT,
    CLUSTER_ID_TEMPERATURE_MEASUREMENT,
    CLUSTER_ID_THERMOSTAT,
    CLUSTER_NAMES,
    DEVICE_TYPE_NAMES,
    MATTER_TYPE_TO_VESPER,
)

logger = logging.getLogger(__name__)


@dataclass
class MatterEndpointInfo:
    """
    Summarised view of a single Matter endpoint.

    Each endpoint on a node acts like a separate "sub-device". A typical
    light bulb has one endpoint (1) with OnOff + LevelControl clusters.
    A multi-socket power strip may have endpoints 1..4 each with OnOff.
    """

    endpoint_id: int
    device_types: list[int] = field(default_factory=list)
    device_type_names: list[str] = field(default_factory=list)
    cluster_ids: list[int] = field(default_factory=list)
    cluster_names: list[str] = field(default_factory=list)
    vesper_category: str = "unknown"
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class MatterDeviceNode:
    """
    A VESPER-friendly representation of a Matter node.

    Created by ``MatterAdapter.discover_nodes()`` from the raw
    ``MatterNode`` objects returned by python-matter-server.
    """

    # ── Identity ───────────────────────────────────────────────────────
    node_id: int
    vendor_id: int = 0
    vendor_name: str = ""
    product_id: int = 0
    product_name: str = ""
    serial_number: str = ""
    unique_id: str = ""

    # ── Human-readable info ────────────────────────────────────────────
    name: str = ""
    model: str = ""
    hw_version: str = ""
    sw_version: str = ""

    # ── Network ────────────────────────────────────────────────────────
    ip_addresses: list[str] = field(default_factory=list)
    network_type: str = ""    # "wifi", "thread", or "ethernet"
    fabric_id: str = ""

    # ── Status ─────────────────────────────────────────────────────────
    available: bool = True
    is_bridge: bool = False

    # ── Endpoints ──────────────────────────────────────────────────────
    endpoints: list[MatterEndpointInfo] = field(default_factory=list)

    # The "primary" VESPER category (derived from endpoint 1, usually)
    primary_vesper_category: str = "unknown"

    @property
    def all_cluster_ids(self) -> set[int]:
        """Union of cluster IDs across all endpoints."""
        ids: set[int] = set()
        for ep in self.endpoints:
            ids.update(ep.cluster_ids)
        return ids

    @property
    def all_device_type_ids(self) -> set[int]:
        """Union of device type IDs across all endpoints."""
        ids: set[int] = set()
        for ep in self.endpoints:
            ids.update(ep.device_types)
        return ids

    @property
    def supports_on_off(self) -> bool:
        return CLUSTER_ID_ON_OFF in self.all_cluster_ids

    @property
    def supports_level_control(self) -> bool:
        return CLUSTER_ID_LEVEL_CONTROL in self.all_cluster_ids

    @property
    def supports_color(self) -> bool:
        return CLUSTER_ID_COLOR_CONTROL in self.all_cluster_ids

    @property
    def supports_lock(self) -> bool:
        return CLUSTER_ID_DOOR_LOCK in self.all_cluster_ids

    @property
    def supports_thermostat(self) -> bool:
        return CLUSTER_ID_THERMOSTAT in self.all_cluster_ids

    def to_dict(self) -> dict[str, Any]:
        """Serialise for dashboard / JSON logging."""
        return {
            "node_id": self.node_id,
            "name": self.name,
            "vendor": f"{self.vendor_name} ({hex(self.vendor_id)})",
            "product": f"{self.product_name} ({hex(self.product_id)})",
            "model": self.model,
            "serial": self.serial_number,
            "hw": self.hw_version,
            "sw": self.sw_version,
            "network": self.network_type,
            "available": self.available,
            "is_bridge": self.is_bridge,
            "primary_category": self.primary_vesper_category,
            "endpoints": [
                {
                    "id": ep.endpoint_id,
                    "device_types": ep.device_type_names,
                    "clusters": ep.cluster_names,
                    "vesper_category": ep.vesper_category,
                    "state": ep.state,
                }
                for ep in self.endpoints
            ],
        }


# ═══════════════════════════════════════════════════════════════════════
# Factory — Build MatterDeviceNode from raw python-matter-server objects
# ═══════════════════════════════════════════════════════════════════════

def build_device_from_node(raw_node: Any) -> MatterDeviceNode:
    """
    Construct a ``MatterDeviceNode`` from a raw ``MatterNode``.

    This mirrors the logic in HA's ``MatterAdapter._create_device_registry``
    and ``MatterAdapter._setup_endpoint``.

    The raw_node comes from ``matter_server.client.models.node.MatterNode``
    and has:
        - raw_node.node_id: int
        - raw_node.available: bool
        - raw_node.device_info: BasicInformation cluster data
        - raw_node.endpoints: dict[int, MatterEndpoint]
    """
    info = raw_node.device_info

    device = MatterDeviceNode(
        node_id=raw_node.node_id,
        available=raw_node.available,
        vendor_id=getattr(info, "vendorID", 0),
        vendor_name=getattr(info, "vendorName", "") or "",
        product_id=getattr(info, "productID", 0),
        product_name=getattr(info, "productName", "") or "",
        serial_number=getattr(info, "serialNumber", "") or "",
        unique_id=getattr(info, "uniqueID", "") or "",
        hw_version=getattr(info, "hardwareVersionString", "") or "",
        sw_version=getattr(info, "softwareVersionString", "") or "",
    )

    # Name resolution: nodeLabel > productLabel > productName
    device.name = (
        _clean(getattr(info, "nodeLabel", ""))
        or _clean(getattr(info, "productLabel", ""))
        or _clean(getattr(info, "productName", ""))
        or f"Matter Node {raw_node.node_id}"
    )
    device.model = _clean(getattr(info, "productName", "")) or "Unknown"

    # ── Build endpoint info ────────────────────────────────────────────
    primary_category = "unknown"

    for ep_id, raw_ep in raw_node.endpoints.items():
        ep_info = _build_endpoint_info(ep_id, raw_ep)
        device.endpoints.append(ep_info)

        # Determine primary category from the first non-root endpoint
        if ep_id > 0 and primary_category == "unknown":
            primary_category = ep_info.vesper_category

        # Check if this is a bridge device (endpoint 0 root node indicator)
        if ep_id == 0:
            for dt in getattr(raw_ep, "device_types", []):
                dt_id = getattr(dt, "device_type", 0)
                if dt_id == 0x000E:  # Aggregator / Bridge
                    device.is_bridge = True

    device.primary_vesper_category = primary_category

    # ── Extract IP addresses (if server_info provides them) ────────────
    if hasattr(raw_node, "ip_addresses"):
        device.ip_addresses = list(raw_node.ip_addresses or [])

    return device


def _build_endpoint_info(ep_id: int, raw_ep: Any) -> MatterEndpointInfo:
    """Build an ``MatterEndpointInfo`` from a raw ``MatterEndpoint``."""
    ep = MatterEndpointInfo(endpoint_id=ep_id)

    # Device types on this endpoint
    for dt in getattr(raw_ep, "device_types", []):
        dt_id = getattr(dt, "device_type", 0)
        ep.device_types.append(dt_id)
        ep.device_type_names.append(
            DEVICE_TYPE_NAMES.get(dt_id, f"0x{dt_id:04X}")
        )

    # Clusters present on this endpoint
    # raw_ep.clusters is a dict of cluster_id -> cluster object
    clusters_dict = getattr(raw_ep, "clusters", {})
    if isinstance(clusters_dict, dict):
        for cid in clusters_dict:
            ep.cluster_ids.append(cid)
            ep.cluster_names.append(
                CLUSTER_NAMES.get(cid, f"0x{cid:04X}")
            )
    # Alternative: clusters might be stored differently
    elif hasattr(raw_ep, "clusters_by_id"):
        for cid in raw_ep.clusters_by_id:
            ep.cluster_ids.append(cid)
            ep.cluster_names.append(
                CLUSTER_NAMES.get(cid, f"0x{cid:04X}")
            )

    # Map to VESPER category
    ep.vesper_category = _infer_vesper_category(ep.device_types, ep.cluster_ids)

    # Extract current state from cluster attributes
    ep.state = _extract_state(clusters_dict)

    return ep


def _infer_vesper_category(
    device_type_ids: list[int],
    cluster_ids: list[int],
) -> str:
    """
    Determine the VESPER device category from Matter device types / clusters.

    Priority: exact device-type match → cluster-based inference.
    This mirrors HA's discovery schema matching logic.
    """
    # 1. Try exact device type match
    for dt_id in device_type_ids:
        if dt_id in MATTER_TYPE_TO_VESPER:
            return MATTER_TYPE_TO_VESPER[dt_id]

    # 2. Fall back to cluster-based inference
    cids = set(cluster_ids)

    if CLUSTER_ID_DOOR_LOCK in cids:
        return "smart_door"
    if CLUSTER_ID_THERMOSTAT in cids:
        return "thermostat"
    if CLUSTER_ID_TEMPERATURE_MEASUREMENT in cids:
        return "temperature_sensor"
    if CLUSTER_ID_RELATIVE_HUMIDITY_MEASUREMENT in cids:
        return "humidity_sensor"
    if CLUSTER_ID_OCCUPANCY_SENSING in cids:
        return "motion_sensor"
    if CLUSTER_ID_BOOLEAN_STATE in cids:
        return "door_sensor"
    if CLUSTER_ID_ILLUMINANCE_MEASUREMENT in cids:
        return "light_sensor"
    if CLUSTER_ID_FAN_CONTROL in cids:
        return "fan"
    if CLUSTER_ID_COLOR_CONTROL in cids:
        return "smart_light"
    if CLUSTER_ID_LEVEL_CONTROL in cids:
        return "smart_light"
    if CLUSTER_ID_ON_OFF in cids:
        return "smart_plug"  # bare on/off without level → plug

    return "unknown"


def _extract_state(clusters_dict: Any) -> dict[str, Any]:
    """
    Read current attribute values from cluster data.

    We extract the most commonly used attributes for VESPER logging.
    """
    state: dict[str, Any] = {}
    if not isinstance(clusters_dict, dict):
        return state

    # OnOff cluster
    on_off = clusters_dict.get(CLUSTER_ID_ON_OFF)
    if on_off and hasattr(on_off, "onOff"):
        state["on"] = bool(on_off.onOff)

    # LevelControl
    level = clusters_dict.get(CLUSTER_ID_LEVEL_CONTROL)
    if level and hasattr(level, "currentLevel"):
        state["brightness"] = level.currentLevel

    # TemperatureMeasurement
    temp = clusters_dict.get(CLUSTER_ID_TEMPERATURE_MEASUREMENT)
    if temp and hasattr(temp, "measuredValue"):
        val = temp.measuredValue
        if val is not None:
            state["temperature_c"] = val / 100.0  # centi-degrees

    # RelativeHumidity
    hum = clusters_dict.get(CLUSTER_ID_RELATIVE_HUMIDITY_MEASUREMENT)
    if hum and hasattr(hum, "measuredValue"):
        val = hum.measuredValue
        if val is not None:
            state["humidity_pct"] = val / 100.0

    # OccupancySensing
    occ = clusters_dict.get(CLUSTER_ID_OCCUPANCY_SENSING)
    if occ and hasattr(occ, "occupancy"):
        state["occupied"] = bool(occ.occupancy & 0x01)

    # BooleanState (contact sensor)
    bs = clusters_dict.get(CLUSTER_ID_BOOLEAN_STATE)
    if bs and hasattr(bs, "stateValue"):
        state["contact"] = bool(bs.stateValue)

    # DoorLock
    dl = clusters_dict.get(CLUSTER_ID_DOOR_LOCK)
    if dl and hasattr(dl, "lockState"):
        lock_val = dl.lockState
        # 1 = locked, 2 = unlocked, 3 = unlatched
        state["locked"] = lock_val == 1

    # Thermostat
    therm = clusters_dict.get(CLUSTER_ID_THERMOSTAT)
    if therm:
        if hasattr(therm, "localTemperature") and therm.localTemperature is not None:
            state["local_temperature_c"] = therm.localTemperature / 100.0
        if (
            hasattr(therm, "occupiedCoolingSetpoint")
            and therm.occupiedCoolingSetpoint is not None
        ):
            state["cooling_setpoint_c"] = therm.occupiedCoolingSetpoint / 100.0
        if (
            hasattr(therm, "occupiedHeatingSetpoint")
            and therm.occupiedHeatingSetpoint is not None
        ):
            state["heating_setpoint_c"] = therm.occupiedHeatingSetpoint / 100.0

    return state


def _clean(name: Optional[str]) -> str:
    """Strip null chars and whitespace from node label strings."""
    if not name:
        return ""
    return name.replace("\x00", "").strip()
