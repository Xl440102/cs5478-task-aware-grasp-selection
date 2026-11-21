"""Independent DIRECT static collision audit; no stepping or motor commands.

Baseline assets/flags match A. Mug placement realizes T_WO=T_WT inv(T_OT)
without IK. Diagnostic single-hull/box proxies are explicitly separate from A.
Closest points are per-pair distance witnesses, NOT a full contact manifold.
"""
from dataclasses import asdict,dataclass
import hashlib
import importlib.metadata
import json
from pathlib import Path
from time import perf_counter
import xml.etree.ElementTree as ET

import numpy as np
from scipy.spatial.transform import Rotation
import pybullet as p
import pybullet_data

from grasp_a.contracts import candidate_sha256
from grasp_a.simulation import ARM_HOME,STEP,TABLE_TOP
from grasp_b.models import rotation
from grasp_b.wrench_v2.geometry import quaternion_matrix,read_gripper_geometry
from grasp_b.wrench_v2.contacts import _freeze_json,_plain
from .contact_regions import digest

ROOT=Path(__file__).resolve().parents[2]
VERSION='bullet_static_alignment_v1'


def transform(position,quaternion):
    matrix=np.eye(4);matrix[:3,:3]=quaternion_matrix(quaternion);matrix[:3,3]=position
    if not np.isfinite(matrix).all():raise ValueError('finite SI pose required')
    return matrix


def pose(matrix):
    return matrix[:3,3].tolist(),Rotation.from_matrix(matrix[:3,:3]).as_quat().tolist()


def _native(value):
    if isinstance(value,bytes):return value.decode('utf-8',errors='replace')
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,(list,tuple)):return [_native(v) for v in value]
    if isinstance(value,dict):return {k:_native(v) for k,v in value.items()}
    return value


@dataclass(frozen=True,kw_only=True)
class StaticQueryConfig:
    maximum_distance_m:float=.2
    gap_tolerance_m:float=2e-6
    displacement_resolution_m:float=1e-7
    frame_tolerance_m:float=1e-8
    rotation_tolerance:float=1e-8
    contact_threshold_m:float=0.
    point_probe_radius_m:float=1e-6
    scan_intervals:int=16

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            v=getattr(self,name)
            if isinstance(v,bool) or not np.isfinite(v):raise ValueError('finite explicit query parameters required')
            if name not in ('contact_threshold_m','scan_intervals') and v<=0:raise ValueError('positive query lengths required')
        if type(self.scan_intervals) is not int or self.scan_intervals<2:raise ValueError('scan intervals >=2')


@dataclass(frozen=True)
class BulletAlignmentEvidence:
    payload:object

    def __post_init__(self):
        d=json.loads(json.dumps(_plain(self.payload),allow_nan=False))
        if d.get('version')!=VERSION or d.get('frame')!='mug' or d.get('length_unit')!='m':raise ValueError('version/frame/SI mismatch')
        if d['classification'] not in ('geometry_aligned','surface_mismatch','frame_mismatch','unknown'):
            raise ValueError('unknown classification')
        for key in ('candidate_hash','contact_spec_hash','input_hash'):
            if len(d[key])!=64 or any(c not in '0123456789abcdef' for c in d[key]):raise ValueError('invalid identity')
        StaticQueryConfig(**d['query_config'])
        old=d.pop('record_hash',None);h=digest(d)
        if old is not None and old!=h:raise ValueError('collision evidence checksum mismatch')
        d['record_hash']=h;object.__setattr__(self,'payload',_freeze_json(d))

    def to_dict(self):return _plain(self.payload)
    def to_json(self):return json.dumps(self.to_dict(),sort_keys=True,separators=(',',':'),allow_nan=False)

    @classmethod
    def from_json(cls,text):
        def unique(pairs):
            d={}
            for k,v in pairs:
                if k in d:raise ValueError('duplicate key')
                d[k]=v
            return d
        d=json.loads(text,object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('nonfinite')))
        if 'record_hash' not in d:raise ValueError('checksum required')
        return cls(d)

    def assert_identity(self,**expected):
        if not expected or set(expected)-{'candidate_id','candidate_hash','contact_spec_hash','input_hash'}:
            raise ValueError('explicit supported identity required')
        if any(self.payload[k]!=v for k,v in expected.items()):raise ValueError('collision evidence identity mismatch')


