"""Oracle-table experiment over A's physical sequences: python -m grasp_c.oracle.

Pipeline: B generates one shared candidate file -> A filters it per
(task, seed) -> A executes feasible candidates ONCE each and the episode is
cached -> every selector (M0/M1/M2/LLM) is scored by table lookup -> report.

Because outcomes are cached per (task, seed, candidate), adding a selector or
re-running the report costs nothing; only new candidates/seeds need physics.
``--execute all`` runs every feasible candidate (enables the Oracle upper bound
and regret); ``--execute selected`` runs only what some method picked.

Selection never sees outcomes: rankings are computed from public metadata (C's
LLM) or B's explicit geometry/task models before any table lookup.
M1/M2 rank the grasps their contact model supports (body); rim/handle grasps
they cannot score are appended after them in ID order, and this is reported.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import argparse
import itertools
import json
import os
from pathlib import Path
import time

SEQUENCE_TASKS = ("transport_place", "handover_transfer", "pour_water")
ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _candidates(out: Path):
    from grasp_a.contracts import load_candidates
    return load_candidates(out / "b_inputs" / "candidates.scored.json")


# ---------------------------------------------------------------- workers
def _filter_job(out, task, seed, scene):
    from grasp_a.sequence import get_sequence_candidates
    started = time.perf_counter()
    filtered = get_sequence_candidates(task, seed, _candidates(Path(out)), scene_config=scene)
    filtered["filter_seconds"] = time.perf_counter() - started
    filtered.pop("ik_diagnostics", None)
    for check in filtered["checks"]:
        check.pop("collision_details", None)
    return task, seed, filtered


LIQUID_MODES = ("proxy", "proxy_margin", "fast", "pbf")


def _liquid_kwargs(task, liquid):
    """pour_water only. proxy: no particles (rigid payload + spout geometry);
    fast: particle fluid at 64 particles / 4 iterations / 1 substep; pbf: default."""
    if task != "pour_water" or liquid == "pbf":
        return {}
    if liquid in ("proxy", "proxy_margin"):
        return {"liquid": liquid}
    from grasp_a.sequence_contracts import SequenceConfig
    return {"config": SequenceConfig(fluid_particles=64, fluid_iterations=4, fluid_substeps=1)}


def _episode_job(out, task, seed, cid, scene, liquid="proxy"):
    from grasp_a.sequence import run_sequence
    started = time.perf_counter()
    try:
        episode = run_sequence(task, seed, cid, _candidates(Path(out)), scene_config=scene,
                               **_liquid_kwargs(task, liquid))
        row = dict(success=bool(episode["success"]), status=episode["status"],
                   failure_reason=episode.get("failure_reason"),
                   stages=[(s["id"], s["control_status"]) for s in episode.get("stages", [])],
                   success_checks=episode.get("success_checks"),
                   final_evidence={k: v for k, v in (episode.get("final_evidence") or {}).items()
                                   if k in ("water", "source_position", "source_upright_deg")},
                   pour_proxy=episode.get("pour_proxy"), liquid=liquid if task == "pour_water" else None)
    except Exception as exc:  # recorded, never silently retried
        row = dict(success=False, status="execution_error", failure_reason=f"{type(exc).__name__}: {exc}")
    row.update(task_id=task, seed=seed, candidate_id=cid, wall_seconds=time.perf_counter() - started)
    return task, seed, cid, row


# --------------------------------------------------------------- selectors
def _split_supported(public, families):
    supported = [r for r in public if families[r["id"]]["m1_m2_supported"]]
    rest = sorted((r for r in public if not families[r["id"]]["m1_m2_supported"]), key=lambda r: r["id"])
    return supported, rest


# Stand-in for the paper's LLM part answer (no LLM key here): the part a
# language model would typically name per task. Configurable; NOT tuned.
PAPER_PART_TABLE = {"transport_place": "handle", "handover_transfer": "body", "pour_water": "handle"}


def family_prior(prior_dir: Path, seeds, tasks):
    """Success rate per (task, family) on TRAINING seeds only (outcome table)."""
    fam = {r["id"]: r["family"] for r in _read(prior_dir / "b_inputs" / "families.internal.json")}
    table = {}
    for task in tasks:
        counts = {}
        for seed in seeds:
            path = prior_dir / "filters" / task / f"seed{seed}.json"
            if not path.exists():
                continue
            for r in _read(path)["feasible_candidates"]:
                ep = prior_dir / "episodes" / task / f"seed{seed}" / f"{r['id']}.json"
                if ep.exists():
                    c = counts.setdefault(fam[r["id"]], [0, 0])
                    c[0] += bool(_read(ep)["success"])
                    c[1] += 1
        # Laplace-smoothed so unseen families get 0.5.
        table[task] = {f: (a+1)/(n+2) for f, (a, n) in counts.items()}
    return table


def build_selectors(llm_methods, cache_dir, *, prior=None):
    """Return {method_id: fn(task, public, seed, candidates, model, families) -> ranking}."""
    from grasp_b.prototype import select_random
    from grasp_b.risk import rank_feasible
    from grasp_b.motion import command_context

    def m0(task, public, seed, candidates, model, families):
        return select_random(public, task_id=task, seed=seed)["ranking"]

    def b_rank(context_for):
        def rank(task, public, seed, candidates, model, families):
            supported, rest = _split_supported(public, families)
            head = rank_feasible(candidates, supported, model, context=context_for(task))["ranking"] if supported else []
            return head + [r["id"] for r in rest]
        return rank

    from grasp_b.risk import static_score
    import numpy as np

    def force_quality(cid, by_id, model, families):
        return static_score(by_id[cid], model)["score"] if families[cid]["m1_m2_supported"] else None

    def m5_paper(task, public, seed, candidates, model, families):
        """C(g,T) = K_force*F + K_dist*(-d_task/5 cm) on the named part first."""
        by_id = {c.id: c for c in candidates}
        part = PAPER_PART_TABLE[task]
        centre = (np.mean(model.handle_bounds, axis=0) if part == "handle" else np.zeros(3))
        scores = {r["id"]: force_quality(r["id"], by_id, model, families) for r in public}
        known = [v for v in scores.values() if v is not None]
        neutral = float(np.mean(known)) if known else 0.5
        def cost(r):
            F = scores[r["id"]] if scores[r["id"]] is not None else neutral
            d = float(np.linalg.norm(np.asarray(by_id[r["id"]].grasp_pose.position)-centre))
            return 1.0*F - 1.0*d/0.05
        return [r["id"] for r in sorted(public, key=lambda r: (r["region"] != part, -cost(r), r["id"]))]

    selectors = {"M0_RANDOM": m0,
                 "M1_STATIC_V2": b_rank(lambda task: None),
                 "M2_TASK_RISK": b_rank(lambda task: command_context(task)),
                 "M5_PAPER_STYLE": m5_paper}
    if prior is not None:
        def m4(task, public, seed, candidates, model, families):
            by_id = {c.id: c for c in candidates}
            rate = prior.get(task, {})
            def key(r):
                q = force_quality(r["id"], by_id, model, families)
                return (-rate.get(families[r["id"]]["family"], 0.5), -(q if q is not None else 0.), r["id"])
            return [r["id"] for r in sorted(public, key=key)]
        selectors["M4_FAMILY_PRIOR"] = m4
    if llm_methods:
        from .providers import build_provider
        from .selectors import LLMSelector
        for method in llm_methods:
            selector = LLMSelector(build_provider(method["provider"]), method["model"],
                                   variant=method.get("variant", "M3"),
                                   parameters=method.get("parameters", {}), cache_dir=cache_dir,
                                   cache_mode=method.get("cache_mode", "read_write"))

            def llm(task, public, seed, candidates, model, families, _s=selector):
                result = _s(task, public, seed)
                return result.get("ranking") or [result["selected_id"]]
            selectors[method["id"]] = llm
    return selectors


# ------------------------------------------------------------------ report
def wilson(k, n, z=1.96):
    if not n:
        return None
    phat = k/n
    denom = 1+z*z/n
    centre = (phat+z*z/(2*n))/denom
    half = z*((phat*(1-phat)+z*z/(4*n))/n)**0.5/denom
    return [max(0., centre-half), min(1., centre+half)]


def sign_test(wins, losses):
    """Exact two-sided McNemar/sign test p-value on discordant paired seeds."""
    from math import comb
    n = wins+losses
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(0, min(wins, losses)+1))/2**n
    return min(1.0, 2*tail)


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 1.0


def build_report(out: Path, tasks, seeds, methods, families):
    filters = {(t, s): _read(out / "filters" / t / f"seed{s}.json") for t in tasks for s in seeds}
    selections = _read(out / "selections.json")
    episodes = {}
    for path in (out / "episodes").glob("*/seed*/*.json"):
        row = _read(path)
        episodes[(row["task_id"], row["seed"], row["candidate_id"])] = row
    kind = {cid: rec["family"] for cid, rec in families.items()}
    report = dict(tasks={}, methods=list(methods) + ["ORACLE"])
    for task in tasks:
        sets = [set(r["id"] for r in filters[(task, s)]["feasible_candidates"]) for s in seeds]
        pairs = list(itertools.combinations(range(len(sets)), 2))
        per_seed = []
        for seed, feasible in zip(seeds, sets):
            outcomes = {cid: episodes.get((task, seed, cid)) for cid in feasible}
            known = {cid: o["success"] for cid, o in outcomes.items() if o is not None}
            row = dict(seed=seed, feasible=len(feasible),
                       feasible_by_family=dict(sorted(Counter(kind[c] for c in feasible).items())),
                       executed=len(known), succeeded=sum(known.values()),
                       complete=len(known) == len(feasible),
                       estimate=filters[(task, seed)]["initial_object_pose_estimate"], methods={})
            row["oracle"] = (any(known.values()) if row["complete"] and feasible else
                             (False if not feasible else (True if any(known.values()) else None)))
            for method in methods:
                sel = selections.get(task, {}).get(str(seed), {}).get(method)
                if not feasible:
                    row["methods"][method] = dict(selected_id=None, success=False, reason="no_feasible_candidate")
                    continue
                if sel is None or sel.get("error"):
                    row["methods"][method] = dict(selected_id=None, success=None,
                                                  reason=(sel or {}).get("error", "missing"))
                    continue
                cid = sel["ranking"][0]
                outcome = episodes.get((task, seed, cid))
                row["methods"][method] = dict(
                    selected_id=cid, family=kind[cid], success=None if outcome is None else outcome["success"],
                    failure_reason=None if outcome is None else outcome.get("failure_reason"))
            per_seed.append(row)
        summary = {}
        for method in list(methods) + ["ORACLE"]:
            values = [r["oracle"] if method == "ORACLE" else r["methods"][method]["success"] for r in per_seed]
            done = [v for v in values if v is not None]
            chosen = [r["methods"][method]["selected_id"] for r in per_seed
                      if method != "ORACLE" and r["methods"][method]["selected_id"]]
            summary[method] = dict(
                seeds=len(values), scored=len(done), successes=sum(done),
                success_rate=sum(done) / len(done) if done else None,
                distinct_selected_ids=len(set(chosen)) if method != "ORACLE" else None,
                selected_families=dict(Counter(kind[c] for c in chosen)) if method != "ORACLE" else None)
        oracle = summary["ORACLE"]["success_rate"]
        for method in list(methods) + ["ORACLE"]:
            summary[method]["ci95"] = wilson(summary[method]["successes"], summary[method]["scored"])
        for method in methods:
            rate = summary[method]["success_rate"]
            summary[method]["regret_vs_oracle"] = (oracle - rate) if None not in (rate, oracle) else None
            if method != "M0_RANDOM" and "M0_RANDOM" in methods:
                pairs_ = [(r["methods"][method]["success"], r["methods"]["M0_RANDOM"]["success"]) for r in per_seed]
                pairs_ = [(a, b) for a, b in pairs_ if a is not None and b is not None]
                wins = sum(1 for a, b in pairs_ if a and not b)
                losses = sum(1 for a, b in pairs_ if b and not a)
                summary[method]["vs_M0"] = dict(wins=wins, losses=losses, p=sign_test(wins, losses))
        executed = [r for r in per_seed if r["complete"] and r["feasible"]]
        summary["UNIFORM_FEASIBLE_EXPECTATION"] = dict(success_rate=(
            sum(r["succeeded"] / r["feasible"] for r in executed) / len(per_seed)) if executed and len(executed) == len(per_seed) else None)
        report["tasks"][task] = dict(
            summary=summary, seeds=per_seed,
            seed_variation=dict(
                feasible_mean=sum(map(len, sets)) / len(sets), feasible_min=min(map(len, sets)),
                distinct_feasible_sets=len({frozenset(s) for s in sets}),
                mean_pairwise_jaccard=sum(_jaccard(sets[i], sets[j]) for i, j in pairs) / len(pairs) if pairs else None,
                union_feasible=len(set().union(*sets)),
                distinct_successful_sets=len({frozenset(c for c in sets[i] if (episodes.get((task, seeds[i], c)) or {}).get("success"))
                                              for i in range(len(seeds))})))
    return report


def markdown(report, meta):
    lines = [f"# Oracle-table experiment", "",
             f"- candidates: {meta['generator']} ({meta['candidate_count']}, profile {meta['profile']})",
             f"- scene: {meta['scene']['name']} = {json.dumps(meta['scene'])}",
             f"- seeds: {meta['seeds']}; execute: {meta['execute']}; pour_water liquid: {meta.get('liquid')}", ""]
    for task, data in report["tasks"].items():
        v = data["seed_variation"]
        lines += [f"## {task}", "",
                  f"Feasible per seed: mean {v['feasible_mean']:.1f}, min {v['feasible_min']}; "
                  f"distinct feasible sets {v['distinct_feasible_sets']}/{len(data['seeds'])}; "
                  f"mean Jaccard {v['mean_pairwise_jaccard'] if v['mean_pairwise_jaccard'] is None else round(v['mean_pairwise_jaccard'], 2)}; "
                  f"distinct successful sets {v['distinct_successful_sets']}", "",
                  "| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |",
                  "|---|---|---|---|---|---|---|---|"]
        for method, s in data["summary"].items():
            if method == "UNIFORM_FEASIBLE_EXPECTATION":
                continue
            rate = "-" if s["success_rate"] is None else f"{s['success_rate']:.2f}"
            regret = s.get("regret_vs_oracle")
            ci = s.get("ci95")
            ci = "-" if not ci else f"{ci[0]:.2f}-{ci[1]:.2f}"
            vs = s.get("vs_M0")
            vs = "-" if not vs else f"{vs['wins']}/{vs['losses']}, p={vs['p']:.2f}"
            lines.append(f"| {method} | {s['successes']}/{s['scored']} | {rate} | {ci} | {vs} | "
                         f"{'-' if regret is None else f'{regret:.2f}'} | {s['distinct_selected_ids'] or '-'} | "
                         f"{s['selected_families'] or '-'} |")
        u = data["summary"]["UNIFORM_FEASIBLE_EXPECTATION"]["success_rate"]
        if u is not None:
            lines.append(f"| uniform over feasible (expectation) | | {u:.2f} | | | | | |")
        lines += ["", "| seed | feasible (by family) | succeeded/executed | " +
                  " | ".join(report["methods"][:-1]) + " |", "|" + "---|" * (3 + len(report["methods"]) - 1)]
        for r in data["seeds"]:
            cells = []
            for method in report["methods"][:-1]:
                m = r["methods"][method]
                mark = {True: "✓", False: "✗", None: "?"}[m["success"]]
                cells.append(f"{mark} {m.get('family') or m.get('reason')}")
            lines.append(f"| {r['seed']} | {r['feasible']} {r['feasible_by_family']} | "
                         f"{r['succeeded']}/{r['executed']} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tasks", nargs="+", choices=SEQUENCE_TASKS, default=["transport_place", "handover_transfer"])
    parser.add_argument("--seeds", nargs="+", type=int, default=list(range(10)))
    parser.add_argument("--scene", default="wide")
    parser.add_argument("--generator", choices=("multifamily_v3", "multifamily_v3p", "adaptive_v2"),
                        default="multifamily_v3",
                        help="multifamily_v3p = pruned v3 with semantic_v1 public labels")
    parser.add_argument("--execute", choices=("all", "selected", "none"), default="all")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--liquid", choices=LIQUID_MODES, default="proxy",
                        help="pour_water physics: proxy (seconds), fast particles, or default pbf (minutes)")
    parser.add_argument("--prior-from", type=Path,
                        help="run dir whose TRAINING-seed outcomes build the M4 family x task prior")
    parser.add_argument("--prior-seeds", nargs="+", type=int, default=list(range(10)))
    parser.add_argument("--report-seeds", nargs="+", type=int,
                        help="also write report_<first>-<last>.md over these seeds (e.g. held-out test seeds)")
    parser.add_argument("--llm-config", type=Path,
                        help="C config JSON; its kind=llm methods are added (default: mock M3/A1 plumbing only)")
    args = parser.parse_args(argv)
    from grasp_a.control import scene_randomization
    out = args.out.resolve()
    scene = scene_randomization(args.scene)
    # 1. B: one shared candidate file
    b_dir = out / "b_inputs"
    if not b_dir.exists():
        if args.generator.startswith("multifamily_v3"):
            from .prepare_b import prepare_b_v3_inputs
            pruned = args.generator == "multifamily_v3p"
            prepare_b_v3_inputs(b_dir, profile="hollow", pruned=pruned, semantic=pruned)
        else:
            _prepare_v2(b_dir)
    candidates = _candidates(out)
    families = {r["id"]: r for r in _read(b_dir / "families.internal.json")}
    meta = dict(generator=args.generator, profile="hollow", candidate_count=len(candidates),
                scene=asdict(scene), seeds=args.seeds, tasks=args.tasks, execute=args.execute,
                liquid=args.liquid)
    if (out / "meta.json").exists():
        old = _read(out / "meta.json")
        if (old["generator"], old["scene"]) != (meta["generator"], json.loads(json.dumps(meta["scene"]))):
            raise SystemExit("existing run uses another generator/scene; choose a new --out")
        if "pour_water" in args.tasks and old.get("liquid", args.liquid) != args.liquid and \
                (out / "episodes" / "pour_water").exists():
            raise SystemExit("existing pour_water episodes use another --liquid; choose a new --out")
        meta["tasks"] = sorted(set(old["tasks"]) | set(args.tasks))
        meta["seeds"] = sorted(set(old["seeds"]) | set(args.seeds))
    _write(out / "meta.json", meta)
    log = lambda *a: print(*a, flush=True)
    # 2. A: filter
    jobs = [(t, s) for t in args.tasks for s in args.seeds
            if not (out / "filters" / t / f"seed{s}.json").exists()]
    with ProcessPoolExecutor(args.workers) as pool:
        for fut in as_completed([pool.submit(_filter_job, str(out), t, s, asdict(scene)) for t, s in jobs]):
            task, seed, filtered = fut.result()
            _write(out / "filters" / task / f"seed{seed}.json", filtered)
            log(f"filter {task} seed{seed}: {len(filtered['feasible_candidates'])}/{len(candidates)} "
                f"({filtered['filter_seconds']:.1f}s)")
    # 3. selectors (before any outcome is read)
    from grasp_b.models import read_model
    import pybullet_data
    model = read_model("hollow", Path(pybullet_data.getDataPath()) / "franka_panda/panda.urdf")
    llm_methods = None
    if args.llm_config:
        llm_methods = [m for m in _read(args.llm_config)["methods"] if m.get("kind") == "llm"]
    else:
        llm_methods = [dict(id="M3_MOCK", variant="M3", provider={"type": "mock"}, model="mock", cache_mode="off"),
                       dict(id="A1_MOCK", variant="A1", provider={"type": "mock"}, model="mock", cache_mode="off")]
    prior = None
    if args.prior_from:
        if set(args.prior_seeds) & set(args.report_seeds or []):
            raise SystemExit("prior seeds and report seeds must not overlap")
        prior = family_prior(args.prior_from.resolve(), args.prior_seeds, args.tasks)
        meta["m4_prior"] = dict(source=str(args.prior_from), seeds=args.prior_seeds, table=prior)
        _write(out / "meta.json", meta)
    selectors = build_selectors(llm_methods, out / "llm_cache", prior=prior)
    sel_path = out / "selections.json"
    selections = _read(sel_path) if sel_path.exists() else {}
    for task, seed in itertools.product(args.tasks, args.seeds):
        public = _read(out / "filters" / task / f"seed{seed}.json")["feasible_candidates"]
        slot = selections.setdefault(task, {}).setdefault(str(seed), {})
        for method, fn in selectors.items():
            if method in slot and not slot[method].get("error") or not public:
                continue
            started = time.perf_counter()
            try:
                ranking = fn(task, public, seed, candidates, model, families)
                slot[method] = dict(ranking=ranking, seconds=time.perf_counter() - started)
            except Exception as exc:
                slot[method] = dict(error=f"{type(exc).__name__}: {exc}")
        _write(sel_path, selections)
    # 4. A: execute (cached per task/seed/candidate)
    if args.execute != "none":
        jobs = []
        for task, seed in itertools.product(args.tasks, args.seeds):
            public = _read(out / "filters" / task / f"seed{seed}.json")["feasible_candidates"]
            ids = ([r["id"] for r in public] if args.execute == "all" else
                   sorted({v["ranking"][0] for v in selections[task][str(seed)].values() if "ranking" in v}))
            jobs += [(task, seed, cid) for cid in ids
                     if not (out / "episodes" / task / f"seed{seed}" / f"{cid}.json").exists()]
        log(f"executing {len(jobs)} episodes on {args.workers} workers")
        done, started = 0, time.perf_counter()
        with ProcessPoolExecutor(args.workers) as pool:
            futures = [pool.submit(_episode_job, str(out), t, s, c, asdict(scene), args.liquid) for t, s, c in jobs]
            for fut in as_completed(futures):
                task, seed, cid, row = fut.result()
                _write(out / "episodes" / task / f"seed{seed}" / f"{cid}.json", row)
                done += 1
                log(f"[{done}/{len(jobs)} {time.perf_counter()-started:.0f}s] {task} seed{seed} "
                    f"{families[cid]['family']} {cid}: {row['status']} {row['success']} {row.get('failure_reason')}")
    # 5. C: report
    report = build_report(out, meta["tasks"], meta["seeds"], list(selectors), families)
    report["meta"] = meta
    _write(out / "report.json", report)
    (out / "report.md").write_text(markdown(report, meta), encoding="utf-8")
    print(markdown(report, meta))
    if args.report_seeds:
        seeds = sorted(args.report_seeds)
        sub = build_report(out, meta["tasks"], seeds, list(selectors), families)
        sub["meta"] = dict(meta, seeds=seeds, report_scope="held-out seeds")
        stem = f"report_seeds{seeds[0]}-{seeds[-1]}"
        _write(out / f"{stem}.json", sub)
        (out / f"{stem}.md").write_text(markdown(sub, sub["meta"]), encoding="utf-8")
        print(markdown(sub, sub["meta"]))


def _prepare_v2(b_dir: Path):
    """Baseline: B's adaptive_body_v2 candidates in the same shared format."""
    from dataclasses import replace
    import pybullet_data
    from grasp_b.adaptive import generate_adaptive
    from grasp_b.generation import write_candidates
    from grasp_b.models import read_model
    from grasp_b.risk import static_score
    model = read_model("hollow", Path(pybullet_data.getDataPath()) / "franka_panda/panda.urdf")
    candidates = generate_adaptive(model)
    scored = [replace(c, geometry_score=float(static_score(c, model)["score"])) for c in candidates]
    b_dir.mkdir(parents=True)
    write_candidates(b_dir / "candidates.scored.json", scored)
    _write(b_dir / "families.internal.json",
           [dict(id=c.id, family="side", region="body", m1_m2_supported=True) for c in scored])


if __name__ == "__main__":
    main()
