# Team 18 presentation guide

The reviewed PowerPoint contains the full English speaker notes, an embedded 15-second demonstration, 10 main slides, and two hidden Q&A backups.

- Presentation: [CS5478_Task_Aware_Grasp_Selection_Presentation_v2.pptx](CS5478_Task_Aware_Grasp_Selection_Presentation_v2.pptx) (adds the rule-only M6 baseline; the previous version is kept as [CS5478_Task_Aware_Grasp_Selection_Presentation.pptx](CS5478_Task_Aware_Grasp_Selection_Presentation.pptx))
- Planned duration: 9:30 including the video, with 0:30 reserve.
- Slide 3: click the video once. The three full task recordings are each accelerated to five seconds. The G02 seed-0 demo is separate from the benchmark.

## Speaker allocation

| Speaker | Project role | Slides | Time | Window |
|---|---|---|---|---|
| Xie Lin | Part A | 1–3 | 2:20 | 0:00–2:20 |
| Zhang Yijie | Part B, shared pipeline and Part C protocol introduction | 4–7 | 4:05 | 2:20–6:25 |
| Yao Xiaolu | Part C results and conclusions | 8–10 | 3:05 | 6:25–9:30 |

The project roles remain A/B/C. B covers shared material to allow a longer speaking contribution without expanding Part B's slide count.

## Accuracy corrections

- Primary totals consistently combine archived transport, handover, and PBF-table outcomes. Separate local PBF reruns are supplementary.
- The recorded 116-candidate pool contains 28 handle, 8 rim, 16 top, and 64 side candidates.
- PBF spill threshold is at most 5%.
- Raw trial records confirm 712 initially valid outputs, 8 repair attempts, and 2 fallbacks.
- The five-field boundary applies to LLM candidate input. Several analytical baselines also use poses and model parameters, while M4 uses training outcomes.
- Seeds 10–29 are excluded from M4 training but are not a blind test set. A1 omits task text but retains the task-filtered feasible pool.
- v2 supplies explicit grasp preferences. The prompt versions change multiple instructions together and do not independently establish 3D reasoning.
- Reported Holm p-values follow the current 170-comparison summary. PBF improvements over random are not significant after correction.
- M2 includes access proxies. M5 is a fixed part surrogate, not a full paper reproduction. Wrench-space work is exploratory and separate from the 18-method benchmark.
- M6 (`grasp_c/rules.py`) applies the v2 grasp preferences without a language model. It scores 54/60, equal to GPT v2, picks the same grasp family as both v2 models in 60/60 trials and the identical candidate in 27/60 (all 20 pours). Its rules were written after seeds 10–29 were examined.

## Speaker notes

Only the spoken-script paragraphs count toward the timed talk. Sources and Q&A details are not read aloud.

### Slide 1

**Speaker:** A · Xie Lin
**Window:** 0:00–0:15

Good afternoon. We are Team 18: Xie Lin, Zhang Yijie, and Yao Xiaolu. Our project studies how a robot should choose a mug grasp for the task that comes after picking it up.

**Sources:**

- `Project implementation: grasp_a/, grasp_b/, grasp_c/`

### Slide 2

**Speaker:** A · Xie Lin
**Window:** 0:15–1:00

A secure pickup is only the beginning. To place a mug, the fingers must open and retreat near the table. To hand it over, the donor must leave room for a receiving gripper to reach the handle. To pour, the arm must carry the mug through a 110-degree tilt. These constraints can favor different grasps on the same object. We therefore ask whether task information improves selection among candidates that already pass a common feasibility filter. We compare geometric and rule-based methods with language-model selectors, and measure complete task success rather than pickup alone.

**Sources:**

- `grasp_a/sequence_contracts.py`
- `grasp_a/sequence_evaluation.py`
- `grasp_c/tasks.py`

### Slide 3

**Speaker:** A · Xie Lin
**Window:** 1:00–2:20

Part A implements a seven-joint Panda arm, a hollow mug, a receiver gripper, and a receiving cup in PyBullet. The filter rehearses the execution waypoints with bounded, multiple-start inverse kinematics. We also restrict finger contact during approach to the final waypoint. This reduces the mismatch between planning and execution, but passing the filter still does not guarantee dynamic success. The benchmark uses noisy pose estimates generated from the simulated object pose, rather than a perception system. For pouring, we use 256 simulated fluid particles and require at least 90 percent transfer with at most five percent spill, together with stability checks. Here is a 15-second recording of the three task sequences. These seed-zero demonstrations use an earlier candidate set and illustrate the controllers, separately from the benchmark results. Zhang Yijie will now explain how the modules fit together.

