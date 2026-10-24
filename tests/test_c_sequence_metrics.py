"""Offline fixtures for the full-task track; no simulator or model calls."""
from copy import deepcopy
import json
import unittest

from grasp_c.sequence_metrics import summarize_sequence_records


class SequenceMetricsTests(unittest.TestCase):
    def plan(self, methods=("M3",), seeds=(0,), *, task="transport_place", repeats=1):
        return [{"track": "sequence", "method_id": method, "task_id": task,
                 "seed": seed, "repeat": repeat}
                for method in methods for seed in seeds for repeat in range(repeats)]

    def record(self, method="M3", seed=0, *, task="transport_place", repeat=0,
               status="completed", success=True, numerical=True):
        returned = status in {"completed", "rejected", "execution_failed", "simulation_error", "simulation_invalid"}
        record = {
            "schema_version": "evaluation_sequence_trial_v1", "track": "sequence",
            "evaluation_scope": "pilot", "method_id": method, "task_id": task,
            "seed": seed, "repeat": repeat, "selected_id": "G02" if returned else None,
            "scene_version": "hollow_cup_sequences_v1", "controller_version": "staged_sequences_v1",
            "evaluation_version": "physical_outcomes_v1", "candidate_sha256": "all-candidates",
            "task_spec_sha256": "spec-" + task, "manifest_sha256": "same-run-environment",
            "observation_config": {"source": "calibrated_prior"}, "status": status,
            "filter": {"status": "no_feasible_candidate" if status == "no_feasible_candidate" else "ok"},
            "selection": {"latency_s": 0.25, "metadata": {}} if returned else None,
            "episode": None, "error": None,
        }
        if returned:
            record["episode"] = {
                key: deepcopy(record[key]) for key in
                ("task_id", "seed", "selected_id", "scene_version", "controller_version", "evaluation_version",
                 "candidate_sha256", "task_spec_sha256", "observation_config", "status")}
            record["episode"].update(
                schema_version="sequence_episode_v1", success=success if status == "completed" else False,
                failure_reason=None if status == "completed" and success else "diagnostic_failure",
                collision=False, dropped=False, elapsed_s=12.5,
                success_checks={"fluid_density_valid": numerical, "no_drop": True,
                                "no_unintended_collision": True, "sequence_complete": status == "completed"},
                terminal_checks={"table_support": success} if status == "completed" else {},
                stages=[{"id": "transport", "control_status": "completed"},
                        {"id": "terminal", "control_status": "completed" if status == "completed" else "not_executed"}],
                stage_evaluation={"transport": {"physical_pass": True, "physical_checks": {"at_B": True}},
                                  "terminal": {"physical_pass": success if status == "completed" else False,
                                               "physical_checks": {"table_support": success} if status == "completed" else {}}},
                final_evidence={"source_xy_error_m": 0.003, "source_z_m": 0.645,
                                "source_upright_deg": 1.0, "donor_contact": False,
                                "table_contact": True, "receiver_contact": False})
        return record

    def group(self, records, planned=None):
        planned = planned if planned is not None else self.plan()
        return summarize_sequence_records(records, planned)["methods"]["M3"][planned[0]["task_id"]]

    def test_statuses_empty_candidates_and_missing_records_keep_explicit_denominators(self):
        statuses = ["completed", "completed", "execution_failed", "simulation_error",
                    "simulation_invalid", "rejected", "no_feasible_candidate", "filter_error", "selector_error"]
        records = [self.record(seed=seed, status=status, success=seed == 0)
                   for seed, status in enumerate(statuses)]
        for seed in (1, 2, 3, 4):
            records[seed]["episode"]["collision"] = True
            records[seed]["episode"]["dropped"] = True
        summary = self.group(records, self.plan(seeds=range(10)))
        self.assertEqual(summary["planned_n"], 10)
        self.assertEqual(summary["saved_n"], 9)
        self.assertEqual(summary["status_counts"]["missing"], 1)
        self.assertEqual(summary["returned_n"], 6)
        self.assertEqual(summary["numerically_valid_n"], 3)
        self.assertEqual(summary["numerically_invalid_n"], 2)
        self.assertEqual(summary["conditional_success_rate"], 0.5)
        self.assertAlmostEqual(summary["success_per_numerically_valid_episode"], 1/3)
        self.assertEqual(summary["success_coverage"], 0.1)
        self.assertEqual(summary["rates"]["numerical_validity"], {"numerator": 3, "denominator": 5, "rate": 0.6})
        self.assertEqual(summary["physical_flags"]["collision"]["valid_n"], 3)
        self.assertEqual(summary["physical_flags"]["collision"]["true_n"], 2)
        self.assertEqual(summary["task_outcomes"]["stages"]["terminal"]["control_status_counts"],
                         {"completed": 2, "not_executed": 1})

    def test_all_errors_and_no_feasible_are_not_synthetic_physical_failures(self):
        records = [self.record(seed=0, status="no_feasible_candidate"),
                   self.record(seed=1, status="filter_error")]
        summary = self.group(records, self.plan(seeds=(0, 1, 2)))
        self.assertEqual(summary["completed_n"], 0)
        self.assertEqual(summary["physical_flags"]["collision"]["valid_n"], 0)
        self.assertIsNone(summary["conditional_success_rate"])
        self.assertIsNone(summary["numerical_validity_rate"])
        self.assertEqual(summary["task_outcomes"]["final_evidence"]["source_xy_error_m"]["n"], 0)
        json.dumps(summary, allow_nan=False)

    def test_execution_failure_with_invalid_density_is_excluded_from_physical_denominator(self):
        records = [self.record(seed=0),
                   self.record(seed=1, status="execution_failed", numerical=False)]
        records[1]["episode"]["collision"] = True
        summary = self.group(records, self.plan(seeds=(0, 1)))
        self.assertEqual(summary["execution_failed_n"], 1)
        self.assertEqual(summary["numerically_valid_n"], 1)
        self.assertEqual(summary["numerically_invalid_n"], 1)
        self.assertEqual(summary["physical_flags"]["collision"]["true_n"], 0)

    def test_missing_numerical_evidence_is_unknown_and_not_assumed_valid(self):
        record = self.record(status="execution_failed")
        record["episode"]["success_checks"].pop("fluid_density_valid")
        summary = self.group([record])
        self.assertEqual(summary["numerical_validity_unknown_n"], 1)
        self.assertEqual(summary["numerically_valid_n"], 0)
        self.assertIsNone(summary["success_per_numerically_valid_episode"])

    def test_explicit_nonfinite_fluid_is_invalid_even_if_density_flag_is_true(self):
        record = self.record(task="pour_water", status="execution_failed")
        record["episode"]["final_evidence"]["water"] = {"finite_state": False}
        summary = self.group([record], self.plan(task="pour_water"))
        self.assertEqual(summary["numerically_invalid_n"], 1)
        self.assertEqual(summary["numerically_valid_n"], 0)

    def test_protocol_errors_and_prep_records_are_rejected(self):
        mutations = [("track", "prep"), ("schema_version", "evaluation_trial_v0.2"),
                     ("scene_version", "legacy-solid-cup"), ("task_id", "pour_prep")]
        for field, value in mutations:
            record = self.record()
            record[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.group([record])
        record = self.record()
        record.pop("task_spec_sha256")
        with self.assertRaisesRegex(ValueError, "missing protocol"):
            self.group([record])
        record = self.record()
        record["episode"]["schema_version"] = "episode_v0.3"
        with self.assertRaisesRegex(ValueError, "sequence_episode_v1"):
            self.group([record])

    def test_trial_episode_disagreement_and_false_success_are_rejected(self):
        record = self.record()
        record["episode"]["task_spec_sha256"] = "other-spec"
        with self.assertRaisesRegex(ValueError, "task_spec_sha256"):
            self.group([record])
        record = self.record(status="simulation_invalid")
        record["episode"]["success"] = True
        with self.assertRaisesRegex(ValueError, "only a completed"):
            self.group([record])
        record = self.record(numerical=False)
        with self.assertRaisesRegex(ValueError, "numerical evidence"):
            self.group([record])

    def test_mixed_specs_within_one_method_group_are_not_averaged(self):
        records = [self.record(seed=0), self.record(seed=1)]
        records[1]["task_spec_sha256"] = records[1]["episode"]["task_spec_sha256"] = "new-goal"
        with self.assertRaisesRegex(ValueError, "mixed sequence protocols"):
            self.group(records, self.plan(seeds=(0, 1)))

    def test_paired_effect_includes_valid_execution_failure_but_excludes_invalid_and_missing(self):
        records = [self.record("A", 0), self.record("B", 0, status="execution_failed"),
                   self.record("A", 1, success=False), self.record("B", 1),
                   self.record("A", 2, status="simulation_invalid"), self.record("B", 2),
                   self.record("A", 3)]
        summary = summarize_sequence_records(records, self.plan(("A", "B"), seeds=range(4)))
        pair = summary["paired_comparisons"][0]
        self.assertEqual(pair["paired_numerically_valid_n"], 2)
        self.assertEqual(pair["paired_completed_n"], 1)
        self.assertEqual(pair["left_only_success_n"], 1)
        self.assertEqual(pair["right_only_success_n"], 1)
        self.assertEqual(pair["success_rate_difference_left_minus_right"], 0)
        self.assertEqual(pair["excluded_n"], 2)
        self.assertEqual(sum(pair["exclusion_reasons"].values()), 2)

    def test_cross_method_protocol_mismatch_has_separate_groups_and_no_pair(self):
        for field in ("task_spec_sha256", "candidate_sha256", "observation_config", "manifest_sha256"):
            records = [self.record("A"), self.record("B")]
            value = {"source": "different-estimator"} if field == "observation_config" else "different"
            records[1][field] = value
            if field in records[1]["episode"]:
                records[1]["episode"][field] = value
            with self.subTest(field=field):
                summary = summarize_sequence_records(records, self.plan(("A", "B")))
                pair = summary["paired_comparisons"][0]
                self.assertEqual(pair["paired_numerically_valid_n"], 0)
                self.assertEqual(pair["exclusion_reasons"], {"provenance_mismatch": 1})
                self.assertEqual(len(summary["execution_protocols"]), 2)

    def test_task_water_evidence_ignores_invalid_runs_and_preserves_nulls(self):
        records = [self.record(seed=0, task="pour_water"),
                   self.record(seed=1, task="pour_water", success=False),
                   self.record(seed=2, task="pour_water", status="simulation_invalid")]
        records[0]["episode"]["final_evidence"]["water"] = {
            "transfer_ratio": .92, "spill_ratio": .02, "finite_state": True}
        records[1]["episode"]["final_evidence"]["water"] = {"transfer_ratio": None}
        records[2]["episode"]["final_evidence"]["water"] = {
            "transfer_ratio": 999, "spill_ratio": 999, "finite_state": False}
        summary = self.group(records, self.plan(seeds=(0, 1, 2), task="pour_water"))
        water = summary["task_outcomes"]["task_specific"]["water"]
        self.assertEqual(water["transfer_ratio"]["n"], 1)
        self.assertEqual(water["transfer_ratio"]["mean"], .92)
        self.assertEqual(water["finite_state"]["valid_n"], 1)
        self.assertIsNone(water["outside_mass_kg"]["mean"])

    def test_handover_support_is_separate_from_legacy_grasp_maintenance(self):
        records = [self.record(seed=0, task="handover_transfer"),
                   self.record(seed=1, task="handover_transfer", success=False)]
        records[0]["episode"]["final_evidence"]["receiver_contact"] = True
        records[1]["episode"]["final_evidence"].pop("donor_contact")
        summary = self.group(records, self.plan(seeds=(0, 1), task="handover_transfer"))
        transfer = summary["task_outcomes"]["task_specific"]
        self.assertEqual(transfer["receiver_both_fingers_support"]["rate"], 0.5)
        self.assertEqual(transfer["donor_detached"]["valid_n"], 1)
        self.assertNotIn("grasp_maintained", summary["physical_flags"])

    def test_model_audit_and_independent_oracle_metrics_are_retained(self):
        record = self.record()
        record["selection"]["metadata"] = {
            "cache_hit": True, "repair_attempts": 1, "fallback_to_geometry": False,
            "requests": 2, "live_requests": 0, "usage": {"input_tokens": 100}, "live_usage": {},
            "calls": [{"phase": "initial", "validation": {"valid": False}},
                      {"phase": "repair", "validation": {"valid": True}}]}
        summary = summarize_sequence_records([record], self.plan(),
                                              acceptable_sets={"transport_place": ["G02"]})
        selection = summary["methods"]["M3"]["transport_place"]["selection"]
        self.assertEqual(selection["initial_model_valid_n"], 0)
        self.assertEqual(selection["model_usable_after_repair_n"], 1)
        self.assertEqual(selection["repair_trial_n"], 1)
        self.assertEqual(selection["acceptable_set"]["rate"], 1.0)
        self.assertEqual(selection["live_request_n"], 0)
        json.dumps(summary, allow_nan=False)

    def test_duplicate_identity_unknown_plan_and_retained_exception_episode_are_explicit(self):
        record = self.record()
        with self.assertRaisesRegex(ValueError, "duplicate saved"):
            self.group([record, deepcopy(record)])
        with self.assertRaisesRegex(ValueError, "duplicate sequence identities"):
            summarize_sequence_records([], self.plan() * 2)
        record = self.record(status="execution_error")
        record["episode"] = {"schema_version": "untrusted-payload", "collision": True}
        summary = self.group([record])
        self.assertEqual(summary["retained_exception_episode_n"], 1)
        self.assertEqual(summary["returned_n"], 0)
        self.assertEqual(summary["physical_flags"]["collision"]["valid_n"], 0)


if __name__ == "__main__":
    unittest.main()
