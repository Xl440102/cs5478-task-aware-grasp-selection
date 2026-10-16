"""Experimental score components, not an aggregate or selector input.

COM, alignment and symmetry are experimental components. No Candidate mutation, ranking, task
weights, simulation calls or episode data. Half body height is a transparent
reference length, NOT a maximum COM distance or stability threshold.
"""
import math

from .features import VERSION as FEATURE_VERSION, candidate_features

COM_SCORE_VERSION = 'com_distance_linear_half_height_v0.1'
FORMULA = 'max(0, 1 - distance_m / reference_distance_m)'


def _finite_number(value):
    # Exclude booleans and strings rather than silently treating them as metres.
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def com_distance_score(distance_m, *, reference_distance_m):
    """Normalize a nonnegative distance, with an explicit positive scale.

    Missing/unusable inputs produce score=None plus a reason. Valid distances
    at/above the reference length produce score=0, not an error. Invalid raw
    numeric inputs are sanitized to None to keep strict JSON serialization safe.
    """
    result = {
        'component': 'com_distance', 'version': COM_SCORE_VERSION,
        'usage': 'experimental_component_not_formal_ranking',
        'formula': FORMULA, 'score': None, 'score_unit': '1',
        'distance_m': distance_m if _finite_number(distance_m) else None,
        'reference_distance_m': (reference_distance_m
                                 if _finite_number(reference_distance_m) else None),
        'status': 'invalid_input', 'reason': None,
    }
    if not _finite_number(reference_distance_m) or reference_distance_m <= 0:
        result['reason'] = 'reference_distance_m_must_be_finite_positive'
    elif distance_m is None:
        result.update(status='unavailable', reason='distance_missing_or_contact_model_unsupported')
    elif not _finite_number(distance_m) or distance_m < 0:
        result['reason'] = 'distance_m_must_be_finite_nonnegative'
    else:
        # Avoid overflow in d/L for very large finite d or very small finite L.
        result.update(score=0.0 if distance_m >= reference_distance_m
                      else 1.0-distance_m/reference_distance_m,
                      status='ok', reason=None)
    return result


def score_candidate_com(candidate, geometry):
    """Compute the component from static candidate/URDF geometry only.

    Reuses candidate_features; does not recalculate contacts with a competing
    model. Geometry COM is the URDF inertial origin, not a measured COM.
    """
    valid_geometry = (
        _finite_number(geometry.radius) and geometry.radius > 0
        and _finite_number(geometry.height) and geometry.height > 0
        and isinstance(geometry.com, (tuple, list)) and len(geometry.com) == 3
        and all(_finite_number(x) for x in geometry.com))
    if not valid_geometry:
        result = com_distance_score(None, reference_distance_m=None)
        result.update(status='invalid_geometry', reason='finite_positive_body_and_finite_com_required')
        return result

    scale = geometry.height / 2
    try:
        features = candidate_features(candidate, geometry)
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as exc:
        result = com_distance_score(None, reference_distance_m=scale)
        result.update(status='invalid_candidate', reason=type(exc).__name__)
        return result
    result = com_distance_score(features['values']['contact_midpoint_com_distance_m'],
                                reference_distance_m=scale)
    result.update(
        candidate_id=candidate.id,
        feature_version=FEATURE_VERSION,
        feature_name='contact_midpoint_com_distance_m',
        contact_model_status=features['contact_model_status'],
        provenance={
            'distance': 'norm(ideal_contact_midpoint - URDF mug inertial origin)',
            'reference_length': 'geometry.height / 2; static URDF body collision cylinder',
            'input_frame': 'mug-local; distances in metres',
            'limitations': 'ideal body point contacts only; no forces, friction or stability proof',
        },
    )
    return result


GEOMETRY_SCORE_VERSION = 'experimental_geometry_weighted_v0.1'
COMPONENTS = ('alignment', 'symmetry', 'com_distance')


def unit_interval_score(value, *, component):
    """Existing alignment/symmetry proxies are already normalized to [0,1].

    Only 1e-12 floating point overshoot is clamped. Missing/invalid values
    remain unavailable; this does not recalculate contact geometry.
    """
    if component not in ('alignment', 'symmetry'):
        raise ValueError('expected alignment or symmetry')
    result = dict(component=component, version='unit_interval_proxy_v0.1',
                  formula='identity on [0,1]; clamp numerical overshoot <= 1e-12',
                  score=None, raw_value=value if _finite_number(value) else None,
                  status='invalid_input')
    if value is None:
        result['status'] = 'unavailable'
    elif _finite_number(value) and -1e-12 <= value <= 1+1e-12:
        result.update(score=min(1.0, max(0.0, value)), status='ok')
    return result


def normalized_weights(weights):
    if not isinstance(weights, dict) or set(weights) != set(COMPONENTS):
        raise ValueError('weights must specify alignment, symmetry and com_distance')
    if any(not _finite_number(v) or v < 0 for v in weights.values()):
        raise ValueError('weights must be finite nonnegative numbers')
    maximum = max(weights.values())
    if maximum == 0:
        raise ValueError('at least one weight must be positive')
    scaled = {k: weights[k]/maximum for k in COMPONENTS}
    total = math.fsum(scaled.values())
    return {k: scaled[k]/total for k in COMPONENTS}


def score_geometry(candidate, geometry, weights):
    """One static feature calculation, strict all-components-required policy.

    Even zero-weight missing components invalidate the row, to avoid implicit
    imputation or candidate-dependent reweighting. Invalid rows are not exported
    as score-bearing candidates by the experimental prototype.
    """
    normalized = normalized_weights(weights)
    result = dict(candidate_id=candidate.id, version=GEOMETRY_SCORE_VERSION,
                  scope='experimental_smoke_only_not_formal_M1', score=None,
                  formula='sum(normalized_weight[k] * component_score[k])',
                  parameters={'weights': dict(weights), 'normalized_weights': normalized,
                              'com_reference': 'body_height/2',
                              'missing_policy': 'require_all_components'},
                  feature_version=FEATURE_VERSION, breakdown={}, status='invalid_geometry')
    if not (_finite_number(geometry.radius) and geometry.radius > 0
            and _finite_number(geometry.height) and geometry.height > 0
            and isinstance(geometry.com, (tuple, list)) and len(geometry.com) == 3
            and all(_finite_number(x) for x in geometry.com)):
        return result
    try:
        features = candidate_features(candidate, geometry)
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as exc:
        result.update(status='invalid_candidate', reason=type(exc).__name__)
        return result
    values = features['values']
    breakdown = dict(
        alignment=unit_interval_score(values['contact_alignment_proxy'], component='alignment'),
        symmetry=unit_interval_score(values['contact_symmetry_proxy'], component='symmetry'),
        com_distance=com_distance_score(values['contact_midpoint_com_distance_m'],
                                        reference_distance_m=geometry.height/2))
    result.update(breakdown=breakdown, contact_model_status=features['contact_model_status'],
                  provenance={'input': 'static mug-local Candidate and URDF geometry only',
                              'contact_model': 'existing features.candidate_features; ideal cylinder points',
                              'com': 'URDF inertial origin',
                              'limitations': 'no forces, friction, execution outcomes or task suitability'})
    if all(row['status'] == 'ok' for row in breakdown.values()):
        result.update(status='ok', score=min(1.0, max(0.0, math.fsum(
            normalized[k]*breakdown[k]['score'] for k in COMPONENTS))))
    else:
        result.update(status='unavailable', reason='all three valid component scores required')
    return result