**Cue:** Play the embedded 15-second montage once after ‘three task sequences’. The montage accelerates each full source clip to five seconds. If playback fails, use the three stills as the demonstration and continue. Total slide budget includes 15 seconds of playback.

**Sources:**

- `grasp_a/control.py`
- `grasp_a/sequence.py`
- `grasp_a/sequence_contracts.py`
- `results/oracle_20261103/r3_v3p_wide_strict_v2/meta.json`
- `results/c_evaluation_multimodel_20261109/manifest.json`
- `results/sequence_v1_20261017/demo_manifest.json`
- `results/sequence_v1_20261017/videos/with_landmarks/*_seed0_G02_ab.mp4`

### Slide 4

**Speaker:** B · Zhang Yijie
**Window:** 2:20–3:15

The pipeline connects all three parts. Part B generates mug-local grasps. Part A filters them for each task and scene seed. A selector returns a ranking, and we evaluate its first choice. All methods choose from the same feasible IDs within each task and seed. Their information access differs: the LLM sees five public candidate fields, while some analytical baselines also use candidate poses and physical model parameters. M4 additionally uses outcomes from training seeds zero to nine. Evaluation outcomes are kept outside the LLM input. The main comparison uses archived execution tables, so it is a retrospective benchmark. We separately rerun selected pouring grasps in particle simulation as a reproducibility check.

**Sources:**

- `grasp_c/oracle.py: build_selectors`
- `grasp_c/prompts.py: PUBLIC_FIELDS, public_snapshot`
- `grasp_c/table_evaluation.py`
- `results/c_evaluation_multimodel_20261109/report.md`

### Slide 5

**Speaker:** B · Zhang Yijie
**Window:** 3:15–4:10

Part B samples 116 grasps across the handle, rim, top of the body, and side of the body. We describe them with semantic tags for approach direction, closing direction, and handle clearance. Executing the feasible pool over 30 scene seeds shows a clear task dependence. Rim grasps succeed in 86 percent of handovers, compared with 24 percent for side grasps. In particle pouring, rim and top grasps achieve about 73 and 67 percent. No handle grasp passes the pouring filter in this setup, so that entry is unavailable rather than a zero-percent execution rate. These are pooled candidate outcomes, not the success rate of a selector or independent samples for a significance test.

**Q&A detail:** Pooled success / executions: Place rim 182/195, handle 339/382, top 280/347, side 171/350. Handover rim 163/189, handle 56/118, top 219/347, side 84/347. PBF pour rim 45/62, top 142/211, side 89/185. Handle: zero feasible pouring candidates, no execution rate. Counts pool candidates within shared scenes and are not independent Bernoulli samples.

**Sources:**

- `grasp_b/multifamily.py`
- `grasp_b/semantics.py`
- `results/oracle_20261103/r3_v3p_wide_strict_v2/b_inputs/families.internal.json`
- `results/oracle_20261103/r3_v3p_wide_strict_v2/episodes/`
- `results/oracle_20261103/r3_pbf_v3p_wide_strict_v2/episodes/`

### Slide 6

**Speaker:** B · Zhang Yijie
**Window:** 4:10–5:25

We evaluate five non-LLM baselines. M0 samples randomly. M1 scores static body grasp quality. Its formula does not cover rim or handle grasps, which receive placeholder zeros. M2 adds task-dependent risk terms, including estimated motion loads and access penalties. These are engineering proxies, not predicted success probabilities. M4 chooses using family success statistics from training seeds zero to nine. M5 is our fixed part-preference surrogate, followed by a force-and-distance score. It is not a reproduction of an external paper's full system. The table shows the main trade-off: M5 gets all 20 transport trials right, but only five handovers. M4 improves handover to 17, but gets 12 particle-pouring cases. M2 includes handover access terms, yet its side-grasp choices still succeed in only 11 cases. This motivates more explicit descriptions of the receiver's approach. Our separate six-dimensional wrench-space extension remains exploratory and is described in the backup slides.

