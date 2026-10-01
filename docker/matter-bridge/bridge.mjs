#!/usr/bin/env node
/**
 * VESPER Matter Bridge
 * ====================
 * Creates a matter.js Aggregator (Bridge) device that exposes VESPER's
 * simulated IoT devices as real Matter endpoints.
 *
 * Architecture:
 *   - ServerNode (Aggregator root) listens on Matter port (default 5540)
 *   - Express REST API (default :8484) lets the Python eval script
 *     add / remove / update bridged devices at runtime
 *   - python-matter-server commissions this bridge via its pairing code
 *   - Home Assistant discovers every bridged endpoint automatically
 *
 * Supported device types:
 *   smart_light   → OnOffLightDevice
 *   smart_plug    → OnOffPlugInUnitDevice
 *   temperature_sensor → TemperatureSensorDevice (bridged)
 *   humidity_sensor    → HumiditySensorDevice (bridged)
 *   contact_sensor / door_sensor → ContactSensorDevice (bridged)
 *   motion_sensor  → OccupancySensorDevice (bridged)
 *
 * REST API:
 *   GET  /health              → { status: "ok", devices: N }
 *   GET  /devices             → [ { id, type, state, ... } ]
 *   POST /devices             → { id, type, name, room, state }  → 201
 *   PUT  /devices/:id/state   → { power, temperature, ... }      → 200
 *   DELETE /devices/:id       → 204
 *   GET  /pairing             → { code, manualCode, qrCode }
 */

// ── Load Node.js platform for matter.js ──────────────────────────────────
import "@matter/nodejs";

import { Endpoint, Environment, ServerNode, VendorId, Time } from "@matter/main";
import { BridgedDeviceBasicInformationServer } from "@matter/main/behaviors/bridged-device-basic-information";
import { OnOffLightDevice } from "@matter/main/devices/on-off-light";
import { OnOffPlugInUnitDevice } from "@matter/main/devices/on-off-plug-in-unit";
import { TemperatureSensorDevice } from "@matter/main/devices/temperature-sensor";
import { HumiditySensorDevice } from "@matter/main/devices/humidity-sensor";
import { ContactSensorDevice } from "@matter/main/devices/contact-sensor";
import { OccupancySensorDevice } from "@matter/main/devices/occupancy-sensor";
import { AggregatorEndpoint } from "@matter/main/endpoints/aggregator";

import express from "express";

// ── Config ───────────────────────────────────────────────────────────────
const API_PORT       = parseInt(process.env.API_PORT  || "8484", 10);
const MATTER_PORT    = parseInt(process.env.MATTER_PORT || "5540", 10);
const PASSCODE       = parseInt(process.env.PASSCODE  || "20202021", 10);
const DISCRIMINATOR  = parseInt(process.env.DISCRIMINATOR || "3840", 10);
const VENDOR_ID      = parseInt(process.env.VENDOR_ID || "0xFFF1", 16);
const PRODUCT_ID     = parseInt(process.env.PRODUCT_ID || "0x8000", 16);
const UNIQUE_ID      = process.env.UNIQUE_ID || `vesper-bridge-${Date.now()}`;

// ── State ────────────────────────────────────────────────────────────────
/** Map device-id → { endpoint, type, state, name, room } */
const deviceMap = new Map();

let server;       // ServerNode
let aggregator;   // AggregatorEndpoint
let pairingInfo = null;

// ── Device-type → matter.js class mapping ────────────────────────────────
function deviceClassFor(type) {
  switch (type) {
    case "smart_light":
    case "light":
      return OnOffLightDevice.with(BridgedDeviceBasicInformationServer);
    case "smart_plug":
    case "plug":
      return OnOffPlugInUnitDevice.with(BridgedDeviceBasicInformationServer);
    case "temperature_sensor":
      return TemperatureSensorDevice.with(BridgedDeviceBasicInformationServer);
    case "humidity_sensor":
      return HumiditySensorDevice.with(BridgedDeviceBasicInformationServer);
    case "contact_sensor":
    case "door_sensor":
      return ContactSensorDevice.with(BridgedDeviceBasicInformationServer);
    case "motion_sensor":
      return OccupancySensorDevice.with(BridgedDeviceBasicInformationServer);
    default:
      // Default to light for unknown types
      return OnOffLightDevice.with(BridgedDeviceBasicInformationServer);
  }
}

