# VESPER Paper — Comprehensive Project Report

## Paper Title

**VESPER: A High-Fidelity Smart Home Simulation Platform with Firmware-in-the-Loop and LLM-Driven Activity Generation**

- **Venue format:** ACM SIGCONF (anonymous submission)
- **Language:** LaTeX
- **Entry point:** `main.tex`

---

## 1. Folder Structure

```
VESPER-paper/
├── main.tex                    # Root LaTeX document (entry point)
├── references.bib              # Bibliography (BibTeX)
│
├── sections/                   # 8 paper sections
│   ├── 01_intro.tex            # Introduction & contributions
│   ├── 02_background.tex       # Background & related concepts
│   ├── 03_system_design.tex    # 7-layer system architecture
│   ├── 04_implementation.tex   # Implementation details
│   ├── 05_evaluation.tex       # 5 RQs + autonomous evaluation
│   ├── 06_discussion.tex       # Limitations, threats, ethics
│   ├── 07_related_work.tex     # Comparison with 9 platforms
│   └── 08_conclusion.tex       # Summary & future work
│
├── tables/                     # 19 LaTeX tables
│   ├── tab_activity_realism.tex
│   ├── tab_autonomous_eval.tex
│   ├── tab_baseline_comparison.tex
│   ├── tab_cve_validation.tex
│   ├── tab_cvss_distribution.tex
│   ├── tab_device_comparison.tex
│   ├── tab_kill_chain.tex
│   ├── tab_latency.tex
│   ├── tab_llm_ablation.tex
│   ├── tab_mitre_coverage.tex
│   ├── tab_per_scene.tex
│   ├── tab_personas.tex
│   ├── tab_protocol_breakdown.tex
│   ├── tab_scalability.tex
│   ├── tab_security_summary.tex
│   ├── tab_sim2real.tex
│   ├── tab_statistical_tests.tex
│   ├── tab_system_comparison.tex
│   └── tab_traffic_analysis.tex
│
├── figures/                    # 13 TikZ/pgfplots figures + pre-rendered assets
│   ├── fig_ablation.tex
│   ├── fig_activity_dist.tex
│   ├── fig_architecture.tex
│   ├── fig_cvss_distribution.tex
│   ├── fig_device_heatmap.tex
│   ├── fig_kill_chain.tex
│   ├── fig_latency_cdf.tex
│   ├── fig_scalability.tex
│   ├── fig_scatter.tex
│   ├── fig_schedule_example.tex
│   ├── fig_sim2real.tex
│   ├── fig_temporal.tex
│   ├── fig_tte_boxplot.tex
│   ├── architecture.png
│   ├── fig_attack_surface.pdf
│   ├── fig_cvss_distribution.pdf
│   ├── fig_device_heatmap.pdf
│   ├── fig_kill_chain.pdf
│   ├── fig_mitre_tactics.pdf
│   └── fig_tte_boxplot.pdf
│
└── .git/                       # Git repository
```

---

## 2. Paper Overview

VESPER (**V**irtual **E**nvironment for **S**mart-home **P**latform **E**valuation and **R**esearch) is a high-fidelity smart home simulation platform that unifies three innovations:

1. **Firmware-in-the-loop emulation** — Real ARM device firmware runs inside QEMU containers managed by Docker, enabling bit-accurate execution of sensor sampling loops, interrupt handlers, and communication stacks.
2. **LLM-driven activity generation** — Large language models (GPT-OSS 20B via LMStudio) generate daily activity schedules conditioned on structured occupant personas, producing diverse yet contextually coherent behavioral patterns.
3. **Bi-directional cloud synchronization** — Samsung SmartThings Schema Connector protocol allows simulated devices to appear as first-class citizens in a real SmartThings account with state changes flowing in both directions.

All components are integrated within Meta's **Habitat 3.0** embodied AI framework, connected by a central publish-subscribe event bus.

### Implementation Scale

- ~15,000 lines core Python 3.9
- ~1,600 lines bare-metal C (6 firmware variants)
- ~3,200 lines Python (attack framework)
- ~900 lines Python (security evaluation pipeline)

---

## 3. Section-by-Section Summary

