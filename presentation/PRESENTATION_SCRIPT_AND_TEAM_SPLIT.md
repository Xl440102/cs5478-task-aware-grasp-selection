# CS5478 Final Presentation Guide: 10-Minute Script & 3-Member Division of Labor

- **Presentation File (`.pptx` with embedded Speaker Notes)**: [`CS5478_Task_Aware_Grasp_Selection_Presentation.pptx`](CS5478_Task_Aware_Grasp_Selection_Presentation.pptx)
- **Custom High-Resolution Charts**: [`assets/chart_family_task_oracle.png`](assets/chart_family_task_oracle.png), [`assets/chart_family_shift.png`](assets/chart_family_shift.png), [`assets/chart_3task_totals.png`](assets/chart_3task_totals.png), [`../results/c_evaluation_multimodel_20261109/success_rates.png`](../results/c_evaluation_multimodel_20261109/success_rates.png)
- **Demo Videos for Live Playback (Slide 4)**: [`../results/sequence_v1_20261017/videos/with_landmarks/`](../results/sequence_v1_20261017/videos/with_landmarks/) (`transport_place_seed0_G02_ab.mp4`, `handover_transfer_seed0_G02_ab.mp4`, `pour_water_seed0_G02_ab.mp4`)

---

## 1. Three-Member Division of Labor (10 Minutes Total)

| Speaker | Project Role | Assigned Slides | Time Budget | Core Narrative Responsibility |
|---|---|---|---|---|
| **Member A** | **Part A — Simulation, Filtering & Task Sequences** | **Slides 1 – 4** | **3 min 40 s**<br>*(0:00 – 3:40)* | Introduce the team and motivation (why static grasp stability $\neq$ task success), explain the 4-stage zero-leakage architecture, present the 3-round filtering evolution (`wide_strict_v2`, 0% execution IK failures), play the 15s simulation video demo, and explain the 458-episode PBF fluid vs proxy pouring audit. |
| **Member B** | **Part B — Multi-Family Generation & Physics/Heuristic Baselines** | **Slides 5 – 6** | **2 min 10 s**<br>*(3:40 – 5:50)* | Present the 116-candidate multi-family pool (`handle`, `rim`, `top`, `side`) and `semantic_v1` descriptors, show the 30-seed ground-truth oracle task shift across families, compare the 5 non-LLM baselines (`M0–M5`), and briefly highlight the 6D Grasp Wrench Space (`wrench_v2`) analysis. |
| **Member C** | **Part C — Multi-Model LLM Selection, 3-Stage Prompt Ablation & Conclusions** | **Slides 7 – 10** | **4 min 10 s**<br>*(5:50 – 10:00)* | Present the audited multi-model LLM protocol (`gpt-5.6-luna` & `gemini-3.8-flash`, 12 LLM configs = 720 trials), diagnose why Prompt `v1` failed (`geometry_score=0.0` unscored trap), explain the 3-stage ablation (`v1` raw $\rightarrow$ `v2_physics` zero-hint scene mechanics $\rightarrow$ `v2` embodiment priors), present the 17-method quantitative benchmark (**90.0%–91.7%** 3-task success, **20/20** zero-hint handover on Gemini), and conclude with limitations and takeaways. |

---

## 2. Slide-by-Slide English Presentation Script (Natural Academic Style)

> **Note**: All scripts below are also embedded directly inside the PowerPoint **Speaker Notes** pane of `CS5478_Task_Aware_Grasp_Selection_Presentation.pptx`.

---

### Slide 1 — Title & Team Overview
- **Speaker**: Member A
- **Time**: ~0:40 *(0:00 – 0:40)*
- **Before presenting**: Replace `[Team XX]` and `[Member A/B/C Name]` on Slide 1 with your official team number and names.

**Script**:
> "Good morning/afternoon everyone. We are Team [XX], and today we are presenting our CS5478 project on *Task-Aware Grasp Selection for Mug Manipulation*.
>
> Our core question is straightforward: when a robot picks up an everyday object like a mug, how should its choice of grasp change depending on whether it wants to place the mug on a coaster, hand it over to another gripper, or pour water from it?
>
> We divided our system into three tightly connected parts: I will cover **Part A** on the PyBullet simulation, execution-consistent filtering, and real-fluid PBF evaluation; **[Member B]** will present **Part B** on multi-family grasp synthesis and our five non-LLM baselines; and **[Member C]** will present **Part C** on our multi-model LLM selectors and our three-stage prompt ablation study across 720 trials."

---

### Slide 2 — Problem & Motivation: Why Static Grasp Quality Alone Fails Downstream Manipulation
- **Speaker**: Member A
- **Time**: ~1:00 *(0:40 – 1:40)*

