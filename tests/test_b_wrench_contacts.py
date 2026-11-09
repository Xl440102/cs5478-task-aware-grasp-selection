"""Schema-only synthetic fixtures; none are physically validated grasps.

No geometry generator, PyBullet world, GWS, or execution outcome is used here.
"""
from dataclasses import FrozenInstanceError, fields, replace
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

from grasp_b.wrench_v2 import (
    CONTACT_SPEC_VERSION, ContactMechanism, ContactModel, ContactParameters,
    ContactPoint, ContactSource, ContactSpec, ContactStatus, FingerCapacityGroup,
    FingerID, GraspFamily,
)


def point(finger=FingerID.LEFT, **changes):
    sign = 1 if finger is FingerID.LEFT else -1
    values = dict(position_m=(sign*.035, 0., 0.),
                  normal_outward=(sign, 0., 0.), finger_id=finger,
                  surface_id=f"body:outer:{finger.value}",
                  contact_model=ContactModel.PCWF,
                  source=ContactSource.PREDICTED_ANALYTICAL,
                  capacity_group_id=finger.value)
    values.update(changes)
    return ContactPoint(**values)


def spec(**changes):
    values = dict(
        schema_version=CONTACT_SPEC_VERSION, candidate_id="B3_schema_fixture",
        candidate_hash="a"*64, generator_name="synthetic_schema_fixture",
        generator_version="test_only_v1", family=GraspFamily.BODY,
        mechanism=ContactMechanism.BODY_SIDE, object_frame="mug",
        reference_point_m=(0., 0., 0.),
        contacts=(point(), point(FingerID.RIGHT)), status=ContactStatus.VALID,
        provenance={"fixture_only": True, "geometry": {"sources": ["synthetic"]}},
        assumptions=("Synthetic declared surfaces; no physical validation.",),
        capacity_groups=tuple(FingerCapacityGroup(group_id=f.value, finger_id=f)
                              for f in FingerID),
        closing_direction=(1., 0., 0.), approach_direction=(0., 1., 0.))
    values.update(changes)
    return ContactSpec(**values)


