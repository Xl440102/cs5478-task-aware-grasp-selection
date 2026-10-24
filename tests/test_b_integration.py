"""Part B generation, A feasibility and benchmark integration tests."""
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pybullet_data
from grasp_a.contracts import Candidate, Pose, candidate_sha256
from grasp_a.sequence_contracts import SequenceConfig
from grasp_b.adaptive import AdaptiveConfig, generate_adaptive
from grasp_b.models import ROOT, read_model, Shape
from grasp_b.geometry import tool_orientation, read_geometry
from grasp_b.motion import MotionSample, TaskContext, command_context
from grasp_b.risk import Physics, features, load_sample, static_score, task_risk, rank_feasible, donor_envelopes
from grasp_b.benchmark import agreement, run_benchmark

PANDA=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'


def candidate(height=0.,yaw=0.,cid='arbitrary'):
    q=tool_orientation(yaw,0)
    direction=np.array((math.cos(math.radians(yaw)),math.sin(math.radians(yaw)),0))
    return Candidate(cid,'body','side',.078,0.,Pose((0,0,height),q),
                     Pose(tuple(np.array((0,0,height))-.15*direction),q))


class PartBIntegrationTests(unittest.TestCase):
    def test_real_A_filter_for_both_profiles_and_no_candidate_mutation(self):
        from grasp_a.simulation import get_feasible_candidates
        from grasp_a.sequence import get_sequence_candidates
        for profile,task in [('solid','pour_prep'),('hollow','handover_transfer')]:
            model=read_model(profile,PANDA)
            candidates=generate_adaptive(model,AdaptiveConfig(height_fractions=(.65,),yaw_step_deg=45))
            before=candidate_sha256(candidates)
            filtered=(get_feasible_candidates(0,candidates) if profile=='solid' else get_sequence_candidates(task,0,candidates))
            self.assertEqual(filtered['candidate_sha256'],before)
            self.assertTrue(filtered['feasible_candidates'])
            static=rank_feasible(candidates,filtered['feasible_candidates'],model)
            dynamic=rank_feasible(candidates,filtered['feasible_candidates'],model,context=command_context(task))
            self.assertEqual(set(static['ranking']),set(dynamic['ranking']))
            self.assertEqual(candidate_sha256(candidates),before)


    def test_shorter_adaptive_approach_improves_feasible_coverage_without_new_ids(self):
        from grasp_a.sequence import get_sequence_candidates
        model=read_model('hollow',PANDA)
        candidates=generate_adaptive(model)
        result=get_sequence_candidates('transport_place',0,candidates)
        self.assertEqual(len(candidates),80)
        self.assertEqual(len(result['feasible_candidates']),6)
        self.assertEqual(result['candidate_sha256'],candidate_sha256(candidates))
        self.assertTrue(all(c.id.startswith('B2_') for c in candidates))


    def test_benchmark_coverage_and_unexecuted_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=run_benchmark(Path(tmp)/'run',tasks=('transport_place',),generation=AdaptiveConfig(height_fractions=(.65,),yaw_step_deg=45))
            trial=report['trials'][0]
            self.assertEqual(trial['generation_count'],16)
            self.assertTrue(trial['feasible_count'])
            sets=[set(r['ranking']) for r in trial['methods'].values()]
            self.assertTrue(all(s==sets[0] for s in sets))
            self.assertIsNone(report['summary']['transport_place/M2']['end_to_end_success_rate'])
            self.assertIsNone(agreement(['x'],['x']))
            self.assertEqual(agreement(['x','y'],['y','x']),0)
            with self.assertRaises(FileExistsError): run_benchmark(Path(tmp)/'run')
