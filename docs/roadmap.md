# VESPER development roadmap

VESPER is evolving into a workbench for reproducible spatial smart-home experiments. The goal is to let a researcher change a sensor deployment or automation rule, run a controlled comparison, explain the resulting difference, and share enough inputs and records for another researcher to repeat it.

Everything below is planned work. The current runnable paths are listed in the [quick start](quickstart.md) and [architecture](architecture.md). Milestones use completion criteria rather than promised release dates.

## First complete example

A small, single-floor home will contain four rooms, one scripted resident, two PIR sensors, a door contact, two lights, and a switch. Its three experiments will compare sensor position/orientation, a declared occlusion model, and conflicting automation rules. Fixed activity and configuration will make each intervention interpretable.

The default example will use software devices and no cloud model. A prebuilt scene will avoid requiring Blender merely to run an experiment. The lightweight backend will expose its supported geometry explicitly; camera and full 3D observations will require an appropriate backend.

## Implementation milestones

| Milestone | Deliverable | Completion criterion |
| --- | --- | --- |
| Experiment foundation | Per-run state, logical clock, ordered events, typed commands, and results | Two experiments can run independently in one process; fixed inputs reproduce normalized simulation records. |
| Offline vertical slice | Resident route, geometric PIR, light, rule, and inspectable result | The complete chain runs without models, platforms, or privileged networking; changing one sensor parameter produces a traceable comparison. |
| Spatial examples | Placement, occlusion, and rule-conflict experiments | Each example has declared opportunities, denominators, controls, and failure cases; an infeasible route is rejected. |
| Scene authoring | Validated house specification, Blender export, and Habitat loading | Bundles contain asset provenance, stable entity IDs, collision/navigation information, and a scene-validation report. |
| Sharing and inspection | Versioned scene, experiment, and run bundles | Another checkout can inspect records and re-execute supported simulations; playback does not resend external commands. |
| Optional adapters | Separated resident/home policies, model providers, and test-platform connectors | Adapters declare capabilities and observation permissions; accepted commands and observed effects are recorded separately. |

## Extension boundaries

Blender-generated houses need more than a mesh: rooms, doors, furniture, activity points, devices, navigation, and asset licenses must stay consistent. Generated geometry will be validated before it is used in an experiment.

LLM and VLM adapters will be research variables, not startup requirements. Home-control policies will not receive hidden resident intentions, future schedules, or evaluation labels by default. Recorded model responses will support repeatable comparisons; fresh model calls will be identified as new runs.

Device connectors will default to disabled. Real execution will require explicit entity selection and a test environment. Home Assistant will be the first proposed platform adapter; SmartThings, thermostat models, and OpenADR will follow separate compatibility and licensing checks. No merge of the related repositories or compatibility with them is claimed here.

## Outside the initial core

The first workbench will not attempt RF/Wi-Fi simulation, network attack campaigns, arbitrary commercial firmware rehosting, calibrated building physics, or a general smart-home agent leaderboard. Existing networking and security fixtures remain a historical research path rather than a new core dependency.

Physical-home representativeness and broad cross-platform 3D support require their own evidence. A mock backend will not silently stand in for a requested camera or an unsupported interaction.

## Contribute to a milestone

Useful early contributions include clock/state isolation tests, sensor boundary cases, small licensed scenes, and a reproducible paired example. Before implementing a large subsystem, propose the research question, minimal example, and acceptance test through the feature-request template. See [contributing](../CONTRIBUTING.md).
