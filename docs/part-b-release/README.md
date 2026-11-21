# Part B source integration — 21 November 2026

This release adds stable contact refinement and joint relaxed selection to strict
GWS methods already on main. It does not replace `static_load_margin_v2` or
`task_trajectory_risk_v2`, modify Part A/C, alter experiment records, or edit the
existing final report.

## Source and compatibility

- Source origin: dirty `feature/part-b-contact-refinement` at
  `3b95a757c3f281f2dbc2809304ab9751a19ed73e`. HEAD alone did not contain new files;
  `source_manifest.json` records original and released core bytes separately.
- Integration base: `5c08cdd721ce211819ed54dc91938693f9d75679`, after report-only
  PR #14. Its newer report is preserved.
- Original workspace remains intact: no checkout, stash, reset, clean or edits.
  Complete pre-operation inventory and tracked patch remain locally under
  `/tmp/cs5478-final-integration-20261121/`.
- Strict/relaxed solvers, constraints, contacts, dynamics, ranking and execution
  adapters are byte-identical to the original working source. The separate exact
  nominal-source profile accepts the reviewed upstream future-annotations import
  in `grasp_a/control.py`; no physical execution identity is relabeled.
- Packaging changes: unchanged validation helpers extracted from final historical
  drivers; synthetic protocol fixture; static test geometry generated on demand;
  explicit skips for absent optional source-bound historical fixtures.

## Minimal release scope

Included: strict GWS dependencies, ContactSpec/static contact evidence, joint
relaxed M1/M2, independent numerical checks, near-tie classification, versioned
comparison/protocol helpers, frozen adapters, tests and small fixtures.
`requirements-b.txt` records SciPy separately.

Excluded and preserved locally:

- All ignored `results/local/`, raw LP/cached records, physical/PBF outputs,
  videos/images, logs, environments and credentials. Nothing ignored is force-added.
- Separate `feature/part-b-v3-candidates` B3/B3H generators and pilots.
- Superseded per-scenario `relaxed_experiment.py`, `relaxed_analysis.py`,
  `relaxed_audit.py` and their tests.
- Historical reconstruction drivers `frozen_audit.py`, `geometry_audit.py`,
  `bullet_audit.py`, `collision_region_audit.py`, `relaxed_evaluation.py`,
  `static_evaluation.py`, `contact_ablation.py`, `numerical_ranking_audit.py`, and
  tests requiring their full reconstructed studies. Only unchanged portable
  identity/nesting checks move to `contact_refinement/validation.py`.
- The prior comprehensive audit, unrelated C changes, report edits, experiment
  summaries and large evidence packages.

## Reproducible from committed source

Small strict/relaxed LP mathematics, independent per-scenario oracle agreement,
shared capacities, tolerance/status rejection, near-tie ranking, static contacts,
identity checks, mock batch behavior and offline A/C contracts can be checked with
[scripts/validate_part_b_release.py](../../scripts/validate_part_b_release.py).
Use the commands in the [package README](../../grasp_b/contact_refinement/README.md).

The full Task09D2 numerical study, Task09F1 static comparison, Task09F3A selection
study and Task09F3C physical canary are **not** reproduced by this release suite.
Their frozen inputs, original source-bound registries and physical evidence remain
local. Historical results came from a dirty working tree and are not fresh tests
of this commit. Numeric coverage, selection coverage, strict equilibrium and task
success remain different quantities. C still uses its archived surrogate methods;
no latest-B/LLM comparison is implied.

## Fresh integration verification

The guarded pre-release run selected 807 tests: **757 passed, 50 skipped, zero
failures/errors**. Three real rollout/video tests were deferred. Guard counters
for physical stepping, sequence episodes, preparation episodes and network were
all zero. Syntax checks covered 171 files and imports covered 45 B modules.
These are actual integration checks, not historical result counts.

Skips cover optional Task07/Task09 archives, their frozen adapter cases and the
missing-SciPy environment branch (SciPy is installed here). They do not count as
reproducing historical studies. The runner writes per-test reasons to its output
JSON/log, outside tracked experiment evidence.

Initial checks found one test's dependency on an unshipped geometry archive, two
missing-dotenv errors, and three guarded real-rollout tests. Static fixtures removed
the geometry dependency; the already-declared `python-dotenv` package was installed
only in an isolated `/tmp` test path; real rollouts were deferred. No algorithm or
A/C source was changed to pass these checks.

Core source fingerprints and diffs verify the intended implementation and unchanged
A/C/config/report/results relative to the base. Original local evidence is checked
separately. No new simulation benchmark or paid inference was run.
