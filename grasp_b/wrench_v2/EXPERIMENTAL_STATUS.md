# Experimental checkpoint: Tasks 04V / 04W

`m1_gws_v2` is experimental physics-based ranking, not a benchmark-ready or
physically validated grasp quality measure. The 116-candidate runs are
**generator-pool diagnostics**, not A-feasible matched-seed comparisons.

- ContactSpec contains predicted analytical/static geometry contacts.
- Multiple samples do not establish a real contact patch, simultaneous load
  sharing, compliant pressure distribution, or calibrated torsional capacity.
- Friction and finger normal capacity remain uncalibrated. Development values
  (mu=0.5, 5 N/finger) are explicit assumptions, never motor-force conversions.
- Model support differs across families. Current two-point PCWF models cannot
  carry some axial moments; four-point Body models can generate additional
  moments under their independent-contact-force assumptions.
- `model_infeasible` means infeasible under that model, not inevitable failure
  in PyBullet or on a real robot. Unscored IDs remain an ID-sorted diagnostic
  tail, not a physical quality ordering. No scored candidate means no choice.
- Transport and donor-only Handover currently have identical load sets. Pour
  uses a finite quasi-static envelope and rigid attached-water assumptions,
  not sloshing, draining, PBF dynamics or a continuous worst-case guarantee.

## Numerical recovery and remaining case

Solver `shared_capacity_utilization_v1.2` retries only a HiGHS Unknown status
once with IPM. Original attempts, options and backend call counts are retained.
Task 04V reproduced 202 Unknown results under simplex; all had rank-5 matrices
and incompatible target wrenches verified by left-null-space witnesses. IPM
classified these as infeasible. This did not change finite scores or ranking.

The mu=0.75 fixture for `B3_db2df20cfd3cb7be` remains `numerical_error` under
default settings. Simplex reports optimal but fails the independent relative
residual check; IPM's reply violates the absolute non-negativity tolerance.
A feasible witness constructed from the narrower friction cone satisfies the
original physical and relative tolerances; it is not an optimality proof.

Task 04W's bounded diagnostic used the existing **explicit** normalization
`WrenchScaling(reference_force_n=1, characteristic_length_m=0.1)` together with
`backend_feasibility=1e-10` (tighter than the default 1e-9). It obtained
utilization approximately 0.368892164859, no negative coefficient violation,
and physical residuals below 1e-12. Capacity constraints, physical parameters
and all acceptance tolerances were unchanged. A real HiGHS regression covers
this result. This numerical setup is **not enabled implicitly in M1**; its
effects across the pool and sensitivity variants must be assessed before
adopting a global policy. No jitter, clipping or constraint deletion is used.

## Before a formal pilot

Resolve contact-model comparability, calibrate or freeze declared uncertainty
ranges, define finite pose/disturbance coverage, retain numerical failures, and
freeze a shared A-feasible candidate/seed/physics/evaluator protocol. M2 motion
estimates cannot repair missing static contact mechanisms. No M2 implementation
or physical rollout is part of this checkpoint.

Regression command (grasp-a environment):

```bash
python -B -m unittest discover -s tests -p 'test_b_wrench*.py' -v
```

Local diagnostic outputs under `results/local/` are intentionally excluded from
the commit. Existing baseline algorithms, A/C code, root README and A/C docs
are unchanged.
