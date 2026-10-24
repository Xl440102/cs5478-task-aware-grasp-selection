# Candidate generation v0.1

Run from the repository root in `grasp-a`:

```bash
python -m grasp_b.generation --check
python -m unittest discover -s tests -v
```

The CLI writes only under `results/local/part_b_v1/`: `candidates.json` is
validated by A's loader; `report.json` records parameters, URDF hashes,
placeholder status, per-seed rejection evidence and feasible diversity.
Use `--config` and `--out-dir` for separate development configurations.
No episode results are inputs to generation. `--check` calls A feasibility
only; it never runs an episode or removes candidates from the exported set.

## Frames and units

Mug-local origin is the body collision cylinder centre; +Z is up and the
handle extends along +Y. A applies the initial mug world transform.
Tool is `panda_grasptarget`, not the hand COM or link inertial frame. Its
fixed transform from `panda_hand` is (0,0,0.105) metres without rotation.
Finger prismatic axes are tool +/-Y, each with 0.04 m maximum travel.
Quaternions are normalized xyzw and map tool vectors into mug coordinates.
Positions and jaw openings are metres; configuration angles are degrees.

Orientation is Rz(yaw) Ry(90 degrees) Rz(roll). Tool +Z is the approach
motion direction (cos(yaw),sin(yaw),0). Grasp is (0,0,height);
pregrasp = grasp - offset * tool_Z with the same orientation. Tool roll
is around local Z, not a rotation around mug X. At roll 0/180 the closing
axis is horizontal and tangent to the cylinder. A 180-degree roll exchanges
finger sides: it changes robot orientation but is not a distinct contact
family for an ideal symmetric parallel gripper.

Width = collision cylinder diameter + opening margin = 0.078 m in v1.
This is a geometric aperture request, not a quality score. A currently
opens fully to 0.08 m and closes to zero; it uses candidate width as a
feasibility constraint, rather than commanding that exact opening.
Body geometry comes from the current mug URDF; full finger/hand meshes,
handle and robot interactions are checked by A, not approximated as safe
by B. The parser deliberately requires the current centred cylinder and
Panda axis convention; this is not a generic URDF grasp planner.

## Reproducibility and limitations

The generator sorts parameter axes and hashes canonical versioned pose/width
parameters to make stable IDs independent of input order or subset. It
rejects duplicate normalized angles, invalid dimensions/offsets and apertures.
The CLI records model hashes; model changes require a new report/config
review, even where candidate poses and IDs remain unchanged.

`geometry_score()` is a reserved interface returning 0.0. All scores are
placeholders and MUST NOT be used for formal ranking. No task suitability,
execution labels or preferred IDs are encoded.

Development seeds 0,1,2 each accept five of twelve candidates with the same
membership. Feasible candidates cover yaw 0/45 degrees, roll 0/180 degrees,
and height 0.012/0.024 m, with one offset and width. This supports integration
and multi-candidate selection checks, but does not establish distinct task
outcomes or adequate benchmark contact diversity. These seeds are development
seeds, not a held-out evaluation set. Historical v0.1 generation only; current standardized M0/M1/M2 APIs are documented below.

## Post-PR review additions (2026-10-16)

`python -m grasp_b.generation --check` now allocates a fresh run directory
under `results/local/part_b_review/`; earlier development data is preserved.
It additionally writes `features.internal.json` with static geometric formulas,
model/source hashes, units, contact-model limitations and equivalence pairs.
These are INTERNAL DIAGNOSTICS and must not be supplied to ID-only selectors.
A maps local poses through the initial estimate in v0.3; the historical v0.1
seed counts above were obtained with bounded_ik_v1 and are not new evaluation.

`selectors.py` validates the exact five public fields, checks ranking IDs and
provides deterministic score sorting with stable ID ties. Sorting is disabled
unless the caller explicitly supplies a validated score version; no actual
score version is approved here. `smoke_selector` uses sorted IDs, not scores,
and is explicitly SMOKE_ONLY. `runner.evaluate_smoke` delegates to C's real
wrapper; it raises a clear unavailable error before the A/C v0.3 merge.
No private geometry lookup, execution data or diagnostic sidecar enters it.

See `docs/PART_B_IMPLEMENTATION_REPORT.md` and
`docs/PART_B_TEAM_DECISIONS.md` for tested revisions and remaining decisions.

## Explicit model and task-risk experimental API (v2)

New work uses `models.py`, `adaptive.py`, `motion.py`, `risk.py`,
`benchmark.py` and `sensitivity.py`. It supports the solid and hollow A cup
profiles, geometry-scaled body candidates, an independent static load baseline
and three task-specific trajectory/space-risk evaluators. Legacy entry points,
score versions and C integration above remain unchanged. Earlier paragraphs
about zero-only scoring and no task-risk implementation describe historical
v0.1, not this independent v2 API. Current definitions: M0 is seeded random,
M1 is task independent geometry scoring (Legacy primary; Static v2 internal
refinement/ablation), and M2 is the task rule based risk scorer.
See [formulas, trajectory audit, assumptions and commands](../docs/PART_B_RISK_V2.md).

Standardized method tests are split by responsibility: `test_b_m0_random.py`, `test_b_m1_geometry.py`, `test_b_m1_static.py`, `test_b_m2_transport.py`, `test_b_m2_handover.py`, `test_b_m2_pouring.py`, `test_b_m2_common.py`, and `test_b_integration.py`. Asset and motion input contracts remain in `test_b_models.py` and `test_b_motion.py`; generation, feature and scoring tests retain their functional names. Run all B tests with `python -m unittest discover -s tests -p 'test_b*.py' -v`.
