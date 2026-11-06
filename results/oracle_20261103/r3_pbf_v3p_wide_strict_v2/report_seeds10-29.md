# Oracle-table experiment

- candidates: multifamily_v3p (116, profile hollow)
- scene: wide_strict_v2 = {"name": "wide_strict_v2", "x_range_m": [-0.04, 0.04], "y_range_m": [-0.05, 0.0], "yaw_range_rad": [-1.0471975511965976, 1.0471975511965976], "observation": "noisy_truth", "estimate_position_noise_m": 0.003, "estimate_yaw_noise_deg": 2.0, "ik_restarts": true, "filter_mode": "execution", "approach_finger_contact": "final_only"}
- seeds: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29]; execute: all; pour_water liquid: pbf

## pour_water

Feasible per seed: mean 15.1, min 12; distinct feasible sets 19/20; mean Jaccard 0.64; distinct successful sets 20

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 16/20 | 0.80 | 0.58-0.92 | - | 0.20 | 13 | {'rim': 5, 'top': 10, 'side': 5} |
| M1_STATIC_V2 | 13/20 | 0.65 | 0.43-0.82 | 4/7, p=0.55 | 0.35 | 4 | {'side': 11, 'top': 9} |
| M2_TASK_RISK | 15/20 | 0.75 | 0.53-0.89 | 4/5, p=1.00 | 0.25 | 5 | {'side': 18, 'top': 2} |
| M5_PAPER_STYLE | 13/20 | 0.65 | 0.43-0.82 | 4/7, p=0.55 | 0.35 | 4 | {'side': 11, 'top': 9} |
| M4_FAMILY_PRIOR | 12/20 | 0.60 | 0.39-0.78 | 1/5, p=0.22 | 0.40 | 2 | {'rim': 20} |
| M3_MOCK | 16/20 | 0.80 | 0.58-0.92 | 3/3, p=1.00 | 0.20 | 5 | {'side': 20} |
| A1_MOCK | 16/20 | 0.80 | 0.58-0.92 | 3/3, p=1.00 | 0.20 | 5 | {'side': 20} |
| ORACLE | 20/20 | 1.00 | 0.84-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.61 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 17 {'rim': 2, 'side': 7, 'top': 8} | 12/17 | ✗ rim | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 11 | 14 {'rim': 2, 'side': 7, 'top': 5} | 7/14 | ✓ top | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 12 | 15 {'rim': 2, 'side': 5, 'top': 8} | 11/15 | ✓ rim | ✗ top | ✓ side | ✗ top | ✓ rim | ✓ side | ✓ side |
| 13 | 15 {'rim': 2, 'side': 6, 'top': 7} | 8/15 | ✓ top | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 14 | 16 {'rim': 2, 'side': 7, 'top': 7} | 12/16 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 15 | 16 {'rim': 4, 'side': 5, 'top': 7} | 7/16 | ✓ side | ✗ top | ✗ side | ✗ top | ✗ rim | ✓ side | ✓ side |
| 16 | 17 {'rim': 2, 'side': 7, 'top': 8} | 11/17 | ✓ rim | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 17 | 12 {'rim': 1, 'side': 6, 'top': 5} | 7/12 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 18 | 16 {'rim': 2, 'side': 8, 'top': 6} | 10/16 | ✓ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ side | ✓ side |
| 19 | 15 {'rim': 2, 'side': 5, 'top': 8} | 12/15 | ✓ rim | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 20 | 17 {'rim': 3, 'side': 7, 'top': 7} | 10/17 | ✗ rim | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 21 | 16 {'rim': 2, 'side': 7, 'top': 7} | 12/16 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 22 | 13 {'rim': 3, 'side': 4, 'top': 6} | 5/13 | ✓ top | ✗ top | ✓ top | ✗ top | ✓ rim | ✓ side | ✓ side |
| 23 | 13 {'rim': 2, 'side': 6, 'top': 5} | 9/13 | ✓ top | ✓ top | ✓ side | ✓ top | ✗ rim | ✓ side | ✓ side |
| 24 | 15 {'rim': 3, 'side': 5, 'top': 7} | 9/15 | ✗ side | ✓ top | ✓ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 25 | 17 {'rim': 2, 'side': 8, 'top': 7} | 9/17 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ rim | ✗ side | ✗ side |
| 26 | 16 {'rim': 2, 'side': 7, 'top': 7} | 9/16 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 27 | 13 {'rim': 2, 'side': 6, 'top': 5} | 8/13 | ✓ top | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 28 | 15 {'rim': 2, 'side': 6, 'top': 7} | 8/15 | ✗ side | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 29 | 14 {'rim': 1, 'side': 6, 'top': 7} | 10/14 | ✓ top | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
