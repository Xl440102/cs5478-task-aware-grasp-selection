"""Keep A's observation and scoring protocols separate in C comparisons."""
from copy import deepcopy
import json
import unittest

from grasp_c.metrics import summarize_records


class MetricsAlignmentTests(unittest.TestCase):
    def record(self, method="A", seed=0):
        return {
            "schema_version": "evaluation_trial_v0.2", "method_id": method,
            "task_id": "pour_prep", "seed": seed, "repeat": 0,
            "manifest_sha256": "same-environment", "candidate_sha256": "same-candidates",
            "controller_version": "observable_primitives_v2", "status": "completed",
            "observation_config": {
                "source": "calibrated_prior", "position_noise_m": 0,
                "yaw_noise_deg": 0, "noise_seed": 0,
                "object_pose": {"position": (0.55, 0, 0.645),
                                "quaternion": (0, 0, 0, 1)},
            },
            "selected_id": "G02", "filter": {"status": "ok"},
            "selection": {"latency_s": 0.01, "metadata": {}},
            "episode": {
                "schema_version": "episode_v0.3", "evaluation_version": "fixed_target_hold_v2",
                "policy_mode": "fixed", "success": True,
            },
        }

    def summarize(self, records):
        plan = [{key: record[key] for key in ("method_id", "task_id", "seed", "repeat")}
                for record in records]
        return summarize_records(records, plan)

    def pair(self, left, right):
        return self.summarize([left, right])["paired_comparisons"][0]

    def test_observation_tuple_list_roundtrip_is_an_equivalent_condition(self):
        left = self.record("A")
        right = json.loads(json.dumps(self.record("B")))
        pair = self.pair(left, right)
        self.assertEqual(pair["paired_completed_n"], 1)
        self.assertEqual(pair["completed_provenance_mismatch_n"], 0)

    def test_pairing_excludes_observation_scoring_policy_and_schema_mismatches(self):
        for section, field, value in (
            ("observation_config", "noise_seed", 10),
            ("observation_config", "position_noise_m", 0.003),
            ("observation_config", "source", "external_estimate"),
            ("episode", "evaluation_version", "other_evaluator"),
            ("episode", "policy_mode", "incremental"),
            ("episode", "schema_version", "episode_v0.2"),
            (None, "schema_version", "evaluation_trial_v0.1"),
        ):
            with self.subTest(section=section, field=field):
                left, right = self.record("A"), self.record("B")
                destination = right[section] if section else right
                destination[field] = value
                pair = self.pair(left, right)
                self.assertEqual(pair["paired_completed_n"], 0)
                self.assertEqual(pair["excluded_n"], 1)
                self.assertEqual(pair["completed_provenance_mismatch_n"], 1)
                self.assertEqual(pair["exclusion_reasons"], {"provenance_mismatch": 1})

    def test_legacy_missing_fields_only_match_the_same_recorded_protocol(self):
        left, right = self.record("A"), self.record("B")
        for record in (left, right):
            record.pop("observation_config")
            record["schema_version"] = "evaluation_trial_v0.1"
            record["controller_version"] = "bounded_ik_v1"
            for field in ("evaluation_version", "policy_mode", "schema_version"):
                record["episode"].pop(field)
        self.assertEqual(self.pair(left, right)["paired_completed_n"], 1)
        changed = deepcopy(right)
        changed["episode"]["evaluation_version"] = "fixed_target_hold_v2"
        self.assertEqual(self.pair(left, changed)["paired_completed_n"], 0)
        changed = deepcopy(right)
        changed["observation_config"] = self.record()["observation_config"]
        self.assertEqual(self.pair(left, changed)["paired_completed_n"], 0)
        changed = deepcopy(right)
        changed["controller_version"] = "observable_primitives_v2"
        self.assertEqual(self.pair(left, changed)["paired_completed_n"], 0)

    def test_summary_preserves_legacy_protocol_and_describes_recorded_v2_scoring(self):
        modern = self.record(seed=0)
        legacy = self.record(seed=1)
        legacy["controller_version"] = "bounded_ik_v1"
        legacy["episode"].pop("evaluation_version")
        legacy["episode"].pop("policy_mode")
        summary = self.summarize([modern, legacy])
        protocols = {p["controller_version"]: p for p in summary["execution_protocols"]}
        self.assertEqual(protocols["observable_primitives_v2"]["evaluation_version"],
                         "fixed_target_hold_v2")
        self.assertIsNone(protocols["bounded_ik_v1"]["evaluation_version"])
        self.assertEqual(protocols["bounded_ik_v1"]["completed_n"], 1)
        notes = " ".join(summary["notes"])
        self.assertIn("shared fixed world targets", notes)
        self.assertIn("full two-second hold", notes)
        self.assertIn("legacy candidate-dependent targets", notes)
        self.assertIn("Multiple execution protocols", notes)
        self.assertEqual(summary["methods"]["A"]["pour_prep"]["execution_protocols"],
                         summary["execution_protocols"])
        old_summary = self.summarize([legacy])
        self.assertNotIn("shared fixed world targets", " ".join(old_summary["notes"]))

    def test_geometry_fallback_flag_takes_precedence_and_legacy_alias_is_supported(self):
        metadata = [
            {"fallback_to_geometry": False, "fallback_to_m1": True},
            {"fallback_to_geometry": True, "fallback_to_m1": False},
            {"fallback_to_m1": False},
            {"fallback_to_m1": True},
        ]
        records = []
        for seed, item in enumerate(metadata):
            record = self.record(seed=seed)
            record["selection"]["metadata"] = dict(item, cache_hit=False)
            records.append(record)
        selection = self.summarize(records)["methods"]["A"]["pour_prep"]["selection"]
        self.assertEqual(selection["fallback_trial_n"], 2)
        self.assertEqual(selection["fallback_rate"], 0.5)
        self.assertEqual(selection["model_usable_after_repair_n"], 2)
        self.assertEqual(selection["model_usable_after_repair_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
