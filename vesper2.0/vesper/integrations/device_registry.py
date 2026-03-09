"""
Device Registry and State Store for VESPER.

Provides centralized storage and management of virtual devices
with SQLite persistence, state history tracking, and event logging.

Features:
- Device registration and metadata storage
- State persistence with SQLite
- State change history with timestamps
- Event logging for audit trails
- Query interface for analytics
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

DEFAULT_DB_PATH = "data/vesper_devices.db"
STATE_HISTORY_RETENTION_DAYS = 30
EVENT_LOG_RETENTION_DAYS = 90


class DeviceCategory(str, Enum):
    """Device categories for organization."""
    LIGHTING = "lighting"
    SECURITY = "security"
    CLIMATE = "climate"
    SENSORS = "sensors"
    LOCKS = "locks"
    APPLIANCES = "appliances"
    OTHER = "other"


class EventType(str, Enum):
    """Event types for logging."""
    DEVICE_CREATED = "device_created"
    DEVICE_UPDATED = "device_updated"
    DEVICE_DELETED = "device_deleted"
    STATE_CHANGED = "state_changed"
    COMMAND_RECEIVED = "command_received"
    COMMAND_EXECUTED = "command_executed"
    SYNC_TO_SMARTTHINGS = "sync_to_smartthings"
    SYNC_FROM_SMARTTHINGS = "sync_from_smartthings"
    ERROR = "error"


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class DeviceMetadata:
    """Metadata for a registered device."""
    
    # Identification
    device_id: str
    device_type: str
    friendly_name: str
    
    # SmartThings mapping
    smartthings_device_id: Optional[str] = None
    smartthings_handler_type: Optional[str] = None
    
    # Container info (if using Docker)
    container_id: Optional[str] = None
    container_status: Optional[str] = None
    
    # Location
    room: Optional[str] = None
    location: Optional[str] = None  # e.g., "Home", "Office"
    
    # Categorization
    category: DeviceCategory = DeviceCategory.OTHER
    tags: List[str] = field(default_factory=list)
    
    # Manufacturer info
    manufacturer: str = "VESPER"
    model: str = "Virtual Device"
    firmware_version: str = "1.0.0"
    
    # Status
    is_online: bool = True
    is_reachable: bool = True
    
    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    
    # Custom attributes
    attributes: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "friendly_name": self.friendly_name,
            "smartthings_device_id": self.smartthings_device_id,
            "smartthings_handler_type": self.smartthings_handler_type,
            "container_id": self.container_id,
            "container_status": self.container_status,
            "room": self.room,
            "location": self.location,
            "category": self.category.value,
            "tags": self.tags,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "firmware_version": self.firmware_version,
            "is_online": self.is_online,
            "is_reachable": self.is_reachable,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "attributes": self.attributes,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeviceMetadata":
        """Create from dictionary."""
        return cls(
            device_id=data["device_id"],
            device_type=data["device_type"],
            friendly_name=data["friendly_name"],
            smartthings_device_id=data.get("smartthings_device_id"),
            smartthings_handler_type=data.get("smartthings_handler_type"),
            container_id=data.get("container_id"),
            container_status=data.get("container_status"),
            room=data.get("room"),
            location=data.get("location"),
            category=DeviceCategory(data.get("category", "other")),
            tags=data.get("tags", []),
            manufacturer=data.get("manufacturer", "VESPER"),
            model=data.get("model", "Virtual Device"),
            firmware_version=data.get("firmware_version", "1.0.0"),
            is_online=data.get("is_online", True),
            is_reachable=data.get("is_reachable", True),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None,
            last_seen_at=datetime.fromisoformat(data["last_seen_at"]) if data.get("last_seen_at") else None,
            attributes=data.get("attributes", {}),
        )


@dataclass
class DeviceState:
    """Current state of a device."""
    device_id: str
    state: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: str = "local"  # "local", "smartthings", "simulation"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "state": self.state,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
        }


@dataclass
class StateHistoryEntry:
    """Historical state entry."""
    id: int
    device_id: str
    attribute: str
    old_value: Any
    new_value: Any
    timestamp: datetime
    source: str


@dataclass
class EventLogEntry:
    """Event log entry."""
    id: int
    device_id: Optional[str]
    event_type: EventType
    message: str
    data: Dict[str, Any]
    timestamp: datetime


# =============================================================================
# Device Registry
# =============================================================================

class DeviceRegistry:
    """
    Central registry for all virtual devices.
    
    Provides:
    - Device registration and metadata management
    - State storage with SQLite persistence
    - State history tracking
    - Event logging for audit trails
    - Query interface for analytics
    
    Usage:
        registry = DeviceRegistry()
        await registry.initialize()
        
        # Register a device
        metadata = DeviceMetadata(
            device_id="switch-001",
            device_type="switch",
            friendly_name="Kitchen Light",
            room="Kitchen",
        )
        await registry.register_device(metadata)
        
        # Update state
        await registry.update_state("switch-001", {"switch": "on"})
        
        # Get state
        state = await registry.get_state("switch-001")
        
        # Query history
        history = await registry.get_state_history("switch-001", hours=24)
    """
    
    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        in_memory: bool = False,
    ):
        """
        Initialize the registry.
        
        Args:
            db_path: Path to SQLite database file
            in_memory: Use in-memory database (for testing)
        """
        self.db_path = ":memory:" if in_memory else db_path
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        
        # In-memory caches
        self._device_cache: Dict[str, DeviceMetadata] = {}
        self._state_cache: Dict[str, DeviceState] = {}
        
        # Callbacks
        self._state_callbacks: List[Callable[[str, Dict[str, Any], Dict[str, Any]], None]] = []
    
    # =========================================================================
    # Initialization
    # =========================================================================
    
    async def initialize(self) -> None:
        """Initialize the database and load cached data."""
        # Ensure directory exists
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Create tables
        await self._create_tables()
        
        # Load cache
        await self._load_cache()
        
        logger.info(f"Device registry initialized ({len(self._device_cache)} devices)")
    
    async def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None
    
    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with thread safety."""
        with self._lock:
            if self._connection is None:
                self._connection = sqlite3.connect(
                    self.db_path,
                    check_same_thread=False,
                )
                self._connection.row_factory = sqlite3.Row
            yield self._connection
    
    async def _create_tables(self) -> None:
        """Create database tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Devices table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    device_type TEXT NOT NULL,
                    friendly_name TEXT NOT NULL,
                    smartthings_device_id TEXT,
                    smartthings_handler_type TEXT,
                    container_id TEXT,
                    container_status TEXT,
                    room TEXT,
                    location TEXT,
                    category TEXT DEFAULT 'other',
                    tags TEXT DEFAULT '[]',
                    manufacturer TEXT DEFAULT 'VESPER',
                    model TEXT DEFAULT 'Virtual Device',
                    firmware_version TEXT DEFAULT '1.0.0',
                    is_online INTEGER DEFAULT 1,
                    is_reachable INTEGER DEFAULT 1,
                    attributes TEXT DEFAULT '{}',
                    created_at TEXT,
                    updated_at TEXT,
                    last_seen_at TEXT
                )
            """)
            
            # Current state table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS device_states (
                    device_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    source TEXT DEFAULT 'local',
                    FOREIGN KEY (device_id) REFERENCES devices(device_id)
                )
            """)
            
            # State history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS state_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    attribute TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    timestamp TEXT NOT NULL,
                    source TEXT DEFAULT 'local',
                    FOREIGN KEY (device_id) REFERENCES devices(device_id)
                )
            """)
            
            # Create index for history queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_state_history_device_time 
                ON state_history(device_id, timestamp)
            """)
            
            # Event log table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT,
                    event_type TEXT NOT NULL,
                    message TEXT,
                    data TEXT DEFAULT '{}',
                    timestamp TEXT NOT NULL
                )
            """)
            
            # Create index for event queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_event_log_time 
                ON event_log(timestamp)
            """)
            
            # SmartThings mapping table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS smartthings_mapping (
                    device_id TEXT PRIMARY KEY,
                    smartthings_device_id TEXT UNIQUE,
                    callback_token TEXT,
                    callback_url TEXT,
                    last_sync TEXT,
                    FOREIGN KEY (device_id) REFERENCES devices(device_id)
                )
            """)
            
            conn.commit()
    
    async def _load_cache(self) -> None:
        """Load devices and states into memory cache."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Load devices
            cursor.execute("SELECT * FROM devices")
            for row in cursor.fetchall():
                metadata = DeviceMetadata(
                    device_id=row["device_id"],
                    device_type=row["device_type"],
                    friendly_name=row["friendly_name"],
                    smartthings_device_id=row["smartthings_device_id"],
                    smartthings_handler_type=row["smartthings_handler_type"],
                    container_id=row["container_id"],
                    container_status=row["container_status"],
                    room=row["room"],
                    location=row["location"],
                    category=DeviceCategory(row["category"]) if row["category"] else DeviceCategory.OTHER,
                    tags=json.loads(row["tags"]) if row["tags"] else [],
                    manufacturer=row["manufacturer"],
                    model=row["model"],
                    firmware_version=row["firmware_version"],
                    is_online=bool(row["is_online"]),
                    is_reachable=bool(row["is_reachable"]),
                    attributes=json.loads(row["attributes"]) if row["attributes"] else {},
                    created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                    updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
                    last_seen_at=datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None,
                )
                self._device_cache[metadata.device_id] = metadata
            
            # Load states
            cursor.execute("SELECT * FROM device_states")
            for row in cursor.fetchall():
                state = DeviceState(
                    device_id=row["device_id"],
                    state=json.loads(row["state"]),
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    source=row["source"],
                )
                self._state_cache[state.device_id] = state
    
    # =========================================================================
    # Device Management
    # =========================================================================
    
    async def register_device(
        self,
        metadata: DeviceMetadata,
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Register a new device.
        
        Args:
            metadata: Device metadata
            initial_state: Optional initial state
            
        Returns:
            True if successful
        """
        if metadata.device_id in self._device_cache:
            logger.warning(f"Device already exists: {metadata.device_id}")
            return False
        
        now = datetime.utcnow()
        metadata.created_at = now
        metadata.updated_at = now
        metadata.last_seen_at = now
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO devices (
                    device_id, device_type, friendly_name,
                    smartthings_device_id, smartthings_handler_type,
                    container_id, container_status,
                    room, location, category, tags,
                    manufacturer, model, firmware_version,
                    is_online, is_reachable, attributes,
                    created_at, updated_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metadata.device_id,
                metadata.device_type,
                metadata.friendly_name,
                metadata.smartthings_device_id,
                metadata.smartthings_handler_type,
                metadata.container_id,
                metadata.container_status,
                metadata.room,
                metadata.location,
                metadata.category.value,
                json.dumps(metadata.tags),
                metadata.manufacturer,
                metadata.model,
                metadata.firmware_version,
                int(metadata.is_online),
                int(metadata.is_reachable),
                json.dumps(metadata.attributes),
                metadata.created_at.isoformat(),
                metadata.updated_at.isoformat(),
                metadata.last_seen_at.isoformat(),
            ))
            
            # Set initial state
            if initial_state:
                cursor.execute("""
                    INSERT INTO device_states (device_id, state, timestamp, source)
                    VALUES (?, ?, ?, 'local')
                """, (
                    metadata.device_id,
                    json.dumps(initial_state),
                    now.isoformat(),
                ))
                self._state_cache[metadata.device_id] = DeviceState(
                    device_id=metadata.device_id,
                    state=initial_state,
                    timestamp=now,
                )
            
            conn.commit()
        
        # Update cache
        self._device_cache[metadata.device_id] = metadata
        
        # Log event
        await self._log_event(
            metadata.device_id,
            EventType.DEVICE_CREATED,
            f"Device registered: {metadata.friendly_name}",
            metadata.to_dict(),
        )
        
        logger.info(f"Registered device: {metadata.friendly_name} ({metadata.device_id})")
        return True
    
    async def update_device(
        self,
        device_id: str,
        updates: Dict[str, Any],
    ) -> bool:
        """
        Update device metadata.
        
        Args:
            device_id: Device ID
            updates: Fields to update
            
        Returns:
            True if successful
        """
        metadata = self._device_cache.get(device_id)
        if not metadata:
            logger.error(f"Device not found: {device_id}")
            return False
        
        # Apply updates
        for key, value in updates.items():
            if hasattr(metadata, key):
                setattr(metadata, key, value)
        
        metadata.updated_at = datetime.utcnow()
        
        # Build SQL update
        columns = []
        values = []
        for key, value in updates.items():
            if key in ["device_id"]:
                continue
            if key == "tags":
                value = json.dumps(value)
            elif key == "attributes":
                value = json.dumps(value)
            elif key == "category":
                value = value.value if isinstance(value, DeviceCategory) else value
            elif isinstance(value, bool):
                value = int(value)
            columns.append(f"{key} = ?")
            values.append(value)
        
        columns.append("updated_at = ?")
        values.append(metadata.updated_at.isoformat())
        values.append(device_id)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE devices SET {', '.join(columns)} WHERE device_id = ?",
                values,
            )
            conn.commit()
        
        # Log event
        await self._log_event(
            device_id,
            EventType.DEVICE_UPDATED,
            f"Device updated: {metadata.friendly_name}",
            {"updates": updates},
        )
        
        return True
    
    async def delete_device(self, device_id: str) -> bool:
        """Delete a device and all its data."""
        metadata = self._device_cache.get(device_id)
        if not metadata:
            return False
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Delete from all tables
            cursor.execute("DELETE FROM state_history WHERE device_id = ?", (device_id,))
            cursor.execute("DELETE FROM device_states WHERE device_id = ?", (device_id,))
            cursor.execute("DELETE FROM smartthings_mapping WHERE device_id = ?", (device_id,))
            cursor.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
            
            conn.commit()
        
        # Update cache
        del self._device_cache[device_id]
        self._state_cache.pop(device_id, None)
        
        # Log event
        await self._log_event(
            device_id,
            EventType.DEVICE_DELETED,
            f"Device deleted: {metadata.friendly_name}",
            {},
        )
        
        logger.info(f"Deleted device: {device_id}")
        return True
    
    def get_device(self, device_id: str) -> Optional[DeviceMetadata]:
        """Get device metadata by ID."""
        return self._device_cache.get(device_id)
    
    def list_devices(
        self,
        room: Optional[str] = None,
        category: Optional[DeviceCategory] = None,
        device_type: Optional[str] = None,
        is_online: Optional[bool] = None,
    ) -> List[DeviceMetadata]:
        """
        List devices with optional filters.
        
        Args:
            room: Filter by room
            category: Filter by category
            device_type: Filter by device type
            is_online: Filter by online status
            
        Returns:
            List of matching devices
        """
        devices = list(self._device_cache.values())
        
        if room is not None:
            devices = [d for d in devices if d.room == room]
        if category is not None:
            devices = [d for d in devices if d.category == category]
        if device_type is not None:
            devices = [d for d in devices if d.device_type == device_type]
        if is_online is not None:
            devices = [d for d in devices if d.is_online == is_online]
        
        return devices
    
    # =========================================================================
    # State Management
    # =========================================================================
    
    async def update_state(
        self,
        device_id: str,
        state_updates: Dict[str, Any],
        source: str = "local",
    ) -> bool:
        """
        Update device state.
        
        Args:
            device_id: Device ID
            state_updates: State updates to apply
            source: Source of the update ("local", "smartthings", "simulation")
            
        Returns:
            True if successful
        """
        if device_id not in self._device_cache:
            logger.error(f"Device not found: {device_id}")
            return False
        
        now = datetime.utcnow()
        
        # Get current state
        current = self._state_cache.get(device_id)
        old_state = current.state.copy() if current else {}
        
        # Apply updates
        new_state = old_state.copy()
        new_state.update(state_updates)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Upsert state
            cursor.execute("""
                INSERT OR REPLACE INTO device_states (device_id, state, timestamp, source)
                VALUES (?, ?, ?, ?)
            """, (
                device_id,
                json.dumps(new_state),
                now.isoformat(),
                source,
            ))
            
            # Log history for each changed attribute
            for key, new_value in state_updates.items():
                old_value = old_state.get(key)
                if old_value != new_value:
                    cursor.execute("""
                        INSERT INTO state_history 
                        (device_id, attribute, old_value, new_value, timestamp, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        device_id,
                        key,
                        json.dumps(old_value),
                        json.dumps(new_value),
                        now.isoformat(),
                        source,
                    ))
            
            # Update last_seen_at
            cursor.execute("""
                UPDATE devices SET last_seen_at = ? WHERE device_id = ?
            """, (now.isoformat(), device_id))
            
            conn.commit()
        
        # Update cache
        self._state_cache[device_id] = DeviceState(
            device_id=device_id,
            state=new_state,
            timestamp=now,
            source=source,
        )
        
        # Update device last_seen
        if device_id in self._device_cache:
            self._device_cache[device_id].last_seen_at = now
        
        # Notify callbacks
        await self._notify_state_change(device_id, old_state, new_state)
        
        # Log event
        await self._log_event(
            device_id,
            EventType.STATE_CHANGED,
            f"State updated",
            {"old_state": old_state, "new_state": new_state, "source": source},
        )
        
        return True
    
    async def get_state(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get current device state."""
        state = self._state_cache.get(device_id)
        return state.state if state else None
    
    async def get_state_history(
        self,
        device_id: str,
        attribute: Optional[str] = None,
        hours: Optional[int] = None,
        limit: int = 1000,
    ) -> List[StateHistoryEntry]:
        """
        Get state change history.
        
        Args:
            device_id: Device ID
            attribute: Filter by specific attribute
            hours: Limit to last N hours
            limit: Maximum entries to return
            
        Returns:
            List of history entries
        """
        query = "SELECT * FROM state_history WHERE device_id = ?"
        params: List[Any] = [device_id]
        
        if attribute:
            query += " AND attribute = ?"
            params.append(attribute)
        
        if hours:
            since = datetime.utcnow() - timedelta(hours=hours)
            query += " AND timestamp >= ?"
            params.append(since.isoformat())
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        entries = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            
            for row in cursor.fetchall():
                entries.append(StateHistoryEntry(
                    id=row["id"],
                    device_id=row["device_id"],
                    attribute=row["attribute"],
                    old_value=json.loads(row["old_value"]) if row["old_value"] else None,
                    new_value=json.loads(row["new_value"]) if row["new_value"] else None,
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    source=row["source"],
                ))
        
        return entries
    
    # =========================================================================
    # SmartThings Mapping
    # =========================================================================
    
    async def set_smartthings_mapping(
        self,
        device_id: str,
        smartthings_device_id: str,
        callback_token: Optional[str] = None,
        callback_url: Optional[str] = None,
    ) -> bool:
        """Store SmartThings device mapping."""
        if device_id not in self._device_cache:
            return False
        
        now = datetime.utcnow()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO smartthings_mapping 
                (device_id, smartthings_device_id, callback_token, callback_url, last_sync)
                VALUES (?, ?, ?, ?, ?)
            """, (
                device_id,
                smartthings_device_id,
                callback_token,
                callback_url,
                now.isoformat(),
            ))
            
            # Update device metadata
            cursor.execute("""
                UPDATE devices SET smartthings_device_id = ? WHERE device_id = ?
            """, (smartthings_device_id, device_id))
            
            conn.commit()
        
        # Update cache
        self._device_cache[device_id].smartthings_device_id = smartthings_device_id
        
        return True
    
    async def get_smartthings_mapping(
        self,
        device_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get SmartThings mapping for a device."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM smartthings_mapping WHERE device_id = ?
            """, (device_id,))
            
            row = cursor.fetchone()
            if row:
                return {
                    "device_id": row["device_id"],
                    "smartthings_device_id": row["smartthings_device_id"],
                    "callback_token": row["callback_token"],
                    "callback_url": row["callback_url"],
                    "last_sync": row["last_sync"],
                }
        return None
    
    async def get_device_by_smartthings_id(
        self,
        smartthings_device_id: str,
    ) -> Optional[DeviceMetadata]:
        """Get device by SmartThings device ID."""
        for device in self._device_cache.values():
            if device.smartthings_device_id == smartthings_device_id:
                return device
        return None
    
    # =========================================================================
    # Event Logging
    # =========================================================================
    
    async def _log_event(
        self,
        device_id: Optional[str],
        event_type: EventType,
        message: str,
        data: Dict[str, Any],
    ) -> None:
        """Log an event."""
        now = datetime.utcnow()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO event_log (device_id, event_type, message, data, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (
                device_id,
                event_type.value,
                message,
                json.dumps(data),
                now.isoformat(),
            ))
            conn.commit()
    
    async def log_event(
        self,
        device_id: Optional[str],
        event_type: EventType,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Public method to log an event."""
        await self._log_event(device_id, event_type, message, data or {})
    
    async def get_events(
        self,
        device_id: Optional[str] = None,
        event_type: Optional[EventType] = None,
        hours: Optional[int] = None,
        limit: int = 100,
    ) -> List[EventLogEntry]:
        """
        Get event log entries.
        
        Args:
            device_id: Filter by device
            event_type: Filter by event type
            hours: Limit to last N hours
            limit: Maximum entries
            
        Returns:
            List of event entries
        """
        query = "SELECT * FROM event_log WHERE 1=1"
        params: List[Any] = []
        
        if device_id:
            query += " AND device_id = ?"
            params.append(device_id)
        
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type.value)
        
        if hours:
            since = datetime.utcnow() - timedelta(hours=hours)
            query += " AND timestamp >= ?"
            params.append(since.isoformat())
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        entries = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            
            for row in cursor.fetchall():
                entries.append(EventLogEntry(
                    id=row["id"],
                    device_id=row["device_id"],
                    event_type=EventType(row["event_type"]),
                    message=row["message"],
                    data=json.loads(row["data"]) if row["data"] else {},
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                ))
        
        return entries
    
    # =========================================================================
    # Callbacks
    # =========================================================================
    
    def on_state_change(
        self,
        callback: Callable[[str, Dict[str, Any], Dict[str, Any]], None],
    ) -> None:
        """
        Register a callback for state changes.
        
        Callback signature: (device_id, old_state, new_state) -> None
        """
        self._state_callbacks.append(callback)
    
    async def _notify_state_change(
        self,
        device_id: str,
        old_state: Dict[str, Any],
        new_state: Dict[str, Any],
    ) -> None:
        """Notify callbacks of state change."""
        for callback in self._state_callbacks:
            try:
                result = callback(device_id, old_state, new_state)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"State callback error: {e}")
    
    # =========================================================================
    # Maintenance
    # =========================================================================
    
    async def cleanup_old_data(
        self,
        history_days: int = STATE_HISTORY_RETENTION_DAYS,
        event_days: int = EVENT_LOG_RETENTION_DAYS,
    ) -> Tuple[int, int]:
        """
        Clean up old history and event data.
        
        Returns:
            Tuple of (history_deleted, events_deleted)
        """
        history_cutoff = datetime.utcnow() - timedelta(days=history_days)
        event_cutoff = datetime.utcnow() - timedelta(days=event_days)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM state_history WHERE timestamp < ?
            """, (history_cutoff.isoformat(),))
            history_deleted = cursor.rowcount
            
            cursor.execute("""
                DELETE FROM event_log WHERE timestamp < ?
            """, (event_cutoff.isoformat(),))
            events_deleted = cursor.rowcount
            
            conn.commit()
        
        logger.info(f"Cleanup: {history_deleted} history entries, {events_deleted} events")
        return history_deleted, events_deleted
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    async def get_statistics(self) -> Dict[str, Any]:
        """Get registry statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Count devices by category
            cursor.execute("""
                SELECT category, COUNT(*) as count 
                FROM devices GROUP BY category
            """)
            by_category = {row["category"]: row["count"] for row in cursor.fetchall()}
            
            # Count devices by room
            cursor.execute("""
                SELECT room, COUNT(*) as count 
                FROM devices WHERE room IS NOT NULL GROUP BY room
            """)
            by_room = {row["room"]: row["count"] for row in cursor.fetchall()}
            
            # Count online vs offline
            cursor.execute("SELECT COUNT(*) FROM devices WHERE is_online = 1")
            online_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM devices WHERE is_online = 0")
            offline_count = cursor.fetchone()[0]
            
            # Count history entries
            cursor.execute("SELECT COUNT(*) FROM state_history")
            history_count = cursor.fetchone()[0]
            
            # Count events
            cursor.execute("SELECT COUNT(*) FROM event_log")
            event_count = cursor.fetchone()[0]
        
        return {
            "total_devices": len(self._device_cache),
            "online_devices": online_count,
            "offline_devices": offline_count,
            "by_category": by_category,
            "by_room": by_room,
            "history_entries": history_count,
            "event_entries": event_count,
        }
