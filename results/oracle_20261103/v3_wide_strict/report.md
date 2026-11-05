# Oracle-table experiment

- candidates: multifamily_v3 (208, profile hollow)
- scene: wide_strict = {"name": "wide_strict", "x_range_m": [-0.04, 0.04], "y_range_m": [-0.05, 0.0], "yaw_range_rad": [-1.0471975511965976, 1.0471975511965976], "observation": "noisy_truth", "estimate_position_noise_m": 0.003, "estimate_yaw_noise_deg": 2.0, "ik_restarts": true, "filter_mode": "execution"}
- seeds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]; execute: all; pour_water liquid: proxy

## handover_transfer

Feasible per seed: mean 36.7, min 33; distinct feasible sets 10/10; mean Jaccard 0.68; distinct successful sets 10

| method | success | rate | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|
| M0_RANDOM | 4/10 | 0.40 | 0.60 | 10 | {'side': 6, 'top': 3, 'handle': 1} |
| M1_STATIC_V2 | 4/10 | 0.40 | 0.60 | 4 | {'side': 6, 'top': 4} |
| M2_TASK_RISK | 5/10 | 0.50 | 0.50 | 4 | {'side': 9, 'top': 1} |
| M3_MOCK | 0/10 | 0.00 | 1.00 | 5 | {'side': 10} |
| A1_MOCK | 0/10 | 0.00 | 1.00 | 5 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | - | - | - |
| uniform over feasible (expectation) | | 0.48 | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|
| 0 | 38 {'handle': 4, 'rim': 6, 'side': 13, 'top': 15} | 23/38 | ✗ side | ✓ side | ✓ side | ✗ side | ✗ side |
| 1 | 33 {'handle': 4, 'rim': 6, 'side': 11, 'top': 12} | 14/33 | ✗ side | ✗ side | ✓ side | ✗ side | ✗ side |
| 2 | 36 {'handle': 4, 'rim': 7, 'side': 10, 'top': 15} | 17/36 | ✗ top | ✓ top | ✗ top | ✗ side | ✗ side |
| 3 | 38 {'handle': 4, 'rim': 7, 'side': 13, 'top': 14} | 17/38 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 4 | 37 {'handle': 4, 'rim': 7, 'side': 12, 'top': 14} | 23/37 | ✓ handle | ✓ side | ✓ side | ✗ side | ✗ side |
| 5 | 34 {'handle': 4, 'rim': 6, 'side': 11, 'top': 13} | 16/34 | ✗ side | ✓ top | ✗ side | ✗ side | ✗ side |
| 6 | 42 {'handle': 4, 'rim': 6, 'side': 17, 'top': 15} | 22/42 | ✗ top | ✗ side | ✓ side | ✗ side | ✗ side |
| 7 | 33 {'handle': 4, 'rim': 6, 'side': 11, 'top': 12} | 16/33 | ✓ side | ✗ top | ✓ side | ✗ side | ✗ side |
| 8 | 39 {'handle': 4, 'rim': 7, 'side': 14, 'top': 14} | 17/39 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 9 | 37 {'handle': 4, 'rim': 7, 'side': 11, 'top': 15} | 13/37 | ✓ top | ✗ top | ✗ side | ✗ side | ✗ side |

## pour_water

Feasible per seed: mean 15.6, min 13; distinct feasible sets 10/10; mean Jaccard 0.7; distinct successful sets 10

