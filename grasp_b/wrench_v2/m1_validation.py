"""04V diagnostics only: fixed geometric ablations and one-factor experiments.

No change to generator, nominal TWS, scoring weights, A/C or runtime physics.
python -B -m grasp_b.wrench_v2.m1_validation --baseline-report ... --output-dir NEW
"""
from collections import Counter, defaultdict
from dataclasses import replace
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import pybullet_data

from grasp_a.contracts import candidate_sha256
from grasp_b.multifamily import VERSION, generate_multifamily, pruned_config
from grasp_b.models import read_model
from .adapter import audit_candidates
from .contacts import ContactSpec, ContactStatus, FingerID, GraspFamily, _plain
from .constraints import CapacityValue
from .geometry import ObjectGeometry, read_gripper_geometry
from .gws import FrictionValue, grasp_matrix, build_primitive_wrenches
from .m1 import development_config, development_task_sets, prepare_contact_spec, score_prepared
from .m1_smoke import distribution
from .ranking import rank_results
from .solver import SciPyHighsBackend, build_lp_problem, solve_wrench, NumericalTolerances
from .tasks import read_declared_object, axis_disturbances


def two_point_spec(spec, candidate):
    """One EXISTING point/finger nearest the TCP closing LINE, in mug frame.

    Never average adjacent facet normals/positions or consult LP scores. Ties
    use surface ID and coordinates. All original capacity groups are retained.
    This is an opt-in diagnostic model, never a change to production adapter.
    """
    if candidate.id != spec.candidate_id or candidate_sha256([candidate]) != spec.candidate_hash:
        raise ValueError("candidate/spec identity mismatch")
    if spec.family is not GraspFamily.BODY or spec.status not in (ContactStatus.VALID, ContactStatus.APPROXIMATE):
        return spec
    center, axis = np.array(candidate.grasp_pose.position), np.array(spec.closing_direction)
    selected = []
    for finger in FingerID:
        def key(point):
            delta = np.array(point.position_m)-center
            distance = float(np.linalg.norm(delta-float(delta@axis)*axis))
            return distance, point.surface_id, point.position_m
        selected.append(min((p for p in spec.contacts if p.finger_id is finger), key=key))
    return replace(spec, contacts=tuple(selected), provenance={**spec.provenance,
        "diagnostic_ablation": "one_existing_point_per_finger_nearest_tcp_closing_line_v1"},
        assumptions=(*spec.assumptions, "04V diagnostic two-point subset; no averaged surface or validated patch assumed"))


def matrix_diagnostics(data, constraints, wrench):
    # Fixed diagnostic force/torque scale, NOT candidate-dependent normalization.
    scale = np.array((1., 1., 1., 10., 10., 10.))
    a, w = scale[:, None]*data.matrix, scale*np.array(wrench)
    u, singular, _ = np.linalg.svd(a, full_matrices=True)
    threshold = singular[0]*1e-10
    rank = int(np.count_nonzero(singular > threshold))
    residual = u[:, rank:]@(u[:, rank:].T@w)
    distance = float(np.linalg.norm(residual))
    witness = residual/distance if distance else np.zeros(6)
    p = build_lp_problem(data, constraints, wrench)
    def details(matrix):
        nonzero = np.abs(matrix[matrix != 0])
        norms = np.linalg.norm(matrix, axis=1)
        unit_rows = matrix[norms > 0]/norms[norms > 0, None]
        return dict(shape=list(matrix.shape), rank=int(np.linalg.matrix_rank(matrix)),
            min_nonzero=None if not len(nonzero) else float(min(nonzero)),
            max_nonzero=None if not len(nonzero) else float(max(nonzero)),
            exact_duplicate_columns=matrix.shape[1]-np.unique(matrix, axis=1).shape[1],
            exact_proportional_rows=len(unit_rows)-np.unique(unit_rows, axis=0).shape[0])
    return dict(A=details(data.matrix), B=details(constraints.matrix),
        equality=details(p.equality_matrix), inequality=details(p.inequality_matrix),
        scaled_singular_values=singular.tolist(), scaled_rank=rank, rank_threshold=threshold,
        diagnostic_scaling="F*=1 N, L=0.1 m", left_null_distance=distance,
        left_null_witness=witness.tolist(), witness_A_error=float(np.max(np.abs(witness@a))),
        witness_w=float(witness@w), outside_span=distance > 1e-7)


