# Oracle-table experiment

- candidates: multifamily_v3 (208, profile hollow)
- scene: wide_strict_v2 = {"name": "wide_strict_v2", "x_range_m": [-0.04, 0.04], "y_range_m": [-0.05, 0.0], "yaw_range_rad": [-1.0471975511965976, 1.0471975511965976], "observation": "noisy_truth", "estimate_position_noise_m": 0.003, "estimate_yaw_noise_deg": 2.0, "ik_restarts": true, "filter_mode": "execution", "approach_finger_contact": "final_only"}
- seeds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]; execute: all; pour_water liquid: proxy

## handover_transfer

Feasible per seed: mean 34.0, min 31; distinct feasible sets 10/10; mean Jaccard 0.69; distinct successful sets 10

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 7/10 | 0.70 | 0.40-0.89 | - | 0.30 | 9 | {'side': 3, 'rim': 1, 'top': 4, 'handle': 2} |
| M1_STATIC_V2 | 4/10 | 0.40 | 0.17-0.69 | 1/4, p=0.38 | 0.60 | 4 | {'side': 6, 'top': 4} |
| M2_TASK_RISK | 0/0 | - | - | 0/0, p=1.00 | - | - | - |
| M5_PAPER_STYLE | 4/10 | 0.40 | 0.17-0.69 | 1/4, p=0.38 | 0.60 | 4 | {'side': 6, 'top': 4} |
| M3_MOCK | 0/10 | 0.00 | 0.00-0.28 | 0/7, p=0.02 | 1.00 | 4 | {'side': 10} |
| A1_MOCK | 0/10 | 0.00 | 0.00-0.28 | 0/7, p=0.02 | 1.00 | 4 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | 0.72-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.52 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|
| 0 | 35 {'handle': 4, 'rim': 6, 'side': 12, 'top': 13} | 23/35 | ✓ side | ✓ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ side | ✗ side | ✗ side |
| 1 | 32 {'handle': 4, 'rim': 6, 'side': 11, 'top': 11} | 14/32 | ✓ rim | ✗ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✗ side | ✗ side | ✗ side |
| 2 | 34 {'handle': 4, 'rim': 7, 'side': 9, 'top': 14} | 17/34 | ✓ top | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ top | ✗ side | ✗ side |
| 3 | 35 {'handle': 4, 'rim': 7, 'side': 12, 'top': 12} | 17/35 | ✓ top | ✗ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✗ side | ✗ side | ✗ side |
| 4 | 34 {'handle': 4, 'rim': 7, 'side': 11, 'top': 12} | 23/34 | ✓ top | ✓ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ side | ✗ side | ✗ side |
| 5 | 32 {'handle': 4, 'rim': 6, 'side': 10, 'top': 12} | 16/32 | ✗ side | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ top | ✗ side | ✗ side |
| 6 | 39 {'handle': 4, 'rim': 6, 'side': 16, 'top': 13} | 22/39 | ✗ side | ✗ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✗ side | ✗ side | ✗ side |
| 7 | 31 {'handle': 4, 'rim': 6, 'side': 10, 'top': 11} | 16/31 | ✓ handle | ✗ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✗ top | ✗ side | ✗ side |
| 8 | 35 {'handle': 4, 'rim': 7, 'side': 11, 'top': 13} | 17/35 | ✗ top | ✗ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✗ side | ✗ side | ✗ side |
| 9 | 33 {'handle': 4, 'rim': 7, 'side': 8, 'top': 14} | 13/33 | ✓ handle | ✗ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✗ top | ✗ side | ✗ side |

## pour_water

