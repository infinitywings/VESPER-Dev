# VESPER research artifact

The retained artifact accompanies *VESPER: An Activity-Conditioned Smart-Home Network Testbed with Cross-Layer Observability*. It includes historical workflow code, small numeric summaries, provenance, and re-analysis tools. It is separate from the [planned spatial workbench](roadmap.md).

## Recheck included inputs

Run from the repository root after the [offline installation](quickstart.md):

```bash
python scripts/verify_claims.py --claim all --output results/local/claim_audit.json
```

This saves a new audit without changing reference files. It does not rerun the historical 90 scene/model experiments.

| Evidence | Command | Included input and interpretation |
| --- | --- | --- |
| Scene and room counts | `python scripts/verify_claims.py --claim scenes` | [scene_runs.json](../results/reference/scene_runs.json) contains 30 unique scenes and 90 scene/model records. Its mean is 15.07 rooms, not 14.8. |
| Activity frequencies | `python scripts/verify_claims.py --claim activity` | [activity_counts.json](../results/reference/activity_counts.json) reproduces five mapped-marginal JS values, 0.0932–0.1825 with natural logarithms. |
| Geometric PIR behavior | `python scripts/verify_claims.py --claim sensors` | Exercises tracked-motion speed gating and cone/range parameters; does not calibrate hardware or validate occlusion. |

The 90-row snapshot has no explicit infrastructure-failure field, so it does not independently substantiate a zero-failure claim. The activity counts report 174 generated days and are separate from that matrix; their per-model composition is not established by the included summaries. The [evidence notes](evidence.md) explain these denominators and the model boundaries. [provenance.json](../results/reference/provenance.json) records source hashes.

## Dataset and optional workflows

The repository provides [dataset processing tools](dataset.md) and small VESPER-SH metadata, not a full downloadable episode archive. Raw episodes, third-party household traces, scene meshes, and model weights are not bundled. Acquire external assets under their own terms through the [dataset guide](../data/README.md).

Optional Habitat, local LLM, platform, QEMU, and Linux radio workflows have dependencies beyond the offline installation. Their retained scripts are not proof that the complete historical environment can be reconstructed from this checkout. Read [Habitat setup](habitat.md), [connectors](connectors.md), and [safe operation](../SECURITY.md) before using them. Deliberately vulnerable fixtures belong only in an isolated environment you control.

## Cite the work

Huan Bui and Chenglong Fu, University of North Carolina at Charlotte. Manuscript submitted to IEEE INFOCOM 2027, 2026. Submission is not an acceptance claim.

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

[CITATION.cff](../CITATION.cff) provides software metadata and a preferred manuscript citation. Package version 0.1.0 does not imply that a tagged release has been published. See [source provenance](provenance.md) for the imported snapshot and historical boundaries.
