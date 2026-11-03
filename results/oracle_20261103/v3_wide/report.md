# Oracle-table experiment

- candidates: multifamily_v3 (208, profile hollow)
- scene: wide = {"name": "wide", "x_range_m": [-0.04, 0.04], "y_range_m": [-0.05, 0.0], "yaw_range_rad": [-1.0471975511965976, 1.0471975511965976], "observation": "noisy_truth", "estimate_position_noise_m": 0.003, "estimate_yaw_noise_deg": 2.0, "ik_restarts": true}
- seeds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]; execute: all; pour_water liquid: proxy

## handover_transfer

Feasible per seed: mean 49.4, min 38; distinct feasible sets 10/10; mean Jaccard 0.67; distinct successful sets 10

| method | success | rate | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|
| M0_RANDOM | 4/10 | 0.40 | 0.60 | 10 | {'rim': 2, 'side': 2, 'handle': 2, 'top': 4} |
| M1_STATIC_V2 | 2/10 | 0.20 | 0.80 | 4 | {'side': 6, 'top': 4} |
| M2_TASK_RISK | 2/10 | 0.20 | 0.80 | 4 | {'side': 10} |
| M3_MOCK | 1/10 | 0.10 | 0.90 | 6 | {'side': 10} |
| A1_MOCK | 1/10 | 0.10 | 0.90 | 6 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | - | - | - |
| uniform over feasible (expectation) | | 0.34 | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|
| 0 | 51 {'handle': 18, 'rim': 6, 'side': 16, 'top': 11} | 21/51 | ✓ rim | ✗ side | ✗ side | ✓ side | ✓ side |
| 1 | 51 {'handle': 25, 'rim': 6, 'side': 11, 'top': 9} | 14/51 | ✗ side | ✗ side | ✓ side | ✗ side | ✗ side |
| 2 | 38 {'handle': 4, 'rim': 7, 'side': 15, 'top': 12} | 17/38 | ✗ handle | ✓ top | ✗ side | ✗ side | ✗ side |
| 3 | 52 {'handle': 21, 'rim': 6, 'side': 15, 'top': 10} | 15/52 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 4 | 55 {'handle': 24, 'rim': 6, 'side': 15, 'top': 10} | 20/55 | ✗ handle | ✗ side | ✗ side | ✗ side | ✗ side |
| 5 | 51 {'handle': 24, 'rim': 6, 'side': 10, 'top': 11} | 16/51 | ✓ top | ✓ top | ✗ side | ✗ side | ✗ side |
| 6 | 57 {'handle': 24, 'rim': 6, 'side': 16, 'top': 11} | 20/57 | ✓ top | ✗ side | ✗ side | ✗ side | ✗ side |
| 7 | 52 {'handle': 25, 'rim': 6, 'side': 11, 'top': 10} | 16/52 | ✗ top | ✗ top | ✓ side | ✗ side | ✗ side |
| 8 | 44 {'handle': 10, 'rim': 7, 'side': 16, 'top': 11} | 16/44 | ✓ top | ✗ side | ✗ side | ✗ side | ✗ side |
| 9 | 43 {'handle': 10, 'rim': 7, 'side': 14, 'top': 12} | 13/43 | ✗ rim | ✗ top | ✗ side | ✗ side | ✗ side |

## pour_water

Feasible per seed: mean 49.4, min 38; distinct feasible sets 10/10; mean Jaccard 0.67; distinct successful sets 10

