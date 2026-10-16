"""Thin C wrapper adapter. No duplicated filter/execution/evaluation logic."""
from .selectors import smoke_selector


def evaluate_smoke(task_id, seed, candidates, *, observation_config=None):
    try:
        from examples.evaluate_one_trial import evaluate_trial
        from grasp_a.simulation import CONTROLLER_VERSION
    except ImportError as exc:
        raise RuntimeError('A/C v0.3 unavailable; synchronize main explicitly before integration') from exc
    if CONTROLLER_VERSION != 'observable_primitives_v2':
        raise RuntimeError('requires reviewed observable_primitives_v2 controller')
    return evaluate_trial(task_id, seed, 'B_SMOKE_ONLY', candidates, smoke_selector,
                          observation_config=observation_config)
