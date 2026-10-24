# Part B model adaptation and trajectory risk v2

> Baseline and metric audit added 2026-10-24. Existing score versions and
> Oracle outputs remain historical records; no Oracle result is an input to a
> score or ranking.

This document freezes the requested team definitions for this B implementation.
A/C source, Candidate, five public selector fields, legacy generation/scoring
versions and historical results stay unchanged. M0 is seeded random selection.
M1 is task independent geometry/static physics scoring; Legacy is the primary
comparison and Static v2 is an internal refinement/ablation. M2 is handcrafted
task aware risk scoring from explicit geometry and estimated/predicted motion.
It is a heuristic, not a learned model or success probability.

## Existing methods and shared candidates

`prototype.select_random` preserves C's local seeded RNG and ordered input; this is formal M0.
`score_geometry` retains `experimental_geometry_weighted_v0.1`, equal alignment,
symmetry and normalized COM-distance components. `generation_v1.json`, zero
placeholder generation and the old public selector remain available unchanged.

For this M1 Legacy implementation the configured normalized weights are
`w_alignment=w_symmetry=w_com=1/3`:

```text
S_com = max(0, 1 - ||contact_midpoint - URDF_COM|| / (body_height/2))
S_legacy = (S_alignment + S_symmetry + S_com) / 3
```

Alignment and symmetry are the existing dimensionless ideal-cylinder proxies
in `features.py`; COM distance and reference length are metres. All components
must be valid; unavailable data are not imputed or reweighted. This preserves
historical semantics and does not establish contact stability.

The new benchmark generates one raw ordered Candidate list per model profile,
filters it with A, and ranks exactly that feasible subset with every method.
Scoring never deletes a candidate or runs an episode to determine eligibility.
Unsupported geometry aborts comparison with a clear exception; it does not
silently become zero or disappear. Raw Candidate.geometry_score stays zero in
this independent experiment; computed legacy/static/M2 scores are explicit
report records, not input mutations or hidden selector lookups. Do not pass
these raw placeholder scores to C's geometry baseline: its existing prepare-b
workflow is intentionally unchanged. Stable IDs hash normalized pose, width and
generator version; they do not encode task suitability.

## Model profiles and adaptive generation

`models.read_model('solid'|'hollow', panda_path)` selects the exact A cup asset.
It records both URDF hashes, model version, all collision shapes/transforms,
mass, full inertia tensor and COM. Missing inertial origin defaults to zero as
in URDF; this fixes the hollow model's lack of an explicit origin element.
Box bounds account for rotation; finite cylinders use exact oriented AABBs.
Meshes and articulated/multilink cups are explicitly unsupported.

Known-cup profile assumption: the single cylinder is the solid body or hollow
bottom; handle boxes have local Y > 1.4 times that cylinder radius. The remaining
primitives form the body. This rule is scale-relative and specific to these two
assets, not semantic segmentation of arbitrary URDFs. The hollow walls' composite
AABB defines the exterior cylindrical contact **proxy**. Actual wall facets,
handle, hand meshes and robot collisions remain A's checks. A support envelope
is not a proof of force closure or a claim to exact hollow-wall contact normals.

`generate_adaptive` derives a usable height interval from body bounds minus an
8 mm pad half-height assumption. Defaults sample fractions .15/.35/.50/.65/.85
of that interval, yaw every 45 degrees, and roll 0/180 degrees: 80 candidates.
Opening is exterior diameter times 1.10; pregrasp distance is the larger of
the 105 mm Panda tool offset and 3.5 body radii. For this cup that is 122.5 mm.
The prior additive half-offset formula yielded 175 mm and reduced A acceptance
from 6 to 4 in the same sequence seed; the shorter geometry-scaled value yields
6/80. These are declared heuristic margins,
not measured safety clearances. Aperture overflow, unsupported pad/body size,
and an exceeded explicit sample budget fail rather than clamp. Changing body
size changes height, aperture and approach distance. Roll pairs are robot
orientations but may share an ideal contact family; candidate count is not
independent contact diversity.

## A trajectory audit

Source: `grasp_a/sequence.py`, `simulation.py`, `sequence_contracts.py` at the
hashes frozen in `motion.MIRROR_SOURCE_HASHES`. A change to these files disables
the command mirror until reviewed. The manifest records those hashes too.

