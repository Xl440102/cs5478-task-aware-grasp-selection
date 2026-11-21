# Part B contact refinement and relaxed selection

This opt-in package preserves A Candidate/sequence contracts and the historical
Part C benchmark. Importing it does not run simulations, models or experiments.

| Method ID | Implementation | Meaning |
|---|---|---|
| `m1_gws_v2` | `grasp_b.wrench_v2.m1` | Strict equilibrium and worst finite-task shared finger-capacity utilization |
| `m2_gws_trajectory_v2` | `grasp_b.wrench_v2.m2` | Same strict model for candidate-dependent predicted trajectory wrenches |
| `relaxed_m1_task_lex_v1` | `relaxed_solver.py`, `relaxed_ranking.py` | Joint worst scaled residual first, then utilization within its accepted radius |
| `relaxed_m2_task_lex_v1` | Same joint solver/ranker | Same formulation with original required trajectory demands |

Strict and relaxed identities/statuses remain distinct. `scored_conditional` is
numeric coverage, not strict feasibility or a success probability. Shared finger
budgets, units/reference frames, tolerances, demand masks and anchored near-tie
bands are unchanged.

`diagnostics.py` explains declared-model infeasibility. `geometry_validation.py`,
`bullet_alignment.py`, `collision_regions.py` and immutable evidence types inspect
static contact representations. Collision queries do not establish load-bearing
pressure patches, simultaneous independent contacts or force closure. Neither
queries nor observed outcomes silently repair predicted contacts.

`numerical_oracles.py` and `ranking_stability_audit.py` provide independent small
LP checks and uncertainty classification. `validation.py` contains unchanged
identity/cone-nesting helpers extracted from final historical audit drivers;
the bulk reconstruction drivers and datasets are not included.

## Validation

From the repository root, using Python 3.11 and project dependencies:

```bash
python -m pip install -r requirements-b.txt -r requirements-c.txt
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -B scripts/validate_part_b_release.py \
  --output /tmp/cs5478-part-b-check/results.json
```

The suite uses real small HiGHS fixtures, static geometry, contact/solver/ranking
regressions, mock batch adapters and offline A/C contracts. Physical stepping,
episode execution and network connections are guarded. Three real rollout/video
tests are deferred; optional historical cases skip when local bundles are absent.
Corrupt or incompatible archives must still be rejected.

Focused mathematical checks:

```bash
python -B -m unittest tests.test_b_task_relaxed_solver \
  tests.test_b_task_relaxed_ranking tests.test_b_numerical_oracles
```

The epigraph and protocol files in `tests/fixtures/` are mathematical or synthetic
contract inputs, never physical results or historical success records.

## Frozen adapters and limitations

`phase1.py`/`phase1_cli.py` and `phase2.py`/`phase2_cli.py` retain verified development
registry/frozen-plan contracts. Historical defaults require original source-bound
Task09 bundles under `results/local/`, intentionally not shipped. They must refuse
missing, changed or incompatible inputs. Source integration neither authorizes a
rollout nor makes historical physical plans valid against new source.

The nominal motion mirror accepts exact reviewed source tuples. The added main
profile accounts only for postponed annotations in `grasp_a/control.py`; it does
not certify physics, IK or historical execution.

Friction and force capacities remain uncalibrated. Raw meshes and Bullet collision
representations differ; independent Body multi-point load sharing is unproven.
Rigid-water prediction is not PBF dynamics. Finite sampling is not a continuous-time
guarantee. No new physical advantage or latest-B/LLM comparison is claimed.

See [release scope and verification](../../docs/part-b-release/README.md).
