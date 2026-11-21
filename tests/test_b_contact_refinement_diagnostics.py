"""Real HiGHS mathematical fixtures; no execution or selector access."""
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json
import unittest
from unittest.mock import patch

import numpy as np

from grasp_b.contact_refinement.diagnostics import (
    Classification as C, DiagnosticRecord, DiagnosticTolerances, diagnose, verify_certificate,
)
from grasp_b.wrench_v2.gws import WrenchScaling, build_primitive_wrenches, grasp_matrix
from grasp_b.wrench_v2.contacts import ContactStatus, ContactParameters
from grasp_b.wrench_v2.constraints import build_capacity_constraints
from tests.test_b_wrench_constraints import MU, capacity
from tests.test_b_wrench_contacts import spec as fixture_spec

SCALE = WrenchScaling(reference_force_n=1., characteristic_length_m=.1)


def model(positions=None, normals=None):
    s = fixture_spec()
    if positions is not None:
        points = []
        for i, p in enumerate(positions):
            template = s.contacts[i % 2]
            points.append(replace(template, position_m=tuple(p),
                                  normal_outward=tuple(normals[i]), surface_id=f"fixture:{i}"))
        s = replace(s, contacts=tuple(points))
    a = build_primitive_wrenches(s, friction=MU, k=16)
    b = build_capacity_constraints(a, finger_budgets={"left": capacity(1), "right": capacity(1)})
    return s, a, b


class StructuralTests(unittest.TestCase):
    def test_two_point_rank5_witness_independently_verified(self):
        s, a, b = model([[-.03, 0, 0], [.03, 0, 0]], [[-1, 0, 0], [1, 0, 0]])
        w = [0, 0, 0, .001, 0, 0]
        r = diagnose(s, a, w, scaling=SCALE, constraints=b)
        self.assertEqual(r.classification, C.STRUCTURAL)
        self.assertEqual(r.payload["rank"], 5)
        self.assertEqual(sum(r.payload["lp_calls"].values()), 0)
        self.assertTrue(verify_certificate(grasp_matrix(s), a.matrix, w, scaling=SCALE,
                                           certificate=r.payload["certificate"]))
        cert = dict(r.to_dict()["certificate"], y_scaled=[0.] * 6)
        self.assertFalse(verify_certificate(grasp_matrix(s), a.matrix, w, scaling=SCALE, certificate=cert))

    def test_near_rank_boundary_is_unknown(self):
        s, a, b = model([[-.03, 0, 0], [.03, 0, 0], [0, 1e-12, 0]],
                        [[-1, 0, 0], [1, 0, 0], [0, -1, 0]])
        singular = np.linalg.svd(np.diag([1, 1, 1, 10, 10, 10]) @ grasp_matrix(s), compute_uv=False)
        tol = DiagnosticTolerances(rank_absolute=float(singular[-1]), rank_relative=1e-16)
        r = diagnose(s, a, np.zeros(6), scaling=SCALE, constraints=b, tolerances=tol)
        self.assertEqual(r.classification, C.UNKNOWN)
        self.assertEqual(r.payload["numerical_status"], "near_rank_boundary")

    def test_identity_and_reference_mismatch(self):
        s, a, b = model()
        for bad in (replace(a, candidate_id="wrong"), replace(a, matrix=a.matrix * 2)):
            self.assertEqual(diagnose(s, bad, [0]*6, scaling=SCALE).classification, C.UNKNOWN)
        self.assertEqual(diagnose(s, a, [0]*6, scaling=SCALE, reference_point_m=(1, 0, 0)).classification, C.UNKNOWN)
        self.assertEqual(diagnose(s, a, [0]*6, scaling=SCALE,
                                 constraints=replace(b, candidate_hash="f"*64)).classification, C.UNKNOWN)

    def test_invalid_geometry_and_missing_inputs(self):
        s, a, b = model()
        invalid = replace(s, status=ContactStatus.INVALID, reasons=("fixture invalid",), contacts=())
        self.assertEqual(diagnose(invalid, None, [0]*6, scaling=SCALE).classification, C.GEOMETRY)
        self.assertEqual(diagnose(s, None, [0]*6, scaling=SCALE).classification, C.UNKNOWN)
        for w in ([0]*5, [float("nan")]*6, [float("inf")]*6):
            self.assertEqual(diagnose(s, a, w, scaling=SCALE).classification, C.UNKNOWN)

    def test_immutable_serialization_checksum(self):
        s, a, _ = model([[-.03, 0, 0], [.03, 0, 0]], [[-1, 0, 0], [1, 0, 0]])
        r = diagnose(s, a, [0, 0, 0, 1, 0, 0], scaling=SCALE)
        self.assertEqual(DiagnosticRecord.from_json(r.to_json()).to_json(), r.to_json())
        with self.assertRaises(TypeError):
            r.payload["rank"] = 6
        with self.assertRaises(FrozenInstanceError):
            r.payload = {}
        p = r.to_dict(); p["rank"] = 6
        with self.assertRaises(ValueError):
            DiagnosticRecord.from_json(json.dumps(p))

    def test_tolerances_and_explicit_scale(self):
        for key in ("rank_relative", "rank_absolute", "certificate_relative"):
            with self.assertRaises(ValueError):
                DiagnosticTolerances(**{key: float("nan")})
        s, a, _ = model()
        with self.assertRaises(ValueError):
            diagnose(s, a, [0]*6, scaling=None)

    def test_frozen_candidate_loading_preserves_exact_quaternion(self):
        from dataclasses import asdict
        from grasp_a.contracts import Candidate, Pose, candidate_sha256
        from grasp_b.contact_refinement.validation import exact_candidates
        p = Pose((0., 0., 0.), (0., 0., 0., 1.000000000000001))
        c = Candidate('identity_fixture', 'body', 'fixture', .077, 0., p, p)
        raw = asdict(c)
        restored = exact_candidates([raw])['identity_fixture']
        self.assertEqual(candidate_sha256([restored]), candidate_sha256([c]))
        self.assertNotEqual(candidate_sha256([Candidate.from_dict(raw)]), candidate_sha256([c]))
        with self.assertRaises(ValueError):
            exact_candidates([raw, raw])


