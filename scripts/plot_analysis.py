#!/usr/bin/env python3
"""Figures for the analysis tables written by scripts/analyze.py. Requires matplotlib.

    python3 scripts/plot_analysis.py --label ascon-core-001 [--metric instance_area_um2 ...] [--top 30]

Writes PNG + PDF under results/<label>/analysis/plots/<design>/:
  noise.*                 seed-to-seed deviation of the baseline, per PDK (Q1)
  effects_<metric>.*      configuration effects vs baseline with CIs and the seed-noise band (Q2)
  variance.*              share of variance: configuration, PDK×configuration, seed (Q3)
  transfer_<metric>.*     normalised medians on PDK a vs PDK b, one point per configuration (Q5)
  dse_reliability.*       Agreement with observed best set vs seeds per configuration (Q6)
Plots show measured data only; nothing is interpolated or invented.
"""
import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
# Categorical slots 1-3 of the reference palette (validated all-pairs for 3 series); neutrals for context.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
INK, MUTED, GRID, BAND = "#0b0b0b", "#52514e", "#e4e3df", "#d9d8d3"
plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "legend.frameon": False})


def read(path):
    if not path.is_file():
        return []
    with path.open() as stream:
        return list(csv.DictReader(stream))


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def save(fig, out, name):
    out.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out / f"{name}.png", dpi=170)
    fig.savefig(out / f"{name}.pdf")
    plt.close(fig)


def color_map(pdks):
    return {p: SERIES[i % len(SERIES)] for i, p in enumerate(sorted(pdks))}


def noise_plot(design, runs, settings_metrics, baseline, out, colors):
    metrics = [m for m in settings_metrics if m["effect"] == "relative"]
    pdks = sorted({r["pdk"] for r in runs if r["design"] == design})
    fig, ax = plt.subplots(figsize=(max(6, 1.1 * len(metrics) * len(pdks) / 2), 4))
    width = 0.8 / max(1, len(pdks))
    for j, p in enumerate(pdks):
        for i, m in enumerate(metrics):
            rows = [r for r in runs if r["design"] == design and r["pdk"] == p and r["configuration"] == baseline
                    and (m["population"] != "passing" or r.get("study_checks_pass") == "True")]
            vals = [num(r.get(m["column"])) for r in rows]
            vals = [v for v in vals if v is not None]
            if len(vals) < 2:
                continue
            med = statistics.median(vals)
            dev = [100 * (v / med - 1) for v in vals] if med else []
            x = i + (j - (len(pdks) - 1) / 2) * width
            ax.scatter([x] * len(dev), dev, s=22, color=colors[p], edgecolor="white", linewidth=0.8, zorder=3,
                       label=p if i == 0 else None)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    ax.set_xticks(range(len(metrics)), [m["column"] for m in metrics], rotation=20, ha="right")
    ax.set_ylabel(f"Deviation from {baseline} median (%)")
    fig.suptitle(f"{design}: seed-to-seed spread of the baseline (only the initial-GRT seed changes)", fontsize=9)
    ax.legend(ncol=len(pdks), loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=8)
    save(fig, out, "noise")


