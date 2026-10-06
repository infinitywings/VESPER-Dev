# VESPER

A research platform for studying how resident activity, sensor observations, and device state shape smart-home automation.

VESPER is for smart-home and IoT researchers who need controllable experiments without starting from a physical home. The current Python prototype includes an offline mock environment, stateful device models, geometric sensor checks, trigger-action automation, and optional Habitat and platform workflows.

The project is evolving toward an editable, reproducible **spatial smart-home workbench**: change a home or sensor deployment, run the same resident activity, inspect the automation consequence, and share the experiment. That workflow is the [development roadmap](docs/roadmap.md), not a feature already delivered by this prototype.

[Quick start](docs/quickstart.md) · [Documentation](docs/README.md) · [Architecture](docs/architecture.md) · [Roadmap](docs/roadmap.md) · [Contributing](CONTRIBUTING.md)

## Start offline

Use Python 3.10 or newer. The offline path needs no IoT hardware, cloud account, model API key, GPU, Docker, or scene download.

```bash
git clone https://github.com/infinitywings/VESPER-Dev.git
cd VESPER-Dev
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m vesper --demo
```

The demo creates four logical devices and two rule-based agents, runs for two seconds, and prints tick and event counts. It uses a **mock environment**, not a rendered home or an LLM. See the [quick start](docs/quickstart.md) for sensor checks, expected output, and troubleshooting.

## Choose a workflow

| Your goal | Start here | Status |
| --- | --- | --- |
| Explore software devices and events | [Offline quick start](docs/quickstart.md) | Lightweight runnable path |
| Inspect PIR range, direction, and tracked motion | [Geometric sensor model](docs/3D_SENSOR_INTEGRATION.md) | Numerical model; not hardware calibration |
| Run embodied resident activity in a 3D scene | [Habitat workflow](docs/habitat.md) | Optional dependencies and licensed assets |
| Integrate a test platform | [SmartThings and Matter/Home Assistant](docs/connectors.md) | Prototype integrations; separate setup |
| Recheck retained research summaries | [Research artifact](docs/research-artifact.md) | Small inputs and audit scripts; not a full rerun |

Blender scene compilation, an occlusion-aware lightweight spatial backend, standardized VLM plugins, and shareable experiment bundles are **planned**. They are not enabled by the quick start. Existing network and attack fixtures are retained for historical research; they are not part of the new core direction or the default onboarding path.

## Development direction

The next implementation priority is a small offline experiment connecting a fixed resident route, a geometric PIR, a device action, and an inspectable result. Subsequent work will add scene authoring and Habitat validation, paired sensor-layout experiments, replayable records, and optional model and device adapters. Real-device control will remain opt-in.

See the [roadmap and completion criteria](docs/roadmap.md). New capabilities will be documented as available only after their examples and tests work.

## Repository guide

```text
vesper/             Current Python engine, devices, automation, and integrations
configs/            Example configuration
scripts/            Demos and optional research workflows
tests/              Unit, mocked-integration, and crafted-data tests
docs/               Setup guides, architecture, roadmap, and evidence notes
results/reference/  Small research summaries with provenance
data/README.md      External asset and dataset acquisition guidance
```

Run the lightweight tests with:

```bash
python -m pytest -q tests --ignore=tests/dataset
```

See [testing](docs/testing.md) for optional dependencies and test boundaries. Software tests do not establish live platform reliability, physical sensor fidelity, or full 3D compatibility on every operating system.

## License and research

VESPER-owned code uses the [MIT license](LICENSE). Third-party dependencies, scene assets, and datasets keep their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md). Scene meshes, raw ARAS/CASAS traces, model weights, and full VESPER-SH episodes are not bundled.

The associated manuscript is *VESPER: An Activity-Conditioned Smart-Home Network Testbed with Cross-Layer Observability*, by Huan Bui and Chenglong Fu. It was submitted to IEEE INFOCOM 2027; this does not imply acceptance. [Citation metadata](CITATION.cff) and the [research artifact guide](docs/research-artifact.md) preserve the manuscript's identity separately from the project's new direction.

Use isolated test environments for optional integrations. Read [safe operation](SECURITY.md) before running inherited networking or deliberately vulnerable fixtures. Contact: [Chenglong Fu](mailto:chenglong.fu@charlotte.edu).
