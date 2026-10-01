# VESPER

VESPER is a research testbed for activity-conditioned smart-home experiments. It combines optional Habitat 3D scenes and LLM-generated resident schedules with stateful sensor/actuator models, trigger-action automation, and SmartThings and Matter/Home Assistant integrations. Researchers can inspect how activity and device state relate to automation outcomes; an optional Linux networking workflow supports packet-level experiments. A small, offline demonstration runs on a laptop without IoT hardware, cloud credentials, a GPU, or scene downloads.

**Manuscript:** *VESPER: An Activity-Conditioned Smart-Home Network Testbed with Cross-Layer Observability* — Huan Bui and Chenglong Fu, University of North Carolina at Charlotte. Submitted to IEEE INFOCOM 2027; manuscript, 2026. This is not an acceptance claim.

## What is included

- An event-driven simulation engine and stateful device models, with a mock environment for offline use.
- Optional Habitat scene/navigation and LLM schedule workflows. The retained evaluation snapshot contains 30 scenes and three local model identifiers.
- A geometric PIR model with position, orientation, detection cone, sensitivity, and tracked-motion speed gating.
- Trigger-action (TAP) automation code and separate SmartThings and Matter/Home Assistant connector implementations.
- Optional Linux bridge / software-emulated 802.11 workflows and mechanism-specific logging. These require an isolated Linux environment and are not activated by the quick start.
- Dataset export, alignment, labeling, and split-generation tools, plus small, provenance-linked evaluation summaries. **Raw VESPER-SH episode files are not included in this repository.**

## Quick start: offline, no hardware

Use Python 3.10 or newer; this path was tested with Python 3.12 on macOS arm64. No Habitat installation is needed.

```bash
git clone https://github.com/infinitywings/VESPER-Dev.git
cd VESPER-Dev
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m vesper --demo
python scripts/simulated_sensors_demo.py --room living_room --duration 2
python -m pytest -q tests --ignore=tests/dataset
```

The first demo creates four logical devices and two rule-based agents in a **mock** environment, then prints tick and event counts. It does not run an LLM, render a 3D home, connect to a cloud, or validate Wi-Fi behavior. The sensor demo is also software-only; simulated sensor values are not measurements from physical devices.

The fast suite includes mocked integration tests. Two legacy firmware-interface test modules are skipped because their APIs are absent from the inherited source snapshot; these skips do not validate QEMU execution.

For the dataset tests, install the optional dependencies and a local `tshark` executable:

```bash
python -m pip install -e '.[dataset,network]'
python -m pytest -q
```

These tests use crafted episodes/packets, not the full historical dataset or live household traffic. See [testing](docs/testing.md).

## Recheck the reported evidence

The following commands re-analyze small retained summaries and exercise the numerical sensor model. They **do not regenerate** the historical 90-run experiment. Output can be saved without modifying the reference files:

```bash
python scripts/verify_claims.py --claim all --output results/local/claim_audit.json
```

| Evidence | Command | Included input and interpretation |
|---|---|---|
| C1: scene count and rooms | `python scripts/verify_claims.py --claim scenes` | [scene_runs.json](results/reference/scene_runs.json): 30 unique scenes; **15.07 rooms on average** in this snapshot, not 14.8. |
| C2: activity-frequency comparison | `python scripts/verify_claims.py --claim activity` | [activity_counts.json](results/reference/activity_counts.json): five mapped-marginal JS values, 0.0932–0.1825 using natural logarithms. This is selected statistical similarity, not a claim that human behavior is matched. |
| C3: geometric motion model | `python scripts/verify_claims.py --claim sensors` | [motion_sensor.py](vesper/habitat/sensors/motion_sensor.py): a previously tracked target at 0.05 m/s is rejected; a target at 0.20 m/s can trigger. The 0.1 m/s gate requires prior position/time history. |
| C4: sensitivity range | Same sensor command | Nominal low/high range multipliers are 0.7/1.2 = 58.33%. Angle and confidence thresholds also change; this is not physical range calibration. |
| C6: scene/model matrix | Same scenes command | 90 stored records, 30 per model. The retained records lack an infrastructure-failure field, so they do not independently establish zero infrastructure failures. |

The activity snapshot reports 174 generated days and is separate from the 90-row scene/model snapshot. It must not be attributed to a particular three-model pooling procedure without additional provenance. [Evidence notes](docs/evidence.md) document these boundaries; [provenance.json](results/reference/provenance.json) identifies source files and hashes.

## Optional 3D and platform workflows

- [Habitat setup and scene layout](docs/habitat.md): install upstream Habitat separately; obtain licensed assets yourself. A complete 3D run has more requirements than the offline demo and was not rerun in this release-readiness check.
- [3D sensor model](docs/3D_SENSOR_INTEGRATION.md): distinguish the geometric PIR model from the simpler logical proximity sensor.
- [SmartThings and Matter/Home Assistant](docs/connectors.md): use test accounts and isolated environments. The inherited Python Matter Server client is a legacy path; its upstream has been archived. Tokens belong in an ignored `.env` file/process environment, never in committed YAML or logs.
- [Dataset tooling](docs/dataset.md): clock-offset estimation, one-second feature extraction, and home/model-disjoint splits. Raw episodes are not bundled, and unseen-attack or physical-home generalization is not claimed.

The Linux networking and deliberately vulnerable device examples are research fixtures. They require additional privileges/dependencies and must not be connected to production or household networks. The default dashboard binds to loopback. See [security and safe operation](SECURITY.md).

## Datasets and licensing

This repository contains no third-party scene meshes, raw ARAS/CASAS traces, broken dataset symlinks, or model weights. [data/README.md](data/README.md) links to upstream acquisition instructions and terms for HSSD, HM3D, ReplicaCAD, CASAS, and ARAS. VESPER's MIT license does **not** relicense these datasets. A local catalog is available with:

```bash
python scripts/download_datasets.py --list
```

The catalog does not download anything or bypass access/license agreements. The legacy ARAS portal was unavailable during this check; its authors' institutional paper is linked as the authoritative contact/reference.

## Repository layout

```text
vesper/             Python engine, devices, activity and integration modules
configs/            Local example configuration; no live credentials
scripts/            Demos, evidence audit, optional experiment/dataset tools
docker/             Optional research services; privileged networking is Linux-only
tests/              Unit and crafted-data tests
results/reference/  Small allowlisted summaries with provenance
data/README.md      External dataset acquisition and license boundaries
docs/               User setup, evidence, connector and testing documentation
```

## License, citation, and contact

VESPER-owned code is licensed under [MIT](LICENSE). Third-party dependencies and assets retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md). Package version 0.1.0 is software metadata, not proof that a tagged release has been published.

[CITATION.cff](CITATION.cff) provides machine-readable metadata. Cite the manuscript as unpublished/submitted:

```bibtex
@unpublished{bui2026vesper,
  title = {{VESPER}: An Activity-Conditioned Smart-Home Network Testbed
           with Cross-Layer Observability},
  author = {Bui, Huan and Fu, Chenglong},
  year = {2026},
  note = {Manuscript submitted to IEEE INFOCOM 2027},
  url = {https://github.com/infinitywings/VESPER-Dev}
}
```

Contact: [Chenglong Fu](mailto:chenglong.fu@charlotte.edu).
