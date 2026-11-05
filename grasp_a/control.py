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
class SceneRandomization:
    """Per-seed initial cup pose and where the controller's estimate comes from.

    The default reproduces the legacy scene exactly (same RNG draws, same
    +/-8 mm / +/-0.08 rad ranges, calibrated prior estimate). With that default
    every seed is filtered against the SAME estimated pose, so A's feasible set
    cannot change with the seed. ``observation="noisy_truth"`` instead builds the
    estimate from the seeded true pose plus independent bounded noise, which is
    what an external pose estimator would supply. Offsets are added to the
    nominal pose (0.55, 0.0) in world metres; yaw is a world-Z rotation.
    """
    name: str = "legacy"
    x_range_m: tuple = (-0.008, 0.008)
    y_range_m: tuple = (-0.008, 0.008)
    yaw_range_rad: tuple = (-0.08, 0.08)
    observation: str = "prior"  # prior / noisy_truth
    estimate_position_noise_m: float = 0.0
    estimate_yaw_noise_deg: float = 0.0
    # Protocol knob kept here so one preset defines the whole filter/controller
    # setup: multi-start IK for the feasibility filter and joint-path moves.
    ik_restarts: bool = False
    # "legacy": A's original pregrasp/grasp check. "execution": the sequence
    # track replays every donor stage kinematically with execution's own IK,
    # waypoints and tolerances (SequenceScene only).
    filter_mode: str = "legacy"

    def __post_init__(self):
        for key in ("x_range_m", "y_range_m", "yaw_range_rad"):
            value = tuple(float(v) for v in getattr(self, key))
            if len(value) != 2 or not all(math.isfinite(v) for v in value) or value[0] > value[1]:
                raise ValueError(f"{key} must be a finite (low, high) pair")
            object.__setattr__(self, key, value)
        if not (-0.10 <= self.x_range_m[0] and self.x_range_m[1] <= 0.10 and
                -0.10 <= self.y_range_m[0] and self.y_range_m[1] <= 0.10 and
                -math.pi <= self.yaw_range_rad[0] and self.yaw_range_rad[1] <= math.pi):
            raise ValueError("cup must stay within +/-10 cm and +/-180 deg of nominal")
        if self.filter_mode not in ("legacy", "execution"):
            raise ValueError("filter_mode must be legacy or execution")
        if type(self.ik_restarts) is not bool:
            raise ValueError("ik_restarts must be a bool")
        if self.observation not in ("prior", "noisy_truth"):
            raise ValueError("observation must be prior or noisy_truth")
        if not all(math.isfinite(v) and v >= 0 for v in
                   (self.estimate_position_noise_m, self.estimate_yaw_noise_deg)):
            raise ValueError("estimate noise must be finite and nonnegative")
        if self.observation == "prior" and (self.estimate_position_noise_m or
                                            self.estimate_yaw_noise_deg):
            raise ValueError("prior mode takes noise from ObservationConfig, not here")

    def estimate_config(self, seed: int, true_pose: Pose) -> ObservationConfig | None:
        """ObservationConfig for noisy_truth mode; None keeps the caller's prior."""
        if self.observation != "noisy_truth":
            return None
        return ObservationConfig(object_pose=true_pose, source="external_estimate",
                                 position_noise_m=self.estimate_position_noise_m,
                                 yaw_noise_deg=self.estimate_yaw_noise_deg,
                                 noise_seed=1_000_003 * int(seed) + 17)


# Presets. Keep ``legacy`` for reproducing archived results. ``wide`` keeps the
# cup 10 cm or more clear of pour_water's target cup (y=0.16, r=0.058) even with
# the handle (reach 0.092 m) pointing at it, hence the asymmetric y range.
SCENE_PRESETS = {
    "legacy": SceneRandomization(),
    "wide": SceneRandomization(
        name="wide", x_range_m=(-0.04, 0.04), y_range_m=(-0.05, 0.0),
        yaw_range_rad=(-math.pi/3, math.pi/3), observation="noisy_truth",
        estimate_position_noise_m=0.003, estimate_yaw_noise_deg=2.0, ik_restarts=True),
    # wide + execution-consistent filtering (sequence tasks).
    "wide_strict": SceneRandomization(
        name="wide_strict", x_range_m=(-0.04, 0.04), y_range_m=(-0.05, 0.0),
        yaw_range_rad=(-math.pi/3, math.pi/3), observation="noisy_truth",
        estimate_position_noise_m=0.003, estimate_yaw_noise_deg=2.0, ik_restarts=True,
        filter_mode="execution"),
    # Legacy scene with only the IK fix, to separate the two effects.
    "legacy_ik": SceneRandomization(name="legacy_ik", ik_restarts=True),
}


def scene_randomization(value) -> SceneRandomization:
    if value is None:
        return SCENE_PRESETS["legacy"]
    if isinstance(value, SceneRandomization):
        return value
    if isinstance(value, str) and value in SCENE_PRESETS:
        return SCENE_PRESETS[value]
    if isinstance(value, dict):
        return SceneRandomization(**value)
    raise ValueError(f"scene randomization must be one of {sorted(SCENE_PRESETS)} or a dict")


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
