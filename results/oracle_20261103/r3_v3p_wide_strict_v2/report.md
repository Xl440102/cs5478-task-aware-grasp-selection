# Oracle-table experiment

- candidates: multifamily_v3p (116, profile hollow)
- scene: wide_strict_v2 = {"name": "wide_strict_v2", "x_range_m": [-0.04, 0.04], "y_range_m": [-0.05, 0.0], "yaw_range_rad": [-1.0471975511965976, 1.0471975511965976], "observation": "noisy_truth", "estimate_position_noise_m": 0.003, "estimate_yaw_noise_deg": 2.0, "ik_restarts": true, "filter_mode": "execution", "approach_finger_contact": "final_only"}
- seeds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29]; execute: all; pour_water liquid: proxy_margin

## handover_transfer

Feasible per seed: mean 33.4, min 29; distinct feasible sets 30/30; mean Jaccard 0.7; distinct successful sets 30

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 18/30 | 0.60 | 0.42-0.75 | - | 0.40 | 21 | {'side': 5, 'rim': 5, 'top': 13, 'handle': 7} |
| M1_STATIC_V2 | 9/30 | 0.30 | 0.17-0.48 | 4/13, p=0.05 | 0.70 | 5 | {'side': 19, 'top': 11} |
| M2_TASK_RISK | 16/30 | 0.53 | 0.36-0.70 | 7/9, p=0.80 | 0.47 | 4 | {'side': 29, 'top': 1} |
| M5_PAPER_STYLE | 9/30 | 0.30 | 0.17-0.48 | 4/13, p=0.05 | 0.70 | 5 | {'side': 19, 'top': 11} |
| M4_FAMILY_PRIOR | 25/30 | 0.83 | 0.66-0.93 | 11/4, p=0.12 | 0.17 | 2 | {'rim': 30} |
| M3_MOCK | 0/30 | 0.00 | 0.00-0.11 | 0/18, p=0.00 | 1.00 | 4 | {'side': 30} |
| A1_MOCK | 0/30 | 0.00 | 0.00-0.11 | 0/18, p=0.00 | 1.00 | 4 | {'side': 30} |
| ORACLE | 30/30 | 1.00 | 0.89-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.52 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 35 {'handle': 4, 'rim': 6, 'side': 12, 'top': 13} | 23/35 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ rim | ✗ side | ✗ side |
| 1 | 32 {'handle': 4, 'rim': 6, 'side': 11, 'top': 11} | 14/32 | ✓ rim | ✗ side | ✓ side | ✗ side | ✗ rim | ✗ side | ✗ side |
| 2 | 34 {'handle': 4, 'rim': 7, 'side': 9, 'top': 14} | 17/34 | ✓ top | ✓ top | ✗ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 3 | 35 {'handle': 4, 'rim': 7, 'side': 12, 'top': 12} | 17/35 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 4 | 34 {'handle': 4, 'rim': 7, 'side': 11, 'top': 12} | 23/34 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✗ side | ✗ side |
| 5 | 32 {'handle': 4, 'rim': 6, 'side': 10, 'top': 12} | 16/32 | ✗ side | ✓ top | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 6 | 39 {'handle': 4, 'rim': 6, 'side': 16, 'top': 13} | 22/39 | ✗ side | ✗ side | ✓ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 7 | 31 {'handle': 4, 'rim': 6, 'side': 10, 'top': 11} | 16/31 | ✓ handle | ✗ top | ✓ side | ✗ top | ✗ rim | ✗ side | ✗ side |
| 8 | 35 {'handle': 4, 'rim': 7, 'side': 11, 'top': 13} | 17/35 | ✗ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 9 | 33 {'handle': 4, 'rim': 7, 'side': 8, 'top': 14} | 13/33 | ✓ handle | ✗ top | ✗ side | ✗ top | ✓ rim | ✗ side | ✗ side |
| 10 | 34 {'handle': 4, 'rim': 6, 'side': 14, 'top': 10} | 18/34 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 11 | 33 {'handle': 4, 'rim': 6, 'side': 12, 'top': 11} | 17/33 | ✓ top | ✗ side | ✓ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 12 | 33 {'handle': 4, 'rim': 6, 'side': 11, 'top': 12} | 17/33 | ✗ top | ✗ top | ✓ side | ✗ top | ✓ rim | ✗ side | ✗ side |
| 13 | 32 {'handle': 4, 'rim': 6, 'side': 12, 'top': 10} | 15/32 | ✓ top | ✗ top | ✓ side | ✗ top | ✗ rim | ✗ side | ✗ side |
| 14 | 31 {'handle': 4, 'rim': 6, 'side': 13, 'top': 8} | 15/31 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 15 | 32 {'handle': 4, 'rim': 7, 'side': 9, 'top': 12} | 19/32 | ✓ top | ✓ top | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 16 | 36 {'handle': 4, 'rim': 6, 'side': 13, 'top': 13} | 20/36 | ✗ side | ✓ top | ✓ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 17 | 29 {'handle': 4, 'rim': 5, 'side': 11, 'top': 9} | 15/29 | ✗ handle | ✗ side | ✓ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 18 | 34 {'handle': 2, 'rim': 7, 'side': 15, 'top': 10} | 18/34 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 19 | 35 {'handle': 4, 'rim': 6, 'side': 12, 'top': 13} | 22/35 | ✗ handle | ✓ top | ✓ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 20 | 35 {'handle': 4, 'rim': 6, 'side': 13, 'top': 12} | 20/35 | ✗ handle | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 21 | 33 {'handle': 4, 'rim': 6, 'side': 14, 'top': 9} | 17/33 | ✓ top | ✗ side | ✓ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 22 | 36 {'handle': 4, 'rim': 7, 'side': 11, 'top': 14} | 16/36 | ✓ rim | ✗ side | ✓ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 23 | 31 {'handle': 4, 'rim': 6, 'side': 9, 'top': 12} | 15/31 | ✓ handle | ✓ top | ✗ side | ✓ top | ✗ rim | ✗ side | ✗ side |
| 24 | 33 {'handle': 4, 'rim': 7, 'side': 9, 'top': 13} | 16/33 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 25 | 36 {'handle': 4, 'rim': 6, 'side': 14, 'top': 12} | 17/36 | ✗ handle | ✗ side | ✓ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 26 | 37 {'handle': 4, 'rim': 7, 'side': 13, 'top': 13} | 16/37 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 27 | 29 {'handle': 4, 'rim': 6, 'side': 10, 'top': 9} | 14/29 | ✗ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 28 | 30 {'handle': 4, 'rim': 6, 'side': 12, 'top': 8} | 20/30 | ✓ top | ✗ side | ✓ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 29 | 32 {'handle': 4, 'rim': 6, 'side': 10, 'top': 12} | 17/32 | ✗ rim | ✓ top | ✓ side | ✓ top | ✗ rim | ✗ side | ✗ side |