| Task | Commands after contact closes | Orientation |
| --- | --- | --- |
| transport_place | lift .14 m / 1 s; transport to B / 1.3 s; lower center to .642 m / 1.3 s; release .5 s; retreat 1 s; settle and terminal | upright |
| handover_transfer | lift 1 s; transport 1.3 s; receiver approach up to 2 s; receiver close .75 s; donor release .5 s; retreat 1 s; receiver lift; settle and terminal | upright transfer |
| pour_water | lift 1 s; transport 1.3 s; tilt 2 s; dwell configured 2 s; return 2 s; settle and terminal | WORLD X, default -110 degrees; carry Y offset sign(angle)*.045 m |
| pour_prep | lift, clearance, rotate, task_motion, each 200/240 s; hold 2 s | WORLD Y +35 degrees; intermediate clearance X -.10 m, Z +.18 m |
| handover_prep | lift and task_motion each 200/240 s; hold 2 s | WORLD Z -90 degrees; no physical receiver or release |

Sequence pregrasp defaults 1 s, approach .7 s, close .75 s. Prep pregrasp is
180/240 s, grasp 120/240 s, close 180/240 s. These unloaded motions are audited
here but omitted from donor load samples. After release the donor no longer
carries the object; no fictitious donor load is assigned to receiver lift or
retreat. The handover geometry evaluator separately checks opened donor pads
and the withdrawal direction.

A runs at 240 Hz. Cartesian targets are subdivided at 15 mm / 5 degrees and
interpolated in joint space with motors. Sequence translations >.20 m are
segmented; endpoint IK refinement and up to 2 s arrival waits add variable time.
Arrival thresholds .02 m / 10 degrees and terminal thresholds .02 m/s /
5 degrees/s are **not commanded speeds**. Receiver rail maxVelocity=.10 m/s is
a configured motor limit, not an observed achieved speed; rails stop on encoder
criteria. No donor Cartesian acceleration limit/profile is published.

`command_context` mirrors nominal object endpoints/durations and estimates
rest-to-rest motion with f(u)=(1-cos(pi*u))/2. Hence v=D*pi*sin(pi*u)/(2T),
a=D*pi^2*cos(pi*u)/(2T^2), with analogous angular derivatives. Samples cover
stage endpoints and interior orientations (default 17 per stage). This profile
is an explicit approximation: A does not execute this time law. It is not a
certified acceleration bound; arrival corrections and waypoint transients can
exceed it. Time is nominal commanded time excluding variable waits. A speed
scale is a counterfactual sensitivity input and never changes A execution.

A's runtime FK/encoders and primitive feedback are available during execution.
Post-execution prep logs contain sampled object/tool poses; sequence evidence
also contains source speed norms and pose. They are terminal/evaluation data,
not candidate-independent forecasts. M2 does not consume them before selection.
External MotionSample inputs distinguish `external_predicted` and
`external_observed`; observed data are suitable for retrospective diagnosis,
not privileged inputs to a prospective shared-controller comparison.

## Physics assumptions and formulas

All positions are metres; times seconds; masses kg; inertia kg m^2; forces N;
moments N m; angles rad; angular speed rad/s. Samples use WORLD acceleration,
angular velocity and angular acceleration, and object roll/pitch/yaw in WORLD.
Default mirrored samples assume initial upright orientation; calibrated or
external nonzero initial orientation requires explicit externally constructed
samples. Initial nominal position is an explicit input (default A prior).

`Physics` defaults: mu=.6, available normal force per finger=15 N, torsion
radius=8 mm, pad half-height=8 mm, geometric clearance scale=20 mm, g=9.81 m/s^2.
These are declared proxy assumptions. A configures finger motor force=35 and
friction parameters 2.0/1.2; neither a motor limit nor those two material
parameters establish a measured normal force or calibrated effective mu.
Sensitivity sweeps expose these assumptions rather than fit them to outcomes.

Let R be cup orientation, c the local COM, p the contact midpoint, a the
estimated origin acceleration, omega/alpha world angular rates, and g the
gravity vector. For each dry/liquid lump k:

