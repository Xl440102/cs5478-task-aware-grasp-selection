"""Opt-in physical sequence track. Legacy preparation APIs remain unchanged.

The controller uses the initial estimate, FK and encoders only. True vessel
poses enter the fluid/rigid physics and the write-only outcome evaluator.
"""
from dataclasses import asdict
import math
from pathlib import Path
import hashlib
import json

import numpy as np

from .contracts import Pose, candidate_sha256
from .control import ObservationConfig, Primitive
from .fluid import Vessel, PositionBasedFluid, FluidNumericalError, rotation_matrix
from .sequence_contracts import (SequenceConfig, task_spec, SEQUENCE_CONTROLLER,
                                 SEQUENCE_EVALUATOR)
from .simulation import MugScene, ASSETS, STEP, p, position_error, orientation_error_deg


class SequenceFailure(RuntimeError):
    pass


def spec_sha256(spec):
    return hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(",",":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


class SequenceScene(MugScene):
    MUG_ASSET = ASSETS / "mug_hollow.urdf"

    def __init__(self, task_id, seed=0, config=None, gui=False, observation_config=None, *,
                 scene_config=None, liquid="pbf"):
        # liquid="proxy" (pour_water only): no particle fluid. The water mass is a
        # rigid payload and pouring is scored by a geometric spout/occlusion proxy
        # in SequenceEvaluator. Same scene, motion, stages and timeouts.
        if liquid not in ("pbf", "proxy"):
            raise ValueError("liquid must be pbf or proxy")
        self.liquid = liquid
        self.config = config or SequenceConfig()
        self.spec = task_spec(task_id, self.config)
        self.task_id = task_id
        self.fluid = None
        self.receiver = None
        self.target_cup = None
        self.stage_log = []
        self.active_stage = "setup"
        super().__init__(seed, gui, observation_config, scene_config=scene_config)
        bx, by = self.config.target_xy
        if task_id == "pour_water":
            self.target_cup = self._make_target_cup((bx, by, .645))
        elif task_id == "handover_transfer":
            self.receiver = self._make_receiver((bx, by+.13, self.config.carry_height_m))
        # Set motor targets before the first physics step (no passive arm fall).
        self._hold_arm(self._angles())
        self._drive_fingers(.04)

    def _make_target_cup(self, position):
        body = p.loadURDF(str(ASSETS/"receiver_cup.urdf"),position,physicsClientId=self.client)
        p.changeDynamics(body, -1, lateralFriction=.8, physicsClientId=self.client)
        return body

    def _make_receiver(self, position):
        # Y carriage -> Z carriage/palm -> opposing Z fingers. Only motors move
        # these links. No weld, grasp constraint or object pose reset is used.
        def box(half, color):
            return (p.createCollisionShape(p.GEOM_BOX, halfExtents=half, physicsClientId=self.client),
                    p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=color,
                                        physicsClientId=self.client))
        palm = box([.05,.012,.025], [.25,.28,.30,1])
        finger = box([.032,.025,.006], [.8,.35,.15,1])
        body = p.createMultiBody(baseMass=0, baseCollisionShapeIndex=-1, basePosition=position,
            linkMasses=[.1,.3,.05,.05], linkCollisionShapeIndices=[-1,palm[0],finger[0],finger[0]],
            linkVisualShapeIndices=[-1,palm[1],finger[1],finger[1]],
            linkPositions=[[0,0,0],[0,0,0],[0,-.035,.006],[0,-.035,-.006]],
            linkOrientations=[(0,0,0,1)]*4, linkInertialFramePositions=[[0,0,0]]*4,
            linkInertialFrameOrientations=[(0,0,0,1)]*4, linkParentIndices=[0,1,2,2],
            linkJointTypes=[p.JOINT_PRISMATIC]*4,
            linkJointAxis=[[0,1,0],[0,0,1],[0,0,1],[0,0,-1]], physicsClientId=self.client)
        for joint, value in enumerate((.12,0,.04,.04)):
            p.resetJointState(body, joint, value, physicsClientId=self.client)
        for joint in (2,3):
            p.changeDynamics(body, joint, lateralFriction=2, spinningFriction=.05,
                             physicsClientId=self.client)
        # Mechanically synchronize the receiver's opposing jaws, as on a
        # parallel gripper. This gear joins TWO GRIPPER LINKS, never the cup.
        gear = p.createConstraint(body,2,body,3,p.JOINT_GEAR,(0,0,1),
                                  (0,0,0),(0,0,0),physicsClientId=self.client)
        p.changeConstraint(gear,gearRatio=-1,maxForce=100,erp=.2,physicsClientId=self.client)
        self._receiver_motor(body, 0, .12)
        self._receiver_motor(body, 1, 0)
        self._receiver_motor(body, 2, .04, 15)
        self._receiver_motor(body, 3, .04, 15)
        return body

    def _receiver_motor(self, body, joint, position, force=80):
        p.setJointMotorControl2(body, joint, p.POSITION_CONTROL, targetPosition=position,
                    force=force, maxVelocity=.10, physicsClientId=self.client)

    def vessels(self):
        """Engine/scorer boundary. Never called by motion target generation."""
        source = Vessel(*p.getBasePositionAndOrientation(self.mug, physicsClientId=self.client),
                        body_id=self.mug)
        target = None
        if self.target_cup is not None:
            target = Vessel(*p.getBasePositionAndOrientation(self.target_cup, physicsClientId=self.client),
                            inner_radius=.055, outer_radius=.058, body_id=self.target_cup)
        return source, target

    def initialize_liquid(self):
        source, _ = self.vessels()
        self.fluid = PositionBasedFluid(source, self.config.fluid_particles,
                                       self.config.water_mass_kg, self.config.fluid_iterations)
        self._step(120, "liquid_settle")
        source, target = self.vessels()
        self.fluid_initial_check = self.fluid.mass_accounting(source, target)
        if self.fluid_initial_check["spill_ratio"] > .01 or self.fluid.last_compression > .1:
            raise FluidNumericalError("initial liquid retention/density check failed")
        self.physics_steps = 0  # explicit pre-episode 0.5 s initialization, not scored

    def _hold_arm(self, angles):
        p.setJointMotorControlArray(self.robot, self.arm, p.POSITION_CONTROL,
                    targetPositions=angles, forces=[87]*7, physicsClientId=self.client)

    def _tick(self, stage):
        if stage != "liquid_settle" and self.physics_steps*STEP >= self.config.episode_timeout_s:
            raise SequenceFailure("episode_timeout")
        if self.fluid is not None:
            source, target = self.vessels()
            vessels = [source] + ([target] if target is not None else [])
            net = {}
            for _ in range(self.config.fluid_substeps):
                self.fluid.step(STEP/self.config.fluid_substeps, vessels)
                for body, contributions in self.fluid.boundary_impulses.items():
                    center = np.asarray(source.position if body == self.mug else target.position)
                    force, torque = net.setdefault(body, [np.zeros(3),np.zeros(3)])
                    for points, impulses in contributions:
                        force += impulses.sum(axis=0)/STEP
                        torque += np.cross(points-center, impulses).sum(axis=0)/STEP
            for body, (force, torque) in net.items():
                center = source.position if body == self.mug else target.position
                p.applyExternalForce(body, -1, force.tolist(), center, p.WORLD_FRAME,
                                     physicsClientId=self.client)
                p.applyExternalTorque(body, -1, torque.tolist(), p.WORLD_FRAME,
                                      physicsClientId=self.client)
        p.stepSimulation(physicsClientId=self.client)
        self._after_step(stage, self.physics_steps, False)

    def _step(self, n, stage):
        for _ in range(n):
            self._tick(stage)

    def _move(self, q_target, steps, stage, offset=0, total_steps=None):
        start = self._angles()
        for i in range(steps):
            f = (i+1)/steps
            self._hold_arm([(1-f)*a+f*b for a,b in zip(start,q_target)])
            self._tick(stage)

    def _plan_cartesian(self, target, stage):
        start = self._tip_pose()
        n = max(1, math.ceil(position_error(start.position,target.position)/.015),
                math.ceil(orientation_error_deg(start.quaternion,target.quaternion)/5))
        saved = p.saveState(physicsClientId=self.client)
        path = []
        try:
            for i in range(1,n+1):
                f = i/n
                pose = Pose(tuple((1-f)*a+f*b for a,b in zip(start.position,target.position)),
                            tuple(p.getQuaternionSlerp(start.quaternion,target.quaternion,f)))
                q,_,_ = self.ik(pose, pos_tol=.008, rot_tol_deg=3, stage=stage)
                if q is None:
                    return None
                self._set_arm(q)
                path.append(q)
            return path
        finally:
            p.restoreState(stateId=saved,physicsClientId=self.client)
            p.removeState(saved,physicsClientId=self.client)

    def _arrive(self, target, stage, limit):
        consecutive = 0
        for _ in range(max(0,round(limit/STEP))):
            self._step(1,stage)
            tip = self._tip_pose()  # FK/proprioception, never object truth
            pe = position_error(tip.position,target.position)
            re = orientation_error_deg(tip.quaternion,target.quaternion)
            consecutive = consecutive+1 if (pe <= self.config.arrival_position_m and
                              re <= self.config.arrival_angle_deg) else 0
            if consecutive*STEP >= self.config.arrival_continuous_s:
                return dict(position_error_m=pe, angle_error_deg=re,
                            continuous_s=consecutive*STEP)
        raise SequenceFailure(stage+"_arrival_timeout")

    def move_stage(self, target, stage, path="cartesian", duration=1.0):
        # Segment long translations to preserve the primitive's 25 cm bound.
        start = self._tip_pose()
        n = max(1,math.ceil(position_error(start.position,target.position)/.20))
        for i in range(1,n+1):
            f = i/n
            segment = Pose(tuple((1-f)*a+f*b for a,b in zip(start.position,target.position)),
                           tuple(p.getQuaternionSlerp(start.quaternion,target.quaternion,f)))
            feedback = self.execute_primitive(Primitive("move_tcp",segment,
                                duration_s=duration/n,path=path),_stage=stage)
            if feedback["status"] != "executed":
                raise SequenceFailure(stage+"_ik_failed")
        # Joint interpolation uses legacy IK acceptance; refine endpoint if the
        # FK target is not yet tight enough, without resetting the live robot.
        q,_,_ = self.ik(target,pos_tol=.008,rot_tol_deg=3,stage=stage+"_endpoint")
        if q is None:
            raise SequenceFailure(stage+"_endpoint_ik_failed")
        self._hold_arm(q)
        budget = min(self.config.arrival_timeout_s,
                     self.stage_deadline-self.physics_steps*STEP)
        return self._arrive(target,stage,budget)

    def run_sequence(self, candidate):
        from .sequence_evaluation import SequenceEvaluator
        checks = self.check_candidate(candidate)
        failure = None
        status = "completed"
        if checks.feasible:
            try:
                if self.task_id == "pour_water" and self.liquid == "pbf":
                    self.initialize_liquid()
                elif self.task_id == "pour_water":
                    mass = p.getDynamicsInfo(self.mug,-1,physicsClientId=self.client)[0]
                    p.changeDynamics(self.mug,-1,mass=mass+self.config.water_mass_kg,
                                     physicsClientId=self.client)
            except FluidNumericalError as exc:
                failure, status = str(exc), "simulation_error"
        else:
            failure, status = "infeasible", "rejected"
        self.evaluator = SequenceEvaluator(self)
        c = self.config
        bx, by = c.target_xy
        upright = (0,0,0,1)
        carry = Pose((bx,by+(math.copysign(.045,c.pour_angle_deg) if self.task_id == "pour_water" else 0),c.carry_height_m),upright)
        def tool(obj):
            pos,quat = p.multiplyTransforms(obj.position,obj.quaternion,
                      candidate.grasp_pose.position,candidate.grasp_pose.quaternion)
            return Pose(tuple(pos),tuple(quat))
        initial_grasp = self.world_pose(candidate.grasp_pose)
        lift = Pose((initial_grasp.position[0],initial_grasp.position[1],
                     initial_grasp.position[2]+.14),initial_grasp.quaternion)
        # Withdrawal depends on the specified approach, not achieved mug pose.
        axis = rotation_matrix(initial_grasp.quaternion)[:,2]
        # Nominal bottom is commanded 3 mm into the support plane, under the
        # existing force-limited arm motors. Compliance/contact stops the cup;
        # the live source pose is not read to choose this lowering distance.
        release_pose = tool(Pose((bx,by,.642),upright)) if self.task_id == "transport_place" else tool(carry)
        retreat = Pose(tuple(np.asarray(release_pose.position)-.10*axis+np.array((0,0,.16))),release_pose.quaternion)
        actions = {
            "pregrasp": lambda: self.move_stage(self.world_pose(candidate.pregrasp_pose),"pregrasp","joint"),
            "approach": lambda: self.move_stage(initial_grasp,"approach","cartesian",.7),
            "close": lambda: self.execute_primitive(Primitive("gripper",width=0,duration_s=.75),_stage="close"),
            "lift": lambda: self.move_stage(lift,"lift",duration=1.0),
            "transport": lambda: self.move_stage(tool(carry),"transport",duration=1.3),
            "lower": lambda: self.move_stage(release_pose,"lower",duration=1.3),
            "release": lambda: self.execute_primitive(Primitive("gripper",width=.08,duration_s=.5),_stage="release"),
            "donor_release": lambda: self.execute_primitive(Primitive("gripper",width=.08,duration_s=.5),_stage="donor_release"),
            "retreat": lambda: self.move_stage(retreat,"retreat",duration=1),
            "receiver_approach": lambda: self.receiver_rail(0,0,"receiver_approach"),
            "receiver_close": self.receiver_close,
            "receiver_lift": lambda: self.receiver_rail(1,c.receiver_lift_m,"receiver_lift"),
            "tilt": lambda: self.move_stage(tool(Pose(carry.position,
                          tuple(p.getQuaternionFromEuler((math.radians(c.pour_angle_deg),0,0))))),"tilt",duration=2),
            "pour_dwell": lambda: self._step(round(c.pour_dwell_s/STEP),"pour_dwell"),
            "return_upright": lambda: self.move_stage(tool(carry),"return_upright",duration=2),
            "terminal": lambda: self._step(round(c.terminal_window_s/STEP),"terminal"),
            "settle": lambda: self._step(round(c.settle_s/STEP),"settle"),
        }
        for spec in self.spec["stages"]:
            stage = spec["id"]
            row = dict(id=stage,control_status="not_executed",start_s=None,end_s=None,feedback=None)
            self.stage_log.append(row)
            if failure is not None:
                continue
            self.active_stage = stage
            row["start_s"] = self.physics_steps*STEP
            self.stage_deadline = row["start_s"]+spec["timeout_s"]
            try:
                row["feedback"] = actions[stage]()
                row["control_status"] = "completed"
                if self.physics_steps*STEP > self.stage_deadline+STEP/2:
                    raise SequenceFailure(stage+"_stage_timeout")
            except FluidNumericalError as exc:
                failure, status = str(exc), "simulation_error"
                row["control_status"] = "simulation_error"
            except SequenceFailure as exc:
                failure, status = str(exc), "execution_failed"
                row["control_status"] = "failed"
            row["end_s"] = self.physics_steps*STEP
        result = self.evaluator.finish()
        if failure:
            result.update(success=False,failure_reason=failure)
        if not result["success_checks"]["fluid_density_valid"] and status == "completed":
            status = "simulation_invalid"
        result.update(schema_version="sequence_episode_v1",status=status,
            scene_version="hollow_cup_sequences_v1",task_spec_sha256=spec_sha256(self.spec),
            controller_version=SEQUENCE_CONTROLLER,evaluation_version=SEQUENCE_EVALUATOR,
            task_id=self.task_id,seed=self.seed,selected_id=candidate.id,
            task_spec=self.spec,stages=self.stage_log,feasibility=asdict(checks),
            elapsed_s=self.physics_steps*STEP,primitive_log=self.primitive_log,
            observation_config=asdict(self.observation_config),
            initial_object_pose_estimate=asdict(self.observed_mug_pose),
            scene_randomization=asdict(self.scene_config),
            liquid_mode=self.liquid if self.task_id == "pour_water" else None,
            liquid_model=self.fluid.metadata() if self.fluid else None,
            liquid_initial_check=getattr(self,"fluid_initial_check",None),
            ik_diagnostics=self.ik_diagnostics)
        return result

    def receiver_rail(self, joint, target, stage):
        self._receiver_motor(self.receiver,joint,target)
        consecutive = 0
        while self.physics_steps*STEP < self.stage_deadline-STEP/2:
            self._step(1,stage)
            error = abs(p.getJointState(self.receiver,joint,physicsClientId=self.client)[0]-target)
            consecutive = consecutive+1 if error < .002 else 0
            if consecutive*STEP >= .10:
                return dict(encoder_error_m=error,continuous_s=consecutive*STEP)
        raise SequenceFailure(stage+f"_arrival_timeout_encoder_{error:.4f}m")

    def receiver_close(self):
        for joint in (2,3):
            self._receiver_motor(self.receiver,joint,0,15)
        self._step(180,"receiver_close")
        return dict(jaw_positions=[p.getJointState(self.receiver,j,physicsClientId=self.client)[0]
                                   for j in (2,3)])


