"""Observable control contracts. No simulator object/contact queries live here."""
from dataclasses import asdict, dataclass
import math

from .contracts import Pose


@dataclass(frozen=True)
class ObservationConfig:
    """A calibrated prior or caller-supplied pose estimate, NOT a vision system.

    Noise is applied to the estimate, independently of scene randomization. It
    never starts from the simulator's actual object pose.
    """
    object_pose: Pose = Pose((0.55, 0.0, 0.645), (0.0, 0.0, 0.0, 1.0))
    source: str = "calibrated_prior"
    position_noise_m: float = 0.0  # uniform +/- error per world axis
    yaw_noise_deg: float = 0.0  # uniform +/- world-Z rotation
    noise_seed: int = 0

    def __post_init__(self):
        if self.source not in ("calibrated_prior", "external_estimate"):
            raise ValueError("source must be calibrated_prior or external_estimate")
        object.__setattr__(self, "object_pose", Pose.from_dict(asdict(self.object_pose)))
        if (not all(math.isfinite(x) and x >= 0 for x in
                    (self.position_noise_m, self.yaw_noise_deg))
                or type(self.noise_seed) is not int):
            raise ValueError("observation noise must be finite/nonnegative; seed must be integer")


@dataclass(frozen=True)
class Primitive:
    """One bounded action, not a pick/place macro.

    move_tcp is an absolute WORLD panda_grasptarget pose, metres/xyzw.
    width is TOTAL opening in metres. duration is simulated seconds.
    """
    kind: str  # move_tcp / gripper / wait
    target: Pose | None = None
    width: float | None = None
    duration_s: float = 0.5
    path: str = "cartesian"  # move_tcp: continuous TCP waypoints or joint interpolation

    def __post_init__(self):
        if (self.kind not in ("move_tcp", "gripper", "wait") or
                self.path not in ("cartesian", "joint") or
                not math.isfinite(self.duration_s) or not 1/240 <= self.duration_s <= 2):
            raise ValueError("unknown primitive or duration outside [1/240, 2] seconds")
        if self.kind == "move_tcp":
            if self.target is None or self.width is not None:
                raise ValueError("move_tcp needs only a target pose")
            Pose.from_dict(asdict(self.target))
            x, y, z = self.target.position
            if not (0.20 <= x <= 0.90 and -0.45 <= y <= 0.45 and 0.60 <= z <= 1.20):
                raise ValueError("TCP target outside configured tabletop workspace")
        elif self.kind == "gripper":
            if (self.target is not None or self.width is None or
                    not math.isfinite(self.width) or not 0 <= self.width <= 0.08):
                raise ValueError("gripper needs only total width in [0, 0.08] metres")
        elif self.target is not None or self.width is not None:
            raise ValueError("wait accepts neither target nor width")


class ControlSession:
    """Policy-facing facade. act/observe return proprioception, never scoring truth.

    This is an API boundary, not a Python security sandbox. Policies must not
    access private attributes, the PyBullet client or global simulator APIs.
    """
    def __init__(self, scene):
        self._scene = scene

    def observe(self) -> dict:
        return self._scene.observe()

    def act(self, command: Primitive) -> dict:
        return self._scene.execute_primitive(command)
