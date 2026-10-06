# Third-party dependencies and assets

The root MIT license applies to VESPER-owned source code. It does not override any upstream license, model terms, platform terms or dataset agreement.

- Habitat-Sim and Habitat-Lab are external dependencies. Obtain them from their upstream repositories, keep their license/notices with your installation, and record the version used. Their source trees are not redistributed here.
- HSSD, HM3D and ReplicaCAD scene assets are not included. See [data/README.md](data/README.md) for upstream terms. In particular, a noncommercial dataset license is not a general open-source software license.
- CASAS and ARAS household traces are not included. Only small mapped aggregate counts from the authors' existing analysis are retained, with provenance; do not assume raw-trace redistribution rights.
- LLM weights and SmartThings/Home Assistant/Matter services have their own licenses, access rules and configuration requirements.
- ESP-IDF, QEMU, Matter.js and other dependencies referenced by optional build scripts must retain their upstream notices in a resulting build. This repository's license does not relicense downloaded dependencies.

If an attribution or ownership issue is found in inherited code, contact the maintainers before redistributing the affected component.
