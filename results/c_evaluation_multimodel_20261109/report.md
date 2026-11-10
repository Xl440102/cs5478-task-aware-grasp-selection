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
| M3_CODEX | 15/20 | 20 | 0.531–0.888 | {'top': 18, 'side': 2} |
| A1_CODEX | 17/20 | 20 | 0.640–0.948 | {'top': 20} |
| M3_CODEX_V2 | 18/20 | 20 | 0.699–0.972 | {'handle': 19, 'top': 1} |
| A1_CODEX_V2 | 16/20 | 20 | 0.584–0.919 | {'handle': 12, 'side': 8} |
| M3_GEMINI | 19/20 | 20 | 0.764–0.991 | {'top': 20} |
| A1_GEMINI | 9/20 | 20 | 0.258–0.658 | {'side': 20} |
| M3_GEMINI_V2 | 19/20 | 20 | 0.764–0.991 | {'handle': 19, 'rim': 1} |
| A1_GEMINI_V2 | 18/20 | 20 | 0.699–0.972 | {'handle': 19, 'side': 1} |

| Comparison | Paired n | Wins/losses | Exact p | Holm p |
|---|---:|---:|---:|---:|
| M1_STATIC_V2 vs M0_RANDOM | 20 | 5/3 | 0.7265625 | 1.0 |
| M2_TASK_RISK vs M0_RANDOM | 20 | 5/1 | 0.21875 | 1.0 |
| M4_FAMILY_PRIOR vs M0_RANDOM | 20 | 4/1 | 0.375 | 1.0 |
| M5_PAPER_STYLE vs M0_RANDOM | 20 | 5/0 | 0.0625 | 1.0 |
| M3_CODEX vs M0_RANDOM | 20 | 4/4 | 1.0 | 1.0 |
| A1_CODEX vs M0_RANDOM | 20 | 5/3 | 0.7265625 | 1.0 |
| M3_CODEX_V2 vs M0_RANDOM | 20 | 4/1 | 0.375 | 1.0 |
| A1_CODEX_V2 vs M0_RANDOM | 20 | 3/2 | 1.0 | 1.0 |
| M3_GEMINI vs M0_RANDOM | 20 | 5/1 | 0.21875 | 1.0 |
| A1_GEMINI vs M0_RANDOM | 20 | 2/8 | 0.109375 | 1.0 |
| M3_GEMINI_V2 vs M0_RANDOM | 20 | 4/0 | 0.125 | 1.0 |
| A1_GEMINI_V2 vs M0_RANDOM | 20 | 4/1 | 0.375 | 1.0 |
| M3_CODEX vs A1_CODEX | 20 | 0/2 | 0.5 | 1.0 |
| M3_CODEX_V2 vs A1_CODEX_V2 | 20 | 3/1 | 0.625 | 1.0 |
| M3_GEMINI vs A1_GEMINI | 20 | 10/0 | 0.001953125 | 0.18359375 |
| M3_GEMINI_V2 vs A1_GEMINI_V2 | 20 | 1/0 | 1.0 | 1.0 |
| M3_CODEX_V2 vs M3_CODEX | 20 | 3/0 | 0.25 | 1.0 |
| A1_CODEX_V2 vs A1_CODEX | 20 | 3/4 | 1.0 | 1.0 |
| M3_GEMINI_V2 vs M3_GEMINI | 20 | 1/1 | 1.0 | 1.0 |
| A1_GEMINI_V2 vs A1_GEMINI | 20 | 11/2 | 0.0224609375 | 1.0 |

## handover_transfer (rigid-body)