@unittest.skipUnless(importlib.util.find_spec("scipy"), "real HiGHS requires SciPy")
class RealConeAndCapacityTests(unittest.TestCase):
    def test_rank5_specific_wrench_optimal_zero_and_overrun(self):
        s, a, b = model([[-.03, 0, 0], [.03, 0, 0]], [[-1, 0, 0], [1, 0, 0]])
        for w, expected in (([0]*6, C.WITHIN), ([0, 0, .5, 0, 0, 0], C.WITHIN),
                            ([0, 0, 2, 0, 0, 0], C.OVER)):
            r = diagnose(s, a, w, scaling=SCALE, constraints=b)
            self.assertEqual(r.classification, expected)
            self.assertEqual(r.payload["rank"], 5)
            self.assertLess(max(abs(v) for v in r.payload["cone_primal"]["equilibrium_residual"]), 1e-7)

    def test_rank6_is_not_force_closure_cone_certificate(self):
        positions = [[-.03, 0, 0], [.03, 0, 0], [0, .03, 0], [0, -.03, 0]]
        s, a, b = model(positions, [[0, 0, -1]] * 4)
        w = [0, 0, -1, 0, 0, 0]
        r = diagnose(s, a, w, scaling=SCALE, constraints=b)
        self.assertEqual(r.payload["rank"], 6)
        self.assertEqual(r.payload["primitive_rank"], 6)
        self.assertEqual(r.classification, C.CONE)
        self.assertTrue(verify_certificate(grasp_matrix(s), a.matrix, w, scaling=SCALE,
                                           certificate=r.payload["certificate"]))
        self.assertEqual(r.payload["lp_calls"]["capacity"], 0)

    def test_four_point_axial_torque_is_representable_under_fixture_assumptions(self):
        s, a, b = model([[-.03, -.01, 0], [.03, -.01, 0], [-.03, .01, 0], [.03, .01, 0]],
                        [[-1, 0, 0], [1, 0, 0], [-1, 0, 0], [1, 0, 0]])
        r = diagnose(s, a, [0, 0, 0, .001, 0, 0], scaling=SCALE, constraints=b)
        self.assertEqual(r.payload["rank"], 6)
        self.assertEqual(r.classification, C.WITHIN)
        self.assertEqual(len(b.rows), 2)

    def test_unverified_infeasibility_is_unknown(self):
        # Fault injection exercises classification, never certificate evidence.
        from types import SimpleNamespace
        s, a, b = model()
        with patch("scipy.optimize.linprog", side_effect=[
                SimpleNamespace(status=2, message="fixture infeasible"),
                SimpleNamespace(status=4, message="fixture missing certificate")]):
            r = diagnose(s, a, [0]*6, scaling=SCALE, constraints=b)
        self.assertEqual(r.classification, C.UNKNOWN)
        self.assertIsNone(r.payload["certificate"])

    def test_unverified_optimal_is_numerical_failure(self):
        from types import SimpleNamespace
        s, a, b = model()
        with patch("scipy.optimize.linprog", return_value=SimpleNamespace(
                status=0, message="fixture inconsistent optimal", x=np.ones(a.matrix.shape[1]))):
            r = diagnose(s, a, [1, 0, 0, 0, 0, 0], scaling=SCALE, constraints=b)
        self.assertEqual(r.classification, C.NUMERICAL)

    def test_missing_capacity_not_assumed(self):
        s, a, _ = model()
        r = diagnose(s, a, [0]*6, scaling=SCALE)
        self.assertEqual(r.classification, C.UNKNOWN)
        self.assertEqual(r.payload["numerical_status"], "missing_physics_parameters")

    def test_reference_shift_and_fixed_scaling_equivalence(self):
        s, a, b = model([[-.03, 0, 0], [.03, 0, 0]], [[-1, 0, 0], [1, 0, 0]])
        w = np.array([0, 0, .5, 0, 0, 0])
        base = diagnose(s, a, w, scaling=SCALE, constraints=b)
        shift = np.array([.01, .02, .03]); shifted = replace(s, reference_point_m=tuple(shift))
        aa = build_primitive_wrenches(shifted, friction=MU, k=16)
        bb = build_capacity_constraints(aa, finger_budgets={"left": capacity(1), "right": capacity(1)})
        ww = np.r_[w[:3], w[3:] - np.cross(shift, w[:3])]
        for scale in (SCALE, WrenchScaling(reference_force_n=3., characteristic_length_m=.2)):
            r = diagnose(shifted, aa, ww, scaling=scale, constraints=bb, reference_point_m=tuple(shift))
            self.assertEqual(r.classification, base.classification)
            self.assertAlmostEqual(r.payload["capacity_result"]["utilization"], base.payload["capacity_result"]["utilization"])

    def test_contact_declared_friction_provenance(self):
        s, _, _ = model()
        s = replace(s, contacts=tuple(replace(p, parameters=ContactParameters(
            friction_coefficient=.5, source="explicit fixture")) for p in s.contacts))
        a = build_primitive_wrenches(s, k=16)
        self.assertNotIn("identity mismatch", diagnose(s, a, [0]*6, scaling=SCALE).payload["reason"])

    def test_repeated_samples_share_budget(self):
        s, a, b = model()
        before = diagnose(s, a, [0, 0, .2, 0, 0, 0], scaling=SCALE, constraints=b)
        s = replace(s, contacts=(*s.contacts, *(replace(p, surface_id=p.surface_id+":repeat") for p in s.contacts)))
        a = build_primitive_wrenches(s, friction=MU, k=16)
        b = build_capacity_constraints(a, finger_budgets={"left": capacity(1), "right": capacity(1)})
        after = diagnose(s, a, [0, 0, .2, 0, 0, 0], scaling=SCALE, constraints=b)
        self.assertEqual(before.classification, after.classification)
        self.assertAlmostEqual(before.payload["capacity_result"]["utilization"], after.payload["capacity_result"]["utilization"])

    def test_backend_failure_is_not_infeasibility(self):
        # Fault injection only; not used as mathematical certificate evidence.
        from types import SimpleNamespace
        s, a, b = model()
        with patch("scipy.optimize.linprog", return_value=SimpleNamespace(status=4, message="fixture failure")):
            r = diagnose(s, a, [0]*6, scaling=SCALE, constraints=b)
        self.assertEqual(r.classification, C.NUMERICAL)
        self.assertIsNone(r.payload["certificate"])


if __name__ == "__main__":
    unittest.main()
