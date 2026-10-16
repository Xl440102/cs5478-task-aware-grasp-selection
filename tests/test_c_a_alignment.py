"""Exercise the real C→A boundary with noisy estimates and no paid model calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from grasp_a.contracts import candidate_sha256, load_candidates
from grasp_c import runner
from grasp_c.providers import MockProvider

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(importlib.util.find_spec("pybullet") is None, "PyBullet is not installed")
class AAlignmentIntegrationTests(unittest.TestCase):
    def test_noisy_fixed_trial_preserves_protocol_and_public_model_boundary(self):
        from examples import evaluate_one_trial as bridge
        from grasp_a.simulation import CONTROLLER_VERSION, get_feasible_candidates

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            out = base / "run"
            config = {
                "candidates": str(ROOT / "candidates.example.json"), "seeds": [0],
                "tasks": ["handover_prep"], "repeats": 1,
                "cache_dir": str(base / "cache"),
                "observation_config": {"position_noise_m": .003, "yaw_noise_deg": 2,
                                       "noise_seed": 1000},
                "methods": [{"id": "M3_MOCK", "kind": "llm", "model": "mock",
                             "provider": {"type": "mock"}}],
            }
            config_path = base / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            requests = []
            generate = MockProvider.generate

            def inspect_request(provider, request):
                requests.append(request)
                return generate(provider, request)

            with patch("grasp_a.simulation.get_feasible_candidates", wraps=get_feasible_candidates) as filtered, \
                    patch.object(bridge, "run_episode", wraps=bridge.run_episode) as executed, \
                    patch.object(MockProvider, "generate", autospec=True, side_effect=inspect_request):
                summary = runner.run_evaluation(config_path, out)
            self.assertEqual(filtered.call_count, 1)
            self.assertEqual(executed.call_count, 1)
            self.assertIs(filtered.call_args.kwargs["observation_config"],
                          executed.call_args.kwargs["observation_config"])
            manifest = json.loads((out / "manifest.json").read_text())
            shared = json.loads((out / "filters" / "seed0.json").read_text())
            trial = json.loads((out / "trials" / "M3_MOCK" / "handover_prep" /
                                "seed0_repeat0.json").read_text())
            self.assertEqual(manifest["schema_version"], runner.RUN_SCHEMA)
            self.assertEqual(shared["schema_version"], runner.FILTER_SCHEMA)
            self.assertEqual(trial["schema_version"], "evaluation_trial_v0.2")
            self.assertEqual(trial["status"], "completed")
            episode = trial["episode"]
            self.assertEqual(episode["schema_version"], "episode_v0.3")
            self.assertEqual(episode["target_mug_pose"]["position"], [.55, 0, .785])
            for record in (manifest, shared, trial, episode, summary):
                self.assertEqual(record["controller_version"], CONTROLLER_VERSION)
                self.assertEqual(record["evaluation_version"], "fixed_target_hold_v2")
                self.assertEqual(record["policy_mode"], "fixed")
                self.assertEqual(record["observation_config"], manifest["observation_config"])
            self.assertEqual(shared["filter"]["observation_config"], manifest["observation_config"])
            self.assertEqual(shared["filter"]["initial_object_pose_estimate"],
                             episode["initial_object_pose_estimate"])
            self.assertEqual(manifest["candidate_sha256"], candidate_sha256(
                load_candidates(config["candidates"])))
            self.assertEqual(len(requests), 1)
            content = json.loads(requests[0].messages[1]["content"])
            self.assertEqual(set(content), {"task_id", "task_intent", "candidates"})
            for candidate in content["candidates"]:
                self.assertEqual(set(candidate), {"id", "region", "approach", "gripper_width", "geometry_score"})
            self.assertEqual(summary["methods"]["M3_MOCK"]["handover_prep"]["completed_n"], 1)
            # Offline reaggregation does not load a provider or call the simulator.
            with patch.object(MockProvider, "generate", side_effect=AssertionError("model called")), \
                    patch.object(bridge, "run_episode", side_effect=AssertionError("simulation called")):
                self.assertEqual(runner.summarize_run(out), summary)


if __name__ == "__main__":
    unittest.main()
