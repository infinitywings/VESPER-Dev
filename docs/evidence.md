# Evidence and reproducibility boundaries

The small files in `results/reference/` were extracted from the public development snapshot `huuhuannt1998/vesper` at commit `0e99cc24ede4e9079c9dcbbdb6abbd112ef23bf2`. The source history was not merged into this repository. `provenance.json` records SHA-256 hashes of the original inputs. Only numeric experiment fields and mapped activity counts were retained; original device identifiers, cloud endpoints, local paths, and interaction logs were excluded.

## Scene and model records (C1, C6)

`scene_runs.json` contains 90 records: Qwen2.5-7B-Instruct, Llama-3.1-8B-Instruct, and Gemma-2-9B-It each cover the same 30 scenes. `verify_claims.py` checks that repeated scene IDs have consistent room counts and computes the mean over unique scenes, not over an arbitrary mixture of rows.

The result is 452 rooms / 30 scenes = 15.0667, with a 6–35 room range. The proposal's 14.8 mean is not reproduced by these files. Room counts are those emitted by the evaluation script; they are not independently audited against scene geometry here. A complete geometry recomputation requires separately acquired HSSD assets and Habitat.

There is no explicit infrastructure-failure/status field in the retained source records. Ninety records therefore substantiate the matrix size, not an audited zero-failure claim. Navigation and TAP action outcomes are separate quantities, and neither should be conflated with infrastructure completion. The original 90 experiments were not rerun during repository preparation.

## Mapped activity frequencies (C2)

`activity_counts.json` retains the generated distribution and five reference count distributions from `results/rq_data/rq1_activity_realism.json`. The comparator in `scripts/collect_real_data.py` uses nine categories:

Sleeping, Personal_Hygiene, Meal_Preparation, Working, Exercising, Relaxing, Socializing, Housekeeping, Other.

CASAS room-proxy events and ARAS activity transitions are not identical observation units. The source maps CASAS bedroom/bathroom/kitchen/living-room labels to Sleeping/Personal_Hygiene/Meal_Preparation/Relaxing, with other room mappings defined in that script. ARAS activity IDs use their annotation mapping. These asymmetric mappings constrain interpretation; correlations between frequency vectors are not temporal correlations.

`verify_claims.py` adds epsilon = 1e-10 to each category count, normalizes each distribution, and computes JS divergence with natural logarithms. Recomputed values agree with all five stored values at four decimals: ARAS A 0.1182, ARAS B 0.0932, CASAS Aruba 0.1702, Milan 0.1662, Cairo 0.1825. The familiar 0.093–0.183 range is rounded from these values, not a confidence interval.

The source snapshot reports 174 generated days. It does not establish a per-model breakdown or that these counts derive from the separate 90-record experiment. Rechecking the counts is inexpensive; regenerating them from raw schedules and reference traces requires the original inputs and additional provenance. The release does not claim semantic, conditional, circadian, or human-behavioral equivalence.

## PIR parameters and behavior (C3, C4)

The numerical checks use `vesper/habitat/sensors/motion_sensor.py`. That model has position, yaw/tilt orientation, a cone/range test, speed gating, and confidence thresholds. It is distinct from `vesper/devices/motion_sensor.py`, the simpler logical proximity sensor used in the mock demo.

Speed is computed from two tracked positions when dt > 0. A first observation has no velocity estimate and may trigger even when the target is stationary. A target below the default 0.1 m/s threshold is rejected only when that history is available and the update reaches the velocity test; cooldown also affects update timing. The checks demonstrate a 0.05 m/s rejected update, a 0.20 m/s accepted update, and a target behind the sensor rejected by its cone.

The nominal low/high range ratio is 0.7 / 1.2 = 0.5833. Confidence and angular multipliers differ too. This parameter ratio is not a measured effective detection radius or calibrated physical PIR behavior.

## Integrations, dataset, and laptop use (C5, C7, C8)

Habitat workflow and SmartThings/Matter modules are present. Their existence does not validate live connector reliability, all-layer causal coupling, or arbitrary commercial firmware execution. This release check exercised mock/numerical/unit-test paths, not live cloud or Matter fabrics.

The source VESPER-SH baseline metadata records 12,099 train + 5,884 test windows (17,983 total), and its home split files list 20 homes. Its Git tree contains metadata and splits but no `episodes/` payload directory. This repository consequently supplies tooling and small metadata, **not a downloadable full dataset**. Do not describe these files as a complete dataset release.

The lightweight mock and numerical workflows run on a laptop without physical IoT devices. That does not imply every experiment runs without scene assets, LLM resources, Linux kernel support, or platform credentials. VESPER-owned code is MIT; HSSD, HM3D, ReplicaCAD and other third-party assets remain under their upstream terms and are not redistributed here.
