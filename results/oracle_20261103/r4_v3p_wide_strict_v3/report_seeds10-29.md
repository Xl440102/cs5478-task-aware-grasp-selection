# Oracle-table experiment

- candidates: multifamily_v3p (116, profile hollow)
- scene: wide_strict_v3 = {"name": "wide_strict_v3", "x_range_m": [-0.04, 0.04], "y_range_m": [-0.05, 0.0], "yaw_range_rad": [-1.0471975511965976, 1.0471975511965976], "observation": "noisy_truth", "estimate_position_noise_m": 0.003, "estimate_yaw_noise_deg": 2.0, "ik_restarts": true, "filter_mode": "execution", "approach_finger_contact": "final_only", "disable_wrist_hand_collision": true}
- seeds: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29]; execute: all; pour_water liquid: proxy_margin

## handover_transfer

Feasible per seed: mean 33.0, min 29; distinct feasible sets 20/20; mean Jaccard 0.72; distinct successful sets 20

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 14/20 | 0.70 | 0.48-0.85 | - | 0.30 | 16 | {'top': 9, 'rim': 4, 'side': 2, 'handle': 5} |
| M1_STATIC_V2 | 7/20 | 0.35 | 0.18-0.57 | 1/8, p=0.04 | 0.65 | 5 | {'side': 13, 'top': 7} |
| M2_TASK_RISK | 5/20 | 0.25 | 0.11-0.47 | 1/10, p=0.01 | 0.75 | 3 | {'side': 20} |
| M5_PAPER_STYLE | 7/20 | 0.35 | 0.18-0.57 | 1/8, p=0.04 | 0.65 | 5 | {'side': 13, 'top': 7} |
| M4_FAMILY_PRIOR | 17/20 | 0.85 | 0.64-0.95 | 6/3, p=0.51 | 0.15 | 2 | {'rim': 20} |
| M3_MOCK | 0/20 | 0.00 | 0.00-0.16 | 0/14, p=0.00 | 1.00 | 3 | {'side': 20} |
| A1_MOCK | 0/20 | 0.00 | 0.00-0.16 | 0/14, p=0.00 | 1.00 | 3 | {'side': 20} |
| ORACLE | 20/20 | 1.00 | 0.84-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.54 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 34 {'handle': 4, 'rim': 6, 'side': 14, 'top': 10} | 22/34 | ✓ top | ✗ side | ✓ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 11 | 33 {'handle': 4, 'rim': 6, 'side': 12, 'top': 11} | 17/33 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 12 | 33 {'handle': 4, 'rim': 6, 'side': 11, 'top': 12} | 17/33 | ✓ top | ✓ top | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 13 | 32 {'handle': 4, 'rim': 6, 'side': 12, 'top': 10} | 14/32 | ✓ top | ✓ top | ✗ side | ✓ top | ✗ rim | ✗ side | ✗ side |
| 14 | 31 {'handle': 4, 'rim': 6, 'side': 13, 'top': 8} | 17/31 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 15 | 32 {'handle': 4, 'rim': 7, 'side': 9, 'top': 12} | 21/32 | ✓ top | ✓ top | ✓ side | ✓ top | ✗ rim | ✗ side | ✗ side |
| 16 | 36 {'handle': 4, 'rim': 6, 'side': 13, 'top': 13} | 22/36 | ✓ side | ✓ top | ✓ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 17 | 29 {'handle': 4, 'rim': 5, 'side': 11, 'top': 9} | 13/29 | ✗ handle | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 18 | 34 {'handle': 2, 'rim': 7, 'side': 15, 'top': 10} | 18/34 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 19 | 35 {'handle': 4, 'rim': 6, 'side': 12, 'top': 13} | 22/35 | ✗ handle | ✓ top | ✓ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 20 | 35 {'handle': 4, 'rim': 6, 'side': 13, 'top': 12} | 19/35 | ✗ handle | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 21 | 33 {'handle': 4, 'rim': 6, 'side': 14, 'top': 9} | 17/33 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 22 | 36 {'handle': 4, 'rim': 7, 'side': 11, 'top': 14} | 18/36 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 23 | 31 {'handle': 4, 'rim': 6, 'side': 9, 'top': 12} | 16/31 | ✓ handle | ✓ top | ✓ side | ✓ top | ✗ rim | ✗ side | ✗ side |
| 24 | 33 {'handle': 4, 'rim': 7, 'side': 9, 'top': 13} | 19/33 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 25 | 36 {'handle': 4, 'rim': 6, 'side': 14, 'top': 12} | 17/36 | ✗ handle | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 26 | 37 {'handle': 4, 'rim': 7, 'side': 13, 'top': 13} | 21/37 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 27 | 29 {'handle': 4, 'rim': 6, 'side': 10, 'top': 9} | 12/29 | ✗ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 28 | 30 {'handle': 4, 'rim': 6, 'side': 12, 'top': 8} | 19/30 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 29 | 32 {'handle': 4, 'rim': 6, 'side': 10, 'top': 12} | 17/32 | ✓ rim | ✓ top | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |

