"""Versioned, machine-readable task/stage definitions; no simulator imports."""
from dataclasses import asdict, dataclass, replace
import math

SEQUENCE_TASKS = ("transport_place", "handover_transfer", "pour_water")
SEQUENCE_CONTROLLER = "staged_sequences_v1"
SEQUENCE_EVALUATOR = "physical_outcomes_v1"


@dataclass(frozen=True)
class SequenceConfig:
    target_xy: tuple = (0.55, 0.16)
    carry_height_m: float = 0.80
    episode_timeout_s: float = 25.0
    arrival_position_m: float = 0.02
    arrival_angle_deg: float = 10.0
    arrival_continuous_s: float = 0.10
    arrival_timeout_s: float = 2.0
    terminal_window_s: float = 1.0
    settle_s: float = 1.0
    placement_tolerance_m: float = 0.02
    upright_tolerance_deg: float = 5.0
    linear_speed_m_s: float = 0.02
    angular_speed_deg_s: float = 5.0
    drop_contact_gap_s: float = 0.10
    receiver_lift_m: float = 0.05
    donor_clearance_m: float = 0.08
    transfer_min_ratio: float = 0.90
    spill_max_ratio: float = 0.05
    water_mass_kg: float = 0.100
    fluid_particles: int = 256
    fluid_iterations: int = 8
    fluid_substeps: int = 2
    pour_angle_deg: float = -110.0
    pour_dwell_s: float = 2.0

    def __post_init__(self):
        values = asdict(self)
        if (len(self.target_xy) != 2 or not all(type(x) in (int, float) and math.isfinite(x)
                                               for x in self.target_xy)):
            raise ValueError("target_xy must contain two finite metres")
        if not (0.30 <= self.target_xy[0] <= 0.75 and -0.25 <= self.target_xy[1] <= 0.25):
            raise ValueError("target B outside supported tabletop workspace")
        for key, value in values.items():
            if key == "target_xy":
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
            if key not in ("pour_angle_deg", "spill_max_ratio") and value <= 0:
                raise ValueError(f"{key} must be positive")
        if not .74 <= self.carry_height_m <= .95 or not 80 <= abs(self.pour_angle_deg) <= 135:
            raise ValueError("carry height or pouring angle outside supported range")
        if not 0 < self.transfer_min_ratio <= 1 or not 0 <= self.spill_max_ratio < 1:
            raise ValueError("mass ratios outside [0,1]")
        for key in ("fluid_particles", "fluid_iterations", "fluid_substeps"):
            if type(getattr(self, key)) is not int:
                raise ValueError(f"{key} must be an integer")
        if not 64 <= self.fluid_particles <= 1024 or not 1 <= self.fluid_iterations <= 12:
            raise ValueError("unsupported fluid discretization")
        if not 1 <= self.fluid_substeps <= 8 or self.spill_max_ratio < 0:
            raise ValueError("invalid fluid substeps or spill ratio")


@dataclass(frozen=True)
class StageSpec:
    id: str
    actor: str
    action: str
    entry: str
    control_exit: str
    evaluation: str
    timeout_s: float
    allowed_contacts: tuple


