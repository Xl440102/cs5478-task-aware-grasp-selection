"""Regression tests for frozen-table identity, missingness and live-call audit."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from grasp_c.config import load_config
from grasp_c.providers import MockProvider, ModelResponse
from grasp_a.contracts import candidate_sha256, load_candidates
from grasp_c.tasks import sequence_specs, load_sequence_config
from grasp_c.table_evaluation import (
    BASELINES, candidate_fingerprints, checked, holm, load_pbf_table, load_table,
    paired, physical_success, rate_summary, run, seal, select_one, summarize, verify_pbf,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results/oracle_20261103/r3_v3p_wide_strict_v2"
PBF_SOURCE = ROOT / "results/oracle_20261103/r3_pbf_v3p_wide_strict_v2"


class TableStatisticsTests(unittest.TestCase):
    def test_execution_failure_counts_but_numerical_and_call_failures_do_not(self):
        e = dict(status="execution_failed", success=False, success_checks={"fluid_density_valid": True})
        self.assertIs(physical_success(e), False)
        for status in ("execution_error", "simulation_error", "simulation_invalid", "rejected"):
            self.assertIsNone(physical_success({**e, "status": status}))
        self.assertIsNone(physical_success({**e, "success_checks": {}}))
        self.assertIsNone(physical_success({**e, "final_evidence": {"water": {"finite_state": False}}}))
        s = rate_summary([True, False, None], 4)
        self.assertEqual((s["successes"], s["scored_n"], s["excluded_n"]), (1, 2, 2))
        self.assertEqual(s["success_rate"], .5)
        self.assertEqual(s["end_to_end_success_coverage"], .25)
        self.assertIsNone(rate_summary([], 2)["success_rate"])

    def test_paired_intersection_and_multiple_comparisons(self):
        p = paired([True]*6 + [None], [False]*6 + [True])
        self.assertEqual((p["paired_n"], p["wins"], p["losses"], p["excluded_n"]), (6, 6, 0, 1))
        self.assertEqual(p["p_exact"], .03125)
        self.assertEqual(p["difference"], 1)
        rows = [p, {"p_exact": .04}, {"p_exact": None}]
        holm(rows)
        self.assertEqual([r.get("p_holm") for r in rows], [.0625, .0625, None])
        self.assertIsNone(paired([None], [False])["p_exact"])

    def test_saved_record_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"trial.json"
            record = seal({"selected_id": "G01"})
            path.write_text(json.dumps(record))
            self.assertEqual(checked(path), record)
            record["selected_id"] = "G02"
            path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                checked(path)

    def test_historical_float_normalization_is_exact_not_tolerant(self):
        p = SOURCE / "b_inputs/candidates.scored.json"
        fingerprints = candidate_fingerprints(p)
        self.assertEqual(fingerprints["sequential_float_sum"],
                         json.loads((p.parent/"provenance.json").read_text())["candidate_sha256"])


class TableRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        raw = json.loads((ROOT / "configs/c.r3.codex.json").read_text())
        raw["candidates"] = str(SOURCE / "b_inputs/candidates.scored.json")
        raw["seeds"] = [10]
        raw["seed_split"]["evaluation"] = [10]
        for m in raw["methods"]:
            m["provider"] = {"type": "mock"}
            m["model"] = "mock"
            m["parameters"] = {}
        self.config_path = self.base/"config.json"
        self.config_path.write_text(json.dumps(raw))
        self.config = load_config(self.config_path)

    def test_frozen_table_and_training_overlap(self):
        table = load_table(SOURCE, self.config)
        self.assertEqual(len(table["filters"]), 3)
        self.assertEqual(len(table["episodes"]), sum(len(f["feasible_candidates"]) for f in table["filters"].values()))
        config = deepcopy(self.config)
        config["seeds"] = [0]
        with self.assertRaisesRegex(ValueError, "training"):
            load_table(SOURCE, config)
        config = deepcopy(self.config)
        config["scene_randomization"]["filter_mode"] = "initial"
        with self.assertRaisesRegex(ValueError, "scenes"):
            load_table(SOURCE, config)

    def test_pbf_table_integration_and_validation(self):
        table = load_table(SOURCE, self.config)
        pbf_table = load_pbf_table(PBF_SOURCE, self.config, table)
        self.assertEqual(pbf_table["meta"]["liquid"], "pbf")
        with self.assertRaisesRegex(ValueError, "liquid='pbf'"):
            load_pbf_table(SOURCE, self.config, table)
        out = self.base/"run_pbf_table"
        run(self.config_path, SOURCE, out, workers=2)
        summary = summarize(out, pbf_table=PBF_SOURCE,
                            summary_path=out/"summary_pbf.json",
                            report_path=out/"report_pbf.md")
        self.assertIn("pour_water_pbf_table", summary["tasks"])
        pbf_task = summary["tasks"]["pour_water_pbf_table"]
        self.assertEqual(pbf_task["liquid"], "pbf")
        self.assertEqual(pbf_task["oracle"]["successes"], 1)
        self.assertIsNotNone(pbf_task["uniform_expectation"])

    def test_prompt_v2_config_and_messages(self):
        v2_config = load_config(ROOT / "configs/c.r3.codex.v2.json")
        self.assertEqual([m["prompt_version"] for m in v2_config["methods"]], ["v2", "v2"])
        table = load_table(SOURCE, self.config)
        public = table["filters"]["handover_transfer", 10]["feasible_candidates"]
        manifest = {"manifest_sha256": "test", "identity": {"source_sha256": "test", "config": self.config}}
        method_m3 = {**self.config["methods"][0], "prompt_version": "v2"}
        method_a1 = {**self.config["methods"][1], "prompt_version": "v2"}
        item = dict(method_id="M3_CODEX_V2", task_id="handover_transfer", seed=10, repeat=0, sample_id=0)
        rec_m3 = select_one(item, method_m3, public, manifest)
        rec_a1 = select_one({**item, "method_id": "A1_CODEX_V2"}, method_a1, public, manifest)
        self.assertIn("unscored (N/A)", rec_m3["messages"][0]["content"])
        self.assertIn("unscored (N/A)", rec_a1["messages"][0]["content"])
        self.assertIn("top-down rim grasps", rec_m3["messages"][1]["content"])
        self.assertNotIn("task_intent", rec_a1["messages"][1]["content"])

    def test_run_resume_audit_missingness_and_config_rejection(self):
        out = self.base/"run"
        result = run(self.config_path, SOURCE, out, workers=2)
        self.assertEqual(result["selection_audit"]["M3_CODEX"]["live_requests"], 3)
        self.assertTrue(result["selection_audit"]["M3_CODEX"]["synthetic"])
        for m in BASELINES:
            self.assertEqual(result["tasks"]["pour_water_pbf"]["summary"][m]["scored_n"], 0)
        before = {p: p.read_bytes() for p in (out/"trials").rglob("*.json")}
        with patch.object(MockProvider, "generate", side_effect=AssertionError("unexpected new call")), \
             patch.object(MockProvider, "preflight", side_effect=AssertionError("unexpected auth check")):
            resumed = run(self.config_path, SOURCE, out, resume=True)
        self.assertEqual(result, resumed)
        self.assertEqual(before, {p: p.read_bytes() for p in (out/"trials").rglob("*.json")})
        trial = next((out/"trials/M3_CODEX").rglob("*.json"))
        trial.unlink()
        partial = summarize(out)
        self.assertEqual(partial["selection_audit"]["M3_CODEX"]["saved_n"], 2)
        raw = json.loads(self.config_path.read_text())
        raw["methods"][0]["model"] = "changed"
        self.config_path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError, "identity changed"):
            run(self.config_path, SOURCE, out, resume=True)

    def test_prompt_excludes_oracle_and_fallback_is_audited(self):
        table = load_table(SOURCE, self.config)
        public = table["filters"]["transport_place", 10]["feasible_candidates"]
        public = [{**r, "success": True, "secret_ground_truth": "must_not_reach_model"} for r in public]
        manifest = {"manifest_sha256": "test", "identity": {"source_sha256": "test", "config": self.config}}
        item = dict(method_id="M3_CODEX", task_id="transport_place", seed=10, repeat=0, sample_id=0)
        def invalid(request):
            self.assertNotIn("must_not_reach_model", str(request.messages))
            return ModelResponse("invalid", "mock")
        with patch.object(MockProvider, "generate", side_effect=invalid):
            record = select_one(item, self.config["methods"][0], public, manifest)
        md = record["selection"]["metadata"]
        self.assertEqual((md["repair_attempts"], md["live_requests"]), (1, 2))
        self.assertTrue(md["fallback_to_geometry"])
        self.assertNotIn("must_not_reach_model", str(record["messages"]))
        method = self.config["methods"][1]
        a1 = select_one({**item, "method_id": method["id"]}, method, public, manifest)
        self.assertNotIn("task_intent", a1["messages"][1]["content"])
        self.assertNotIn("transport_place", a1["messages"][1]["content"])

    def test_pbf_rejects_wrong_candidate_and_proxy(self):
        manifest = {"manifest_sha256": "test", "identity": {"config": self.config}}
        with self.assertRaisesRegex(ValueError, "identity"):
            verify_pbf(dict(manifest_sha256="test", seed=10, candidate_id="wrong"), manifest, 10, "G01")
        with self.assertRaisesRegex(ValueError, "protocol"):
            verify_pbf(dict(manifest_sha256="test", seed=10, candidate_id="G01",
                            episode={"pour_proxy": {"dwell_samples": 480}}), manifest, 10, "G01")

    def test_pbf_accepts_live_tuples_and_json_lists_but_rejects_changed_scene(self):
        manifest = {"manifest_sha256": "test", "identity": {"config": self.config}}
        scene = deepcopy(self.config["scene_randomization"])
        for key in ("x_range_m", "y_range_m", "yaw_range_rad"):
            scene[key] = tuple(scene[key])
        episode = dict(task_id="pour_water", seed=10, selected_id="G01", scene_randomization=scene,
                       candidate_sha256=candidate_sha256(load_candidates(self.config["candidates"])),
                       task_spec_sha256=sequence_specs(["pour_water"], load_sequence_config(
                           self.config["sequence_config"]))["pour_water"]["sha256"])
        record = dict(manifest_sha256="test", execution_sha256="execution", seed=10,
                      candidate_id="G01", episode=episode)
        verify_pbf(record, manifest, 10, "G01", "execution")
        verify_pbf(json.loads(json.dumps(record)), manifest, 10, "G01", "execution")
        with self.assertRaisesRegex(ValueError, "execution fingerprint"):
            verify_pbf(record, manifest, 10, "G01", "another execution")
        scene["estimate_position_noise_m"] = .002
        with self.assertRaisesRegex(ValueError, "protocol"):
            verify_pbf(record, manifest, 10, "G01", "execution")


if __name__ == "__main__":
    unittest.main()
