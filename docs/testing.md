# Testing

The offline path needs the core package plus the `dev` extra. Use `python -m pytest -q tests --ignore=tests/dataset` for unit and mocked-integration tests. The dataset suite additionally needs `.[dataset,network]` and `tshark` in PATH, then `python -m pytest -q` runs all collected tests.

The dataset tests construct synthetic/crafted event logs and packet captures in temporary directories. They do not contact SmartThings, a home network, or a Matter fabric. Multi-model helper tests include mocked imports and source parsing, so their success does not validate actual Habitat/LLM execution.

Two legacy modules explicitly skip when `vesper.firmware.emulator` and `vesper.firmware.qemu_runner` are absent. The current source snapshot instead contains ESP32-specific and software sensor paths. Those skips are not counted as firmware integration coverage.

`python scripts/verify_claims.py --claim all` independently rechecks included count snapshots and exercises the geometric PIR model. A discrepancy with the proposal's room mean is reported, not silently replaced. This command never launches 90 experiments or connects to a cloud.

The release-readiness environment was macOS arm64 / Python 3.12.9. Habitat rendering, local LLM inference, Linux virtual radios, QEMU firmware, live SmartThings and Matter were not integration-tested in that environment. Optional setup commands in other documents are therefore setup guidance, not claims of a validated full-stack installation.