## pour_water

Feasible per seed: mean 15.1, min 12; distinct feasible sets 19/20; mean Jaccard 0.64; distinct successful sets 19

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 11/20 | 0.55 | 0.34-0.74 | - | 0.45 | 13 | {'rim': 5, 'top': 10, 'side': 5} |
| M1_STATIC_V2 | 13/20 | 0.65 | 0.43-0.82 | 4/2, p=0.69 | 0.35 | 4 | {'side': 11, 'top': 9} |
| M2_TASK_RISK | 14/20 | 0.70 | 0.48-0.85 | 4/1, p=0.38 | 0.30 | 5 | {'side': 18, 'top': 2} |
| M5_PAPER_STYLE | 13/20 | 0.65 | 0.43-0.82 | 4/2, p=0.69 | 0.35 | 4 | {'side': 11, 'top': 9} |
| M4_FAMILY_PRIOR | 12/20 | 0.60 | 0.39-0.78 | 5/4, p=1.00 | 0.40 | 2 | {'rim': 20} |
| M3_MOCK | 11/20 | 0.55 | 0.34-0.74 | 3/3, p=1.00 | 0.45 | 5 | {'side': 20} |
| A1_MOCK | 11/20 | 0.55 | 0.34-0.74 | 3/3, p=1.00 | 0.45 | 5 | {'side': 20} |
| ORACLE | 20/20 | 1.00 | 0.84-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.46 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 17 {'rim': 2, 'side': 7, 'top': 8} | 10/17 | ✓ rim | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 11 | 14 {'rim': 2, 'side': 7, 'top': 5} | 6/14 | ✓ top | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 12 | 15 {'rim': 2, 'side': 5, 'top': 8} | 7/15 | ✓ rim | ✗ top | ✓ side | ✗ top | ✓ rim | ✓ side | ✓ side |
| 13 | 15 {'rim': 2, 'side': 6, 'top': 7} | 7/15 | ✗ top | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 14 | 16 {'rim': 2, 'side': 7, 'top': 7} | 8/16 | ✗ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 15 | 16 {'rim': 4, 'side': 5, 'top': 7} | 8/16 | ✗ side | ✓ top | ✓ side | ✓ top | ✗ rim | ✓ side | ✓ side |
| 16 | 17 {'rim': 2, 'side': 7, 'top': 8} | 10/17 | ✓ rim | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 17 | 12 {'rim': 1, 'side': 6, 'top': 5} | 4/12 | ✗ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 18 | 16 {'rim': 2, 'side': 8, 'top': 6} | 7/16 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 19 | 15 {'rim': 2, 'side': 5, 'top': 8} | 9/15 | ✓ rim | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 20 | 17 {'rim': 3, 'side': 7, 'top': 7} | 8/17 | ✗ rim | ✗ side | ✗ side | ✗ side | ✗ rim | ✗ side | ✗ side |
| 21 | 16 {'rim': 2, 'side': 7, 'top': 7} | 8/16 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 22 | 13 {'rim': 3, 'side': 4, 'top': 6} | 5/13 | ✓ top | ✓ top | ✓ top | ✓ top | ✗ rim | ✗ side | ✗ side |
| 23 | 13 {'rim': 2, 'side': 6, 'top': 5} | 7/13 | ✓ top | ✓ top | ✓ side | ✓ top | ✗ rim | ✓ side | ✓ side |
| 24 | 15 {'rim': 3, 'side': 5, 'top': 7} | 6/15 | ✗ side | ✓ top | ✓ top | ✓ top | ✗ rim | ✗ side | ✗ side |
| 25 | 17 {'rim': 2, 'side': 8, 'top': 7} | 9/17 | ✓ side | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 26 | 16 {'rim': 2, 'side': 7, 'top': 7} | 6/16 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 27 | 13 {'rim': 2, 'side': 6, 'top': 5} | 4/13 | ✗ top | ✗ side | ✗ side | ✗ side | ✗ rim | ✗ side | ✗ side |
| 28 | 15 {'rim': 2, 'side': 6, 'top': 7} | 6/15 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 29 | 14 {'rim': 1, 'side': 6, 'top': 7} | 6/14 | ✓ top | ✓ top | ✓ side | ✓ top | ✓ rim | ✗ side | ✗ side |