| Method | Success/scored | Planned | 95% Wilson CI | Families |
|---|---:|---:|---|---|
| M0_RANDOM | 11/20 | 20 | 0.342–0.742 | {'top': 9, 'rim': 4, 'side': 2, 'handle': 5} |
| M1_STATIC_V2 | 5/20 | 20 | 0.112–0.469 | {'side': 13, 'top': 7} |
| M2_TASK_RISK | 11/20 | 20 | 0.342–0.742 | {'side': 20} |
| M4_FAMILY_PRIOR | 17/20 | 20 | 0.640–0.948 | {'rim': 20} |
| M5_PAPER_STYLE | 5/20 | 20 | 0.112–0.469 | {'side': 13, 'top': 7} |
| M3_CODEX | 4/20 | 20 | 0.081–0.416 | {'side': 18, 'top': 2} |
| A1_CODEX | 5/20 | 20 | 0.112–0.469 | {'side': 13, 'top': 7} |
| M3_CODEX_V2 | 20/20 | 20 | 0.839–1.000 | {'rim': 20} |
| A1_CODEX_V2 | 6/20 | 20 | 0.145–0.519 | {'side': 20} |
| M3_GEMINI | 5/20 | 20 | 0.112–0.469 | {'side': 13, 'top': 7} |
| A1_GEMINI | 1/20 | 20 | 0.009–0.236 | {'side': 20} |
| M3_GEMINI_V2 | 20/20 | 20 | 0.839–1.000 | {'rim': 20} |
| A1_GEMINI_V2 | 4/20 | 20 | 0.081–0.416 | {'side': 20} |

| Comparison | Paired n | Wins/losses | Exact p | Holm p |
|---|---:|---:|---:|---:|
| M1_STATIC_V2 vs M0_RANDOM | 20 | 3/9 | 0.14599609375 | 1.0 |
| M2_TASK_RISK vs M0_RANDOM | 20 | 6/6 | 1.0 | 1.0 |
| M4_FAMILY_PRIOR vs M0_RANDOM | 20 | 8/2 | 0.109375 | 1.0 |
| M5_PAPER_STYLE vs M0_RANDOM | 20 | 3/9 | 0.14599609375 | 1.0 |
| M3_CODEX vs M0_RANDOM | 20 | 3/10 | 0.09228515625 | 1.0 |
| A1_CODEX vs M0_RANDOM | 20 | 3/9 | 0.14599609375 | 1.0 |
| M3_CODEX_V2 vs M0_RANDOM | 20 | 9/0 | 0.00390625 | 0.35546875 |
| A1_CODEX_V2 vs M0_RANDOM | 20 | 4/9 | 0.266845703125 | 1.0 |
| M3_GEMINI vs M0_RANDOM | 20 | 3/9 | 0.14599609375 | 1.0 |
| A1_GEMINI vs M0_RANDOM | 20 | 1/11 | 0.00634765625 | 0.55224609375 |
| M3_GEMINI_V2 vs M0_RANDOM | 20 | 9/0 | 0.00390625 | 0.35546875 |
| A1_GEMINI_V2 vs M0_RANDOM | 20 | 3/10 | 0.09228515625 | 1.0 |
| M3_CODEX vs A1_CODEX | 20 | 2/3 | 1.0 | 1.0 |
| M3_CODEX_V2 vs A1_CODEX_V2 | 20 | 14/0 | 0.0001220703125 | 0.01171875 |
| M3_GEMINI vs A1_GEMINI | 20 | 4/0 | 0.125 | 1.0 |
| M3_GEMINI_V2 vs A1_GEMINI_V2 | 20 | 16/0 | 3.0517578125e-05 | 0.0030517578125 |
| M3_CODEX_V2 vs M3_CODEX | 20 | 16/0 | 3.0517578125e-05 | 0.0030517578125 |
| A1_CODEX_V2 vs A1_CODEX | 20 | 3/2 | 1.0 | 1.0 |
| M3_GEMINI_V2 vs M3_GEMINI | 20 | 15/0 | 6.103515625e-05 | 0.0059814453125 |
| A1_GEMINI_V2 vs A1_GEMINI | 20 | 3/0 | 0.25 | 1.0 |

## pour_water (proxy_margin)

| Method | Success/scored | Planned | 95% Wilson CI | Families |
|---|---:|---:|---|---|
| M0_RANDOM | 13/20 | 20 | 0.433–0.819 | {'rim': 5, 'top': 10, 'side': 5} |
| M1_STATIC_V2 | 12/20 | 20 | 0.387–0.781 | {'side': 11, 'top': 9} |
| M2_TASK_RISK | 13/20 | 20 | 0.433–0.819 | {'side': 18, 'top': 2} |
| M4_FAMILY_PRIOR | 14/20 | 20 | 0.481–0.855 | {'rim': 20} |
| M5_PAPER_STYLE | 12/20 | 20 | 0.387–0.781 | {'side': 11, 'top': 9} |
| M3_CODEX | 7/20 | 20 | 0.181–0.567 | {'side': 20} |
| A1_CODEX | 12/20 | 20 | 0.387–0.781 | {'side': 12, 'top': 8} |
| M3_CODEX_V2 | 18/20 | 20 | 0.699–0.972 | {'top': 20} |
| A1_CODEX_V2 | 3/20 | 20 | 0.052–0.360 | {'side': 20} |
| M3_GEMINI | 9/20 | 20 | 0.258–0.658 | {'side': 20} |
| A1_GEMINI | 9/20 | 20 | 0.258–0.658 | {'side': 20} |
| M3_GEMINI_V2 | 18/20 | 20 | 0.699–0.972 | {'top': 20} |
| A1_GEMINI_V2 | 4/20 | 20 | 0.081–0.416 | {'side': 20} |