def effects_plot(design, metric, effects, floors, out, colors, top):
    rows = [r for r in effects if r["design"] == design and r["metric"] == metric["column"] and num(r.get("effect")) is not None]
    if not rows:
        return
    pdks = sorted({r["pdk"] for r in rows})
    strength = defaultdict(float)
    for r in rows:
        strength[r["configuration"]] = max(strength[r["configuration"]], abs(num(r["effect"])))
    configs = sorted(strength, key=lambda c: strength[c])[-top:]
    fig, axes = plt.subplots(1, len(pdks), figsize=(3.6 * len(pdks), 0.24 * len(configs) + 1.6), sharey=True, squeeze=False)
    unit = "%" if metric["effect"] == "relative" else ""
    for ax, p in zip(axes[0], pdks):
        floor = next((f for f in floors if f["design"] == design and f["pdk"] == p and f["metric"] == metric["column"]), {})
        sd = num(floor.get("pooled_within_config_cv_pct" if metric["effect"] == "relative" else "pooled_within_config_sd"))
        if sd:
            ax.axvspan(-2 * sd, 2 * sd, color=BAND, alpha=0.6, linewidth=0, label="±2 seed SD")
        ax.axvline(0, color=MUTED, linewidth=0.8)
        for y, c in enumerate(configs):
            r = next((r for r in rows if r["pdk"] == p and r["configuration"] == c), None)
            if not r:
                continue
            e, lo, hi = num(r["effect"]), num(r.get("ci_low")), num(r.get("ci_high"))
            filled = r.get("verdict") in {"better", "worse"}
            if lo is not None:
                ax.plot([lo, hi], [y, y], color=colors[p], linewidth=2, solid_capstyle="round")
            ax.scatter([e], [y], s=40, color=colors[p] if filled else "white", edgecolor=colors[p], linewidth=1.5, zorder=3)
        ax.set_title(p, fontsize=9)
        ax.set_xlabel(f"Effect vs baseline ({unit or 'units'})")
        ax.set_yticks(range(len(configs)), configs, fontsize=7)
    if axes[0][0].get_legend_handles_labels()[0]:
        axes[0][0].legend(loc="lower left", fontsize=7)
    fig.suptitle(f"{design}: {metric['column']} ({metric['direction']} is better). Filled = beyond noise "
                 f"(CI excludes 0 and FDR-significant); hollow = within noise", fontsize=9)
    save(fig, out, f"effects_{metric['column']}")


def variance_plot(design, variance, out):
    rows = [r for r in variance if r["design"] == design and num(r.get("share_seed_pct")) is not None]
    if not rows:
        return
    labels = [f"{r['metric']}  [{r['pdk']}]" for r in rows]
    parts = [("share_configuration_pct", "configuration", SERIES[0]),
             ("share_pdk_x_configuration_pct", "PDK × configuration", SERIES[1]),
             ("share_pdk_residual_pct", "PDK residual (after normalising)", "#b9b8b2"),
             ("share_seed_pct", "seed (noise)", "#7d7c78")]
    fig, ax = plt.subplots(figsize=(8, 0.26 * len(rows) + 1.4))
    left = [0.0] * len(rows)
    for key, name, color in parts:
        widths = [num(r.get(key)) or 0 for r in rows]
        ax.barh(range(len(rows)), widths, left=left, color=color, edgecolor="white", linewidth=1.5, label=name, height=0.7)
        left = [a + b for a, b in zip(left, widths)]
    ax.set_yticks(range(len(rows)), labels, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of total variance (%)")
    ax.grid(axis="y", visible=False)
    ax.legend(ncol=4, loc="lower center", bbox_to_anchor=(0.4, 1.0), fontsize=7)
    fig.suptitle(f"{design}: where the variation comes from", fontsize=9)
    save(fig, out, "variance")


def transfer_plot(design, metric, ranking, out):
    rows = [r for r in ranking if r["design"] == design and r["metric"] == metric["column"] and r.get("feasible") == "True"]
    by_pdk = defaultdict(dict)
    for r in rows:
        v = num(r.get("normalised_median"))
        if v is not None:
            by_pdk[r["pdk"]][r["configuration"]] = v
    pdks = sorted(by_pdk)
    pairs = [(a, b) for i, a in enumerate(pdks) for b in pdks[i + 1:]]
    if not pairs:
        return
    fig, axes = plt.subplots(1, len(pairs), figsize=(3.6 * len(pairs), 3.6), squeeze=False)
    scale = 100 if metric["effect"] == "relative" else 1
    shift = 1 if metric["effect"] == "relative" else 0
    for ax, (a, b) in zip(axes[0], pairs):
        common = sorted(set(by_pdk[a]) & set(by_pdk[b]))
        xs = [scale * (by_pdk[a][c] - shift) for c in common]
        ys = [scale * (by_pdk[b][c] - shift) for c in common]
        if not xs:
            continue
        lo, hi = min(xs + ys), max(xs + ys)
        pad = 0.05 * (hi - lo or 1)
        ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], color=MUTED, linewidth=0.8, linestyle="--")
        ax.scatter(xs, ys, s=26, color=SERIES[0], edgecolor="white", linewidth=0.8, zorder=3)
        unit = "% vs baseline" if metric["effect"] == "relative" else "Δ vs baseline"
        ax.set_xlabel(f"{a} ({unit})")
        ax.set_ylabel(f"{b} ({unit})")
        ax.set_title(f"{a} vs {b}: {len(common)} configs", fontsize=9)
    fig.suptitle(f"{design}: {metric['column']}: points on the diagonal = effect transfers between PDKs", fontsize=9)
    save(fig, out, f"transfer_{metric['column']}")