/** Build initial cluster values for a bridged endpoint */
function initialClusterData(type, name, uniqueId, state) {
  const base = {
    bridgedDeviceBasicInformation: {
      nodeLabel: name,
      productName: name,
      productLabel: name,
      serialNumber: `vesper-${uniqueId}`,
      reachable: true,
    },
  };

  switch (type) {
    case "smart_light":
    case "light":
    case "smart_plug":
    case "plug":
      base.onOff = { onOff: state?.power === "on" || state?.power === true };
      break;
    case "temperature_sensor":
      // Matter uses hundredths of °C
      base.temperatureMeasurement = {
        measuredValue: Math.round((state?.temperature ?? 22) * 100),
      };
      break;
    case "humidity_sensor":
      // Matter uses hundredths of %RH
      base.relativeHumidityMeasurement = {
        measuredValue: Math.round((state?.humidity ?? 50) * 100),
      };
      break;
    case "contact_sensor":
    case "door_sensor":
      // false = closed, true = open
      base.booleanState = {
        stateValue: !(state?.contact === true || state?.open === true),
      };
      break;
    case "motion_sensor":
      base.occupancySensing = {
        occupancy: { occupied: state?.motion === true || state?.occupancy === true },
      };
      break;
  }

  return base;
}

// ── Create Matter Server Node ────────────────────────────────────────────
async function startMatterBridge() {
  console.log("[VESPER-BRIDGE] Creating Matter ServerNode …");
  console.log(`  Matter port : ${MATTER_PORT}`);
  console.log(`  Passcode    : ${PASSCODE}`);
  console.log(`  Discriminator: ${DISCRIMINATOR}`);
  console.log(`  Unique ID   : ${UNIQUE_ID}`);

  server = await ServerNode.create({
    id: UNIQUE_ID,

    network: {
      port: MATTER_PORT,
    },

    commissioning: {
      passcode: PASSCODE,
      discriminator: DISCRIMINATOR,
    },

    productDescription: {
      name: "VESPER IoT Bridge",
      deviceType: AggregatorEndpoint.deviceType,
    },

    basicInformation: {
      vendorName: "VESPER-Lab",
      vendorId: VendorId(VENDOR_ID),
      nodeLabel: "VESPER Matter Bridge",
      productName: "VESPER Matter Bridge",
      productLabel: "VESPER Matter Bridge",
      productId: PRODUCT_ID,
      serialNumber: `vesper-bridge-${UNIQUE_ID}`,
      uniqueId: UNIQUE_ID,
    },
  });

  // Create the aggregator endpoint (acts as bridge root)
  aggregator = new Endpoint(AggregatorEndpoint, { id: "aggregator" });
  await server.add(aggregator);

  // Start the Matter server (this prints pairing info / QR code)
  console.log("[VESPER-BRIDGE] Starting Matter server …");
  // Use server.start() instead of server.run() so we don't block
  await server.start();

  console.log("[VESPER-BRIDGE] ✓ Matter bridge online on port", MATTER_PORT);

  // Capture pairing info
  pairingInfo = {
    passcode: PASSCODE,
    discriminator: DISCRIMINATOR,
    manualCode: `${PASSCODE}`,
  };

  return server;
}

