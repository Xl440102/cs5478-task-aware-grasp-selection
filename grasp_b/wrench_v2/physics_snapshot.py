"""Static asset audit ONLY: isolated DIRECT client, no steps or motor commands.

This queries initial asset dynamics, not trial state or observed contacts. A's
changeDynamics surface overrides are reported separately as SOURCE declarations;
we do not apply them or pretend the raw loaded friction equals A's override.
Never automatically replaces TWS assumptions or finger capacity parameters.
"""
import hashlib
from importlib.metadata import version
from pathlib import Path
import sys


def static_physics_snapshot(repository_root=None):
    import pybullet as p
    import pybullet_data

    root = Path(repository_root) if repository_root is not None else Path(__file__).resolve().parents[2]
    panda = Path(pybullet_data.getDataPath())/"franka_panda/panda.urdf"
    paths = {"legacy": root/"assets/mug.urdf", "sequence": root/"assets/mug_hollow.urdf", "panda": panda}
    client = p.connect(p.DIRECT)
    if client < 0:
        raise RuntimeError("DIRECT client unavailable")

    def dynamics(body, link):
        values = p.getDynamicsInfo(body, link, physicsClientId=client)
        keys = ("mass_kg", "lateral_friction", "local_inertia_diagonal_kg_m2",
                "local_inertial_position_m", "local_inertial_quaternion_xyzw",
                "restitution", "rolling_friction", "spinning_friction",
                "contact_damping", "contact_stiffness")
        return dict(zip(keys, values[:10]))

    def shapes(body, link):
        return [dict(link_index=v[1], geometry_type=v[2], pybullet_reported_dimensions=v[3],
                     filename=v[4].decode(errors="replace"), local_position_m=v[5], local_quaternion_xyzw=v[6])
                for v in p.getCollisionShapeData(body, link, physicsClientId=client)]

    try:
        result = dict(snapshot_version="static_asset_load_v1", physics_steps=0, motor_commands=0,
            python=sys.version.split()[0], pybullet=version("pybullet"), pybullet_api=p.getAPIVersion(),
            collision_pose_convention="raw getCollisionShapeData local pose, not world; retain local inertial transform separately",
            assets={}, source_sha256={name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                for name in ("grasp_a/simulation.py", "grasp_a/sequence.py", "grasp_a/control.py")},
            source_declared_overrides_not_applied=dict(
                source="grasp_a/simulation.py:MugScene.__init__",
                mug_lateral_friction=1.2, finger_lateral_friction=2., finger_spinning_friction=.05,
                contact_normal_force_capacity_n=None, contact_mu_calibrated=False))
        for name, path in paths.items():
            robot = name == "panda"
            flags = p.URDF_USE_SELF_COLLISION | p.URDF_USE_SELF_COLLISION_EXCLUDE_PARENT if robot else 0
            # Same URDF loader flags and fixed-base setting as A. No inertia-
            # from-file flag is introduced to "correct" the measured mismatch.
            body = p.loadURDF(str(path), basePosition=(0, 0, .6) if robot else (.55, 0, .645),
                              useFixedBase=robot, flags=flags, physicsClientId=client)
            entry = dict(asset_path=str(path), asset_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         flags=flags, fixed_base=robot, dynamics=dynamics(body, -1), collision_shapes=shapes(body, -1))
            if robot:
                fingers = {}
                for j in range(p.getNumJoints(body, physicsClientId=client)):
                    info = p.getJointInfo(body, j, physicsClientId=client)
                    if info[1].decode() in ("panda_finger_joint1", "panda_finger_joint2"):
                        fingers[info[1].decode()] = dict(link_index=j, dynamics=dynamics(body, j),
                                                       collision_shapes=shapes(body, j))
                entry["fingers"] = fingers
            result["assets"][name] = entry
        return result
    finally:
        p.disconnect(physicsClientId=client)
