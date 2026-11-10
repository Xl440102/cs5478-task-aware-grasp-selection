# C frozen-table evaluation

Seeds: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29]

Success includes physical execution failures in the denominator; numerical/call/missing outcomes are excluded and counted.
Wilson intervals use scene seeds as units. Holm correction covers all reported comparisons with valid pairs.

## transport_place (rigid-body)

| Method | Success/scored | Planned | 95% Wilson CI | Families |
|---|---:|---:|---|---|
| M0_RANDOM | 15/20 | 20 | 0.531–0.888 | {'handle': 5, 'rim': 4, 'top': 5, 'side': 6} |
| M1_STATIC_V2 | 17/20 | 20 | 0.640–0.948 | {'top': 20} |
| M2_TASK_RISK | 19/20 | 20 | 0.764–0.991 | {'top': 20} |
| M4_FAMILY_PRIOR | 18/20 | 20 | 0.699–0.972 | {'rim': 20} |
| M5_PAPER_STYLE | 20/20 | 20 | 0.839–1.000 | {'handle': 20} |
| M3_CODEX_V2 | 18/20 | 20 | 0.699–0.972 | {'handle': 19, 'top': 1} |
| A1_CODEX_V2 | 16/20 | 20 | 0.584–0.919 | {'handle': 12, 'side': 8} |

| Comparison | Paired n | Wins/losses | Exact p | Holm p |
|---|---:|---:|---:|---:|
| M1_STATIC_V2 vs M0_RANDOM | 20 | 5/3 | 0.7265625 | 1.0 |
| M2_TASK_RISK vs M0_RANDOM | 20 | 5/1 | 0.21875 | 1.0 |
| M4_FAMILY_PRIOR vs M0_RANDOM | 20 | 4/1 | 0.375 | 1.0 |
| M5_PAPER_STYLE vs M0_RANDOM | 20 | 5/0 | 0.0625 | 1.0 |
| M3_CODEX_V2 vs M0_RANDOM | 20 | 4/1 | 0.375 | 1.0 |
| A1_CODEX_V2 vs M0_RANDOM | 20 | 3/2 | 1.0 | 1.0 |
| M3_CODEX_V2 vs A1_CODEX_V2 | 20 | 3/1 | 0.625 | 1.0 |

## handover_transfer (rigid-body)

| Method | Success/scored | Planned | 95% Wilson CI | Families |
|---|---:|---:|---|---|
| M0_RANDOM | 11/20 | 20 | 0.342–0.742 | {'top': 9, 'rim': 4, 'side': 2, 'handle': 5} |
| M1_STATIC_V2 | 5/20 | 20 | 0.112–0.469 | {'side': 13, 'top': 7} |
| M2_TASK_RISK | 11/20 | 20 | 0.342–0.742 | {'side': 20} |
| M4_FAMILY_PRIOR | 17/20 | 20 | 0.640–0.948 | {'rim': 20} |
| M5_PAPER_STYLE | 5/20 | 20 | 0.112–0.469 | {'side': 13, 'top': 7} |
| M3_CODEX_V2 | 20/20 | 20 | 0.839–1.000 | {'rim': 20} |
| A1_CODEX_V2 | 6/20 | 20 | 0.145–0.519 | {'side': 20} |

| Comparison | Paired n | Wins/losses | Exact p | Holm p |
|---|---:|---:|---:|---:|
| M1_STATIC_V2 vs M0_RANDOM | 20 | 3/9 | 0.14599609375 | 1.0 |
| M2_TASK_RISK vs M0_RANDOM | 20 | 6/6 | 1.0 | 1.0 |
| M4_FAMILY_PRIOR vs M0_RANDOM | 20 | 8/2 | 0.109375 | 1.0 |
| M5_PAPER_STYLE vs M0_RANDOM | 20 | 3/9 | 0.14599609375 | 1.0 |
| M3_CODEX_V2 vs M0_RANDOM | 20 | 9/0 | 0.00390625 | 0.09765625 |
| A1_CODEX_V2 vs M0_RANDOM | 20 | 4/9 | 0.266845703125 | 1.0 |
| M3_CODEX_V2 vs A1_CODEX_V2 | 20 | 14/0 | 0.0001220703125 | 0.0032958984375 |

## pour_water (proxy_margin)

| Method | Success/scored | Planned | 95% Wilson CI | Families |
|---|---:|---:|---|---|
| M0_RANDOM | 13/20 | 20 | 0.433–0.819 | {'rim': 5, 'top': 10, 'side': 5} |
| M1_STATIC_V2 | 12/20 | 20 | 0.387–0.781 | {'side': 11, 'top': 9} |
| M2_TASK_RISK | 13/20 | 20 | 0.433–0.819 | {'side': 18, 'top': 2} |
| M4_FAMILY_PRIOR | 14/20 | 20 | 0.481–0.855 | {'rim': 20} |
| M5_PAPER_STYLE | 12/20 | 20 | 0.387–0.781 | {'side': 11, 'top': 9} |
| M3_CODEX_V2 | 18/20 | 20 | 0.699–0.972 | {'top': 20} |
| A1_CODEX_V2 | 3/20 | 20 | 0.052–0.360 | {'side': 20} |

| Comparison | Paired n | Wins/losses | Exact p | Holm p |
|---|---:|---:|---:|---:|
| M1_STATIC_V2 vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| M2_TASK_RISK vs M0_RANDOM | 20 | 3/3 | 1.0 | 1.0 |
| M4_FAMILY_PRIOR vs M0_RANDOM | 20 | 5/4 | 1.0 | 1.0 |
| M5_PAPER_STYLE vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| M3_CODEX_V2 vs M0_RANDOM | 20 | 6/1 | 0.125 | 1.0 |
| A1_CODEX_V2 vs M0_RANDOM | 20 | 0/10 | 0.001953125 | 0.05078125 |
| M3_CODEX_V2 vs A1_CODEX_V2 | 20 | 15/0 | 6.103515625e-05 | 0.001708984375 |