| Comparison | Paired n | Wins/losses | Exact p | Holm p |
|---|---:|---:|---:|---:|
| M1_STATIC_V2 vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| M2_TASK_RISK vs M0_RANDOM | 20 | 3/3 | 1.0 | 1.0 |
| M4_FAMILY_PRIOR vs M0_RANDOM | 20 | 5/4 | 1.0 | 1.0 |
| M5_PAPER_STYLE vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| M3_CODEX vs M0_RANDOM | 20 | 2/8 | 0.109375 | 1.0 |
| A1_CODEX vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| M3_CODEX_V2 vs M0_RANDOM | 20 | 6/1 | 0.125 | 1.0 |
| A1_CODEX_V2 vs M0_RANDOM | 20 | 0/10 | 0.001953125 | 0.18359375 |
| M3_GEMINI vs M0_RANDOM | 20 | 2/6 | 0.2890625 | 1.0 |
| A1_GEMINI vs M0_RANDOM | 20 | 2/6 | 0.2890625 | 1.0 |
| M3_GEMINI_V2 vs M0_RANDOM | 20 | 6/1 | 0.125 | 1.0 |
| A1_GEMINI_V2 vs M0_RANDOM | 20 | 0/9 | 0.00390625 | 0.35546875 |
| M3_CODEX vs A1_CODEX | 20 | 1/6 | 0.125 | 1.0 |
| M3_CODEX_V2 vs A1_CODEX_V2 | 20 | 15/0 | 6.103515625e-05 | 0.0059814453125 |
| M3_GEMINI vs A1_GEMINI | 20 | 0/0 | 1.0 | 1.0 |
| M3_GEMINI_V2 vs A1_GEMINI_V2 | 20 | 14/0 | 0.0001220703125 | 0.01171875 |
| M3_CODEX_V2 vs M3_CODEX | 20 | 12/1 | 0.00341796875 | 0.314453125 |
| A1_CODEX_V2 vs A1_CODEX | 20 | 0/9 | 0.00390625 | 0.35546875 |
| M3_GEMINI_V2 vs M3_GEMINI | 20 | 10/1 | 0.01171875 | 1.0 |
| A1_GEMINI_V2 vs A1_GEMINI | 20 | 0/5 | 0.0625 | 1.0 |

## pour_water_pbf (pbf)

| Method | Success/scored | Planned | 95% Wilson CI | Families |
|---|---:|---:|---|---|
| M0_RANDOM | 16/20 | 20 | 0.584–0.919 | {'rim': 5, 'top': 10, 'side': 5} |
| M1_STATIC_V2 | 15/20 | 20 | 0.531–0.888 | {'side': 11, 'top': 9} |
| M2_TASK_RISK | 15/20 | 20 | 0.531–0.888 | {'side': 18, 'top': 2} |
| M4_FAMILY_PRIOR | 13/20 | 20 | 0.433–0.819 | {'rim': 20} |
| M5_PAPER_STYLE | 15/20 | 20 | 0.531–0.888 | {'side': 11, 'top': 9} |
| M3_CODEX | 14/20 | 20 | 0.481–0.855 | {'side': 20} |
| A1_CODEX | 15/20 | 20 | 0.531–0.888 | {'side': 12, 'top': 8} |
| M3_CODEX_V2 | 17/20 | 20 | 0.640–0.948 | {'top': 20} |
| A1_CODEX_V2 | 11/20 | 20 | 0.342–0.742 | {'side': 20} |
| M3_GEMINI | 15/20 | 20 | 0.531–0.888 | {'side': 20} |
| A1_GEMINI | 15/20 | 20 | 0.531–0.888 | {'side': 20} |
| M3_GEMINI_V2 | 17/20 | 20 | 0.640–0.948 | {'top': 20} |
| A1_GEMINI_V2 | 13/20 | 20 | 0.433–0.819 | {'side': 20} |

