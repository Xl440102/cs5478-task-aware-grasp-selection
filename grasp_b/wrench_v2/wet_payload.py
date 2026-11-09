"""Predicted rigid loads, never measured/settled liquid states or outcomes."""
import ast
from collections.abc import Mapping
from dataclasses import dataclass, field, asdict
import hashlib
import math
from numbers import Real
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from scipy.spatial.transform import Rotation

from grasp_a.sequence_contracts import SequenceConfig
from grasp_a.fluid import Vessel, PositionBasedFluid
from .contacts import _freeze_json, _plain
from .m1 import fingerprint
from .tasks import ObjectParameters, RigidPayload, combine_payload

LABEL = 'wet_rigid_uniform_cylinder_v1'
LATTICE_LABEL = 'wet_rigid_pbf_constructor_moments_v1'
ROOT = Path(__file__).resolve().parents[2]


def scalar(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)) or value < 0 or (positive and value == 0):
        raise ValueError(f'{name}: finite {"positive" if positive else "nonnegative"} SI scalar required')
    return float(value)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class WetPayloadSpec:
    model_name: str
    load_case_id: str
    water_mass_kg: float
    density_kg_m3: float
    radius_m: float
    bottom_z_m: float
    rim_z_m: float
    fill_height_m: float
    water_com_mug: tuple
    water_inertia_about_com_mug: tuple | None
    asset_hashes: Mapping
    config_hash: str
    parameter_sources: Mapping
    retention_assumption: str = 'constant mass rigidly entrained; no drainage'
    frame: str = 'mug'
    calibrated: bool = False
    assumptions: tuple = ('predicted rigid load; not PBF execution state',)
    limitations: tuple = ('no sloshing, redistribution, draining or fluid impulses; no validated tilt envelope',)
    model_version: str = field(default='wet_payload_schema_v1', init=False)
    model_hash: str = field(init=False)

    def __post_init__(self):
        if self.model_name not in (LABEL, LATTICE_LABEL) or self.frame != 'mug' or type(self.calibrated) is not bool:
            raise ValueError('unsupported model/frame/calibration flag')
        for name in ('load_case_id','retention_assumption'):
            if not isinstance(getattr(self,name),str) or not getattr(self,name).strip():
                raise ValueError(f'{name} required')
        for name in ('water_mass_kg','fill_height_m','density_kg_m3','radius_m'):
            object.__setattr__(self,name,scalar(getattr(self,name),name,positive=name in ('density_kg_m3','radius_m')))
        for name in ('bottom_z_m','rim_z_m'):
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value):raise ValueError('finite boundary coordinates required')
            object.__setattr__(self,name,float(value))
        if self.rim_z_m <= self.bottom_z_m or self.bottom_z_m+self.fill_height_m > self.rim_z_m+1e-12:
            raise ValueError('load does not fit declared vessel height')
        vessel=Vessel((0,0,0),(0,0,0,1))
        if self.radius_m>vessel.inner_radius or self.bottom_z_m<vessel.bottom or self.rim_z_m>vessel.rim:
            raise ValueError('declared load boundary exceeds A fluid surrogate')
        if not isinstance(self.water_com_mug,(tuple,list,np.ndarray)) or any(isinstance(v,bool) or not isinstance(v,Real) for v in self.water_com_mug):
            raise ValueError('COM requires real SI coordinates, no coercion')
        c=np.asarray(self.water_com_mug,dtype=float)
        if c.shape!=(3,) or not np.isfinite(c).all():raise ValueError('finite COM[3] in metres required')
        if np.linalg.norm(c[:2])>self.radius_m+1e-12 or not self.bottom_z_m-1e-12<=c[2]<=self.rim_z_m+1e-12:
            raise ValueError('COM outside declared vessel')
        object.__setattr__(self,'water_com_mug',tuple(map(float,c)))
        inertia=self.water_inertia_about_com_mug
        if inertia is not None:
            raw=np.asarray(inertia,dtype=object)
            if raw.shape!=(3,3) or any(isinstance(v,bool) or not isinstance(v,Real) for v in raw.flat):raise ValueError('inertia requires real SI 3x3 entries')
            a=np.asarray(inertia,dtype=float)
            if a.shape!=(3,3) or not np.isfinite(a).all() or not np.allclose(a,a.T,atol=1e-15,rtol=0) or np.linalg.eigvalsh(a).min() < -1e-15:
                raise ValueError('finite symmetric PSD COM inertia required')
            object.__setattr__(self,'water_inertia_about_com_mug',tuple(tuple(map(float,r)) for r in a))
        if self.model_name==LABEL:
            h=self.water_mass_kg/(self.density_kg_m3*math.pi*self.radius_m**2)
            expected=np.diag((self.water_mass_kg*(3*self.radius_m**2+h*h)/12,)*2+(self.water_mass_kg*self.radius_m**2/2,))
            if not math.isclose(h,self.fill_height_m,rel_tol=1e-12,abs_tol=1e-15) or not np.allclose(c,(0,0,self.bottom_z_m+h/2),atol=1e-14,rtol=0):
                raise ValueError('cylinder mass/volume/COM inconsistency')
            if inertia is not None and not np.allclose(inertia,expected,atol=1e-15,rtol=1e-12):
                raise ValueError('cylinder COM inertia formula mismatch')
        for name in ('asset_hashes','parameter_sources'):
            if not isinstance(getattr(self,name),Mapping) or not getattr(self,name):raise ValueError('nonempty provenance mappings required')
            object.__setattr__(self,name,_freeze_json(getattr(self,name)))
        for value in (self.config_hash,*self.asset_hashes.values()):
            if not isinstance(value,str) or len(value)!=64 or any(x not in '0123456789abcdef' for x in value):raise ValueError('SHA256 identities required')
        if self.model_name==LATTICE_LABEL:
            raw=self.parameter_sources.get('sequence_config')
            if not isinstance(raw,Mapping) or fingerprint(raw)!=self.config_hash:raise ValueError('constructor moments require bound full SequenceConfig')
            seq=SequenceConfig(**_plain(raw))
            if self.water_mass_kg!=seq.water_mass_kg or self.radius_m!=vessel.inner_radius or self.bottom_z_m!=vessel.bottom or self.rim_z_m!=vessel.rim:raise ValueError('constructor boundary/config mismatch')
            fluid=PositionBasedFluid(vessel,seq.fluid_particles,seq.water_mass_kg,seq.fluid_iterations)
            positions=fluid.positions;center=positions.mean(axis=0);delta=positions-center
            expected=fluid.mass*(np.eye(3)*np.sum(delta*delta)-delta.T@delta)
            if self.density_kg_m3!=fluid.rest_density or not np.allclose(c,center,rtol=0,atol=1e-14) or not math.isclose(self.fill_height_m,float(positions[:,2].max()-vessel.bottom+fluid.boundary_radius),abs_tol=1e-14,rel_tol=0):raise ValueError('constructor moment geometry mismatch')
            if inertia is not None and not np.allclose(inertia,expected,rtol=1e-12,atol=1e-15):raise ValueError('constructor inertia mismatch')
        for name in ('assumptions','limitations'):
            if not isinstance(getattr(self,name),(tuple,list)):raise ValueError('assumptions/limitations must be sequences')
            values=tuple(getattr(self,name))
            if not values or any(not isinstance(v,str) or not v.strip() for v in values):raise ValueError('explicit assumptions and limitations required')
            object.__setattr__(self,name,values)
        object.__setattr__(self,'model_hash',fingerprint({k:_plain(getattr(self,k)) for k in self.__dataclass_fields__ if k!='model_hash'}))

    @property
    def status(self):
        return 'missing_inertia' if self.water_mass_kg>0 and self.water_inertia_about_com_mug is None else 'complete_approximate'

    def to_dict(self):
        return dict(_plain(self),status=self.status,units='kg,m,kg/m^3,kg*m^2')

    @classmethod
    def from_dict(cls,data):
        expected=set(cls.__dataclass_fields__)|{'status','units'}
        if set(data)!=expected or data['model_version']!='wet_payload_schema_v1' or data['units']!='kg,m,kg/m^3,kg*m^2':raise ValueError('invalid wet payload schema/units')
        value=cls(**{k:v for k,v in data.items() if k not in ('model_version','model_hash','status','units')})
        if data['model_hash']!=value.model_hash or data['status']!=value.status:raise ValueError('wet payload hash/status mismatch')
        return value


