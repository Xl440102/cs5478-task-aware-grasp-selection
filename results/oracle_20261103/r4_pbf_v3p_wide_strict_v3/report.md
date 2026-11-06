# Oracle-table experiment

- candidates: multifamily_v3p (116, profile hollow)
- scene: wide_strict_v3 = {"name": "wide_strict_v3", "x_range_m": [-0.04, 0.04], "y_range_m": [-0.05, 0.0], "yaw_range_rad": [-1.0471975511965976, 1.0471975511965976], "observation": "noisy_truth", "estimate_position_noise_m": 0.003, "estimate_yaw_noise_deg": 2.0, "ik_restarts": true, "filter_mode": "execution", "approach_finger_contact": "final_only", "disable_wrist_hand_collision": true}
- seeds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29]; execute: all; pour_water liquid: pbf

## pour_water

Feasible per seed: mean 15.3, min 12; distinct feasible sets 28/30; mean Jaccard 0.66; distinct successful sets 30

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 23/30 | 0.77 | 0.59-0.88 | - | 0.23 | 17 | {'top': 14, 'rim': 6, 'side': 10} |
| M1_STATIC_V2 | 26/30 | 0.87 | 0.70-0.95 | 6/3, p=0.51 | 0.13 | 5 | {'side': 17, 'top': 13} |
| M2_TASK_RISK | 26/30 | 0.87 | 0.70-0.95 | 5/2, p=0.45 | 0.13 | 6 | {'side': 26, 'top': 4} |
| M5_PAPER_STYLE | 26/30 | 0.87 | 0.70-0.95 | 6/3, p=0.51 | 0.13 | 5 | {'side': 17, 'top': 13} |
| M4_FAMILY_PRIOR | 22/30 | 0.73 | 0.56-0.86 | 4/5, p=1.00 | 0.27 | 2 | {'rim': 30} |
| M3_MOCK | 23/30 | 0.77 | 0.59-0.88 | 5/5, p=1.00 | 0.23 | 6 | {'side': 30} |
| A1_MOCK | 23/30 | 0.77 | 0.59-0.88 | 5/5, p=1.00 | 0.23 | 6 | {'side': 30} |
| ORACLE | 30/30 | 1.00 | 0.89-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.69 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 16 {'rim': 2, 'side': 6, 'top': 8} | 12/16 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 1 | 14 {'rim': 1, 'side': 6, 'top': 7} | 9/14 | ✓ rim | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 2 | 15 {'rim': 3, 'side': 4, 'top': 8} | 11/15 | ✓ top | ✓ top | ✗ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 3 | 18 {'rim': 2, 'side': 8, 'top': 8} | 9/18 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 4 | 17 {'rim': 2, 'side': 7, 'top': 8} | 11/17 | ✗ side | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 5 | 13 {'rim': 1, 'side': 5, 'top': 7} | 10/13 | ✓ side | ✗ top | ✓ side | ✗ top | ✓ rim | ✓ side | ✓ side |
| 6 | 18 {'rim': 2, 'side': 8, 'top': 8} | 15/18 | ✗ side | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 7 | 14 {'rim': 2, 'side': 5, 'top': 7} | 9/14 | ✓ side | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 8 | 17 {'rim': 2, 'side': 7, 'top': 8} | 15/17 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 9 | 14 {'rim': 2, 'side': 4, 'top': 8} | 8/14 | ✗ top | ✓ top | ✗ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 10 | 17 {'rim': 2, 'side': 7, 'top': 8} | 14/17 | ✓ rim | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 11 | 14 {'rim': 2, 'side': 7, 'top': 5} | 8/14 | ✓ top | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 12 | 15 {'rim': 2, 'side': 5, 'top': 8} | 9/15 | ✓ rim | ✗ top | ✓ side | ✗ top | ✓ rim | ✓ side | ✓ side |
| 13 | 15 {'rim': 2, 'side': 6, 'top': 7} | 10/15 | ✓ top | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 14 | 16 {'rim': 2, 'side': 7, 'top': 7} | 8/16 | ✗ top | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 15 | 16 {'rim': 4, 'side': 5, 'top': 7} | 9/16 | ✓ side | ✓ top | ✓ side | ✓ top | ✗ rim | ✗ side | ✗ side |
| 16 | 17 {'rim': 2, 'side': 7, 'top': 8} | 12/17 | ✓ rim | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 17 | 12 {'rim': 1, 'side': 6, 'top': 5} | 7/12 | ✗ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 18 | 16 {'rim': 2, 'side': 8, 'top': 6} | 13/16 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 19 | 15 {'rim': 2, 'side': 5, 'top': 8} | 13/15 | ✓ rim | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 20 | 17 {'rim': 3, 'side': 7, 'top': 7} | 13/17 | ✗ rim | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 21 | 16 {'rim': 2, 'side': 7, 'top': 7} | 12/16 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 22 | 13 {'rim': 3, 'side': 4, 'top': 6} | 10/13 | ✓ top | ✓ top | ✓ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 23 | 13 {'rim': 2, 'side': 6, 'top': 5} | 11/13 | ✓ top | ✓ top | ✓ side | ✓ top | ✗ rim | ✓ side | ✓ side |
| 24 | 15 {'rim': 3, 'side': 5, 'top': 7} | 9/15 | ✓ side | ✓ top | ✓ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 25 | 17 {'rim': 2, 'side': 8, 'top': 7} | 8/17 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ rim | ✗ side | ✗ side |
| 26 | 16 {'rim': 2, 'side': 7, 'top': 7} | 12/16 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 27 | 13 {'rim': 2, 'side': 6, 'top': 5} | 8/13 | ✗ top | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 28 | 15 {'rim': 2, 'side': 6, 'top': 7} | 9/15 | ✓ side | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 29 | 14 {'rim': 1, 'side': 6, 'top': 7} | 11/14 | ✓ top | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