// ── Express REST API ─────────────────────────────────────────────────────
function startRestApi() {
  const app = express();
  app.use(express.json());

  // Health check
  app.get("/health", (_req, res) => {
    res.json({
      status: "ok",
      devices: deviceMap.size,
      matterPort: MATTER_PORT,
      commissioned: server?.lifecycle?.isCommissioned ?? false,
    });
  });

  // List all bridged devices
  app.get("/devices", (_req, res) => {
    const devices = [];
    for (const [id, info] of deviceMap) {
      devices.push({
        id,
        type: info.type,
        name: info.name,
        room: info.room,
        state: info.state,
      });
    }
    res.json(devices);
  });

  // Add a new bridged device
  app.post("/devices", async (req, res) => {
    try {
      const { id, type, name, room, state } = req.body;

      if (!id || !type) {
        return res.status(400).json({ error: "id and type are required" });
      }

      if (deviceMap.has(id)) {
        return res.status(409).json({ error: `Device ${id} already exists` });
      }

      const DeviceClass = deviceClassFor(type);
      const clusterData = initialClusterData(type, name || id, id, state || {});

      const endpoint = new Endpoint(DeviceClass, {
        id: `dev-${id}`,
        ...clusterData,
      });

      await aggregator.add(endpoint);

      // Register event handlers for controllable devices
      if (type === "smart_light" || type === "light" || type === "smart_plug" || type === "plug") {
        endpoint.events.onOff.onOff$Changed.on(value => {
          console.log(`[VESPER-BRIDGE] ${name || id} → ${value ? "ON" : "OFF"}`);
          const info = deviceMap.get(id);
          if (info) {
            info.state = { ...info.state, power: value ? "on" : "off" };
          }
        });
      }

      deviceMap.set(id, {
        endpoint,
        type,
        name: name || id,
        room: room || "unknown",
        state: state || {},
      });

      console.log(`[VESPER-BRIDGE] + Added device: ${id} (${type}) → ${name}`);

      res.status(201).json({
        id,
        type,
        name: name || id,
        room: room || "unknown",
        state: state || {},
      });
    } catch (err) {
      console.error("[VESPER-BRIDGE] Error adding device:", err);
      res.status(500).json({ error: err.message });
    }
  });

  // Update device state
  app.put("/devices/:id/state", async (req, res) => {
    try {
      const { id } = req.params;
      const newState = req.body;

      const info = deviceMap.get(id);
      if (!info) {
        return res.status(404).json({ error: `Device ${id} not found` });
      }

      const { endpoint, type } = info;

      // Apply state updates to the Matter endpoint
      switch (type) {
        case "smart_light":
        case "light":
        case "smart_plug":
        case "plug":
          if ("power" in newState) {
            const onOff = newState.power === "on" || newState.power === true;
            await endpoint.set({ onOff: { onOff } });
          }
          break;

        case "temperature_sensor":
          if ("temperature" in newState) {
            await endpoint.set({
              temperatureMeasurement: {
                measuredValue: Math.round(newState.temperature * 100),
              },
            });
          }
          break;

        case "humidity_sensor":
          if ("humidity" in newState) {
            await endpoint.set({
              relativeHumidityMeasurement: {
                measuredValue: Math.round(newState.humidity * 100),
              },
            });
          }
          break;

        case "contact_sensor":
        case "door_sensor":
          if ("contact" in newState || "open" in newState) {
            const isOpen = newState.open === true || newState.contact === false;
            await endpoint.set({
              booleanState: { stateValue: !isOpen },
            });
          }
          break;

        case "motion_sensor":
          if ("motion" in newState || "occupancy" in newState) {
            const occupied = newState.motion === true || newState.occupancy === true;
            await endpoint.set({
              occupancySensing: {
                occupancy: { occupied },
              },
            });
          }
          break;
      }

      // Merge state
      info.state = { ...info.state, ...newState };

      console.log(`[VESPER-BRIDGE] ~ Updated ${id}: ${JSON.stringify(newState)}`);
      res.json({ id, state: info.state });
    } catch (err) {
      console.error("[VESPER-BRIDGE] Error updating state:", err);
      res.status(500).json({ error: err.message });
    }
  });

  // Remove a bridged device
  app.delete("/devices/:id", async (req, res) => {
    try {
      const { id } = req.params;
      const info = deviceMap.get(id);
      if (!info) {
        return res.status(404).json({ error: `Device ${id} not found` });
      }

      // Closing the endpoint removes it from the aggregator
      await info.endpoint.close();
      deviceMap.delete(id);

      console.log(`[VESPER-BRIDGE] - Removed device: ${id}`);
      res.status(204).end();
    } catch (err) {
      console.error("[VESPER-BRIDGE] Error removing device:", err);
      res.status(500).json({ error: err.message });
    }
  });

  // Get pairing info
  app.get("/pairing", (_req, res) => {
    if (!pairingInfo) {
      return res.status(503).json({ error: "Bridge not started yet" });
    }
    res.json(pairingInfo);
  });

  // Bulk add devices
  app.post("/devices/bulk", async (req, res) => {
    try {
      const devices = req.body; // Array of { id, type, name, room, state }
      const results = [];

      for (const dev of devices) {
        const { id, type, name, room, state } = dev;
        if (!id || !type) {
          results.push({ id, error: "id and type are required" });
          continue;
        }
        if (deviceMap.has(id)) {
          results.push({ id, error: "already exists" });
          continue;
        }

        try {
          const DeviceClass = deviceClassFor(type);
          const clusterData = initialClusterData(type, name || id, id, state || {});

          const endpoint = new Endpoint(DeviceClass, {
            id: `dev-${id}`,
            ...clusterData,
          });

          await aggregator.add(endpoint);

          if (type === "smart_light" || type === "light" || type === "smart_plug" || type === "plug") {
            endpoint.events.onOff.onOff$Changed.on(value => {
              const info = deviceMap.get(id);
              if (info) info.state = { ...info.state, power: value ? "on" : "off" };
            });
          }

          deviceMap.set(id, {
            endpoint,
            type,
            name: name || id,
            room: room || "unknown",
            state: state || {},
          });

          results.push({ id, status: "created" });
        } catch (err) {
          results.push({ id, error: err.message });
        }
      }

      console.log(`[VESPER-BRIDGE] Bulk added ${results.filter(r => r.status === "created").length} devices`);
      res.status(201).json(results);
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // Reset all devices
  app.post("/reset", async (_req, res) => {
    try {
      for (const [id, info] of deviceMap) {
        await info.endpoint.close();
      }
      deviceMap.clear();
      console.log("[VESPER-BRIDGE] All devices removed (reset)");
      res.json({ status: "ok", devices: 0 });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  app.listen(API_PORT, "0.0.0.0", () => {
    console.log(`[VESPER-BRIDGE] REST API listening on http://0.0.0.0:${API_PORT}`);
  });

  return app;
}

// ── Main ─────────────────────────────────────────────────────────────────
async function main() {
  console.log("═══════════════════════════════════════════════════════════");
  console.log("  VESPER Matter Bridge — matter.js " + (process.env.npm_package_version || ""));
  console.log("═══════════════════════════════════════════════════════════");

  // Start the Express REST API first so Python can reach /health
  startRestApi();

  // Then start the Matter bridge
  await startMatterBridge();

  console.log("─────────────────────────────────────────────────────────");
  console.log("  Bridge is ready!");
  console.log(`  REST API   : http://0.0.0.0:${API_PORT}`);
  console.log(`  Matter port: ${MATTER_PORT}`);
  console.log(`  Passcode   : ${PASSCODE}`);
  console.log(`  Discriminator: ${DISCRIMINATOR}`);
  console.log("─────────────────────────────────────────────────────────");
  console.log("  Commission this bridge in Home Assistant:");
  console.log("  Settings → Devices → Add Integration → Matter");
  console.log(`  Manual pairing code: ${PASSCODE}`);
  console.log("─────────────────────────────────────────────────────────");
}

main().catch(err => {
  console.error("[VESPER-BRIDGE] Fatal:", err);
  process.exit(1);
});