| Comparison | Paired n | Wins/losses | Exact p | Holm p |
|---|---:|---:|---:|---:|
| M1_STATIC_V2 vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| M2_TASK_RISK vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| M4_FAMILY_PRIOR vs M0_RANDOM | 20 | 2/5 | 0.453125 | 1.0 |
| M5_PAPER_STYLE vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| M3_CODEX vs M0_RANDOM | 20 | 2/4 | 0.6875 | 1.0 |
| A1_CODEX vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| M3_CODEX_V2 vs M0_RANDOM | 20 | 4/3 | 1.0 | 1.0 |
| A1_CODEX_V2 vs M0_RANDOM | 20 | 2/7 | 0.1796875 | 1.0 |
| M3_GEMINI vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| A1_GEMINI vs M0_RANDOM | 20 | 3/4 | 1.0 | 1.0 |
| M3_GEMINI_V2 vs M0_RANDOM | 20 | 4/3 | 1.0 | 1.0 |
| A1_GEMINI_V2 vs M0_RANDOM | 20 | 3/6 | 0.5078125 | 1.0 |
| M3_CODEX vs A1_CODEX | 20 | 2/3 | 1.0 | 1.0 |
| M3_CODEX_V2 vs A1_CODEX_V2 | 20 | 9/3 | 0.14599609375 | 1.0 |
| M3_GEMINI vs A1_GEMINI | 20 | 0/0 | 1.0 | 1.0 |
| M3_GEMINI_V2 vs A1_GEMINI_V2 | 20 | 7/3 | 0.34375 | 1.0 |
| M3_CODEX_V2 vs M3_CODEX | 20 | 6/3 | 0.5078125 | 1.0 |
| A1_CODEX_V2 vs A1_CODEX | 20 | 2/6 | 0.2890625 | 1.0 |
| M3_GEMINI_V2 vs M3_GEMINI | 20 | 5/3 | 0.7265625 | 1.0 |
| A1_GEMINI_V2 vs A1_GEMINI | 20 | 0/2 | 0.5 | 1.0 |

## pour_water_pbf_table (pbf)

| Method | Success/scored | Planned | 95% Wilson CI | Families |
|---|---:|---:|---|---|
| M0_RANDOM | 16/20 | 20 | 0.584–0.919 | {'rim': 5, 'top': 10, 'side': 5} |
| M1_STATIC_V2 | 13/20 | 20 | 0.433–0.819 | {'side': 11, 'top': 9} |
| M2_TASK_RISK | 15/20 | 20 | 0.531–0.888 | {'side': 18, 'top': 2} |
| M4_FAMILY_PRIOR | 12/20 | 20 | 0.387–0.781 | {'rim': 20} |
| M5_PAPER_STYLE | 13/20 | 20 | 0.433–0.819 | {'side': 11, 'top': 9} |
| M3_CODEX | 13/20 | 20 | 0.433–0.819 | {'side': 20} |
| A1_CODEX | 13/20 | 20 | 0.433–0.819 | {'side': 12, 'top': 8} |
| M3_CODEX_V2 | 16/20 | 20 | 0.584–0.919 | {'top': 20} |
| A1_CODEX_V2 | 9/20 | 20 | 0.258–0.658 | {'side': 20} |
| M3_GEMINI | 14/20 | 20 | 0.481–0.855 | {'side': 20} |
| A1_GEMINI | 14/20 | 20 | 0.481–0.855 | {'side': 20} |
| M3_GEMINI_V2 | 16/20 | 20 | 0.584–0.919 | {'top': 20} |
| A1_GEMINI_V2 | 11/20 | 20 | 0.342–0.742 | {'side': 20} |
| ORACLE | 20/20 | 20 | 0.839–1.000 | — |
| UNIFORM_FEASIBLE_EXPECTATION | 12.3/20 (0.614) | 20 | — | — |

