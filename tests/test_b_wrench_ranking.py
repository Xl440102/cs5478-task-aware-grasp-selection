from dataclasses import replace
import unittest
import tempfile
from unittest.mock import patch

from grasp_a.contracts import candidate_sha256
from grasp_b.wrench_v2.contacts import ContactStatus
from grasp_b.wrench_v2.m1 import development_config, development_task_sets
from grasp_b.wrench_v2.ranking import rank_results, rank_candidates
from tests import test_b_wrench_contacts as contacts
from tests import test_b_wrench_tasks as tasks
from tests import test_b_wrench_m1 as fixtures


class RankingTests(unittest.TestCase):
    def test_smoke_export_refuses_existing_directory_before_scoring(self):
        from grasp_b.wrench_v2 import m1_smoke
        with tempfile.TemporaryDirectory() as directory, patch("sys.argv", ["m1_smoke", "--output-dir", directory]), patch.object(
                m1_smoke, "run_smoke", side_effect=AssertionError("must refuse before scoring")):
            with self.assertRaises(FileExistsError):
                m1_smoke.main()

    def test_full_permutation_scored_first_then_id_tail_and_ties(self):
        a = fixtures.score(spec=replace(contacts.spec(), candidate_id="A"))
        b = fixtures.score(spec=replace(contacts.spec(), candidate_id="B"))
        c = fixtures.score(spec=replace(contacts.spec(), candidate_id="C", status=ContactStatus.UNSUPPORTED,
                                       reasons=("unsupported fixture",), contacts=()))
        result = rank_results([c, b, a], ["C", "B", "A"])
        self.assertEqual(result["ranking"], ["A", "B", "C"])
        self.assertEqual(result["selected_id"], "A")
        self.assertEqual(rank_results([b, a, c], ["B", "A", "C"])["ranking"], result["ranking"])

    def test_no_scored_pool_does_not_invent_a_recommendation(self):
        r = fixtures.score(tws=tasks.task(model=tasks.body(com_m=(0, .001, 0))))
        result = rank_results([r], [r.candidate_id])
        self.assertEqual(result["ranking"], [r.candidate_id])
        self.assertIsNone(result["selected_id"])
        self.assertEqual(result["selection_status"], "no_scored_candidate")
        self.assertEqual(rank_results([], [])["ranking"], [])

    def test_duplicate_unknown_and_mixed_context_results_rejected(self):
        r = fixtures.score()
        for ids, rows in (([r.candidate_id]*2, [r]*2), (["unknown"], [r]), ([], [r])):
            with self.assertRaises(ValueError):
                rank_results(rows, ids)
        other = fixtures.score(spec=replace(contacts.spec(), candidate_id="B"),
                               tws=tasks.task(model=tasks.body(mass_kg=.56)))
        with self.assertRaises(ValueError):
            rank_results([r, other], [r.candidate_id, "B"])

    def test_real_candidate_preparation_reused_across_tasks_public_fields_unchanged(self):
        from tests import test_b_wrench_adapter as geometry
        from grasp_b.wrench_v2 import m1
        geometry.StaticAdapterTests.setUpClass()
        f = geometry.StaticAdapterTests()
        cs = list(f.candidates)
        ids = [c.id for c in cs if f.by_id[c.id]["family"] == "side"][:2]
        before = candidate_sha256(cs)
        public = [c.public_metadata() for c in cs]
        config = development_config()
        options = dict(object_geometry=f.obj, gripper_geometry=f.gripper, config=config,
            task_sets=development_task_sets(tasks.body(mass_kg=.28), config),
            generator_infos={c.id: f.info(c) for c in cs}, pool_scope="caller_supplied_a_feasible")
        # Scope label tests a caller contract; these IDs are a static fixture,
        # not a claim that A feasibility was executed in this unit test.
        with patch.object(m1, "build_contact_spec", wraps=m1.build_contact_spec) as adapter, patch.object(
                m1, "build_primitive_wrenches", wraps=m1.build_primitive_wrenches) as primitives:
            result = rank_candidates(cs, ids, **options)
        self.assertEqual(adapter.call_count, 2)
        self.assertEqual(primitives.call_count, 2)
        self.assertEqual(candidate_sha256(cs), before)
        self.assertEqual([c.public_metadata() for c in cs], public)
        for task in result["tasks"].values():
            self.assertEqual(set(task["ranking"]), set(ids))
            self.assertEqual(len(task["ranking"]), len(ids))
            self.assertIn(task["selected_id"], ids)
        for invalid in ([ids[0], ids[0]], ["unknown"]):
            with self.assertRaises(ValueError):
                rank_candidates(cs, invalid, **options)
        diagnostic = rank_candidates(cs, [], **{**options, "pool_scope": "generator_pool_diagnostic"})
        self.assertIsNone(diagnostic["a_feasible_count"])


if __name__ == "__main__":
    unittest.main()
