# External data: acquire separately

No third-party scene assets, household traces, model weights or developer-machine symlinks are distributed in this directory. MIT covers VESPER-owned code, not the assets below. Review upstream terms, access conditions and attribution requirements before downloading or redistributing anything.

| Data | Authoritative source | License/access boundary |
|---|---|---|
| HSSD / HSSD-Hab | [Project](https://3dlg-hcvc.github.io/hssd/), [Habitat-ready files](https://huggingface.co/datasets/hssd/hssd-hab) | The Habitat-ready dataset card identifies CC BY-NC 4.0. Preserve attribution and noncommercial restrictions; check terms for the exact asset revision. |
| HM3D | [AI Habitat dataset page](https://aihabitat.org/datasets/hm3d/) | Academic, noncommercial research with upstream access/license agreement. Do not redistribute based on VESPER's MIT license. |
| ReplicaCAD | [AI Habitat dataset page](https://aihabitat.org/datasets/replica_cad/) | Upstream states CC BY 4.0; retain attribution and the supplied license. |
| CASAS Aruba, Milan, Cairo | [CASAS dataset catalog](https://casas.wsu.edu/datasets/) | Use the original catalog downloads and dataset-specific documentation. No blanket MIT redistribution permission is asserted here. |
| ARAS Houses A and B | [Authors' institutional paper record](https://open.metu.edu.tr/handle/11511/36652) | The original university download portal was unreachable during the release check. Obtain authorized access from the dataset authors and retain their terms; no unverified mirror is supplied. |

Dataset terms above were checked on 2026-10-01; the upstream terms remain authoritative. The ARAS paper identifies the legacy portal at `www.cmpe.boun.edu.tr/aras/`, but the current check could not verify a working download from it.

For the inherited scene workflow, place the licensed HSSD-Hab files in `data/scene_datasets/hssd-hab/`, including the scene dataset configuration and `scenes/`. Humanoid assets used by some controllers go in `data/humanoids/humanoid_data/` or the upstream downloader's `data/versioned_data/` layout. See [Habitat guidance](../docs/habitat.md). Do not assume that a newer scene revision reproduces historical room counts.

CASAS/ARAS raw data are needed only to reconstruct the original mapping/count analysis. The lightweight evidence checker uses small retained aggregate counts and requires no household trace downloads. Generated local assets and outputs remain Git-ignored; do not add personal household data.

`python scripts/download_datasets.py --list` prints the upstream catalog only. It neither downloads data nor accepts licenses on your behalf.
