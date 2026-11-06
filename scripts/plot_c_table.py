"""Optional static figure for C table results (requires matplotlib).

python -m scripts.plot_c_table results/c_evaluation_20261106/summary.json
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    data = json.loads(args.summary.read_text())
    methods = ["M0_RANDOM", "M1_STATIC_V2", "M2_TASK_RISK", "M4_FAMILY_PRIOR", "M5_PAPER_STYLE",
               "M3_CODEX", "A1_CODEX"]
    labels = ["M0 Random", "M1 Static", "M2 Task risk", "M4 Family prior", "M5 Part heuristic",
              "M3 + task", "A1 - task"]
    tasks = [("transport_place", "Transport / place"), ("handover_transfer", "Handover"),
             ("pour_water_pbf", "Pouring / default PBF")]
    colors = ["#94a3b8"]*5 + ["#2563eb", "#a855b5"]
    plt.rcParams.update({"font.size": 11, "font.family": "DejaVu Sans"})
    fig, axes = plt.subplots(1, 3, figsize=(14, 5.4), sharey=True)
    for ax, (task, title) in zip(axes, tasks):
        groups = data["tasks"][task]["summary"]
        for y, (method, color) in enumerate(zip(methods, colors)):
            s = groups[method]
            if s["scored_n"] != s["planned_n"]:
                raise ValueError(f"refusing an apparently complete plot with missing outcomes: {task}/{method}")
            rate = s["success_rate"]
            low, high = s["ci95"]
            ax.barh(y, rate, height=.63, color=color, zorder=2)
            ax.errorbar(rate, y, xerr=[[max(0., rate-low)], [max(0., high-rate)]], color="#334155", capsize=3,
                        linewidth=1.1, zorder=3)
            ax.text(1.045, y, f"{s['successes']}/{s['scored_n']}", va="center", fontsize=10)
        ax.set_title(title, fontsize=13, weight="bold", pad=14)
        ax.set_xlim(0, 1.18)
        ax.set_xticks([0, .25, .5, .75, 1])
        ax.xaxis.set_major_formatter(PercentFormatter(1))
        ax.grid(axis="x", color="#e2e8f0", zorder=0)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color("#cbd5e1")
        ax.tick_params(axis="y", length=0)
        ax.set_xlabel("Task success")
    axes[0].set_yticks(range(len(methods)), labels)
    axes[0].invert_yaxis()
    fig.suptitle("C evaluation: real model selections versus baselines", x=.05, ha="left",
                 fontsize=16, weight="bold")
    fig.text(.05, .025, "20 scene seeds per task • Wilson 95% intervals • retrospective simulation pilot\n"
             "M4 uses training outcomes; M5 uses a hand-written part table. Blue/purple: real GPT selections.",
             fontsize=10, color="#475569")
    fig.tight_layout(rect=[0, .09, 1, .91], w_pad=2)
    output = args.out or args.summary.parent/"success_rates.png"
    fig.savefig(output, dpi=200, facecolor="white")
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    main()