**Script**:
> "Why does task-aware grasp selection matter? Traditional grasp planners focus on static stability—picking a grasp near the center of mass that resists wrench disturbances during lifting. However, on a structured object like a mug, a grasp that lifts the mug reliably often fails during the downstream manipulation sequence.
>
> On the right, you can see our three full manipulation tasks in PyBullet:
> - First, in **Transport and Place**, the robot lifts the mug, translates it to a target coaster, and must release and retreat cleanly without tipping the mug over.
> - Second, in **Handover Transfer**, the robot presents the mug to a physical receiver gripper that approaches horizontally along the $+Y$ rail to grab the handle. If the donor robot uses a horizontal side grasp on the mug body, its fingers sit right inside that horizontal approach corridor—causing a 76% failure rate.
> - Third, in **Pour Water**, the robot tilts the mug by 110 degrees to pour 256 Position-Based Fluids particles into a receiver cup. Here, handle grasps become completely IK-infeasible during the 110-degree wrist rotation.
>
> Our goal was to build a controlled, zero-leakage benchmark to test how geometric models, empirical priors, and LLMs adapt grasp selection across these three tasks."

---

### Slide 3 — Approach & Architecture: Modular Pipeline & Strict Audit Boundary
- **Speaker**: Member A
- **Time**: ~0:50 *(1:40 – 2:30)*

**Script**:
> "Slide 3 shows our four-stage architecture and the strict audit boundary across Parts A, B, and C.
>
> In **Stage 1**, Part B synthesizes 116 candidate grasps across four families—handle, rim, body top, and body side—and annotates each grasp with semantic geometry tags and a static body score.
> In **Stage 2**, Part A filters those candidates for the current task and scene seed using only a calibrated pose prior, never reading the perturbed ground-truth mug pose.
> In **Stage 3**, the feasible candidate list is passed to Part B's 5 baselines and Part C's 12 LLM selector configurations. Crucially, selectors receive *only* five public fields: candidate ID, region, semantic approach string, gripper width, and geometry score. No 6D coordinates, no failure logs, and no oracle outcomes ever leak into the selector.
> Finally, in **Stage 4**, Part A executes the chosen ID in a fresh PyBullet instance, and Part C computes Wilson 95% confidence intervals and Holm-corrected exact sign tests."

---

### Slide 4 — Part A: Closing the Filter-to-Execution Gap & Validating Proxy vs Real-Fluid Pouring
- **Speaker**: Member A
- **Time**: ~1:10 *(2:30 – 3:40, including 15s video demo)*

**Script**:
> "In Part A, one of our most important engineering findings was closing the gap between feasibility filtering and physical execution.
>
> - In **Round 1**, adding multi-start bounded IK expanded our feasible pool from 6 identical side grasps per seed to nearly 50 grasps across all four families. However, 14 to 19 percent of those grasps still failed IK during actual execution.
> - In **Round 2**, we replaced the loose filter with an execution-consistent kinematic rehearsal that steps through the exact Cartesian waypoints of the task at an 8-millimeter, 3-degree tolerance. That reduced execution-time IK failures to **zero**.
> - In **Round 3**, we noticed M2 was failing on transport because deep 30-millimeter top grasps brushed against the handle bar during approach. Enforcing our final-waypoint-only finger contact rule eliminated those approach jams and boosted M2 transport from 0 out of 10 to 28 out of 30.
>
> *[Optional: Play 15-second video clip of Transport, Handover, and PBF Pouring here]*
>
> On the right, we also audited our fast geometric pouring proxy against full 256-particle Position-Based Fluids across all 458 feasible pouring episodes. While they agree 75.8% of the time, the proxy is overly pessimistic on side grasps—scoring them at 20% versus 48% in real fluid. Therefore, we use full PBF fluid outcomes for all final pouring comparisons. Now I will hand over to **[Member B]** for Part B."

---

### Slide 5 — Part B: Multi-Family Candidate Space (`multifamily_v3p`) & Ground-Truth Task Shift
- **Speaker**: Member B
- **Time**: ~1:05 *(3:40 – 4:45)*

