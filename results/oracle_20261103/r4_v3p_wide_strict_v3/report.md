# Oracle-table experiment

- candidates: multifamily_v3p (116, profile hollow)
- scene: wide_strict_v3 = {"name": "wide_strict_v3", "x_range_m": [-0.04, 0.04], "y_range_m": [-0.05, 0.0], "yaw_range_rad": [-1.0471975511965976, 1.0471975511965976], "observation": "noisy_truth", "estimate_position_noise_m": 0.003, "estimate_yaw_noise_deg": 2.0, "ik_restarts": true, "filter_mode": "execution", "approach_finger_contact": "final_only", "disable_wrist_hand_collision": true}
- seeds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29]; execute: all; pour_water liquid: proxy_margin

## handover_transfer

Feasible per seed: mean 33.4, min 29; distinct feasible sets 30/30; mean Jaccard 0.7; distinct successful sets 30

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 23/30 | 0.77 | 0.59-0.88 | - | 0.23 | 21 | {'side': 5, 'rim': 5, 'top': 13, 'handle': 7} |
| M1_STATIC_V2 | 13/30 | 0.43 | 0.27-0.61 | 1/11, p=0.01 | 0.57 | 5 | {'side': 19, 'top': 11} |
| M2_TASK_RISK | 11/30 | 0.37 | 0.22-0.54 | 1/13, p=0.00 | 0.63 | 4 | {'side': 29, 'top': 1} |
| M5_PAPER_STYLE | 13/30 | 0.43 | 0.27-0.61 | 1/11, p=0.01 | 0.57 | 5 | {'side': 19, 'top': 11} |
| M6_PROMPT_RULES | 28/30 | 0.93 | 0.79-0.98 | 7/2, p=0.18 | 0.07 | 1 | {'rim': 30} |
| M4_FAMILY_PRIOR | 23/30 | 0.77 | 0.59-0.88 | 7/7, p=1.00 | 0.23 | 2 | {'rim': 30} |
| M3_MOCK | 0/30 | 0.00 | 0.00-0.11 | 0/23, p=0.00 | 1.00 | 4 | {'side': 30} |
| A1_MOCK | 0/30 | 0.00 | 0.00-0.11 | 0/23, p=0.00 | 1.00 | 4 | {'side': 30} |
| ORACLE | 30/30 | 1.00 | 0.89-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.54 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M6_PROMPT_RULES | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 35 {'handle': 4, 'rim': 6, 'side': 12, 'top': 13} | 25/35 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 1 | 32 {'handle': 4, 'rim': 6, 'side': 11, 'top': 11} | 14/32 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ rim | ✗ rim | ✗ side | ✗ side |
| 2 | 34 {'handle': 4, 'rim': 7, 'side': 9, 'top': 14} | 18/34 | ✓ top | ✗ top | ✓ top | ✗ top | ✗ rim | ✓ rim | ✗ side | ✗ side |
| 3 | 35 {'handle': 4, 'rim': 7, 'side': 12, 'top': 12} | 20/35 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✗ rim | ✗ side | ✗ side |
| 4 | 34 {'handle': 4, 'rim': 7, 'side': 11, 'top': 12} | 22/34 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ rim | ✗ rim | ✗ side | ✗ side |
| 5 | 32 {'handle': 4, 'rim': 6, 'side': 10, 'top': 12} | 19/32 | ✓ side | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 6 | 39 {'handle': 4, 'rim': 6, 'side': 16, 'top': 13} | 26/39 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 7 | 31 {'handle': 4, 'rim': 6, 'side': 10, 'top': 11} | 15/31 | ✓ handle | ✓ top | ✗ side | ✓ top | ✓ rim | ✗ rim | ✗ side | ✗ side |
| 8 | 35 {'handle': 4, 'rim': 7, 'side': 11, 'top': 13} | 15/35 | ✗ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 9 | 33 {'handle': 4, 'rim': 7, 'side': 8, 'top': 14} | 15/33 | ✓ handle | ✗ top | ✗ side | ✗ top | ✗ rim | ✓ rim | ✗ side | ✗ side |
| 10 | 34 {'handle': 4, 'rim': 6, 'side': 14, 'top': 10} | 22/34 | ✓ top | ✗ side | ✓ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 11 | 33 {'handle': 4, 'rim': 6, 'side': 12, 'top': 11} | 17/33 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 12 | 33 {'handle': 4, 'rim': 6, 'side': 11, 'top': 12} | 17/33 | ✓ top | ✓ top | ✗ side | ✓ top | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 13 | 32 {'handle': 4, 'rim': 6, 'side': 12, 'top': 10} | 14/32 | ✓ top | ✓ top | ✗ side | ✓ top | ✓ rim | ✗ rim | ✗ side | ✗ side |
| 14 | 31 {'handle': 4, 'rim': 6, 'side': 13, 'top': 8} | 17/31 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 15 | 32 {'handle': 4, 'rim': 7, 'side': 9, 'top': 12} | 21/32 | ✓ top | ✓ top | ✓ side | ✓ top | ✓ rim | ✗ rim | ✗ side | ✗ side |
| 16 | 36 {'handle': 4, 'rim': 6, 'side': 13, 'top': 13} | 22/36 | ✓ side | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 17 | 29 {'handle': 4, 'rim': 5, 'side': 11, 'top': 9} | 13/29 | ✗ handle | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 18 | 34 {'handle': 2, 'rim': 7, 'side': 15, 'top': 10} | 18/34 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 19 | 35 {'handle': 4, 'rim': 6, 'side': 12, 'top': 13} | 22/35 | ✗ handle | ✓ top | ✓ side | ✓ top | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 20 | 35 {'handle': 4, 'rim': 6, 'side': 13, 'top': 12} | 19/35 | ✗ handle | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 21 | 33 {'handle': 4, 'rim': 6, 'side': 14, 'top': 9} | 17/33 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 22 | 36 {'handle': 4, 'rim': 7, 'side': 11, 'top': 14} | 18/36 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 23 | 31 {'handle': 4, 'rim': 6, 'side': 9, 'top': 12} | 16/31 | ✓ handle | ✓ top | ✓ side | ✓ top | ✓ rim | ✗ rim | ✗ side | ✗ side |
| 24 | 33 {'handle': 4, 'rim': 7, 'side': 9, 'top': 13} | 19/33 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 25 | 36 {'handle': 4, 'rim': 6, 'side': 14, 'top': 12} | 17/36 | ✗ handle | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 26 | 37 {'handle': 4, 'rim': 7, 'side': 13, 'top': 13} | 21/37 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 27 | 29 {'handle': 4, 'rim': 6, 'side': 10, 'top': 9} | 12/29 | ✗ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 28 | 30 {'handle': 4, 'rim': 6, 'side': 12, 'top': 8} | 19/30 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ rim | ✓ rim | ✗ side | ✗ side |
| 29 | 32 {'handle': 4, 'rim': 6, 'side': 10, 'top': 12} | 17/32 | ✓ rim | ✓ top | ✗ side | ✓ top | ✓ rim | ✓ rim | ✗ side | ✗ side |