### 3.1 Introduction (`sections/01_intro.tex`)

Motivates the problem: physical IoT testbeds are expensive, non-reproducible, and limited in scope; existing simulators lack fidelity (stateless message endpoints, Markov-chain occupant models). Defines the three key innovations and lists **6 contributions**:

1. Design and implementation of VESPER (first to combine embodied 3D simulation + firmware-in-the-loop + cloud IoT)
2. LLM-based activity generation pipeline with 10 diverse personas (mean JS divergence 0.218 against CASAS/ARAS)
3. Comprehensive evaluation across 5 RQs (fidelity, scalability, latency, Sim2Real, security)
4. Large-scale autonomous evaluation (28 HSSD scenes, 7 simulated days each, 88.0 hours wall-clock)
5. 5-suite security testing framework (36 unique attacks, CVSS 3.1 scoring, MITRE ATT&CK mapping, tshark pcap validation)
6. Open-source release

### 3.2 Background (`sections/02_background.tex`)

Covers four background areas:

- **3D Embodied Simulation:** Habitat 3.0, AI2-THOR, iGibson — photorealistic environments with physics, but focused on navigation rather than IoT
- **IoT Testbeds & Simulators:** IoTSim, FIESTA-IoT, SWoTSuite — device networks without 3D spatial grounding or firmware-level behavior
- **Activity Generation:** Markov chains, stochastic Petri nets, agent-based models — require extensive manual parameterization
- **Firmware-in-the-Loop Testing:** QEMU full-system ARM emulation, Firmadyne, Avatar
- **LLMs for Behavioral Modeling:** Generative agents (Park et al.), LLM world models
- **SmartThings Ecosystem:** Schema Connector webhook-based lifecycle protocol

### 3.3 System Design (`sections/03_system_design.tex`)

Describes VESPER's **7-layer architecture**:

| Layer | Description |
|-------|-------------|
| **Habitat Simulation Core** | HSSD/Replica-CAD scenes, SMPL-X humanoid models, Bullet physics engine |
| **Firmware Emulation Layer** | Docker + QEMU 10.2 ARM (LM3S6965EVB), 6 device types, UART-over-TCP protocol, intentional vulnerabilities |
| **LLM Activity Engine** | 10 personas, JSON schedule generation, Jinja2 prompts, validation + retry |
| **Cloud Sync Bridge** | SmartThings Schema Connector, OAuth2, SQLite device registry, ngrok HTTPS tunnel |
| **Event Coordination Bus** | Pub/sub with nanosecond timestamps, ring buffer, JSONL logging, SQLite task DB |
| **Simulated Home Network** | Docker bridge (172.20.0.0/24), MQTT broker, Zigbee/Z-Wave/BLE protocol simulators, tshark pcap capture |
| **Security Testing Layer** | 5 attack suites, 36 unique attacks, CVSS 3.1 scoring, MITRE ATT&CK mapping |

#### 6 Firmware Device Types

| Device | Key Features |
|--------|-------------|
| Smart Light | PWM brightness (0-100%), color temperature (2,700-6,500 K), on/off/toggle |
| Motion Sensor | PIR interrupt, configurable sensitivity (1-10), cooldown timer |
| Temperature Sensor | ADC sampling at 1 Hz, Kalman filtering, threshold alerts |
| Humidity Sensor | Relative humidity + temperature combo, calibration |
| Door Sensor | Magnetic contact, open/closed state, tamper detection |
| Smart Plug | Relay-driven outlet, power metering (watts), scheduling |

#### Intentional Vulnerability Model

Each firmware embeds three vulnerability classes:

1. **Buffer overflow** — 128-byte `fw_update_buf` accepts data without bounds checking
2. **Missing authentication** — all commands execute without verifying AUTH token
3. **Information disclosure** — `DEBUG_DUMP` returns cleartext auth token and firmware buffer contents

#### 5 Attack Suites (36 unique attacks)

