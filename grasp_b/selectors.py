"""Public-input utilities. No formal M-method or quality model is defined."""
import math

PUBLIC_FIELDS = {'id', 'region', 'approach', 'gripper_width', 'geometry_score'}


def validate_public(records):
    ids = []
    for record in records:
        if set(record) != PUBLIC_FIELDS:
            raise ValueError('selector accepts only the five public fields')
        if not isinstance(record['id'], str) or not record['id']:
            raise ValueError('ID must be a nonempty string')
        if not all(isinstance(record[k], str) for k in ('region', 'approach')):
            raise ValueError('public labels must be strings')
        if not all(type(record[k]) in (int, float) and math.isfinite(record[k])
                   for k in ('gripper_width', 'geometry_score')):
            raise ValueError('public numeric fields must be finite')
        ids.append(record['id'])
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate public IDs')
    return set(ids)


def validate_ranking(ranking, records):
    allowed = validate_public(records)
    if (not isinstance(ranking, list) or not all(isinstance(i, str) for i in ranking)
            or len(ranking) != len(allowed) or set(ranking) != allowed):
        raise ValueError('ranking must be an exact permutation of feasible IDs')
    return ranking


def deterministic_geometry_ranking(records, *, score_version=None):
    """Utility only. Caller must supply a frozen validated score version.

    Default is disabled; v0.1 zeros are not a geometry method. The version
    assertion is caller-owned provenance, not proof of physical score validity.
    """
    validate_public(records)
    if (not isinstance(score_version, str) or not score_version.strip()
            or 'placeholder' in score_version.lower()):
        raise ValueError('validated score version required; placeholder ranking disabled')
    return [r['id'] for r in sorted(records, key=lambda r: (-r['geometry_score'], r['id']))]


def smoke_selector(task_id, public_candidates, seed):
    """Stable ID-only integration probe, not M0/M1/M2 or a quality baseline."""
    ids = validate_public(public_candidates)
    if not ids:
        raise ValueError('no feasible candidate; caller must skip selection')
    ranking = sorted(ids)
    return {'selected_id': ranking[0], 'ranking': ranking,
            'metadata': {'scope': 'SMOKE_ONLY', 'score_used': False}}
