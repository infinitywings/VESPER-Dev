# VESPER-SH tooling and distribution status

`scripts/dataset/` contains event logging, sequence-matched clock-offset estimation, pcap feature extraction, attack-interval labeling, episode export, split generation and baseline utilities. Full raw/derived episode payloads are **not bundled** here. Small [metadata](../results/reference/dataset_metadata.json) preserve the source baseline's 12,099 train and 5,884 test windows and the split files' 20 home IDs.

The development snapshot describes a 60-episode dataset, but its public Git tree has no `episodes/` payload directory. There is no DOI or verified downloadable full dataset archive supplied by this release. Contact the maintainers for distribution plans; do not call these metadata a complete dataset release.

## Processing your own isolated synthetic episodes

Install `.[dataset,network]` and `tshark`. For a local raw episode directory, the exporter reads `events.jsonl`, `attack_schedule.jsonl`, `ap.pcap`, `rf.pcap`, and, when available, `bridge_sync_mac.jsonl` and `bridge_sync_vm.jsonl`. It writes `windows.parquet`, `labels.csv` and `meta.json`. Inputs must come from an isolated authorized simulation, not your home or a production network.

Clock offset is the median VM-minus-host timestamp difference across matching sequence IDs. The code raises when both synchronization files exist but no sequence IDs match; **when synchronization files are absent, it falls back to offset 0**, so such output must not be described as verified cross-host alignment. Residual alignment error is not calibrated. One-second windowing does not establish sub-second causal ordering.

`vesper_sh.make_splits` separates homes, and its `by_resident` split separates **LLM model identifiers**, not real residents or necessarily distinct personas. Training/test attack templates and generator implementations are not necessarily disjoint. No unseen-attack or physical-home generalization is claimed.

The full unit suite includes crafted episode export and baseline tests. It does not reproduce the source detector scores. Third-party source traces/assets retain upstream licenses; VESPER's MIT license applies to owned code, not every dataset it can process.
