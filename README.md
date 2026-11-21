# Task-Aware Mug Grasp Selection

CS5478 final project, Team 18 (Xie Lin, Zhang Yijie, Yao Xiaolu).

We study how a robot should choose a mug grasp for the task that follows the
pickup: **transport and placement**, **cooperative handover** to a receiving
gripper, and **pouring** simulated water into a cup. A Franka Panda in PyBullet
executes every selected grasp with a shared controller, and an independent
evaluator scores the complete task. We compare analytical baselines,
language-model selectors and a rule-only control on the same feasible
candidates.

- **Report:** [`report/final_report.pdf`](report/final_report.pdf)
- **Presentation:** [`presentation/CS5478_Task_Aware_Grasp_Selection_Presentation_v2.pptx`](presentation/CS5478_Task_Aware_Grasp_Selection_Presentation_v2.pptx)
- **Repository:** <https://github.com/Xl440102/cs5478-task-aware-grasp-selection>

## Repository layout

| Path | Contents |
|---|---|
| `grasp_a/` | Part A: PyBullet scenes, feasibility filter, staged controllers, terminal evaluator, PBF fluid (`python -m grasp_a`) |
| `grasp_b/` | Part B: grasp candidate generation, analytical baselines (M0–M2), wrench-space ranking (`wrench_v2/`, `contact_refinement/`) |
| `grasp_c/` | Part C: language-model selectors and prompts, oracle-table evaluation, M4–M6 baselines, statistics |
| `results/oracle_20261103/` | Archived execution tables used in the report (R3, R3 PBF, R4, R4 PBF) |
| `results/c_evaluation_multimodel_20261109/` | The 720 recorded language-model trials, local PBF reruns and statistics |
| `report/` | LaTeX source, figures, generated tables and `prepare_results.py` |
| `presentation/` | Slides and speaker script |
| `tests/` | Unit and integration tests |
| `configs/`, `assets/`, `scripts/`, `examples/` | Run configurations, URDF models, helper scripts, examples |
| `docs/` | Detailed interfaces, task definitions and experiment logs (mostly Chinese); the previous README is [`docs/DEVELOPMENT_NOTES.md`](docs/DEVELOPMENT_NOTES.md) |

## Setup

Python 3.11 is required. With pip (Linux or macOS):

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-b.txt -r requirements-c.txt pytest
```

With conda (also works on Windows):

```bash
conda env create -f environment.yml
conda activate grasp-a
```

All commands below run from the repository root.

## 1. Reproduce the report's tables and figures (about 5 seconds, no simulation)

```bash
python report/prepare_results.py
```

This reads the archived execution tables and recorded model trials and does
not call any simulator or language model. It checks every per-seed count and
selected episode, recomputes the paired tests and Holm correction, evaluates
the rule-only control M6, and regenerates `report/generated/*.tex`,
`report/generated/evidence_audit.json` and `report/figures/family_task.pdf`.
The regenerated files may differ from the committed ones only in JSON key
order and figure metadata. [`report/README.md`](report/README.md) maps each
reported number to its source directory. To rebuild the PDF, run
`make -C report` (needs Tectonic, or `pdflatex` with the `cm-super` fonts).

## 2. Run one simulated task (about 1 minute)

```bash
python -m grasp_a sequence --task transport_place --seed 0 --id G02 --visualize --media-dir results/local/demo --out results/local/demo/transport.json
```

The JSON reports `success`, the stage outcomes and the paths of an MP4 video
and two images. Replace `transport_place` with `handover_transfer`, or with
`pour_water` for the particle-fluid task, which takes several minutes on a CPU.
Candidates `G01` and `G02` come from `candidates.example.json`; add
`--no-landmarks` for a video without the A/B overlay. Personal runs belong in
`results/local/`, which is ignored by git.

## 3. Run the tests

```bash
python -m pytest -q tests
```

The suite takes about 2.5 minutes. In a fresh environment set up as above,
821 tests pass, 48 are skipped (optional local archives or deferred real
rollouts) and 4 fail. The four failures are Part B static contact-geometry
tests (`test_b_bullet_alignment.py`, `test_b_collision_regions.py`,
`test_b_contact_geometry_validation.py`) that look up candidate IDs the
committed generator does not produce; they do not affect any result in the
report.

## Reproducing the full benchmark

The archived tables come from executing every feasible candidate once per task
and scene seed, which takes hours (real-fluid pouring is about 150 s per
episode). The commands are listed in
[`docs/EXPERIMENT_20261103_CANDIDATES_SEEDS.md`](docs/EXPERIMENT_20261103_CANDIDATES_SEEDS.md).
New language-model runs need provider credentials; see
[`docs/PART_C.md`](docs/PART_C.md). They are not needed to reproduce the
report, because every recorded response is archived under `results/`.

## Scope

Everything runs in simulation with one mug model and synthetic pose noise; no
perception or real-robot experiments are included. The limitations are
discussed in Section 7 of the report.
