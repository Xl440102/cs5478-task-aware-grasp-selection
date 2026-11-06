"""Pure NumPy shared normal-force constraints; no LP backend required."""
from dataclasses import replace
import json
import unittest

import numpy as np

from grasp_b.wrench_v2.contacts import ContactParameters
from grasp_b.wrench_v2.constraints import (
    CapacityValue, ConstraintStatus, build_capacity_constraints, validate_constraints,
)
from grasp_b.wrench_v2.gws import FrictionValue, NumericalInputError, build_primitive_wrenches
from tests import test_b_wrench_contacts as fixtures

MU = FrictionValue(coefficient=.5, source="mu_assumed_for_numerical_fixture")


def capacity(value):
    return CapacityValue(normal_force_n=value, source="normal_budget_assumed_for_numerical_fixture")


def data(k=8, mu=MU, spec=None):
    return build_primitive_wrenches(fixtures.spec() if spec is None else spec, friction=mu, k=k)


class CapacityConstraintTests(unittest.TestCase):
    def test_missing_group_budgets_produce_no_numeric_constraint_matrix(self):
        d = data()
        c = build_capacity_constraints(d)
        self.assertIs(c.status, ConstraintStatus.MISSING_PHYSICS_PARAMETERS)
        self.assertIsNone(c.matrix)
        self.assertIsNone(c.bounds_n)
        self.assertEqual(len(c.missing_parameters), 2)
        self.assertEqual(c.unknown_local_contacts, (0, 1))
        validate_constraints(d, c)

    def test_independent_fingers_and_one_row_for_all_directions(self):
        d = data()
        c = build_capacity_constraints(d, finger_budgets={"left": capacity(2), "right": capacity(4)})
        self.assertEqual(c.matrix.shape, (2, 16))
        np.testing.assert_array_equal(c.matrix[0], [1]*8+[0]*8)
        np.testing.assert_array_equal(c.matrix[1], [0]*8+[1]*8)
        np.testing.assert_array_equal(c.bounds_n, [2., 4.])
        self.assertEqual(c.rows[0].unit, "N")
        self.assertFalse(c.rows[0].calibrated)
        self.assertIsNone(d.capacity_groups[0].normal_force_budget_n)

    def test_extra_samples_and_k_do_not_multiply_capacity(self):
        spec = fixtures.spec()
        spec = replace(spec, contacts=(*spec.contacts, replace(spec.contacts[0], surface_id="duplicate_numeric_sample")))
        for k in (8, 16, 32):
            c = build_capacity_constraints(data(k, spec=spec), finger_budgets={"left": capacity(1), "right": capacity(3)})
            self.assertEqual(len(c.rows), 2)
            self.assertEqual(c.bounds_n[0], 1.)
            self.assertEqual(int(c.matrix[0].sum()), 2*k)
            self.assertEqual(int(c.matrix[1].sum()), k)

    def test_column_permutation_uses_metadata_not_fixed_positions(self):
        d = data()
        order = np.arange(len(d.columns))[::-1]
        moved = replace(d, matrix=d.matrix[:, order], columns=tuple(d.columns[i] for i in order))
        c = build_capacity_constraints(moved, finger_budgets={"left": capacity(2), "right": capacity(4)})
        np.testing.assert_array_equal(c.matrix[0], [0]*8+[1]*8)
        validate_constraints(moved, c)

    def test_local_limits_are_additional_rows_and_scale_with_utilization(self):
        spec = fixtures.spec()
        local = ContactParameters(normal_force_limit_n=.5, source="local_numeric_fixture")
        spec = replace(spec, contacts=(replace(spec.contacts[0], parameters=local), spec.contacts[1]))
        d = data(spec=spec)
        c = build_capacity_constraints(d, finger_budgets={"left": capacity(2), "right": capacity(3)},
                                       contact_limits={1: capacity(.75)})
        self.assertEqual(c.matrix.shape, (4, 16))
        self.assertEqual([r.kind for r in c.rows], ["finger", "finger", "contact", "contact"])
        np.testing.assert_array_equal(c.bounds_n, [2., 3., .5, .75])
        validate_constraints(d, c)
        strict = build_capacity_constraints(data(), finger_budgets={"left": capacity(1), "right": capacity(1)},
                                            require_local_limits=True)
        self.assertIs(strict.status, ConstraintStatus.MISSING_PHYSICS_PARAMETERS)

    def test_known_spec_budget_and_explicit_override_keep_provenance(self):
        spec = fixtures.spec()
        group = replace(spec.capacity_groups[0], normal_force_budget_n=2., source="declared_fixture")
        d = data(spec=replace(spec, capacity_groups=(group, spec.capacity_groups[1])))
        c = build_capacity_constraints(d, finger_budgets={"right": capacity(3)})
        self.assertEqual(c.rows[0].origin, "contact_spec")
        self.assertEqual(c.rows[1].origin, "external_explicit")
        override = build_capacity_constraints(d, finger_budgets={"left": capacity(4), "right": capacity(3)})
        self.assertEqual(override.bounds_n[0], 4.)
        self.assertEqual(d.capacity_groups[0].normal_force_budget_n, 2.)

    def test_bad_capacities_units_and_unknown_override_keys_rejected(self):
        for value in (-1, float("nan"), float("inf"), True, "2"):
            with self.subTest(value=value), self.assertRaises(NumericalInputError):
                capacity(value)
        with self.assertRaises(NumericalInputError):
            CapacityValue(normal_force_n=2, source="unknown")
        with self.assertRaises(NumericalInputError):
            CapacityValue(normal_force_n=2, source="fixture", unit="mN")
        for options in ({"finger_budgets": {"typo": capacity(2)}},
                        {"contact_limits": {True: capacity(2)}}, {"finger_budgets": {"left": 2.}}):
            with self.subTest(options=options), self.assertRaises(NumericalInputError):
                build_capacity_constraints(data(), **options)

    def test_zero_budgets_are_valid_declared_constraints(self):
        c = build_capacity_constraints(data(), finger_budgets={"left": capacity(0), "right": capacity(0)})
        self.assertIs(c.status, ConstraintStatus.READY)
        np.testing.assert_array_equal(c.bounds_n, [0., 0.])

    def test_tampered_stale_or_malformed_constraints_rejected(self):
        d = data()
        c = build_capacity_constraints(d, finger_budgets={"left": capacity(1), "right": capacity(1)})
        for changed in (replace(c, matrix=np.zeros(c.matrix.shape)), replace(c, bounds_n=np.ones(2)*99),
                        replace(c, candidate_hash="0"*64)):
            with self.subTest(changed=changed.candidate_hash), self.assertRaises(NumericalInputError):
                validate_constraints(d, changed)
        with self.assertRaises(NumericalInputError):
            validate_constraints(replace(d, matrix=d.matrix*2), c)
        with self.assertRaises(NumericalInputError):
            build_capacity_constraints(replace(d, columns=d.columns[:-1]))
        bad = replace(d.columns[0], capacity_group_id="another")
        with self.assertRaises(NumericalInputError):
            build_capacity_constraints(replace(d, columns=(bad, *d.columns[1:])))

    def test_serialization_and_arrays_are_detached_and_read_only(self):
        c = build_capacity_constraints(data(), finger_budgets={"left": capacity(1), "right": capacity(1)})
        exported = c.to_dict()
        json.dumps(exported, allow_nan=False)
        exported["bounds_n"][0] = 99
        self.assertEqual(c.bounds_n[0], 1.)
        with self.assertRaises(ValueError):
            c.matrix[0, 0] = 0.


if __name__ == "__main__":
    unittest.main()