Feasible per seed: mean 15.6, min 13; distinct feasible sets 10/10; mean Jaccard 0.7; distinct successful sets 10

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 6/10 | 0.60 | 0.31-0.83 | - | 0.40 | 9 | {'top': 4, 'rim': 1, 'side': 5} |
| M1_STATIC_V2 | 7/10 | 0.70 | 0.40-0.89 | 4/3, p=1.00 | 0.30 | 5 | {'side': 6, 'top': 4} |
| M2_TASK_RISK | 0/0 | - | - | 0/0, p=1.00 | - | - | - |
| M5_PAPER_STYLE | 7/10 | 0.70 | 0.40-0.89 | 4/3, p=1.00 | 0.30 | 5 | {'side': 6, 'top': 4} |
| M3_MOCK | 7/10 | 0.70 | 0.40-0.89 | 3/2, p=1.00 | 0.30 | 4 | {'side': 10} |
| A1_MOCK | 7/10 | 0.70 | 0.40-0.89 | 3/2, p=1.00 | 0.30 | 4 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | 0.72-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.67 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|
| 0 | 16 {'rim': 2, 'side': 6, 'top': 8} | 12/16 | ✓ top | ✓ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ side | ✓ side | ✓ side |
| 1 | 14 {'rim': 1, 'side': 6, 'top': 7} | 10/14 | ✓ rim | ✓ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ side | ✓ side | ✓ side |
| 2 | 15 {'rim': 3, 'side': 4, 'top': 8} | 7/15 | ✓ top | ✗ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✗ top | ✗ side | ✗ side |
| 3 | 18 {'rim': 2, 'side': 8, 'top': 8} | 12/18 | ✓ top | ✗ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✗ side | ✓ side | ✓ side |
| 4 | 17 {'rim': 2, 'side': 7, 'top': 8} | 13/17 | ✓ side | ✓ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ side | ✓ side | ✓ side |
| 5 | 13 {'rim': 1, 'side': 5, 'top': 7} | 8/13 | ✗ side | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ top | ✓ side | ✓ side |
| 6 | 18 {'rim': 2, 'side': 8, 'top': 8} | 13/18 | ✗ side | ✓ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ side | ✓ side | ✓ side |
| 7 | 14 {'rim': 2, 'side': 5, 'top': 7} | 10/14 | ✗ side | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ top | ✓ side | ✓ side |
| 8 | 17 {'rim': 2, 'side': 7, 'top': 8} | 11/17 | ✓ side | ✗ side | ? RuntimeError: A source changed: review command mirror before generating estimates | ✗ side | ✗ side | ✗ side |
| 9 | 14 {'rim': 2, 'side': 4, 'top': 8} | 9/14 | ✗ top | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ top | ✗ side | ✗ side |

## transport_place

Feasible per seed: mean 44.3, min 37; distinct feasible sets 10/10; mean Jaccard 0.71; distinct successful sets 10

| method | success | rate | 95% CI | vs M0 (win/loss, p) | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|---|---|
| M0_RANDOM | 10/10 | 1.00 | 0.72-1.00 | - | 0.00 | 9 | {'handle': 4, 'rim': 5, 'top': 1} |
| M1_STATIC_V2 | 8/10 | 0.80 | 0.49-0.94 | 0/2, p=0.50 | 0.20 | 2 | {'top': 10} |
| M2_TASK_RISK | 0/0 | - | - | 0/0, p=1.00 | - | - | - |
| M5_PAPER_STYLE | 10/10 | 1.00 | 0.72-1.00 | 0/0, p=1.00 | 0.00 | 2 | {'handle': 10} |
| M3_MOCK | 6/10 | 0.60 | 0.31-0.83 | 0/4, p=0.12 | 0.40 | 1 | {'side': 10} |
| A1_MOCK | 6/10 | 0.60 | 0.31-0.83 | 0/4, p=0.12 | 0.40 | 1 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | 0.72-1.00 | - | - | - | - |
| uniform over feasible (expectation) | | 0.71 | | | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M5_PAPER_STYLE | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|---|
| 0 | 50 {'handle': 17, 'rim': 7, 'side': 13, 'top': 13} | 36/50 | ✓ handle | ✗ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ handle | ✓ side | ✓ side |
| 1 | 40 {'handle': 14, 'rim': 6, 'side': 9, 'top': 11} | 32/40 | ✓ rim | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ handle | ✓ side | ✓ side |
| 2 | 37 {'handle': 4, 'rim': 7, 'side': 12, 'top': 14} | 24/37 | ✓ handle | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ handle | ✗ side | ✗ side |
| 3 | 50 {'handle': 17, 'rim': 8, 'side': 13, 'top': 12} | 37/50 | ✓ rim | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ handle | ✓ side | ✓ side |
| 4 | 49 {'handle': 17, 'rim': 8, 'side': 12, 'top': 12} | 40/49 | ✓ handle | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ handle | ✓ side | ✓ side |
| 5 | 39 {'handle': 11, 'rim': 6, 'side': 10, 'top': 12} | 28/39 | ✓ rim | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ handle | ✗ side | ✗ side |
| 6 | 51 {'handle': 17, 'rim': 7, 'side': 14, 'top': 13} | 38/51 | ✓ handle | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ handle | ✓ side | ✓ side |
| 7 | 41 {'handle': 14, 'rim': 6, 'side': 10, 'top': 11} | 18/41 | ✓ top | ✗ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ handle | ✗ side | ✗ side |
| 8 | 43 {'handle': 10, 'rim': 7, 'side': 13, 'top': 13} | 34/43 | ✓ rim | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ handle | ✓ side | ✓ side |
| 9 | 43 {'handle': 10, 'rim': 7, 'side': 12, 'top': 14} | 28/43 | ✓ rim | ✓ top | ? RuntimeError: A source changed: review command mirror before generating estimates | ✓ handle | ✗ side | ✗ side |
