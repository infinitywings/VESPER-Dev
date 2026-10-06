# Contributing to VESPER

VESPER welcomes contributions that make smart-home experiments easier to run, inspect, and repeat. Start with the [offline quick start](docs/quickstart.md), then read the [architecture](docs/architecture.md) and [roadmap](docs/roadmap.md).

## Choose a small change

Good starting points are a minimal bug reproduction, a clearer setup guide, a sensor boundary test, or a narrowly scoped experiment example. Discuss changes to timing, event ordering, device contracts, scene formats, or connector behavior before building a large subsystem. The current source is a prototype; the roadmap is not an established plugin API.

Bug reports should include the checkout commit, operating system, Python/dependency versions, exact command, expected result, and actual result. Use synthetic inputs. Never attach credentials, real household logs, private floor plans, or raw cloud-device identifiers.

For an extension proposal, identify the research question, required capability, smallest runnable example, and the test that would demonstrate completion. A feature request is not a commitment to a release date.

## Develop locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q tests --ignore=tests/dataset
```

Optional workflows need additional setup; see [testing](docs/testing.md). Tests for core behavior should run without a model server, physical devices, Docker, or platform tokens. Use mocks for external services and separate integration tests from lightweight tests.

## Prepare a pull request

- Explain the user-facing change and keep unrelated refactoring separate.
- Include commands actually run and their results. Distinguish unit tests, mocked tests, and live integration checks.
- Add a regression test for changed behavior and update the relevant guide.
- Preserve historical result files. New runs belong in separate local output directories, not in `results/reference/`.
- Record the source and license of any imported code or asset. VESPER's MIT license does not relicense third-party material.
- Keep credentials and private data out of code, examples, logs, and screenshots.

Do not run network attacks or live device commands as part of a routine contribution. If a change genuinely needs those checks, describe the isolated setup and coordinate it separately. For a vulnerability or exposed credential, use the private contact in [SECURITY.md](SECURITY.md), not a public issue.
