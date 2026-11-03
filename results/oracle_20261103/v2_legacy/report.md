# Oracle-table experiment

- candidates: adaptive_v2 (80, profile hollow)
- scene: legacy = {"name": "legacy", "x_range_m": [-0.008, 0.008], "y_range_m": [-0.008, 0.008], "yaw_range_rad": [-0.08, 0.08], "observation": "prior", "estimate_position_noise_m": 0.0, "estimate_yaw_noise_deg": 0.0, "ik_restarts": false}
- seeds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]; execute: all; pour_water liquid: proxy

## transport_place

Feasible per seed: mean 6.0, min 6; distinct feasible sets 1/10; mean Jaccard 1.0; distinct successful sets 3

| method | success | rate | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|
| M0_RANDOM | 9/10 | 0.90 | 0.10 | 6 | {'side': 10} |
| M1_STATIC_V2 | 0/10 | 0.00 | 1.00 | 1 | {'side': 10} |
| M2_TASK_RISK | 0/10 | 0.00 | 1.00 | 1 | {'side': 10} |
| M3_MOCK | 0/10 | 0.00 | 1.00 | 1 | {'side': 10} |
| A1_MOCK | 0/10 | 0.00 | 1.00 | 1 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | - | - | - |
| uniform over feasible (expectation) | | 0.73 | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|
| 0 | 6 {'side': 6} | 5/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 1 | 6 {'side': 6} | 4/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 2 | 6 {'side': 6} | 5/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 3 | 6 {'side': 6} | 4/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 4 | 6 {'side': 6} | 4/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 5 | 6 {'side': 6} | 5/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 6 | 6 {'side': 6} | 5/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 7 | 6 {'side': 6} | 4/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 8 | 6 {'side': 6} | 4/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 9 | 6 {'side': 6} | 4/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |

## handover_transfer

Feasible per seed: mean 6.0, min 6; distinct feasible sets 1/10; mean Jaccard 1.0; distinct successful sets 3

| method | success | rate | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|
| M0_RANDOM | 9/10 | 0.90 | 0.10 | 6 | {'side': 10} |
| M1_STATIC_V2 | 8/10 | 0.80 | 0.20 | 1 | {'side': 10} |
| M2_TASK_RISK | 8/10 | 0.80 | 0.20 | 1 | {'side': 10} |
| M3_MOCK | 8/10 | 0.80 | 0.20 | 1 | {'side': 10} |
| A1_MOCK | 8/10 | 0.80 | 0.20 | 1 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | - | - | - |
| uniform over feasible (expectation) | | 0.83 | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|
| 0 | 6 {'side': 6} | 6/6 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ side |
| 1 | 6 {'side': 6} | 3/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 2 | 6 {'side': 6} | 6/6 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ side |
| 3 | 6 {'side': 6} | 5/6 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ side |
| 4 | 6 {'side': 6} | 5/6 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ side |
| 5 | 6 {'side': 6} | 6/6 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ side |
| 6 | 6 {'side': 6} | 6/6 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ side |
| 7 | 6 {'side': 6} | 5/6 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ side |
| 8 | 6 {'side': 6} | 3/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 9 | 6 {'side': 6} | 5/6 | ✓ side | ✓ side | ✓ side | ✓ side | ✓ side |

## pour_water

Feasible per seed: mean 6.0, min 6; distinct feasible sets 1/10; mean Jaccard 1.0; distinct successful sets 1

| method | success | rate | regret vs Oracle | distinct picks | picked families |
|---|---|---|---|---|---|
| M0_RANDOM | 1/10 | 0.10 | 0.90 | 6 | {'side': 10} |
| M1_STATIC_V2 | 0/10 | 0.00 | 1.00 | 1 | {'side': 10} |
| M2_TASK_RISK | 0/10 | 0.00 | 1.00 | 1 | {'side': 10} |
| M3_MOCK | 0/10 | 0.00 | 1.00 | 1 | {'side': 10} |
| A1_MOCK | 0/10 | 0.00 | 1.00 | 1 | {'side': 10} |
| ORACLE | 10/10 | 1.00 | - | - | - |
| uniform over feasible (expectation) | | 0.17 | | | |

| seed | feasible (by family) | succeeded/executed | M0_RANDOM | M1_STATIC_V2 | M2_TASK_RISK | M3_MOCK | A1_MOCK |
|---|---|---|---|---|---|---|---|
| 0 | 6 {'side': 6} | 1/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 1 | 6 {'side': 6} | 1/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 2 | 6 {'side': 6} | 1/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 3 | 6 {'side': 6} | 1/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 4 | 6 {'side': 6} | 1/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 5 | 6 {'side': 6} | 1/6 | ✓ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 6 | 6 {'side': 6} | 1/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 7 | 6 {'side': 6} | 1/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 8 | 6 {'side': 6} | 1/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
| 9 | 6 {'side': 6} | 1/6 | ✗ side | ✗ side | ✗ side | ✗ side | ✗ side |
