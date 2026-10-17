"""New sequence contracts, actual support transfer and scoring negatives."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from grasp_a.contracts import TASKS, load_candidates, Pose
from grasp_a.sequence_contracts import SequenceConfig, SEQUENCE_TASKS, task_spec
try:
    import numpy as np
    from grasp_a.fluid import PositionBasedFluid, Vessel
    from grasp_a.sequence_evaluation import terminal_checks
    from grasp_a.sequence import SequenceScene, run_sequence, p, SequenceFailure
except ImportError:
    p = None

ROOT = Path(__file__).resolve().parents[1]


class SequenceContractTests(unittest.TestCase):
    def test_old_task_ids_unchanged_and_stages_defined(self):
        self.assertEqual(TASKS,("pour_prep","handover_prep"))
        for task in SEQUENCE_TASKS:
            spec = task_spec(task)
            ids = [s["id"] for s in spec["stages"]]
            self.assertEqual(len(ids),len(set(ids)))
            self.assertEqual(ids[-1],"terminal")
            for stage in spec["stages"]:
                for field in ("entry","action","control_exit","evaluation","actor","timeout_s"):
                    self.assertTrue(stage[field])
            json.dumps(spec,allow_nan=False)

    def test_bad_config_rejected(self):
        for change in ({"target_xy":(2,0)},{"terminal_window_s":0},{"fluid_particles":63},
                       {"fluid_substeps":9},{"pour_angle_deg":35},{"water_mass_kg":float("nan")}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                SequenceConfig(**change)
        SequenceConfig(spill_max_ratio=0)

    @unittest.skipIf(p is None,"physics/numerical environment not installed")
    def test_water_must_be_received_not_just_not_spilled(self):
        c = SequenceConfig()
        e = dict(source_upright_deg=0,source_linear_speed_m_s=0,source_angular_speed_deg_s=0,
            donor_contact=True,source_goal_error_m=0,target_upright_deg=0,
            target_linear_speed_m_s=0,target_angular_speed_deg_s=0,target_displacement_m=0,
            water=dict(transfer_ratio=0,spill_ratio=0,finite_state=True,accounting_error_kg=0,max_compression=0))
        self.assertFalse(terminal_checks("pour_water",c,e)["received"])
        e["water"]["transfer_ratio"] = .95
        self.assertTrue(all(terminal_checks("pour_water",c,e).values()))
        e["target_displacement_m"] = .02
        self.assertFalse(terminal_checks("pour_water",c,e)["target_unmoved"])
        e["water"]["max_compression"] = .2
        self.assertFalse(terminal_checks("pour_water",c,e)["fluid_valid"])


@unittest.skipIf(p is None,"physics/numerical environment not installed")
class FluidTests(unittest.TestCase):
    def test_static_retention_mass_and_density(self):
        vessel = Vessel((.55,0,.645),(0,0,0,1))
        target = Vessel((.55,.16,.645),(0,0,0,1))
        fluid = PositionBasedFluid(vessel,count=128)
        for _ in range(240): fluid.step(1/480,[vessel,target])
        account = fluid.mass_accounting(vessel,target)
        self.assertEqual(account["spill_ratio"],0)
        self.assertLess(account["accounting_error_kg"],1e-12)
        self.assertLess(account["max_compression"],.1)
        self.assertFalse(fluid.metadata()["rigid_beads"])

    def test_density_solver_changes_compressed_fluid(self):
        vessel = Vessel((.55,0,.80),(0,0,0,1))
        fluid = PositionBasedFluid(vessel,count=64,iterations=8)
        center = fluid.positions.mean(axis=0)
        fluid.positions = center+.7*(fluid.positions-center)
        before = fluid.density().max()
        fluid.step(1/480,[])
        self.assertLess(fluid.density().max(),before)

    def test_open_top_no_lid_and_finite_shell(self):
        vessel = Vessel((0,0,0),(0,0,0,1))
        point = np.array([[0.,0.,.05]])
        np.testing.assert_equal(vessel.project(point,point,.001),point)
        wall = np.array([[.033,0,0.]])
        self.assertLess(vessel.project(wall,np.array([[.03,0,0.]]),.001)[0,0],.032)


@unittest.skipIf(p is None,"PyBullet environment not installed")
class PhysicalSequenceTests(unittest.TestCase):
    def test_control_does_not_require_runtime_object_or_scoring_truth(self):
        from unittest.mock import patch
        class NoTruthEvaluator:
            def __init__(self,scene): pass
            def sample(self,*args): pass
            def finish(self):
                return dict(success=False,failure_reason="unscored",
                            success_checks={"fluid_density_valid":True})
        def forbidden():
            raise AssertionError("controller read privileged object state")
        with SequenceScene("transport_place") as scene:
            scene.vessels = forbidden
            scene._object_pose = forbidden
            with patch("grasp_a.sequence_evaluation.SequenceEvaluator",NoTruthEvaluator):
                result = scene.run_sequence(load_candidates(ROOT/"candidates.example.json")[1])
            self.assertTrue(all(s["control_status"] == "completed" for s in result["stages"]))
            self.assertFalse(result["success"])

    def test_place_and_handover_independent_support(self):
        candidates = load_candidates(ROOT/"candidates.example.json")
        for task in ("transport_place","handover_transfer"):
            with self.subTest(task=task):
                r = run_sequence(task,0,"G02",candidates)
                self.assertTrue(r["success"],r["terminal_checks"])
                self.assertFalse(r["final_evidence"]["donor_contact"])
                self.assertEqual(r["terminal_samples"],240)
                if task == "handover_transfer":
                    self.assertTrue(r["final_evidence"]["receiver_contact"])
                    self.assertGreater(r["final_evidence"]["source_z_m"],.84)
                json.dumps(r,allow_nan=False)

    def test_handover_not_supported_and_donor_held_are_failures(self):
        c = SequenceConfig()
        e = dict(source_upright_deg=0,source_linear_speed_m_s=0,source_angular_speed_deg_s=0,
                 source_xy_error_m=0,source_z_m=.85,receiver_contact=False,donor_contact=True,
                 donor_clearance_m=0,table_contact=False)
        checks = terminal_checks("handover_transfer",c,e)
        self.assertFalse(checks["receiver_support"])
        self.assertFalse(checks["donor_released"])

    def test_missing_one_terminal_step_is_not_hidden_by_last_frame(self):
        from grasp_a.sequence_evaluation import SequenceEvaluator
        with SequenceScene("transport_place") as scene:
            evaluator = SequenceEvaluator(scene)
            good = dict(source_upright_deg=0,source_linear_speed_m_s=0,
                source_angular_speed_deg_s=0,source_xy_error_m=0,source_z_m=.645,
                donor_contact=False,receiver_contact=False,receiver_contact_links=[],
                donor_clearance_m=.1,table_contact=True)
            evaluator._collisions = lambda stage: []
            evaluator.evidence = lambda: {**good,"source_xy_error_m":.04}
            evaluator.sample("terminal",0,False)
            evaluator.evidence = lambda: dict(good)
            evaluator.sample("terminal",1,True)
            self.assertFalse(evaluator.terminal_all["at_B"])

    def test_unreachable_never_executes(self):
        c = load_candidates(ROOT/"candidates.example.json")[1]
        c = replace(c,grasp_pose=Pose((3,0,0),(0,0,0,1)))
        r = run_sequence("transport_place",0,c.id,[c])
        self.assertFalse(r["success"])
        self.assertEqual(r["status"],"rejected")
        self.assertTrue(all(s["control_status"] == "not_executed" for s in r["stages"]))

    def test_arrival_requires_continuous_fk_evidence(self):
        with SequenceScene("transport_place") as scene:
            target = Pose((10,10,10),(0,0,0,1))
            with self.assertRaisesRegex(SequenceFailure,"arrival_timeout"):
                scene._arrive(target,"test",.1)


if __name__ == "__main__":
    unittest.main()
