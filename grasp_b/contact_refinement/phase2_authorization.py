"""Exact future-human approval binding; this module never issues approval.

Mock approvals are a separate domain accepted only with unittest.mock runners.
This is an auditable caller boundary, not protection against hostile Python.
"""
from dataclasses import dataclass
from grasp_b.wrench_v2.contacts import _freeze_json, _plain
from grasp_b.wrench_v2.episode_store import StoreError, canonical, digest

PHYSICAL_DOMAIN = 'explicit_future_human_confirmation_v1'
MOCK_DOMAIN = 'mock_only_never_physical_v1'


def check_hash(value, field):
    if not isinstance(value, dict) or value.get(field) != digest({k:v for k,v in value.items() if k != field}):
        raise StoreError('identity mismatch: '+field)


def authorization_request(manifest):
    check_hash(manifest, 'manifest_hash')
    identity = {k:manifest[k] for k in ('version','batch_id','protocol_hash','registry_hash',
        'phase1_plan_hash','ordered_episode_keys','manifest_hash','adapter_identity',
        'resource_identity','physical_configuration_hash','policy_hash','replicate_index')}
    identity['authorization_version'] = 'five_method_exact_subset_authorization_v1'
    return dict(identity=identity, authorization_identity_hash=digest(identity), approval_status='pending',
        physical_execution_authorized=False,
        required_user_confirmation='Authorize only this exact ordered development physical batch.\n'+canonical(identity).decode())


@dataclass(frozen=True)
class Approval:
    """Constructed only from a separately supplied caller confirmation, never a flag."""
    payload: object

    def __post_init__(self):
        p = _plain(self.payload)
        if not isinstance(p, dict) or set(p) != {'domain','authorization_identity_hash','identity','confirmed_by','confirmation_reference','user_confirmation'}:
            raise StoreError('complete structured confirmation required')
        if p['domain'] not in (PHYSICAL_DOMAIN, MOCK_DOMAIN):
            raise StoreError('unknown approval domain')
        if any(not isinstance(p[k], str) or not p[k].strip() for k in ('confirmed_by','confirmation_reference','user_confirmation')):
            raise StoreError('explicit user attribution and full statement required')
        object.__setattr__(self, 'payload', _freeze_json(p))

    def to_dict(self):
        return _plain(self.payload)


def validate_authorization(manifest, approval, *, mock_only=False):
    if not isinstance(approval, Approval):
        raise StoreError('separate exact structured user approval required')
    request = authorization_request(manifest); p = approval.to_dict()
    if (p['domain'] != (MOCK_DOMAIN if mock_only else PHYSICAL_DOMAIN)
        or p['authorization_identity_hash'] != request['authorization_identity_hash']
        or p['identity'] != request['identity']
        or p['user_confirmation'] != request['required_user_confirmation']):
        raise StoreError('authorization identity/order/source/batch/domain/statement mismatch')
    return digest(p)