| method | success | rate | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|
| M0_RANDOM | 6/10 | 0.60 | 0.40 | 9 | {'top': 4, 'rim': 1, 'side': 5} |
| M1_STATIC_V2 | 7/10 | 0.70 | 0.30 | 5 | {'side': 6, 'top': 4} |
| M2_TASK_RISK | 7/10 | 0.70 | 0.30 | 6 | {'side': 8, 'top': 2} |
| M3_MOCK | 7/10 | 0.70 | 0.30 | 4 | {'side': 10} |
| A1_MOCK | 7/10 | 0.70 | 0.30 | 4 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | - | - | - |
| uniform over feasible (expectation) | | 0.67 | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|
| 0 | 16 {'rim': 2, 'side': 6, 'top': 8} | 12/16 | ✓ top | ✓ side | ✓ side | ✓ side | ✓ side |
| 1 | 14 {'rim': 1, 'side': 6, 'top': 7} | 10/14 | ✓ rim | ✓ side | ✓ side | ✓ side | ✓ side |
| 2 | 15 {'rim': 3, 'side': 4, 'top': 8} | 7/15 | ✓ top | ✗ top | ✓ top | ✗ side | ✗ side |
| 3 | 18 {'rim': 2, 'side': 8, 'top': 8} | 12/18 | ✓ top | ✗ side | ✗ side | ✓ side | ✓ side |
| 4 | 17 {'rim': 2, 'side': 7, 'top': 8} | 13/17 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ side |
| 5 | 13 {'rim': 1, 'side': 5, 'top': 7} | 8/13 | ✗ side | ✓ top | ✓ side | ✓ side | ✓ side |
| 6 | 18 {'rim': 2, 'side': 8, 'top': 8} | 13/18 | ✗ side | ✓ side | ✓ side | ✓ side | ✓ side |
| 7 | 14 {'rim': 2, 'side': 5, 'top': 7} | 10/14 | ✗ side | ✓ top | ✓ side | ✓ side | ✓ side |
| 8 | 17 {'rim': 2, 'side': 7, 'top': 8} | 11/17 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 9 | 14 {'rim': 2, 'side': 4, 'top': 8} | 9/14 | ✗ top | ✓ top | ✗ top | ✗ side | ✗ side |

## transport_place

Feasible per seed: mean 48.5, min 41; distinct feasible sets 10/10; mean Jaccard 0.71; distinct successful sets 10

| method | success | rate | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|
| M0_RANDOM | 5/10 | 0.50 | 0.50 | 10 | {'top': 3, 'handle': 4, 'side': 2, 'rim': 1} |
| M1_STATIC_V2 | 5/10 | 0.50 | 0.50 | 2 | {'top': 10} |
| M2_TASK_RISK | 0/10 | 0.00 | 1.00 | 2 | {'top': 10} |
| M3_MOCK | 6/10 | 0.60 | 0.40 | 1 | {'side': 10} |
| A1_MOCK | 6/10 | 0.60 | 0.40 | 1 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | - | - | - |
| uniform over feasible (expectation) | | 0.65 | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|
| 0 | 55 {'handle': 17, 'rim': 7, 'side': 16, 'top': 15} | 36/55 | ✗ top | ✗ top | ✗ top | ✓ side | ✓ side |
| 1 | 42 {'handle': 14, 'rim': 6, 'side': 10, 'top': 12} | 32/42 | ✓ handle | ✓ top | ✗ top | ✓ side | ✓ side |
| 2 | 41 {'handle': 4, 'rim': 7, 'side': 15, 'top': 15} | 24/41 | ✗ side | ✓ top | ✗ top | ✗ side | ✗ side |
| 3 | 55 {'handle': 17, 'rim': 8, 'side': 16, 'top': 14} | 37/55 | ✓ rim | ✗ top | ✗ top | ✓ side | ✓ side |
| 4 | 54 {'handle': 17, 'rim': 8, 'side': 15, 'top': 14} | 40/54 | ✓ handle | ✗ top | ✗ top | ✓ side | ✓ side |
| 5 | 42 {'handle': 11, 'rim': 6, 'side': 12, 'top': 13} | 28/42 | ✗ side | ✓ top | ✗ top | ✗ side | ✗ side |
| 6 | 56 {'handle': 17, 'rim': 7, 'side': 17, 'top': 15} | 38/56 | ✓ top | ✗ top | ✗ top | ✓ side | ✓ side |
| 7 | 44 {'handle': 14, 'rim': 6, 'side': 12, 'top': 12} | 19/44 | ✓ top | ✗ top | ✗ top | ✗ side | ✗ side |
| 8 | 48 {'handle': 10, 'rim': 7, 'side': 17, 'top': 14} | 34/48 | ✗ handle | ✓ top | ✗ top | ✓ side | ✓ side |
| 9 | 48 {'handle': 10, 'rim': 7, 'side': 16, 'top': 15} | 28/48 | ✗ handle | ✓ top | ✗ top | ✗ side | ✗ side |
