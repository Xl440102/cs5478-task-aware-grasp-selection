"""Privileged scoring channel. Never return this data to an acting policy."""
from dataclasses import asdict


class EpisodeEvaluator:
    def __init__(self, scene, target):
        self.scene, self.target = scene, target
        self.trajectory = []
        self.problems = set()
        self.hold_steps = 0
        self.hold_pose_ok = True
        self.hold_grasp_ok = True
        self.ever_lifted = False
        self.dropped = False

    def sample(self, stage, step, last):
        # Hold is checked EVERY physics step; other stages are sampled at 10 Hz.
        if stage != "hold" and step % 24 != 0 and not last:
            return
        from .simulation import TABLE_TOP, orientation_error_deg, position_error
        obj = self.scene._object_pose()
        contacts = self.scene._finger_contacts()
        held = contacts > 0 and obj.position[2] > TABLE_TOP + 0.09
        # An intended change in object height is not a drop. Track loss of
        # finger contact after a lift; score height separately during hold.
        if self.ever_lifted and contacts == 0:
            self.dropped = True
        self.ever_lifted |= held
        if stage == "hold":
            self.hold_steps += 1
            self.hold_grasp_ok &= held
            self.hold_pose_ok &= (
                position_error(obj.position, self.target.position) <= 0.04 and
                orientation_error_deg(obj.quaternion, self.target.quaternion) <= 15)
        if step % 24 == 0 or last:
            self.trajectory.append({"stage": stage, "step": step,
                                    "physics_step": self.scene.physics_steps,
                                    "action_index": len(self.scene.primitive_log),
                                    "mug_pose": asdict(obj),
                                    "tool_pose": asdict(self.scene._tip_pose()),
                                    "finger_contacts": contacts})
            self.problems.update(self.scene._unexpected_collision(
                allow_finger_mug=stage != "pregrasp"))
            if self.ever_lifted:
                import pybullet as p
                if p.getContactPoints(self.scene.mug, self.scene.table,
                                       physicsClientId=self.scene.client):
                    self.problems.add("mug_table_after_lift")

    def finish(self):
        from .simulation import STEP, TABLE_TOP, orientation_error_deg, position_error
        final = self.scene._object_pose()
        held = self.scene._finger_contacts() > 0 and final.position[2] > TABLE_TOP + 0.09
        pe = position_error(final.position, self.target.position)
        re = orientation_error_deg(final.quaternion, self.target.quaternion)
        maintained = held and self.hold_grasp_ok and not self.dropped
        full_hold = self.hold_steps >= round(2 / STEP)
        success = maintained and full_hold and self.hold_pose_ok and not self.problems
        reason = (None if success else "lost_grasp" if not maintained else
                  "unintended_collision" if self.problems else
                  "hold_incomplete" if not full_hold else "target_pose_error")
        return {"success": success, "failure_reason": reason,
                "dropped": self.dropped, "collision": bool(self.problems),
                "collision_types": sorted(self.problems),
                "grasp_maintained": maintained, "position_error_m": pe,
                "orientation_error_deg": re, "target_mug_pose": asdict(self.target),
                "final_mug_pose": asdict(final), "trajectory": self.trajectory,
                "hold_steps": self.hold_steps, "hold_duration_s": self.hold_steps * STEP,
                "hold_pose_within_tolerance": self.hold_pose_ok and full_hold,
                "evaluation_version": "fixed_target_hold_v2"}
