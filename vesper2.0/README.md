# VESPER — Virtual Environment for Smart-home Platform Evaluation & Research

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?logo=docker&logoColor=white)](docker/)
[![Habitat 3.0](https://img.shields.io/badge/Habitat-3.0-orange.svg)](https://aihabitat.org/)
[![Attacks](https://img.shields.io/badge/attacks-36_unique-red.svg)](#rq5-security-assessment)
[![pcap verified](https://img.shields.io/badge/pcap-154%2C151_packets-blueviolet.svg)](#step-9-pcap-validated-traffic-capture-rq5)

> **Paper:** *VESPER: A High-Fidelity Smart Home Simulation Platform with Firmware-in-the-Loop and LLM-Driven Activity Generation*
>
> Accepted at [Conference Name], 2026.

VESPER is a full-stack IoT simulation platform that bridges **virtual smart-home devices** to **real cloud platforms** (Samsung SmartThings). Each virtual device runs compiled ARM firmware inside QEMU, packaged in its own Docker container, and is controllable from your phone. The platform integrates:

- **Firmware-in-the-loop emulation** — real ARM Cortex-M3 firmware in QEMU Docker containers
- **LLM-driven activity generation** — GPT-OSS 20B generates daily schedules from 10 diverse personas
- **3D embodied simulation** — Habitat 3.0 with HSSD scenes and humanoid navigation
- **Bi-directional cloud sync** — Samsung SmartThings Schema Connector
- **Five-suite security framework** — 36 attacks with pcap-validated evidence (154,151 TCP segments, 12.1 MB)

```
SmartThings App (Phone)
        │
        ▼
  SmartThings Cloud ◄──── HTTPS (ngrok) ────► VESPER Schema Connector
                                                      │
                                    ┌─────────────────┼─────────────────┐
                                    ▼                 ▼                 ▼
                              ┌──────────┐     ┌──────────┐     ┌──────────┐
                              │ Docker   │     │ Docker   │     │ Docker   │
                              │ QEMU ARM │     │ QEMU ARM │     │ QEMU ARM │
                              │ Firmware │     │ Firmware │     │ Firmware │
                              └──────────┘     └──────────┘     └──────────┘
                              Kitchen Light    Living Room      Bedroom Light
                                    │                 │                 │
                              ┌─────┴─────────────────┴─────────────────┘
                              ▼
                    Docker Network (bridge / macvlan / ipvlan / host)
                              │
                    ┌─────────┼─────────┐
                    ▼                   ▼
         tshark pcap capture   Wireshark Live Capture
       (per-attack + global)       (optional GUI)
```


---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Reproducing the Paper Experiments](#reproducing-the-paper-experiments)
  - [Prerequisites](#prerequisites-for-experiments)
  - [Step 1: Environment Setup](#step-1-environment-setup)
  - [Step 2: Compile Firmware & Build Docker Image](#step-2-compile-firmware--build-docker-image)
  - [Step 3: Download Datasets](#step-3-download-datasets)
  - [Step 4: Start the LLM Server](#step-4-start-the-llm-server)
  - [Step 5: Run the Autonomous Evaluation (RQ1–RQ4)](#step-5-run-the-autonomous-evaluation-rq1rq4)
  - [Step 6: Run the RQ Experiments (RQ1–RQ4)](#step-6-run-the-rq-experiments-rq1rq4)
  - [Step 7: Run the Security Assessment (RQ5)](#step-7-run-the-security-assessment-rq5)
  - [Step 8: Run Standalone Attack Suites (Suites 4 & 5)](#step-8-run-standalone-attack-suites-suites-4--5)
  - [Step 9: pcap-Validated Traffic Capture (RQ5)](#step-9-pcap-validated-traffic-capture-rq5)
  - [Step 10: Generate Paper Figures](#step-10-generate-paper-figures)
- [Modes of Operation](#modes-of-operation)
- [Network Configuration](#network-configuration)
- [SmartThings Setup](#smartthings-setup-optional)
- [Artifact & Reproducibility](#artifact--reproducibility)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Tests](#tests)
- [Tech Stack](#tech-stack)
- [License](#license)

---

## Features

- **Real Firmware Emulation** — ARM Cortex-M3 firmware compiled with `arm-none-eabi-gcc`, running in QEMU
- **Docker-per-Device** — Each virtual IoT device is an isolated Docker container
- **6 Device Types** — Smart light, motion sensor, temperature sensor, humidity sensor, door sensor, smart plug
- **SmartThings Bi-Directional Sync** — Devices appear in the Samsung SmartThings app with real-time sync
- **LLM-Driven Activity Generation** — GPT-OSS 20B generates realistic daily schedules from 10 diverse personas
- **3D Habitat Integration** — Habitat 3.0 with HSSD scenes, humanoid navigation, and proximity-based automation
- **Five-Suite Security Framework** — 36 unique attacks (18 firmware + 14 network + 3 phantom-delay + 1 SmartApp + 1 ESP32 overflow) with CVSS 3.1 scoring and MITRE ATT&CK mapping
- **pcap-Validated Evidence** — tshark captures every TCP segment during attacks: 154,151 packets (12.1 MB) in 736 per-attack pcap files + global session capture, independently verifiable with Wireshark
- **Configurable Docker Networking** — Bridge, macvlan, ipvlan, and host modes with Wireshark live-capture support
- **Automated Evaluation Pipeline** — Reproducible experiments with LaTeX table/figure generation
- **Event-Driven Architecture** — Pub/sub event bus with sub-millisecond dispatch (P99 = 7 μs)

---

## Quick Start

### Prerequisites

| Tool | Version | Install (macOS) | Install (Linux) |
|------|---------|-----------------|-----------------|
| Python | 3.9+ | `brew install python@3.9` | `sudo apt install python3.9` |
| Docker | 20+ | [docker.com](https://www.docker.com/products/docker-desktop/) | [docker.com](https://docs.docker.com/engine/install/) |
| ngrok | 3+ | `brew install ngrok` | `snap install ngrok` |
| ARM GCC | 13+ | `brew install arm-none-eabi-gcc` | `sudo apt install gcc-arm-none-eabi` |
| QEMU | 8+ | `brew install qemu` | `sudo apt install qemu-system-arm` |
| tshark | 4.0+ | `brew install wireshark` | `sudo apt install tshark` |
| Conda | — | [miniforge](https://github.com/conda-forge/miniforge) | [miniforge](https://github.com/conda-forge/miniforge) |

### 1. Clone & Install

```bash
git clone https://github.com/huuhuannt1998/vesper2.0.git
cd vesper

# Create conda environment (recommended for Habitat support)
conda create -n vesper python=3.9 cmake=3.22 -y
conda activate vesper

# Install Habitat-Sim with Bullet physics
conda install habitat-sim withbullet -c conda-forge -c aihabitat

# Install VESPER and all dependencies
pip install -e ".[all]"
pip install aiohttp
```

### 2. Compile the Firmware

```bash
cd vesper/firmware/samples
make
# → Produces 6 firmware .elf files (one per device type)
cd ../../..
```

### 3. Build the Docker Image

```bash
docker build -f docker/Dockerfile.device -t vesper-qemu-arm:latest .
```

### 4. Run a Quick Demo

```bash
# Firmware-only demo (no cloud, no 3D)
python scripts/firmware_demo.py

# 3D environment with humanoid navigation
python scripts/vesper_objectnav_camera_humanoid.py

# Full stack with SmartThings (requires ngrok + credentials)
python scripts/vesper_smartthings.py
```

---

## Reproducing the Paper Experiments

This section provides step-by-step instructions to reproduce all five research questions (RQ1–RQ5) and the large-scale autonomous evaluation from the paper.

### Prerequisites for Experiments

- **Hardware:** Apple M2 Pro or equivalent (32 GB RAM recommended)
- **OS:** macOS 14+ or Ubuntu 22.04+
- **Disk:** ~20 GB free (datasets + Docker images)
- **Docker:** Must be running with at least 8 GB RAM allocated
- **LMStudio:** Required for LLM-based schedule generation

### Step 1: Environment Setup

```bash
# Create and activate conda environment
conda create -n vesper python=3.9 cmake=3.22 -y
conda activate vesper

# Install Habitat-Sim with Bullet physics
conda install habitat-sim withbullet -c conda-forge -c aihabitat

# Install VESPER with all dependencies
pip install -e ".[all]"
pip install aiohttp scipy matplotlib seaborn
```

### Step 2: Compile Firmware & Build Docker Image

```bash
# Compile all 6 firmware variants (smart_light, motion_sensor, etc.)
cd vesper/firmware/samples
make
cd ../../..

# Build the QEMU ARM Docker image
docker build -f docker/Dockerfile.device -t vesper-qemu-arm:latest .

# Verify the image
docker run --rm vesper-qemu-arm:latest ls /firmware/
# Should list: smart_light.elf  motion_sensor.elf  temperature_sensor.elf
#              humidity_sensor.elf  door_sensor.elf  smart_plug.elf
```

### Step 3: Download Datasets

```bash
# Download HSSD-Hab scenes, humanoid assets, and test scenes (~12 GB)
python -m habitat_sim.utils.datasets_download --uids \
    hssd-hab habitat_humanoids hab_fetch \
    hab3_bench_assets replica_cad_dataset habitat_test_scenes \
    --data-path data/
```

For the Sim2Real evaluation (RQ4), you also need CASAS and ARAS datasets:
```bash
# Download CASAS and ARAS datasets
python vesper/evaluation/download_datasets.py
# → Downloads to data/datasets/casas/ and data/datasets/aras/
```

### Step 4: Start the LLM Server

The evaluation uses **GPT-OSS 20B** for generating activity schedules. You need a local LLM server:

1. Download and install [LMStudio](https://lmstudio.ai/)
2. Download a model (GPT-OSS 20B recommended, GGUF format, 4-bit quantization)
3. Load the model in LMStudio
4. Start the local server → it runs at `http://localhost:1234` by default

Verify it's running:
```bash
curl http://localhost:1234/v1/models
```

### Step 5: Run the Autonomous Evaluation (RQ1–RQ4)

This is the **main experiment** from the paper: 28 HSSD scenes × 7 simulated days with full SmartThings cloud integration and automated security testing.

#### Without SmartThings (simpler, no cloud credentials needed)

```bash
conda activate vesper
python scripts/run_autonomous_eval.py \
    --num-scenes 28 \
    --num-days 7 \
    --time-acceleration 60 \
    --headless
```

#### With SmartThings Cloud Sync (full paper configuration)

```bash
# Terminal 1: Start ngrok tunnel
ngrok http 8443

# Terminal 2: Set credentials and run
export SMARTTHINGS_CLIENT_ID="your-oauth-client-id"
export SMARTTHINGS_CLIENT_SECRET="your-oauth-client-secret"
export ST_APP_CLIENT_SECRET="your-app-client-secret"

conda activate vesper
python scripts/run_autonomous_eval.py \
    --num-scenes 28 \
    --num-days 7 \
    --with-smartthings \
    --time-acceleration 60 \
    --headless
```

**Parameters:**

| Flag | Description | Paper Value |
|------|-------------|-------------|
| `--num-scenes N` | Number of HSSD scenes to evaluate | 28 |
| `--num-days D` | Simulated days per scene | 7 |
| `--time-acceleration X` | Speedup factor (60× = 1 sim-day per 24 min) | 60 |
| `--with-smartthings` | Enable SmartThings cloud sync | Yes |
| `--headless` | No 3D visualization (faster) | Yes |
| `--allow-fallback-tasks` | Use emergency schedule on LLM failure | Yes |

**Expected runtime:** ~88 hours on Apple M2 Pro for 28 scenes × 7 days.

**Monitor progress:**
```bash
# Follow live output
tail -f logs/vesper_objectnav_*.log

# Check navigation success
grep "Navigation trials" logs/vesper_objectnav_*.log

# Check SmartThings sync
grep "proximity_toggles" logs/vesper_objectnav_*.log
```

**Results location:**
```
results/vesper_autonomous_eval/
├── <scene_id>/              # Per-scene results (28 folders)
│   ├── eval_results.json    # Detailed per-scene data
│   └── *.log                # Scene-specific logs
├── eval_summary.txt         # Aggregate summary across all scenes
└── eval_metadata.json       # Configuration and timestamps
```

### Step 6: Run the RQ Experiments (RQ1–RQ4)

The evaluation framework provides individual RQ experiments via the `ExperimentRunner`:

```bash
conda activate vesper

# Run ALL RQ experiments (RQ1–RQ4)
python -m vesper.evaluation.experiment_runner \
    --config vesper/evaluation/configs/full_evaluation.yaml \
    --output results/full_evaluation

# Or run individual experiments:

# RQ1: Activity Realism (JS divergence against CASAS/ARAS)
python -m vesper.evaluation.experiment_runner \
    --experiment activity \
    --output results/rq1_activity

# RQ2: Scalability (5→200 devices, throughput, CPU, memory)
python -m vesper.evaluation.experiment_runner \
    --experiment scalability \
    --output results/rq2_scalability

# RQ3: Latency (event bus, database, LLM profiling)
python -m vesper.evaluation.experiment_runner \
    --experiment latency \
    --output results/rq3_latency

# RQ4: LLM Ablation (6 models × 50 attempts)
# Requires all 6 models loaded in LMStudio
python -m vesper.evaluation.experiment_runner \
    --experiment llm \
    --output results/rq4_llm_ablation
```

**Generate a default config:**
```bash
python -m vesper.evaluation.experiment_runner --generate-config
# → Creates configs/evaluation.yaml that you can customize
```

**Key configuration** (`vesper/evaluation/configs/full_evaluation.yaml`):
```yaml
seed: 42
num_trials: 5
confidence_level: 0.95
device_counts: [5, 10, 25, 50, 100, 200]     # RQ2
latency_iterations: 1000                       # RQ3
comparison_days: 30                            # RQ1
```

### Step 7: Run the Security Assessment (RQ5)

The security assessment runs five attack suites: 18 firmware attacks × 6 devices × 28 scenes (504), 14 network attacks × 28 scenes (392), 3 phantom-delay variants × 28 scenes (84), plus 2 standalone demonstrations — totaling 982 attack instances.

```bash
conda activate vesper

# Run the full per-scene attack suites (firmware + network + phantom-delay)
python scripts/run_attack_demo.py

# Run firmware attacks only
python scripts/run_attack_demo.py --firmware-only

# Run network attacks only
python scripts/run_attack_demo.py --network-only

# Run phantom-delay attacks only
python scripts/run_attack_demo.py --phantom-delay-only

# Target a specific device type
python scripts/run_attack_demo.py --device-type smart_light

# Use Docker containers (recommended for full fidelity)
python scripts/run_attack_demo.py --use-docker
```

**Parameters:**

| Flag | Description | Default |
|------|-------------|---------|
| `--firmware-only` | Run only the 18 firmware attacks per device | Off |
| `--network-only` | Run only the 14 network attacks | Off |
| `--phantom-delay-only` | Run only the 3 phantom-delay variants | Off |
| `--device-type TYPE` | Target one device (e.g., `smart_light`) | All 6 |
| `--use-docker` | Use Docker containers for QEMU | Off |
| `--base-port PORT` | Base TCP port for QEMU instances | 15020 |
| `--mqtt-port PORT` | MQTT broker port for network attacks | 11883 |
| `--output-dir DIR` | Output directory for attack results | `results/security` |

**Expected runtime:** ~15–20 minutes for all per-scene attacks.

**Results location:**
```
results/security/
├── firmware_attacks_smart_light_*.json
├── firmware_attacks_motion_sensor_*.json
├── firmware_attacks_temperature_sensor_*.json
├── firmware_attacks_humidity_sensor_*.json
├── firmware_attacks_door_sensor_*.json
├── firmware_attacks_smart_plug_*.json
├── network_attacks_*.json
├── phantom_delay_attacks_*.json
└── security_summary_*.json
```

### Step 8: Run Standalone Attack Suites (Suites 4 & 5)

Two additional attack suites target the SmartThings cloud API and ESP32 firmware directly:

```bash
conda activate vesper

# Suite 4: Malicious SmartApp (CVSS 8.8)
# Targets SmartThings Schema Connector OAuth and device enumeration
python scripts/attacks/smartapp.py

# Suite 5: ESP32 Buffer Overflow (CVSS 9.8)
# Targets ESP32 command buffer with 137-byte crafted payload
python scripts/attacks/esp32_overflow.py --target <device-ip>:15011

# Run the relay attack against a firmware device
python scripts/attacks/relay.py --target <device-ip>:15011
```

**Results location:**
```
results/
├── smartapp_attack_output.txt
└── esp32_overflow_attack_output.txt
```

### Step 9: pcap-Validated Traffic Capture (RQ5)

This step captures **every TCP segment** during the full attack campaign using `tshark` (Wireshark CLI), producing genuine `.pcap` files that are independently verifiable with Wireshark, `tcpdump`, or any libpcap-compatible tool.

**Prerequisites:** Two QEMU firmware containers must be running:
```bash
# Start two firmware containers on ports 15011 and 15012
docker run -d --name vesper-fw-1 -p 15011:15000 vesper-qemu-arm:latest
docker run -d --name vesper-fw-2 -p 15012:15000 vesper-qemu-arm:latest

# Verify they respond
echo "STATUS" | nc -w2 127.0.0.1 15011
echo "STATUS" | nc -w2 127.0.0.1 15012
```

**macOS BPF permissions** (required for tshark capture on loopback):
```bash
brew install --cask wireshark-chmodbpf
# Restart terminal after installation
```

**Run the pcap-validated attack campaign:**
```bash
conda activate vesper
python scripts/pcap_attack_capture.py
```

This executes all 35 attacks × 28 scenes = **980 attack instances** against the live firmware containers, with:
- A dedicated `tshark` process per attack capturing to an individual `.pcap` file
- A global `tshark` process recording the entire session to `full_session.pcap`
- BPF filter: `tcp port 15011 or tcp port 15012`

**Expected runtime:** ~37 minutes.

**Verify the captures:**
```bash
# Global session statistics
tshark -r results/pcap_analysis/pcaps/full_session.pcap -q -z io,stat,300

# Count SYN packets (connection attempts)
tshark -r results/pcap_analysis/pcaps/full_session.pcap -Y "tcp.flags.syn==1" | wc -l

# TCP conversation summary (6,748 unique conversations)
tshark -r results/pcap_analysis/pcaps/full_session.pcap -q -z conv,tcp | tail -5

# Inspect an individual attack pcap in Wireshark
wireshark results/pcap_analysis/pcaps/DoS_Rapid_Commands_scene102343_p15011.pcap

# Follow a TCP stream
tshark -r results/pcap_analysis/pcaps/Buffer_Overflow_Cmd_scene102343_p15011.pcap \
    -z follow,tcp,ascii,0
```

**Results location:**
```
results/pcap_analysis/
├── pcap_campaign.json           # Full campaign results (19,640 lines)
├── pcap_attacks.csv             # 980 rows, one per attack execution
└── pcaps/
    ├── full_session.pcap        # Global capture (154,151 packets, 12.1 MB)
    ├── DoS_Rapid_Commands_scene102343_p15011.pcap
    ├── Buffer_Overflow_Cmd_scene102343_p15011.pcap
    ├── TCP_Flood_scene102343_p15011.pcap
    └── ... (736 per-attack pcap files, 31.9 MB total)
```

**Expected output:**

| Metric | Value |
|--------|-------|
| Total attack executions | 980 (28 scenes × 35 attacks) |
| Successful exploits | 503 (51.3%) |
| Per-attack pcap packets | 152,970 |
| Per-attack pcap bytes | 12,047,604 (12.0 MB) |
| Global capture packets | 154,151 |
| Global capture bytes | 12,113,002 (12.1 MB) |
| Individual pcap files | 736 |
| Total pcap disk usage | 31.9 MB |
| Session duration | 2,215.6 seconds (~37 min) |
| TCP conversations | 6,748 |
| SYN segments | 13,496 |
| DATA segments | 61,994 (3.36 MB payload) |
| FIN segments | 10,070 |
| RST segments | 3,255 |

### Step 10: Generate Paper Figures

After running all experiments, regenerate the publication-quality PDF figures:

```bash
conda activate vesper

# Generate all PDF figures for the paper
python scripts/generate_paper_figures.py
# → Outputs to paper-latex/figures/

# Analyze the autonomous evaluation data
python scripts/analyze_eval.py
# → Outputs aggregate statistics to results/analysis_output.txt
```

### Expected Results Summary

If everything runs correctly, you should see results comparable to:

| Metric | Expected Value |
|--------|---------------|
| **Autonomous Evaluation (28 scenes × 7 days)** | |
| Wall-clock runtime | ~88.0 hours |
| Scenes with navigation | 26 of 28 |
| Navigation success rate | 94.9% (3,580 trials) |
| Tasks scheduled (LLM) | 4,307 |
| Task completion rate | 83.1% |
| SmartThings cloud updates | 47,207 (zero data loss) |
| Articulated object interactions | 11,936 |
| Room coverage | 62.3% (95% CI: [55.3%, 69.3%]) |
| **RQ1: Activity Realism** | |
| Mean JS divergence | ~0.146 (95% CI: [0.10, 0.19]) |
| Best match | ARAS-House B (JS = 0.093) |
| **RQ2: Scalability** | |
| Max throughput (200 devices) | ~10,901 events/s |
| CPU at 200 devices | <36% |
| Memory at 200 devices | <106 MB |
| **RQ3: Latency** | |
| Event bus P99 | ~7 μs |
| Database write P99 | ~2.84 ms |
| **RQ5: Security Assessment** | |
| Total attack instances | 982 (5 suites) |
| Exploited | 662 (67.4%) |
| Firmware exploit rate | 58.3% (294/504) |
| Network exploit rate | 73.5% (288/392) |
| Phantom-delay exploit rate | 92.9% (78/84) |
| Mean CVSS 3.1 | 8.1 |
| MITRE ATT&CK coverage | 83% (10/12 tactics) |
| Kill chain completeness | 100% (7/7 stages) |
| **pcap Traffic Capture (28 scenes × 35 attacks)** | |
| Global pcap packets | 154,151 (12.1 MB) |
| Per-attack pcap packets | 152,970 (12.0 MB) |
| Individual pcap files | 736 (31.9 MB total) |
| TCP conversations | 6,748 |
| Session duration | 2,215.6 s (~37 min) |

> **Note:** Exact numbers may vary slightly due to LLM non-determinism, system load, and network conditions. Confidence intervals account for this variation. Two scenes may fail to generate navmeshes — these scenes still run security attacks successfully but produce no navigation trials.

---

## Modes of Operation

### 1. Full Stack — SmartThings + Docker + Firmware *(recommended)*

The primary mode. Real compiled firmware in Docker containers, synced to the SmartThings cloud.

```bash
python scripts/unified_smartthings_firmware.py
```

### 2. Firmware Demo — No Cloud

Test QEMU firmware devices locally with interactive serial communication:

```bash
python scripts/firmware_demo.py
```

Type commands (`ON`, `OFF`, `GET_TEMP`, `STATUS`) directly in the terminal.

### 3. Simulated Sensors — No Docker, No QEMU

Pure-Python sensor simulation for rapid prototyping:

```bash
python scripts/simulated_sensors_demo.py
python scripts/simulated_sensors_demo.py --interactive
python scripts/simulated_sensors_demo.py --room kitchen
```

Supports motion, temperature, humidity, door/window, light, smoke, CO2, water leak, thermostat, and smart plug sensors.

### 4. Docker Compose

Spin up the full device fleet without the Python server:

```bash
cd docker
docker compose up -d
docker compose ps
docker compose down
```

### 5. 3D Habitat Environment *(optional)*

Requires Habitat-Sim via conda. See [Step 1: Environment Setup](#step-1-environment-setup) and [Step 3: Download Datasets](#step-3-download-datasets) in the Reproducing section.

```bash
python scripts/vesper_objectnav_camera_humanoid.py
```

| Key | Action |
|-----|--------|
| W / A / S / D | Move / Turn |
| G | Random navigation goal |
| I | Toggle IoT device panel |
| V | Toggle 1st / 3rd person view |
| ESC | Quit |

---

## Network Configuration

VESPER supports four Docker network modes for different testing scenarios:

| Mode | Use Case | Wireshark | Isolation |
|------|----------|-----------|-----------|
| `bridge` (default) | Standard operation, NAT-based | ✅ via `docker0` | Full |
| `macvlan` | Real LAN integration, each container gets a LAN IP | ✅ native capture | Full |
| `ipvlan` | Similar to macvlan, uses parent interface's MAC | ✅ native capture | Full |
| `host` | Direct host networking, no isolation | ✅ host interface | None |

### Configuring Network Mode

Set the network mode via environment variable or configuration:

```bash
# Use macvlan for real LAN integration + Wireshark capture
export VESPER_NETWORK_MODE=macvlan

# Or in configs/default.yaml:
# network:
#   mode: macvlan
#   parent_interface: en0
#   subnet: 192.168.1.0/24
#   gateway: 192.168.1.1
```

### Wireshark / tshark Capture

VESPER provides two complementary capture paths:

**1. tshark pcap capture (recommended for reproducibility):**
```bash
# Run the full pcap-validated attack campaign
python scripts/pcap_attack_capture.py
# → 736 per-attack pcaps + full_session.pcap in results/pcap_analysis/pcaps/

# Verify with standard Wireshark tooling
tshark -r results/pcap_analysis/pcaps/full_session.pcap -q -z io,stat,300
wireshark results/pcap_analysis/pcaps/full_session.pcap
```

**2. Wireshark GUI live capture (macvlan/ipvlan mode):**
```bash
# Enable Wireshark capture (macvlan mode recommended)
python scripts/unified_smartthings_firmware.py --wireshark

# Capture only MQTT traffic
python scripts/unified_smartthings_firmware.py --wireshark --capture-filter "tcp port 1883"
```

In macvlan mode, Wireshark captures real Ethernet frames on the parent interface, enabling full protocol analysis of MQTT and UART-over-TCP traffic between firmware containers. All `.pcap` files are independently verifiable with any libpcap-compatible tool.

---

## SmartThings Setup (Optional)

For bi-directional cloud sync with the SmartThings app:

1. Go to the [SmartThings Developer Portal](https://developer.smartthings.com)
2. Create a new project → **Device Integration** → **SmartThings Schema Connector**
3. Fill in (use your ngrok URL):

| Field | Value |
|-------|-------|
| App Name | VESPER Smart Home |
| Target URL | `https://<NGROK_URL>/schema` |
| OAuth Authorization URI | `https://<NGROK_URL>/oauth/authorize` |
| Token URI | `https://<NGROK_URL>/oauth/token` |

4. Save and note **two sets** of credentials:
   - **Device Cloud Credentials** → Client ID and Client Secret
   - **App Credentials** (top of page) → Click **Regenerate** if hidden

5. Start ngrok and VESPER:

```bash
# Terminal 1
ngrok http 8443

# Terminal 2
export SMARTTHINGS_CLIENT_ID="your-oauth-client-id"
export SMARTTHINGS_CLIENT_SECRET="your-oauth-client-secret"
export ST_APP_CLIENT_SECRET="your-app-client-secret"
python scripts/vesper_smartthings.py
```

6. Link in the SmartThings app: **+** → **Add device** → **Partner devices** → **VESPER Smart Home**

---

## Artifact & Reproducibility

This repository is the **complete artifact** accompanying the VESPER paper. It contains:

| Artifact | Location | Description |
|----------|----------|-------------|
| Platform source | `vesper/` | ~15,000 lines Python + ~1,600 lines C firmware |
| Attack framework | `vesper/attacks/` | 36 unique attacks across 5 suites (~3,200 lines) |
| Evaluation pipeline | `vesper/evaluation/` | Automated RQ1–RQ5 experiments |
| pcap evidence | `results/pcap_analysis/pcaps/` | 736 per-attack + 1 global `.pcap` (32 MB total) |
| Campaign data | `results/pcap_analysis/` | JSON log (19,640 lines) + CSV (980 rows) |
| Autonomous eval | `results/vesper_autonomous_eval/` | 28-scene × 7-day evaluation outputs |
| Paper source | `paper-latex/` | Full LaTeX source (ACM sigconf, 8 sections, 14 tables) |
| Configs | `configs/`, `vesper/evaluation/configs/` | All experiment YAML configurations |
| Docker | `docker/` | Dockerfile + compose for QEMU ARM containers |

All experiments are reproducible from a single clone:
```bash
git clone https://github.com/huuhuannt1998/vesper2.0.git && cd vesper
pip install -e ".[all]"
# See "Reproducing the Paper Experiments" above for full instructions
```

---

## Project Structure

```
vesper/
├── vesper/                          # Main package (~15,000 lines Python)
│   ├── core/                        # Event bus, environment engine
│   ├── devices/                     # IoT device models
│   ├── agents/                      # LLM-controlled agents (10 personas)
│   ├── firmware/                    # Firmware emulation layer
│   │   ├── samples/                 # ARM Cortex-M3 firmware source (~1,800 lines C)
│   │   │   ├── smart_light.c        # Smart light firmware
│   │   │   ├── motion_sensor.c      # Motion sensor firmware
│   │   │   ├── temperature_sensor.c # Temperature sensor firmware
│   │   │   ├── humidity_sensor.c    # Humidity sensor firmware
│   │   │   ├── door_sensor.c        # Door sensor firmware
│   │   │   ├── smart_plug.c         # Smart plug firmware
│   │   │   ├── linker.ld            # LM3S6965 memory layout (64KB flash / 20KB SRAM)
│   │   │   └── Makefile             # Cross-compilation build
│   │   ├── device_firmware_manager.py  # Docker container lifecycle
│   │   └── qemu_runner.py           # QEMU process management
│   ├── attacks/                     # Security testing framework (~3,200 lines Python)
│   │   ├── firmware_attacks.py      # Suite 1: 18 firmware attacks (9 categories)
│   │   ├── network_attacks.py       # Suite 2: 14 network attacks (5 categories)
│   │   └── phantom_delay_attack.py  # Suite 3: 3 phantom-delay variants
│   ├── network/                     # Configurable Docker networking
│   │   └── home_network.py          # Bridge/macvlan/ipvlan/host + Wireshark capture
│   ├── integrations/                # Cloud platform connectors
│   │   ├── schema_connector.py      # SmartThings Schema Protocol
│   │   └── sync_bridge.py           # Bi-directional state sync
│   ├── evaluation/                  # Automated evaluation pipeline
│   │   ├── experiment_runner.py     # RQ1-RQ4 experiment orchestrator
│   │   ├── security_eval.py         # RQ5 security evaluation
│   │   ├── activity_comparison.py   # Activity realism metrics
│   │   ├── scalability_bench.py     # Scalability benchmarks
│   │   ├── latency_profiler.py      # Latency profiling
│   │   ├── llm_ablation.py          # LLM model comparison
│   │   ├── report_generator.py      # LaTeX table/figure generation
│   │   └── configs/                 # Experiment YAML configs
│   ├── habitat/                     # Habitat 3.0 integration
│   ├── protocol/                    # IoT protocol implementations
│   └── simulation/                  # Simulation engine
├── scripts/
│   ├── vesper_smartthings.py        # Full stack: 3D + firmware + SmartThings
│   ├── run_autonomous_eval.py       # Autonomous 28-scene evaluation
│   ├── run_attack_demo.py           # Per-scene security assessment (Suites 1–3)
│   ├── analyze_eval.py              # Aggregate evaluation analysis
│   ├── generate_paper_figures.py    # Publication-quality PDF figure generation
│   ├── firmware_demo.py             # Standalone QEMU demo
│   ├── vesper_objectnav_camera_humanoid.py  # 3D navigation demo
│   ├── simulated_sensors_demo.py    # Pure-Python sensor demo
│   └── attacks/                     # Standalone attack scripts
│       ├── smartapp.py              # Suite 4: Malicious SmartApp (CVSS 8.8)
│       ├── esp32_overflow.py        # Suite 5: ESP32 Buffer Overflow (CVSS 9.8)
│       ├── relay.py                 # Relay attack utility
│       ├── firmware.py              # Firmware attack CLI
│       └── network.py              # Network attack CLI
├── docker/
│   ├── Dockerfile.device            # QEMU ARM device image
│   ├── docker-compose.yml           # Multi-device orchestration
│   └── entrypoint.sh               # Container startup
├── tests/                           # Unit tests (10 test modules)
├── results/                         # Experiment outputs
├── configs/                         # Default configuration
│   └── default.yaml                 # Network, device, and evaluation defaults
└── paper-latex/                     # Paper source (ACM sigconf)
    ├── main.tex                     # Main document
    ├── sections/                    # 8 section files
    ├── tables/                      # 14 LaTeX tables
    └── figures/                     # TikZ sources + PDF figures
```

---

## Configuration

### Environment Variables

| Variable | Description | Required For |
|----------|-------------|-------------|
| `SMARTTHINGS_CLIENT_ID` | OAuth Client ID (from Device Cloud Credentials) | SmartThings sync |
| `SMARTTHINGS_CLIENT_SECRET` | OAuth Client Secret (from Device Cloud Credentials) | SmartThings sync |
| `ST_APP_CLIENT_SECRET` | SmartThings App Client Secret (from App Credentials) | Bi-directional sync (3D→Phone) |
| `VESPER_NETWORK_MODE` | Docker network mode (`bridge`, `macvlan`, `ipvlan`, `host`) | Network configuration |

 **Critical:** `ST_APP_CLIENT_SECRET` is **required** for 3D→SmartThings proactive state updates. Find it at the top of your SmartThings Developer Portal project page under "App Credentials" (click Regenerate if hidden).

### Adding More Devices

Edit the device list in `scripts/unified_smartthings_firmware.py`:

```python
device_configs = [
    FirmwareDeviceConfig(
        device_id="vesper-fw-garage",
        name="Garage Door Sensor",
        room="Garage",
        device_type=DeviceHandlerType.DIMMER,
        host_port=15004,  # unique port per device
    ),
    # ...
]
```

Then unlink and re-link in the SmartThings app to trigger a new device discovery.

### Programmatic Usage

```python
from vesper.integrations import (
    SmartThingsSchemaConnector,
    SchemaConnectorConfig,
    VirtualDeviceDefinition,
    DeviceHandlerType,
)

config = SchemaConnectorConfig(
    host="0.0.0.0",
    port=8443,
    webhook_path="/schema",
    smartthings_client_id="your-client-id",
    smartthings_client_secret="your-secret",
)
connector = SmartThingsSchemaConnector(config)

device = VirtualDeviceDefinition(
    external_device_id="my-device",
    friendly_name="My Custom Light",
    device_handler_type=DeviceHandlerType.DIMMER,
    manufacturer_name="VESPER",
    model_name="Virtual Light",
)
connector.register_device(device)

async def handle_command(device_id, capability, command, args):
    print(f"Command: {device_id} → {capability}.{command}")
    return True

connector.on_command(handle_command)
await connector.start()
```

---

## Troubleshooting

### Port 8443 already in use

```bash
lsof -ti:8443 | xargs kill -9
```

### ngrok URL changed

Free-tier ngrok assigns a new URL on every restart. Update all three URLs (Target, OAuth, Token) in the [SmartThings Developer Portal](https://developer.smartthings.com).

### Devices not appearing in SmartThings

- Ensure the VESPER server is running **and** ngrok is forwarding
- Check server logs for incoming `discoveryRequest` — if absent, the URL is wrong
- Unlink and re-link in the SmartThings app

### 3D → SmartThings sync not working

- Verify `ST_APP_CLIENT_SECRET` is set (check startup banner)
- SmartThings only sends `grantCallbackAccess` during initial linking — **fully remove** the integration, then re-add it
- If still failing with `INVALID-CLIENT-SECRET`, regenerate App Credentials in Developer Portal

### Docker containers won't start

```bash
docker info                    # Check Docker daemon is running
docker rm -f $(docker ps -aq --filter "name=vesper")   # Remove stale containers
docker build -f docker/Dockerfile.device -t vesper-qemu-arm:latest .  # Rebuild
```

### Firmware won't compile

```bash
arm-none-eabi-gcc --version    # Verify toolchain is installed

# Install if missing:
brew install arm-none-eabi-gcc          # macOS
sudo apt install gcc-arm-none-eabi      # Linux
```

### LLM generation fails

```bash
# Check LMStudio is running
curl http://localhost:1234/v1/models

# Increase timeout in vesper/agents/llm_client.py
# Default: timeout=180 → increase to 300
```

### Out of memory during evaluation

```bash
# Use headless mode (saves ~4GB GPU memory)
python scripts/run_autonomous_eval.py --num-scenes 10 --num-days 7 --headless

# Clean up old Docker containers
docker rm -f $(docker ps -aq --filter "name=vesper-fw")
```

### Navmesh failures

Some HSSD scenes may fail to generate valid navmeshes (preventing humanoid navigation). The evaluation handles this gracefully — security attacks still run in these scenes, but no navigation trials are produced. In our 28-scene evaluation, 2 scenes exhibited this behavior.

---

## Tests

```bash
conda activate vesper
python -m pytest tests/ -v
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Cloud Platform | Samsung SmartThings (Schema Connector) |
| Webhook Server | aiohttp (async Python) |
| HTTPS Tunnel | ngrok |
| Containerization | Docker (bridge / macvlan / ipvlan / host) |
| Firmware Emulation | QEMU 10.2 — ARM Cortex-M3 (LM3S6965EVB) |
| Firmware Toolchain | arm-none-eabi-gcc |
| Firmware Language | C (bare-metal, no stdlib, ~1,800 lines across 6 devices) |
| 3D Environment | Habitat 3.0 / Habitat-Sim, HSSD-Hab scenes |
| LLM Engine | GPT-OSS 20B via LMStudio (OpenAI-compatible API) |
| Security Framework | 5 suites, 36 unique attacks, CVSS 3.1, MITRE ATT&CK for IoT |
| Network Analysis | tshark 4.6.3 pcap capture (loopback + macvlan) + Wireshark GUI |
| Evaluation Framework | Custom Python + LaTeX/PDF auto-generation |

## License

MIT License — See [LICENSE](LICENSE) for details.
