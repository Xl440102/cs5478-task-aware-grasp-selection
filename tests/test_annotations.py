"""A/B overlays label frozen task landmarks, without modifying physics."""
from copy import deepcopy
import json
import unittest
from pathlib import Path
import tempfile

from grasp_a.sequence_contracts import SequenceConfig, task_spec
try:
    import numpy as np
    from PIL import Image
    from grasp_a.annotations import sequence_annotations, project_points, draw_annotations
except ImportError:
    np = None


@unittest.skipIf(np is None,"numerical/image environment not installed")
class AnnotationTests(unittest.TestCase):
    def test_projection_and_behind_camera_mask(self):
        identity = np.eye(4).flatten(order="F")
        pixels,valid = project_points([(0,0,0),(1,1,0),(0,0,2)],identity,identity,(640,480))
        np.testing.assert_allclose(pixels[:2],[[320,240],[640,0]])
        self.assertEqual(valid.tolist(),[True,True,False])

    def test_task_endpoints_and_height_definitions(self):
        place = sequence_annotations(task_spec("transport_place"))
        handover = sequence_annotations(task_spec("handover_transfer"))
        water = sequence_annotations(task_spec("pour_water"))
        self.assertEqual(place["A"],[.55,0,.645])
        self.assertEqual(place["B"],[.55,.16,.645])
        self.assertAlmostEqual(handover["B"][2],.85)
        self.assertEqual(handover["extras"][0]["point"],[.55,.16,.8])
        self.assertEqual(water["B"],[.55,.16,.645])
        self.assertEqual(water["B_label"],"B RECEIVER")
        self.assertEqual(water["extras"][0]["point"],[.55,.115,.8])
        self.assertIn("NOT a robot trajectory",water["arrow_meaning"])

    def test_custom_B_and_lift_are_used_not_hardcoded(self):
        config = SequenceConfig(target_xy=(.50,.20),carry_height_m=.82,receiver_lift_m=.06)
        spec = task_spec("handover_transfer",config)
        original = deepcopy(spec)
        overlay = sequence_annotations(spec)
        np.testing.assert_allclose(overlay["B"],[.5,.2,.88])
        self.assertEqual(spec,original)
        json.dumps(overlay,allow_nan=False)

    def test_drawing_does_not_mutate_spec_or_add_physics_objects(self):
        try:
            import pybullet as p
            from grasp_a.sequence import SequenceScene
            from grasp_a.visualization import camera_image
        except ImportError:
            self.skipTest("PyBullet environment not installed")
        with SequenceScene("transport_place") as scene:
            spec = sequence_annotations(scene.spec)
            original = deepcopy(spec)
            bodies_before = p.getNumBodies(physicsClientId=scene.client)
            joints_before = scene._angles()
            pose_before = scene._object_pose()
            camera_image(scene.client,"test",annotations=spec)
            self.assertEqual(spec,original)
            self.assertEqual(bodies_before,p.getNumBodies(physicsClientId=scene.client))
            self.assertEqual(joints_before,scene._angles())
            self.assertEqual(pose_before,scene._object_pose())

    def test_both_video_modes_coexist_and_physics_is_identical(self):
        try:
            from unittest.mock import patch
            from grasp_a.contracts import load_candidates
            from grasp_a.sequence import run_sequence
        except ImportError:
            self.skipTest("PyBullet environment not installed")
        # Lightweight recording double checks actual API wiring/paths. Actual
        # MP4 captures and full dynamics are also checked in the demo workflow.
        class Recorder:
            def __init__(self,client,directory,stem,*,camera_kwargs):
                self.path = Path(directory)/(stem+".mp4")
                self.annotations = camera_kwargs["annotations"]
            def on_step(self,stage): pass
            def finish(self,result):
                return dict(video=str(self.path),annotations=self.annotations)
            def close(self): pass
        root = Path(__file__).resolve().parents[1]
        candidates = load_candidates(root/"candidates.example.json")
        with tempfile.TemporaryDirectory() as directory, patch("grasp_a.visualization.EpisodeRecorder",Recorder):
            marked = run_sequence("transport_place",0,"G02",candidates,media_dir=directory)
            plain = run_sequence("transport_place",0,"G02",candidates,media_dir=directory,show_landmarks=False)
        self.assertTrue(marked["success"] and plain["success"])
        self.assertTrue(marked["media"]["landmarks_enabled"])
        self.assertFalse(plain["media"]["landmarks_enabled"])
        self.assertTrue(marked["media"]["video"].endswith("_ab.mp4"))
        self.assertTrue(plain["media"]["video"].endswith("_plain.mp4"))
        self.assertIsNotNone(marked["media"]["annotations"])
        self.assertIsNone(plain["media"]["annotations"])
        self.assertEqual(marked["trajectory"],plain["trajectory"])
        self.assertEqual(marked["final_evidence"],plain["final_evidence"])
        self.assertEqual(marked["task_spec_sha256"],plain["task_spec_sha256"])


if __name__ == "__main__": unittest.main()
