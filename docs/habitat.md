# Optional Habitat workflow

The offline demo uses `MockHabitatSimulator`. Real 3D execution requires [Habitat-Sim](https://github.com/facebookresearch/habitat-sim) and, for embodied resident controllers, [Habitat-Lab](https://github.com/facebookresearch/habitat-lab), along with separately licensed scene/humanoid assets. There is no vendored Habitat source tree in this release.

Use an isolated environment and the upstream installation instructions for your platform. For a reproducible starting point, use the upstream `v0.3.3` tags for both projects, rather than a moving branch. This is an explicit setup baseline, **not a verified reconstruction of the historical evaluation environment**. Record the actual commits, build options, Python/dependency versions and asset revisions for any experiment. Native macOS 3D support depends on the upstream build; this release check validates only the offline path on macOS.

After installing upstream Habitat, install VESPER's optional workflow dependencies:

```bash
python -m pip install -e '.[llm,visualization,network]'
```

The evaluation script uses repository-relative scene paths:

```text
data/scene_datasets/hssd-hab/
  hssd-hab.scene_dataset_config.json
  scenes/
data/humanoids/humanoid_data/
```

Some inherited paths also look for articulated scene assets and `data/versioned_data/`. Follow [upstream dataset instructions](../data/README.md); do not copy broken developer-machine symlinks. The current upstream HSSD articulated layout may differ from the script's historical `scenes-articulated` spelling. Configure/adapt local paths to the acquired revision instead of assuming a newer asset release reproduces old results.

Configure a local OpenAI-compatible LLM endpoint in `.env`/the process environment. For example, after installing the chosen model in your local server and obtaining the assets, a reduced scene workflow is:

```bash
python scripts/run_autonomous_eval.py --headless --num-scenes 1 --num-days 1 \
  --models qwen2.5-7b-instruct --no-attacks --no-dashboard --no-pause
```

This command is optional setup guidance; it was not run during this release-readiness check. The historical results do not provide a fully pinned scene/LLM build manifest, so do not promise bit-for-bit reproduction from this command. The `final` model roster is Qwen2.5-7B, Llama-3.1-8B and Gemma-2-9B; model availability, quantization and hardware memory must be checked independently.

Linux virtual-radio experiments are a separate optional workflow. `mac80211_hwsim` is a Linux kernel facility, not a native macOS capability. Privileged Docker/VM scripts are research tooling and must be reviewed before execution; no networking installer is part of the quick start.