```
a_k = a + alpha cross (R c_k) + omega cross (omega cross R c_k)
F = sum_k m_k (a_k - g)
tau = sum_k [R(c_k-p) cross m_k(a_k-g)] + I_world alpha
      + omega cross (I_world omega)
```

Dry I is the URDF tensor rotated into WORLD. Liquid is a point mass at an
explicit fixed local COM (default origin, default mass from SequenceConfig,
only pour_water). It contributes force/moment through the sum. Mass is held
constant across tilt, dwell and return: no draining, slosh, free-surface motion,
CFD, or hydrodynamic validity is claimed. The off-origin liquid lump's angular
acceleration term is a rigid-entrainment surrogate. pour_prep uses a dry solid
cup with no invented fluid.

Let n be the closing axis, r the body radius and r_t the assumed torsion radius:

```
F_t = norm(F - dot(F,n)n)
tau_t = norm(tau - dot(tau,n)n)
N_required_per_finger = abs(dot(F,n))/2 + F_t/(2 mu)
                       + tau_t/(2 mu r) + abs(dot(tau,n))/(2 mu r_t)
load_ratio = N_required_per_finger / assumed_available_normal_force
```

This conservative additive demand surrogate combines separate friction and
moment budgets. It is not a wrench-cone solver, calibrated contact mechanics,
or a physical probability. Inertia and COM must describe the declared model;
COM sensitivity modifications do not automatically re-identify inertia.

`static_v2`: upright, zero acceleration/rotation, dry mass; compute load_ratio
and pad edge penalty h_pad/(contact_edge_margin+h_pad). Risk is their sum.
COM offset is reported explicitly; only mechanically relevant components
contribute torque (a vertical COM displacement under upright gravity alone
must not fabricate a moment).

```text
R_static = load_ratio + h_pad/(contact_edge_margin+h_pad)
S_static_v2 = 1/(1+R_static)
```

Here `h_pad` and edge margin are metres; the ratio and score are dimensionless.
The reference is the assumed 8 mm finger half-height. It applies only to
supported horizontal body contacts and the stated rigid cup/finger proxy.

`M2-Transport`: maximum load_ratio over all loaded motion stages, plus pad edge
penalty. Rows retain stage, time, force, torque and normalized demand; max is
sampled, not a continuous-time supremum or safety guarantee.

`M2-Handover`: independent receiver-space calculation. Sweep envelope relative
to nominal B: X +/-32 mm, Y 70..240 mm, Z +/-52 mm, from A finger half sizes,
rail approach and open-jaw positions. Donor pads use half sizes 9/4/25 mm,
centers +/- (radius+4 mm) along closing axis; opened centers use max aperture/2
+4 mm. Palm proxy has half sizes 35/50/25 mm and center -65 mm along tool Z.
These are conservative approximate AABBs, not imported Panda collision meshes.
Transform donor and handle envelopes by each transfer/presentation orientation.
Risk sums handle occlusion fraction, donor/receiver swept overlap fraction,
unavailable handle fraction, withdrawal cosine toward receiver, and
L/(gap+L), then adds `grasp_stability_risk = max_load_ratio + edge_penalty`.
This reuses the M1 static edge-margin proxy because handover still requires a
stable donor grasp. Evaluate closed/open states and retain the worst geometry
state. Overlap fractions are clamped to [0,1] to
avoid double-counting overlapping proxies. For handover_prep the receiver is
explicitly hypothetical, since A only performs a presentation rotation.

`M2-Pouring`: independent angle-sampled load maximum plus the same
`grasp_stability_risk = max_load_ratio + edge_penalty`, rim obstruction, handle
obstruction and L/(rim_gap+L). Rim proxy is the square opening envelope
at top +/-4 mm; this conservative disk approximation penalizes interference
with opening space. Orientation-dependent torque is sampled throughout tilt
and return; local rim/handle clearance is invariant under the rigid donor/cup
rotation. Its validity excludes contact changes, spill prediction and slosh.