def build_wet_payload(sequence_config=None, *, radius_m=None, water_mass_kg=None, root=ROOT, model_name=LABEL):
    seq=SequenceConfig() if sequence_config is None else sequence_config
    if not isinstance(seq,SequenceConfig):raise ValueError('SequenceConfig required')
    root=Path(root); v=Vessel((0,0,0),(0,0,0,1))
    # Read the declared numerical density, rather than duplicating an unbound constant.
    tree=ast.parse((root/'grasp_a/fluid.py').read_text())
    values=[n.value.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Attribute) and t.attr=='rest_density' for t in n.targets) and isinstance(n.value,ast.Constant)]
    if len(values)!=1:raise ValueError('fluid density declaration changed; review required')
    density=scalar(values[0],'A rest density',True)
    radius=scalar(v.inner_radius if radius_m is None else radius_m,'radius',True)
    if radius>v.inner_radius:raise ValueError('radius exceeds declared A fluid boundary')
    mass=scalar(seq.water_mass_kg if water_mass_kg is None else water_mass_kg,'water mass')
    h=mass/(density*math.pi*radius**2)
    com=(0,0,v.bottom+h/2)
    inertia=np.diag((mass*(3*radius**2+h*h)/12,)*2+(mass*radius**2/2,))
    if model_name==LATTICE_LABEL:
        if radius_m is not None or water_mass_kg is not None or mass<=0:raise ValueError('constructor sensitivity uses exact A config/boundary')
        f=PositionBasedFluid(v,seq.fluid_particles,mass,seq.fluid_iterations)  # constructor ONLY, zero steps
        points=f.positions;com=points.mean(axis=0);d=points-com
        inertia=f.mass*(np.eye(3)*np.sum(d*d)-d.T@d)
        h=float(points[:,2].max()-v.bottom+f.boundary_radius)
    return WetPayloadSpec(model_name=model_name,load_case_id='development_uncalibrated',
        water_mass_kg=mass,density_kg_m3=density,radius_m=radius,bottom_z_m=v.bottom,rim_z_m=v.rim,
        fill_height_m=h,water_com_mug=tuple(com),water_inertia_about_com_mug=inertia,
        asset_hashes={name:sha(root/name) for name in ('assets/mug_hollow.urdf','grasp_a/fluid.py','grasp_a/sequence_contracts.py')},
        config_hash=fingerprint(asdict(seq)),parameter_sources=dict(mass='SequenceConfig.water_mass_kg' if water_mass_kg is None else 'explicit development override; not calibrated',
            density='A PositionBasedFluid.rest_density numerical declaration',radius='A Vessel.inner_radius' if radius_m is None else 'explicit geometry sensitivity override',
            boundary='A Vessel.bottom/rim; fluid surrogate differs from collision facets',
            moments='analytic uniform cylinder' if model_name==LABEL else 'A constructor moments at steps=0, NOT 120-step settled liquid',sequence_config=asdict(seq)),
        assumptions=('constant mass and body-fixed predicted moments','zero mass is mathematical dry limit, not A dry Pour mode'),
        limitations=('no calibrated angle envelope, free surface redistribution, sloshing or draining',
                     'fluid surrogate, collision facets and PBF particle distribution are distinct',
                     'constructor sample moments are not settled state or observed execution'))