| Suite | Attacks | Target |
|-------|---------|--------|
| Suite 1: Firmware | 18 attacks (9 categories) | QEMU ARM firmware via UART-over-TCP |
| Suite 2: Network | 14 attacks (5 sub-suites) | MQTT, TCP/IP, Zigbee, infrastructure, traffic analysis |
| Suite 3: Phantom-Delay | 3 variants | IoT timeout behaviors via transparent TCP proxying |
| Suite 4: Malicious SmartApp | 1 attack | SmartThings Schema Connector HTTP API (CVSS 8.8) |
| Suite 5: ESP32 Buffer Overflow | 1 attack | 128-byte command buffer stack overflow (CVSS 9.8) |

### 3.4 Implementation (`sections/04_implementation.tex`)

Key implementation decisions:

- **3D Simulation:** Habitat-Lab v0.3.0, HSSD-Hab and Replica-CAD datasets, shortest-path follower navigation
- **Firmware Containers:** Dockerfile installs QEMU 10.2, copies pre-compiled ELF, exposes TCP port 15000; `DeviceFirmwareManager` wraps Docker SDK with health checks; `DeviceType` enum with 28 name aliases for fuzzy resolution
- **Firmware Build:** Shared Makefile with `arm-none-eabi-gcc`, custom linker script (`linker.ld`), 64 KB flash / 20 KB SRAM, PL011-compatible UART at `0x4000C000`
- **LLM Integration:** OpenAI-compatible API via LMStudio on localhost:1234; GPT-OSS 20B with 4-bit GGUF quantization on Apple M2 Pro (32 GB RAM)
- **SmartThings:** Flask app handling lifecycle webhooks, SQLite-backed OAuth2 token store, auto-generated device profiles
- **Network:** Configurable Docker network (bridge/macvlan/ipvlan/host), MQTT broker (32 concurrent clients), protocol simulators (Zigbee, Z-Wave, BLE)
- **Wireshark Integration:** tshark 4.6.3 on loopback + InstrumentedSocket wrapper for dual-path capture
- **Configuration:** Single YAML file with Pydantic model, seed 42

### 3.5 Evaluation (`sections/05_evaluation.tex`)

**Experimental Setup:** Apple M2 Pro, 32 GB RAM, macOS 14; seed 42; 5 repetitions with 95% CIs; GPT-OSS 20B via LMStudio.

#### RQ1: Activity Realism

- **Method:** 30 days of schedules for each of 10 personas; compared against CASAS (Aruba, Milan, Cairo) and ARAS (House A, House B) using KL divergence, JS divergence, Wasserstein distance, temporal correlation
- **Results:**
  - Mean JS divergence = 0.218
  - ARAS (ground-truth labels): JS = 0.101-0.113 (best match)
  - CASAS (room-proxy labels): JS = 0.278-0.301 (category coverage mismatch)
  - Temporal correlation averages 0.573 (CASAS: 0.80-0.87; ARAS: 0.17-0.21)
- **Baseline comparison:** VESPER outperforms Markov baseline on ARAS by 50-62% in JS divergence (0.113 vs 0.297 on ARAS-A; 0.101 vs 0.202 on ARAS-B), while Markov wins on homogeneous CASAS homes

#### RQ2: Scalability

- **Device scaling:** 5 to 200 devices — throughput 435 to 10,901 events/s (25x increase), CPU < 36%, memory < 106 MB
- **Container scaling:** 3 to 20 QEMU containers — startup < 7 s total, TCP latency 2-19 ms
- **Duration stability:** 168 simulated hours — stable throughput (~420 events/s), bounded memory growth (max +24.7 MB), no memory leaks

#### RQ3: Latency Profiling

| Data Path | P50 | P95 | P99 |
|-----------|-----|-----|-----|
| Event bus dispatch | 0.002 ms | 0.003 ms | 0.007 ms (7 μs) |
| DB query | 0.004 ms | 0.005 ms | 0.006 ms |
| SQLite task write | 0.188 ms | 0.406 ms | 2.839 ms |
| LLM schedule generation | 6.33 s | 34.24 s | 51.23 s |

- 47,207 SmartThings cloud updates with zero data loss across 28 scenes

#### RQ4: Sim2Real Transfer