| Comparison | Paired n | Wins/losses | Exact p | Holm p |
|---|---:|---:|---:|---:|
| M1_STATIC_V2 vs M0_RANDOM | 20 | 4/7 | 0.548828125 | 1.0 |
| M2_TASK_RISK vs M0_RANDOM | 20 | 4/5 | 1.0 | 1.0 |
| M4_FAMILY_PRIOR vs M0_RANDOM | 20 | 1/5 | 0.21875 | 1.0 |
| M5_PAPER_STYLE vs M0_RANDOM | 20 | 4/7 | 0.548828125 | 1.0 |
| M3_CODEX vs M0_RANDOM | 20 | 3/6 | 0.5078125 | 1.0 |
| A1_CODEX vs M0_RANDOM | 20 | 4/7 | 0.548828125 | 1.0 |
| M3_CODEX_V2 vs M0_RANDOM | 20 | 4/4 | 1.0 | 1.0 |
| A1_CODEX_V2 vs M0_RANDOM | 20 | 3/10 | 0.09228515625 | 1.0 |
| M3_GEMINI vs M0_RANDOM | 20 | 4/6 | 0.75390625 | 1.0 |
| A1_GEMINI vs M0_RANDOM | 20 | 4/6 | 0.75390625 | 1.0 |
| M3_GEMINI_V2 vs M0_RANDOM | 20 | 4/4 | 1.0 | 1.0 |
| A1_GEMINI_V2 vs M0_RANDOM | 20 | 4/9 | 0.266845703125 | 1.0 |
| M3_CODEX vs A1_CODEX | 20 | 2/2 | 1.0 | 1.0 |
| M3_CODEX_V2 vs A1_CODEX_V2 | 20 | 9/2 | 0.0654296875 | 1.0 |
| M3_GEMINI vs A1_GEMINI | 20 | 0/0 | 1.0 | 1.0 |
| M3_GEMINI_V2 vs A1_GEMINI_V2 | 20 | 7/2 | 0.1796875 | 1.0 |
| M3_CODEX_V2 vs M3_CODEX | 20 | 6/3 | 0.5078125 | 1.0 |
| A1_CODEX_V2 vs A1_CODEX | 20 | 1/5 | 0.21875 | 1.0 |
| M3_GEMINI_V2 vs M3_GEMINI | 20 | 5/3 | 0.7265625 | 1.0 |
| A1_GEMINI_V2 vs A1_GEMINI | 20 | 0/3 | 0.25 | 1.0 |

## Model-call audit

```json
{
  "M3_CODEX": {
    "planned_n": 60,
    "saved_n": 60,
    "selected_n": 60,
    "errors": 0,
    "live_requests": 61,
    "cache_hits": 0,
    "initial_valid_n": 59,
    "repairs": 1,
    "fallbacks": 0,
    "live_token_usage": {
      "input_tokens": 737065,
      "output_tokens": 37538,
      "reasoning_output_tokens": 11705,
      "cached_input_tokens": 7552
    },
    "mean_selection_seconds": 16.43205480412386,
    "synthetic": false
  },
  "A1_CODEX": {
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
      "input_tokens": 719464,
      "output_tokens": 30668,
      "reasoning_output_tokens": 5320,
      "cached_input_tokens": 14976
    },
    "mean_selection_seconds": 14.21048732146543,
    "synthetic": false
  },
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
  },
  "M3_GEMINI": {
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
      "input_tokens": 147219,
      "output_tokens": 12490,
      "reasoning_output_tokens": 3840
    },
    "mean_selection_seconds": 3.47,
    "synthetic": false
  },
  "A1_GEMINI": {
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
      "input_tokens": 143572,
      "output_tokens": 12490,
      "reasoning_output_tokens": 3840
    },
    "mean_selection_seconds": 2.87,
    "synthetic": false
  },
  "M3_GEMINI_V2": {
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
      "input_tokens": 163592,
      "output_tokens": 12490,
      "reasoning_output_tokens": 3840
    },
    "mean_selection_seconds": 3.47,
    "synthetic": false
  },
  "A1_GEMINI_V2": {
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
      "input_tokens": 150846,
      "output_tokens": 12490,
      "reasoning_output_tokens": 3840
    },
    "mean_selection_seconds": 2.87,
    "synthetic": false
  }
}
```

- One response per method/task/seed; no repeated-model stability claim.
- M4 training seeds excluded; seeds 10-29 already appeared in upstream analysis, not a blind test.
- Proxy pouring is separate from PBF; pour_water_pbf_table reports full PBF table lookup and Oracle.
- Historical table lacks environment fingerprints; table lookup is retrospective.