def verify_dry_object(body, spec, *, root=ROOT):
    """Fail closed on proxy mass, prior payload, stale assets or edited inertia."""
    if not isinstance(body,ObjectParameters) or not isinstance(spec,WetPayloadSpec):raise ValueError('typed dry body and wet spec required')
    for name,digest in spec.asset_hashes.items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or sha(Path(root)/path)!=digest:raise ValueError('stale/invalid wet asset binding')
    required={'assets/mug_hollow.urdf','grasp_a/fluid.py','grasp_a/sequence_contracts.py'}
    if not required<=set(spec.asset_hashes):raise ValueError('missing audited asset identities')
    prov=body.provenance
    if body.source!='runtime_loaded_initial_asset_dynamics_not_measured_trajectory' or 'payload' in prov or 'dry_object' in prov:
        raise ValueError('unverified dry runtime source or already-combined payload')
    entry=prov.get('snapshot_entry',{}); d=entry.get('dynamics',{})
    digest=spec.asset_hashes['assets/mug_hollow.urdf']
    declared=float(ET.parse(Path(root)/'assets/mug_hollow.urdf').getroot().find('link/inertial/mass').attrib['value'])
    if prov.get('asset_sha256')!=digest or entry.get('asset_sha256')!=digest or entry.get('flags')!=0 or entry.get('fixed_base') is not False or body.mass_kg!=declared or d.get('mass_kg')!=declared:
        raise ValueError('dry mass/asset/loader mismatch (proxy is not dry)')
    r=Rotation.from_quat(d['local_inertial_quaternion_xyzw']).as_matrix()
    inertia=r@np.diag(d['local_inertia_diagonal_kg_m2'])@r.T
    if body.inertia_com_kg_m2 is None or not np.array_equal(body.com_m,d['local_inertial_position_m']) or not np.allclose(body.inertia_com_kg_m2,inertia,rtol=0,atol=1e-15):
        raise ValueError('dry runtime COM/inertia mismatch; no URDF substitution')
    return fingerprint(body)


def combine_wet_object(body,spec,*,root=ROOT):
    dry_hash=verify_dry_object(body,spec,root=root)
    wet=combine_payload(body,RigidPayload(mass_kg=spec.water_mass_kg,com_m=spec.water_com_mug,
        inertia_com_kg_m2=spec.water_inertia_about_com_mug,source='predicted '+spec.model_name+' '+spec.model_hash))
    wet=ObjectParameters(mass_kg=wet.mass_kg,com_m=wet.com_m,inertia_com_kg_m2=wet.inertia_com_kg_m2,
        source=wet.source,provenance=dict(_plain(wet.provenance),wet_payload=spec.to_dict(),dry_object_hash=dry_hash))
    return wet,dict(model_label=spec.model_name,dry_object_hash=dry_hash,wet_payload_hash=spec.model_hash,
                    combined_object_hash=fingerprint(wet),wet_payload=spec.to_dict())


def validate_wet_object(wet,spec,*,root=ROOT):
    if not isinstance(wet,ObjectParameters) or not isinstance(spec,WetPayloadSpec):raise ValueError('wet object/spec required')
    raw=wet.provenance.get('dry_object')
    if not isinstance(raw,Mapping):raise ValueError('wet object lacks explicit dry binding')
    dry=ObjectParameters(**_plain(raw))
    # Recompute a validation witness, never a separate M1/M2 scoring model.
    expected,binding=combine_wet_object(dry,spec,root=root)
    if fingerprint(expected)!=fingerprint(wet):raise ValueError('combined wet object identity mismatch')
    return binding