def compare_unknowns(old):
    config = development_config()
    if old["manifest"]["config"] != config.to_dict():
        raise ValueError("baseline config differs; do not silently compare different physics")
    cases = []
    totals = defaultdict(Counter)
    for task, table in old["tasks"].items():
        for row in table["scores"]:
            failures = [s for s in row["scenario_results"] if s["solver_result"]["status"] == "numerical_error"]
            if not failures:
                continue
            prepared = prepare_contact_spec(ContactSpec.from_dict(row["provenance"]["contact_spec"]), config)
            for scenario in failures:
                previous = scenario["solver_result"]
                w = previous["requested_wrench"]
                trials = {}
                for method in ("highs", "highs-ds", "highs-ipm"):
                    for presolve in (True, False):
                        result = solve_wrench(prepared.primitives, w, constraints=prepared.constraints,
                            backend=SciPyHighsBackend(method=method, presolve=presolve, retry_unknown=False))
                        key = f"{method}/presolve={presolve}"
                        trials[key] = result.to_dict()
                        totals[key][result.status.value] += 1
                cases.append(dict(task=task, candidate_id=row["candidate_id"], family=row["family"],
                    scenario_id=scenario["scenario_id"], previous=previous,
                    previous_presolve="implicit HiGHS default True, inferred; not in original log",
                    matrix_diagnostics=matrix_diagnostics(prepared.primitives, prepared.constraints, w),
                    A=prepared.primitives.matrix.tolist(), B=prepared.constraints.matrix.tolist(),
                    b=prepared.constraints.bounds_n.tolist(), w=w, comparisons=trials))
    return dict(cases=cases, summary={k: dict(v) for k, v in totals.items()},
        case_count=len(cases), by_family=dict(Counter(c["family"] for c in cases)),
        by_task=dict(Counter(c["task"] for c in cases)))


def task_summary(rows, task_set, baseline=None):
    ranked = rank_results(rows, [r.candidate_id for r in rows])
    scored = [r for r in ranked["scores"] if r.status == "scored"]
    family = defaultdict(Counter)
    for r in rows:
        family[r.family][r.status] += 1
    top = [r.candidate_id for r in scored[:5]]
    return dict(status_counts=dict(Counter(r.status for r in rows)),
        family_counts={k: dict(v) for k, v in family.items()}, selected_id=ranked["selected_id"], top5=top,
        top1_same_as_baseline=None if baseline is None else ranked["selected_id"] == baseline["selected_id"],
        top5_overlap=None if baseline is None else len(set(top)&set(baseline["top5"])),
        worst_utilization=distribution([r.worst_utilization for r in scored]),
        scenarios=task_set.to_dict(), ranking=ranked["ranking"],
        rows=[dict(candidate_id=r.candidate_id, family=r.family, status=r.status, score=r.score,
                   worst_utilization=r.worst_utilization, worst_scenario_ids=r.worst_scenario_ids,
                   scenario_status_counts=dict(r.provenance["scenario_status_counts"])) for r in rows],
        numerical_errors=[dict(candidate_id=r.candidate_id, scenario_id=sid, solver_result=lp.to_dict())
                          for r in rows for sid, lp in r.scenario_results if lp.status.value == "numerical_error"],
        lp_calls=sum(r.timings_s["lp_calls"] for r in rows))


def changed_perturbations(sets, amplitude):
    if amplitude == .1:
        return sets
    ds = axis_disturbances(horizontal_force_n=amplitude, vertical_force_n=0, application_point_m=(0, 0, 0),
                           source="04V one-factor assumed perturbation amplitude")
    result = {}
    for name, ts in sets.items():
        nominal = [s for s in ts.scenarios if not any(s.disturbance.force_n)]
        result[name] = replace(ts, scenarios=tuple(replace(s, disturbance=d) for s in nominal for d in ds))
    return result