## pour_water

Feasible per seed: mean 15.3, min 12; distinct feasible sets 28/30; mean Jaccard 0.66; distinct successful sets 27

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 15/30 | 0.50 | 0.33-0.67 | - | 0.50 | 17 | {'top': 14, 'rim': 6, 'side': 10} |
| M1_STATIC_V2 | 17/30 | 0.57 | 0.39-0.73 | 7/5, p=0.77 | 0.43 | 5 | {'side': 17, 'top': 13} |
| M2_TASK_RISK | 17/30 | 0.57 | 0.39-0.73 | 6/4, p=0.75 | 0.43 | 6 | {'side': 26, 'top': 4} |
| M5_PAPER_STYLE | 17/30 | 0.57 | 0.39-0.73 | 7/5, p=0.77 | 0.43 | 5 | {'side': 17, 'top': 13} |
| M6_PROMPT_RULES | 28/30 | 0.93 | 0.79-0.98 | 15/2, p=0.00 | 0.07 | 2 | {'top': 30} |
| M4_FAMILY_PRIOR | 22/30 | 0.73 | 0.56-0.86 | 11/4, p=0.12 | 0.27 | 2 | {'rim': 30} |
| M3_MOCK | 14/30 | 0.47 | 0.30-0.64 | 5/6, p=1.00 | 0.53 | 6 | {'side': 30} |
| A1_MOCK | 14/30 | 0.47 | 0.30-0.64 | 5/6, p=1.00 | 0.53 | 6 | {'side': 30} |
| ORACLE | 30/30 | 1.00 | 0.89-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.48 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M6_PROMPT_RULES | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 16 {'rim': 2, 'side': 6, 'top': 8} | 9/16 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 1 | 14 {'rim': 1, 'side': 6, 'top': 7} | 7/14 | ✓ rim | ✗ side | ✗ side | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 2 | 15 {'rim': 3, 'side': 4, 'top': 8} | 10/15 | ✓ top | ✓ top | ✓ top | ✓ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 3 | 18 {'rim': 2, 'side': 8, 'top': 8} | 9/18 | ✓ top | ✗ side | ✗ side | ✗ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 4 | 17 {'rim': 2, 'side': 7, 'top': 8} | 8/17 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 5 | 13 {'rim': 1, 'side': 5, 'top': 7} | 7/13 | ✗ side | ✓ top | ✓ side | ✓ top | ✓ top | ✓ rim | ✓ side | ✓ side |
| 6 | 18 {'rim': 2, 'side': 8, 'top': 8} | 9/18 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 7 | 14 {'rim': 2, 'side': 5, 'top': 7} | 8/14 | ✗ side | ✓ top | ✓ side | ✓ top | ✓ top | ✓ rim | ✓ side | ✓ side |
| 8 | 17 {'rim': 2, 'side': 7, 'top': 8} | 7/17 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 9 | 14 {'rim': 2, 'side': 4, 'top': 8} | 7/14 | ✗ top | ✓ top | ✗ top | ✓ top | ✓ top | ✓ rim | ✗ side | ✗ side |
| 10 | 17 {'rim': 2, 'side': 7, 'top': 8} | 10/17 | ✓ rim | ✓ side | ✓ side | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 11 | 14 {'rim': 2, 'side': 7, 'top': 5} | 6/14 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ top | ✗ rim | ✓ side | ✓ side |
| 12 | 15 {'rim': 2, 'side': 5, 'top': 8} | 7/15 | ✓ rim | ✗ top | ✓ side | ✗ top | ✗ top | ✓ rim | ✓ side | ✓ side |
| 13 | 15 {'rim': 2, 'side': 6, 'top': 7} | 7/15 | ✗ top | ✓ top | ✓ side | ✓ top | ✓ top | ✓ rim | ✓ side | ✓ side |
| 14 | 16 {'rim': 2, 'side': 7, 'top': 7} | 8/16 | ✗ top | ✓ side | ✓ side | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 15 | 16 {'rim': 4, 'side': 5, 'top': 7} | 8/16 | ✗ side | ✓ top | ✓ side | ✓ top | ✓ top | ✗ rim | ✓ side | ✓ side |
| 16 | 17 {'rim': 2, 'side': 7, 'top': 8} | 10/17 | ✓ rim | ✓ top | ✓ side | ✓ top | ✓ top | ✓ rim | ✓ side | ✓ side |
| 17 | 12 {'rim': 1, 'side': 6, 'top': 5} | 4/12 | ✗ top | ✗ side | ✗ side | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 18 | 16 {'rim': 2, 'side': 8, 'top': 6} | 7/16 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 19 | 15 {'rim': 2, 'side': 5, 'top': 8} | 9/15 | ✓ rim | ✓ top | ✓ side | ✓ top | ✓ top | ✓ rim | ✓ side | ✓ side |
| 20 | 17 {'rim': 3, 'side': 7, 'top': 7} | 8/17 | ✗ rim | ✗ side | ✗ side | ✗ side | ✓ top | ✗ rim | ✗ side | ✗ side |
| 21 | 16 {'rim': 2, 'side': 7, 'top': 7} | 8/16 | ✓ top | ✗ side | ✗ side | ✗ side | ✗ top | ✓ rim | ✗ side | ✗ side |
| 22 | 13 {'rim': 3, 'side': 4, 'top': 6} | 5/13 | ✓ top | ✓ top | ✓ top | ✓ top | ✓ top | ✗ rim | ✗ side | ✗ side |
| 23 | 13 {'rim': 2, 'side': 6, 'top': 5} | 7/13 | ✓ top | ✓ top | ✓ side | ✓ top | ✓ top | ✗ rim | ✓ side | ✓ side |
| 24 | 15 {'rim': 3, 'side': 5, 'top': 7} | 6/15 | ✗ side | ✓ top | ✓ top | ✓ top | ✓ top | ✗ rim | ✗ side | ✗ side |
| 25 | 17 {'rim': 2, 'side': 8, 'top': 7} | 9/17 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ top | ✗ rim | ✓ side | ✓ side |
| 26 | 16 {'rim': 2, 'side': 7, 'top': 7} | 6/16 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ top | ✓ rim | ✓ side | ✓ side |
| 27 | 13 {'rim': 2, 'side': 6, 'top': 5} | 4/13 | ✗ top | ✗ side | ✗ side | ✗ side | ✓ top | ✗ rim | ✗ side | ✗ side |
| 28 | 15 {'rim': 2, 'side': 6, 'top': 7} | 6/15 | ✗ side | ✗ side | ✗ side | ✗ side | ✓ top | ✓ rim | ✗ side | ✗ side |
| 29 | 14 {'rim': 1, 'side': 6, 'top': 7} | 6/14 | ✓ top | ✓ top | ✓ side | ✓ top | ✓ top | ✓ rim | ✗ side | ✗ side |

