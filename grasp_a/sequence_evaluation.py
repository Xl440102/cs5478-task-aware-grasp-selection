"""Privileged physical scoring; never used to choose/stop a sequence action."""
import math
import numpy as np
from .simulation import STEP, p, position_error
from .fluid import rotation_matrix


def upright_deg(quaternion):
    return math.degrees(math.acos(float(np.clip(rotation_matrix(quaternion)[2,2],-1,1))))


def terminal_checks(task_id, config, evidence):
    """Pure predicate for independent auditing and failure-injection tests."""
    e, c = evidence, config
    checks = dict(source_upright=e["source_upright_deg"] <= c.upright_tolerance_deg,
                  source_linear_stable=e["source_linear_speed_m_s"] <= c.linear_speed_m_s,
                  source_angular_stable=e["source_angular_speed_deg_s"] <= c.angular_speed_deg_s)
    if task_id == "transport_place":
        checks.update(at_B=e["source_xy_error_m"] <= c.placement_tolerance_m,
            tabletop_height=abs(e["source_z_m"]-.645) <= .01,
            table_support=e["table_contact"],donor_released=not e["donor_contact"],
            donor_clear=e["donor_clearance_m"] >= c.donor_clearance_m)
    elif task_id == "handover_transfer":
        checks.update(at_B=e["source_xy_error_m"] <= c.placement_tolerance_m,
            receiver_support=e["receiver_contact"],donor_released=not e["donor_contact"],
            donor_clear=e["donor_clearance_m"] >= c.donor_clearance_m,
            independently_lifted=e["source_z_m"] >= c.carry_height_m+c.receiver_lift_m-.015,
            not_on_table=not e["table_contact"])
    else:
        water = e.get("water")
        proxy = e.get("pour_proxy")
        checks.update(donor_support=e["donor_contact"],
            source_at_goal=e["source_goal_error_m"] <= .03,
            target_stable=e["target_upright_deg"] <= c.upright_tolerance_deg and
                          e["target_linear_speed_m_s"] <= c.linear_speed_m_s and
                          e["target_angular_speed_deg_s"] <= c.angular_speed_deg_s,
            target_unmoved=e["target_displacement_m"] <= .01,
            )
        if proxy is not None:
            # Dry proxy: a dwell spent pouring over the receiver, unobstructed.
            checks.update(spout_over_target=proxy["over_target_fraction"] >= c.transfer_min_ratio,
                          pour_path_clear=proxy["path_clear_fraction"] >= c.transfer_min_ratio,
                          poured_at_tilt=proxy["dwell_samples"] > 0 and proxy["min_tilt_deg"] >= 80)
            if proxy.get("margins_enforced"):
                checks.update(
                    receiver_margin=proxy["min_source_target_gap_m"] >= PROXY_TARGET_MARGIN_M,
                    palm_margin=proxy["min_palm_source_gap_m"] >= PROXY_PALM_MARGIN_M)
        else:
            checks.update(
                received=water is not None and water["transfer_ratio"] >= c.transfer_min_ratio,
                spill_limited=water is not None and water["spill_ratio"] <= c.spill_max_ratio,
                fluid_valid=water is not None and water["finite_state"] and
                            water["accounting_error_kg"] < 1e-9 and water["max_compression"] <= .10)
    return checks


# proxy_margin thresholds, calibrated on 15 particle-fluid reruns (see
# docs/EXPERIMENT_20261103_CANDIDATES_SEEDS.md). Water impulses sway the cup
# inside the grip; tight clearances that are safe dry become contacts wet.
PROXY_TARGET_MARGIN_M = 0.003
PROXY_PALM_MARGIN_M = 0.004


def proxy_gaps(scene, stage):
    """Smallest source/receiver and palm/source gaps (m, capped at 0.05)."""
    s = scene
    out = {}
    if stage in ("tilt", "pour_dwell", "return_upright") and s.target_cup is not None:
        pts = p.getClosestPoints(s.mug, s.target_cup, distance=.05, physicsClientId=s.client)
        out["source_target"] = min((cp[8] for cp in pts), default=.05)
    if stage in ("lift", "transport", "tilt", "pour_dwell", "return_upright", "settle", "terminal"):
        pts = [cp for cp in p.getClosestPoints(s.robot, s.mug, distance=.05, physicsClientId=s.client)
               if cp[3] not in s.fingers]
        out["palm_source"] = min((cp[8] for cp in pts), default=.05)
    return out