## pour_water_pbf (pbf)

| Method | Success/scored | Planned | 95% Wilson CI | Families |
|---|---:|---:|---|---|
| M0_RANDOM | 0/0 | 20 | — | {'rim': 5, 'top': 10, 'side': 5} |
| M1_STATIC_V2 | 0/0 | 20 | — | {'side': 11, 'top': 9} |
| M2_TASK_RISK | 0/0 | 20 | — | {'side': 18, 'top': 2} |
| M4_FAMILY_PRIOR | 0/0 | 20 | — | {'rim': 20} |
| M5_PAPER_STYLE | 0/0 | 20 | — | {'side': 11, 'top': 9} |
| M3_CODEX_V2 | 0/0 | 20 | — | {'top': 20} |
| A1_CODEX_V2 | 0/0 | 20 | — | {'side': 20} |

| Comparison | Paired n | Wins/losses | Exact p | Holm p |
|---|---:|---:|---:|---:|
| M1_STATIC_V2 vs M0_RANDOM | 0 | 0/0 | None | None |
| M2_TASK_RISK vs M0_RANDOM | 0 | 0/0 | None | None |
| M4_FAMILY_PRIOR vs M0_RANDOM | 0 | 0/0 | None | None |
| M5_PAPER_STYLE vs M0_RANDOM | 0 | 0/0 | None | None |
| M3_CODEX_V2 vs M0_RANDOM | 0 | 0/0 | None | None |
| A1_CODEX_V2 vs M0_RANDOM | 0 | 0/0 | None | None |
| M3_CODEX_V2 vs A1_CODEX_V2 | 0 | 0/0 | None | None |

## pour_water_pbf_table (pbf)

| Method | Success/scored | Planned | 95% Wilson CI | Families |
|---|---:|---:|---|---|
| M0_RANDOM | 16/20 | 20 | 0.584–0.919 | {'rim': 5, 'top': 10, 'side': 5} |
| M1_STATIC_V2 | 13/20 | 20 | 0.433–0.819 | {'side': 11, 'top': 9} |
| M2_TASK_RISK | 15/20 | 20 | 0.531–0.888 | {'side': 18, 'top': 2} |
| M4_FAMILY_PRIOR | 12/20 | 20 | 0.387–0.781 | {'rim': 20} |
| M5_PAPER_STYLE | 13/20 | 20 | 0.433–0.819 | {'side': 11, 'top': 9} |
| M3_CODEX_V2 | 16/20 | 20 | 0.584–0.919 | {'top': 20} |
| A1_CODEX_V2 | 9/20 | 20 | 0.258–0.658 | {'side': 20} |
| ORACLE | 20/20 | 20 | 0.839–1.000 | — |
| UNIFORM_FEASIBLE_EXPECTATION | 12.3/20 (0.614) | 20 | — | — |

| Comparison | Paired n | Wins/losses | Exact p | Holm p |
|---|---:|---:|---:|---:|
| M1_STATIC_V2 vs M0_RANDOM | 20 | 4/7 | 0.548828125 | 1.0 |
| M2_TASK_RISK vs M0_RANDOM | 20 | 4/5 | 1.0 | 1.0 |
| M4_FAMILY_PRIOR vs M0_RANDOM | 20 | 1/5 | 0.21875 | 1.0 |
| M5_PAPER_STYLE vs M0_RANDOM | 20 | 4/7 | 0.548828125 | 1.0 |
| M3_CODEX_V2 vs M0_RANDOM | 20 | 4/4 | 1.0 | 1.0 |
| A1_CODEX_V2 vs M0_RANDOM | 20 | 3/10 | 0.09228515625 | 1.0 |
| M3_CODEX_V2 vs A1_CODEX_V2 | 20 | 9/2 | 0.0654296875 | 1.0 |

## Model-call audit

```json
{
  "M3_CODEX_V2": {
    "planned_n": 60,
    "saved_n": 60,
    "selected_n": 60,
    "errors": 0,
    "live_requests": 62,
    "cache_hits": 0,
    "initial_valid_n": 58,
    "repairs": 2,
    "fallbacks": 1,
    "live_token_usage": {
      "input_tokens": 767352,
      "output_tokens": 39796,
      "reasoning_output_tokens": 13359
    },
    "mean_selection_seconds": 18.328768221500045,
    "synthetic": false
  },
  "A1_CODEX_V2": {
    "planned_n": 60,
    "saved_n": 60,
    "selected_n": 60,
    "errors": 0,
    "live_requests": 60,
    "cache_hits": 0,
    "initial_valid_n": 60,
    "repairs": 0,
    "fallbacks": 0,
    "live_token_usage": {
      "input_tokens": 726022,
      "output_tokens": 37453,
      "reasoning_output_tokens": 12078,
      "cached_input_tokens": 29440
    },
    "mean_selection_seconds": 17.33056086805033,
    "synthetic": false
  }
}
```

- One response per method/task/seed; no repeated-model stability claim.
- M4 training seeds excluded; seeds 10-29 already appeared in upstream analysis, not a blind test.
- Proxy pouring is separate from PBF; pour_water_pbf_table reports full PBF table lookup and Oracle.
- Historical table lacks environment fingerprints; table lookup is retrospective.
