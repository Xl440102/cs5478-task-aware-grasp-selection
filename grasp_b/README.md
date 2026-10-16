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
seeds, not a held-out evaluation set. No M0/M1/M2 is implemented here.
