# VESPER architecture

The current prototype separates activity, software devices, automation, and platform workflows into Python modules, with `VesperEngine` coordinating the lightweight runtime. The planned workbench will introduce stronger experiment and plugin contracts; those interfaces are not yet the public API.

## Current modules

| Module | Responsibility |
| --- | --- |
| [`vesper/core/`](../vesper/core/) | Event Bus, logical environment, and device registry |
| [`vesper/engine.py`](../vesper/engine.py) | Runtime assembly, device setup, simulator initialization, and tick execution |
| [`vesper/agents/`](../vesper/agents/) | Rule-based agents and optional LLM clients/controllers |
| [`vesper/habitat/`](../vesper/habitat/) | Scene loading, embodied execution, sensor geometry, and integration helpers |
| [`vesper/devices/`](../vesper/devices/) | Stateful logical sensors and actuators |
| [`vesper/automation/`](../vesper/automation/) | Internal trigger-action programming engine |
| [`vesper/integrations/`](../vesper/integrations/), [`vesper/matter/`](../vesper/matter/), [`vesper/hub/`](../vesper/hub/) | Optional platform, bridge, and hub paths |
| [`scripts/dataset/`](../scripts/dataset/) | Event/packet processing, feature extraction, labeling, and split tools |

`VesperEngine` creates an Event Bus, a device registry, an environment, and an agent controller. Initialization selects a simulator and sets up devices. Matter, Wi-Fi, and hub initialization are optional flags in the engine; the offline demo does not enable them. This is a logical description, not a claim that every module participates in every experiment.

## Two sensor paths

The mock demo uses the simpler logical motion sensor in [`vesper/devices/motion_sensor.py`](../vesper/devices/motion_sensor.py). It is useful for software integration but does not establish 3D sensor coverage.

The geometric PIR in [`vesper/habitat/sensors/motion_sensor.py`](../vesper/habitat/sensors/motion_sensor.py) models range, orientation, detection cone, confidence, cooldown, and speed estimated from tracked positions. A first observation can trigger without speed history. The current detection function does not perform a geometry-based occlusion query. Neither path is calibrated to physical PIR hardware. See [geometric sensors](3D_SENSOR_INTEGRATION.md).

## Automation and external platforms

The internal TAP engine evaluates trigger-action rules. SmartThings and Matter/Home Assistant modules provide separate state and command paths. An internal TAP outcome is not an external-platform result unless that platform is causally involved in the experiment.

Platform dependencies are optional. The inherited Python Matter Server client is a legacy path; consult [connector setup](connectors.md) before choosing it for new work. The basic demo does not exercise platform automation, authentication, commissioning, or synchronization reliability.

## Time and reproducibility

The current Event Bus orders queued events by priority and timestamp. Its event records include wall-clock timestamps and generated identifiers. Activity workflows also use accelerated logical schedule time, while TAP cooldowns use wall-clock time. These implementation choices must be accounted for when comparing runs; a fixed random seed alone does not promise identical event records.

The next kernel will make logical ordering, state ownership, and recorded inputs explicit. Deterministic re-execution and replayable experiment bundles remain [roadmap work](roadmap.md), distinct from re-analyzing the current reference summaries.

## Direction of the new core

The intended workflow is a controlled spatial experiment: a scene and resident activity produce sensor observations, an automation policy produces device commands, and recorded effects support comparison. Resident behavior and home-control policy will have separate observation permissions.

Blender will be a scene-authoring and build tool, not the runtime scheduler. Habitat will remain the principal optional 3D backend. A lightweight spatial backend will support CPU-only examples without pretending to provide RGB or complete 3D physics. LLM/VLM providers and real-device connectors will be optional adapters.

Existing network simulation and attack modules remain historical research fixtures in this tree. They will not be mandatory stages of the new spatial experiment workflow. Protocol communication for an optional connector is distinct from simulating a network substrate.

See the [roadmap](roadmap.md) for implementation order and completion criteria, and the [research artifact](research-artifact.md) for the retained historical workflows.
