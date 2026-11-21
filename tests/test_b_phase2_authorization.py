"""Pending requests and domain-isolated MOCK confirmations; never human approval."""
import copy
import unittest
from grasp_b.contact_refinement import phase2 as p
from grasp_b.contact_refinement.phase2_authorization import (Approval,MOCK_DOMAIN,authorization_request,validate_authorization)
from grasp_b.wrench_v2.episode_store import StoreError


def mock_approval(manifest,**changes):
    request=authorization_request(manifest)
    payload=dict(domain=MOCK_DOMAIN,identity=request['identity'],authorization_identity_hash=request['authorization_identity_hash'],
        confirmed_by='unittest.mock only',confirmation_reference='synthetic-no-human-approval',user_confirmation=request['required_user_confirmation'])
    payload.update(changes)
    return Approval(payload)


@unittest.skipUnless(p.RUN_A.exists() and p.RUN_B.exists(), 'optional historical Task09 phase bundles are not included in the source release')
class AuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle=p.load_frozen();cls.canary,cls.remainder=p.prepare_manifests(cls.bundle)

    def test_request_pending_never_an_approval(self):
        r=authorization_request(self.canary)
        self.assertEqual(r['approval_status'],'pending');self.assertFalse(r['physical_execution_authorized'])
        for bad in (r,True,'yes',None,{'authorized':True}):
            with self.assertRaises(StoreError):validate_authorization(self.canary,bad,mock_only=True)

    def test_correct_mock_confirmation_has_disjoint_domain(self):
        a=mock_approval(self.canary)
        validate_authorization(self.canary,a,mock_only=True)
        with self.assertRaises(StoreError):validate_authorization(self.canary,a,mock_only=False)
        with self.assertRaises(TypeError):a.payload['confirmed_by']='changed'

    def test_confirmation_text_and_attribution_required(self):
        for field in ('confirmed_by','confirmation_reference','user_confirmation'):
            with self.assertRaises(StoreError):mock_approval(self.canary,**{field:''})
        for text in ('YES','authorized=True','Task08 authorization'):
            with self.assertRaises(StoreError):validate_authorization(self.canary,mock_approval(self.canary,user_confirmation=text),mock_only=True)

    def test_wrong_source_protocol_config_identity(self):
        request=authorization_request(self.canary)
        for field in ('protocol_hash','registry_hash','phase1_plan_hash','adapter_identity','resource_identity','physical_configuration_hash','replicate_index'):
            identity=copy.deepcopy(request['identity']);identity[field]='wrong'
            with self.assertRaises(StoreError):validate_authorization(self.canary,mock_approval(self.canary,identity=identity),mock_only=True)

    def test_wrong_batch_reordered_partial_authorization(self):
        a=mock_approval(self.canary)
        with self.assertRaises(StoreError):validate_authorization(self.remainder,a,mock_only=True)
        for keys in (list(reversed(self.canary['ordered_episode_keys'])),self.canary['ordered_episode_keys'][:-1]):
            identity=copy.deepcopy(a.to_dict()['identity']);identity['ordered_episode_keys']=keys
            with self.assertRaises(StoreError):validate_authorization(self.canary,mock_approval(self.canary,identity=identity),mock_only=True)

    def test_tampered_manifest_and_unknown_approval_schema(self):
        m=copy.deepcopy(self.canary);m['batch_id']='other'
        with self.assertRaises(StoreError):authorization_request(m)
        d=mock_approval(self.canary).to_dict();d['authorized']=True
        with self.assertRaises(StoreError):Approval(d)