**Script**:
> "Thank you, [Member A]. I will now present Part B: how we construct the multi-family candidate space and our non-LLM baselines.
>
> Our pruned `multifamily_v3p` generator produces 116 candidate grasps across four structural families: rim pinches, handle grasps, top-down body grasps, and body-side grasps. Across 30 randomized scene seeds, each seed yields 15 to 43 feasible candidates, with nearly every seed producing a distinct feasible set. To describe grasp geometry without exposing raw 6D poses, we built `semantic_v1`, which encodes approach direction, finger closing axis, handle clearance, and mouth coverage purely from mug dimensions.
>
> When we execute every single feasible candidate across all 30 seeds, the ground-truth chart on the right proves that the optimal grasp family shifts dramatically by task:
> - For **Transport**, rim and handle grasps lead at **93%** and **89%**, whereas body-side grasps achieve only **49%**.
> - For **Handover**, rim grasps reach **86%**, while body-side grasps drop to **24%** because they block the horizontal receiver rail.
> - For **Pouring** in real PBF fluid, handle grasps have zero feasible candidates due to the 110-degree tilt IK, while rim and top-down body grasps achieve **73%** and **67%**."

---

### Slide 6 — Part B: Five Non-LLM Baselines (`M0–M5`) & 6D Wrench Space (`wrench_v2`) Analysis
- **Speaker**: Member B
- **Time**: ~1:05 *(4:45 – 5:50)*

**Script**:
> "On Slide 6, we evaluate five non-LLM baselines on the 20 held-out seeds (seeds 10 to 29):
> - **M0 Random** achieves **70.0%** overall (42 out of 60).
> - **M1 Static v2** scores cylindrical body grasps by static load margin, but assigns `0.0` to rim and handle grasps because the cylindrical body formula does not apply to them. Consequently, M1 only selects body grasps and drops to **5 out of 20 (25%)** on handover—significantly worse than random.
> - **M2 Task Risk** adds handcrafted task penalties and reaches **19 out of 20** on transport, but stays at **11 out of 20** on handover because it does not model the receiver's horizontal approach corridor.
> - **M5 Paper Style** implements a literature-style part preference table—grasping the handle for transport and the body for handover and pouring. It gets a perfect **20 out of 20** on transport, but collapses to **5 out of 20** on handover, showing that coarse part labels like 'grasp the body' fail without fine-grained approach-direction reasoning.
> - **M4 Family Prior** learns an empirical family-by-task lookup table from training seeds 0 to 9. It reaches **17 out of 20** on handover, but overfits to rim grasps on pouring, dropping to **12 out of 20 (60%)** on held-out PBF fluid.
>
> On the right, we also developed a 6D Grasp Wrench Space LP solver in `wrench_v2` with HiGHS Simplex-to-IPM recovery. This analysis confirmed why cross-family analytical scoring is difficult: two-point friction cones on thin rims cannot carry the axial moments absorbed by four-point body contacts. Now I'll pass to **[Member C]** to show how LLM reasoning bridges this gap."

---

### Slide 7 — Part C: Audited Multi-Model LLM Pipeline (720 Trials) & Diagnosis of Prompt `v1`
- **Speaker**: Member C
- **Time**: ~1:05 *(5:50 – 6:55)*

**Script**:
> "Thank you, [Member B]. In Part C, we built an audited multi-model evaluation pipeline testing both OpenAI's `gpt-5.6-luna` and Google's `gemini-3.8-flash` across 12 configurations and 720 total trials. Every model call must output a valid JSON object containing `selected_id` and a complete permutation ranking of all feasible candidates. We also run a strict causal ablation comparing **M3**—which receives the task intent—against **A1**, which receives the exact same candidate list and system prompt but with the task description removed.
>
> When we ran our initial `prompt_version="v1"`, we encountered an important negative result: `M3_CODEX` scored only **53.3%** overall and `M3_GEMINI` scored **63.3%**, failing to beat the heuristic baselines.
>
> Auditing the full rankings revealed three concrete representation bottlenecks:
> 1. **The `geometry_score = 0.0` trap**: Because Part B's static model only scores cylindrical body grasps (`0.59–0.75`) and leaves rim and handle grasps at `0.0`, Prompt `v1`'s statement that *'geometry_score is a higher-is-better prior'* caused both GPT and Gemini to treat `0.0` as zero quality—selecting rim or handle grasps **zero times out of 240 trials**!
> 2. **Handover corridor blindness**: Prompt `v1` only asked to *'leave the handle accessible'*, so both models chose horizontal body-side grasps that collided with the $+Y$ receiver gripper, scoring only 20% to 25%.
> 3. **Pouring tag misdirection**: The generic tag `mouth=hand over the opening` drove `v1` models to pick 20 out of 20 side grasps, which suffer high 110-degree tilt timeouts."

---

### Slide 8 — Part C: 3-Stage Prompt Ablation (`v1` $\rightarrow$ `v2_physics` $\rightarrow$ `v2`)
- **Speaker**: Member C
- **Time**: ~1:05 *(6:55 – 8:00)*