- **Method:** 119-dimensional enriched features (activity distributions, transition matrices, n-grams, temporal entropy, circadian profiles); Random Forest classifier
- **Results:**
  - ARAS-House A: **0.813 accuracy** (F1 = 0.783) — zero-shot, no real training data
  - CASAS: 0.082-0.537 (room-proxy label noise)
  - Domain discrimination near 1.0 on CASAS (trivially separable due to category mismatch)
  - Markov baseline shows comparable or worse discriminability on ARAS

#### RQ5: Security Assessment

- **Scale:** 982 attack instances across 5 suites against 6 device types in 28 scenes
- **Overall exploit rate:** 67.4% (662/982)
- **Mean CVSS 3.1:** 8.1 (8.6 for successful exploits only)

| Suite | Instances | Exploit Rate |
|-------|-----------|-------------|
| Firmware (18 attacks × 28 scenes) | 504 | 58.3% |
| Network (14 attacks × 28 scenes) | 392 | 73.5% |
| Phantom-Delay (3 attacks × 28 scenes) | 84 | 92.9% |
| Malicious SmartApp (standalone) | 1 | 100% |
| ESP32 Buffer Overflow (standalone) | 1 | 100% |

- **CVSS by severity:** Critical ≥ 9.0: 70.6% | High 7.0-8.9: 71.8% | Medium 4.0-6.9: 63.8% | Low < 4.0: 50.0%
- **MITRE ATT&CK:** 83.3% tactic coverage (10/12), 15 unique techniques
- **IoT Cyber Kill Chain:** 100% stage completeness (all 7 stages exploited)
- **CVE validation:** 24 real-world CVEs, 7/10 OWASP IoT Top 10 categories
- **Statistical tests:** All p > 0.05 — balanced, unbiased test suite

##### Packet-Level Traffic Analysis

- **Global pcap:** 154,151 TCP segments, 12.1 MB (`full_session.pcap`)
- **Per-attack pcaps:** 736 files, 31.9 MB total
- **TCP breakdown:** 13,496 SYN | 61,994 DATA (3.36 MB) | 10,070 FIN | 3,255 RST | 65,336 ACK-only
- **Campaign duration:** 2,215.6 seconds (~37 minutes)
- All independently verifiable with Wireshark/tcpdump

##### Phantom-Delay Case Study

- Reproduces all 4 variants from Fu et al. within VESPER's Docker bridge network
- State-update delay, erroneous automation execution, routine invalidation, action reorder
- Mean CVSS 8.8 (Critical)
- Validates VESPER as a reproduction platform for published IoT attacks

#### Large-Scale Autonomous Evaluation

| Metric | Value |
|--------|-------|
| Scenes evaluated | 28 (HSSD articulated) |
| Simulated days per scene | 7 |
| Time acceleration | 60x |
| Total wall-clock time | 88.0 hours |
| Scenes with full navigation | 26/28 (92.9%) |
| Navigation trials | 3,580 |
| Navigation success rate | 94.9% (95% CI: [91.2%, 98.3%]) |
| Mean SPL | 1.000 |
| LLM-generated tasks | 4,307 |
| Task completion rate | 83.1% |
| SmartThings cloud updates | 47,207 (100% push success) |
| Room coverage | 62.3% (95% CI: [55.3%, 69.3%]) |
| Per-scene devices | 15-64 (mean 30.9) |
| Per-scene rooms | 6-35 |

### 3.6 Discussion (`sections/06_discussion.tex`)

**Limitations:**

- CASAS room-proxy labeling inflates divergence metrics (no explicit activity annotations)
- Domain discrimination near 1.0 on CASAS (9 vs 3-4 categories = trivially separable)
- SPL = 1.0 follows from shortest-path follower (validates reachability, not learned policies)
- QEMU lacks analog peripherals, RF propagation, cycle-accurate timing

**Threats to Validity:**

- *Internal:* LLM non-determinism mitigated by multiple schedules + confidence intervals
- *External:* Western household bias in CASAS/ARAS; 28 HSSD scenes are a subset of possible layouts
- *Construct:* KL/JS divergence as proxies for "realism" (human evaluation studies would complement)

**Other Discussion Points:**