def run_validation(baseline_path, output):
    if output.exists():
        raise FileExistsError("refusing existing audit output")
    began = perf_counter()
    root = Path(__file__).resolve().parents[2]
    raw = baseline_path.read_bytes()
    old = json.loads(raw)
    unknowns = compare_unknowns(old)
    print("Unknown comparisons:", unknowns["summary"], flush=True)
    panda = Path(pybullet_data.getDataPath())/"franka_panda/panda.urdf"
    model = read_model("hollow", panda)
    candidates, records = generate_multifamily(model, pruned_config())
    if candidate_sha256(candidates) != old["manifest"]["candidate_sha256"]:
        raise ValueError("generator pool does not match frozen Task04 input hash")
    geometry = ObjectGeometry.from_model(model)
    static = audit_candidates(candidates, geometry, read_gripper_geometry(panda), records=records,
                              generator_name="grasp_b.multifamily", generator_version=VERSION)
    specs = static["specs"]
    by_id = {c.id: c for c in candidates}
    config = development_config()
    body = read_declared_object(root/"assets/mug_hollow.urdf")
    variants = [("baseline", config, body, .1, False)]
    for mu in (.25, .75):
        variants.append((f"mu_{mu}", replace(config, friction=FrictionValue(coefficient=mu, source="04V one-factor uncalibrated mu")), body, .1, False))
    for capacity in (2.5, 10.):
        capacities = {f: CapacityValue(normal_force_n=capacity, source="04V one-factor uncalibrated capacity") for f in FingerID}
        variants.append((f"capacity_{capacity}", replace(config, finger_capacities=capacities), body, .1, False))
    variants.append(("body_two_point", config, body, .1, True))
    for com in (-.005, .005):
        variants.append((f"com_x_{com}", config, replace(body, com_m=(com, 0, 0), source="04V assumed COM uncertainty; not changed runtime physics"), .1, False))
    for amplitude in (0., .2):
        variants.append((f"perturbation_{amplitude}", config, body, amplitude, False))
    report = {}
    for label, cfg, mass, amplitude, reduce_contacts in variants:
        chosen = [two_point_spec(s, by_id[s.candidate_id]) if reduce_contacts else s for s in specs]
        prepared = [prepare_contact_spec(s, cfg) for s in chosen]
        sets = changed_perturbations(development_task_sets(mass, cfg), amplitude)
        report[label] = dict(config=cfg.to_dict(), body=_plain(mass), tasks={})
        for name, ts in sets.items():
            rows = [score_prepared(p, ts) for p in prepared]
            baseline = None if label == "baseline" else report["baseline"]["tasks"][name]
            report[label]["tasks"][name] = task_summary(rows, ts, baseline)
        print(label, {name: t["status_counts"] for name, t in report[label]["tasks"].items()}, flush=True)
    pairs = []
    for spec in specs:
        if spec.family is not GraspFamily.BODY or len(spec.contacts) != 4:
            continue
        reduced = two_point_spec(spec, by_id[spec.candidate_id])
        models = [prepare_contact_spec(s, config) for s in (spec, reduced)]
        axis = np.array(reduced.contacts[0].position_m)-reduced.contacts[1].position_m
        axis /= np.linalg.norm(axis)
        pair = dict(candidate_id=spec.candidate_id, selection=reduced.provenance["diagnostic_ablation"], models=[])
        for p in models:
            w = np.concatenate((np.zeros(3), .001*axis))
            result = solve_wrench(p.primitives, w, constraints=p.constraints)
            pair["models"].append(dict(contacts=[c.to_dict() for c in p.spec.contacts],
                G_rank=int(np.linalg.matrix_rank(grasp_matrix(p.spec))), A_rank=int(np.linalg.matrix_rank(p.primitives.matrix)),
                capacity_groups=[g.to_dict() for g in p.spec.capacity_groups], resolved_constraints=p.constraints.to_dict(),
                axial_torque_test=result.to_dict(), axial_negative_test=solve_wrench(
                    p.primitives, -w, constraints=p.constraints).to_dict()))
        pairs.append(pair)
    family = {}
    sets = development_task_sets(body, config)
    for family_name in ("body", "top", "handle", "rim"):
        selected = [s for s in specs if s.family.value == family_name]
        active = [s for s in selected if s.status in (ContactStatus.VALID, ContactStatus.APPROXIMATE)]
        family[family_name] = dict(geometry=dict(Counter(s.status.value for s in selected)),
            geometry_reasons=dict(Counter(reason for s in selected for reason in s.reasons)),
            mechanisms=dict(Counter(s.mechanism.value for s in selected)),
            contact_counts=dict(Counter(len(s.contacts) for s in active)),
            G_ranks=dict(Counter(int(np.linalg.matrix_rank(grasp_matrix(s))) for s in active)),
            A_ranks=dict(Counter(int(np.linalg.matrix_rank(build_primitive_wrenches(s, friction=config.friction, k=config.k).matrix)) for s in active)),
            normal_closing_dot_min=min(abs(float(np.dot(p.normal_outward, s.closing_direction))) for s in active for p in s.contacts),
            normal_sign_violations=sum((1 if p.finger_id is FingerID.LEFT else -1)*float(
                np.dot(p.normal_outward, s.closing_direction)) <= 1e-8 for s in active for p in s.contacts))
        for task, ts in sets.items():
            statuses = Counter()
            failures = Counter()
            outside = 0
            for s in active:
                p = prepare_contact_spec(s, config)
                for scenario in ts.scenarios:
                    r = solve_wrench(p.primitives, scenario.required_contact_wrench, constraints=p.constraints)
                    statuses[r.status.value] += 1
                    if r.status.value != "optimal":
                        failures[scenario.scenario_id] += 1
                        outside += matrix_diagnostics(p.primitives, p.constraints, scenario.required_contact_wrench)["outside_span"]
            family[family_name][task] = dict(lp_status_counts=dict(statuses), failed_scenarios=dict(failures),
                outside_span_failures=outside, lp_total=sum(statuses.values()))
    monotonicity = {}
    for label in report:
        if label.startswith("com_") or label == "baseline":
            continue
        monotonicity[label] = {}
        for task, table in report[label]["tasks"].items():
            base_rows = {r["candidate_id"]: r for r in report["baseline"]["tasks"][task]["rows"] if r["status"] == "scored"}
            common = [r for r in table["rows"] if r["status"] == "scored" and r["candidate_id"] in base_rows]
            # Comparisons are PER CANDIDATE: medians of different scored
            # populations need not be monotone (selection/survivorship effect).
            decrease = label in ("mu_0.75", "capacity_10.0", "perturbation_0.0")
            differences = [(r["worst_utilization"]-base_rows[r["candidate_id"]]["worst_utilization"])*(1 if decrease else -1) for r in common]
            monotonicity[label][task] = dict(shared_scored=len(common),
                max_violation=max([0., *differences]), violations_above_1e_7=sum(d > 1e-7 for d in differences))
    output.mkdir(parents=True, exist_ok=False)
    data = {"unknown_cases.json": unknowns, "sensitivity.json": report, "contact_ablation.json": pairs, "families.json": family,
        "per_candidate_monotonicity.json": monotonicity,
        "manifest.json": dict(version="m1_validation_04v_v1", baseline_path=str(baseline_path),
            baseline_sha256=hashlib.sha256(raw).hexdigest(), runtime_s=perf_counter()-began,
            generator_pool_count=len(candidates), a_feasible_count=None, physical_execution=False,
            source_sha256={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/"grasp_b/wrench_v2").glob("*.py")})}
    for name, value in data.items():
        with (output/name).open("x") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
    print("OUTPUT", output.resolve(), "seconds", perf_counter()-began, flush=True)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    run_validation(args.baseline_report, args.output_dir)


if __name__ == "__main__":
    main()