**Q&A detail:** M5's configured preferred part is handle for transport and pouring, body for handover. Pouring has no feasible handle candidates, so its observed choices are body grasps. M1/M2 rank supported body grasps first and append unsupported families as an ID-sorted tail. M2 does include access/sweep proxies: do not say it has no receiver model.

**Sources:**

- `grasp_b/risk.py`
- `grasp_b/motion.py`
- `grasp_c/oracle.py: PAPER_PART_TABLE, build_selectors, family_prior`
- `results/c_evaluation_multimodel_20261109/method_results.csv`
- `grasp_b/wrench_v2/EXPERIMENTAL_STATUS.md`

### Slide 7

**Speaker:** B · Zhang Yijie
**Window:** 5:25–6:25

For Part C, we test two recorded model configurations, three prompt versions, and two input variants. M3 receives task text. A1 omits that text while retaining the same feasible candidate list and version-specific system prompt. That gives 12 configurations and 720 selection trials. A1 is not completely free of task information, because the feasible pool itself depends on the task. We use seeds ten to twenty-nine, excluded from M4 training but already examined during development. They are not a blind test set. Each response must rank every feasible ID exactly once. The logs contain 712 initially valid responses, eight repair attempts, and two deterministic fallbacks. Thus 720 valid final selections does not mean 720 correct first responses. Yao Xiaolu will explain what the prompt comparison actually tells us.

**Q&A detail:** Model identifiers recorded by the run: gpt-5.6-luna through the Codex adapter (reasoning_effort=low), and gemini-3.8-flash through the Gemini adapter (thinking_level=low). These are run metadata, not an independent verification of provider model identity. One response per method/task/seed, with format repair permitted.

**Sources:**

- `grasp_c/prompts.py: build_messages`
- `grasp_c/selectors.py`
- `results/c_evaluation_multimodel_20261109/summary.json: selection_audit, notes`
- `results/c_evaluation_multimodel_20261109/manifest.json`

### Slide 8

**Speaker:** C · Yao Xiaolu
**Window:** 6:25–7:25

The original prompt treats geometry score as higher-is-better, without explaining the unsupported zero values. Across the 240 original-prompt selections, neither model chooses a rim or handle grasp. This is consistent with a representation problem, although we did not isolate its cause with a score-only ablation. The mechanics prompt clarifies the score and opening tags and describes the motion constraints without an explicit preferred family. Gemini then selects rim grasps and succeeds in all 20 handovers. The explicit-preference prompt goes further: it recommends handle or rim grasps for placement, top-down rim grasps for handover, and high-scoring top grasps for pouring. These versions change several instructions together. As a check, our rule-only baseline M6 applies the v2 preferences without any language model and also gets all 20 handovers, so the v2 gain reflects the supplied preferences rather than independent three-dimensional reasoning.

**Sources:**

- `grasp_c/prompts.py: SYSTEM_PROMPT_V1, SYSTEM_PROMPT_V2_PHYSICS, SYSTEM_PROMPT_V2`
- `grasp_c/tasks.py: SEQUENCE_INTENT_V2, SEQUENCE_INTENT_V2_PHYSICS`
- `results/c_evaluation_multimodel_20261109/report.md`
- `grasp_c/rules.py`
- `results/c_evaluation_multimodel_20261109/summary.json`

### Slide 9

**Speaker:** C · Yao Xiaolu
**Window:** 7:25–8:40

Our main result combines archived transport, handover, and particle-pouring outcomes for the same 20 evaluation seeds. With explicit preferences, GPT succeeds in 54 of 60 cases and Gemini in 55, numerically above the five original baselines. Our rule-only baseline M6 applies the same preferences without a language model and also reaches 54. Separate local particle reruns give 17 of 20 pouring successes, and should not be mixed into these totals. In handover, the task-text variant beats the text-omitted variant for both models after Holm correction. However, the mechanics-prompt comparison of Gemini against its text-omitted variant does not remain significant after that correction. Nor do the particle-pouring comparisons show a significant improvement over random. With only 20 seeds, even 20 successes gives a Wilson interval from about 84 to 100 percent. We therefore report promising task-specific behavior within this benchmark, without a general superiority claim.