def dse_plot(design, reliability, metrics, out, colors):
    rows = [r for r in reliability if r["design"] == design]
    names = [m["column"] for m in metrics if any(r["metric"] == m["column"] for r in rows)]
    if not names:
        return
    cols = min(3, len(names))
    nrows = (len(names) + cols - 1) // cols
    fig, axes = plt.subplots(nrows, cols, figsize=(3.4 * cols, 2.8 * nrows), squeeze=False, sharey=True)
    for ax in axes.flat[len(names):]:
        ax.set_visible(False)
    for ax, name in zip(axes.flat, names):
        for p in sorted({r["pdk"] for r in rows}):
            pts = sorted((int(r["seeds_per_config"]), num(r["p_pick_observed_best"]), num(r["chance_level"]))
                         for r in rows if r["metric"] == name and r["pdk"] == p)
            if not pts:
                continue
            ax.plot([k for k, _, _ in pts], [v for _, v, _ in pts], color=colors[p], linewidth=2, marker="o",
                    markersize=5, markeredgecolor="white", label=p)
            ax.plot([k for k, _, _ in pts], [c for _, _, c in pts], color=colors[p], linewidth=1, linestyle=":")
        ax.set_ylim(0, 1.03)
        ax.set_title(name, fontsize=8)
        ax.set_xlabel("seeds per configuration")
    axes[0][0].set_ylabel("Agreement with observed best set")
    axes[0][0].legend(fontsize=7, loc="lower right")
    fig.suptitle(f"{design}: within-sample selection stability (dotted = chance)", fontsize=9)
    save(fig, out, "dse_reliability")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True)
    parser.add_argument("--analysis-dir", help="Default: results/<label>/analysis")
    parser.add_argument("--metric", action="append", help="Limit per-metric plots to these columns.")
    parser.add_argument("--top", type=int, default=30, help="Configurations per effects plot (largest |effect|).")
    args = parser.parse_args()
    analysis = Path(args.analysis_dir) if args.analysis_dir else ROOT / "results" / args.label / "analysis"
    import json
    settings = json.loads((analysis / "analysis_settings.json").read_text())
    metrics = [m for m in settings["metrics"] if not args.metric or m["column"] in args.metric]
    runs = read(ROOT / "results" / args.label / "runs.csv")
    tables = {name: read(analysis / f"{name}.csv") for name in
              ["effects", "noise_floor", "variance", "ranking", "dse_reliability"]}
    for design in sorted({r["design"] for r in tables["ranking"]}):
        out = analysis / "plots" / design
        colors = color_map({r["pdk"] for r in tables["ranking"] if r["design"] == design})
        noise_plot(design, runs, metrics, settings["baseline_config"], out, colors)
        for m in metrics:
            effects_plot(design, m, tables["effects"], tables["noise_floor"], out, colors, args.top)
            transfer_plot(design, m, tables["ranking"], out)
        variance_plot(design, tables["variance"], out)
        dse_plot(design, tables["dse_reliability"], metrics, out, colors)
        print(out)


if __name__ == "__main__":
    main()
