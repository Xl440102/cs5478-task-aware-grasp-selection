"""Offline A command mirror. Samples are estimates, NEVER measured trajectories.

Half-cosine rest-to-rest interpolation estimates acceleration from command
endpoints/durations. A actually interpolates joints with motor/arrival dynamics.
The mirror is source-hash guarded so later A changes cannot silently reuse it.
"""
from dataclasses import dataclass, asdict
import hashlib
import math
from pathlib import Path
import numpy as np
from grasp_a.sequence_contracts import SequenceConfig, task_spec
from .models import rotation

ROOT = Path(__file__).resolve().parents[1]
# Recorded source identity when this command mirror was reviewed.
SOURCE_FILES = ('grasp_a/sequence.py', 'grasp_a/simulation.py', 'grasp_a/sequence_contracts.py')


@dataclass(frozen=True)
class MotionSample:
    stage: str
    time_s: float
    rotation_rpy_rad: tuple = (0.,0.,0.)
    acceleration_world_m_s2: tuple = (0.,0.,0.)
    omega_world_rad_s: tuple = (0.,0.,0.)
    alpha_world_rad_s2: tuple = (0.,0.,0.)
    speed_m_s: float = 0.
    source: str = 'command_endpoint_half_cosine_estimate'

    def __post_init__(self):
        if not self.stage or self.source not in ('command_endpoint_half_cosine_estimate', 'external_predicted', 'external_observed'):
            raise ValueError('explicit sample stage/source required')
        for v in (self.rotation_rpy_rad,self.acceleration_world_m_s2,self.omega_world_rad_s,self.alpha_world_rad_s2):
            if len(v) != 3 or not all(type(x) in (int,float) and math.isfinite(x) for x in v):
                raise ValueError('finite sample vectors required')
        if not math.isfinite(self.time_s) or self.time_s < 0 or not math.isfinite(self.speed_m_s) or self.speed_m_s < 0:
            raise ValueError('invalid sample time/speed')


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    samples: tuple
    liquid_mass_kg: float = 0.
    liquid_com_m: tuple = (0.,0.,0.)
    source_hashes: tuple = ()
    provenance: str = 'external_explicit_samples'
    stage_commands: tuple = ()

    def __post_init__(self):
        if self.task_id not in ('transport_place','handover_transfer','pour_water','pour_prep','handover_prep'):
            raise ValueError('unsupported A task')
        if not self.samples or any(not isinstance(s,MotionSample) for s in self.samples):
            raise ValueError('nonempty explicit motion samples required')
        if not math.isfinite(self.liquid_mass_kg) or self.liquid_mass_kg < 0:
            raise ValueError('nonnegative liquid mass required')
        if len(self.liquid_com_m) != 3 or not all(math.isfinite(x) for x in self.liquid_com_m):
            raise ValueError('finite liquid COM required')
        if any(b.time_s < a.time_s for a,b in zip(self.samples,self.samples[1:])):
            raise ValueError('samples must be time ordered')


def command_context(task_id, *, config=None, speed_scale=1., initial_position=(.55,0,.645), samples_per_stage=17):
    """Build nominal cup motion while donor holds it; no post-release load.

    speed_scale is a sensitivity scenario, not a change to A's executor.
    Absolute times exclude variable endpoint refinement/arrival waits.
    """
    if not math.isfinite(speed_scale) or speed_scale <= 0 or type(samples_per_stage) is not int or samples_per_stage < 3:
        raise ValueError('positive speed scale and >=3 samples required')
    if len(initial_position) != 3 or not all(math.isfinite(x) for x in initial_position):
        raise ValueError('finite initial position required')
    c = config or SequenceConfig()
    hashes = tuple((p,hashlib.sha256((ROOT/p).read_bytes()).hexdigest()) for p in SOURCE_FILES)
    expected = MIRROR_SOURCE_HASHES
    if dict(hashes) != expected:
        raise RuntimeError('A source changed: review command mirror before generating estimates')
    initial = np.array(initial_position,dtype=float)
    lifted = initial+np.array((0,0,.14))
    commands = []
    zero = np.zeros(3)
    if task_id in ('transport_place','handover_transfer','pour_water'):
        # task_spec validates ID/config; public thresholds are not velocities.
        task_spec(task_id,c)
        carry = np.array((*c.target_xy,c.carry_height_m),dtype=float)
        if task_id == 'pour_water':
            carry[1] += math.copysign(.045,c.pour_angle_deg)
        commands = [('lift',lifted,zero,1.),('transport',carry,zero,1.3)]
        if task_id == 'transport_place':
            commands += [('lower',np.array((*c.target_xy,.642)),zero,1.3),('release',np.array((*c.target_xy,.642)),zero,.5)]
        elif task_id == 'handover_transfer':
            commands += [('receiver_approach',carry,zero,2.),('receiver_close',carry,zero,.75),('donor_release',carry,zero,.5)]
        else:
            angle = np.array((math.radians(c.pour_angle_deg),0,0))
            commands += [('tilt',carry,angle,2.),('pour_dwell',carry,angle,c.pour_dwell_s),('return_upright',carry,zero,2.)]
    elif task_id in ('pour_prep','handover_prep'):
        target = np.array((.55,0,.785))
        angle = np.array((0,math.radians(35),0)) if task_id == 'pour_prep' else np.array((0,0,-math.pi/2))
        duration = 200/240
        commands = [('lift',lifted,zero,duration)]
        if task_id == 'pour_prep':
            commands += [('task_clearance',initial+np.array((-.10,0,.18)),zero,duration),
                         ('task_rotate',target+np.array((-.10,0,.04)),angle,duration)]
        commands += [('task_motion',target,angle,duration),('hold',target,angle,2.)]
    else:
        raise ValueError('unsupported A task')
    samples, audit = [], []
    position, rpy, elapsed = initial, zero, 0.
    for stage,end,end_rpy,nominal_duration in commands:
        duration = nominal_duration/speed_scale
        delta, angular = end-position,end_rpy-rpy
        audit.append(dict(stage=stage,duration_s=duration,nominal_duration_s=nominal_duration,
                          displacement_m=tuple(delta),rotation_delta_rad=tuple(angular)))
        for t in np.linspace(0,1,samples_per_stage):
            f = (1-math.cos(math.pi*t))/2
            df = math.pi*math.sin(math.pi*t)/(2*duration)
            ddf = math.pi**2*math.cos(math.pi*t)/(2*duration**2)
            samples.append(MotionSample(stage,elapsed+t*duration,tuple(map(float,rpy+f*angular)),tuple(map(float,ddf*delta)),
                                        tuple(map(float,df*angular)),tuple(map(float,ddf*angular)),float(np.linalg.norm(df*delta))))
        elapsed += duration
        position,rpy = end,end_rpy
    return TaskContext(task_id,tuple(samples), c.water_mass_kg if task_id == 'pour_water' else 0.,
                       (0.,0.,0.),hashes,'A endpoints/durations + assumed half-cosine; static liquid at origin',tuple(audit))


# Re-reviewed for SceneRandomization: only the initial cup pose/estimate source
# changed; commanded stage endpoints and durations are unchanged.
MIRROR_SOURCE_HASHES = {'grasp_a/sequence.py': '8314d5cd21d482eb6e86ce5b9935fcbad386a618fad34305014d8989b8e8f2ed', 'grasp_a/simulation.py': '65292f5e38f96e2f3d7e123e2611be81c4f4f5970ee3883e643b7c59d106dddf', 'grasp_a/sequence_contracts.py': 'e1dd49fa119f2e9e16933145e42699ddb4f034d1411dc83124e0ed7296a128e0'}
