"""
Report Generator for VESPER evaluation.

Produces:
  1.  LaTeX tables and figure includes for the paper
  2.  Matplotlib figures (PDF / PNG) for all experiments
  3.  A standalone summary Markdown report
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Optional heavy imports — guard so the rest of the module works even if
# matplotlib/pandas are not installed.
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


# =============================================================================
# Helpers
# =============================================================================

def _safe_fmt(v, fmt=".3f"):
    """Format a numeric value safely."""
    if v is None:
        return "—"
    try:
        return f"{float(v):{fmt}}"
    except (ValueError, TypeError):
        return str(v)


def _latex_escape(s: str) -> str:
    """Escape LaTeX special characters."""
    for ch in ("_", "%", "&", "#", "$"):
        s = s.replace(ch, f"\\{ch}")
    return s


# =============================================================================
# Report Generator
# =============================================================================

class ReportGenerator:
    """
    Takes a results dict (from ExperimentRunner.run()) and produces
    paper-ready tables, figures, and a Markdown summary.
    """

    FIGURE_DPI = 300
    FIGURE_FORMAT = "pdf"

    def __init__(
        self,
        results: Dict[str, Any],
        output_dir: str = "results/report",
    ):
        self.results = results
        self.output_dir = Path(output_dir)
        self.figures_dir = self.output_dir / "figures"
        self.tables_dir = self.output_dir / "tables"
        for d in (self.output_dir, self.figures_dir, self.tables_dir):
            d.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------------- #
    #  Public API                                                             #
    # --------------------------------------------------------------------- #

    def generate_all(self):
        """Generate all report artefacts."""
        self._generate_markdown_summary()

        if self.results.get("activity_comparison"):
            self._generate_activity_table()
            if HAS_MATPLOTLIB:
                self._plot_activity_distributions()

        if self.results.get("latency"):
            self._generate_latency_table()
            if HAS_MATPLOTLIB:
                self._plot_latency_cdf()

        if self.results.get("scalability"):
            self._generate_scalability_table()
            if HAS_MATPLOTLIB:
                self._plot_scalability()

        if self.results.get("llm_ablation"):
            self._generate_ablation_table()
            if HAS_MATPLOTLIB:
                self._plot_ablation_radar()
                self._plot_ablation_bars()

        logger.info(f"Report artefacts written to {self.output_dir}")

    # --------------------------------------------------------------------- #
    #  Markdown Summary                                                       #
    # --------------------------------------------------------------------- #

    def _generate_markdown_summary(self):
        md = []
        md.append(f"# VESPER Evaluation Report")
        md.append(f"")
        md.append(f"- **Experiment:** {self.results.get('experiment_name', 'n/a')}")
        md.append(f"- **ID:** {self.results.get('experiment_id', 'n/a')}")
        md.append(f"- **Started:** {self.results.get('started_at', 'n/a')}")
        md.append(f"- **Duration:** {_safe_fmt(self.results.get('total_duration_s'), '.1f')}s")
        md.append("")

        cfg = self.results.get("config", {})
        md.append("## Configuration")
        md.append(f"- Seed: {cfg.get('seed', 42)}")
        md.append(f"- Confidence: {cfg.get('confidence_level', 0.95)}")
        md.append("")

        # Activity comparison
        ac = self.results.get("activity_comparison", {})
        if ac:
            md.append("## Activity Realism (RQ1)")
            md.append(f"- VESPER schedules: {ac.get('vesper_schedule_count', 'n/a')}")
            for ds_name, metrics in ac.items():
                if ds_name == "vesper_schedule_count":
                    continue
                if isinstance(metrics, dict):
                    md.append(f"### vs {ds_name}")
                    md.append(f"| Metric | Value |")
                    md.append(f"|--------|-------|")
                    md.append(f"| KL Divergence | {_safe_fmt(metrics.get('kl_divergence'))} |")
                    md.append(f"| JS Divergence | {_safe_fmt(metrics.get('js_divergence'))} |")
                    md.append(f"| Wasserstein | {_safe_fmt(metrics.get('wasserstein'))} |")
                    md.append(f"| Temporal Corr. | {_safe_fmt(metrics.get('temporal_correlation'))} |")
                    md.append(f"| Schedule Entropy | {_safe_fmt(metrics.get('schedule_entropy'))} |")
                    md.append("")

        # Latency
        lat = self.results.get("latency", {})
        if lat:
            md.append("## Latency (RQ4)")
            md.append("| Path | P50 (ms) | P95 (ms) | P99 (ms) | Mean (ms) |")
            md.append("|------|----------|----------|----------|-----------|")
            for path_name, m in lat.items():
                if isinstance(m, dict):
                    md.append(
                        f"| {path_name} | "
                        f"{_safe_fmt(m.get('p50_ms'), '.2f')} | "
                        f"{_safe_fmt(m.get('p95_ms'), '.2f')} | "
                        f"{_safe_fmt(m.get('p99_ms'), '.2f')} | "
                        f"{_safe_fmt(m.get('mean_ms'), '.2f')} |"
                    )
            md.append("")

        # Scalability
        sc = self.results.get("scalability", {})
        if sc:
            md.append("## Scalability (RQ3)")
            ds = sc.get("device_scaling", {})
            if isinstance(ds, dict):
                for count, data in sorted(ds.items(), key=lambda x: str(x[0])):
                    md.append(f"- **{count} devices**: "
                              f"throughput={_safe_fmt(data.get('throughput_events_per_s'), '.1f')} ev/s, "
                              f"CPU={_safe_fmt(data.get('cpu_percent'), '.1f')}%, "
                              f"Mem={_safe_fmt(data.get('memory_mb'), '.1f')} MB")
            md.append("")

        # LLM ablation
        llm = self.results.get("llm_ablation", {})
        if llm:
            md.append("## LLM Ablation (RQ2)")
            summary = llm.get("model_summary", {})
            if summary:
                md.append("| Model | Entropy (μ) | Error % | Latency (ms) |")
                md.append("|-------|-------------|---------|--------------|")
                for model, stats in summary.items():
                    md.append(
                        f"| {model} | "
                        f"{_safe_fmt(stats.get('entropy_mean'))} | "
                        f"{_safe_fmt(stats.get('error_rate_mean'), '.1f')} | "
                        f"{_safe_fmt(stats.get('latency_mean'), '.1f')} |"
                    )
            md.append("")

        path = self.output_dir / "REPORT.md"
        path.write_text("\n".join(md))
        logger.info(f"Markdown report → {path}")

    # --------------------------------------------------------------------- #
    #  LaTeX Tables                                                           #
    # --------------------------------------------------------------------- #

    def _generate_activity_table(self):
        """Table 1 — Activity realism metrics."""
        ac = self.results["activity_comparison"]
        rows = []
        for ds_name, m in ac.items():
            if not isinstance(m, dict):
                continue
            rows.append(
                f"  {_latex_escape(ds_name)} "
                f"& {_safe_fmt(m.get('kl_divergence'))} "
                f"& {_safe_fmt(m.get('js_divergence'))} "
                f"& {_safe_fmt(m.get('wasserstein'))} "
                f"& {_safe_fmt(m.get('temporal_correlation'))} "
                f"& {_safe_fmt(m.get('schedule_entropy'))} \\\\"
            )

        table = (
            "\\begin{table}[t]\n"
            "\\centering\n"
            "\\caption{Activity Distribution Realism}\n"
            "\\label{tab:activity-realism}\n"
            "\\begin{tabular}{lccccc}\n"
            "\\toprule\n"
            "Dataset & KL$\\downarrow$ & JS$\\downarrow$ & Wass.$\\downarrow$ & Temp. Corr.$\\uparrow$ & Entropy \\\\\n"
            "\\midrule\n"
            + "\n".join(rows) + "\n"
            "\\bottomrule\n"
            "\\end{tabular}\n"
            "\\end{table}\n"
        )
        path = self.tables_dir / "tab_activity_realism.tex"
        path.write_text(table)
        logger.info(f"LaTeX table → {path}")

    def _generate_latency_table(self):
        """Table 2 — End-to-end latency."""
        lat = self.results["latency"]
        rows = []
        for path_name, m in lat.items():
            if not isinstance(m, dict):
                continue
            rows.append(
                f"  {_latex_escape(path_name)} "
                f"& {_safe_fmt(m.get('mean_ms'), '.2f')} "
                f"& {_safe_fmt(m.get('std_ms'), '.2f')} "
                f"& {_safe_fmt(m.get('p50_ms'), '.2f')} "
                f"& {_safe_fmt(m.get('p95_ms'), '.2f')} "
                f"& {_safe_fmt(m.get('p99_ms'), '.2f')} "
                f"& {m.get('count', 0)} \\\\"
            )

        table = (
            "\\begin{table}[t]\n"
            "\\centering\n"
            "\\caption{End-to-End Latency (ms)}\n"
            "\\label{tab:latency}\n"
            "\\begin{tabular}{lrrrrrr}\n"
            "\\toprule\n"
            "Path & Mean & Std & P50 & P95 & P99 & $n$ \\\\\n"
            "\\midrule\n"
            + "\n".join(rows) + "\n"
            "\\bottomrule\n"
            "\\end{tabular}\n"
            "\\end{table}\n"
        )
        path = self.tables_dir / "tab_latency.tex"
        path.write_text(table)
        logger.info(f"LaTeX table → {path}")

    def _generate_scalability_table(self):
        """Table 3 — Device scaling results."""
        sc = self.results["scalability"]
        ds = sc.get("device_scaling", {})
        rows = []
        for count in sorted(ds.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
            data = ds[count]
            if not isinstance(data, dict):
                continue
            rows.append(
                f"  {count} "
                f"& {_safe_fmt(data.get('throughput_events_per_s'), '.1f')} "
                f"& {_safe_fmt(data.get('latency_mean_ms'), '.2f')} "
                f"& {_safe_fmt(data.get('cpu_percent'), '.1f')} "
                f"& {_safe_fmt(data.get('memory_mb'), '.1f')} \\\\"
            )

        table = (
            "\\begin{table}[t]\n"
            "\\centering\n"
            "\\caption{Device Scaling Performance}\n"
            "\\label{tab:scalability}\n"
            "\\begin{tabular}{lrrrr}\n"
            "\\toprule\n"
            "Devices & Throughput (ev/s) & Latency (ms) & CPU (\\%) & Memory (MB) \\\\\n"
            "\\midrule\n"
            + "\n".join(rows) + "\n"
            "\\bottomrule\n"
            "\\end{tabular}\n"
            "\\end{table}\n"
        )
        path = self.tables_dir / "tab_scalability.tex"
        path.write_text(table)
        logger.info(f"LaTeX table → {path}")

    def _generate_ablation_table(self):
        """Table 4 — LLM ablation results."""
        llm = self.results["llm_ablation"]
        summary = llm.get("model_summary", {})
        rows = []
        for model, stats in summary.items():
            rows.append(
                f"  {_latex_escape(model)} "
                f"& {_safe_fmt(stats.get('entropy_mean'))} "
                f"& {_safe_fmt(stats.get('entropy_std'))} "
                f"& {_safe_fmt(stats.get('error_rate_mean'), '.1f')} "
                f"& {_safe_fmt(stats.get('latency_mean'), '.0f')} "
                f"& {stats.get('n_schedules', 0)} \\\\"
            )

        table = (
            "\\begin{table}[t]\n"
            "\\centering\n"
            "\\caption{LLM Schedule Generation — Ablation Study}\n"
            "\\label{tab:llm-ablation}\n"
            "\\begin{tabular}{lcccrc}\n"
            "\\toprule\n"
            "Model & Entropy ($\\mu$) & Entropy ($\\sigma$) & Error (\\%) & Latency (ms) & $n$ \\\\\n"
            "\\midrule\n"
            + "\n".join(rows) + "\n"
            "\\bottomrule\n"
            "\\end{tabular}\n"
            "\\end{table}\n"
        )
        path = self.tables_dir / "tab_llm_ablation.tex"
        path.write_text(table)
        logger.info(f"LaTeX table → {path}")

    # --------------------------------------------------------------------- #
    #  Matplotlib Figures                                                      #
    # --------------------------------------------------------------------- #

    def _savefig(self, fig, name: str):
        path = self.figures_dir / f"{name}.{self.FIGURE_FORMAT}"
        fig.savefig(path, bbox_inches="tight", dpi=self.FIGURE_DPI)
        plt.close(fig)
        logger.info(f"Figure → {path}")

    def _plot_activity_distributions(self):
        """Fig 1 — Side-by-side bar charts of activity distributions."""
        ac = self.results["activity_comparison"]
        for ds_name, m in ac.items():
            if not isinstance(m, dict):
                continue
            vesper_dist = m.get("vesper_distribution", {})
            ref_dist = m.get("reference_distribution", {})
            if not vesper_dist or not ref_dist:
                continue

            all_acts = sorted(set(list(vesper_dist.keys()) + list(ref_dist.keys())))
            x = np.arange(len(all_acts))
            w = 0.35
            v_vals = [vesper_dist.get(a, 0) for a in all_acts]
            r_vals = [ref_dist.get(a, 0) for a in all_acts]

            fig, ax = plt.subplots(figsize=(10, 5))
            ax.bar(x - w / 2, v_vals, w, label="VESPER", color="#4C72B0")
            ax.bar(x + w / 2, r_vals, w, label=ds_name, color="#DD8452")
            ax.set_ylabel("Proportion")
            ax.set_xticks(x)
            ax.set_xticklabels(all_acts, rotation=45, ha="right", fontsize=8)
            ax.legend()
            ax.set_title(f"Activity Distribution: VESPER vs {ds_name}")
            self._savefig(fig, f"fig_activity_dist_{ds_name.lower()}")

    def _plot_latency_cdf(self):
        """Fig 2 — CDF of latency for each path."""
        lat = self.results["latency"]
        fig, ax = plt.subplots(figsize=(8, 5))
        for path_name, m in lat.items():
            if not isinstance(m, dict):
                continue
            # Reconstruct approximate CDF from percentiles
            pts = []
            for k in ("p50_ms", "p95_ms", "p99_ms"):
                v = m.get(k)
                if v is not None:
                    pts.append(v)
            if len(pts) < 2:
                continue
            ps = [0.5, 0.95, 0.99]
            ax.plot(pts, ps, marker="o", label=path_name)

        ax.set_xlabel("Latency (ms)")
        ax.set_ylabel("Percentile")
        ax.set_title("Latency CDF")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)
        self._savefig(fig, "fig_latency_cdf")

    def _plot_scalability(self):
        """Fig 3 — Throughput & CPU vs device count."""
        sc = self.results["scalability"]
        ds = sc.get("device_scaling", {})
        if not ds:
            return

        counts, throughputs, cpus, mems = [], [], [], []
        for count in sorted(ds.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
            data = ds[count]
            if not isinstance(data, dict):
                continue
            counts.append(int(count))
            throughputs.append(data.get("throughput_events_per_s", 0))
            cpus.append(data.get("cpu_percent", 0))
            mems.append(data.get("memory_mb", 0))

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

        # Throughput
        ax1.plot(counts, throughputs, "o-", color="#4C72B0")
        ax1.set_xlabel("Number of Devices")
        ax1.set_ylabel("Throughput (events/s)")
        ax1.set_title("Event Throughput vs Scale")
        ax1.grid(True, alpha=0.3)

        # Resource usage
        ax2_twin = ax2.twinx()
        l1 = ax2.plot(counts, cpus, "o-", color="#DD8452", label="CPU %")
        l2 = ax2_twin.plot(counts, mems, "s--", color="#55A868", label="Memory (MB)")
        ax2.set_xlabel("Number of Devices")
        ax2.set_ylabel("CPU %")
        ax2_twin.set_ylabel("Memory (MB)")
        ax2.set_title("Resource Usage vs Scale")
        lns = l1 + l2
        ax2.legend(lns, [l.get_label() for l in lns])
        ax2.grid(True, alpha=0.3)

        fig.tight_layout()
        self._savefig(fig, "fig_scalability")

    def _plot_ablation_radar(self):
        """Fig 4 — Radar chart comparing LLM models."""
        llm = self.results["llm_ablation"]
        summary = llm.get("model_summary", {})
        if not summary:
            return

        models = list(summary.keys())
        axes_labels = ["Entropy", "Error Rate (inv)", "Speed (inv latency)", "Consistency"]
        N = len(axes_labels)

        # Normalise each axis to [0, 1]
        data = {}
        for model, s in summary.items():
            ent = s.get("entropy_mean", 0)
            err = 1.0 - min(s.get("error_rate_mean", 0) / 100.0, 1.0)
            lat = 1.0 / max(s.get("latency_mean", 1), 1)  # inverse latency
            cons = max(1.0 - s.get("entropy_std", 0), 0)
            data[model] = [ent, err, lat, cons]

        # Normalise across models
        for i in range(N):
            vals = [data[m][i] for m in models]
            lo, hi = min(vals), max(vals)
            rng = hi - lo if hi > lo else 1.0
            for m in models:
                data[m][i] = (data[m][i] - lo) / rng

        angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
        angles += angles[:1]

        fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(polar=True))
        colors = plt.cm.Set2(np.linspace(0, 1, len(models)))
        for idx, model in enumerate(models):
            vals = data[model] + data[model][:1]
            ax.plot(angles, vals, "o-", label=model, color=colors[idx])
            ax.fill(angles, vals, alpha=0.1, color=colors[idx])

        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(axes_labels, fontsize=9)
        ax.set_title("LLM Model Comparison", y=1.08)
        ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=8)
        self._savefig(fig, "fig_llm_radar")

    def _plot_ablation_bars(self):
        """Fig 5 — Grouped bar chart: entropy per model × persona type."""
        llm = self.results["llm_ablation"]
        details = llm.get("details", [])
        if not details:
            return

        # Gather entropy by model
        model_entropies: Dict[str, List[float]] = {}
        for d in details:
            m = d.get("model", "unknown")
            e = d.get("entropy")
            if e is not None:
                model_entropies.setdefault(m, []).append(e)

        models = sorted(model_entropies.keys())
        means = [np.mean(model_entropies[m]) for m in models]
        stds = [np.std(model_entropies[m]) for m in models]

        fig, ax = plt.subplots(figsize=(10, 5))
        x = np.arange(len(models))
        bars = ax.bar(x, means, yerr=stds, capsize=4, color="#4C72B0", alpha=0.85)
        ax.set_xticks(x)
        ax.set_xticklabels(models, rotation=30, ha="right", fontsize=9)
        ax.set_ylabel("Schedule Entropy (bits)")
        ax.set_title("Schedule Diversity by LLM Model")
        ax.grid(axis="y", alpha=0.3)
        self._savefig(fig, "fig_llm_entropy_bars")


# =============================================================================
#  Stand-alone CLI
# =============================================================================

def main():
    """Generate report from a results JSON file."""
    import argparse, json
    parser = argparse.ArgumentParser(description="VESPER Report Generator")
    parser.add_argument("results", help="Path to results.json")
    parser.add_argument("-o", "--output", default="results/report")
    args = parser.parse_args()

    with open(args.results) as f:
        results = json.load(f)

    rg = ReportGenerator(results, output_dir=args.output)
    rg.generate_all()
    print(f"Report generated at {args.output}")


if __name__ == "__main__":
    main()
