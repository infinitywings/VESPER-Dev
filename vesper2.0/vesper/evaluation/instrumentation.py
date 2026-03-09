"""
Instrumentation hooks for VESPER evaluation.

Attaches latency probes to existing subsystems without modifying
their source code.  Call `instrument_all()` to wire everything up.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Optional

from vesper.evaluation.latency_profiler import LatencyPath, LatencyProfiler
from vesper.evaluation.metrics import MetricsCollector

logger = logging.getLogger(__name__)


def instrument_event_stream(event_stream, profiler: LatencyProfiler):
    """
    Wrap EventStream.publish so that every event records its
    dispatch latency automatically.
    """
    original_publish = event_stream.publish

    @functools.wraps(original_publish)
    def instrumented_publish(*args, **kwargs):
        probe = profiler.start_probe(LatencyPath.EVENT_BUS_DISPATCH)
        try:
            result = original_publish(*args, **kwargs)
            return result
        finally:
            profiler.stop_probe(probe)

    event_stream.publish = instrumented_publish
    logger.info("Instrumented EventStream.publish")


def instrument_task_database(task_db, profiler: LatencyProfiler):
    """
    Wrap TaskDatabase write methods to measure DB latency.
    """
    for method_name in ("add_task", "update_task_status", "complete_task"):
        original = getattr(task_db, method_name, None)
        if original is None:
            continue

        @functools.wraps(original)
        def instrumented(
            *args,
            _orig=original,
            **kwargs,
        ):
            probe = profiler.start_probe(LatencyPath.DB_TASK_WRITE)
            try:
                return _orig(*args, **kwargs)
            finally:
                profiler.stop_probe(probe)

        setattr(task_db, method_name, instrumented)
        logger.info(f"Instrumented TaskDatabase.{method_name}")


def instrument_schema_connector(connector, profiler: LatencyProfiler):
    """
    Wrap SmartThings Schema Connector webhook handler and state
    push methods.
    """
    # Wrap handle_webhook if it exists
    if hasattr(connector, "handle_webhook"):
        original = connector.handle_webhook

        @functools.wraps(original)
        async def instrumented_webhook(*args, **kwargs):
            probe = profiler.start_probe(LatencyPath.SCHEMA_WEBHOOK_ROUNDTRIP)
            try:
                return await original(*args, **kwargs)
            finally:
                profiler.stop_probe(probe)

        connector.handle_webhook = instrumented_webhook
        logger.info("Instrumented SchemaConnector.handle_webhook")

    # Wrap push_state_update if it exists
    if hasattr(connector, "push_state_update"):
        original = connector.push_state_update

        @functools.wraps(original)
        async def instrumented_push(*args, **kwargs):
            probe = profiler.start_probe(LatencyPath.FIRMWARE_TO_SMARTTHINGS)
            try:
                return await original(*args, **kwargs)
            finally:
                profiler.stop_probe(probe)

        connector.push_state_update = instrumented_push
        logger.info("Instrumented SchemaConnector.push_state_update")


def instrument_docker_manager(manager, profiler: LatencyProfiler):
    """
    Wrap DockerDeviceManager start_device to measure container
    startup latency.
    """
    if hasattr(manager, "start_device"):
        original = manager.start_device

        @functools.wraps(original)
        async def instrumented(*args, **kwargs):
            probe = profiler.start_probe(LatencyPath.DOCKER_STARTUP)
            try:
                return await original(*args, **kwargs)
            finally:
                profiler.stop_probe(probe)

        manager.start_device = instrumented
        logger.info("Instrumented DockerDeviceManager.start_device")


def instrument_llm_client(llm_client, profiler: LatencyProfiler):
    """
    Wrap LLMClient to measure schedule generation latency.
    """
    if hasattr(llm_client, "generate"):
        original = llm_client.generate

        @functools.wraps(original)
        async def instrumented(*args, **kwargs):
            probe = profiler.start_probe(LatencyPath.LLM_SCHEDULE_GENERATION)
            try:
                return await original(*args, **kwargs)
            finally:
                profiler.stop_probe(probe)

        llm_client.generate = instrumented
        logger.info("Instrumented LLMClient.generate")


def instrument_all(
    collector: MetricsCollector,
    event_stream=None,
    task_db=None,
    schema_connector=None,
    docker_manager=None,
    llm_client=None,
) -> LatencyProfiler:
    """
    Attach latency probes to all provided subsystems.

    Returns the LatencyProfiler for later analysis.

    Usage:
        from vesper.evaluation.instrumentation import instrument_all
        from vesper.evaluation.metrics import MetricsCollector

        collector = MetricsCollector("results")
        profiler = instrument_all(
            collector,
            event_stream=my_event_stream,
            task_db=my_task_db,
        )
        # ... run simulation ...
        summary = profiler.summary()
    """
    profiler = LatencyProfiler(collector)

    if event_stream is not None:
        instrument_event_stream(event_stream, profiler)

    if task_db is not None:
        instrument_task_database(task_db, profiler)

    if schema_connector is not None:
        instrument_schema_connector(schema_connector, profiler)

    if docker_manager is not None:
        instrument_docker_manager(docker_manager, profiler)

    if llm_client is not None:
        instrument_llm_client(llm_client, profiler)

    logger.info("All instrumentation hooks attached")
    return profiler