- Firmware fidelity: instruction-level accuracy but not analog/RF; 47,207 state changes with zero data loss
- LLM: GPT-OSS 20B with retry mechanism brings effective failure rate below 1%
- Scalability ceiling: Docker QEMU overhead; gVisor or Kubernetes could raise ceiling
- Navigation: 5.1% failure rate from furniture collisions and multi-floor navmesh discontinuities
- Privacy/Ethics: synthetic data only, fictional personas, researcher's own SmartThings account
- Security platform: pcap validation provides independently verifiable evidence of network-realistic attacks

### 3.7 Related Work (`sections/07_related_work.tex`)

Comparison against 9 existing platforms across 6 capability dimensions:

| Platform | 3D Sim | Firmware | Cloud IoT | Activity Gen | Security | Open Source |
|----------|--------|----------|-----------|-------------|----------|------------|
| **VESPER** | **Yes** | **Yes** | **Yes** | **Yes** | **Yes** | **Yes** |
| Habitat 3.0 | Yes | No | No | No | No | Yes |
| AI2-THOR | Yes | No | No | No | No | Yes |
| iGibson | Yes | No | No | No | No | Yes |
| IoTSim | No | No | Yes | No | No | Yes |
| FIESTA-IoT | No | No | Yes | No | No | Yes |
| DPWSim | No | No | Partial | No | No | Yes |
| SWoTSuite | No | No | Yes | No | No | Yes |
| Firmadyne | No | Yes | No | No | Yes | Yes |

VESPER is the only platform integrating all six capabilities.

### 3.8 Conclusion (`sections/08_conclusion.tex`)

Summarizes all 5 RQ findings and autonomous evaluation results. Claims VESPER is the first platform to integrate embodied 3D simulation with real device firmware execution and live cloud-connected IoT.

**Future Work:**
1. Multi-occupant coordination with inter-agent communication
2. Additional IoT platforms (Google Home, Apple HomeKit)
3. LLM model ablation study
4. Fine-tune compact LLM on successful VESPER schedules
5. Zigbee and Z-Wave protocol stack firmware
6. Domain adaptation techniques for improved Sim2Real transfer

---

## 4. Tables Catalog (19 tables)

| # | File | Label | Description | Key Data Points |
|---|------|-------|-------------|-----------------|
| 1 | `tab_activity_realism.tex` | `tab:activity-realism` | VESPER vs CASAS/ARAS activity distributions | JS divergence 0.101-0.301; temporal correlation 0.573 avg |
| 2 | `tab_autonomous_eval.tex` | `tab:autonomous-eval` | 28-scene end-to-end evaluation summary | 94.9% nav success, 4,307 tasks, 47,207 cloud updates |
| 3 | `tab_baseline_comparison.tex` | `tab:baseline-comparison` | VESPER (LLM) vs Markov chain baseline | VESPER 50-62% lower JS on ARAS; Markov wins on CASAS |
| 4 | `tab_cve_validation.tex` | `tab:cve-validation` | CVE cross-reference for 36 attacks | 24 CVEs, 8 vulnerability classes, 7/10 OWASP IoT Top 10 |
| 5 | `tab_cvss_distribution.tex` | `tab:cvss-distribution` | CVSS severity breakdown | 67.4% exploit rate, weighted risk score 5.49 |
| 6 | `tab_device_comparison.tex` | `tab:device-comparison` | Per-device vulnerability analysis | Smart Light 66.7% highest; Motion Sensor 50.0% lowest |
| 7 | `tab_kill_chain.tex` | `tab:kill-chain` | IoT Cyber Kill Chain coverage | 100% completeness across 7 stages |
| 8 | `tab_latency.tex` | `tab:latency` | Platform latency profiling | Event bus P99 = 7 μs |
| 9 | `tab_llm_ablation.tex` | `tab:llm-ablation` | 6 LLM model comparison | GPT-OSS 20B: entropy 2.80, 0% error rate |
| 10 | `tab_mitre_coverage.tex` | `tab:mitre-coverage` | MITRE ATT&CK for IoT mapping | 83.3% tactic coverage (10/12), 15 techniques |
| 11 | `tab_per_scene.tex` | `tab:per-scene` | 28-scene detailed breakdown | 6-35 rooms, 15-64 devices, 59.2-100% nav success |
| 12 | `tab_personas.tex` | `tab:personas` | 10 occupant personas | Ages 19-72, diverse demographics and lifestyles |
| 13 | `tab_protocol_breakdown.tex` | `tab:protocol-breakdown` | TCP segment type analysis | 13,496 SYN / 61,994 DATA / 10,070 FIN / 3,255 RST |
| 14 | `tab_scalability.tex` | `tab:scalability` | Device/container/duration scaling | 10,901 events/s at 200 devices, < 36% CPU |
| 15 | `tab_security_summary.tex` | `tab:security-summary` | 5-suite aggregate security results | 982 attacks, 662 successful (67.4%), mean CVSS 8.1 |
| 16 | `tab_sim2real.tex` | `tab:sim2real` | Sim2Real transfer evaluation | 0.813 accuracy on ARAS-A (119-dim features) |
| 17 | `tab_statistical_tests.tex` | `tab:stat-tests` | Hypothesis testing results | All p > 0.05 (balanced test suite confirmed) |
| 18 | `tab_system_comparison.tex` | `tab:system-comparison` | Platform comparison matrix | VESPER only one with all 6 capabilities |
| 19 | `tab_traffic_analysis.tex` | `tab:traffic-analysis` | Per-attack pcap traffic breakdown | 736 pcap files, 154,151 segments, 12.1 MB |