## transport_place

Feasible per seed: mean 41.5, min 34; distinct feasible sets 19/20; mean Jaccard 0.74; distinct successful sets 20

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 19/20 | 0.95 | 0.76-0.99 | - | 0.05 | 15 | {'handle': 5, 'rim': 4, 'top': 5, 'side': 6} |
| M1_STATIC_V2 | 17/20 | 0.85 | 0.64-0.95 | 1/3, p=0.62 | 0.15 | 4 | {'top': 20} |
| M2_TASK_RISK | 18/20 | 0.90 | 0.70-0.97 | 1/2, p=1.00 | 0.10 | 2 | {'top': 20} |
| M5_PAPER_STYLE | 20/20 | 1.00 | 0.84-1.00 | 1/0, p=1.00 | 0.00 | 2 | {'handle': 20} |
| M4_FAMILY_PRIOR | 19/20 | 0.95 | 0.76-0.99 | 1/1, p=1.00 | 0.05 | 3 | {'handle': 20} |
| M3_MOCK | 11/20 | 0.55 | 0.34-0.74 | 1/9, p=0.02 | 0.45 | 2 | {'side': 20} |
| A1_MOCK | 11/20 | 0.55 | 0.34-0.74 | 1/9, p=0.02 | 0.45 | 2 | {'side': 20} |
| ORACLE | 20/20 | 1.00 | 0.84-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.83 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 43 {'handle': 14, 'rim': 6, 'side': 13, 'top': 10} | 39/43 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 11 | 38 {'handle': 11, 'rim': 6, 'side': 10, 'top': 11} | 28/38 | ✓ rim | ✗ top | ✓ top | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 12 | 43 {'handle': 14, 'rim': 6, 'side': 11, 'top': 12} | 35/43 | ✓ rim | ✓ top | ✗ top | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 13 | 42 {'handle': 14, 'rim': 6, 'side': 12, 'top': 10} | 33/42 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 14 | 40 {'handle': 14, 'rim': 6, 'side': 12, 'top': 8} | 32/40 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 15 | 43 {'handle': 14, 'rim': 7, 'side': 10, 'top': 12} | 39/43 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 16 | 52 {'handle': 17, 'rim': 7, 'side': 15, 'top': 13} | 43/52 | ✓ side | ✓ top | ✓ top | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 17 | 34 {'handle': 11, 'rim': 5, 'side': 9, 'top': 9} | 29/34 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 18 | 48 {'handle': 15, 'rim': 7, 'side': 16, 'top': 10} | 41/48 | ✓ side | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 19 | 51 {'handle': 17, 'rim': 7, 'side': 14, 'top': 13} | 39/51 | ✓ side | ✓ top | ✓ top | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 20 | 43 {'handle': 14, 'rim': 6, 'side': 11, 'top': 12} | 39/43 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 21 | 41 {'handle': 14, 'rim': 6, 'side': 12, 'top': 9} | 33/41 | ✓ handle | ✗ top | ✗ top | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 22 | 38 {'handle': 4, 'rim': 7, 'side': 13, 'top': 14} | 33/38 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 23 | 38 {'handle': 11, 'rim': 6, 'side': 9, 'top': 12} | 31/38 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 24 | 41 {'handle': 10, 'rim': 7, 'side': 11, 'top': 13} | 28/41 | ✓ side | ✓ top | ✓ top | ✓ handle | ✗ handle | ✓ side | ✓ side |
| 25 | 39 {'handle': 11, 'rim': 6, 'side': 10, 'top': 12} | 34/39 | ✗ side | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 26 | 45 {'handle': 10, 'rim': 7, 'side': 15, 'top': 13} | 34/45 | ✓ top | ✗ top | ✓ top | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 27 | 34 {'handle': 11, 'rim': 6, 'side': 8, 'top': 9} | 33/34 | ✓ side | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 28 | 39 {'handle': 14, 'rim': 6, 'side': 11, 'top': 8} | 34/39 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 29 | 39 {'handle': 11, 'rim': 6, 'side': 10, 'top': 12} | 30/39 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ handle | ✗ side | ✗ side |