| method | success | rate | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|
| M0_RANDOM | 2/10 | 0.20 | 0.80 | 10 | {'rim': 2, 'side': 2, 'handle': 2, 'top': 4} |
| M1_STATIC_V2 | 0/10 | 0.00 | 1.00 | 4 | {'side': 6, 'top': 4} |
| M2_TASK_RISK | 0/10 | 0.00 | 1.00 | 4 | {'side': 6, 'top': 4} |
| M3_MOCK | 0/10 | 0.00 | 1.00 | 6 | {'side': 10} |
| A1_MOCK | 0/10 | 0.00 | 1.00 | 6 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | - | - | - |
| uniform over feasible (expectation) | | 0.18 | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|
| 0 | 51 {'handle': 18, 'rim': 6, 'side': 16, 'top': 11} | 10/51 | ✓ rim | ✗ side | ✗ side | ✗ side | ✗ side |
| 1 | 51 {'handle': 25, 'rim': 6, 'side': 11, 'top': 9} | 8/51 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 2 | 38 {'handle': 4, 'rim': 7, 'side': 15, 'top': 12} | 7/38 | ✗ handle | ✗ top | ✗ top | ✗ side | ✗ side |
| 3 | 52 {'handle': 21, 'rim': 6, 'side': 15, 'top': 10} | 10/52 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 4 | 55 {'handle': 24, 'rim': 6, 'side': 15, 'top': 10} | 11/55 | ✗ handle | ✗ side | ✗ side | ✗ side | ✗ side |
| 5 | 51 {'handle': 24, 'rim': 6, 'side': 10, 'top': 11} | 6/51 | ✗ top | ✗ top | ✗ top | ✗ side | ✗ side |
| 6 | 57 {'handle': 24, 'rim': 6, 'side': 16, 'top': 11} | 10/57 | ✓ top | ✗ side | ✗ side | ✗ side | ✗ side |
| 7 | 52 {'handle': 25, 'rim': 6, 'side': 11, 'top': 10} | 9/52 | ✗ top | ✗ top | ✗ top | ✗ side | ✗ side |
| 8 | 44 {'handle': 10, 'rim': 7, 'side': 16, 'top': 11} | 10/44 | ✗ top | ✗ side | ✗ side | ✗ side | ✗ side |
| 9 | 43 {'handle': 10, 'rim': 7, 'side': 14, 'top': 12} | 9/43 | ✗ rim | ✗ top | ✗ top | ✗ side | ✗ side |

## transport_place

Feasible per seed: mean 49.4, min 38; distinct feasible sets 10/10; mean Jaccard 0.67; distinct successful sets 10

| method | success | rate | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|
| M0_RANDOM | 7/10 | 0.70 | 0.30 | 10 | {'rim': 2, 'side': 2, 'handle': 2, 'top': 4} |
| M1_STATIC_V2 | 3/10 | 0.30 | 0.70 | 4 | {'side': 6, 'top': 4} |
| M2_TASK_RISK | 3/10 | 0.30 | 0.70 | 5 | {'side': 6, 'top': 4} |
| M3_MOCK | 2/10 | 0.20 | 0.80 | 6 | {'side': 10} |
| A1_MOCK | 2/10 | 0.20 | 0.80 | 6 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | - | - | - |
| uniform over feasible (expectation) | | 0.62 | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|
| 0 | 51 {'handle': 18, 'rim': 6, 'side': 16, 'top': 11} | 36/51 | ✓ rim | ✗ side | ✗ side | ✗ side | ✗ side |
| 1 | 51 {'handle': 25, 'rim': 6, 'side': 11, 'top': 9} | 32/51 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 2 | 38 {'handle': 4, 'rim': 7, 'side': 15, 'top': 12} | 24/38 | ✓ handle | ✓ top | ✗ top | ✓ side | ✓ side |
| 3 | 52 {'handle': 21, 'rim': 6, 'side': 15, 'top': 10} | 34/52 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 4 | 55 {'handle': 24, 'rim': 6, 'side': 15, 'top': 10} | 39/55 | ✓ handle | ✗ side | ✗ side | ✗ side | ✗ side |
| 5 | 51 {'handle': 24, 'rim': 6, 'side': 10, 'top': 11} | 28/51 | ✓ top | ✓ top | ✓ top | ✗ side | ✗ side |
| 6 | 57 {'handle': 24, 'rim': 6, 'side': 16, 'top': 11} | 36/57 | ✓ top | ✗ side | ✗ side | ✗ side | ✗ side |
| 7 | 52 {'handle': 25, 'rim': 6, 'side': 11, 'top': 10} | 18/52 | ✗ top | ✗ top | ✓ top | ✗ side | ✗ side |
| 8 | 44 {'handle': 10, 'rim': 7, 'side': 16, 'top': 11} | 31/44 | ✓ top | ✗ side | ✗ side | ✗ side | ✗ side |
| 9 | 43 {'handle': 10, 'rim': 7, 'side': 14, 'top': 12} | 28/43 | ✓ rim | ✓ top | ✓ top | ✓ side | ✓ side |