**Q&A detail:** Main metric: transport_place + handover_transfer + pour_water_pbf_table, 60 binary outcomes across 20 shared seeds. It is a descriptive sum, not 60 independent scene draws. The local pour_water_pbf series is a distinct evaluation. Holm correction covers all 170 reported comparisons. Handover M3_CODEX_V2 vs A1_CODEX_V2: 14 wins/0 losses, exact p=0.0001220703125, Holm p=0.02001953125. Gemini equivalent: 16/0, exact p=0.000030517578125, Holm p=0.00518798828125. Mechanics Gemini vs text-omitted mechanics Gemini: 11/0, exact p=0.0009765625, Holm p=0.15625. Archived and local PBF v2 versus M0: Holm p=1.0. M6_PROMPT_RULES (grasp_c/rules.py) picks the same grasp family as both v2 models in 60/60 trials and the identical candidate in 27/60 (all 20 pours). Its rules were written after these seeds were examined.

**Sources:**

- `results/c_evaluation_multimodel_20261109/method_results.csv`
- `results/c_evaluation_multimodel_20261109/summary.json: comparisons`
- `results/c_evaluation_multimodel_20261109/report.md`
- `results/oracle_20261103/m6_prompt_rules.md`

### Slide 10

**Speaker:** C · Yao Xiaolu
**Window:** 8:40–9:30

We built an integrated benchmark linking grasp generation, task execution, and validated LLM selection. The central finding is that downstream constraints and the way we describe candidate scores both affect the chosen grasp. The fluid audit also matters: proxy and particle outcomes agree in only 347 of 458 cases, so a geometric proxy cannot replace fluid evaluation. The limitations are one simulated mug, synthetic pose observations, previously examined seeds, and one model sample per setting. Explicit preferences supply substantial domain knowledge: a rule-only baseline using the same preferences matches GPT at 54 of 60. Historical tables also lack complete environment fingerprints. Next, we would expose measured kinematic clearance features, freeze the prompts, and evaluate new objects and unseen seeds before a real-robot study. Thank you.

**Cue:** End by 9:30. Keep the remaining 30 seconds for slide changes, pauses, and handovers. Backup slides are hidden from the regular slide show.

**Sources:**

- `results/c_evaluation_multimodel_20261109/report.md: notes`
- `results/c_evaluation_multimodel_20261109/summary.json`
- `results/oracle_20261103/r3_v3p_wide_strict_v2/episodes/pour_water/`
- `results/oracle_20261103/r3_pbf_v3p_wide_strict_v2/episodes/pour_water/`

### Slide 11

**Speaker:** Q&A backup
**Window:** Outside timed presentation

Complete 18-method count table for questions. M6 is the rule-only baseline; its P-local equals the v2 models' 17 because its pour picks are identical. T and H are archived rigid-body outcomes. P-table is historical PBF lookup and defines the primary total. P-local is a separate particle rerun of each selected grasp. Each task has denominator 20. All physical failures remain failures. Primary totals must use P-table consistently.

**Sources:**

- `results/c_evaluation_multimodel_20261109/method_results.csv`
- `results/c_evaluation_multimodel_20261109/report.md`

### Slide 12

**Speaker:** Q&A backup · Zhang Yijie
**Window:** Outside timed presentation

The wrench_v2 extension solves a linear program over predicted contact forces and compares their achievable six-dimensional wrenches against declared load sets. It is separate from M1_STATIC_V2 and from the 18-method benchmark. Two-point frictional models cannot support some axial moments that four-point body models can represent under their assumptions. Model infeasibility is not physical failure. Contact geometry, friction, and force capacity are not calibrated. The 202 recovered HiGHS Unknown statuses were diagnostic numerical cases, not successful grasp episodes. A further fixture retains a numerical error under the default settings. A bounded scaling-and-tolerance diagnostic resolved that fixture, but is not a global solver policy or validated quality measure.

**Q&A detail:** The original presentation's statement that HiGHS v1.2 was used is misleading: v1.2 names the project's shared_capacity_utilization solver version, not necessarily the installed HiGHS release. The original 222→2 timeout comparison belongs to a later wide_strict_v3 controller experiment and must not be blended with the frozen wide_strict_v2 benchmark.

**Sources:**

- `grasp_b/wrench_v2/EXPERIMENTAL_STATUS.md`
- `grasp_b/wrench_v2/solver.py`
- `grasp_b/wrench_v2/gws.py`
