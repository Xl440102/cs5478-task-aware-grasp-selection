"""C delegates B algorithms and freezes scored static inputs without labels."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from grasp_a.contracts import candidate_sha256, load_candidates
from grasp_b.generation import DEFAULT_CONFIG
from grasp_b.prototype import select_geometry, select_random
from grasp_b.scoring import GEOMETRY_SCORE_VERSION, normalized_weights
from grasp_c.baselines import EXPERIMENTAL_SCOPE, build_b_selector
from grasp_c.config import load_b_provenance
from grasp_c.prepare_b import prepare_b_inputs
from grasp_c.tasks import tasks_for_track


def provenance():
    return {
        "score_version": GEOMETRY_SCORE_VERSION, "scope": EXPERIMENTAL_SCOPE,
        "weights": {"alignment": 1.0, "symmetry": 1.0, "com_distance": 1.0},
        "candidate_sha256": "a" * 64, "candidate_file_sha256": "b" * 64,
        "report_sha256": "c" * 64, "score_status": "scored_experimental",
    }


PUBLIC = [
    {"id": "b", "region": "body", "approach": "side", "gripper_width": 0.078,
     "geometry_score": 0.8},
    {"id": "a", "region": "body", "approach": "side_high", "gripper_width": 0.078,
     "geometry_score": 0.8},
    {"id": "c", "region": "body", "approach": "diagonal", "gripper_width": 0.078,
     "geometry_score": 0.5},
]


class BaselineAdapterTests(unittest.TestCase):
    def test_geometry_delegates_B_and_preserves_public_score_provenance(self):
        origin = provenance()
        origin["private_features"] = "private_pose_and_contact_breakdown"
        frozen = deepcopy(origin)
        selector = build_b_selector("b_geometry", origin)
        with patch("grasp_c.baselines.select_geometry", wraps=select_geometry) as b_selector:
            result = selector("pour_prep", PUBLIC, 4)
        b_selector.assert_called_once_with(PUBLIC)
        self.assertEqual(result["ranking"], ["a", "b", "c"])
        self.assertEqual(result["selected_id"], "a")
        metadata = result["metadata"]
        for key in ("score_version", "scope", "weights", "candidate_sha256", "report_sha256"):
            self.assertEqual(metadata[key], origin[key])
        self.assertAlmostEqual(sum(metadata["normalized_weights"].values()), 1)
        self.assertTrue(metadata["score_used"])
        self.assertNotIn("private_features", metadata)
        self.assertNotIn("private_pose_and_contact_breakdown", json.dumps(result))
        self.assertEqual(origin, frozen)

    def test_random_delegates_B_is_repeat_invariant_and_does_not_consume_global_rng(self):
        selector = build_b_selector("b_random", provenance())
        state = random.getstate()
        with patch("grasp_c.baselines.select_random", wraps=select_random) as b_selector:
            result = selector("pour_prep", PUBLIC, 7)
        b_selector.assert_called_once_with(PUBLIC, task_id="pour_prep", seed=7)
        self.assertEqual(random.getstate(), state)
        self.assertEqual(result, selector("pour_prep", PUBLIC, 7))
        altered = deepcopy(PUBLIC)
        for i, item in enumerate(altered):
            item["geometry_score"] = i
        self.assertEqual(result["ranking"], selector("pour_prep", altered, 7)["ranking"])
        self.assertEqual(result["metadata"]["selection_seed_rule"], "scene_seed_repeat_invariant")
        self.assertFalse(result["metadata"]["score_used"])

    def test_all_registered_prep_and_sequence_tasks_use_the_same_public_adapter(self):
        for kind in ("b_geometry", "b_random"):
            selector = build_b_selector(kind, provenance())
            for task in (*tasks_for_track("prep"), *tasks_for_track("sequence")):
                with self.subTest(kind=kind, task=task):
                    result = selector(task, PUBLIC, 0)
                    self.assertEqual(set(result["ranking"]), {item["id"] for item in PUBLIC})
                    self.assertEqual(result["selected_id"], result["ranking"][0])

    def test_placeholder_unapproved_formal_and_invalid_provenance_fail_closed(self):
        for update in (
            {"score_version": "placeholder_zero"},
            {"score_version": "formal_geometry_v1"},
            {"scope": "formal_M1"},
            {"score_status": "placeholder_zero_do_not_use_for_ranking"},
            {"weights": {"alignment": 0, "symmetry": 0, "com_distance": 0}},
            {"normalized_weights": {"alignment": 1, "symmetry": 0, "com_distance": 0}},
            {"candidate_sha256": "not-a-hash"},
            {"report_sha256": "not-a-hash"},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                build_b_selector("b_geometry", dict(provenance(), **update))
        with self.assertRaises(ValueError):
            build_b_selector("b_m2", provenance())

    def test_public_contract_rejects_hidden_fields_empty_or_invalid_selection(self):
        for kind in ("b_geometry", "b_random"):
            selector = build_b_selector(kind, provenance())
            for public in ([], [dict(PUBLIC[0], task_labels=["private_answer"])],
                           [PUBLIC[0], PUBLIC[0]]):
                with self.subTest(kind=kind, public=public), self.assertRaises(ValueError):
                    selector("pour_prep", public, 0)
            for seed in (False, 0.1):
                with self.assertRaises(ValueError):
                    selector("pour_prep", PUBLIC, seed)
            with self.assertRaises(ValueError):
                selector("unknown_task", PUBLIC, 0)
        with patch("grasp_c.baselines.select_geometry", return_value={"selected_id": "foreign",
                                                                     "ranking": ["foreign"]}):
            with self.assertRaises(ValueError):
                build_b_selector("b_geometry", provenance())("pour_prep", PUBLIC, 0)

    def test_factory_and_return_metadata_do_not_mutate_future_calls(self):
        original = provenance()
        selector = build_b_selector("b_geometry", original)
        original["weights"]["alignment"] = 999
        first = selector("pour_prep", PUBLIC, 0)
        first["metadata"]["weights"]["alignment"] = 999
        self.assertEqual(selector("pour_prep", PUBLIC, 0)["metadata"]["weights"]["alignment"], 1)


class PrepareBInputsTests(unittest.TestCase):
    def prepare(self, output, **kwargs):
        with patch("grasp_a.simulation.get_feasible_candidates", side_effect=AssertionError("filter called")), \
             patch("grasp_a.simulation.run_episode", side_effect=AssertionError("episode called")):
            return prepare_b_inputs(output, **kwargs)

    def test_default_artifacts_share_hashes_and_never_call_filter_or_episode(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.prepare(Path(directory) / "shared")
            self.assertEqual(set(paths), {"candidates", "provenance", "report", "features"})
            candidates = load_candidates(paths["candidates"])
            origin = json.loads(Path(paths["provenance"]).read_text())
            report = json.loads(Path(paths["report"]).read_text())
            features = json.loads(Path(paths["features"]).read_text())
            self.assertEqual(len(candidates), 12)
            self.assertTrue(all(0 <= c.geometry_score <= 1 for c in candidates))
            self.assertGreater(len({c.geometry_score for c in candidates}), 1)
            for document in (origin, report, features):
                self.assertEqual(document["candidate_sha256"], candidate_sha256(candidates))
            self.assertEqual(origin["candidate_file_sha256"],
                             hashlib.sha256(Path(paths["candidates"]).read_bytes()).hexdigest())
            self.assertEqual(origin["report_sha256"],
                             hashlib.sha256(Path(paths["report"]).read_bytes()).hexdigest())
            self.assertEqual(origin["features_sha256"],
                             hashlib.sha256(Path(paths["features"]).read_bytes()).hexdigest())
            self.assertEqual(origin["score_status"], "scored_experimental")
            self.assertIn("internal", report["visibility"])
            self.assertNotIn("checks", report)
            for kind in ("b_geometry", "b_random"):
                result = build_b_selector(kind, origin)(
                    "pour_prep", [c.public_metadata() for c in candidates], 0)
                self.assertEqual(len(result["ranking"]), len(candidates))
                self.assertNotIn("score_breakdown", result["metadata"])

    def test_preparation_is_deterministic_and_existing_outputs_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.prepare(Path(directory) / "first")
            second = self.prepare(Path(directory) / "second")
            for key in first:
                self.assertEqual(Path(first[key]).read_bytes(), Path(second[key]).read_bytes())
            before = {key: Path(value).read_bytes() for key, value in first.items()}
            with self.assertRaises(FileExistsError):
                prepare_b_inputs(Path(directory) / "first")
            for key, path in first.items():
                self.assertEqual(Path(path).read_bytes(), before[key])

    def test_path_and_dict_configs_exclude_unsupported_scores_with_explicit_audit(self):
        generation = json.loads(DEFAULT_CONFIG.read_text())
        generation["roll_deg"] = [0, 90]
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "generation.json"
            config_path.write_text(json.dumps(generation))
            paths = self.prepare(Path(directory) / "mixed", generation_config=config_path)
            report = json.loads(Path(paths["report"]).read_text())
            candidates = load_candidates(paths["candidates"])
            self.assertEqual(report["generated_candidate_count"], 12)
            self.assertEqual(len(candidates), 6)
            self.assertEqual(len(report["excluded_ids"]), 6)
            self.assertFalse(set(report["excluded_ids"]) & {c.id for c in candidates})
            self.assertIn(str(config_path.resolve()), report["source_sha256"])
            generation["roll_deg"] = [90]
            absent = Path(directory) / "unusable"
            with self.assertRaisesRegex(ValueError, "no usable candidates"):
                prepare_b_inputs(absent, generation_config=generation)
            self.assertFalse(absent.exists())

    def test_invalid_score_configuration_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, config in enumerate((
                {"version": "placeholder", "scope": EXPERIMENTAL_SCOPE, "weights": provenance()["weights"]},
                {"version": GEOMETRY_SCORE_VERSION, "scope": "formal_M1", "weights": provenance()["weights"]},
                {"version": GEOMETRY_SCORE_VERSION, "scope": EXPERIMENTAL_SCOPE, "weights": {}},
            )):
                target = Path(directory) / str(index)
                with self.subTest(config=config), self.assertRaises(ValueError):
                    prepare_b_inputs(target, scoring_config=config)
                self.assertFalse(target.exists())

    def test_provenance_cannot_relabel_existing_scores_with_different_valid_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.prepare(Path(directory) / "shared")
            origin_path = Path(paths["provenance"])
            origin = load_b_provenance(origin_path, paths["candidates"])
            candidate_bytes = Path(paths["candidates"]).read_bytes()
            report_bytes = Path(paths["report"]).read_bytes()
            origin["weights"] = {"alignment": 3.0, "symmetry": 2.0, "com_distance": 1.0}
            origin["normalized_weights"] = normalized_weights(origin["weights"])
            origin_path.write_text(json.dumps(origin, allow_nan=False))
            # Both objects remain individually well formed and file hashes match;
            # their relationship is invalid because scores used the original weights.
            build_b_selector("b_geometry", origin)
            self.assertEqual(Path(paths["candidates"]).read_bytes(), candidate_bytes)
            self.assertEqual(Path(paths["report"]).read_bytes(), report_bytes)
            with self.assertRaises(ValueError):
                load_b_provenance(origin_path, paths["candidates"])

    def test_provenance_rejects_another_valid_report_even_with_its_correct_file_hash(self):
        generation = json.loads(DEFAULT_CONFIG.read_text())
        generation["height_m"] = [0.020, 0.030]
        with tempfile.TemporaryDirectory() as directory:
            paths = self.prepare(Path(directory) / "original")
            other = self.prepare(Path(directory) / "other", generation_config=generation)
            origin_path = Path(paths["provenance"])
            origin = load_b_provenance(origin_path, paths["candidates"])
            donor = json.loads(Path(other["report"]).read_text())
            self.assertNotEqual(origin["candidate_sha256"], donor["candidate_sha256"])
            copied_report = origin_path.parent / "different.internal.json"
            copied_report.write_bytes(Path(other["report"]).read_bytes())
            origin["report_file"] = copied_report.name
            origin["report_sha256"] = hashlib.sha256(copied_report.read_bytes()).hexdigest()
            origin_path.write_text(json.dumps(origin, allow_nan=False))
            with self.assertRaises(ValueError):
                load_b_provenance(origin_path, paths["candidates"])

    def test_report_contents_are_cross_checked_after_the_report_hash_is_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, field in enumerate(("score_version", "score_breakdown",
                                           "scoring_config_canonical_sha256",
                                           "generation_config_canonical_sha256", "schema_version")):
                with self.subTest(field=field):
                    paths = self.prepare(Path(directory) / str(index))
                    origin_path, report_path = Path(paths["provenance"]), Path(paths["report"])
                    origin = load_b_provenance(origin_path, paths["candidates"])
                    report = json.loads(report_path.read_text())
                    if field == "score_breakdown":
                        self.assertEqual(report[field][0]["status"], "ok")
                        report[field][0]["score"] = 0.25
                    elif field.endswith("canonical_sha256"):
                        report[field] = "0" * 64
                    else:
                        report[field] = "unsupported_version"
                    report_path.write_text(json.dumps(report, allow_nan=False))
                    origin["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
                    origin_path.write_text(json.dumps(origin, allow_nan=False))
                    with self.assertRaises(ValueError):
                        load_b_provenance(origin_path, paths["candidates"])


if __name__ == "__main__":
    unittest.main()