## transport_place

Feasible per seed: mean 42.5, min 34; distinct feasible sets 29/30; mean Jaccard 0.72; distinct successful sets 30

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 29/30 | 0.97 | 0.83-0.99 | - | 0.03 | 21 | {'handle': 9, 'rim': 9, 'top': 6, 'side': 6} |
| M1_STATIC_V2 | 27/30 | 0.90 | 0.74-0.97 | 1/3, p=0.62 | 0.10 | 4 | {'top': 30} |
| M2_TASK_RISK | 28/30 | 0.93 | 0.79-0.98 | 1/2, p=1.00 | 0.07 | 2 | {'top': 30} |
| M5_PAPER_STYLE | 30/30 | 1.00 | 0.89-1.00 | 1/0, p=1.00 | 0.00 | 2 | {'handle': 30} |
| M6_PROMPT_RULES | 26/30 | 0.87 | 0.70-0.95 | 1/4, p=0.38 | 0.13 | 3 | {'handle': 28, 'rim': 2} |
| M4_FAMILY_PRIOR | 26/30 | 0.87 | 0.70-0.95 | 1/4, p=0.38 | 0.13 | 3 | {'handle': 30} |
| M3_MOCK | 16/30 | 0.53 | 0.36-0.70 | 1/14, p=0.00 | 0.47 | 2 | {'side': 30} |
| A1_MOCK | 16/30 | 0.53 | 0.36-0.70 | 1/14, p=0.00 | 0.47 | 2 | {'side': 30} |
| ORACLE | 30/30 | 1.00 | 0.89-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.80 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M6_PROMPT_RULES | M4_FAMILY_PRIOR | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 50 {'handle': 17, 'rim': 7, 'side': 13, 'top': 13} | 42/50 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 1 | 40 {'handle': 14, 'rim': 6, 'side': 9, 'top': 11} | 31/40 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 2 | 37 {'handle': 4, 'rim': 7, 'side': 12, 'top': 14} | 29/37 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ handle | ✗ side | ✗ side |
| 3 | 50 {'handle': 17, 'rim': 8, 'side': 13, 'top': 12} | 38/50 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 4 | 49 {'handle': 17, 'rim': 8, 'side': 12, 'top': 12} | 40/49 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 5 | 39 {'handle': 11, 'rim': 6, 'side': 10, 'top': 12} | 31/39 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 6 | 51 {'handle': 17, 'rim': 7, 'side': 14, 'top': 13} | 42/51 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 7 | 41 {'handle': 14, 'rim': 6, 'side': 10, 'top': 11} | 16/41 | ✓ top | ✓ top | ✓ top | ✓ handle | ✗ handle | ✗ handle | ✗ side | ✗ side |
| 8 | 43 {'handle': 10, 'rim': 7, 'side': 13, 'top': 13} | 38/43 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✗ handle | ✗ handle | ✓ side | ✓ side |
| 9 | 43 {'handle': 10, 'rim': 7, 'side': 12, 'top': 14} | 30/43 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✗ handle | ✗ handle | ✗ side | ✗ side |
| 10 | 43 {'handle': 14, 'rim': 6, 'side': 13, 'top': 10} | 39/43 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 11 | 38 {'handle': 11, 'rim': 6, 'side': 10, 'top': 11} | 28/38 | ✓ rim | ✗ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 12 | 43 {'handle': 14, 'rim': 6, 'side': 11, 'top': 12} | 35/43 | ✓ rim | ✓ top | ✗ top | ✓ handle | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 13 | 42 {'handle': 14, 'rim': 6, 'side': 12, 'top': 10} | 33/42 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 14 | 40 {'handle': 14, 'rim': 6, 'side': 12, 'top': 8} | 32/40 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 15 | 43 {'handle': 14, 'rim': 7, 'side': 10, 'top': 12} | 39/43 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 16 | 52 {'handle': 17, 'rim': 7, 'side': 15, 'top': 13} | 43/52 | ✓ side | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 17 | 34 {'handle': 11, 'rim': 5, 'side': 9, 'top': 9} | 29/34 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 18 | 48 {'handle': 15, 'rim': 7, 'side': 16, 'top': 10} | 41/48 | ✓ side | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 19 | 51 {'handle': 17, 'rim': 7, 'side': 14, 'top': 13} | 39/51 | ✓ side | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✗ side | ✗ side |
| 20 | 43 {'handle': 14, 'rim': 6, 'side': 11, 'top': 12} | 39/43 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 21 | 41 {'handle': 14, 'rim': 6, 'side': 12, 'top': 9} | 33/41 | ✓ handle | ✗ top | ✗ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 22 | 38 {'handle': 4, 'rim': 7, 'side': 13, 'top': 14} | 33/38 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ rim | ✓ handle | ✗ side | ✗ side |
| 23 | 38 {'handle': 11, 'rim': 6, 'side': 9, 'top': 12} | 31/38 | ✓ top | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 24 | 41 {'handle': 10, 'rim': 7, 'side': 11, 'top': 13} | 28/41 | ✓ side | ✓ top | ✓ top | ✓ handle | ✗ handle | ✗ handle | ✓ side | ✓ side |
| 25 | 39 {'handle': 11, 'rim': 6, 'side': 10, 'top': 12} | 34/39 | ✗ side | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 26 | 45 {'handle': 10, 'rim': 7, 'side': 15, 'top': 13} | 34/45 | ✓ top | ✗ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 27 | 34 {'handle': 11, 'rim': 6, 'side': 8, 'top': 9} | 33/34 | ✓ side | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 28 | 39 {'handle': 14, 'rim': 6, 'side': 11, 'top': 8} | 34/39 | ✓ handle | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✓ side | ✓ side |
| 29 | 39 {'handle': 11, 'rim': 6, 'side': 10, 'top': 12} | 30/39 | ✓ rim | ✓ top | ✓ top | ✓ handle | ✓ handle | ✓ handle | ✗ side | ✗ side |