class StaticBulletAlignment:
    """Owned client, explicitly closed even on errors. No external client reset."""
    def __init__(self,*,config=None,expected_assets=None,base_pose=None):
        self.config=config or StaticQueryConfig();self.client=None;self.query_count=0;self.cache_hits=0
        self.panda=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'
        self.mug_path=ROOT/'assets/mug_hollow.urdf';self.gripper=read_gripper_geometry(self.panda)
        self.paths=dict(panda_urdf=self.panda,mug_urdf=self.mug_path,
            finger_obj=self.panda.parent/'meshes/collision/finger.obj',palm_obj=self.panda.parent/'meshes/collision/hand.obj')
        self.assets={k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in self.paths.items()}
        if expected_assets and any(k not in self.assets or self.assets[k]!=h for k,h in expected_assets.items()):
            raise ValueError('asset hash mismatch before client initialization')
        self.base_pose=base_pose or ((0.,0.,TABLE_TOP),(0.,0.,0.,1.))
        self.robot_flags=p.URDF_USE_SELF_COLLISION|p.URDF_USE_SELF_COLLISION_EXCLUDE_PARENT
        self.mug_flags=0;self._distance_cache={};self.proxies={};self._probes={}

    def __enter__(self):
        if self.client is not None:raise RuntimeError('client already open')
        self.client=p.connect(p.DIRECT)
        if self.client<0:self.client=None;raise RuntimeError('DIRECT unavailable')
        try:
            p.setTimeStep(STEP,physicsClientId=self.client)
            p.setGravity(0,0,-9.81,physicsClientId=self.client)
            p.setPhysicsEngineParameter(numSolverIterations=150,deterministicOverlappingPairs=1,physicsClientId=self.client)
            self.robot=p.loadURDF(str(self.panda),*self.base_pose,useFixedBase=True,flags=self.robot_flags,physicsClientId=self.client)
            self.mug=p.loadURDF(str(self.mug_path),flags=self.mug_flags,physicsClientId=self.client)
            info=[p.getJointInfo(self.robot,j,physicsClientId=self.client) for j in range(p.getNumJoints(self.robot,physicsClientId=self.client))]
            self.links={v[12].decode():i for i,v in enumerate(info)};self.joints={v[1].decode():i for i,v in enumerate(info)}
            for i,q in enumerate(ARM_HOME,1):p.resetJointState(self.robot,self.joints[f'panda_joint{i}'],q,physicsClientId=self.client)
            self.fingers={name:self.links['panda_'+name+'finger'] for name in ('left','right')}
            for j in self.fingers.values():
                p.resetJointState(self.robot,j,.04,physicsClientId=self.client)
                p.changeDynamics(self.robot,j,lateralFriction=2.,spinningFriction=.05,physicsClientId=self.client)
            p.changeDynamics(self.mug,-1,lateralFriction=1.2,physicsClientId=self.client)
            self.tcp=self.link_pose(self.links['panda_grasptarget'])
            self.mug_inertial=p.getDynamicsInfo(self.mug,-1,physicsClientId=self.client)
            self.metadata=self._metadata()
            return self
        except BaseException:
            self.close();raise

    def close(self):
        if self.client is not None:
            p.disconnect(physicsClientId=self.client);self.client=None
    def __exit__(self,*args):self.close()

    def _check(self):
        if self.client is None:raise RuntimeError('DIRECT client is closed')
        if any(hashlib.sha256(v.read_bytes()).hexdigest()!=self.assets[k] for k,v in self.paths.items()):raise ValueError('asset changed during audit')

    def link_pose(self,link):
        st=p.getLinkState(self.robot,link,computeForwardKinematics=True,physicsClientId=self.client)
        # COM and local inertial pose use double precision. [4:6] link pose can
        # be float32-rounded; measure that separately, never silently mix them.
        return transform(st[0],st[1])@np.linalg.inv(transform(st[2],st[3]))

    def _shapes(self,body,link):
        rows=p.getCollisionShapeData(body,link,physicsClientId=self.client)
        # Compound API child body/link fields can contain non-semantic values;
        # never use them for identity or pretend they expose URDF primitive IDs.
        return [dict(api_order=i,geometry_type=int(x[2]),dimensions=list(x[3]),filename=_native(x[4]),
                     local_inertial_frame_position=list(x[5]),local_inertial_frame_quaternion=list(x[6])) for i,x in enumerate(rows)]

    def _metadata(self):
        root=ET.parse(self.panda).getroot();fingers={}
        for name,j in self.fingers.items():
            f=self.gripper.finger(name);dyn=p.getDynamicsInfo(self.robot,j,physicsClientId=self.client)
            sim=np.array(p.getMeshData(self.robot,j,flags=p.MESH_DATA_SIMULATION_MESH,physicsClientId=self.client)[1])
            shapes=self._shapes(self.robot,j);ic=transform(shapes[0]['local_inertial_frame_position'],shapes[0]['local_inertial_frame_quaternion'])
            raw=np.unique(np.array(f.triangles).reshape(-1,3),axis=0);raw_i=raw@ic[:3,:3].T+ic[:3,3]
            nearest=np.linalg.norm(sim[:,None,:]-raw_i[None,:,:],axis=2).min(axis=1) if len(sim) else np.array([])
            collision=root.find(f"link[@name='panda_{name}finger']/collision")
            visual=root.find(f"link[@name='panda_{name}finger']/visual/geometry/mesh")
            origin=collision.find('origin');attributes={} if origin is None else origin.attrib
            li=transform(dyn[3],dyn[4]);expected_ic=np.linalg.inv(li)@transform(
                [float(x) for x in attributes.get('xyz','0 0 0').split()],
                Rotation.from_euler('xyz',[float(x) for x in attributes.get('rpy','0 0 0').split()]).as_quat())
            fingers[name]=dict(link_index=j,dynamics=_native(dyn),collision=shapes,
                visual_mesh_uri=None if visual is None else visual.get('filename'),
                urdf_mesh_scale=[float(x) for x in collision.find('geometry/mesh').get('scale','1 1 1').split()],
                simulation_mesh_vertices_inertial=sim.tolist(),raw_unique_vertices=len(raw),raw_triangles=len(f.triangles),
                nearest_vertex_difference_m=None if not len(nearest) else dict(min=float(nearest.min()),max=float(nearest.max())),
                collision_frame_residual=float(np.max(np.abs(ic-expected_ic))),
                mesh_frame='local inertial, verified against URDF transforms; connectivity not exposed')
        return dict(fingers=fingers,mug=dict(dynamics=_native(self.mug_inertial),collision=self._shapes(self.mug,-1)),
            palm=dict(dynamics=_native(p.getDynamicsInfo(self.robot,self.links['panda_hand'],physicsClientId=self.client)),
                      collision=self._shapes(self.robot,self.links['panda_hand'])),
            visual_shapes=_native(p.getVisualShapeData(self.robot,physicsClientId=self.client)),
            shape_identity_limit='closestPoints exposes links, not compound child/face IDs or complete manifold')

    def place(self,candidate):
        self._check();ot=transform(candidate.grasp_pose.position,candidate.grasp_pose.quaternion)
        self.world_object=self.tcp@np.linalg.inv(ot)
        world_inertial=self.world_object@transform(self.mug_inertial[3],self.mug_inertial[4])
        p.resetBasePositionAndOrientation(self.mug,*pose(world_inertial),physicsClientId=self.client)
        self._distance_cache={}

    def _closest(self,bodyA,bodyB,*,link=-1):
        self.query_count+=1
        points=p.getClosestPoints(bodyA,bodyB,self.config.maximum_distance_m,linkIndexA=link,physicsClientId=self.client)
        result=[dict(distance_m=float(x[8]),position_on_A_world_m=list(x[5]),position_on_B_world_m=list(x[6]),
                     normal_on_B_world=list(x[7]),link_A=int(x[3]),link_B=int(x[4])) for x in points]
        if any(not np.isfinite(x['distance_m']) for x in result):raise RuntimeError('nonfinite static query')
        return result

    def at_joint(self,name,q):
        key=(name,float(q))
        p.resetJointState(self.robot,self.fingers[name],float(q),physicsClientId=self.client)
        if key in self._distance_cache:self.cache_hits+=1;return self._distance_cache[key]
        points=self._closest(self.robot,self.mug,link=self.fingers[name])
        result=dict(q_m=float(q),minimum_distance_m=min((x['distance_m'] for x in points),default=None),points=points)
        self._distance_cache[key]=result;return result

    def transition(self,name,q_open,q_target):
        previous=None;samples=[];bracket=None
        for q in np.linspace(q_open,q_target,self.config.scan_intervals+1):
            row=self.at_joint(name,float(q));samples.append(row)
            if row['minimum_distance_m'] is None:return dict(status='range_censored',samples=samples)
            if row['minimum_distance_m']<=self.config.contact_threshold_m:
                if previous is None:return dict(status='opening_overlap_or_contact',displacement_interval_m=[0.,0.],samples=samples)
                bracket=(previous['q_m'],row['q_m']);break
            previous=row
        if bracket is None:return dict(status='no_detected_transition',samples=samples,
            continuous_clearance_certified=False)
        high,low=bracket
        while high-low>self.config.displacement_resolution_m:
            mid=(high+low)/2;row=self.at_joint(name,mid)
            if row['minimum_distance_m'] is None:return dict(status='range_censored',samples=samples)
            if row['minimum_distance_m']<=self.config.contact_threshold_m:low=mid
            else:high=mid
        return dict(status='first_detected_transition',displacement_interval_m=[q_open-high,q_open-low],
            q_interval_m=[low,high],continuous_first_contact_certified=False,
            limitation='finite scan can miss earlier transient contacts; bisection refines detected bracket only',samples=samples)

    def point_gap(self,point_mug,*,name=None,radius=None):
        radius=self.config.point_probe_radius_m if radius is None else radius
        if radius<=0 or not np.isfinite(radius):raise ValueError('finite positive probe radius')
        if radius not in self._probes:
            shape=p.createCollisionShape(p.GEOM_SPHERE,radius=radius,physicsClientId=self.client)
            self._probes[radius]=p.createMultiBody(baseMass=0,baseCollisionShapeIndex=shape,physicsClientId=self.client)
        probe=self._probes[radius];world=self.world_object[:3,:3]@point_mug+self.world_object[:3,3]
        p.resetBasePositionAndOrientation(probe,world,[0,0,0,1],physicsClientId=self.client)
        points=self._closest(self.robot if name else self.mug,probe,link=self.fingers[name] if name else -1)
        return dict(probe_radius_m=radius,point_mug_m=list(point_mug),
            inferred_point_gap_m=None if not points else min(x['distance_m'] for x in points)+radius,
            points=points,limitation='distance+sphere radius; GJK/EPA approximation, compound min is not a union SDF')

    def proxy_comparison(self,name,primitive,q):
        """Explicit stand-alone hull/box experiments, never change A bodies."""
        self.at_joint(name,q);st=p.getLinkState(self.robot,self.fingers[name],computeForwardKinematics=True,physicsClientId=self.client)
        verts=self.metadata['fingers'][name]['simulation_mesh_vertices_inertial']
        shape=p.createCollisionShape(p.GEOM_MESH,vertices=verts,physicsClientId=self.client)
        hull=p.createMultiBody(baseMass=0,baseCollisionShapeIndex=shape,basePosition=st[0],baseOrientation=st[1],physicsClientId=self.client)
        box=None;rows=[]
        try:
            if primitive.shape.kind!='box':return dict(status='unsupported isolated primitive')
            bshape=p.createCollisionShape(p.GEOM_BOX,halfExtents=(np.array(primitive.shape.dimensions)/2).tolist(),physicsClientId=self.client)
            op=np.eye(4);op[:3,:3]=rotation(primitive.shape.rpy);op[:3,3]=primitive.shape.position
            box=p.createMultiBody(baseMass=0,baseCollisionShapeIndex=bshape,basePosition=pose(self.world_object@op)[0],
                                  baseOrientation=pose(self.world_object@op)[1],physicsClientId=self.client)
            baseline=p.getDynamicsInfo(self.robot,self.fingers[name],physicsClientId=self.client)[11]
            for hm in (baseline,0.,.0005):
                p.changeDynamics(hull,-1,collisionMargin=hm,physicsClientId=self.client)
                actual=p.getDynamicsInfo(hull,-1,physicsClientId=self.client)[11]
                full=self._closest(hull,self.mug)
                for bm in (0.,.001):
                    p.changeDynamics(box,-1,collisionMargin=bm,physicsClientId=self.client)
                    box_actual=p.getDynamicsInfo(box,-1,physicsClientId=self.client)[11]
                    isolated=self._closest(hull,box)
                    rows.append(dict(requested_hull_margin_m=hm,reported_hull_margin_m=actual,
                        requested_box_margin_m=bm,reported_box_margin_m=box_actual,
                        intervention_readback_matches=abs(hm-actual)<1e-12 and abs(bm-box_actual)<1e-12,
                        hull_vs_original_mug_min_m=min((x['distance_m'] for x in full),default=None),
                        hull_vs_isolated_box_min_m=min((x['distance_m'] for x in isolated),default=None)))
            return dict(status='diagnostic_proxies_only',rows=rows,primitive_id=primitive.primitive_id,
                baseline_reported_finger_margin_m=baseline,full_query=self.at_joint(name,q))
        finally:
            if box is not None:p.removeBody(box,physicsClientId=self.client)
            p.removeBody(hull,physicsClientId=self.client)

    def audit(self,candidate,spec,geometry_evidence,geometry,*,detailed=False):
        began=perf_counter();before=self.query_count;hits=self.cache_hits
        spec.assert_candidate_identity(candidate.id,candidate_sha256([candidate]))
        if geometry_evidence['candidate_hash']!=spec.candidate_hash:raise ValueError('C1 candidate mismatch')
        if geometry_evidence['contact_spec_hash']!=digest(spec.to_dict()):raise ValueError('C1 ContactSpec mismatch')
        if geometry.asset_sha256!=self.assets['mug_urdf'] or self.gripper.panda_sha256!=self.assets['panda_urdf']:
            raise ValueError('model/assets mismatch')
        for key,actual in [('object_asset_sha256',self.assets['mug_urdf']),('panda_sha256',self.assets['panda_urdf']),('palm_mesh_sha256',self.assets['palm_obj'])]:
            if key in spec.provenance and spec.provenance[key]!=actual:raise ValueError('ContactSpec asset mismatch: '+key)
        self.place(candidate);fingers=[];contacts=[];frame_errors=[]
        for g in geometry_evidence['finger_groups']:
            name=g['finger_id'];q_open=candidate.gripper_width/2;q=g['first_joint_position_m']
            transition=self.transition(name,q_open,max(0.,min(q_open,q)))
            target=self.at_joint(name,q);link=self.link_pose(self.fingers[name]);relative=np.linalg.inv(self.tcp)@link
            expected=np.eye(4);expected[:3,3]=np.asarray(self.gripper.finger(name).joint_origin_tool_m)+[0,(1 if name=='left' else -1)*q,0]
            frame_error=float(np.max(np.abs(relative-expected)));frame_errors.append(frame_error)
            st=p.getLinkState(self.robot,self.fingers[name],computeForwardKinematics=True,physicsClientId=self.client)
            rounded_error=float(np.max(np.abs(link-transform(st[4],st[5]))))
            row=dict(finger_id=name,predicted_q_m=q,predicted_displacement_m=g.get('first_contact_displacement_m'),
                transition=transition,at_predicted=target,relative_link_to_tcp=relative.tolist(),frame_error=frame_error,
                frame_translation_error_m=float(np.max(np.abs(relative[:3,3]-expected[:3,3]))),
                frame_rotation_matrix_error=float(np.max(np.abs(relative[:3,:3]-expected[:3,:3]))),
                direct_link_pose_rounding_difference=rounded_error,loaded_multicontact_simultaneity='unknown: closest points are not a full manifold')
            raw=g['mesh_sweep'].get('definite_first')
            if raw and g.get('first_contact_displacement_m') is not None:
                row['at_C1_raw_intersection']=self.at_joint(name,q_open-raw['displacement_m'])
                if detailed and 'witness' in raw:
                    point=np.array(raw['witness']['point_mug_m'])
                    row['raw_witness_loaded_gaps']=dict(finger=self.point_gap(point,name=name),mug=self.point_gap(point))
                    row['proxy_interventions']=self.proxy_comparison(name,geometry.primitive(raw['primitive_id']),q)
            fingers.append(row)
        for i,contact in enumerate(spec.contacts):
            name=contact.finger_id.value;g=next(g for g in geometry_evidence['finger_groups'] if g['finger_id']==name)
            self.at_joint(name,g['first_joint_position_m'])
            contact_row=dict(contact_index=i,finger_id=name,surface_id_predicted=contact.surface_id,
                finger_query_q_m=g['first_joint_position_m'],
                position_mug_m=list(contact.position_m),normal_outward_predicted=list(contact.normal_outward),
                finger_gap=self.point_gap(np.array(contact.position_m),name=name),mug_gap=self.point_gap(np.array(contact.position_m)))
            if detailed:
                contact_row['probe_radius_sensitivity']=[self.point_gap(np.array(contact.position_m),name=name,radius=r) for r in (1e-6,1e-5,1e-4)]
                contact_row['mug_probe_radius_sensitivity']=[self.point_gap(np.array(contact.position_m),radius=r) for r in (1e-6,1e-5,1e-4)]
                transition=next(f['transition'] for f in fingers if f['finger_id']==name)
                if 'q_interval_m' in transition:
                    qq=transition['q_interval_m'][0];self.at_joint(name,qq)
                    contact_row['at_first_detected_transition']=dict(q_m=qq,finger_gap=self.point_gap(np.array(contact.position_m),name=name))
                    self.at_joint(name,g['first_joint_position_m'])
            contacts.append(contact_row)
        supported=all(f['at_predicted']['minimum_distance_m'] is not None for f in fingers)
        mismatch=any(c[k]['inferred_point_gap_m'] is not None and abs(c[k]['inferred_point_gap_m'])>self.config.gap_tolerance_m
                     for c in contacts for k in ('finger_gap','mug_gap'))
        earlier=any(f.get('predicted_displacement_m') is not None and f['at_predicted']['minimum_distance_m'] is not None
            and f['at_predicted']['minimum_distance_m'] < -self.config.gap_tolerance_m for f in fingers)
        primitive_frames=[]
        for i,primitive in enumerate(geometry.primitives):
            if i>=len(self.metadata['mug']['collision']):break
            loaded=self.metadata['mug']['collision'][i];shape=primitive.shape
            expected=np.eye(4);expected[:3,:3]=rotation(shape.rpy);expected[:3,3]=shape.position
            expected=np.linalg.inv(transform(self.mug_inertial[3],self.mug_inertial[4]))@expected
            actual=transform(loaded['local_inertial_frame_position'],loaded['local_inertial_frame_quaternion'])
            primitive_frames.append(dict(urdf_primitive_id=primitive.primitive_id,api_order=i,
                translation_error_m=float(np.max(np.abs(actual[:3,3]-expected[:3,3]))),
                rotation_error=float(np.max(np.abs(actual[:3,:3]-expected[:3,:3]))),
                declared_kind=shape.kind,reported_geometry_type=loaded['geometry_type'],
                box_dimension_error_m=None if shape.kind!='box' else float(np.max(np.abs(np.array(loaded['dimensions'])-shape.dimensions))),
                identity_note='matched URDF/API array order and transform, not engine contact face ID'))
        bad_frame=(any(f['frame_translation_error_m']>self.config.frame_tolerance_m or f['frame_rotation_matrix_error']>self.config.rotation_tolerance for f in fingers)
            or any(f['translation_error_m']>self.config.frame_tolerance_m or f['rotation_error']>self.config.rotation_tolerance for f in primitive_frames))
        category='frame_mismatch' if bad_frame else ('unknown' if not supported or not contacts else ('surface_mismatch' if mismatch else 'geometry_aligned'))
        reasons=[]
        if bad_frame:reasons.append('declared/runtime relative transforms differ beyond explicit tolerance')
        if not supported:reasons.append('closest-point query unsupported or range-censored')
        if not contacts:reasons.append('no predicted contact points: '+spec.status.value+'; '+';'.join(spec.reasons))
        if mismatch:reasons.append('loaded point-gap differs from predicted surface beyond comparison tolerance; not a physical failure label')
        if earlier:reasons.append('loaded overlap at predicted first-contact configuration; finite-scan bracket reported separately')
        result=dict(version=VERSION,candidate_id=candidate.id,candidate_hash=spec.candidate_hash,contact_spec_hash=digest(spec.to_dict()),
            input_hash=digest(dict(candidate=spec.candidate_hash,spec=digest(spec.to_dict()),assets=self.assets,
                query=asdict(self.config),robot_flags=self.robot_flags,mug_flags=self.mug_flags,C1=geometry_evidence['input_hash'],
                base_pose=_native(self.base_pose),detailed=detailed,algorithm_version=VERSION)),
            frame='mug',length_unit='m',classification=category,coverage=dict(static_query_supported=supported,
                geometry_aligned=category=='geometry_aligned',surface_mismatch=mismatch,earlier_contact_evidence=earlier,
                frame_mismatch=bad_frame,unknown_or_unsupported=category=='unknown'),
            asset_hashes=self.assets,loading_flags=dict(panda=self.robot_flags,mug=self.mug_flags,robot_useFixedBase=True,mug_useFixedBase=False),
            pybullet_version=importlib.metadata.version('pybullet'),api_version=p.getAPIVersion(),query_config=asdict(self.config),
            world_object_transform=self.world_object.tolist(),world_tcp_transform=self.tcp.tolist(),fingers=fingers,contacts=contacts,
            mug_primitive_frames=primitive_frames,reasons=reasons,
            query_count=self.query_count-before,query_count_definition='actual getClosestPoints calls only; excludes metadata/FK and does not count cache hits',
            cache_hits=self.cache_hits-hits,elapsed_s=perf_counter()-began,
            assumptions=['pair-local fixed mug, joint resets only, arm at A home; no IK/control',
                'baseline original URDF shapes/margins; proxies labelled and isolated'],
            limitations=['no physics integration or observed force','no complete contact manifold or reliable compound face IDs',
                'finite scan is not continuous clearance proof','query/probe precision is not material contact accuracy'])
        return BulletAlignmentEvidence(result)
