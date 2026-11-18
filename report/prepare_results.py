"""Audit archived evidence and regenerate report assets; no model/physics calls.

Run: python report/prepare_results.py (requires matplotlib).
"""
from collections import Counter, defaultdict
import json
from math import comb, isclose
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from grasp_c.rules import rank as rule_rank


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(ok, detail):
    if not ok:
        raise ValueError(detail)


def exact(w, l):
    n = w + l
    return min(1., 2 * sum(comb(n, i) for i in range(min(w, l) + 1)) / 2**n)


def main():
    base = ROOT / "results/oracle_20261103"
    r3 = base / "r3_v3p_wide_strict_v2"
    pbf = base / "r3_pbf_v3p_wide_strict_v2"
    run = ROOT / "results/c_evaluation_multimodel_20261109"
    summary = read(run / "summary.json")
    require(summary["seeds"] == list(range(10, 30)), "Unexpected seeds")
    for task, group in summary["tasks"].items():
        for method, row in group["summary"].items():
            outcomes = [r["methods"][method]["success"] for r in group["rows"]]
            require(all(type(v) is bool for v in outcomes), f"Missing: {task}/{method}")
            require(sum(outcomes) == row["successes"] and len(outcomes) == row["scored_n"] == 20,
                    f"Count mismatch: {task}/{method}")
    comparisons = summary["comparisons"]
    require(len(comparisons) == 170, "Changed multiplicity family")
    for c in comparisons:
        rows = summary["tasks"][c["task"]]["rows"]
        pairs = [(r["methods"][c["method"]]["success"],
                  r["methods"][c["reference"]]["success"]) for r in rows]
        w = sum(a and not b for a, b in pairs)
        l = sum(b and not a for a, b in pairs)
        require((w, l) == (c["wins"], c["losses"]) and isclose(exact(w, l), c["p_exact"]),
                f"Paired comparison mismatch: {c}")
    running = 0.
    for i, c in enumerate(sorted(comparisons, key=lambda c: c["p_exact"])):
        running = max(running, min(1., (len(comparisons) - i) * c["p_exact"]))
        require(isclose(running, c["p_holm"]), "Holm mismatch")

    endpoints = {"transport_place": (r3, "transport_place"),
                 "handover_transfer": (r3, "handover_transfer"),
                 "pour_water": (r3, "pour_water"),
                 "pour_water_pbf_table": (pbf, "pour_water"),
                 "pour_water_pbf": (run, "pour_water")}
    for endpoint, (source, task) in endpoints.items():
        for row in summary["tasks"][endpoint]["rows"]:
            for method, outcome in row["methods"].items():
                if endpoint == "pour_water_pbf":
                    path = source / "pbf" / f"seed{row['seed']}" / f"{outcome['selected_id']}.json"
                else:
                    path = source / "episodes" / task / f"seed{row['seed']}" / f"{outcome['selected_id']}.json"
                ep = read(path)
                # Local rerun records wrap their physical outcome in `episode`.
                ep = ep.get("episode", ep)
                require(ep["success"] == outcome["success"], f"Episode mismatch: {endpoint}/{method}")

    trials = [read(p) for p in sorted((run / "trials").glob("*/*/seed*.json"))]
    require(len(trials) == 720, "Expected 720 trials")
    audit = {k: sum(v.get(k, 0) for v in summary["selection_audit"].values())
             for k in ("initial_valid_n", "repairs", "fallbacks", "errors")}
    raw_audit = Counter()
    for trial in trials:
        md = trial["selection"]["metadata"]
        public = json.loads(trial["messages"][1]["content"])["candidates"]
        ids = {c["id"] for c in public}
        chosen = trial["selection"]
        require(len(chosen["ranking"]) == len(ids)
                and set(chosen["ranking"]) == ids
                and chosen["selected_id"] == chosen["ranking"][0],
                "Invalid final candidate permutation")
        require(all(set(c) == {"id", "region", "approach", "gripper_width", "geometry_score"}
                    for c in public), "Unexpected private field in model input")
        raw_audit["repairs"] += md.get("repair_attempts", 0)
        raw_audit["fallbacks"] += bool(md.get("fallback_to_geometry", False))
        raw_audit["initial_valid_n"] += sum(bool(c.get("validation", {}).get("valid", False))
            for c in md.get("calls", []) if c.get("phase") == "initial")
        raw_audit["errors"] += trial.get("error") is not None
    require(dict(raw_audit) == audit, f"Call audit mismatch: {dict(raw_audit)} vs {audit}")
    v1 = [t for t in trials if t["selection"]["metadata"]["prompt_version"] == "v1"]
    families = {r["id"]: r["family"] for r in read(r3 / "b_inputs/families.internal.json")}
    require(len(v1) == 240 and all(families[t["selection"]["selected_id"]] in ("top", "side")
                                 for t in v1), "v1 family claim changed")

    m6 = {}
    for task in ("transport_place", "handover_transfer", "pour_water"):
        outcomes = []
        for seed in range(10, 30):
            public = read(r3 / "filters" / task / f"seed{seed}.json")["feasible_candidates"]
            cid = rule_rank(task, public)[0]
            source = pbf if task == "pour_water" else r3
            outcomes.append(read(source / "episodes" / task / f"seed{seed}" / f"{cid}.json")["success"])
        m6[task] = sum(outcomes)
    require(list(m6.values()) == [18, 20, 16], "M6 differs from reference")

    generated = HERE / "generated"
    figures = HERE / "figures"
    generated.mkdir(exist_ok=True)
    figures.mkdir(exist_ok=True)
    methods = [("M0_RANDOM", "M0 Random"), ("M1_STATIC_V2", "M1 Static v2"),
               ("M2_TASK_RISK", "M2 Task risk"), ("M4_FAMILY_PRIOR", "M4 Family prior"),
               ("M5_PAPER_STYLE", "M5 Part surrogate")]
    for model, label in [("CODEX", "GPT"), ("GEMINI", "Gemini")]:
        for suffix, prompt in [("", "v1"), ("_V2_PHYSICS", "Mech."), ("_V2", "Pref.")]:
            for variant in ("M3", "A1"):
                methods.append((f"{variant}_{model}{suffix}", f"{label} {prompt} {variant}"))
    main_tasks = ("transport_place", "handover_transfer", "pour_water_pbf_table")

    def count(method, task):
        return summary["tasks"][task]["summary"][method]["successes"]

    end = r" \\"
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             "Method & Place & Handover & Pour (PBF) & Total" + end, r"\midrule"]
    for i, (method, label) in enumerate(methods):
        if i in (5, 11):
            lines.append(r"\midrule")
        counts = [count(method, t) for t in main_tasks]
        lines.append(label + " & " + " & ".join(map(str, counts + [sum(counts)])) + end)
    lines += [r"\midrule", "M6 Prompt rules & 18 & 20 & 16 & 54" + end]
    vals = [summary["tasks"][t]["uniform_expectation"] for t in main_tasks]
    lines += ["Uniform expected count & " + " & ".join(f"{20*x:.1f}" for x in vals)
              + f" & {20*sum(vals):.1f}" + end,
              "Oracle upper bound & 20 & 20 & 20 & 60" + end, r"\bottomrule", r"\end{tabular}"]
    (generated / "main_table.tex").write_text("\n".join(lines) + "\n")

    r4 = read(base / "r4_pbf_v3p_wide_strict_v3/report_seeds10-29.json")["tasks"]["pour_water"]["summary"]
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule",
             "Method & R3 proxy & R3 PBF & Local PBF & R4 PBF" + end, r"\midrule"]
    for method, label in methods[:5] + [("M3_CODEX_V2", "GPT Pref. M3"), ("M3_GEMINI_V2", "Gemini Pref. M3")]:
        values = [count(method, t) for t in ("pour_water", "pour_water_pbf_table", "pour_water_pbf")]
        last = str(r4[method]["successes"]) if method in r4 else "--"
        lines.append(label + " & " + " & ".join(map(str, values)) + " & " + last + end)
    lines += [r"\bottomrule", r"\end{tabular}"]
    (generated / "sensitivity_table.tex").write_text("\n".join(lines) + "\n")

    pooled = defaultdict(lambda: [0, 0])
    for task in ("transport_place", "handover_transfer", "pour_water"):
        source = pbf if task == "pour_water" else r3
        for path in (source / "episodes" / task).glob("seed*/*.json"):
            ep = read(path)
            row = pooled[task, families[ep["candidate_id"]]]
            row[0] += bool(ep["success"])
            row[1] += 1
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    order = ("handle", "rim", "top", "side")
    tasks = ("transport_place", "handover_transfer", "pour_water")
    data = np.array([[pooled[t, f][0] / pooled[t, f][1] if pooled[t, f][1] else np.nan
                      for t in tasks] for f in order])
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "pdf.fonttype": 42})
    fig, ax = plt.subplots(figsize=(7.0, 2.9), layout="constrained")
    ax.imshow(data, cmap="Blues", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(3), ["Placement", "Handover", "Pouring (PBF)"])
    ax.set_yticks(range(4), ["Handle", "Rim", "Top body", "Side body"])
    ax.tick_params(length=0, pad=8)
    for i, f in enumerate(order):
        for j, t in enumerate(tasks):
            k, n = pooled[t, f]
            label = f"{k}/{n}  ({k/n:.0%})" if n else "No feasible grasps"
            ax.text(j, i, label, ha="center", va="center", color="white" if n and k/n > .65 else "#172b40")
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.savefig(figures / "family_task.pdf")
    plt.close(fig)
    agree = total = 0
    for path in (pbf / "episodes/pour_water").glob("seed*/*.json"):
        a = read(path)
        b = read(r3 / "episodes/pour_water" / path.parent.name / path.name)
        agree += a["success"] == b["success"]
        total += 1
    require((agree, total) == (347, 458), "Proxy/PBF agreement changed")
    wrist = {}
    for name in ("r3_v3p_wide_strict_v2", "r4_v3p_wide_strict_v3"):
        wrist[name] = sum(read(p).get("failure_reason") == "pregrasp_arrival_timeout"
                         for p in (base / name / "episodes").glob("*/seed*/*.json"))
    require(list(wrist.values()) == [222, 2], "Wrist timeout claim changed")
    result = {"saved_trials": len(trials), "output_audit": audit, "paired_comparisons": len(comparisons),
              "m6_primary_successes": m6, "proxy_pbf_agreement": [agree, total],
              "local_pbf_unique_episodes": len(list((run / "pbf").glob("seed*/*.json"))),
              "pregrasp_timeouts": wrist,
              "pooled_counts": {f"{t}/{f}": v for (t, f), v in pooled.items()}}
    (generated / "evidence_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "pooled_counts"}, indent=2))


if __name__ == "__main__":
    main()