---

## 5. Figures Catalog (13 figures)

| # | File | Label | Type | Description |
|---|------|-------|------|-------------|
| 1 | `fig_architecture.tex` + `architecture.png` | `fig:architecture` | System diagram | 7-layer architecture with central event bus |
| 2 | `fig_activity_dist.tex` | `fig:activity-dist` | Stacked bar chart | 9-category activity distribution: VESPER vs CASAS vs ARAS |
| 3 | `fig_scalability.tex` | `fig:scalability` | Dual-axis line plot | Throughput + CPU utilization vs device count (5-200) |
| 4 | `fig_latency_cdf.tex` | `fig:latency-cdf` | CDF (log-scale x-axis) | Event bus, DB query, SQLite write latency distributions |
| 5 | `fig_sim2real.tex` | `fig:sim2real` | Grouped bar chart | Transfer accuracy across 5 datasets (RF + GB classifiers) |
| 6 | `fig_temporal.tex` | `fig:temporal` | Hourly bar chart | Circadian activity distribution (4,307 tasks, 28 scenes) |
| 7 | `fig_scatter.tex` | `fig:scatter-toggles` | Scatter + regression | Navmesh area vs SmartThings proximity toggles |
| 8 | `fig_schedule_example.tex` | `fig:schedule-example` | Table | Example daily schedule (Alex persona, weekday, 13 activities) |
| 9 | `fig_cvss_distribution.tex` + `.pdf` | `fig:cvss-distribution` | Histogram + box plot | CVSS score distribution by attack layer |
| 10 | `fig_device_heatmap.tex` + `.pdf` | `fig:device-heatmap` | Heatmap | Device type × attack category exploit success rates |
| 11 | `fig_kill_chain.tex` + `.pdf` | `fig:kill-chain` | Stage diagram | 7-stage IoT Cyber Kill Chain coverage |
| 12 | `fig_tte_boxplot.tex` + `.pdf` | `fig:tte-boxplot` | Box plot (log-scale) | Time-to-exploit by CVSS severity |
| 13 | `fig_ablation.tex` | `fig:ablation` | Bar chart + error bars | Schedule entropy across 6 LLM models |

Additional pre-rendered assets: `fig_attack_surface.pdf`, `fig_mitre_tactics.pdf`

---

## 6. Code / Page Logic

### Compilation Flow

