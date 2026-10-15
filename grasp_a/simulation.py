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

from .contracts import Candidate, Pose, TASKS, validate_task_id
from .collision_rules import fixed_link_groups

try:
    import pybullet as p
    import pybullet_data
except ImportError as exc:
    raise ImportError("PyBullet missing. On Windows use: conda env create -f environment.yml") from exc

STEP = 1 / 240
CONTROLLER_VERSION = "bounded_ik_v1"
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
    def __init__(self, seed: int, gui: bool = False):
        self.seed = seed
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
            start = [0.55+rng.uniform(-0.008, 0.008), rng.uniform(-0.008, 0.008), MUG_CENTER_Z]
            start_q = p.getQuaternionFromEuler([0, 0, rng.uniform(-0.08, 0.08)])
            self.mug = p.loadURDF(str(ASSETS / "mug.urdf"), start, start_q,
                                  physicsClientId=self.client)
            for f in self.fingers:
                p.changeDynamics(self.robot, f, lateralFriction=2.0, spinningFriction=0.05,
                                 physicsClientId=self.client)
            p.changeDynamics(self.mug, -1, lateralFriction=1.2, physicsClientId=self.client)
            self.initial_mug_pose = Pose(tuple(start), tuple(start_q))
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
        pos, quat = p.multiplyTransforms(self.initial_mug_pose.position,
                                          self.initial_mug_pose.quaternion,
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

    def ik(self, target: Pose, *, pos_tol=0.025, rot_tol_deg=15.0, stage="unspecified"):
        """Validate local IK, then retry with all DOF limits and bounded FK refinement.

        A returned solution always satisfies arm limits and FK tolerances.
        All positions, velocities and contact state are restored after probing.
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
        reasons = []
        if not (0.005 <= candidate.gripper_width <= 0.08):
            return Feasibility(candidate.id, False, ["gripper_width_out_of_range"])
        pre = self.world_pose(candidate.pregrasp_pose)
        grasp = self.world_pose(candidate.grasp_pose)
        saved = self._angles()
        collision_details = []
        try:
            q_pre, pe_pre, _ = self.ik(pre, stage="filter_pregrasp")
            q_grasp, pe_grasp, _ = self.ik(grasp, stage="filter_grasp")
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
            self._set_arm(saved)

    def _finger_contacts(self) -> int:
        return sum(1 for cp in p.getContactPoints(self.robot, self.mug,
                                                  physicsClientId=self.client)
                   if cp[3] in self.fingers)

    def _drive_fingers(self, half_width: float):
        for j in self.fingers:
            p.setJointMotorControl2(self.robot, j, p.POSITION_CONTROL, targetPosition=half_width,
                                    force=35, physicsClientId=self.client)

    def _step(self, n: int, stage: str, trajectory: list, problems: set):
        for i in range(n):
            p.stepSimulation(physicsClientId=self.client)
            if self.recorder is not None:
                self.recorder.on_step(stage)
            if i % 24 == 0 or i == n-1:
                obj = self._object_pose()
                tip = self._tip_pose()
                trajectory.append({"stage": stage, "step": i, "mug_pose": asdict(obj),
                                   "tool_pose": asdict(tip), "finger_contacts": self._finger_contacts()})
                problems.update(self._unexpected_collision(allow_finger_mug=stage != "pregrasp"))

    def _move(self, q_target, steps: int, stage: str, trajectory: list, problems: set):
        q_start = self._angles()
        for i in range(steps):
            fraction = (i+1)/steps
            q_cmd = [(1-fraction)*a+fraction*b for a,b in zip(q_start,q_target)]
            p.setJointMotorControlArray(self.robot, self.arm, p.POSITION_CONTROL,
                                        targetPositions=q_cmd, forces=[87]*7,
                                        physicsClientId=self.client)
            p.stepSimulation(physicsClientId=self.client)
            if self.recorder is not None:
                self.recorder.on_step(stage)
            if i % 24 == 0 or i == steps-1:
                obj = self._object_pose()
                trajectory.append({"stage":stage,"step":i,"mug_pose":asdict(obj),
                                   "tool_pose":asdict(self._tip_pose()),
                                   "finger_contacts":self._finger_contacts()})
                problems.update(self._unexpected_collision(allow_finger_mug=stage != "pregrasp"))

    def _fail(self, candidate: Candidate, task: str, checks: Feasibility,
              reason: str, trajectory: list, problems: set) -> dict:
        obj = self._object_pose()
        # An infeasible candidate may be rejected before execution starts.  Its
        # collision evidence still belongs in the common result contract so
        # evaluators can count failures consistently with executed episodes.
        check_collision_types = {
            detail["type"] for detail in checks.collision_details
            if "type" in detail
        }
        all_collision_types = set(problems) | check_collision_types
        return {"controller_version":CONTROLLER_VERSION,
                "seed":self.seed,"task_id":task,"selected_id":candidate.id,
                "feasibility":asdict(checks),"success":False,"failure_reason":reason,
                "dropped":reason in ("no_grasp", "lost_grasp"),
                "collision":bool(all_collision_types),
                "collision_types":sorted(all_collision_types),
                "grasp_maintained":False,"position_error_m":None,
                "orientation_error_deg":None,"final_mug_pose":asdict(obj),
                "trajectory":trajectory,"ik_diagnostics":self.ik_diagnostics}

    def run_episode(self, task: str, candidate: Candidate) -> dict:
        validate_task_id(task)
        checks = self.check_candidate(candidate)
        trajectory, problems = [], set()
        if not checks.feasible:
            return self._fail(candidate,task,checks,"infeasible",trajectory,problems)

        pre = self.world_pose(candidate.pregrasp_pose)
        grasp = self.world_pose(candidate.grasp_pose)
        q_pre, _, _ = self.ik(pre, stage="pregrasp")
        q_grasp, _, _ = self.ik(grasp, stage="grasp")
        self._drive_fingers(0.04)
        self._move(q_pre,180,"pregrasp",trajectory,problems)
        self._move(q_grasp,120,"grasp",trajectory,problems)
        self._drive_fingers(0.0)
        self._step(180,"close",trajectory,problems)
        if self._finger_contacts() == 0:
            return self._fail(candidate,task,checks,"no_grasp",trajectory,problems)

        tool_at_grasp = self._tip_pose()
        obj_at_grasp = self._object_pose()
        inv_tool = p.invertTransform(tool_at_grasp.position, tool_at_grasp.quaternion)
        relative_object = p.multiplyTransforms(*inv_tool, obj_at_grasp.position,
                                                obj_at_grasp.quaternion)
        lifted_tip = Pose((tool_at_grasp.position[0],tool_at_grasp.position[1],
                           tool_at_grasp.position[2]+0.14),tool_at_grasp.quaternion)
        q_lift, _, _ = self.ik(lifted_tip, pos_tol=0.035, stage="lift")
        if q_lift is None:
            return self._fail(candidate,task,checks,"lift_ik_failed",trajectory,problems)
        self._move(q_lift,200,"lift",trajectory,problems)
        lifted_obj = self._object_pose()
        if lifted_obj.position[2] < TABLE_TOP+0.09 or self._finger_contacts() == 0:
            return self._fail(candidate,task,checks,"lost_grasp",trajectory,problems)

        # A1/M0/M1/M2/M3 all use this exact same task motion after selection.
        angle = math.radians(35) if task == "pour_prep" else -math.pi/2
        axis = [0,1,0] if task == "pour_prep" else [0,0,1]
        rotation = p.getQuaternionFromAxisAngle(axis,angle)
        target_q = p.multiplyTransforms([0,0,0],rotation,[0,0,0],obj_at_grasp.quaternion)[1]
        target_obj = Pose((lifted_obj.position[0],lifted_obj.position[1],
                           obj_at_grasp.position[2]+0.14),tuple(target_q))
        inv_rel = p.invertTransform(*relative_object)
        tpos,tquat = p.multiplyTransforms(target_obj.position,target_obj.quaternion,*inv_rel)
        q_task, _, _ = self.ik(Pose(tuple(tpos),tuple(tquat)),pos_tol=0.035,
                              stage="task_motion")
        if q_task is None:
            return self._fail(candidate,task,checks,"task_ik_failed",trajectory,problems)
        self._move(q_task,200,"task_motion",trajectory,problems)
        self._step(480,"hold",trajectory,problems)
        final = self._object_pose()
        pe = position_error(final.position,target_obj.position)
        re = orientation_error_deg(final.quaternion,target_obj.quaternion)
        held = self._finger_contacts()>0 and final.position[2]>TABLE_TOP+0.09
        success = held and not problems and pe<=0.04 and re<=15.0
        return {"controller_version":CONTROLLER_VERSION,
                "seed":self.seed,"task_id":task,"selected_id":candidate.id,
                "feasibility":asdict(checks),"success":success,
                "failure_reason":None if success else ("lost_grasp" if not held else
                    "unintended_collision" if problems else "target_pose_error"),
                "dropped":not held,"collision":bool(problems),
                "collision_types":sorted(problems),"grasp_maintained":held,
                "position_error_m":pe,"orientation_error_deg":re,
                "target_mug_pose":asdict(target_obj),"final_mug_pose":asdict(final),
                "trajectory":trajectory,"ik_diagnostics":self.ik_diagnostics}


def get_feasible_candidates(seed: int, candidates: list[Candidate]):
    """Return all checks and public records usable by B/C's selectors."""
    with MugScene(seed) as scene:
        checks = [scene.check_candidate(c) for c in candidates]
    allowed = {c.candidate_id for c in checks if c.feasible}
    feasible_candidates = [c.public_metadata() for c in candidates if c.id in allowed]
    return {
        "controller_version": CONTROLLER_VERSION,
        "seed": seed,
        "status": "ok" if feasible_candidates else "no_feasible_candidate",
        "feasible_candidates": feasible_candidates,
        "checks": [asdict(x) for x in checks],
    }


def run_episode(task_id: str, seed: int, selected_id: str,
                candidates: list[Candidate], gui: bool = False,
                media_dir: str | Path | None = None):
    validate_task_id(task_id)
    matches = [c for c in candidates if c.id == selected_id]
    if len(matches) != 1:
        raise ValueError("selected_id must match exactly one supplied candidate")
    with MugScene(seed,gui=gui) as scene:
        if media_dir is not None:
            from .visualization import EpisodeRecorder
            stem = f"{task_id}_seed{seed}_{selected_id}"
            scene.recorder = EpisodeRecorder(scene.client, media_dir, stem)
        result = scene.run_episode(task_id,matches[0])
        if scene.recorder is not None:
            result["media"] = scene.recorder.finish(result)
            scene.recorder = None
        return result
