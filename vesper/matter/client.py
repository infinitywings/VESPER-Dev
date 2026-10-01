"""
Matter Client — Direct WebSocket connection to python-matter-server.

This module provides the core connection layer between VESPER and the
python-matter-server (running in Docker). It mirrors the architecture of
Home Assistant's Matter integration at:
    homeassistant/components/matter/__init__.py

The real HA flow is:
    Home Assistant → MatterClient (WS) → python-matter-server → CHIP SDK → Matter devices

VESPER's flow is:
    VESPER → VesperMatterClient (WS) → python-matter-server → CHIP SDK → Matter devices

The python-matter-server exposes a WebSocket API on ws://host:5580/ws
that accepts JSON-RPC commands. This client wraps that API so VESPER
can discover nodes, subscribe to events, and send device commands
without going through Home Assistant at all.

Dependencies:
    pip install python-matter-server[client]   # WebSocket client only
    pip install home-assistant-chip-clusters   # cluster definitions

Reference:
    https://github.com/home-assistant-libs/python-matter-server
    https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/__init__.py
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from .const import (
    CONNECT_TIMEOUT,
    DEFAULT_MATTER_SERVER_URL,
    LISTEN_READY_TIMEOUT,
    LOGGER,
)

logger = logging.getLogger(__name__)

# ── Conditional imports ────────────────────────────────────────────────
# python-matter-server may not be installed on every VESPER host (e.g.
# macOS dev machines without the CHIP SDK). We fail gracefully.

_HAS_MATTER_CLIENT = False
_HAS_CHIP_CLUSTERS = False

try:
    from matter_server.client import MatterClient
    from matter_server.client.exceptions import (
        CannotConnect,
        InvalidServerVersion,
        NotConnected,
    )
    from matter_server.common.models import EventType, ServerInfoMessage

    _HAS_MATTER_CLIENT = True
except ImportError:
    MatterClient = None  # type: ignore[assignment,misc]
    CannotConnect = ConnectionError  # type: ignore[assignment,misc]
    InvalidServerVersion = Exception  # type: ignore[assignment,misc]
    NotConnected = Exception  # type: ignore[assignment,misc]
    EventType = None  # type: ignore[assignment]
    ServerInfoMessage = None  # type: ignore[assignment]

try:
    from chip.clusters import Objects as chip_clusters

    _HAS_CHIP_CLUSTERS = True
except ImportError:
    chip_clusters = None  # type: ignore[assignment]


def matter_sdk_available() -> bool:
    """Return True if both the Matter client and CHIP clusters are installed."""
    return _HAS_MATTER_CLIENT and _HAS_CHIP_CLUSTERS


class VesperMatterClient:
    """
    Async WebSocket client to the python-matter-server.

    Mirrors the lifecycle of HA's ``async_setup_entry`` but without the
    Home Assistant dependency — VESPER talks to the server directly.

    Usage::

        client = VesperMatterClient("ws://localhost:5580/ws")
        await client.connect()
        nodes = client.get_nodes()
        for node in nodes:
            print(node.node_id, node.device_info)
        await client.disconnect()
    """

    def __init__(
        self,
        server_url: str = DEFAULT_MATTER_SERVER_URL,
        aiohttp_session: Optional[Any] = None,
    ):
        self._server_url = server_url
        self._aiohttp_session = aiohttp_session
        self._client: Optional[MatterClient] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._init_ready = asyncio.Event()
        self._connected = False
        self._event_callbacks: list[tuple[Optional[Any], Callable]] = []

    # ── Connection Lifecycle ───────────────────────────────────────────

    async def connect(self) -> bool:
        """
        Connect to python-matter-server and start the listen loop.

        This follows the same sequence as HA's ``async_setup_entry``:
        1. Create ``MatterClient`` with the WebSocket URL
        2. Call ``await client.connect()``
        3. Start ``client.start_listening()`` in a background task
        4. Wait for the init_ready event (server sends all nodes)
        """
        if not _HAS_MATTER_CLIENT:
            logger.error(
                "python-matter-server is not installed. "
                "Install with: pip install python-matter-server[client]"
            )
            return False

        try:
            # Create aiohttp session if not provided
            if self._aiohttp_session is None:
                import aiohttp

                self._aiohttp_session = aiohttp.ClientSession()

            self._client = MatterClient(
                self._server_url, self._aiohttp_session
            )

            # Phase 1: WebSocket handshake + auth
            async with asyncio.timeout(CONNECT_TIMEOUT):
                await self._client.connect()

            logger.info(
                "Matter Client: Connected to %s (server %s)",
                self._server_url,
                (
                    self._client.server_info.server_version
                    if self._client.server_info
                    else "unknown"
                ),
            )

            # Phase 2: Start the listen loop (receives node data)
            self._init_ready.clear()
            self._listen_task = asyncio.create_task(
                self._client_listen()
            )

            # Wait for the server to finish sending initial node data
            try:
                async with asyncio.timeout(LISTEN_READY_TIMEOUT):
                    await self._init_ready.wait()
            except TimeoutError:
                logger.warning(
                    "Matter Client: Timeout waiting for initial node data "
                    "(server may have zero nodes)"
                )
                # Not fatal — server may just be empty

            self._connected = True
            logger.info(
                "Matter Client: Ready — %d node(s) on fabric",
                len(self.get_nodes()),
            )
            return True

        except CannotConnect as exc:
            logger.error("Matter Client: Cannot connect to %s: %s",
                         self._server_url, exc)
            return False
        except InvalidServerVersion as exc:
            logger.error("Matter Client: Server version mismatch: %s", exc)
            return False
        except Exception as exc:
            logger.error("Matter Client: Unexpected error: %s", exc)
            return False

    async def _client_listen(self) -> None:
        """
        Background task: run ``start_listening`` on the MatterClient.

        This is identical to HA's ``_client_listen`` helper. The client
        receives all node data + subscriptions through this coroutine.
        """
        if not self._client:
            return
        try:
            await self._client.start_listening(self._init_ready)
        except Exception as exc:
            logger.error("Matter Client: Listen loop error: %s", exc)
        finally:
            self._connected = False

    async def disconnect(self) -> None:
        """Disconnect from the matter-server and clean up."""
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass

        if self._aiohttp_session and not self._aiohttp_session.closed:
            await self._aiohttp_session.close()

        self._connected = False
        logger.info("Matter Client: Disconnected")

    # ── Node Access ────────────────────────────────────────────────────

    def get_nodes(self) -> list:
        """
        Return all Matter nodes known to the server.

        Each node is a ``MatterNode`` with:
          - node.node_id          (int)
          - node.endpoints        (dict[int, MatterEndpoint])
          - node.device_info      (BasicInformation cluster)
          - node.available        (bool)
        """
        if not self._client:
            return []
        try:
            return self._client.get_nodes()
        except Exception:
            return []

    def get_node(self, node_id: int):
        """Return a specific node by its Matter node_id."""
        if not self._client:
            return None
        try:
            return self._client.get_node(node_id)
        except Exception:
            return None

    @property
    def server_info(self) -> Optional[Any]:
        """Return the ServerInfoMessage from the matter-server."""
        if self._client:
            return self._client.server_info
        return None

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Device Commands ────────────────────────────────────────────────

    async def send_command(
        self,
        node_id: int,
        endpoint_id: int,
        command: Any,
    ) -> Any:
        """
        Send a CHIP cluster command to a Matter device.

        This is the real deal — the command is a chip.clusters.Objects
        command instance, e.g.::

            from chip.clusters import Objects as clusters
            await client.send_command(
                node_id=1,
                endpoint_id=1,
                command=clusters.OnOff.Commands.On(),
            )

        Args:
            node_id:     Target Matter node
            endpoint_id: Target endpoint on the node
            command:     A CHIP cluster command object

        Returns:
            Command response from the device
        """
        if not self._client or not self._connected:
            raise ConnectionError("Not connected to matter-server")

        return await self._client.send_device_command(
            node_id=node_id,
            endpoint_id=endpoint_id,
            command=command,
        )

    async def write_attribute(
        self,
        node_id: int,
        endpoint_id: int,
        attribute: Any,
        value: Any,
    ) -> Any:
        """
        Write an attribute value to a Matter device.

        Args:
            node_id:     Target node
            endpoint_id: Target endpoint
            attribute:   CHIP attribute descriptor
            value:       Value to write
        """
        if not self._client or not self._connected:
            raise ConnectionError("Not connected to matter-server")

        return await self._client.write_attribute(
            node_id=node_id,
            attribute_path=f"{endpoint_id}/{attribute.cluster_id}/{attribute.attribute_id}",
            value=value,
        )

    async def interview_node(self, node_id: int) -> None:
        """
        Re-interview a node to refresh its attributes.
        Mirrors HA's ``websocket_interview_node``.
        """
        if not self._client or not self._connected:
            raise ConnectionError("Not connected to matter-server")

        await self._client.interview_node(node_id=node_id)

    async def commission_with_code(self, code: str) -> int:
        """
        Commission (pair) a new Matter device using its setup code.

        Args:
            code: The Matter setup code (numeric or QR payload)

        Returns:
            node_id of the newly commissioned device
        """
        if not self._client or not self._connected:
            raise ConnectionError("Not connected to matter-server")

        return await self._client.commission_with_code(code)

    async def remove_node(self, node_id: int) -> None:
        """Remove (unpair) a node from the Matter fabric."""
        if not self._client or not self._connected:
            raise ConnectionError("Not connected to matter-server")

        await self._client.remove_node(node_id=node_id)

    # ── Event Subscriptions ────────────────────────────────────────────

    def subscribe(
        self,
        callback: Callable,
        event_filter: Optional[Any] = None,
    ) -> Callable:
        """
        Subscribe to matter-server events.

        Event types (from matter_server.common.models.EventType):
          - NODE_ADDED, NODE_UPDATED, NODE_REMOVED
          - ENDPOINT_ADDED, ENDPOINT_REMOVED
          - ATTRIBUTE_UPDATED

        Args:
            callback:     ``async def on_event(event_type, data)``
            event_filter: Optional EventType to filter on

        Returns:
            An unsubscribe callable
        """
        if not self._client:
            logger.warning("Cannot subscribe: client not initialized")
            return lambda: None

        return self._client.subscribe_events(
            callback=callback,
            event_filter=event_filter,
        )

    # ── Diagnostics ────────────────────────────────────────────────────

    def get_diagnostics(self) -> dict[str, Any]:
        """Return connection diagnostics for dashboard / logging."""
        nodes = self.get_nodes()
        info = self.server_info

        return {
            "connected": self._connected,
            "server_url": self._server_url,
            "server_version": (
                info.server_version if info else None
            ),
            "sdk_version": (
                info.sdk_version if info else None
            ),
            "fabric_id": (
                info.compressed_fabric_id if info else None
            ),
            "total_nodes": len(nodes),
            "available_nodes": sum(1 for n in nodes if n.available),
            "unavailable_nodes": sum(1 for n in nodes if not n.available),
        }
