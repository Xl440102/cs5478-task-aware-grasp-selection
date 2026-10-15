# Part A Interface v0.1

This is the integration contract between A (simulation and feasibility), B
(candidates and non-LLM selectors), and C (LLM selection and evaluation).

## Shared assumptions

- PyBullet simulates a Panda-style 7-DoF arm, parallel gripper, mug, and table.
- `task_id` is `pour_prep` or `handover_prep`.
- `seed` fixes the mug's initial pose and deterministic scene perturbations.
- Candidate positions use metres; quaternions use `[x, y, z, w]` order.
- `grasp_pose` and `pregrasp_pose` are in the **mug frame**. A converts them to
  world coordinates using the seeded initial mug pose.
- A owns feasibility and execution. B/C return an existing candidate ID;
  they do not return robot joint commands or new poses.

## Candidate input

The candidate file is a JSON list. Each item has:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable unique ID within one candidate set |
| `region` | string | Semantic region, e.g. `body` or `handle` |
| `approach` | string | Human-readable approach label |
| `gripper_width` | number | Total jaw opening in metres |
| `geometry_score` | number | B-owned, predeclared geometric score |
| `grasp_pose` | object | Mug-frame `{"position": [x,y,z], "quaternion": [x,y,z,w]}` |
| `pregrasp_pose` | object | Mug-frame pre-grasp pose, same shape |

Only `id`, `region`, `approach`, `gripper_width`, and `geometry_score` are sent
to selectors. Task suitability labels and episode results are not candidate
metadata. The scores in `candidates.example.json` are pilot values, not a
frozen B ranking. `candidates.pilot.json` contains G03 for calibration; its
zero score is only a placeholder.

## A API

```python
from grasp_a.contracts import load_candidates
from grasp_a.simulation import get_feasible_candidates, run_episode

candidates = load_candidates("candidates.example.json")
filtered = get_feasible_candidates(seed=0, candidates=candidates)
if filtered["status"] == "no_feasible_candidate":
    result = None  # Log this seed and skip selection/execution.
else:
    selected_id = "G02"  # Seed-0 example; replace with B/C selector output.
    assert selected_id in {c["id"] for c in filtered["feasible_candidates"]}
    result = run_episode("handover_prep", 0, selected_id, candidates)
```

### `get_feasible_candidates(seed, candidates)`

The function returns `seed`, `status`, `feasible_candidates`, and `checks`.
This is an abbreviated seed-0 example:

```json
{
  "seed": 0,
  "status": "ok",
  "feasible_candidates": [
    {"id": "G02", "region": "body", "approach": "from_robot_side_high",
     "gripper_width": 0.078, "geometry_score": 0.66}
  ],
  "checks": [
    {"candidate_id": "G01", "feasible": false,
     "reasons": ["approach:robot_table"]},
    {"candidate_id": "G02", "feasible": true, "reasons": []}
  ]
}
```

Actual check records also contain pose errors, table clearance, and collision
details. `status` is `ok` if at least one candidate is feasible and
`no_feasible_candidate` otherwise. A applies width, IK/FK pose error, joint
limit, and sampled path collision checks. Intended finger-to-mug contact at
the final approach sample is allowed.

### `run_episode(task_id, seed, selected_id, candidates)`

The function creates a fresh world, rechecks the selected candidate, and
executes the same controller for every selector. A result includes `seed`,
`task_id`, `selected_id`, `feasibility`, `success`, `failure_reason`,
`dropped`, `collision`, `collision_types`, `grasp_maintained`,
`position_error_m`, `orientation_error_deg`, `final_mug_pose`, and
sampled `trajectory`. A completed motion also includes `target_mug_pose`.
Early failures can omit that target and set pose errors to `null`. With
`--visualize`, the result includes `media` paths for the video and images.

The `bounded_ik_v1` controller adds `controller_version` to filter and episode
results, and `ik_diagnostics` to episode results. Each diagnostic records the
stage, target pose, movable DOF count, solver attempts, raw joint-limit
violations, FK errors, and accepted joint positions. These are additional
debugging fields; candidate and selector inputs are unchanged.

An invalid `task_id` or an ID absent from the supplied candidate set raises
`ValueError`. An existing but infeasible ID returns `failure_reason:
"infeasible"` and preserves collision evidence. B/C must choose from the
current `feasible_candidates` and skip selection when the list is empty.

## Task definitions and matched seeds

- `pour_prep`: lift the mug, tilt toward the fixed 35-degree target, and hold
  for two seconds.
- `handover_prep`: lift the mug, turn the handle toward the handover direction,
  and hold for two seconds.
- Success requires a maintained grasp, no unintended collision, and final
  pose errors within pilot tolerances of 0.04 m and 15 degrees.

For one seed, all methods must receive the same candidate list, scene, task,
and controller settings. A creates a new world for every episode, so methods
do not share simulation state. Thresholds, candidate IDs, scores, and task
targets must be frozen before the evaluation seeds are run.
