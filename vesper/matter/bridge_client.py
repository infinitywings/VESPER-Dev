"""
Matter Bridge Client — REST client for the VESPER matter.js bridge.

Provides a Python interface to the matter.js bridge's REST API,
allowing the eval script to dynamically add/remove/update
Matter-compliant virtual devices at runtime.

Usage::

    from vesper.matter.bridge_client import MatterBridgeClient

    client = MatterBridgeClient("http://localhost:8484")
    await client.wait_ready()

    await client.add_device(
        device_id="kitchen-light-01",
        device_type="smart_light",
        name="Kitchen Ceiling Light",
        room="kitchen",
        state={"power": "off"},
    )

    await client.update_state("kitchen-light-01", {"power": "on"})
    await client.remove_device("kitchen-light-01")
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Singleton guard (set by VesperEngine in strict mode) ──────────────────
_INSTANCE_COUNT = 0
_STRICT_MODE = False  # Set to True by VesperEngine when strict=True

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

try:
    import requests as _requests_mod
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


class MatterBridgeClient:
    """
    Async REST client for the VESPER matter.js bridge.

    Falls back to synchronous ``requests`` if ``httpx`` is not available.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8484",
        timeout: float = 10.0,
    ):
        global _INSTANCE_COUNT
        _INSTANCE_COUNT += 1
        if _STRICT_MODE and _INSTANCE_COUNT > 1:
            raise RuntimeError(
                f"STRICT MODE: {self.__class__.__name__} instantiated "
                f"{_INSTANCE_COUNT} times. Only VesperEngine may create this object."
            )
        elif _INSTANCE_COUNT > 1:
            logger.warning("%s instantiated %d times", self.__class__.__name__, _INSTANCE_COUNT)

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._async_client: Optional[Any] = None
        self._ready = False

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def wait_ready(self, max_wait: float = 60.0, poll: float = 2.0) -> bool:
        """Block until the bridge REST API responds to /health."""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                data = await self._get("/health")
                if data and data.get("status") == "ok":
                    self._ready = True
                    logger.info(
                        "Matter bridge ready — %d device(s) already registered",
                        data.get("devices", 0),
                    )
                    return True
            except Exception:
                pass
            await asyncio.sleep(poll)
        logger.warning("Matter bridge did not become ready within %.0fs", max_wait)
        return False

    def wait_ready_sync(self, max_wait: float = 60.0, poll: float = 2.0) -> bool:
        """Synchronous version of wait_ready."""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                data = self._get_sync("/health")
                if data and data.get("status") == "ok":
                    self._ready = True
                    logger.info(
                        "Matter bridge ready — %d device(s)",
                        data.get("devices", 0),
                    )
                    return True
            except Exception:
                pass
            time.sleep(poll)
        logger.warning("Matter bridge not ready within %.0fs", max_wait)
        return False

    @property
    def ready(self) -> bool:
        return self._ready

    # ── Device Management ─────────────────────────────────────────────

    async def add_device(
        self,
        device_id: str,
        device_type: str,
        name: str = "",
        room: str = "unknown",
        state: Optional[dict] = None,
    ) -> dict:
        """Add a new bridged device to the Matter bridge."""
        payload = {
            "id": device_id,
            "type": device_type,
            "name": name or device_id,
            "room": room,
            "state": state or {},
        }
        result = await self._post("/devices", payload)
        logger.info("Added Matter device: %s (%s)", device_id, device_type)
        return result

    def add_device_sync(
        self,
        device_id: str,
        device_type: str,
        name: str = "",
        room: str = "unknown",
        state: Optional[dict] = None,
    ) -> dict:
        """Synchronous version of add_device."""
        payload = {
            "id": device_id,
            "type": device_type,
            "name": name or device_id,
            "room": room,
            "state": state or {},
        }
        result = self._post_sync("/devices", payload)
        logger.info("Added Matter device: %s (%s)", device_id, device_type)
        return result

    async def add_devices_bulk(self, devices: list[dict]) -> list[dict]:
        """Add multiple devices in one request."""
        result = await self._post("/devices/bulk", devices)
        logger.info("Bulk added %d devices to Matter bridge", len(devices))
        return result

    def add_devices_bulk_sync(self, devices: list[dict]) -> list[dict]:
        """Synchronous bulk add."""
        result = self._post_sync("/devices/bulk", devices)
        logger.info("Bulk added %d devices to Matter bridge", len(devices))
        return result

    async def update_state(self, device_id: str, state: dict) -> dict:
        """Update the state of an existing bridged device."""
        result = await self._put(f"/devices/{device_id}/state", state)
        return result

    def update_state_sync(self, device_id: str, state: dict) -> dict:
        """Synchronous state update."""
        result = self._put_sync(f"/devices/{device_id}/state", state)
        return result

    async def remove_device(self, device_id: str) -> None:
        """Remove a bridged device."""
        await self._delete(f"/devices/{device_id}")
        logger.info("Removed Matter device: %s", device_id)

    def remove_device_sync(self, device_id: str) -> None:
        """Synchronous device removal."""
        self._delete_sync(f"/devices/{device_id}")
        logger.info("Removed Matter device: %s", device_id)

    async def list_devices(self) -> list[dict]:
        """List all currently bridged devices."""
        return await self._get("/devices") or []

    def list_devices_sync(self) -> list[dict]:
        """Synchronous device listing."""
        return self._get_sync("/devices") or []

    async def get_pairing(self) -> dict:
        """Get the bridge's pairing information."""
        return await self._get("/pairing") or {}

    async def reset(self) -> dict:
        """Remove all bridged devices."""
        return await self._post("/reset", {})

    # ── Internal HTTP helpers ─────────────────────────────────────────

    async def _get(self, path: str) -> Any:
        if _HAS_HTTPX:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(f"{self.base_url}{path}")
                r.raise_for_status()
                return r.json()
        else:
            return self._get_sync(path)

    async def _post(self, path: str, data: Any) -> Any:
        if _HAS_HTTPX:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base_url}{path}", json=data)
                r.raise_for_status()
                return r.json() if r.content else {}
        else:
            return self._post_sync(path, data)

    async def _put(self, path: str, data: Any) -> Any:
        if _HAS_HTTPX:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.put(f"{self.base_url}{path}", json=data)
                r.raise_for_status()
                return r.json() if r.content else {}
        else:
            return self._put_sync(path, data)

    async def _delete(self, path: str) -> None:
        if _HAS_HTTPX:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.delete(f"{self.base_url}{path}")
                r.raise_for_status()
        else:
            self._delete_sync(path)

    # Sync fallbacks using requests
    def _get_sync(self, path: str) -> Any:
        if _HAS_REQUESTS:
            r = _requests_mod.get(
                f"{self.base_url}{path}", timeout=self.timeout
            )
            r.raise_for_status()
            return r.json()
        raise RuntimeError("Neither httpx nor requests available")

    def _post_sync(self, path: str, data: Any) -> Any:
        if _HAS_REQUESTS:
            r = _requests_mod.post(
                f"{self.base_url}{path}", json=data, timeout=self.timeout
            )
            r.raise_for_status()
            return r.json() if r.content else {}
        raise RuntimeError("Neither httpx nor requests available")

    def _put_sync(self, path: str, data: Any) -> Any:
        if _HAS_REQUESTS:
            r = _requests_mod.put(
                f"{self.base_url}{path}", json=data, timeout=self.timeout
            )
            r.raise_for_status()
            return r.json() if r.content else {}
        raise RuntimeError("Neither httpx nor requests available")

    def _delete_sync(self, path: str) -> None:
        if _HAS_REQUESTS:
            r = _requests_mod.delete(
                f"{self.base_url}{path}", timeout=self.timeout
            )
            r.raise_for_status()
            return
        raise RuntimeError("Neither httpx nor requests available")