## pour_water

Feasible per seed: mean 15.3, min 12; distinct feasible sets 28/30; mean Jaccard 0.66; distinct successful sets 28

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 17/30 | 0.57 | 0.39-0.73 | - | 0.43 | 17 | {'top': 14, 'rim': 6, 'side': 10} |
| M1_STATIC_V2 | 15/30 | 0.50 | 0.33-0.67 | 6/8, p=0.79 | 0.50 | 5 | {'side': 17, 'top': 13} |
| M2_TASK_RISK | 15/30 | 0.50 | 0.33-0.67 | 5/7, p=0.77 | 0.50 | 6 | {'side': 26, 'top': 4} |
| M5_PAPER_STYLE | 15/30 | 0.50 | 0.33-0.67 | 6/8, p=0.79 | 0.50 | 5 | {'side': 17, 'top': 13} |
| M4_FAMILY_PRIOR | 23/30 | 0.77 | 0.59-0.88 | 11/5, p=0.21 | 0.23 | 2 | {'rim': 30} |
| M3_MOCK | 13/30 | 0.43 | 0.27-0.61 | 4/8, p=0.39 | 0.57 | 6 | {'side': 30} |
| A1_MOCK | 13/30 | 0.43 | 0.27-0.61 | 4/8, p=0.39 | 0.57 | 6 | {'side': 30} |
| ORACLE | 30/30 | 1.00 | 0.89-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.48 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 16 {'rim': 2, 'side': 6, 'top': 8} | 9/16 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 1 | 14 {'rim': 1, 'side': 6, 'top': 7} | 6/14 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 2 | 15 {'rim': 3, 'side': 4, 'top': 8} | 5/15 | ✓ top | ✗ top | ✗ top | ✗ top | ✗ rim | ✗ side | ✗ side |
| 3 | 18 {'rim': 2, 'side': 8, 'top': 8} | 9/18 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ side | ✓ side |
| 4 | 17 {'rim': 2, 'side': 7, 'top': 8} | 9/17 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 5 | 13 {'rim': 1, 'side': 5, 'top': 7} | 7/13 | ✗ side | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 6 | 18 {'rim': 2, 'side': 8, 'top': 8} | 8/18 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 7 | 14 {'rim': 2, 'side': 5, 'top': 7} | 9/14 | ✗ side | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 8 | 17 {'rim': 2, 'side': 7, 'top': 8} | 7/17 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 9 | 14 {'rim': 2, 'side': 4, 'top': 8} | 7/14 | ✗ top | ✓ top | ✗ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 10 | 17 {'rim': 2, 'side': 7, 'top': 8} | 9/17 | ✓ rim | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 11 | 14 {'rim': 2, 'side': 7, 'top': 5} | 7/14 | ✓ top | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 12 | 15 {'rim': 2, 'side': 5, 'top': 8} | 9/15 | ✓ rim | ✗ top | ✓ side | ✗ top | ✓ rim | ✓ side | ✓ side |
| 13 | 15 {'rim': 2, 'side': 6, 'top': 7} | 7/15 | ✗ top | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 14 | 16 {'rim': 2, 'side': 7, 'top': 7} | 9/16 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ side | ✓ side |
| 15 | 16 {'rim': 4, 'side': 5, 'top': 7} | 9/16 | ✗ side | ✓ top | ✓ side | ✓ top | ✗ rim | ✓ side | ✓ side |
| 16 | 17 {'rim': 2, 'side': 7, 'top': 8} | 8/17 | ✓ rim | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 17 | 12 {'rim': 1, 'side': 6, 'top': 5} | 5/12 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 18 | 16 {'rim': 2, 'side': 8, 'top': 6} | 7/16 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 19 | 15 {'rim': 2, 'side': 5, 'top': 8} | 10/15 | ✓ rim | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 20 | 17 {'rim': 3, 'side': 7, 'top': 7} | 8/17 | ✗ rim | ✗ side | ✗ side | ✗ side | ✗ rim | ✗ side | ✗ side |
| 21 | 16 {'rim': 2, 'side': 7, 'top': 7} | 6/16 | ✗ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 22 | 13 {'rim': 3, 'side': 4, 'top': 6} | 5/13 | ✓ top | ✓ top | ✓ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 23 | 13 {'rim': 2, 'side': 6, 'top': 5} | 6/13 | ✓ top | ✓ top | ✓ side | ✓ top | ✗ rim | ✗ side | ✗ side |
| 24 | 15 {'rim': 3, 'side': 5, 'top': 7} | 7/15 | ✗ side | ✓ top | ✓ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 25 | 17 {'rim': 2, 'side': 8, 'top': 7} | 9/17 | ✓ side | ✓ side | ✓ side | ✓ side | ✗ rim | ✓ side | ✓ side |
| 26 | 16 {'rim': 2, 'side': 7, 'top': 7} | 4/16 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 27 | 13 {'rim': 2, 'side': 6, 'top': 5} | 5/13 | ✓ top | ✗ side | ✗ side | ✗ side | ✗ rim | ✗ side | ✗ side |
| 28 | 15 {'rim': 2, 'side': 6, 'top': 7} | 6/15 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ side | ✗ side |
| 29 | 14 {'rim': 1, 'side': 6, 'top': 7} | 9/14 | ✓ top | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |

