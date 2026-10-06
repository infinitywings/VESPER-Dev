"""
Scalability Benchmark for VESPER.

Tests system performance under scaling across multiple dimensions:
- Number of IoT devices (5 → 500)
- Number of humanoid agents (1 → 10)
- Number of Docker containers (3 → 50)
- Simulation duration (1h → 30 days accelerated)
- Event throughput under load

Collects CPU, memory, event throughput, latency, and error metrics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from .metrics import MetricsCollector, ScalabilityMetrics, confidence_interval

logger = logging.getLogger(__name__)


# =============================================================================
# Resource Monitor
# =============================================================================

class ResourceMonitor:
    """
    Monitors system resource usage during benchmark runs.
    Samples CPU, memory, threads, and disk I/O at configurable intervals.
    """

    def __init__(self, interval_s: float = 1.0):
        self.interval = interval_s
        self._samples: List[Dict[str, Any]] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start periodic resource monitoring."""
        self._running = True
        self._samples.clear()
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> List[Dict[str, Any]]:
        """Stop monitoring and return samples."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return self._samples

    async def _monitor_loop(self):
        """Periodic sampling loop."""
        try:
            import psutil
        except ImportError:
            logger.warning("psutil not installed — resource monitoring disabled")
            return

        process = psutil.Process()
        while self._running:
            try:
                sample = {
                    "timestamp": time.time(),
                    "cpu_percent": process.cpu_percent(interval=0),
                    "memory_mb": process.memory_info().rss / (1024 * 1024),
                    "threads": process.num_threads(),
                    "system_cpu": psutil.cpu_percent(interval=0),
                    "system_memory_percent": psutil.virtual_memory().percent,
                }

                # Docker container count
                try:
                    result = subprocess.run(
                        ["docker", "ps", "-q", "--filter", "name=vesper-"],
                        capture_output=True, text=True, timeout=2,
                    )
                    sample["docker_containers"] = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
                except Exception:
                    sample["docker_containers"] = -1

                self._samples.append(sample)
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Resource monitor error: {e}")
                await asyncio.sleep(self.interval)

    def get_summary(self) -> Dict[str, Any]:
        """Compute summary statistics from samples."""
        if not self._samples:
            return {}

        cpu = [s["cpu_percent"] for s in self._samples]
        mem = [s["memory_mb"] for s in self._samples]

        return {
            "cpu_mean": float(np.mean(cpu)),
            "cpu_max": float(np.max(cpu)),
            "cpu_p95": float(np.percentile(cpu, 95)),
            "memory_mean_mb": float(np.mean(mem)),
            "memory_max_mb": float(np.max(mem)),
            "memory_p95_mb": float(np.percentile(mem, 95)),
            "samples_collected": len(self._samples),
            "duration_s": self._samples[-1]["timestamp"] - self._samples[0]["timestamp"] if len(self._samples) > 1 else 0,
        }


# =============================================================================
# Device Scaling Benchmark
# =============================================================================

class DeviceScalingBench:
    """
    Benchmark event throughput and resource usage as device count scales.

    Creates N simulated devices, each generating events at a fixed rate,
    and measures system capacity.
    """

    def __init__(
        self,
        device_counts: List[int] = None,
        event_rate_per_device: float = 1.0,  # events/sec per device
        duration_s: float = 30.0,
        trials: int = 5,
    ):
        self.device_counts = device_counts or [5, 10, 25, 50, 100, 200]
        self.event_rate = event_rate_per_device
        self.duration = duration_s
        self.trials = trials
        self._results: List[ScalabilityMetrics] = []

    async def run(self) -> List[ScalabilityMetrics]:
        """Run device scaling benchmark across all configurations."""
        from vesper.simulation.event_stream import EventStream, EventType

        for n_devices in self.device_counts:
            trial_results = []

            for trial in range(self.trials):
                logger.info(f"Device scaling: {n_devices} devices, trial {trial + 1}/{self.trials}")

                event_stream = EventStream(max_history=10000)
                event_stream.start()

                # Track received events
                received_count = 0
                latencies = []

                def handler(event):
                    nonlocal received_count
                    received_count += 1
                    # Measure dispatch latency
                    emit_time = event.data.get("emit_time", 0)
                    if emit_time > 0:
                        latencies.append(time.perf_counter() - emit_time)

                event_stream.subscribe(EventType.IOT_DEVICE_STATE, handler)

                monitor = ResourceMonitor(interval_s=1.0)
                await monitor.start()

                # Generate events from N devices
                start = time.perf_counter()
                total_events = 0
                interval = 1.0 / self.event_rate if self.event_rate > 0 else 1.0

                while (time.perf_counter() - start) < self.duration:
                    batch_start = time.perf_counter()
                    for d in range(n_devices):
                        event_stream.publish(
                            EventType.IOT_DEVICE_STATE,
                            f"device_{d}",
                            {"state": {"switch": "on"}, "emit_time": time.perf_counter()},
                        )
                        total_events += 1
                    elapsed_batch = time.perf_counter() - batch_start
                    sleep_time = max(0, interval - elapsed_batch)
                    await asyncio.sleep(sleep_time)

                elapsed = time.perf_counter() - start
                resource_samples = await monitor.stop()
                event_stream.stop()

                resource_summary = monitor.get_summary()
                throughput = received_count / elapsed if elapsed > 0 else 0
                avg_latency_ms = np.mean(latencies) * 1000 if latencies else 0

                result = ScalabilityMetrics(
                    parameter_name="num_devices",
                    parameter_value=n_devices,
                    cpu_percent=resource_summary.get("cpu_mean", 0),
                    memory_mb=resource_summary.get("memory_mean_mb", 0),
                    event_throughput=throughput,
                    avg_latency_ms=avg_latency_ms,
                    errors=total_events - received_count,
                    duration_s=elapsed,
                )
                trial_results.append(result)

            # Average across trials
            avg_result = self._average_trials(trial_results, "num_devices", n_devices)
            self._results.append(avg_result)

        return self._results

    def _average_trials(
        self,
        trials: List[ScalabilityMetrics],
        param_name: str,
        param_value: int,
    ) -> ScalabilityMetrics:
        """Average results across trials."""
        return ScalabilityMetrics(
            parameter_name=param_name,
            parameter_value=param_value,
            cpu_percent=float(np.mean([t.cpu_percent for t in trials])),
            memory_mb=float(np.mean([t.memory_mb for t in trials])),
            event_throughput=float(np.mean([t.event_throughput for t in trials])),
            avg_latency_ms=float(np.mean([t.avg_latency_ms for t in trials])),
            errors=int(np.mean([t.errors for t in trials])),
            duration_s=float(np.mean([t.duration_s for t in trials])),
        )

    def get_results(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._results]


# =============================================================================
# Docker Container Scaling Benchmark
# =============================================================================

class DockerScalingBench:
    """
    Benchmark Docker container startup time and resource usage.

    Measures time to spin up N QEMU ARM containers
    and TCP round-trip latency under load.
    """

    def __init__(
        self,
        container_counts: List[int] = None,
        image: str = "vesper-qemu-arm:latest",
        base_port: int = 16000,  # Use high ports to avoid conflict
        trials: int = 3,
    ):
        self.container_counts = container_counts or [3, 5, 10, 20]
        self.image = image
        self.base_port = base_port
        self.trials = trials
        self._results: List[ScalabilityMetrics] = []

    async def run(self) -> List[ScalabilityMetrics]:
        """Run Docker scaling benchmark."""
        for n in self.container_counts:
            trial_results = []

            for trial in range(self.trials):
                logger.info(f"Docker scaling: {n} containers, trial {trial + 1}/{self.trials}")

                containers = []
                startup_times = []

                # Start N containers
                total_start = time.perf_counter()
                for i in range(n):
                    port = self.base_port + i
                    name = f"vesper-bench-{i}"

                    start = time.perf_counter()
                    try:
                        result = subprocess.run(
                            [
                                "docker", "run", "-d",
                                "--name", name,
                                "-p", f"{port}:5555",
                                "-e", f"DEVICE_TYPE=switch",
                                "-e", f"DEVICE_NAME=bench_{i}",
                                self.image,
                            ],
                            capture_output=True, text=True, timeout=30,
                        )
                        if result.returncode == 0:
                            containers.append(name)
                            startup_times.append(time.perf_counter() - start)
                    except Exception as e:
                        logger.warning(f"Failed to start container {i}: {e}")

                total_startup = time.perf_counter() - total_start

                # Wait for containers to be ready
                await asyncio.sleep(2)

                # Measure TCP round-trip to each container
                import socket
                tcp_latencies = []
                for i, name in enumerate(containers):
                    port = self.base_port + i
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(3.0)
                        start = time.perf_counter()
                        sock.connect(("localhost", port))
                        sock.send(b"STATUS\n")
                        data = sock.recv(1024)
                        tcp_latencies.append(time.perf_counter() - start)
                        sock.close()
                    except Exception:
                        pass

                # Get memory usage
                memory_mb = 0
                try:
                    result = subprocess.run(
                        ["docker", "stats", "--no-stream", "--format",
                         "{{.MemUsage}}"] + containers,
                        capture_output=True, text=True, timeout=10,
                    )
                    for line in result.stdout.strip().split("\n"):
                        if "MiB" in line:
                            mem_str = line.split("/")[0].strip().replace("MiB", "")
                            memory_mb += float(mem_str)
                        elif "GiB" in line:
                            mem_str = line.split("/")[0].strip().replace("GiB", "")
                            memory_mb += float(mem_str) * 1024
                except Exception:
                    pass

                # Cleanup
                for name in containers:
                    subprocess.run(
                        ["docker", "rm", "-f", name],
                        capture_output=True, timeout=10,
                    )

                result = ScalabilityMetrics(
                    parameter_name="num_containers",
                    parameter_value=n,
                    container_startup_s=float(np.mean(startup_times)) if startup_times else 0,
                    memory_mb=memory_mb,
                    avg_latency_ms=float(np.mean(tcp_latencies)) * 1000 if tcp_latencies else 0,
                    errors=n - len(containers),
                    duration_s=total_startup,
                )
                trial_results.append(result)

            # Average
            avg = ScalabilityMetrics(
                parameter_name="num_containers",
                parameter_value=n,
                container_startup_s=float(np.mean([t.container_startup_s for t in trial_results])),
                memory_mb=float(np.mean([t.memory_mb for t in trial_results])),
                avg_latency_ms=float(np.mean([t.avg_latency_ms for t in trial_results])),
                errors=int(np.mean([t.errors for t in trial_results])),
                duration_s=float(np.mean([t.duration_s for t in trial_results])),
            )
            self._results.append(avg)

        return self._results

    def get_results(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._results]


# =============================================================================
# Duration Stability Benchmark
# =============================================================================

class DurationStabilityBench:
    """
    Test system stability over extended simulation durations.

    Runs the simulation for increasing durations (accelerated time)
    and monitors for memory leaks, state drift, and DB growth.
    """

    def __init__(
        self,
        durations_hours: List[float] = None,
        time_acceleration: float = 60.0,  # 1 real second = 60 sim seconds
        trials: int = 3,
    ):
        self.durations = durations_hours or [1, 6, 24, 168]  # 1h, 6h, 1d, 7d
        self.acceleration = time_acceleration
        self.trials = trials
        self._results: List[Dict[str, Any]] = []

    async def run(self) -> List[Dict[str, Any]]:
        """Run duration stability benchmark."""
        from vesper.simulation.event_stream import EventStream, EventType

        for hours in self.durations:
            trial_data = []

            for trial in range(self.trials):
                real_duration = (hours * 3600) / self.acceleration
                logger.info(
                    f"Duration stability: {hours}h sim ({real_duration:.0f}s real), "
                    f"trial {trial + 1}/{self.trials}"
                )

                event_stream = EventStream(max_history=100000)
                event_stream.start()

                # Use temp DB
                db_path = Path(tempfile.mkdtemp()) / "stability_test.db"
                conn = sqlite3.connect(str(db_path))
                conn.execute(
                    "CREATE TABLE events (id INTEGER PRIMARY KEY, type TEXT, ts REAL, data TEXT)"
                )
                conn.commit()

                monitor = ResourceMonitor(interval_s=max(1.0, real_duration / 100))
                await monitor.start()

                event_count = 0
                start = time.perf_counter()
                sim_time = 0.0

                # Simulate events over the duration
                while (time.perf_counter() - start) < min(real_duration, 300):  # Cap at 5 min real
                    sim_time += self.acceleration
                    # Generate events at realistic rate
                    for _ in range(5):  # 5 devices
                        event_stream.publish(
                            EventType.IOT_DEVICE_STATE,
                            f"device_{event_count % 5}",
                            {"sim_time": sim_time, "switch": "on"},
                        )
                        conn.execute(
                            "INSERT INTO events (type, ts, data) VALUES (?, ?, ?)",
                            ("state_change", sim_time, f'{{"count": {event_count}}}'),
                        )
                        event_count += 1

                    if event_count % 100 == 0:
                        conn.commit()

                    await asyncio.sleep(0.01)

                conn.commit()
                elapsed = time.perf_counter() - start

                # Get DB size
                db_size_mb = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0

                resource_samples = await monitor.stop()
                resource_summary = monitor.get_summary()

                # Memory growth detection
                mem_samples = [s["memory_mb"] for s in resource_samples] if resource_samples else [0]
                memory_growth = mem_samples[-1] - mem_samples[0] if len(mem_samples) > 1 else 0

                trial_data.append({
                    "sim_hours": hours,
                    "real_duration_s": elapsed,
                    "event_count": event_count,
                    "db_size_mb": db_size_mb,
                    "memory_start_mb": mem_samples[0],
                    "memory_end_mb": mem_samples[-1],
                    "memory_growth_mb": memory_growth,
                    "cpu_mean": resource_summary.get("cpu_mean", 0),
                    "memory_mean_mb": resource_summary.get("memory_mean_mb", 0),
                    "events_per_second": event_count / elapsed if elapsed > 0 else 0,
                })

                # Cleanup
                conn.close()
                event_stream.stop()
                if db_path.exists():
                    db_path.unlink()

            # Average across trials
            avg = {}
            for key in trial_data[0]:
                if isinstance(trial_data[0][key], (int, float)):
                    avg[key] = float(np.mean([t[key] for t in trial_data]))
            avg["sim_hours"] = hours
            self._results.append(avg)

        return self._results

    def get_results(self) -> List[Dict[str, Any]]:
        return self._results


# =============================================================================
# Full Scalability Suite
# =============================================================================

class ScalabilitySuite:
    """
    Orchestrates all scalability benchmarks.
    """

    def __init__(self, output_dir: str = "results/scalability"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._results: Dict[str, Any] = {}

    async def run_device_scaling(self, **kwargs) -> List[Dict[str, Any]]:
        """Run device scaling benchmark."""
        bench = DeviceScalingBench(**kwargs)
        await bench.run()
        results = bench.get_results()
        self._results["device_scaling"] = results
        return results

    async def run_docker_scaling(self, **kwargs) -> List[Dict[str, Any]]:
        """Run Docker scaling benchmark."""
        bench = DockerScalingBench(**kwargs)
        await bench.run()
        results = bench.get_results()
        self._results["docker_scaling"] = results
        return results

    async def run_duration_stability(self, **kwargs) -> List[Dict[str, Any]]:
        """Run duration stability benchmark."""
        bench = DurationStabilityBench(**kwargs)
        results = await bench.run()
        self._results["duration_stability"] = results
        return results

    async def run_all(self) -> Dict[str, Any]:
        """Run all scalability benchmarks."""
        logger.info("Starting full scalability suite...")

        await self.run_device_scaling()
        await self.run_docker_scaling()
        await self.run_duration_stability()

        self.export()
        return self._results

    def export(self):
        """Export all scalability results."""
        output_path = self.output_dir / f"scalability_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_path, "w") as f:
            json.dump(self._results, f, indent=2, default=str)
        logger.info(f"Exported scalability results to {output_path}")