```
main.tex
  ├── \documentclass[sigconf,anonymous]{acmart}
  ├── Package imports (booktabs, siunitx, pgfplots, tikz, listings, etc.)
  ├── Custom macros (\sys{} → "VESPER", colors for architecture diagram)
  ├── Abstract + keywords
  ├── \maketitle
  ├── \input{sections/01_intro}
  │     └── Contributions list (itemize)
  ├── \input{sections/02_background}
  │     └── 4 subsections (smart home sim, firmware testing, LLMs, SmartThings)
  ├── \input{sections/03_system_design}
  │     ├── \includegraphics{figures/architecture.png}
  │     ├── 7 subsections (one per architecture layer)
  │     ├── \input{tables/tab_personas}
  │     └── \input{figures/fig_schedule_example}
  ├── \input{sections/04_implementation}
  │     └── 8 subsections (3D sim, firmware, LLM, SmartThings, network, attacks, security eval, config)
  ├── \input{sections/05_evaluation}
  │     ├── RQ1: \input{tab_activity_realism, tab_baseline_comparison, fig_activity_dist}
  │     ├── RQ2: \input{tab_scalability, fig_scalability}
  │     ├── RQ3: \input{tab_latency, fig_latency_cdf}
  │     ├── RQ4: \input{tab_sim2real, fig_sim2real}
  │     ├── RQ5: \input{tab_security_summary, tab_cvss_distribution, tab_device_comparison,
  │     │         fig_device_heatmap, tab_mitre_coverage, tab_kill_chain, tab_cve_validation,
  │     │         tab_statistical_tests, fig_cvss_distribution, fig_kill_chain, fig_tte_boxplot,
  │     │         tab_traffic_analysis, tab_protocol_breakdown}
  │     ├── Phantom-Delay case study
  │     └── Autonomous eval: \input{tab_autonomous_eval, fig_temporal, tab_per_scene, fig_scatter}
  ├── \input{sections/06_discussion}
  │     └── Limitations, threats to validity, ethics, broader impact
  ├── \input{sections/07_related_work}
  │     └── \input{tab_system_comparison}
  ├── \input{sections/08_conclusion}
  │     └── Summary of 5 RQs + future work (6 items)
  ├── Data Availability paragraph
  └── \bibliography{references}
```

### Key Design Patterns

1. **Modular sections:** Each section is a standalone `.tex` file `\input{}`-ed by `main.tex`
2. **Inline table/figure inclusion:** Tables and figures are `\input{}`-ed directly where discussed in the evaluation section, keeping content close to narrative
3. **Consistent table style:** All tables use `booktabs` formatting (`\toprule`, `\midrule`, `\bottomrule`)
4. **Mixed figure generation:** Some figures are TikZ/pgfplots code (programmatic charts), others reference pre-rendered PDFs via `\includegraphics`
5. **Cross-referencing:** Heavy use of `\Cref{}` (cleveref) for automatic "Table X", "Figure Y" formatting
6. **Custom macros:** `\sys{}` expands to `\textsc{VESPER}` throughout
7. **Structured around 5 RQs:** Evaluation section has a clear methodology → results pattern for each research question

### Bibliography

`references.bib` contains all cited works, referenced via `\cite{}` and compiled with `ACM-Reference-Format` bibliography style.

---

## 7. Key Numbers at a Glance

| Metric | Value |
|--------|-------|
| Firmware device types | 6 |
| Occupant personas | 10 |
| Attack suites | 5 (36 unique attacks) |
| Total attack instances evaluated | 982 |
| Overall exploit rate | 67.4% |
| Mean CVSS 3.1 score | 8.1 |
| OWASP IoT Top 10 coverage | 7/10 |
| Real-world CVEs validated | 24 |
| MITRE ATT&CK tactic coverage | 83.3% (10/12) |
| Kill chain completeness | 100% (7/7 stages) |
| Event bus P99 latency | 7 μs |
| Max device throughput | 10,901 events/s |
| HSSD scenes evaluated | 28 |
| Wall-clock runtime | 88.0 hours |
| Navigation success rate | 94.9% |
| LLM-generated tasks | 4,307 |
| SmartThings cloud updates | 47,207 (0% data loss) |
| Sim2Real transfer accuracy (ARAS-A) | 0.813 |
| Mean JS divergence | 0.218 |
| Global pcap capture | 154,151 TCP segments / 12.1 MB |
| Per-attack pcap files | 736 (31.9 MB) |