## transport_place

Feasible per seed: mean 42.5, min 34; distinct feasible sets 29/30; mean Jaccard 0.72; distinct successful sets 30

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 25/30 | 0.83 | 0.66-0.93 | - | 0.17 | 21 | {'handle': 9, 'rim': 9, 'top': 6, 'side': 6} |
| M1_STATIC_V2 | 25/30 | 0.83 | 0.66-0.93 | 5/5, p=1.00 | 0.17 | 4 | {'top': 30} |
| M2_TASK_RISK | 28/30 | 0.93 | 0.79-0.98 | 5/2, p=0.45 | 0.07 | 2 | {'top': 30} |
| M5_PAPER_STYLE | 30/30 | 1.00 | 0.89-1.00 | 5/0, p=0.06 | 0.00 | 2 | {'handle': 30} |
| M4_FAMILY_PRIOR | 25/30 | 0.83 | 0.66-0.93 | 4/4, p=1.00 | 0.17 | 3 | {'rim': 30} |
| M3_MOCK | 17/30 | 0.57 | 0.39-0.73 | 4/12, p=0.08 | 0.43 | 2 | {'side': 30} |
| A1_MOCK | 17/30 | 0.57 | 0.39-0.73 | 4/12, p=0.08 | 0.43 | 2 | {'side': 30} |
| ORACLE | 30/30 | 1.00 | 0.89-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.77 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 50 {'handle': 17, 'rim': 7, 'side': 13, 'top': 13} | 36/50 | ✓ handle | ✗ top | ✓ top | ✓ handle | ✗ rim | ✓ side | ✓ side |
| 1 | 40 {'handle': 14, 'rim': 6, 'side': 9, 'top': 11} | 32/40 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 2 | 37 {'handle': 4, 'rim': 7, 'side': 12, 'top': 14} | 24/37 | ✓ handle | ✓ top | ✗ top | ✓ handle | ✓ rim | ✗ side | ✗ side |
| 3 | 50 {'handle': 17, 'rim': 8, 'side': 13, 'top': 12} | 37/50 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 4 | 49 {'handle': 17, 'rim': 8, 'side': 12, 'top': 12} | 40/49 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 5 | 39 {'handle': 11, 'rim': 6, 'side': 10, 'top': 12} | 28/39 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ rim | ✗ side | ✗ side |
| 6 | 51 {'handle': 17, 'rim': 7, 'side': 14, 'top': 13} | 38/51 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✗ rim | ✓ side | ✓ side |
| 7 | 41 {'handle': 14, 'rim': 6, 'side': 10, 'top': 11} | 18/41 | ✓ top | ✗ top | ✓ top | ✓ handle | ✗ rim | ✗ side | ✗ side |
| 8 | 43 {'handle': 10, 'rim': 7, 'side': 13, 'top': 13} | 34/43 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 9 | 43 {'handle': 10, 'rim': 7, 'side': 12, 'top': 14} | 28/43 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ rim | ✗ side | ✗ side |
| 10 | 43 {'handle': 14, 'rim': 6, 'side': 13, 'top': 10} | 38/43 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 11 | 38 {'handle': 11, 'rim': 6, 'side': 10, 'top': 11} | 29/38 | ✓ rim | ✗ top | ✓ top | ✓ handle | ✓ rim | ✗ side | ✗ side |
| 12 | 43 {'handle': 14, 'rim': 6, 'side': 11, 'top': 12} | 34/43 | ✓ rim | ✗ top | ✗ top | ✓ handle | ✓ rim | ✗ side | ✗ side |
| 13 | 42 {'handle': 14, 'rim': 6, 'side': 12, 'top': 10} | 33/42 | ✓ top | ✗ top | ✓ top | ✓ handle | ✓ rim | ✗ side | ✗ side |
| 14 | 40 {'handle': 14, 'rim': 6, 'side': 12, 'top': 8} | 32/40 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ rim | ✗ side | ✗ side |
| 15 | 43 {'handle': 14, 'rim': 7, 'side': 10, 'top': 12} | 36/43 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ rim | ✗ side | ✗ side |
| 16 | 52 {'handle': 17, 'rim': 7, 'side': 15, 'top': 13} | 38/52 | ✗ side | ✓ top | ✓ top | ✓ handle | ✗ rim | ✗ side | ✗ side |
| 17 | 34 {'handle': 11, 'rim': 5, 'side': 9, 'top': 9} | 29/34 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 18 | 48 {'handle': 15, 'rim': 7, 'side': 16, 'top': 10} | 37/48 | ✗ side | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 19 | 51 {'handle': 17, 'rim': 7, 'side': 14, 'top': 13} | 36/51 | ✓ side | ✓ top | ✓ top | ✓ handle | ✗ rim | ✗ side | ✗ side |
| 20 | 43 {'handle': 14, 'rim': 6, 'side': 11, 'top': 12} | 39/43 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 21 | 41 {'handle': 14, 'rim': 6, 'side': 12, 'top': 9} | 33/41 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 22 | 38 {'handle': 4, 'rim': 7, 'side': 13, 'top': 14} | 30/38 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 23 | 38 {'handle': 11, 'rim': 6, 'side': 9, 'top': 12} | 30/38 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ rim | ✗ side | ✗ side |
| 24 | 41 {'handle': 10, 'rim': 7, 'side': 11, 'top': 13} | 28/41 | ✗ side | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 25 | 39 {'handle': 11, 'rim': 6, 'side': 10, 'top': 12} | 35/39 | ✓ side | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 26 | 45 {'handle': 10, 'rim': 7, 'side': 15, 'top': 13} | 26/45 | ✗ top | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 27 | 34 {'handle': 11, 'rim': 6, 'side': 8, 'top': 9} | 30/34 | ✗ side | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 28 | 39 {'handle': 14, 'rim': 6, 'side': 11, 'top': 8} | 33/39 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ side | ✓ side |
| 29 | 39 {'handle': 11, 'rim': 6, 'side': 10, 'top': 12} | 31/39 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ rim | ✗ side | ✗ side |