All risk terms are dimensionless: load utilization uses an assumed per-finger
force capacity; edge/clearance ratios use declared millimetre scales; overlap
terms are volume fractions; withdrawal is a clipped directional cosine. Within
each task, listed components currently have unit coefficient. This is a
transparent engineering aggregation, not a paper-calibrated weighting or
learned probability. The normalized force margin is reported as
`1 - load_ratio` (and in N per finger) for diagnosis; it is not counted as a
second score term. Scores use `score=1/(1+risk)` in (0,1], monotone and
dimensionless. Larger is better. New ranking keys round scores to 12 decimal places before stable ID ties,
so symmetric rolls cannot obtain spurious rank differences from trigonometric
roundoff. Raw scores are retained for audit. The legacy selector remains unchanged.

M2's current scoring version is `task_trajectory_risk_v2`. It adds the already
declared static contact-edge penalty to Handover and Pouring grasp-stability
terms, matching Transport's treatment. Earlier `task_trajectory_risk_v1`
reports retain their old scores and rankings unchanged.

## Independent API and reproducible experiments

```python
from grasp_b.models import read_model
from grasp_b.adaptive import generate_adaptive
from grasp_b.motion import command_context
from grasp_b.risk import rank_feasible

model = read_model('hollow', panda_urdf)
candidates = generate_adaptive(model)
filtered = get_sequence_candidates(task, seed, candidates)
context = command_context(task)  # explicitly estimated motion
ranking = rank_feasible(candidates, filtered['feasible_candidates'], model,
                        context=context)
```

Full explicit inputs are available to this B experimental API. Five-field
selectors and C are unchanged; M2 is not registered in that five-field C selector. The
caller must retain A filter provenance; the ranker verifies metadata matches
full raw candidates but cannot authenticate an arbitrary caller-supplied filter.

```bash
PY=/home/zhyjh/miniconda3/envs/grasp-a/bin/python
$PY -m grasp_b.benchmark --execute --out results/local/my_new_b_comparison
$PY -m grasp_b.sensitivity --out results/local/my_new_sensitivity.json
$PY -m unittest discover -s tests -p 'test_b*.py' -v
```

Outputs are fresh directories/files. CLI artifacts are confined to ignored
results/local. Comparison reports contain raw counts, A checks/rates, geometry
score distributions, pairwise ranking concordance/top-1 agreement, task risks,
conditional execution success, end-to-end success, generation/filter/ranking/
execution runtimes, configuration and hashes. Unexecuted success metrics are
null. Empty feasible seeds remain in end-to-end denominator. Identical selected
IDs within a task/seed reuse one deterministic physical episode, explicitly
flagged; they are not independent replicate evidence. Runtime zero for a reused
episode means no additional simulation, not zero physical simulation cost.
A numerical/execution exception aborts rather than silently count as success.
Sensitivity output includes mass, COM, friction, speed and stage maxima, plus
contrasts with physically identical or changed declared inputs. No held-out
claim is made for these development seeds or counterfactual predictions.

## Missing A data and minimal future interface

A currently lacks a read-only per-candidate planned object/TCP trajectory with
timestamps, world velocity/acceleration vectors and angular derivatives;
controller arrival/refinement motion is not represented by nominal duration
alone. It also lacks calibrated contact force capacity, pad/contact torsion
parameters and liquid COM evolution forecasts. The minimal future optional
interface would return timestamped nominal object poses and time law (or
velocity/acceleration vectors), frame/provenance, stage and controller/model
hashes for a candidate and ObservationConfig, **without executing outcomes**.
This work makes no A/C change to add that interface. M2 accepts explicit samples
so that such a future adapter can be introduced without changing Candidate.

## Formal baseline meanings and P0/P1 boundary

- **M0 — Random Feasible Grasp Selection:** uniformly permute/select only A's
  initial-feasible five-field records with a local seeded RNG. It does not
  compute or consume geometry scores, task-specific risks, execution outcomes,
  or global RNG state. In paired trials, the benchmark also uses the seed to
  randomize A's scene; these are separate effects recorded in the report.
- **M1 — Task-independent Geometry-based Selection:** rank the exact same A
  feasible subset without task labels or motion context. The unchanged
  `experimental_geometry_weighted_v0.1` is the Legacy baseline;
  `static_load_margin_v2` is an internal refinement/ablation, not another
  numbered method.
- **M2 — Handcrafted Task-aware Grasp Selection:** rank that same subset using
  explicit task context and task-specific Transport, Handover or Pouring
  evaluators. It is a physics-informed rule heuristic, not a learned model,
  success probability, or complete task-feasibility predictor.

