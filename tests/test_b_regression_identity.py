"""Task09B-V: identity acceptance/rejection without weakening any verifier."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from grasp_b.wrench_v2.motion_prediction import AUDITED_SOURCE_SHA256, audited_sources
from grasp_b.wrench_v2.motion_source_profiles import MAIN_3B95A75_NOMINAL_SOURCES, MAIN_259D308_NOMINAL_SOURCES, is_reviewed_nominal_profile
from grasp_b.wrench_v2.episode_store import EpisodeStore, StoreError
from grasp_b.wrench_v2.geometry import clear_geometry_caches
from tests.wrench_historical_fixture import ROOT, snapshot, historical_filter, historical_filter_context

CACHE = ROOT / 'results/local/task07_shared_feasible_20261106_final'


class SourceIdentityTests(unittest.TestCase):
    def test_current_profile_returns_actual_hashes_not_historical_ones(self):
        actual = {n: hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in AUDITED_SOURCE_SHA256}
        self.assertEqual(audited_sources(), actual)
        self.assertEqual(actual, MAIN_259D308_NOMINAL_SOURCES)
        self.assertTrue(is_reviewed_nominal_profile(MAIN_3B95A75_NOMINAL_SOURCES))
        self.assertNotEqual(actual, AUDITED_SOURCE_SHA256)
        self.assertEqual(AUDITED_SOURCE_SHA256['grasp_a/control.py'],
                         '041b30e75f15f76048f934b66da1b81e4b02c73f093c2c1c31901ca8fad33c9b')

    def test_historical_profile_kept_and_mixed_profile_rejected(self):
        historic, _ = snapshot('69f410adfa672141f50d3a11433a73e07652b94f')
        self.assertEqual(audited_sources(historic), AUDITED_SOURCE_SHA256)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in AUDITED_SOURCE_SHA256:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(ROOT/name,path)
            shutil.copyfile(historic/'grasp_a/control.py',root/'grasp_a/control.py')
            with self.assertRaises(ValueError):audited_sources(root)

    def test_any_changed_file_or_missing_key_rejected(self):
        for changed in AUDITED_SOURCE_SHA256:
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                for name in AUDITED_SOURCE_SHA256:
                    path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes((ROOT/name).read_bytes() + (b'\n# unreviewed\n' if name==changed else b''))
                with self.assertRaises(ValueError):audited_sources(root)
        self.assertFalse(is_reviewed_nominal_profile({'grasp_a/sequence.py':'wrong'}))

    def test_profile_mapping_is_immutable(self):
        with self.assertRaises(TypeError):MAIN_3B95A75_NOMINAL_SOURCES['grasp_a/control.py']='wrong'


@unittest.skipUnless((CACHE/'summary.json').exists(),'historical Task07 cache unavailable')
class HistoricalCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_b_wrench_motion_prediction import pool
        cls.candidates=pool()[0]
        cls.manifest=json.loads((CACHE/'summary.json').read_text())

    def test_correct_history_reads_without_A_or_environment_leak(self):
        before=dict(os.environ)
        with patch('grasp_a.sequence.get_sequence_candidates',side_effect=AssertionError('no filter')):
            record,metrics=historical_filter(CACHE,'pour_water',0,self.candidates)
        self.assertTrue(metrics['cache_hit'])
        self.assertEqual(len(record['feasible_candidates']),16)
        self.assertEqual(before,dict(os.environ))

    def test_wrong_source_config_dependency_and_asset_rejected(self):
        for kind in ('source','config','dependency','asset'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);m=json.loads(json.dumps(self.manifest))
                if kind=='source':m['source_sha256']['grasp_a/sequence.py']='0'*64
                if kind=='config':m['scene_config']['name']='unreviewed'
                if kind=='dependency':m['environment']['numpy']='0.invalid'
                if kind=='asset':
                    item=next(iter(m['static_snapshot']['assets'].values()))
                    p=root/'corrupt-asset';p.write_bytes(b'corrupt')
                    item['asset_path']=str(p)
                (root/'summary.json').write_text(json.dumps(m))
                expected={'source':'stale filter source','config':'configuration mismatch',
                          'dependency':'dependency version mismatch','asset':'stale cached asset'}[kind]
                with self.assertRaisesRegex(ValueError,expected):historical_filter(root,'pour_water',0,self.candidates)

    def test_corrupt_cached_trial_rejected(self):
        m=self.manifest;entry=next(r for r in m['trials'] if (r['task'],r['scene_seed'])==('pour_water',0))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'summary.json').write_text(json.dumps(m))
            p=root/entry['artifact'];p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{malformed')
            with self.assertRaises(ValueError):historical_filter(root,'pour_water',0,self.candidates)

    def test_historical_environment_restored_on_exception(self):
        before=dict(os.environ)
        with self.assertRaises(RuntimeError):
            with historical_filter_context(CACHE):raise RuntimeError('test fixture exception')
        self.assertEqual(before,dict(os.environ))


class CacheIntegrityTests(unittest.TestCase):
    def test_episode_store_corruption_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=EpisodeStore(tmp);store.write_once('fixture.json',{'type':'test only'})
            p=store.path('fixture.json');value=json.loads(p.read_text());value['payload']['type']='corrupt'
            p.write_text(json.dumps(value))
            with self.assertRaises(StoreError):store.read('fixture.json')

    def test_clearing_geometry_caches_preserves_math(self):
        from tests.test_b_contact_refinement_diagnostics import model,SCALE
        from grasp_b.contact_refinement.diagnostics import diagnose
        s,a,b=model()
        first=diagnose(s,a,[0,0,.2,0,0,0],scaling=SCALE,constraints=b).to_dict()
        clear_geometry_caches()
        second=diagnose(s,a,[0,0,.2,0,0,0],scaling=SCALE,constraints=b).to_dict()
        self.assertEqual(first['classification'],second['classification'])
        self.assertEqual(first['matrix_hash'],second['matrix_hash'])
        self.assertAlmostEqual(first['capacity_result']['utilization'],second['capacity_result']['utilization'])
        from tests.test_b_wrench_adapter import StaticAdapterTests,PANDA
        from grasp_b.wrench_v2.geometry import read_gripper_geometry
        StaticAdapterTests.setUpClass();f=StaticAdapterTests();candidate=f.candidate('side')
        spec_before=f.build(candidate).to_dict()
        clear_geometry_caches()
        spec_after=f.build(candidate,gripper=read_gripper_geometry(PANDA)).to_dict()
        self.assertEqual(spec_before,spec_after)

    def test_independent_processes_start_with_empty_geometry_and_lp_caches(self):
        code = '''import json
from grasp_b.wrench_v2.geometry import geometry_cache_info
from grasp_b.wrench_v2.m2 import ExactWrenchCache
print(json.dumps(dict(geometry=geometry_cache_info(),lp=len(ExactWrenchCache()._values)),sort_keys=True))
'''
        values=[json.loads(subprocess.check_output([sys.executable,'-B','-c',code],cwd=ROOT,text=True)) for _ in range(2)]
        self.assertEqual(values[0],values[1])
        self.assertEqual(values[0]['lp'],0)
        self.assertTrue(all(c['currsize']==0 for c in values[0]['geometry'].values()))

    def test_exact_wrench_cache_model_keys_are_isolated(self):
        from tests.test_b_wrench_m2 import fixture
        from grasp_b.wrench_v2.m2 import ExactWrenchCache,score_trajectory
        prepared,motion,dynamics=fixture();cache=ExactWrenchCache()
        a=score_trajectory(prepared,motion,dynamics,cache=cache)
        b=score_trajectory(prepared,motion,dynamics,cache=cache)
        self.assertEqual(a.worst_utilization,b.worst_utilization)
        self.assertEqual(b.timings_s['lp_calls'],0)
        from grasp_b.wrench_v2.m1 import prepare_contact_spec
        changed=prepare_contact_spec(prepared.spec,replace(prepared.config,k=8))
        c=score_trajectory(changed,motion,dynamics,cache=cache)
        self.assertGreater(c.timings_s['lp_calls'],0)
