# VESPER offline quick start

Run software devices and inspect events before installing a renderer or connecting a platform. This path uses Python 3.10 or newer and has been checked on macOS arm64 with Python 3.12. It needs no hardware, cloud credentials, model server, Docker, or scene assets.

## Install from source

```bash
git clone https://github.com/infinitywings/VESPER-Dev.git
cd VESPER-Dev
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

The activation command is for macOS and Linux shells. On Windows PowerShell, use `.venv\Scripts\Activate.ps1`; Windows execution is not part of the checked configuration. The editable install loads code from this checkout. No PyPI publication is implied.

## Run the mock environment

```bash
python -m vesper --demo
```

The terminal lists four logical devices and two rule-based agents, then prints `Ticks` and `Events processed` after a two-second run. Exact counts can vary with execution timing.

This demo checks the lightweight engine and event path. It does not render a 3D house, call an LLM or VLM, contact SmartThings, commission Matter devices, or exercise Wi-Fi. The agent named `HomeAssistant` in the demo is a local software agent, not the external Home Assistant application.

## Try software sensors

```bash
python scripts/simulated_sensors_demo.py --room living_room --duration 2
```

This separate example prints generated sensor updates. They are synthetic software values, not measurements from a physical room. It does not establish realistic temperature, illuminance, or occupancy behavior.

To inspect the geometric PIR model without rendering a scene:

```bash
python scripts/verify_claims.py --claim sensors
```

The command checks range and direction behavior, tracked-motion speed gating, and sensitivity parameters. The geometric model is separate from the simpler proximity sensor in `--demo`. Read the [sensor guide](3D_SENSOR_INTEGRATION.md) before interpreting detection results.

## Run the lightweight tests

```bash
python -m pytest -q tests --ignore=tests/dataset
```

The suite includes unit and mocked-integration tests. Two inherited firmware-interface modules skip because their legacy APIs are absent; those skips do not validate QEMU. Optional crafted-data tests require additional packages and `tshark`; see [testing](testing.md).

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `No module named vesper` | Activate the virtual environment and run the editable install from the repository root. |
| Missing `yaml`, `numpy`, or `pydantic` | Confirm that `python -m pip` and `python` use the same virtual environment, then repeat the install. |
| No 3D window appears | Expected for `--demo`. Use the separate [Habitat workflow](habitat.md) for rendered scenes. |
| Dataset tests fail because `tshark` is missing | Use the lightweight command above, or install the dataset prerequisites described in [testing](testing.md). |
| A platform endpoint is unavailable | Platform services are not started by the demo. Follow [connector setup](connectors.md) in an isolated test environment. |

Do not run a privileged networking installer to fix an offline-demo problem.

## Next steps

For existing code, explore the [architecture](architecture.md) and optional setup guides. For the planned scene-editing and repeatable-experiment workflow, read the [roadmap](roadmap.md). Commands such as `vesper init`, `vesper run`, and `vesper bundle` are not current CLI features.