def pour_proxy_sample(scene):
    """Geometric pour proxy for liquid="proxy"; evaluation only, never control.

    Spout = lowest point of the source rim. The stream falls vertically from
    it. Over target: spout inside the receiver's inner radius (5 mm margin) and
    above its rim. Clear: neither the cup-centre->spout ray (mouth occlusion by
    the donor) nor the spout->receiver-rim ray hits the robot.
    """
    s = scene
    source, target = s.vessels()
    rot = rotation_matrix(source.quaternion)
    centre = np.asarray(source.position)
    angles = np.linspace(0, 2*math.pi, 48, endpoint=False)
    rim = np.stack([source.outer_radius*np.cos(angles), source.outer_radius*np.sin(angles),
                    np.full_like(angles, source.rim)], axis=1) @ rot.T + centre
    spout = rim[int(np.argmin(rim[:, 2]))]
    tpos = np.asarray(target.position)
    target_rim_z = tpos[2]+target.rim
    radial = math.hypot(spout[0]-tpos[0], spout[1]-tpos[1])
    over = radial <= target.inner_radius-.005 and spout[2] > target_rim_z
    mouth = centre+rot@np.array((0, 0, source.rim*.5))
    # Rays report only the first body hit, so stop short of / start just off
    # the source wall; otherwise the cup itself would hide the gripper.
    near_spout = spout+(mouth-spout)*.12
    below_spout = spout-np.array((0, 0, .006))
    rays = p.rayTestBatch([mouth.tolist(), below_spout.tolist()],
                          [near_spout.tolist(), [spout[0], spout[1], target_rim_z]],
                          physicsClientId=s.client)
    clear = all(hit[0] != s.robot for hit in rays)
    return dict(over_target=bool(over), path_clear=bool(clear), spout_radial_m=radial,
                tilt_deg=upright_deg(source.quaternion))