def task_spec(task_id, config=None):
    if task_id not in SEQUENCE_TASKS:
        raise ValueError(f"sequence task must be one of {SEQUENCE_TASKS}")
    c = config or SequenceConfig()
    donor = ("donor_fingers_source",)
    common = [
        StageSpec("pregrasp", "donor", "Move to estimated pregrasp; jaws open", "Fresh scene and selected candidate feasible", "TCP arrival sustained", "No unintended collision", 3, ("source_table",)),
        StageSpec("approach", "donor", "Move to estimated candidate grasp pose", "pregrasp control completed", "TCP arrival sustained", "No nonfinger source collision", 3, donor+("source_table",)),
        StageSpec("close", "donor", "Close jaws under force-limited motor control", "approach control completed", "Fixed 0.75 s close; encoder feedback only", "Physical source/finger contact formed", 1, donor+("source_table",)),
        StageSpec("lift", "donor", "Raise nominal source by 0.14 m", "close action completed, not an oracle grasp gate", "TCP arrival sustained", "Source lifted >=0.09 m with donor contact", 4, donor),
        StageSpec("transport", "donor", "Move upright source from A to the B work region", "lift control completed", "TCP arrival sustained", "Source near prescribed B pose; no drop/collision", 4, donor),
    ]
    if task_id == "transport_place":
        rest = [
            StageSpec("lower", "donor", "Lower nominal center to z=0.642 m (3 mm compliant overtravel)", "transport control completed", "TCP arrival sustained", "Actual source/table contact at B", 4, donor+("source_table",)),
            StageSpec("release", "donor", "Open jaws; do not attach or reposition source", "lower control completed", "Fixed 0.5 s open", "Table supports source", 1, donor+("source_table",)),
            StageSpec("retreat", "donor", "Withdraw 0.10 m along approach axis and raise 0.16 m", "release completed", "TCP arrival sustained", "Donor no longer supports source; clearance >= configured value", 4, ("source_table",)),
            StageSpec("terminal", "evaluator", "Observe released source for the final window", "retreat control completed", "Fixed terminal window", "Every step: B position, upright, speed limits, table support, donor detached", c.terminal_window_s, ("source_table",)),
        ]
    elif task_id == "handover_transfer":
        rest = [
            StageSpec("receiver_approach", "receiver", "Motorized Y rail approaches known B handle region; jaws open", "donor transport control completed", "Receiver rail encoder reaches target", "No donor/receiver collision or nonfinger source contact", 2, donor),
            StageSpec("receiver_close", "receiver", "Close synchronized upper/lower fingers on handle", "receiver approach control completed", "Fixed 0.75 s force-limited close", "At least one receiver finger contacts handle; both required after donor release", 1, donor+("receiver_fingers_handle",)),
            StageSpec("donor_release", "donor", "Open donor jaws", "receiver close action completed, no truth-based gate", "Fixed 0.5 s open", "Receiver support maintained; donor release is intentional", 1, donor+("receiver_fingers_handle",)),
            StageSpec("retreat", "donor", "Withdraw 0.10 m along approach axis and raise 0.16 m", "donor release completed", "TCP arrival sustained", "Donor detached and clear", 4, ("receiver_fingers_handle",)),
            StageSpec("receiver_lift", "receiver", "Raise Z rail by configured distance", "donor retreat control completed", "Receiver rail encoder reaches target", "Source actually rises with receiver", 2, ("receiver_fingers_handle",)),
            StageSpec("terminal", "evaluator", "Observe recipient-only support", "receiver lift control completed", "Fixed terminal window", "Every step: recipient contact, donor detached, height, B pose, upright and speed limits", c.terminal_window_s, ("receiver_fingers_handle",)),
        ]
    else:
        rest = [
            StageSpec("tilt", "donor", "Rotate source about WORLD X at a fixed center above receiver", "transport control completed", "TCP arrival at final tilt", "Water moves through physical open rim; source stays held", 5, donor+("water_vessels",)),
            StageSpec("pour_dwell", "donor", "Maintain tilt for a fixed time, not until an oracle mass threshold", "tilt control completed", "Fixed pour_dwell_s", "No drop or source/receiver collision", c.pour_dwell_s, donor+("water_vessels",)),
            StageSpec("return_upright", "donor", "Restore upright at B safety pose", "pour dwell completed", "TCP arrival sustained", "Source remains held; receiver not displaced/tipped", 5, donor+("water_vessels",)),
            StageSpec("terminal", "evaluator", "Observe water and vessels in final window", "return control completed", "Fixed terminal window", f"Every step: transfer >= {c.transfer_min_ratio}, spill <= {c.spill_max_ratio}, stable upright source and receiver; valid fluid state", c.terminal_window_s, donor+("water_vessels",)),
        ]
    rest.insert(-1,StageSpec("settle","all_actuators","Keep final motor targets for a fixed settling interval",
                 "last physical action control completed","Fixed settle_s; no oracle stability gate",
                 "Continue collision/drop sampling; no terminal stability score yet",c.settle_s,
                 rest[-1].allowed_contacts))
    rest[-1] = replace(rest[-1],entry="settle control completed")
    stages = common+rest
    if task_id == "pour_water":
        stages = [replace(s,allowed_contacts=tuple(dict.fromkeys(s.allowed_contacts+
                  ("water_vessels","water_table_floor","target_cup_table")))) for s in stages]
    return dict(task_id=task_id, schema_version="task_spec_v1", controller_version=SEQUENCE_CONTROLLER,
                evaluation_version=SEQUENCE_EVALUATOR, config=asdict(c),
                point_A=[.55, 0, .645], point_B=[*c.target_xy, .645],
                handoff_pose=[*c.target_xy,c.carry_height_m] if task_id == "handover_transfer" else None,
                pouring_pose=[c.target_xy[0],c.target_xy[1]+math.copysign(.045,c.pour_angle_deg),c.carry_height_m] if task_id == "pour_water" else None,
                material_model="position_based_fluid" if task_id == "pour_water" else "empty_cup",
                stages=[asdict(s) for s in stages],
                policy="ID-only; same sequence/observation/limits for all selectors; no donor regrasp",
                note="Stage evaluation never controls transitions. Thresholds come from config, not labels.")