class ContactRecordTests(unittest.TestCase):
    def test_construct_and_round_trip_full_json_record(self):
        model = spec()
        restored = ContactSpec.from_dict(json.loads(json.dumps(model.to_dict(), allow_nan=False)))
        self.assertEqual(model, restored)
        self.assertEqual(restored.schema_version, "contact_spec_v2")
        self.assertIs(restored.contacts[0].finger_id, FingerID.LEFT)
        self.assertEqual(model.object_frame, "mug")

    def test_dataclasses_and_nested_records_are_immutable(self):
        model = spec()
        for record, name, value in ((model, "candidate_id", "changed"),
                                    (model.contacts[0], "position_m", (0., 0., 0.)),
                                    (model.contacts[0].parameters, "friction_coefficient", 10.),
                                    (model.capacity_groups[0], "normal_force_budget_n", 100.)):
            with self.subTest(record=type(record).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(record, name, value)
        with self.assertRaises(TypeError):
            model.provenance["geometry"]["sources"][0] = "changed"
        with self.assertRaises(TypeError):
            model.provenance["geometry"]["extra"] = "changed"

    def test_mutable_inputs_and_serialized_outputs_do_not_alias_internal_data(self):
        provenance = {"sources": ["synthetic", {"version": 1}]}
        position = [.035, 0., 0.]
        left = point(position_m=position)
        contacts = [left, point(FingerID.RIGHT)]
        assumptions = ["Synthetic only"]
        model = spec(provenance=provenance, contacts=contacts, assumptions=assumptions)
        provenance["sources"][1]["version"] = 99
        position[0] = 99
        contacts.clear()
        assumptions.clear()
        exported = model.to_dict()
        exported["provenance"]["sources"][1]["version"] = 10
        self.assertEqual(model.provenance["sources"][1]["version"], 1)
        self.assertEqual(model.contacts[0].position_m[0], .035)
        self.assertEqual(len(model.contacts), 2)
        self.assertEqual(model.assumptions, ("Synthetic only",))

    def test_schema_and_fingerprint_format_are_strict(self):
        for changes in ({"schema_version": "contact_spec_v1"},
                        {"candidate_hash": "not-a-hash"}, {"candidate_hash": "a"*63},
                        {"candidate_id": " "}, {"generator_version": ""},
                        {"generator_name": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                spec(**changes)

    def test_unknown_fields_and_implicit_units_are_rejected_on_load(self):
        for target, key in (("spec", "position_unit"), ("point", "force_closure"),
                            ("parameters", "motor_force_n"), ("group", "per_sample_budget_n")):
            data = spec().to_dict()
            record = {"spec": data, "point": data["contacts"][0],
                      "parameters": data["contacts"][0]["parameters"],
                      "group": data["capacity_groups"][0]}[target]
            record[key] = "mm"
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "fields"):
                ContactSpec.from_dict(data)

    def test_provenance_rejects_non_json_nonfinite_and_cyclic_values(self):
        cycle = []
        cycle.append(cycle)
        for provenance in ({}, {"bad": float("nan")}, {"bad": float("inf")},
                           {"bad": {1, 2}}, {1: "non-string key"}, {"cycle": cycle}):
            with self.subTest(provenance=repr(provenance)), self.assertRaises(ValueError):
                spec(provenance=provenance)

    def test_identity_binding_uses_full_single_candidate_contract_hash(self):
        from grasp_a.contracts import Candidate, Pose, candidate_sha256
        candidate = Candidate("B3H_fixture", "handle", "synthetic", .02, 0.,
                              Pose((0., 0., 0.), (0., 0., 0., 1.)),
                              Pose((0., 0., -.1), (0., 0., 0., 1.)))
        fingerprint = candidate_sha256([candidate])
        model = spec(candidate_id=candidate.id, candidate_hash=fingerprint)
        model.assert_candidate_identity(candidate.id, fingerprint)
        with self.assertRaisesRegex(ValueError, "identity"):
            model.assert_candidate_identity("another-id", fingerprint)
        moved = replace(candidate, grasp_pose=Pose((.01, 0., 0.), candidate.grasp_pose.quaternion))
        with self.assertRaisesRegex(ValueError, "identity"):
            model.assert_candidate_identity(candidate.id, candidate_sha256([moved]))


class GeometryValidationTests(unittest.TestCase):
    def test_bad_normals_are_rejected_without_repair(self):
        for normal in ((2., 0., 0.), (0., 0., 0.), (.999, 0., 0.),
                       (float("nan"), 0., 1.), (float("inf"), 0., 0.)):
            with self.subTest(normal=normal), self.assertRaises(ValueError):
                point(normal_outward=normal)

    def test_positions_and_reference_reject_dimensions_nonfinite_and_coercion(self):
        for vector in ((0., 1.), (0., 1., 2., 3.), (float("nan"), 0., 0.),
                       (0., float("-inf"), 0.), (".035", 0., 0.), (True, 0., 0.)):
            with self.subTest(vector=vector):
                with self.assertRaises(ValueError):
                    point(position_m=vector)
                with self.assertRaises(ValueError):
                    spec(reference_point_m=vector)

    def test_directions_must_be_unit_and_orthogonal_and_are_retained(self):
        for changes in ({"closing_direction": (2., 0., 0.)},
                        {"approach_direction": (0., 0., 0.)},
                        {"approach_direction": (1., 0., 0.)},
                        {"approach_direction": (0., float("nan"), 0.)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                spec(**changes)
        axis = (math.sqrt(.5), math.sqrt(.5), 0.)
        model = spec(closing_direction=axis, approach_direction=(0., 0., -1.))
        self.assertEqual(model.closing_direction, axis)

    def test_only_mug_frame_is_accepted(self):
        for frame in ("world", "tool", "mug_mm", ""):
            with self.subTest(frame=frame), self.assertRaisesRegex(ValueError, "object_frame"):
                spec(object_frame=frame)

    def test_illegal_finger_surface_or_enum_is_rejected(self):
        for changes in ({"finger_id": "palm"}, {"finger_id": "third"},
                        {"surface_id": ""}, {"source": "observed"},
                        {"contact_model": "magic"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                point(**changes)

    def test_body_and_top_are_distinct_despite_both_contacting_sidewalls(self):
        body = spec()
        top = spec(family=GraspFamily.TOP, mechanism=ContactMechanism.BODY_TOP_DOWN,
                   approach_direction=(0., 0., -1.))
        self.assertEqual(top.contacts, body.contacts)
        self.assertNotEqual(top.family, body.family)
        self.assertEqual(top.contacts[0].surface_id, "body:outer:left")

    def test_rim_inner_outer_opposing_normals_are_legal(self):
        outer = point(position_m=(.03525, 0., .03), surface_id="wall:0:outer")
        inner = point(FingerID.RIGHT, position_m=(.03175, 0., .03), surface_id="wall:0:inner")
        model = spec(family="rim", mechanism="rim_inner_outer", contacts=(outer, inner),
                     approach_direction=(0., 0., -1.))
        self.assertEqual(model.contacts[0].normal_outward, (1., 0., 0.))
        self.assertEqual(model.contacts[1].normal_outward, (-1., 0., 0.))

    def test_handle_mechanisms_preserve_different_closing_axes(self):
        side_points = (point(position_m=(.006, .086, 0.), surface_id="outer_bar:+x"),
                       point(FingerID.RIGHT, position_m=(-.006, .086, 0.), surface_id="outer_bar:-x"))
        side = spec(family="handle", mechanism="handle_side", contacts=side_points,
                    approach_direction=(0., -1., 0.))
        z_points = (point(position_m=(0., .06, .035), normal_outward=(0., 0., 1.),
                          surface_id="upper_bar:+z"),
                    point(FingerID.RIGHT, position_m=(0., .06, .023), normal_outward=(0., 0., -1.),
                          surface_id="upper_bar:-z"))
        pinch = spec(family="handle", mechanism="handle_bar_pinch", contacts=z_points,
                     closing_direction=(0., 0., 1.), approach_direction=(1., 0., 0.))
        top = spec(family="handle", mechanism="handle_top_down", contacts=side_points,
                   approach_direction=(0., 0., -1.))
        self.assertNotEqual(side.mechanism, pinch.mechanism)
        self.assertNotEqual(side.closing_direction, pinch.closing_direction)
        self.assertNotEqual(top.mechanism, side.mechanism)
        self.assertEqual(ContactSpec.from_dict(pinch.to_dict()), pinch)

    def test_family_mechanism_conflicts_are_rejected(self):
        for family, mechanism in (("body", "handle_side"), ("top", "body_side"),
                                  ("rim", "body_top_down"), ("handle", "rim_inner_outer"),
                                  ("side", "body_side")):
            with self.subTest(family=family, mechanism=mechanism), self.assertRaises(ValueError):
                spec(family=family, mechanism=mechanism)


class PhysicsValidationTests(unittest.TestCase):
    def test_unknown_parameters_remain_none(self):
        model = spec()
        self.assertIsNone(model.contacts[0].parameters.friction_coefficient)
        self.assertIsNone(model.contacts[0].parameters.normal_force_limit_n)
        self.assertIsNone(model.contacts[0].parameters.torsional_moment_per_normal_m)
        self.assertIsNone(model.capacity_for_finger(FingerID.LEFT).normal_force_budget_n)
        self.assertFalse(model.contacts[0].parameters.calibrated)

    def test_negative_and_nonfinite_physics_rejected(self):
        for value in (-1., float("nan"), float("inf"), True, "15"):
            with self.subTest(value=value):
                for name in ("friction_coefficient", "normal_force_limit_n", "torsional_moment_per_normal_m"):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        ContactParameters(**{name: value}, source="synthetic parameter")
                with self.assertRaises(ValueError):
                    FingerCapacityGroup(group_id="left", finger_id="left",
                                        normal_force_budget_n=value, source="synthetic parameter")

    def test_zero_limits_and_friction_are_explicitly_supported(self):
        parameters = ContactParameters(friction_coefficient=0., normal_force_limit_n=0.,
                                       source="synthetic zero bound")
        left = point(parameters=parameters, contact_model=ContactModel.FRICTIONLESS_POINT)
        groups = (FingerCapacityGroup(group_id="left", finger_id="left", normal_force_budget_n=0.,
                                      source="synthetic zero bound"), spec().capacity_groups[1])
        model = spec(contacts=(left, point(FingerID.RIGHT)), capacity_groups=groups)
        self.assertEqual(model.capacity_for_finger("left").normal_force_budget_n, 0.)

    def test_supplied_parameters_need_source_and_calibration_is_not_invented(self):
        with self.assertRaisesRegex(ValueError, "source"):
            ContactParameters(friction_coefficient=.4)
        with self.assertRaisesRegex(ValueError, "source"):
            FingerCapacityGroup(group_id="left", finger_id="left", normal_force_budget_n=3.)
        for changes in ({"calibrated": True}, {"calibrated": "yes"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ContactParameters(**changes)
        parameters = ContactParameters(friction_coefficient=.4, source="synthetic calibration fixture",
                                       calibrated=True)
        self.assertIsNone(parameters.normal_force_limit_n)
        self.assertEqual(ContactParameters.from_dict(parameters.to_dict()), parameters)

    def test_contact_model_parameter_compatibility(self):
        with self.assertRaisesRegex(ValueError, "frictionless"):
            point(contact_model="frictionless_point",
                  parameters=ContactParameters(friction_coefficient=.4, source="fixture"))
        parameters = ContactParameters(torsional_moment_per_normal_m=.001, source="synthetic only")
        with self.assertRaisesRegex(ValueError, "soft_finger"):
            point(parameters=parameters)
        soft = point(contact_model="soft_finger", parameters=parameters)
        self.assertEqual(soft.parameters.torsional_moment_per_normal_m, .001)
        self.assertIsNone(point(contact_model="soft_finger").parameters.torsional_moment_per_normal_m)

    def test_multiple_samples_share_one_budget_without_multiplication(self):
        local = ContactParameters(normal_force_limit_n=7., source="synthetic local bound")
        left = point(parameters=local)
        group = FingerCapacityGroup(group_id="left", finger_id="left", normal_force_budget_n=9.,
                                    source="synthetic aggregate bound")
        model = spec(contacts=(left, point(FingerID.RIGHT)),
                     capacity_groups=(group, spec().capacity_groups[1]))
        sampled = replace(model, contacts=(left, replace(left, position_m=(.035, 0., .001)),
                                          replace(left, position_m=(.035, 0., -.001)), point(FingerID.RIGHT)))
        self.assertEqual(sampled.capacity_for_finger("left"), model.capacity_for_finger("left"))
        self.assertEqual(sampled.capacity_for_finger("left").normal_force_budget_n, 9.)
        unknown = replace(sampled, capacity_groups=spec().capacity_groups)
        self.assertIsNone(unknown.capacity_for_finger("left").normal_force_budget_n)

    def test_bad_group_references_and_splitting_finger_budgets_rejected(self):
        for contacts, groups in (
            ((point(capacity_group_id="missing"), point(FingerID.RIGHT)), spec().capacity_groups),
            ((point(capacity_group_id="right"), point(FingerID.RIGHT)), spec().capacity_groups),
            (spec().contacts, (spec().capacity_groups[0], spec().capacity_groups[0])),
            (spec().contacts, (*spec().capacity_groups, FingerCapacityGroup(group_id="extra", finger_id="left"))),
            ((point(), point(), point(FingerID.RIGHT)), spec().capacity_groups),
        ):
            with self.subTest(contacts=contacts, groups=groups), self.assertRaises(ValueError):
                spec(contacts=contacts, capacity_groups=groups)

    def test_group_identity_and_calibration_metadata_are_validated(self):
        for changes in ({"group_id": ""}, {"finger_id": "palm"},
                        {"calibrated": True}, {"calibrated": 1}):
            values = dict(group_id="left", finger_id="left")
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                FingerCapacityGroup(**values)

    def test_wrong_nested_record_types_are_rejected(self):
        for changes in ({"contacts": ({},)}, {"capacity_groups": ({},)},
                        {"contacts": "left"}, {"assumptions": "a string is not a list"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                spec(**changes)
        with self.assertRaises(ValueError):
            point(parameters={"friction_coefficient": .4})


class StatusAndBoundaryTests(unittest.TestCase):
    def test_all_statuses_preserve_identity_and_reasons(self):
        for status in ContactStatus:
            changes = {} if status is ContactStatus.VALID else {"reasons": ("synthetic diagnostic reason",)}
            if status in (ContactStatus.UNSUPPORTED, ContactStatus.UNKNOWN, ContactStatus.INVALID):
                changes.update(contacts=(), capacity_groups=(), closing_direction=None, approach_direction=None)
            model = spec(status=status, **changes)
            self.assertEqual(model.candidate_id, "B3_schema_fixture")
            self.assertEqual(ContactSpec.from_dict(model.to_dict()), model)
            if status is not ContactStatus.VALID:
                self.assertEqual(model.reasons, ("synthetic diagnostic reason",))

    def test_nonvalid_status_needs_reason_and_approximate_needs_assumption(self):
        for status in ContactStatus:
            if status is not ContactStatus.VALID:
                with self.subTest(status=status), self.assertRaisesRegex(ValueError, "reasons"):
                    spec(status=status)
        with self.assertRaisesRegex(ValueError, "assumptions"):
            spec(status="approximate", assumptions=(), reasons=("idealized surface",))

    def test_valid_and_approximate_need_both_fingers_and_directions(self):
        for status in (ContactStatus.VALID, ContactStatus.APPROXIMATE):
            for changes in ({"contacts": ()}, {"contacts": (point(),)},
                            {"closing_direction": None}, {"approach_direction": None}):
                with self.subTest(status=status, changes=changes), self.assertRaises(ValueError):
                    spec(status=status, reasons=("fixture assumption",), **changes)

    def test_invalid_status_does_not_allow_malformed_numeric_payload(self):
        with self.assertRaises(ValueError):
            spec(status="invalid", reasons=("known geometric violation",),
                 reference_point_m=(float("nan"), 0., 0.))

    def test_execution_source_rejected_in_constructor_and_deserialization(self):
        with self.assertRaisesRegex(ValueError, "observed_execution"):
            point(source=ContactSource.OBSERVED_EXECUTION)
        data = spec().to_dict()
        data["contacts"][0]["source"] = "observed_execution"
        with self.assertRaisesRegex(ValueError, "observed_execution"):
            ContactSpec.from_dict(data)
        self.assertEqual(point(source="predicted_geometry_query").source,
                         ContactSource.PREDICTED_GEOMETRY_QUERY)

    def test_public_candidate_and_selector_snapshot_are_unchanged(self):
        from grasp_a.contracts import Candidate, Pose
        expected = ("id", "region", "approach", "gripper_width", "geometry_score",
                    "grasp_pose", "pregrasp_pose")
        self.assertEqual(tuple(f.name for f in fields(Candidate)), expected)
        candidate = Candidate("B3_fixture", "body", "synthetic", .077, 0.,
                              Pose((0., 0., 0.), (0., 0., 0., 1.)),
                              Pose((0., 0., -.1), (0., 0., 0., 1.)))
        before = candidate.public_metadata()
        spec(candidate_id=candidate.id)
        self.assertEqual(candidate.public_metadata(), before)
        self.assertEqual(set(before), set(expected[:5]))

    def test_model_import_requires_only_standard_library(self):
        script = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'grasp_a', 'grasp_c', 'pybullet', 'pybullet_data', 'numpy', 'scipy'}:
        raise AssertionError('forbidden dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from grasp_b.wrench_v2 import ContactSpec, ContactParameters
assert ContactParameters().friction_coefficient is None
'''
        result = subprocess.run([sys.executable, "-B", "-c", script],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
