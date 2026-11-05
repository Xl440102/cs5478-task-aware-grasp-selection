"""One mug / one Panda controlled benchmark. No rigid attachment is used.

All simulation state lives in one PyBullet client. Every trial creates a fresh
world, so methods can be compared under exactly the same seed and candidates.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
import math
from pathlib import Path
import random

import numpy as np

from .contracts import Candidate, Pose, TASKS, candidate_sha256, validate_task_id
from .collision_rules import fixed_link_groups
from .control import ControlSession, ObservationConfig, Primitive, scene_randomization
from .evaluation import EpisodeEvaluator

try:
    import pybullet as p
    import pybullet_data
except ImportError as exc:
    raise ImportError("PyBullet missing. On Windows use: conda env create -f environment.yml") from exc

STEP = 1 / 240
CONTROLLER_VERSION = "observable_primitives_v2"
# Bullet's mesh contacts can report submillimetre numerical penetration.
# This pilot tolerance applies only to collision classification, not grasp contact.
PENETRATION_TOLERANCE_M = 0.001
TABLE_TOP = 0.60
MUG_CENTER_Z = TABLE_TOP + 0.045
# Fixed pilot standby pose: gripper above the mug-side pregrasp region. The
# previous standby crossed the tabletop during joint interpolation to G03.
# Freeze this pose together with the scene and candidates before evaluation.
ARM_HOME = (1.3934329285721332, 0.5211163979062032, -1.1352682332805228,
            -2.830430345242138, -2.8429055993398142, 1.7866499535174747,
            1.2101914604831052)
ASSETS = Path(__file__).resolve().parents[1] / "assets"
# Opt-in multi-start IK seeds (SceneRandomization.ik_restarts). Canonical Panda
# "ready" configurations with several wrist rolls, plus a low forward reach.
_READY = (0.0, -0.785, 0.0, -2.356, 0.0, 1.571)
IK_RESTART_POSES = tuple(
    [_READY + (j7,) for j7 in (0.785, -0.785, 2.356, -2.356)] +
    [(0.0, 0.3, 0.0, -1.9, 0.0, 2.2, j7) for j7 in (0.785, -0.785)] +
    [(0.0, -0.3, 0.0, -2.6, 0.0, 2.3, j7) for j7 in (0.785, -0.785)] +
    [ARM_HOME])


def orientation_error_deg(a, b) -> float:
    dot = min(1.0, abs(sum(x * y for x, y in zip(a, b))))
    return math.degrees(2.0 * math.acos(dot))


def position_error(a, b) -> float:
    return math.sqrt(sum((x-y)**2 for x, y in zip(a, b)))


@dataclass
class Feasibility:
    candidate_id: str
    feasible: bool
    reasons: list[str]
    pregrasp_position_error_m: float | None = None
    grasp_position_error_m: float | None = None
    min_table_clearance_m: float | None = None
    collision_details: list[dict] = field(default_factory=list)


class MugScene:
    MUG_ASSET = ASSETS / "mug.urdf"

    def __init__(self, seed: int, gui: bool = False,
                 observation_config: ObservationConfig | None = None, *,
                 scene_config=None):
        self.seed = seed
        self.scene_config = scene_randomization(scene_config)
        if self.scene_config.observation == "noisy_truth" and observation_config is not None:
            raise ValueError("noisy_truth scenes derive the estimate; omit observation_config")
        self.observation_config = observation_config or ObservationConfig()
        self.primitive_log = []
        self.physics_steps = 0
        self.evaluator = None
        self.client = p.connect(p.GUI if gui else p.DIRECT)
        if self.client < 0:
            raise RuntimeError("Could not start PyBullet")
        self.recorder = None
        self.ik_diagnostics = []
        try:
            p.resetSimulation(physicsClientId=self.client)
            p.setAdditionalSearchPath(pybullet_data.getDataPath(), physicsClientId=self.client)
            p.setTimeStep(STEP, physicsClientId=self.client)
            p.setGravity(0, 0, -9.81, physicsClientId=self.client)
            p.setPhysicsEngineParameter(numSolverIterations=150, deterministicOverlappingPairs=1,
                                        physicsClientId=self.client)
            self.plane = p.loadURDF("plane.urdf", physicsClientId=self.client)
            table_col = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.35, 0.45, 0.30],
                                               physicsClientId=self.client)
            table_vis = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.35, 0.45, 0.30],
                                            rgbaColor=[0.68, 0.55, 0.39, 1], physicsClientId=self.client)
            self.table = p.createMultiBody(baseMass=0, baseCollisionShapeIndex=table_col,
                                           baseVisualShapeIndex=table_vis, basePosition=[0.55, 0, 0.30],
                                           physicsClientId=self.client)
            self.robot = p.loadURDF("franka_panda/panda.urdf", [0, 0, TABLE_TOP],
                                    useFixedBase=True,
                                    flags=(p.URDF_USE_SELF_COLLISION |
                                           p.URDF_USE_SELF_COLLISION_EXCLUDE_PARENT),
                                    physicsClientId=self.client)
            joints = [p.getJointInfo(self.robot, j, physicsClientId=self.client)
                      for j in range(p.getNumJoints(self.robot, physicsClientId=self.client))]
            self.rigid_group = fixed_link_groups(
                [info[16] for info in joints],
                {j for j,info in enumerate(joints) if info[2] == p.JOINT_FIXED})
            self.arm = [self._joint_index(f"panda_joint{i}") for i in range(1, 8)]
            self.fingers = [self._joint_index("panda_finger_joint1"),
                            self._joint_index("panda_finger_joint2")]
            # Bullet IK arrays and return values follow all movable DOFs, not
            # just the seven arm joints. Panda also has two finger DOFs.
            self.movable = sorted(
                (j for j, info in enumerate(joints) if info[3] >= 0),
                key=lambda j: joints[j][3])
            self.arm_dofs = [self.movable.index(j) for j in self.arm]
            self.tip = self._link_index("panda_grasptarget")
            rng = random.Random(seed)
            # Same draw order as the legacy scene, so the default reproduces it.
            sc = self.scene_config
            start = [0.55+rng.uniform(*sc.x_range_m), rng.uniform(*sc.y_range_m), MUG_CENTER_Z]
            start_q = p.getQuaternionFromEuler([0, 0, rng.uniform(*sc.yaw_range_rad)])
            self.mug = p.loadURDF(str(self.MUG_ASSET), start, start_q,
                                  physicsClientId=self.client)
            for f in self.fingers:
                p.changeDynamics(self.robot, f, lateralFriction=2.0, spinningFriction=0.05,
                                 physicsClientId=self.client)
            p.changeDynamics(self.mug, -1, lateralFriction=1.2, physicsClientId=self.client)
            self.initial_mug_pose = Pose(tuple(start), tuple(start_q))
            derived = sc.estimate_config(seed, self.initial_mug_pose)
            if derived is not None:
                self.observation_config = derived
            # Control starts from a calibrated prior / external estimate, never
            # from start or getBasePositionAndOrientation. Independent RNG.
            config = self.observation_config
            sensor_rng = random.Random(config.noise_seed)
            observed_position = tuple(v + sensor_rng.uniform(-config.position_noise_m,
                                                             config.position_noise_m)
                                      for v in config.object_pose.position)
            yaw = math.radians(sensor_rng.uniform(-config.yaw_noise_deg, config.yaw_noise_deg))
            observed_q = p.multiplyTransforms(
                (0, 0, 0), p.getQuaternionFromEuler((0, 0, yaw)),
                (0, 0, 0), config.object_pose.quaternion)[1]
            self.observed_mug_pose = Pose(observed_position, tuple(observed_q))
            self._reset_robot()
        except Exception:
            self.close()
            raise

    def close(self):
        if self.recorder is not None:
            self.recorder.close()
            self.recorder = None
        if self.client is not None:
            p.disconnect(physicsClientId=self.client)
            self.client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _joint_index(self, name: str) -> int:
        for i in range(p.getNumJoints(self.robot, physicsClientId=self.client)):
            if p.getJointInfo(self.robot, i, physicsClientId=self.client)[1].decode() == name:
                return i
        raise RuntimeError(f"Panda joint missing: {name}")

    def _link_index(self, name: str) -> int:
        for i in range(p.getNumJoints(self.robot, physicsClientId=self.client)):
            if p.getJointInfo(self.robot, i, physicsClientId=self.client)[12].decode() == name:
                return i
        raise RuntimeError(f"Panda link missing: {name}")

    def _reset_robot(self):
        for j, angle in zip(self.arm, ARM_HOME):
            p.resetJointState(self.robot, j, angle, physicsClientId=self.client)
        for j in self.fingers:
            p.resetJointState(self.robot, j, 0.04, physicsClientId=self.client)
        p.performCollisionDetection(physicsClientId=self.client)

    def _set_arm(self, angles):
        for j, angle in zip(self.arm, angles):
            p.resetJointState(self.robot, j, angle, physicsClientId=self.client)
        p.performCollisionDetection(physicsClientId=self.client)

    def _angles(self):
        return tuple(p.getJointState(self.robot, j, physicsClientId=self.client)[0] for j in self.arm)

    def _tip_pose(self) -> Pose:
        state = p.getLinkState(self.robot, self.tip, computeForwardKinematics=True,
                               physicsClientId=self.client)
        return Pose(tuple(state[4]), tuple(state[5]))

    def _object_pose(self) -> Pose:
        pos, quat = p.getBasePositionAndOrientation(self.mug, physicsClientId=self.client)
        return Pose(tuple(pos), tuple(quat))

    def world_pose(self, local: Pose) -> Pose:
        pos, quat = p.multiplyTransforms(self.observed_mug_pose.position,
                                          self.observed_mug_pose.quaternion,
                                          local.position, local.quaternion)
        return Pose(tuple(pos), tuple(quat))

    def _ik_residual(self, target: Pose, q):
        """Weighted pose residual for bounded refinement; never steps physics."""
        for joint, value in zip(self.arm, q):
            p.resetJointState(self.robot, joint, float(value),
                              physicsClientId=self.client)
        got = self._tip_pose()
        inverse = p.invertTransform((0, 0, 0), got.quaternion)[1]
        delta = p.multiplyTransforms((0, 0, 0), target.quaternion,
                                     (0, 0, 0), inverse)[1]
        if delta[3] < 0:
            delta = tuple(-x for x in delta)
        length = math.sqrt(sum(x*x for x in delta[:3]))
        factor = 2 * math.atan2(length, delta[3]) / length if length > 1e-10 else 2
        # Scale radians to a comparable length for optimization only. Acceptance
        # still uses the caller's unchanged metre/degree tolerances.
        residual = np.concatenate((
            np.asarray(target.position) - got.position,
            0.2 * factor * np.asarray(delta[:3])))
        return residual, position_error(got.position, target.position), \
            orientation_error_deg(got.quaternion, target.quaternion)

    def _refine_ik(self, target: Pose, initial, lower, upper):
        """Projected damped least squares with a finite-difference FK Jacobian."""
        q = np.clip(np.asarray(initial, dtype=float), lower, upper)
        for _ in range(60):
            error, pe, re = self._ik_residual(target, q)
            if pe <= 0.0002 and re <= 0.1:
                break
            jacobian = np.zeros((6, len(q)))
            for column in range(len(q)):
                left, right = q.copy(), q.copy()
                left[column] = max(lower[column], q[column] - 0.0001)
                right[column] = min(upper[column], q[column] + 0.0001)
                distance = right[column] - left[column]
                if distance > 0:
                    a = self._ik_residual(target, left)[0]
                    b = self._ik_residual(target, right)[0]
                    jacobian[:, column] = (b-a) / distance
            free = np.ones(len(q), dtype=bool)
            step = np.zeros(len(q))
            for _ in range(len(q)):
                matrix = jacobian[:, free]
                step[:] = 0
                step[free] = -matrix.T @ np.linalg.solve(
                    matrix @ matrix.T + np.eye(6)*1e-6, error)
                blocked = ((q <= lower+1e-7) & (step < 0)) | \
                          ((q >= upper-1e-7) & (step > 0))
                if not np.any(blocked & free):
                    break
                free[blocked] = False
            step /= max(1.0, float(np.max(np.abs(step))) / 0.15)
            for scale in (1.0, 0.5, 0.25, 0.125, 0.0625):
                trial = np.clip(q + scale*step, lower, upper)
                trial_error = self._ik_residual(target, trial)[0]
                if float(trial_error @ trial_error) < float(error @ error):
                    q = trial
                    break
            else:
                break
        return tuple(float(v) for v in q)

    def ik(self, target: Pose, *, pos_tol=0.025, rot_tol_deg=15.0, stage="unspecified",
           restarts=False):
        """Validate local IK, then retry with all DOF limits and bounded FK refinement.

        A returned solution always satisfies arm limits and FK tolerances.
        All positions, velocities and contact state are restored after probing.
        With ``restarts`` a failed or loose (>2 mm / >1 deg) single-start result
        is retried from fixed canonical seeds and the most accurate accepted
        solution is kept. Deterministic: seeds do not depend on the live state.
        """
        info = [p.getJointInfo(self.robot, j, physicsClientId=self.client)
                for j in self.movable]
        lower = [item[8] for item in info]
        upper = [item[9] for item in info]
        current = [p.getJointState(self.robot, j, physicsClientId=self.client)[0]
                   for j in self.movable]
        arm_lower = np.asarray([lower[d] for d in self.arm_dofs])
        arm_upper = np.asarray([upper[d] for d in self.arm_dofs])
        saved = p.saveState(physicsClientId=self.client)
        record = {"stage": stage, "target_pose": asdict(target),
                  "movable_dofs": len(self.movable), "success": False, "attempts": []}
        result = (None, None, None)
        try:
            for method in ("local", "bounded"):
                p.restoreState(stateId=saved, physicsClientId=self.client)
                options = {}
                if method == "bounded":
                    options = dict(
                        lowerLimits=lower, upperLimits=upper,
                        jointRanges=[hi-lo for lo, hi in zip(lower, upper)],
                        restPoses=current)
                solution = p.calculateInverseKinematics(
                    self.robot, self.tip, target.position, target.quaternion,
                    maxNumIterations=1000 if method == "bounded" else 200,
                    residualThreshold=1e-5, physicsClientId=self.client, **options)
                attempt = {"method": method}
                record["attempts"].append(attempt)
                if len(solution) != len(self.movable) or not all(
                        math.isfinite(v) for v in solution):
                    attempt["reason"] = "invalid_solver_output"
                    continue
                raw = tuple(solution[d] for d in self.arm_dofs)
                violations = [p.getJointInfo(self.robot, joint,
                                             physicsClientId=self.client)[1].decode()
                              for joint, value, lo, hi
                              in zip(self.arm, raw, arm_lower, arm_upper)
                              if value < lo or value > hi]
                attempt["raw_joint_limit_violations"] = violations
                q = raw
                if method == "bounded":
                    q = self._refine_ik(target, raw, arm_lower, arm_upper)
                _, pe, re = self._ik_residual(target, q)
                attempt.update(position_error_m=pe, orientation_error_deg=re)
                result = (None, pe, re)
                if any(v < lo or v > hi for v, lo, hi in zip(q, arm_lower, arm_upper)):
                    attempt["reason"] = "joint_limit"
                elif pe > pos_tol or re > rot_tol_deg:
                    attempt["reason"] = "fk_tolerance"
                else:
                    attempt["reason"] = "accepted"
                    record.update(success=True, solver=method, joint_positions=list(q))
                    result = (q, pe, re)
                    break
            if restarts and (result[0] is None or result[1] > 0.002 or result[2] > 1.0):
                best = result if result[0] is not None else None
                for index, rest in enumerate(IK_RESTART_POSES):
                    p.restoreState(stateId=saved, physicsClientId=self.client)
                    rest = tuple(float(np.clip(v, lo, hi)) for v, lo, hi
                                 in zip(rest, arm_lower, arm_upper))
                    self._set_arm(rest)
                    full_rest = list(current)
                    for d, v in zip(self.arm_dofs, rest):
                        full_rest[d] = v
                    solution = p.calculateInverseKinematics(
                        self.robot, self.tip, target.position, target.quaternion,
                        lowerLimits=lower, upperLimits=upper,
                        jointRanges=[hi-lo for lo, hi in zip(lower, upper)],
                        restPoses=full_rest, maxNumIterations=1000,
                        residualThreshold=1e-5, physicsClientId=self.client)
                    attempt = {"method": f"restart_{index}"}
                    record["attempts"].append(attempt)
                    if len(solution) != len(self.movable) or not all(
                            math.isfinite(v) for v in solution):
                        attempt["reason"] = "invalid_solver_output"
                        continue
                    q = self._refine_ik(target, tuple(solution[d] for d in self.arm_dofs),
                                        arm_lower, arm_upper)
                    _, pe, re = self._ik_residual(target, q)
                    attempt.update(position_error_m=pe, orientation_error_deg=re)
                    if pe > pos_tol or re > rot_tol_deg:
                        attempt["reason"] = "fk_tolerance"
                        continue
                    attempt["reason"] = "accepted"
                    if best is None or pe/0.01+re/5 < best[1]/0.01+best[2]/5:
                        best = (tuple(float(v) for v in q), pe, re)
                        record.update(success=True, solver=attempt["method"],
                                      joint_positions=list(best[0]))
                    if pe <= 0.002 and re <= 1.0:
                        break
                if best is not None:
                    result = best
        finally:
            p.restoreState(stateId=saved, physicsClientId=self.client)
            p.removeState(stateUniqueId=saved, physicsClientId=self.client)
            self.ik_diagnostics.append(record)
        return result

    def _link_name(self, link_index: int) -> str:
        if link_index < 0:
            return "base"
        return p.getJointInfo(self.robot, link_index,
                              physicsClientId=self.client)[12].decode()

    def _unexpected_collision(self, allow_finger_mug: bool, details: list | None = None):
        """Report penetrations deeper than 1 mm; retain link pairs for diagnosis."""
        p.performCollisionDetection(physicsClientId=self.client)
        issues = []
        def note(kind, contact):
            issues.append(kind)
            if details is not None:
                details.append({"type":kind, "robot_link":self._link_name(contact[3]),
                                "other_link":self._link_name(contact[4]) if kind == "robot_self"
                                else str(contact[4]),
                                "penetration_m":round(max(0.0,-contact[8]),6)})

        for other, name in [(self.table, "robot_table"), (self.plane, "robot_floor")]:
            contacts = p.getClosestPoints(self.robot, other, distance=0,
                                          physicsClientId=self.client)
            deep = [x for x in contacts if x[8] < -PENETRATION_TOLERANCE_M]
            if deep:
                note(name, min(deep,key=lambda x:x[8]))
        for cp in p.getClosestPoints(self.robot, self.mug, distance=0,
                                     physicsClientId=self.client):
            if cp[8] >= -PENETRATION_TOLERANCE_M:
                continue
            if not (allow_finger_mug and cp[3] in self.fingers):
                note("robot_mug_nonfinger", cp)
                break
        for cp in p.getContactPoints(self.robot, self.robot, physicsClientId=self.client):
            if cp[8] >= -PENETRATION_TOLERANCE_M:
                continue
            a, b = cp[3], cp[4]
            # Two meshes joined through one or several fixed joints are one
            # rigid assembly. Their overlap is part of the supplied URDF.
            if self.rigid_group[a] == self.rigid_group[b]:
                continue
            parent_a = p.getJointInfo(self.robot, a, physicsClientId=self.client)[16] if a >= 0 else -2
            parent_b = p.getJointInfo(self.robot, b, physicsClientId=self.client)[16] if b >= 0 else -2
            if a == b or a == parent_b or b == parent_a or {a,b} == set(self.fingers):
                continue
            note("robot_self", cp)
            break
        return sorted(set(issues))

    def check_candidate(self, candidate: Candidate) -> Feasibility:
        if (self.scene_config.filter_mode == "execution" and
                type(self).check_candidate is MugScene.check_candidate):
            raise ValueError("filter_mode='execution' is implemented for the sequence track only")
        reasons = []
        if not (0.005 <= candidate.gripper_width <= 0.08):
            return Feasibility(candidate.id, False, ["gripper_width_out_of_range"])
        pre = self.world_pose(candidate.pregrasp_pose)
        grasp = self.world_pose(candidate.grasp_pose)
        saved = p.saveState(physicsClientId=self.client)
        collision_details = []
        try:
            # Planning uses the known mesh AT THE ESTIMATED pose, not the true
            # mug position. This is an idealized collision model, not perception.
            p.resetBasePositionAndOrientation(self.mug, self.observed_mug_pose.position,
                                              self.observed_mug_pose.quaternion,
                                              physicsClientId=self.client)
            restarts = self.scene_config.ik_restarts
            q_pre, pe_pre, _ = self.ik(pre, stage="filter_pregrasp", restarts=restarts)
            if restarts and q_pre is not None:
                # Mirror execution: the grasp move starts from the pregrasp pose.
                self._set_arm(q_pre)
            q_grasp, pe_grasp, _ = self.ik(grasp, stage="filter_grasp", restarts=restarts)
            if q_pre is None: reasons.append("pregrasp_ik_or_fk_error")
            if q_grasp is None: reasons.append("grasp_ik_or_fk_error")
            min_clearance = None
            if q_pre is not None and q_grasp is not None:
                # Check home->pregrasp and pregrasp->grasp, including the last point.
                for start, end, label in [(ARM_HOME, q_pre, "home_to_pregrasp"),
                                          (q_pre, q_grasp, "approach")]:
                    for step in range(21):
                        t = step / 20
                        self._set_arm(tuple((1-t)*a+t*b for a,b in zip(start,end)))
                        at_step = []
                        hits = self._unexpected_collision(
                            allow_finger_mug=(label == "approach" and step == 20),
                            details=at_step)
                        if hits:
                            reasons.append(label + ":" + ",".join(hits))
                            for detail in at_step:
                                detail["path"] = label
                                detail["sample"] = step
                            collision_details.extend(at_step)
                            break
                self._set_arm(q_pre)
                distances = p.getClosestPoints(self.robot, self.table, distance=0.10,
                                               physicsClientId=self.client)
                min_clearance = min((x[8] for x in distances), default=0.10)
            return Feasibility(candidate.id, not reasons, reasons, pe_pre, pe_grasp,
                               min_clearance, collision_details)
        finally:
            p.restoreState(stateId=saved, physicsClientId=self.client)
            p.removeState(stateUniqueId=saved, physicsClientId=self.client)

    def _finger_contacts(self) -> int:
        return sum(1 for cp in p.getContactPoints(self.robot, self.mug,
                                                  physicsClientId=self.client)
                   if cp[3] in self.fingers)

    def _drive_fingers(self, half_width: float):
        for j in self.fingers:
            p.setJointMotorControl2(self.robot, j, p.POSITION_CONTROL, targetPosition=half_width,
                                    force=35, physicsClientId=self.client)

    def observe(self) -> dict:
        """Only proprioception and the frozen pose estimate; no object tracking.

        The estimate is intentionally NOT updated when the mug slips. Width is
        an encoder measurement, not evidence that the intended object is held.
        """
        last = None
        if self.primitive_log:
            entry = self.primitive_log[-1]
            last = {"kind": entry["command"]["kind"],
                    **{k: v for k, v in entry["feedback"].items() if k != "observation"}}
        return {"tool_pose": asdict(self._tip_pose()),
                "joint_positions": list(self._angles()),
                "gripper_width": sum(p.getJointState(self.robot, j,
                                      physicsClientId=self.client)[0] for j in self.fingers),
                "initial_object_pose_estimate": asdict(self.observed_mug_pose),
                "observation_source": self.observation_config.source,
                "elapsed_s": self.physics_steps * STEP,
                "actions_executed": len(self.primitive_log), "last_action": last}

    def _after_step(self, stage, step, last):
        self.physics_steps += 1
        if self.recorder is not None:
            self.recorder.on_step(stage)
        if self.evaluator is not None:
            # Scoring is a write-only side channel from the controller's view.
            # It must never influence an action or be sent to a policy callback.
            self.evaluator.sample(stage, step, last)

    def _step(self, n: int, stage: str):
        for i in range(n):
            p.stepSimulation(physicsClientId=self.client)
            self._after_step(stage, i, i == n-1)

    def _move(self, q_target, steps: int, stage: str, offset=0, total_steps=None):
        q_start = self._angles()
        for i in range(steps):
            fraction = (i+1)/steps
            q_cmd = [(1-fraction)*a+fraction*b for a,b in zip(q_start,q_target)]
            p.setJointMotorControlArray(self.robot, self.arm, p.POSITION_CONTROL,
                                        targetPositions=q_cmd, forces=[87]*7,
                                        physicsClientId=self.client)
            p.stepSimulation(physicsClientId=self.client)
            self._after_step(stage, i + offset,
                             i + offset == (total_steps or steps) - 1)

    def execute_primitive(self, command: Primitive, *, _stage="policy") -> dict:
        """Execute a bounded physical action; feedback contains no scoring truth.

        Workspace/duration bounds and IK are not a collision-free guarantee.
        A primitive moves at most 25 cm; policies must segment longer motions.
        """
        if not isinstance(command, Primitive):
            raise ValueError("action must be a Primitive")
        steps = max(1, round(command.duration_s / STEP))
        status = "executed"
        if command.kind == "move_tcp":
            target = Pose.from_dict(asdict(command.target))
            if position_error(self._tip_pose().position, target.position) > 0.25:
                raise ValueError("move_tcp exceeds the 0.25 m per-action translation limit")
            if command.path == "cartesian":
                waypoints = self._plan_cartesian(target, _stage)
            else:
                q, _, _ = self.ik(target, pos_tol=0.025, stage=_stage,
                                  restarts=self.scene_config.ik_restarts)
                waypoints = [q] if q is not None else None
            if waypoints is None:
                status = "ik_failed"
            else:
                # Preserve requested total duration; at least one step per
                # waypoint. Current waypoint density is below 240 per second.
                if len(waypoints) > steps:
                    raise ValueError("duration too short for Cartesian waypoint count")
                for index, q in enumerate(waypoints):
                    n = (index + 1) * steps // len(waypoints) - index * steps // len(waypoints)
                    self._move(q, n, _stage, index * steps // len(waypoints), steps)
        else:
            if command.kind == "gripper":
                self._drive_fingers(command.width / 2)
            self._step(steps, _stage)
        feedback = {"status": status, "observation": self.observe()}
        if command.kind == "move_tcp":
            tip = self._tip_pose()
            feedback["tool_position_error_m"] = position_error(tip.position, target.position)
            feedback["tool_orientation_error_deg"] = orientation_error_deg(tip.quaternion,
                                                                           target.quaternion)
        self.primitive_log.append({"stage": _stage, "command": asdict(command),
                                   "feedback": feedback})
        # Return the post-action count, consistently with a subsequent observe.
        feedback["observation"]["actions_executed"] = len(self.primitive_log)
        feedback["observation"]["last_action"] = self.observe()["last_action"]
        from copy import deepcopy
        return deepcopy(feedback)

    def _plan_cartesian(self, target: Pose, stage: str):
        """Small TCP segments keep IK on a nearby branch; no physics is stepped."""
        start = self._tip_pose()
        count = max(1, math.ceil(position_error(start.position, target.position) / 0.015),
                    math.ceil(orientation_error_deg(start.quaternion, target.quaternion) / 5))
        saved = p.saveState(physicsClientId=self.client)
        waypoints = []
        try:
            for index in range(1, count + 1):
                fraction = index / count
                pos = tuple((1-fraction)*a+fraction*b
                            for a, b in zip(start.position, target.position))
                quat = p.getQuaternionSlerp(start.quaternion, target.quaternion, fraction)
                q, _, _ = self.ik(Pose(pos, tuple(quat)), pos_tol=0.025,
                                  rot_tol_deg=15, stage=stage)
                if q is None:
                    return None
                waypoints.append(q)
                self._set_arm(q)
            return waypoints
        finally:
            p.restoreState(stateId=saved, physicsClientId=self.client)
            p.removeState(stateUniqueId=saved, physicsClientId=self.client)

    def task_target(self, task: str) -> Pose:
        """Calibrated WORLD goal, independent of seed, estimate, grasp and outcome."""
        validate_task_id(task)
        angle = math.radians(35) if task == "pour_prep" else -math.pi/2
        axis = [0, 1, 0] if task == "pour_prep" else [0, 0, 1]
        rotation = p.getQuaternionFromAxisAngle(axis, angle)
        initial = ObservationConfig().object_pose
        quat = p.multiplyTransforms((0, 0, 0), rotation, (0, 0, 0), initial.quaternion)[1]
        return Pose((initial.position[0], initial.position[1], initial.position[2] + 0.14),
                    tuple(quat))

    def _episode_result(self, candidate, task, checks, failure=None, policy_mode="fixed"):
        # Only terminal reporting reads privileged evaluation data.
        result = self.evaluator.finish()
        if failure is not None:
            result.update(success=False, failure_reason=failure)
        collision_types = set(result["collision_types"]) | {
            detail["type"] for detail in checks.collision_details if "type" in detail}
        result.update(controller_version=CONTROLLER_VERSION, seed=self.seed,
                      task_id=task, selected_id=candidate.id, feasibility=asdict(checks),
                      collision=bool(collision_types), collision_types=sorted(collision_types),
                      ik_diagnostics=self.ik_diagnostics, primitive_log=self.primitive_log,
                      observation_config=asdict(self.observation_config),
                      initial_object_pose_estimate=asdict(self.observed_mug_pose),
                      scene_randomization=asdict(self.scene_config),
                      policy_mode=policy_mode, schema_version="episode_v0.3")
        return result

    def run_episode(self, task: str, candidate: Candidate) -> dict:
        validate_task_id(task)
        self.evaluator = EpisodeEvaluator(self, self.task_target(task))
        checks = self.check_candidate(candidate)
        if not checks.feasible:
            return self._episode_result(candidate, task, checks, "infeasible")

        pre = self.world_pose(candidate.pregrasp_pose)
        grasp = self.world_pose(candidate.grasp_pose)
        # Opening before pregrasp does not step physics; the first primitive
        # physically opens while moving. Subsequent phases all use the same API
        # that is available to an incremental policy.
        self._drive_fingers(0.04)
        for stage, target, steps in (("pregrasp", pre, 180), ("grasp", grasp, 120)):
            feedback = self.execute_primitive(Primitive("move_tcp", target, duration_s=steps*STEP,
                                                        path="joint"),
                                              _stage=stage)
            if feedback["status"] != "executed":
                return self._episode_result(candidate, task, checks, stage + "_ik_failed")
        self.execute_primitive(Primitive("gripper", width=0, duration_s=180*STEP), _stage="close")

        # No exact contact-count or true object-height gate. The default policy
        # continues open-loop; a custom policy may use encoder/tool feedback.
        tool_at_grasp = self._tip_pose()
        lifted_tip = Pose((tool_at_grasp.position[0],tool_at_grasp.position[1],
                           tool_at_grasp.position[2]+0.14),tool_at_grasp.quaternion)
        # Nominal T_tool_mug comes from the candidate, not achieved true pose.
        target_obj = self.task_target(task)
        tpos, tquat = p.multiplyTransforms(target_obj.position, target_obj.quaternion,
                                          candidate.grasp_pose.position, candidate.grasp_pose.quaternion)
        # T_world_tool = T_world_mug * T_mug_tool (equivalently inverse of
        # nominal T_tool_mug). Keep no achieved-object transform in control.
        task_tip = Pose(tuple(tpos), tuple(tquat))
        # Move toward the robot before pouring, then rotate and return. This
        # fixed, observation-based waypoint avoids the near-table wrist branch
        # switch of a direct rotation at the original x. It is not GT-adaptive.
        clearance_tip = Pose((grasp.position[0] - 0.10, grasp.position[1],
                              grasp.position[2] + 0.18), grasp.quaternion)
        rotated_tip = Pose((task_tip.position[0] - 0.10, task_tip.position[1],
                            task_tip.position[2] + 0.04), task_tip.quaternion)
        motion = [("lift", lifted_tip)]
        if task == "pour_prep":
            motion += [("task_clearance", clearance_tip), ("task_rotate", rotated_tip)]
        motion += [("task_motion", task_tip)]
        for stage, target in motion:
            feedback = self.execute_primitive(Primitive("move_tcp", target, duration_s=200*STEP),
                                              _stage=stage)
            if feedback["status"] != "executed":
                reason = "lift_ik_failed" if stage == "lift" else "task_ik_failed"
                return self._episode_result(candidate, task, checks, reason)
        self.execute_primitive(Primitive("wait", duration_s=2), _stage="hold")
        return self._episode_result(candidate, task, checks)


def get_feasible_candidates(seed: int, candidates: list[Candidate], *,
                            observation_config: ObservationConfig | None = None,
                            scene_config=None):
    """Return all checks and public records usable by B/C's selectors."""
    with MugScene(seed, observation_config=observation_config,
                  scene_config=scene_config) as scene:
        checks = [scene.check_candidate(c) for c in candidates]
        estimate = asdict(scene.observed_mug_pose)
        config = asdict(scene.observation_config)
        scene_record = asdict(scene.scene_config)
    allowed = {c.candidate_id for c in checks if c.feasible}
    feasible_candidates = [c.public_metadata() for c in candidates if c.id in allowed]
    feasible_full = [c for c in candidates if c.id in allowed]
    warnings = ["task_diversity_not_validated"]
    if len(feasible_full) < 2:
        warnings.append("fewer_than_two_feasible_candidates")
    if len({c.region for c in feasible_full}) <= 1:
        warnings.append("single_or_no_semantic_region")
    return {
        "controller_version": CONTROLLER_VERSION,
        "seed": seed,
        "candidate_sha256": candidate_sha256(candidates),
        "observation_config": config,
        "initial_object_pose_estimate": estimate,
        "scene_randomization": scene_record,
        "status": "ok" if feasible_candidates else "no_feasible_candidate",
        "feasible_candidates": feasible_candidates,
        "checks": [asdict(x) for x in checks],
        "selection_space": {"feasible_count": len(feasible_full),
                            "regions": sorted({c.region for c in feasible_full}),
                            "approaches": sorted({c.approach for c in feasible_full}),
                            "warnings": warnings},
    }