class SequenceEvaluator:
    def __init__(self, scene):
        self.scene = scene
        self.by_stage = {}
        self.trajectory = []
        self.collision_types = set()
        self.collision_events = []
        self.terminal_all = {}
        self.terminal_samples = 0
        self.gap_steps = 0
        self.max_gap_steps = 0
        self.lifted = False
        self.dropped = False
        self.max_fluid_compression = 0
        self.pour_proxy = dict(dwell_samples=0, over_target=0, path_clear=0,
                               min_tilt_deg=180., max_spout_radial_m=0.,
                               min_source_target_gap_m=.05, min_palm_source_gap_m=.05,
                               margins_enforced=getattr(scene, "liquid", "pbf") == "proxy_margin")

    def pour_proxy_summary(self):
        pr = self.pour_proxy
        n = max(1, pr["dwell_samples"])
        return dict(pr, over_target_fraction=pr["over_target"]/n,
                    path_clear_fraction=pr["path_clear"]/n,
                    min_tilt_deg=pr["min_tilt_deg"] if pr["dwell_samples"] else 0.)

    def _contacts(self, first, second, links=None):
        if first is None or second is None:
            return []
        contacts = p.getContactPoints(first,second,physicsClientId=self.scene.client)
        return [cp for cp in contacts if cp[8] <= .001 and cp[9] > 1e-4 and
                (links is None or cp[3] in links)]

    def evidence(self):
        s, c = self.scene, self.scene.config
        source, target = s.vessels()
        linear,angular = p.getBaseVelocity(s.mug,physicsClientId=s.client)
        closest = p.getClosestPoints(s.robot,s.mug,distance=2,physicsClientId=s.client)
        bx,by = c.target_xy
        receiver_links = sorted({cp[3] for cp in self._contacts(s.receiver,s.mug,(2,3))})
        out = dict(source_position=list(source.position),source_quaternion=list(source.quaternion),
            source_z_m=source.position[2],source_upright_deg=upright_deg(source.quaternion),
            source_xy_error_m=math.hypot(source.position[0]-bx,source.position[1]-by),
            source_goal_error_m=position_error(source.position,(bx,by+(math.copysign(.045,c.pour_angle_deg) if s.task_id=="pour_water" else 0),c.carry_height_m)),
            source_linear_speed_m_s=float(np.linalg.norm(linear)),
            source_angular_speed_deg_s=math.degrees(float(np.linalg.norm(angular))),
            donor_contact=bool(self._contacts(s.robot,s.mug,s.fingers)),
            receiver_contact=receiver_links == [2,3],receiver_contact_links=receiver_links,
            table_contact=bool(self._contacts(s.mug,s.table)),
            donor_clearance_m=min((cp[8] for cp in closest),default=2.0))
        if target is not None:
            v,w = p.getBaseVelocity(s.target_cup,physicsClientId=s.client)
            out.update(target_upright_deg=upright_deg(target.quaternion),
                target_linear_speed_m_s=float(np.linalg.norm(v)),
                target_angular_speed_deg_s=math.degrees(float(np.linalg.norm(w))),
                target_displacement_m=position_error(target.position,(bx,by,.645)),
                water=s.fluid.mass_accounting(source,target) if s.fluid else None)
        return out

    def _collisions(self,stage):
        s = self.scene
        types = s._unexpected_collision(allow_finger_mug=stage != "pregrasp")
        # A shallow contact can still carry a large blocking force. In this
        # track, force-bearing unintended contact is not excused by the 1 mm
        # penetration tolerance inherited from the legacy geometry checker.
        for cp in p.getContactPoints(s.robot,s.robot,physicsClientId=s.client):
            a,b = cp[3],cp[4]
            if cp[9] <= .2 or s.rigid_group[a] == s.rigid_group[b]:
                continue
            pa = p.getJointInfo(s.robot,a,physicsClientId=s.client)[16] if a>=0 else -2
            pb = p.getJointInfo(s.robot,b,physicsClientId=s.client)[16] if b>=0 else -2
            if a != b and a != pb and b != pa and {a,b} != set(s.fingers):
                types.append("robot_self_force_contact")
        pairs = []
        if s.target_cup is not None:
            pairs += [(s.robot,s.target_cup,"donor_target_cup"),
                      (s.mug,s.target_cup,"source_target_cup")]
        if s.receiver is not None:
            pairs += [(s.robot,s.receiver,"donor_receiver"),
                      (s.receiver,s.table,"receiver_table"),(s.receiver,s.plane,"receiver_floor")]
            for cp in p.getContactPoints(s.receiver,s.mug,physicsClientId=s.client):
                if cp[8] < -.001 or cp[9] > .2:
                    if cp[3] not in (2,3):
                        types.append("receiver_nonfinger_source")
                    else:
                        # Contact must be on the handle, not a hidden body grip.
                        src,_ = s.vessels()
                        local = src.local(np.asarray([cp[6]]))[0]
                        if local[1] < .035:
                            types.append("receiver_finger_not_handle")
        for first,second,label in pairs:
            if any(cp[8] < -.001 or cp[9] > .2 for cp in p.getContactPoints(first,second,physicsClientId=s.client)):
                types.append(label)
        table_allowed = (stage in ("pregrasp","approach","close") or
                        (stage == "lift" and not self.lifted) or
                        (s.task_id == "transport_place" and stage in ("lower","release","retreat","settle","terminal")))
        if not table_allowed and self._contacts(s.mug,s.table):
            types.append("source_table_out_of_phase")
        if self._contacts(s.mug,s.plane):
            types.append("source_floor")
        return sorted(set(types))

    def sample(self, stage, step, last):
        s = self.scene
        e = self.evidence()
        e.update(time_s=s.physics_steps*STEP,stage=stage)
        row = self.by_stage.setdefault(stage,dict(samples=0,endpoint=None,
            donor_contact_seen=False,receiver_contact_seen=False,table_support_seen=False,
            source_height_max_m=0,collision_types=set()))
        row["samples"] += 1
        row["endpoint"] = e
        row["donor_contact_seen"] |= e["donor_contact"]
        row["receiver_contact_seen"] |= e["receiver_contact"]
        row["table_support_seen"] |= e["table_contact"]
        row["source_height_max_m"] = max(row["source_height_max_m"],e["source_z_m"])
        if e["source_z_m"] >= .735 and e["donor_contact"]:
            self.lifted = True
        supported = e["donor_contact"] or bool(e["receiver_contact_links"]) or e["table_contact"]
        if self.lifted:
            self.gap_steps = 0 if supported else self.gap_steps+1
            self.max_gap_steps = max(self.max_gap_steps,self.gap_steps)
            if self.gap_steps*STEP > s.config.drop_contact_gap_s:
                self.dropped = True
        collisions = self._collisions(stage)
        row["collision_types"].update(collisions)
        for kind in collisions:
            if kind not in self.collision_types:
                event = dict(time_s=e["time_s"],stage=stage,type=kind)
                if kind == "donor_target_cup":
                    event["contacts"] = [dict(link=s._link_name(cp[3]),force_n=cp[9],
                                             position=cp[5],penetration_m=max(0,-cp[8]))
                        for cp in p.getContactPoints(s.robot,s.target_cup,physicsClientId=s.client)]
                self.collision_events.append(event)
        self.collision_types.update(collisions)
        proxy_mode = getattr(s, "liquid", "pbf") != "pbf" and s.task_id == "pour_water"
        if proxy_mode and s.physics_steps % 24 == 0:
            gaps = proxy_gaps(s, stage)
            pr = self.pour_proxy
            if "source_target" in gaps:
                pr["min_source_target_gap_m"] = min(pr["min_source_target_gap_m"], gaps["source_target"])
            if "palm_source" in gaps:
                pr["min_palm_source_gap_m"] = min(pr["min_palm_source_gap_m"], gaps["palm_source"])
        if proxy_mode and stage == "pour_dwell":
            sample = pour_proxy_sample(s)
            pr = self.pour_proxy
            pr["dwell_samples"] += 1
            pr["over_target"] += sample["over_target"]
            pr["path_clear"] += sample["path_clear"]
            pr["min_tilt_deg"] = min(pr["min_tilt_deg"], sample["tilt_deg"])
            pr["max_spout_radial_m"] = max(pr["max_spout_radial_m"], sample["spout_radial_m"])
        if stage == "terminal" and proxy_mode:
            e["pour_proxy"] = self.pour_proxy_summary()
        if e.get("water"):
            self.max_fluid_compression = max(self.max_fluid_compression,e["water"]["max_compression"])
        if stage == "terminal":
            checks = terminal_checks(s.task_id,s.config,e)
            self.terminal_samples += 1
            for key,value in checks.items():
                self.terminal_all[key] = self.terminal_all.get(key,True) and bool(value)
        if s.physics_steps % 24 == 0 or stage == "terminal":
            self.trajectory.append(e)

    def finish(self):
        s = self.scene
        rows = {}
        for spec in s.spec["stages"]:
            stage = spec["id"]
            evidence = self.by_stage.get(stage)
            physical = {}
            if evidence:
                e = evidence["endpoint"]
                physical["collision_free"] = not evidence["collision_types"]
                if stage == "close": physical["contact_formed"] = e["donor_contact"]
                if stage == "lift": physical["lifted_with_donor"] = e["source_z_m"] >= .735 and e["donor_contact"]
                if stage == "transport":
                    physical.update(at_B=e["source_goal_error_m"] <= .03,
                                    upright=e["source_upright_deg"] <= s.config.upright_tolerance_deg)
                if stage == "lower": physical["table_support"] = e["table_contact"]
                if stage == "release": physical["table_support"] = e["table_contact"]
                if stage == "receiver_close": physical["recipient_contact_started"] = bool(e["receiver_contact_links"])
                if stage == "donor_release": physical["recipient_support"] = e["receiver_contact"]
                if stage == "receiver_lift":
                    before = self.by_stage.get("retreat",{}).get("endpoint",{})
                    physical.update(recipient_support=e["receiver_contact"],
                        source_rose=e["source_z_m"]-before.get("source_z_m",e["source_z_m"]) >= s.config.receiver_lift_m-.015)
                if stage == "retreat" and s.task_id != "pour_water":
                    physical.update(donor_detached=not e["donor_contact"],
                                    donor_clear=e["donor_clearance_m"] >= s.config.donor_clearance_m)
                if stage == "terminal": physical.update(self.terminal_all)
            rows[stage] = dict(evidence={**evidence,"collision_types":sorted(evidence["collision_types"])} if evidence else None,
                               physical_checks=physical,physical_pass=bool(physical) and all(physical.values()))
        complete = all(row["control_status"] == "completed" for row in s.stage_log) and len(s.stage_log)==len(s.spec["stages"])
        window = self.terminal_samples >= round(s.config.terminal_window_s/STEP)
        checks = dict(sequence_complete=complete,lift_achieved=self.lifted,
            no_drop=not self.dropped,no_unintended_collision=not self.collision_types,
            stage_outcomes=all(row["physical_pass"] for row in rows.values()),
            full_terminal_window=window,terminal_outcome=bool(self.terminal_all) and all(self.terminal_all.values()),
            fluid_density_valid=self.max_fluid_compression <= .10)
        failure = next((key for key,passed in checks.items() if not passed),None)
        return dict(success=all(checks.values()),failure_reason=failure,success_checks=checks,
            terminal_checks=self.terminal_all,stage_evaluation=rows,
            collision=bool(self.collision_types),collision_types=sorted(self.collision_types),
            collision_events=self.collision_events,dropped=self.dropped,
            maximum_unsupported_s=self.max_gap_steps*STEP,terminal_samples=self.terminal_samples,
            max_fluid_compression=self.max_fluid_compression,
            pour_proxy=self.pour_proxy_summary() if getattr(s, "liquid", "pbf") != "pbf" and s.task_id == "pour_water" else None,
            final_evidence=self.evidence(),trajectory=self.trajectory)
