#!/usr/bin/env python3
"""Plot observed data only. Requires matplotlib. No example results are invented."""
import argparse
import csv
import random
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from study import campaign, number

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--label", required=True)
parser.add_argument("--metric", action="append",
                    help="runs.csv column to plot (repeatable). Default: setup_ws_ns and instance_area_um2.")
parser.add_argument("--config", action="append", help="Only these configurations (repeatable). Default: all.")
args = parser.parse_args()
metrics = [(m, m) for m in args.metric] if args.metric else [
    ("setup_ws_ns", "Worst setup slack (ns)"), ("instance_area_um2", "Reported instance area (µm²)")]
root = campaign(args.label)
with (root / "runs.csv").open() as f:
    rows = [r for r in csv.DictReader(f) if r["repeat"] == "1" and r["status"] not in {"not_run", "running"}
            and (not args.config or r["configuration"] in args.config)]
designs = sorted(set(r["design"] for r in rows))
if not designs:
    raise SystemExit("No attempted runs to plot.")
all_rows = rows
for design in designs:
    rows = [row for row in all_rows if row["design"] == design]
    pdks = sorted(set(row["pdk"] for row in rows))
    out = root / "plots" / design
    out.mkdir(parents=True, exist_ok=True)
    for metric, ylabel in metrics:
        n_configs = len(set(row["configuration"] for row in rows))
        fig, axes = plt.subplots(1, len(pdks), figsize=(max(4.2, 0.35 * n_configs)*len(pdks), 4), squeeze=False)
        rng = random.Random(0)
        for ax, pdk in zip(axes[0], pdks):
            configs = sorted(set(r["configuration"] for r in rows if r["pdk"] == pdk))
            for i, cfg in enumerate(configs):
                subset = [r for r in rows if r["pdk"] == pdk and r["configuration"] == cfg]
                values = [number(r[metric]) for r in subset]
                values = [v for v in values if v is not None]
                if values:
                    ax.boxplot([values], positions=[i], widths=.45, showfliers=False)
                    ax.scatter([i+rng.uniform(-.11,.11) for _ in values], values, s=18, alpha=.7, color="#147b8b")
                ax.text(i, .02, f"{len(values)}/{len(subset)} measured", transform=ax.get_xaxis_transform(), ha="center", fontsize=8)
            if metric == "setup_ws_ns":
                ax.axhline(0, color="#a43838", linestyle="--", linewidth=1)
            ax.set_xticks(range(len(configs)), configs, rotation=90 if len(configs) > 6 else 0, fontsize=7)
            ax.set_title(pdk)
            ax.set_ylabel(ylabel)
            ax.grid(axis="y", alpha=.2)
        fig.suptitle(f"{design}: available post-route observations (see runs.csv for failures)", fontsize=10)
        fig.tight_layout()
        fig.savefig(out / f"{metric}.pdf")
        fig.savefig(out / f"{metric}.png", dpi=160)
        plt.close(fig)
    summary_path = root / "summary.csv"
    if summary_path.exists():
        with summary_path.open() as f:
            summary = [row for row in csv.DictReader(f) if row["design"] == design]
        if summary:
            fig, ax = plt.subplots(figsize=(max(6, len(summary)*.8), 5))
            for i, row in enumerate(summary):
                p = float(row["study_pass_fraction"])
                low, high = float(row["study_pass_wilson95_low"]), float(row["study_pass_wilson95_high"])
                ax.errorbar(i, p, yerr=[[p-low], [high-p]], fmt="o", capsize=4, color="#147b8b")
            labels = []
            for row in summary:
                host = row.get("machine_id") or "unrecorded host"
                threads = row.get("threads") or "?"
                labels.append(f"{row['pdk']}\n{row['configuration']}\n{host}, threads={threads}\nn={row['attempts']}")
            ax.set_xticks(range(len(summary)), labels, rotation=25)
            ax.set_ylim(-.03, 1.03)
            ax.set_ylabel("Fraction passing study checks\n(95% Wilson interval)")
            ax.grid(axis="y", alpha=.2)
            fig.tight_layout()
            fig.savefig(out / "success_rates.pdf")
            fig.savefig(out / "success_rates.png", dpi=160)
            plt.close(fig)
print(root / "plots")