def run_episode(task_id: str, seed: int, selected_id: str,
                candidates: list[Candidate], gui: bool = False,
                media_dir: str | Path | None = None, *,
                observation_config: ObservationConfig | None = None,
                scene_config=None):
    validate_task_id(task_id)
    matches = [c for c in candidates if c.id == selected_id]
    if len(matches) != 1:
        raise ValueError("selected_id must match exactly one supplied candidate")
    with MugScene(seed,gui=gui,observation_config=observation_config,
                  scene_config=scene_config) as scene:
        if media_dir is not None:
            from .visualization import EpisodeRecorder
            stem = f"{task_id}_seed{seed}_{selected_id}"
            scene.recorder = EpisodeRecorder(scene.client, media_dir, stem)
        result = scene.run_episode(task_id,matches[0])
        if scene.recorder is not None:
            result["media"] = scene.recorder.finish(result)
            scene.recorder = None
        return result


def run_primitive_episode(task_id: str, seed: int, selected_id: str,
                          candidates: list[Candidate], policy, *,
                          observation_config: ObservationConfig | None = None,
                          max_actions: int = 32, scene_config=None):
    """Separate incremental-control track; do NOT mix with ID-only comparisons.

    policy(context, observation) returns Primitive or None (stop). Context
    contains estimated poses only. None triggers a standard 2-second hold
    before privileged terminal scoring. No automatic retry or task execution.
    """
    from copy import deepcopy
    validate_task_id(task_id)
    if type(max_actions) is not int or not 1 <= max_actions <= 64:
        raise ValueError("max_actions must be an integer in [1, 64]")
    matches = [c for c in candidates if c.id == selected_id]
    if len(matches) != 1:
        raise ValueError("selected_id must match exactly one supplied candidate")
    candidate = matches[0]
    with MugScene(seed, observation_config=observation_config,
                  scene_config=scene_config) as scene:
        scene.evaluator = EpisodeEvaluator(scene, scene.task_target(task_id))
        checks = scene.check_candidate(candidate)
        if not checks.feasible:
            return scene._episode_result(candidate, task_id, checks, "infeasible", "incremental")
        session = ControlSession(scene)
        context = {"task_id": task_id, "candidate": asdict(candidate),
                   "pregrasp_tool_pose": asdict(scene.world_pose(candidate.pregrasp_pose)),
                   "grasp_tool_pose": asdict(scene.world_pose(candidate.grasp_pose)),
                   "target_object_pose": asdict(scene.task_target(task_id))}
        # Last callback can stop after exactly max_actions; another action is
        # rejected without being executed. Bound total simulated time as well.
        for index in range(max_actions + 1):
            command = policy(deepcopy(context), session.observe())
            if command is None:
                scene.execute_primitive(Primitive("wait", duration_s=2), _stage="hold")
                return scene._episode_result(candidate, task_id, checks, policy_mode="incremental")
            if index == max_actions:
                return scene._episode_result(candidate, task_id, checks,
                                              "action_budget_exceeded", "incremental")
            session.act(command)