def get_sequence_candidates(task_id,seed,candidates,*,config=None,observation_config=None,
                            scene_config=None):
    with SequenceScene(task_id,seed,config,observation_config=observation_config,
                       scene_config=scene_config) as scene:
        checks = [scene.check_candidate(candidate) for candidate in candidates]
        allowed = {row.candidate_id for row in checks if row.feasible}
        return dict(schema_version="sequence_filter_v1",task_id=task_id,seed=seed,
            status="ok" if allowed else "no_feasible_candidate",
            controller_version=SEQUENCE_CONTROLLER,candidate_sha256=candidate_sha256(candidates),
            evaluation_version=SEQUENCE_EVALUATOR,scene_version="hollow_cup_sequences_v1",
            task_spec_sha256=spec_sha256(scene.spec),task_spec=scene.spec,
            observation_config=asdict(scene.observation_config),
            initial_object_pose_estimate=asdict(scene.observed_mug_pose),
            scene_randomization=asdict(scene.scene_config),
            feasible_candidates=[c.public_metadata() for c in candidates if c.id in allowed],
            checks=[asdict(row) for row in checks],
            scope="initial width/IK/sampled approach only; not full-task feasibility or outcome")


def run_sequence(task_id,seed,selected_id,candidates,*,config=None,observation_config=None,
                 gui=False,media_dir=None,show_landmarks=True,scene_config=None,liquid="pbf"):
    """Run a fresh physical episode; landmarks change recorded pixels only.

    Video stems end in _ab or _plain, so both modes can share media_dir.
    Legacy preparation APIs and task/scoring versions are not changed.
    """
    if type(show_landmarks) is not bool:
        raise ValueError("show_landmarks must be a bool")
    matches = [c for c in candidates if c.id == selected_id]
    if len(matches) != 1:
        raise ValueError("selected_id must match exactly one supplied candidate")
    with SequenceScene(task_id,seed,config,gui,observation_config,scene_config=scene_config,
                       liquid=liquid) as scene:
        if media_dir is not None:
            from .visualization import EpisodeRecorder
            from .annotations import sequence_annotations
            mode = "ab" if show_landmarks else "plain"
            scene.recorder = EpisodeRecorder(scene.client,media_dir,f"{task_id}_seed{seed}_{selected_id}_{mode}",
                        camera_kwargs=dict(target=(.53,.12,.75),distance=.80,pitch=-40,yaw=62,
                                           fluid_provider=lambda: scene.fluid,
                                           annotations=sequence_annotations(scene.spec) if show_landmarks else None))
        result = scene.run_sequence(matches[0])
        result["candidate_sha256"] = candidate_sha256(candidates)
        if scene.recorder:
            result["media"] = scene.recorder.finish(result)
            result["media"]["landmarks_enabled"] = show_landmarks
            scene.recorder = None
        return result