**Script**:
> "A natural scientific question is: if we fix the `0.0` score ambiguity and describe the physical task trajectory *without* naming any preferred grasp family, can the LLM infer the right grasp on its own? To test this rigorously, we designed a three-stage ablation: `v1`, `v2_physics`, and `v2`.
>
> - In **Stage 2 (`v2_physics`)**, we clarify that `0.0` means **unscored (`N/A`)** and describe only the objective physical trajectory—such as *'another gripper approaches horizontally along $+Y$ toward the handle'*—with **zero grasp-family hints**.
>   - Remarkably, on **Handover**, `M3_GEMINI_V2_PHYSICS` autonomously deduces that horizontal side grasps will block the receiver and switches **20 out of 20 times** to vertical rim grasps from above—achieving **100% handover success (20/20)** with zero family hints!
>   - Meanwhile, GPT (`M3_CODEX_V2_PHYSICS`) exhibits stronger risk aversion toward `N/A` scores and stays within scored body grasps, yet it still uses spatial reasoning to completely avoid `side=beside the handle` (`0/20` vs `6/20` in task-blind `A1`).
> - However, in **Transport** and **Pouring**, both models under `v2_physics` default to high-scoring `body mid` side grasps because generic commonsense cannot guess Panda finger clearance during table release or that the `'blocks pouring'` tag is a false alarm in particle simulation.
> - When **Stage 3 (`v2`)** provides these simulator embodiment priors, both GPT and Gemini converge to the exact same adaptive policy: **Handle** for Transport (`19/20`), **Rim** for Handover (`20/20`), and **Body Top** for Pouring (`20/20`)."

---

### Slide 9 — Comprehensive Evaluation Results (Seeds 10–29, 17 Methods) & Causal Ablation
- **Speaker**: Member C
- **Time**: ~1:05 *(8:00 – 9:05)*

**Script**:
> "Slide 9 presents our complete benchmark results across all 17 methods—5 baselines and 12 LLM variants—on the 20 held-out seeds.
>
> Tracing the three-stage progression across the 60 held-out trials:
> - In **Stage 1 (`v1` Raw)**, GPT scores **32/60 (53.3%)** and Gemini scores **38/60 (63.3%)**.
> - In **Stage 2 (`v2_physics` Zero-Hint)**, `M3_GEMINI_V2_PHYSICS` rises to **43/60 (71.7%)**, hitting **20/20 on Handover** (**15 wins to 0** over `v1`, **Holm $p = 0.0112$**; **11 wins to 0** over task-blind `A1_GEMINI_V2_PHYSICS`, exact $p = 9.77\times 10^{-4}$).
> - In **Stage 3 (`v2` Embodiment Prior)**, **`M3_GEMINI_V2` leads at 55 out of 60 (91.7%)** and **`M3_CODEX_V2` reaches 54 out of 60 (90.0%)**—outperforming every non-LLM baseline, including empirical lookup `M4` (78.3%) and task-risk `M2` (75.0%), and reaching **17/20 (85%)** in local PBF verification.
>
> Our paired exact sign tests confirm both causal task awareness (`M3` vs `A1` in `v2`: **Holm $p \le 0.0218$** on Handover and Pouring) and the statistically significant gain from embodiment priors (`v2` vs `v2_physics`: **Holm $p \le 0.0218$**)."

---

### Slide 10 — Discussion, Honest Limitations & Key Takeaways
- **Speaker**: Member C
- **Time**: ~0:55 *(9:05 – 10:00)*

**Script**:
> "To conclude on Slide 10: we built an end-to-end, cryptographically audited benchmark connecting multi-family grasp generation, execution-consistent PyBullet simulation with 256-particle PBF fluid, and a 720-trial multi-model LLM ablation study.
>
> Our three-stage prompt study provides a clear scientific takeaway on what LLMs can and cannot infer on their own:
> 1. **Genuine 3D spatial reasoning (`v2_physics`)**: When `0.0` score ambiguity is removed and objective task trajectories are described with zero hints, LLMs can infer 3D obstacle avoidance—shown by Gemini achieving 20/20 on Handover and GPT avoiding beside-the-handle collisions.
> 2. **Why embodiment priors (`v2`) are still necessary**: Generic commonsense cannot guess robot-specific hardware clearances (such as Panda finger clearance when releasing an 8-centimeter mug on a table, or 7-DoF wrist limits during a 110-degree pour). Providing those embodiment priors lifts both GPT and Gemini to **90.0%–91.7%** overall success.
> 3. **Future direction**: Exporting kinematic clearance and wrist-margin features directly from Part A's rehearsal filter into the candidate table will allow LLMs to achieve this performance purely from pre-execution kinematics without manual prompt priors.
>
> Thank you very much, and we welcome your questions!"
