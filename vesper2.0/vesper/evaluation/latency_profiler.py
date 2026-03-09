"""
Latency Profiler for VESPER.

Instruments end-to-end latency across all critical paths:
- 3D proximity → firmware toggle
- Firmware state → SmartThings cloud
- SmartThings command → 3D update
- LLM task generation
- QEMU firmware boot → ready
- Event bus publish → handler delivery
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import statistics
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .metrics import LatencyMetrics, MetricsCollector

logger = logging.getLogger(__name__)


# =============================================================================
# Latency Paths (named measurement points)
# =============================================================================

class LatencyPath:
    """Standard latency path identifiers."""
    # Core simulation
    PROXIMITY_TO_TOGGLE = "3d_proximity_to_firmware_toggle"
    FIRMWARE_TO_SMARTTHINGS = "firmware_state_to_smartthings_cloud"
    SMARTTHINGS_TO_3D = "smartthings_command_to_3d_update"
    EVENT_BUS_DISPATCH = "event_bus_publish_to_handler"

    # LLM
    LLM_SCHEDULE_GENERATION = "llm_schedule_generation"
    LLM_TASK_REASONING = "llm_task_reasoning"
    LLM_FIRST_TOKEN = "llm_time_to_first_token"

    # Infrastructure
    QEMU_BOOT = "qemu_firmware_boot"
    DOCKER_STARTUP = "docker_container_startup"
    DOCKER_TCP_ROUNDTRIP = "docker_tcp_serial_roundtrip"

    # Database
    DB_TASK_WRITE = "db_task_write"
    DB_STATE_WRITE = "db_device_state_write"
    DB_QUERY = "db_query"

    # Schema connector
    SCHEMA_WEBHOOK_ROUNDTRIP = "schema_webhook_roundtrip"
    SCHEMA_CALLBACK_ROUNDTRIP = "schema_state_callback_roundtrip"
    OAUTH_TOKEN_EXCHANGE = "oauth_token_exchange"


@dataclass
class LatencyProbe:
    """A single latency measurement probe."""
    path: str
    start_time: float = 0.0
    end_time: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_s(self) -> float:
        return self.end_time - self.start_time if self.end_time > 0 else 0.0

    @property
    def elapsed_ms(self) -> float:
        return self.elapsed_s * 1000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "start": self.start_time,
            "end": self.end_time,
            "metadata": self.metadata,
        }


class LatencyProfiler:
    """
    Centralized latency profiler for VESPER.

    Provides decorators, context managers, and manual probes
    for instrumenting all critical paths.
    """

    def __init__(self, collector: Optional[MetricsCollector] = None):
        self._collector = collector or MetricsCollector()
        self._active_probes: Dict[str, LatencyProbe] = {}
        self._all_probes: List[LatencyProbe] = []
        self._enabled = True
        logger.info("LatencyProfiler initialized")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, val: bool):
        self._enabled = val

    # --- Manual probe API ---

    def start_probe(self, path: str, **metadata) -> str:
        """Start a latency probe. Returns probe_id."""
        if not self._enabled:
            return path
        probe = LatencyProbe(
            path=path,
            start_time=time.perf_counter(),
            metadata=metadata,
        )
        probe_id = f"{path}_{id(probe)}"
        self._active_probes[probe_id] = probe
        return probe_id

    def stop_probe(self, probe_id: str) -> float:
        """Stop a latency probe. Returns elapsed seconds."""
        if probe_id not in self._active_probes:
            return 0.0
        probe = self._active_probes.pop(probe_id)
        probe.end_time = time.perf_counter()
        self._all_probes.append(probe)
        self._collector.record_latency(probe.path, probe.elapsed_s)
        return probe.elapsed_s

    # --- Context manager ---

    @contextmanager
    def measure(self, path: str, **metadata):
        """
        Context manager for measuring latency.

        Usage:
            with profiler.measure(LatencyPath.LLM_SCHEDULE_GENERATION):
                result = await llm.generate(...)
        """
        if not self._enabled:
            yield
            return

        probe = LatencyProbe(
            path=path,
            start_time=time.perf_counter(),
            metadata=metadata,
        )
        try:
            yield probe
        finally:
            probe.end_time = time.perf_counter()
            self._all_probes.append(probe)
            self._collector.record_latency(path, probe.elapsed_s)

    # --- Decorator ---

    def profile(self, path: str):
        """
        Decorator for profiling function latency.

        Usage:
            @profiler.profile(LatencyPath.DB_TASK_WRITE)
            def save_task(task):
                ...
        """
        def decorator(func):
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                if not self._enabled:
                    return func(*args, **kwargs)
                with self.measure(path):
                    return func(*args, **kwargs)

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                if not self._enabled:
                    return await func(*args, **kwargs)
                with self.measure(path):
                    return await func(*args, **kwargs)

            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper
        return decorator

    # --- Results ---

    def get_results(self, path: Optional[str] = None) -> Dict[str, LatencyMetrics]:
        """Get computed latency metrics, optionally filtered by path."""
        if path:
            return {path: self._collector.get_latency_metrics(path)}
        return self._collector.get_all_latency_metrics()

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all latency measurements."""
        results = self.get_results()
        summary = {}
        for name, metrics in results.items():
            if metrics.count > 0:
                summary[name] = {
                    "count": metrics.count,
                    "mean_ms": round(metrics.mean * 1000, 2),
                    "p50_ms": round(metrics.p50 * 1000, 2),
                    "p95_ms": round(metrics.p95 * 1000, 2),
                    "p99_ms": round(metrics.p99 * 1000, 2),
                }
        return summary

    def get_raw_probes(self, path: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get raw probe data for detailed analysis."""
        probes = self._all_probes
        if path:
            probes = [p for p in probes if p.path == path]
        return [p.to_dict() for p in probes]

    def export(self, output_path: str):
        """Export all probe data to JSON."""
        data = {
            "summary": self.get_summary(),
            "probes": self.get_raw_probes(),
            "exported_at": datetime.now().isoformat(),
            "total_probes": len(self._all_probes),
        }
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Exported {len(self._all_probes)} probes to {output_path}")

    def reset(self):
        """Reset all collected data."""
        self._active_probes.clear()
        self._all_probes.clear()


# =============================================================================
# Latency Benchmark Runner
# =============================================================================

class LatencyBenchmark:
    """
    Automated latency benchmark runner.

    Runs N iterations of each latency path measurement
    and produces a comprehensive report.
    """

    def __init__(
        self,
        profiler: Optional[LatencyProfiler] = None,
        iterations: int = 1000,
    ):
        self.profiler = profiler or LatencyProfiler()
        self.iterations = iterations
        self._results: Dict[str, LatencyMetrics] = {}

    async def benchmark_event_bus(self, event_stream) -> LatencyMetrics:
        """Benchmark event bus publish-to-handler latency."""
        from vesper.simulation.event_stream import EventType

        latencies = []
        received = []

        def handler(event):
            received.append(time.perf_counter())

        event_stream.subscribe(EventType.IOT_DEVICE_STATE, handler)

        for _ in range(self.iterations):
            start = time.perf_counter()
            event_stream.publish(
                EventType.IOT_DEVICE_STATE,
                "benchmark",
                {"state": {"switch": "on"}},
            )
            if received:
                latencies.append(received[-1] - start)
            received.clear()

        event_stream.unsubscribe(EventType.IOT_DEVICE_STATE, handler)

        metrics = LatencyMetrics(
            path_name=LatencyPath.EVENT_BUS_DISPATCH,
            samples=latencies,
        )
        metrics.compute()
        self._results[LatencyPath.EVENT_BUS_DISPATCH] = metrics
        return metrics

    async def benchmark_docker_roundtrip(
        self,
        host: str = "localhost",
        port: int = 15011,
    ) -> LatencyMetrics:
        """Benchmark Docker TCP serial round-trip latency."""
        import socket

        latencies = []
        for _ in range(min(self.iterations, 100)):  # Limit for network tests
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                start = time.perf_counter()
                sock.connect((host, port))
                sock.send(b"STATUS\n")
                data = sock.recv(1024)
                elapsed = time.perf_counter() - start
                latencies.append(elapsed)
                sock.close()
            except Exception:
                pass

        metrics = LatencyMetrics(
            path_name=LatencyPath.DOCKER_TCP_ROUNDTRIP,
            samples=latencies,
        )
        metrics.compute()
        self._results[LatencyPath.DOCKER_TCP_ROUNDTRIP] = metrics
        return metrics

    async def benchmark_db_writes(self, db_path: Optional[str] = None) -> LatencyMetrics:
        """Benchmark SQLite write latency."""
        import sqlite3
        import tempfile

        if db_path is None:
            db_path = Path(tempfile.mkdtemp()) / "bench.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS bench (id INTEGER PRIMARY KEY, data TEXT, ts REAL)"
        )
        conn.commit()

        latencies = []
        for i in range(self.iterations):
            start = time.perf_counter()
            conn.execute(
                "INSERT INTO bench (data, ts) VALUES (?, ?)",
                (f"test_data_{i}", time.time()),
            )
            conn.commit()
            latencies.append(time.perf_counter() - start)

        conn.close()

        metrics = LatencyMetrics(
            path_name=LatencyPath.DB_TASK_WRITE,
            samples=latencies,
        )
        metrics.compute()
        self._results[LatencyPath.DB_TASK_WRITE] = metrics
        return metrics

    async def benchmark_llm_latency(
        self,
        llm_client,
        prompt: str = "Generate a simple 3-task morning schedule in JSON format.",
        n: int = 10,
    ) -> LatencyMetrics:
        """Benchmark LLM generation latency."""
        latencies = []
        for _ in range(n):
            start = time.perf_counter()
            try:
                response = llm_client.chat([
                    {"role": "user", "content": prompt},
                ])
                latencies.append(time.perf_counter() - start)
            except Exception as e:
                logger.warning(f"LLM benchmark error: {e}")

        metrics = LatencyMetrics(
            path_name=LatencyPath.LLM_SCHEDULE_GENERATION,
            samples=latencies,
        )
        metrics.compute()
        self._results[LatencyPath.LLM_SCHEDULE_GENERATION] = metrics
        return metrics

    def get_all_results(self) -> Dict[str, Dict[str, Any]]:
        """Get all benchmark results."""
        return {name: m.to_dict() for name, m in self._results.items()}

    def export(self, output_path: str):
        """Export benchmark results."""
        data = {
            "benchmark_results": self.get_all_results(),
            "iterations": self.iterations,
            "timestamp": datetime.now().isoformat(),
        }
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Exported latency benchmark to {output_path}")