The implemented protocol is **P0**: A initial-grasp feasibility → each method
ranks the shared accepted set → selected grasps are executed and outcomes are
reported afterward. The seed-0 Sequence Execution Oracle is retrospective
evaluation only; its full-sequence results do not flow back into any method.
**P1** would add an A-owned sequence-level feasibility/planning probe before
ranking and give every method the exact same resulting set. P1 is a future
integration protocol, not implemented here. It must not be an M2-only IK
penalty, because that would confound task-aware ranking with extra feasibility
information.

## Literature alignment and claims boundary

| Work | Research question and metric/assumptions | Required inputs | Fit to this repository |
| --- | --- | --- | --- |
| Ferrari & Canny (1992), [Planning Optimal Grasps](https://people.eecs.berkeley.edu/~jfc/papers/92/FCicra92.pdf) | Quantifies worst-direction wrench resistance under a hard-finger contact model and friction cones; quality derives from the grasp wrench set (including a ball/radius criterion), assuming known contacts and force constraints. | Contact locations/normals, friction cones, wrench scaling, force limits, convex hull of achievable wrenches. | Useful theory for contact stability and wrench reasoning. Current ideal-cylinder contacts and Coulomb-like load proxy do not construct a grasp wrench space or solve force closure; do not call them Ferrari–Canny metric or force-closure test. |
| Mahler et al. (2017), [Dex-Net 2.0 proceedings](https://roboticsproceedings.org/rss13/p58.html), [paper](https://arxiv.org/abs/1703.09312), [project/code](https://berkeleyautomation.github.io/dex-net/) | Uses analytic robust grasp metrics based on quasi-static GWS under sampled uncertainty to supervise GQ-CNN; antipodal candidates are evaluated under uncertainty. | Object mesh, depth/point-cloud observations, candidate contacts, friction and pose uncertainty distributions, wrench-space analysis, synthetic labels for learned prediction. | Adopt the protocol ideas: state physical assumptions, expose uncertainty parameters, compare candidates on identical sets, and report robustness as a proxy unless calibrated. This repository lacks its uncertainty model, GWS implementation, training data and trained network. |
| Lin & Sun (2015), [Task-Based Grasp Quality Measures for Grasp Synthesis](https://digitalcommons.usf.edu/esb_facpub/79/) | Introduces task-wrench coverage and manipulator efficiency: assess whether a grasp supplies task interaction wrenches and enables functional motion with lower manipulator effort. | Task wrench set/measurements, grasp wrench set/contact mechanics, defined functional motion, robot kinematics and effort over the motion. | Direct motivation for M2's task-separated structure and explicit operational constraints. Current transport loads and handover/pouring clearances are only proxies; there is no TWS/GWS coverage calculation or whole-arm efficiency optimization. |
| Lin & Sun (2015), [Grasp Planning to Maximize Task Coverage](https://digitalcommons.usf.edu/esb_facpub/56/), [paper](https://rpal.cse.usf.edu/publications/ijrr2015b.pdf) | Models task disturbances with a non-parametric distribution from demonstrations and scores how much of that distribution a grasp can resist, rather than assuming uniform disturbance in all wrench directions. | Demonstration-derived task disturbance samples/distribution plus grasp wrench resistance. | Supports task-specific rather than isotropic demands. We have commanded endpoints and estimated motion, but no demonstrated disturbance distribution; M2 risk is not task-wrench coverage. |
| Sundermeyer et al. (2021), [Contact-GraspNet paper](https://arxiv.org/abs/2103.14127), [authors' code](https://github.com/NVlabs/contact_graspnet) | Predicts distributions of 6-DoF grasp hypotheses from scene point clouds, representing observed points as potential grasp contacts for cluttered unknown objects. | Partial scene point cloud, trained network/checkpoints, candidate contacts and a cluttered-scene inference pipeline. | Relevant to future candidate coverage/diversity evaluation, not a scoring replacement. This known-model cylinder sampler lacks camera point clouds and a learned proposal pipeline, so coverage accounting is the immediately usable idea. |

These works solve different problems and their names are not interchangeable.
M1's `COM distance`, `alignment`, `symmetry`, `contact margin` and
`load_ratio` are named proxies with idealized inputs. M2's rules are not a TWS
implementation. The formulas in this document are dimensionally explicit
engineering heuristics, not reproductions of the cited metrics.

The relevant metric forms clarify the input gap. For Ferrari–Canny, a contact
force contributes wrench
`w_i = [f_i; (p_i-o) × f_i]`, with `f_i` constrained to the contact friction
cone; the grasp wrench set is the convex hull of achievable contact wrenches.
The epsilon quality is the radius of the largest origin-centered ball contained
in that set, `Q_ε = sup{ε : B_ε(0) ⊆ GWS}` (after choosing a force/torque
normalization). This requires contact mechanics and a wrench-space solve that
the current code does not perform.

Dex-Net's robust analytic target evaluates epsilon quality over sampled pose
and friction uncertainty, conceptually `Q_rob(g) = E_{δ~P}[Q_ε(g;δ)]`; its
learned GQ-CNN predicts a robust grasp label/score from depth input. Lin & Sun's
task-wrench coverage is task-distribution weighted: a conceptual empirical form
is `Q_task(g) = Σ_k p_k 1[w_k ∈ GWS(g)]`, where task disturbances `w_k` and
weights `p_k` come from task data; their manipulator-efficiency measure also
uses robot motion/effort over the functional trajectory. The precise
implementations depend on the papers' contact and task models; these compact
forms state the information dependency, not a claim that Part B implements
those metrics. Contact-GraspNet instead estimates a 6-DoF grasp distribution
conditioned on a scene point cloud, `p(g | point cloud)`, and is a proposal
generator rather than a force-quality equation.

## M1 feature audit on the seed-0 sequence candidate set

The six candidates accepted by A for each sequence task share the same profile,
observation and feasible set. Their ideal-cylinder alignment and symmetry are
both 1.0 for all candidates. Static v2 upright `load_ratio` is 0.1526 for all
six. Grasp heights are 0, 11.1 mm (three yaw/roll variants), and 25.9 mm (two
variants); the ideal contact-midpoint to URDF COM distances are the same
respective values. Legacy and Static v2 therefore have identical rankings,
primarily separated by height-correlated terms on this candidate set. This is
a set-specific result, not a claim that these features are constant for all
poses.

Static v2's load ratio is computed from the candidate closing axis, ideal body
contact midpoint, URDF COM/inertia, assumed friction and finger force capacity.
It is pose-dependent in supported geometries, but for these centered upright
horizontal grasps gravity is perpendicular to the closing axis and the
vertical COM/contact displacement creates no gravity moment. The static load
demand is consequently candidate-invariant. Candidate height instead changes
the pad-edge margin. That margin is vertical body-bound clearance minus an
assumed 8 mm pad half-height: a one-dimensional fingertip placement proxy, not
an exact contact patch or collision computation. A's composite collision
checks remain authoritative.

This exposes correlation in the current M1 features: Legacy's COM-distance
term and Static v2's edge-margin term both largely encode vertical distance
from the same centered body region. Static v2 includes mass, friction and
contact-force assumptions, but those are common across candidates here and
change score level more than within-set order. No formula or historical result
was changed in this audit. A future version should add independently validated
contact information (for example a measured pad/contact patch model or
uncertainty-aware wrench analysis) before claiming extra discriminative power.

## Part B code and test responsibilities

`risk.py` remains the public scoring entry point because task evaluators share
force/moment computation, proxy envelope operations, validation, diagnostics
and ranking. The strategy boundary is explicit in `transport_risk`,
`handover_risk` and `pouring_risk`; `task_risk` dispatches using the explicit
`TaskContext.task_id`. Splitting these functions into extra modules would
duplicate shared assumptions without improving the current boundary. Tests
are now separated by behavior in `test_b_m1_static.py`,
`test_b_m2_transport.py`, `test_b_m2_handover.py`, `test_b_m2_pouring.py` and
`test_b_m2_common.py`. Asset parsing and motion context have their own test
files; A/B and benchmark contracts live in `test_b_integration.py`. Existing
`test_b_m0_random.py` and `test_b_m1_geometry.py` keep their original M0 and
Legacy M1 semantics.
