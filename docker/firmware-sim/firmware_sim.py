#!/usr/bin/env python3
"""
VESPER Lightweight Firmware Simulator
=====================================

Drop-in replacement for the ESP32 QEMU firmware container.
Speaks the same TCP serial protocol on port 5555:

  Commands IN:   IDENTIFY, ON, OFF, LEVEL <n>
  Responses OUT: SWITCH:on, SWITCH:off, BRIGHTNESS:<n>, TEMP:<n>C,
                 HUMIDITY:<n>%, MOTION:detected, DOOR:open/closed

Environment variables:
  DEVICE_ID       — unique device identifier
  DEVICE_NAME     — human-readable name
  DEVICE_TYPE     — smart_light | motion_sensor | temperature_sensor |
                    humidity_sensor | door_sensor | generic
  SERIAL_PORT     — TCP listen port (default 5555)
"""

import asyncio
import os
import random
import signal
import sys
import time

DEVICE_ID = os.environ.get("DEVICE_ID", "vesper-device-01")
DEVICE_NAME = os.environ.get("DEVICE_NAME", "VESPER Device")
DEVICE_TYPE = os.environ.get("DEVICE_TYPE", "smart_light")
SERIAL_PORT = int(os.environ.get("SERIAL_PORT", os.environ.get("QEMU_SERIAL_PORT", "5555")))

# Device state
switch_state = "off"
brightness = 100
temperature = 22.0 + random.uniform(-2, 2)
humidity = 45.0 + random.uniform(-5, 5)
door_state = "closed"


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Handle one TCP client connection (the eval script)."""
    global switch_state, brightness, temperature, humidity, door_state

    peer = writer.get_extra_info("peername")
    print(f"[FW-SIM] Client connected: {peer}", flush=True)

    # Send initial identification
    writer.write(f"VESPER-FW {DEVICE_TYPE} {DEVICE_ID}\n".encode())
    await writer.drain()

    # Start background sensor task for sensor device types
    sensor_task = None
    if DEVICE_TYPE in ("motion_sensor", "temperature_sensor", "humidity_sensor", "door_sensor"):
        sensor_task = asyncio.create_task(sensor_loop(writer))

    try:
        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if not line:
                break

            cmd = line.decode("utf-8", errors="replace").strip().upper()
            if not cmd:
                continue

            print(f"[FW-SIM] RX: {cmd}", flush=True)

            if cmd == "IDENTIFY":
                resp = f"ID:{DEVICE_ID} TYPE:{DEVICE_TYPE} NAME:{DEVICE_NAME}\n"
                writer.write(resp.encode())
                await writer.drain()

            elif cmd == "ON":
                switch_state = "on"
                writer.write(f"SWITCH:on\n".encode())
                await writer.drain()
                print(f"[FW-SIM] Switch → ON", flush=True)

            elif cmd == "OFF":
                switch_state = "off"
                writer.write(f"SWITCH:off\n".encode())
                await writer.drain()
                print(f"[FW-SIM] Switch → OFF", flush=True)

            elif cmd.startswith("LEVEL"):
                parts = cmd.split()
                if len(parts) >= 2:
                    try:
                        brightness = int(parts[1])
                    except ValueError:
                        brightness = 100
                if brightness > 0:
                    switch_state = "on"
                    writer.write(f"SWITCH:on\n".encode())
                else:
                    switch_state = "off"
                    writer.write(f"SWITCH:off\n".encode())
                writer.write(f"BRIGHTNESS:{brightness}\n".encode())
                await writer.drain()
                print(f"[FW-SIM] Brightness → {brightness}", flush=True)

            elif cmd == "STATUS":
                writer.write(f"SWITCH:{switch_state}\n".encode())
                writer.write(f"BRIGHTNESS:{brightness}\n".encode())
                if DEVICE_TYPE in ("temperature_sensor", "smart_light"):
                    writer.write(f"TEMP:{temperature:.1f}C\n".encode())
                if DEVICE_TYPE in ("humidity_sensor",):
                    writer.write(f"HUMIDITY:{humidity:.1f}%\n".encode())
                if DEVICE_TYPE in ("door_sensor",):
                    writer.write(f"DOOR:{door_state}\n".encode())
                await writer.drain()

            else:
                writer.write(f"ERR:unknown_command:{cmd}\n".encode())
                await writer.drain()

    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        if sensor_task:
            sensor_task.cancel()
            try:
                await sensor_task
            except asyncio.CancelledError:
                pass
        writer.close()
        print(f"[FW-SIM] Client disconnected: {peer}", flush=True)


async def sensor_loop(writer: asyncio.StreamWriter):
    """Periodically emit sensor readings (motion, temperature, etc.)."""
    global temperature, humidity, door_state
    try:
        while True:
            await asyncio.sleep(random.uniform(5, 15))

            if DEVICE_TYPE == "motion_sensor":
                # Random motion detection
                if random.random() < 0.3:
                    writer.write(b"MOTION:detected\n")
                    await writer.drain()

            elif DEVICE_TYPE == "temperature_sensor":
                temperature += random.uniform(-0.5, 0.5)
                temperature = max(15.0, min(35.0, temperature))
                writer.write(f"TEMP:{temperature:.1f}C\n".encode())
                await writer.drain()

            elif DEVICE_TYPE == "humidity_sensor":
                humidity += random.uniform(-1, 1)
                humidity = max(20.0, min(80.0, humidity))
                writer.write(f"HUMIDITY:{humidity:.1f}%\n".encode())
                await writer.drain()

            elif DEVICE_TYPE == "door_sensor":
                if random.random() < 0.1:
                    door_state = "open" if door_state == "closed" else "closed"
                    writer.write(f"DOOR:{door_state}\n".encode())
                    await writer.drain()

    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
        pass


async def main():
    print("╔══════════════════════════════════════════════════════════════╗", flush=True)
    print(f"║  VESPER Firmware Simulator", flush=True)
    print(f"║  Device : {DEVICE_ID}", flush=True)
    print(f"║  Name   : {DEVICE_NAME}", flush=True)
    print(f"║  Type   : {DEVICE_TYPE}", flush=True)
    print(f"║  Serial : tcp://0.0.0.0:{SERIAL_PORT}", flush=True)
    print("╚══════════════════════════════════════════════════════════════╝", flush=True)

    server = await asyncio.start_server(handle_client, "0.0.0.0", SERIAL_PORT)
    print(f"[FW-SIM] Listening on port {SERIAL_PORT}", flush=True)

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown(server)))

    async with server:
        await server.serve_forever()


async def shutdown(server):
    print("[FW-SIM] Shutting down...", flush=True)
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
