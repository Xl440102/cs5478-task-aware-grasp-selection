"""Static rigid closure evidence from original primitives and full finger mesh.

Continuous triangle-box SAT is exact under the declared fixed-orientation mesh
model (within numerical tolerance). Cylinders are only broad-phase excluded;
an overlapping cylinder bound is UNKNOWN, never a collision/clearance proof.
No Bullet, force model, GWS column filtering, or physical execution is used.
"""
from dataclasses import asdict
from functools import lru_cache
import itertools
from time import perf_counter
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from grasp_a.contracts import candidate_sha256
from grasp_b.models import rotation
from grasp_b.wrench_v2.geometry import (
    quaternion_matrix, transform_finger_mesh, on_surface, union_intersections,
    containing_primitives, _mesh, _xyz,
)
from grasp_b.wrench_v2.diagnostics import wall_pair_diagnostics, rim_contact_diagnostics
from .contact_regions import GeometryEvidence, GeometryTolerances, Simultaneity, VERSION, digest, displacement_evidence


@lru_cache(maxsize=32)
def prepare_mesh(finger):
    """Cache binds actual vertices, transforms, joint limits AND mesh hash."""
    tri = np.asarray(transform_finger_mesh(finger), dtype=float)
    normals = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths < 1e-15) or not np.isfinite(tri).all():
        raise ValueError('degenerate/nonfinite mesh triangles unsupported')
    normals /= lengths[:, None]
    edges = {}
    orientation = {}
    for t in tri:
        for a, b in zip(t, np.roll(t, -1, axis=0)):
            a, b = tuple(a), tuple(b)
            key = tuple(sorted((a, b)))
            edges[key] = edges.get(key, 0)+1
            orientation[key] = orientation.get(key, 0)+(1 if a < b else -1)
    closed = all(v == 2 for v in edges.values()) and all(v == 0 for v in orientation.values())
    tri.setflags(write=False); normals.setflags(write=False)
    return tri, normals, closed


def _point_in_closed_mesh(point, triangles):
    p = triangles-np.asarray(point)
    lengths = np.linalg.norm(p, axis=2)
    if np.min(lengths) < 1e-12:
        return None
    a, b, c = p[:, 0], p[:, 1], p[:, 2]
    numerator = np.einsum('ij,ij->i', a, np.cross(b, c))
    denominator = (np.prod(lengths, axis=1)+np.einsum('ij,ij->i', a, b)*lengths[:, 2]
                   +np.einsum('ij,ij->i', b, c)*lengths[:, 0]+np.einsum('ij,ij->i', c, a)*lengths[:, 1])
    angle = abs(float(np.sum(2*np.arctan2(numerator, denominator))))
    if angle < 1e-6:return False
    if abs(angle-4*np.pi) < 1e-6:return True
    return None


def swept_triangles_box(triangles, velocity, half_extents, max_travel_m, *, margin_m=0.):
    """SAT time intervals for EACH translated triangle vs origin-centred box.

    Positive margin expands SAT intervals (possible contact); negative margin
    requires strict penetration. Times are inward displacement m, not seconds.
    Empty intervals are +inf. AABB is NOT used as the final collision test.
    """
    t, v, h = np.asarray(triangles, float), np.asarray(velocity, float), np.asarray(half_extents, float)
    if (t.ndim != 3 or t.shape[1:] != (3, 3) or v.shape != (3,) or h.shape != (3,)
            or not all(np.isfinite(x).all() for x in (t, v, h)) or np.any(h <= 0)
            or not np.isfinite(max_travel_m) or max_travel_m < 0 or not np.isfinite(margin_m)):
        raise ValueError('finite triangles, velocity, positive box and nonnegative travel required')
    e = np.roll(t, -1, axis=1)-t
    axes = np.concatenate((np.broadcast_to(np.eye(3), (len(t), 3, 3)),
        np.cross(e[:, 0], e[:, 1])[:, None, :],
        np.cross(e[:, :, None, :], np.eye(3)[None, None, :, :]).reshape(len(t), 9, 3)), axis=1)
    norm = np.linalg.norm(axes, axis=2)
    active = norm > 1e-14
    axes = axes/np.where(active, norm, 1.)[:, :, None]
    projections = np.einsum('nac,nvc->nav', axes, t)
    lo, hi = projections.min(axis=2), projections.max(axis=2)
    radius = np.einsum('nac,c->na', np.abs(axes), h)+margin_m
    speed = axes@v
    moving = np.abs(speed) > 1e-14
    denom = np.where(moving, speed, 1.)
    t0, t1 = (-radius-hi)/denom, (radius-lo)/denom
    entry = np.where(active & moving, np.minimum(t0, t1), -np.inf).max(axis=1)
    exit = np.where(active & moving, np.maximum(t0, t1), np.inf).min(axis=1)
    stationary_separated = np.any(active & ~moving & ((lo > radius) | (hi < -radius)), axis=1)
    entry = np.maximum(entry, 0.)
    return np.where(~stationary_separated & (entry <= exit) & (entry <= max_travel_m), entry, np.inf)


def triangle_box_witness(triangle, half_extents):
    """Independent polygon clipping with barycentric coordinates, not SAT.

    A returned point proves surface/box intersection; positive box slack proves
    strict penetration. The original triangle normal is not smoothed.
    """
    polygon=[(np.array(p),np.eye(3)[i]) for i,p in enumerate(triangle)]
    for axis,sign in itertools.product(range(3),(-1,1)):
        clipped=[]
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            da=sign*a[0][axis]-half_extents[axis];db=sign*b[0][axis]-half_extents[axis]
            if da<=0:clipped.append(a)
            if (da<0<db) or (db<0<da):
                alpha=da/(da-db)
                clipped.append((a[0]+alpha*(b[0]-a[0]),a[1]+alpha*(b[1]-a[1])))
            elif da==0 and db>0:
                clipped.append(a)
        polygon=clipped
        if not polygon:return None
    point=np.mean([p for p,b in polygon],axis=0);bary=np.mean([b for p,b in polygon],axis=0)
    return dict(point_local=point.tolist(),triangle_barycentric=bary.tolist(),
                box_interior_slack_m=float(np.min(half_extents-np.abs(point))))


def mesh_primitive_sweep(triangles_mug, direction_mug, travel_m, geometry, *, tolerance_m, closed=True):
    known, potential, unresolved = [], [], []
    queries = 0
    for primitive in geometry.primitives:
        shape = primitive.shape;r = rotation(shape.rpy)
        tri = (triangles_mug-np.asarray(shape.position))@r
        v = np.asarray(direction_mug)@r
        half = np.asarray(shape.dimensions)/2 if shape.kind == 'box' else np.array([shape.dimensions[0]]*2+[shape.dimensions[1]/2])
        end = tri+travel_m*v
        low, high = np.minimum(tri.min(axis=(0, 1)), end.min(axis=(0, 1))), np.maximum(tri.max(axis=(0, 1)), end.max(axis=(0, 1)))
        queries += 1
        if np.any(high < -half-tolerance_m) or np.any(low > half+tolerance_m):
            continue  # Broad-phase disjointness IS a clearance proof.
        if shape.kind != 'box':
            unresolved.append(dict(primitive_id=primitive.primitive_id, reason='cylinder_bound_overlaps_exact_sweep_not_implemented'))
            continue
        queries += 2*len(tri)
        for margin, target in ((tolerance_m, potential), (-tolerance_m, known)):
            entry = swept_triangles_box(tri, v, half, travel_m, margin_m=margin)
            if np.any(np.isfinite(entry)):
                for i in np.argsort(entry):
                    if not np.isfinite(entry[i]):break
                    item=dict(primitive_id=primitive.primitive_id,mesh_triangle_index=int(i),
                              displacement_m=float(entry[i]),query='continuous_triangle_box_SAT')
                    if margin<0:
                        witness=None
                        probes=([0.] if entry[i]==0 else [])+[
                            min(travel_m,float(entry[i])+min(max(0.,travel_m-float(entry[i]))/2,1e-5)),
                            min(travel_m,float(entry[i])+max(4*tolerance_m,1e-9))]
                        for probe in sorted(set(probes)):
                            attempt=triangle_box_witness(tri[i]+probe*v,half)
                            queries+=1
                            if attempt is not None and attempt['box_interior_slack_m']>tolerance_m:
                                witness=attempt;break
                        if witness is None:
                            continue
                        point=np.array(witness['point_local'])@r.T+shape.position
                        item.update(displacement_m=probe,sat_entry_bound_m=float(entry[i]),
                                    witness=dict(**witness,point_mug_m=point.tolist()))
                    target.append(item)
                    break
        if closed:
            inside = _point_in_closed_mesh((0, 0, 0), tri)
            if inside is True:
                known.append(dict(primitive_id=primitive.primitive_id, displacement_m=0.,
                                  mesh_triangle_index=None, query='box_centre_contained_in_closed_mesh_at_opening'))
            elif inside is None:
                unresolved.append(dict(primitive_id=primitive.primitive_id, reason='opening_containment_ambiguous'))
    if not closed:
        unresolved.append(dict(reason='mesh_not_closed_or_consistently_oriented_containment_unverified'))
    return dict(definite_first=min(known, key=lambda x:x['displacement_m']) if known else None,
                possible_first=min(potential, key=lambda x:x['displacement_m']) if potential else None,
                unresolved=unresolved, geometry_queries=queries,
                scope='all declared triangles vs boxes; cylinders only broad-phase disjointness')


def load_palm_mesh(panda_urdf):
    """Existing URDF/OBJ only; no simulator. Palm remains fixed during closure."""
    path=Path(panda_urdf);root=ET.parse(path).getroot()
    triangles, xyz, rpy, sha = _mesh(path, root, 'panda_hand')
    tool=root.find("joint[@name='panda_grasptarget_hand']")
    if tool is None or np.linalg.norm(_xyz(tool.find('origin'),'rpy')) > 1e-12:
        raise ValueError('unsupported palm/TCP transform')
    tcp=_xyz(tool.find('origin'));r=rotation(rpy)
    tri=np.asarray(triangles)@r.T+xyz-tcp
    edges={};orientation={}
    for t in tri:
        for a,b in zip(t,np.roll(t,-1,axis=0)):
            a,b=tuple(a),tuple(b);key=tuple(sorted((a,b)))
            edges[key]=edges.get(key,0)+1;orientation[key]=orientation.get(key,0)+(1 if a<b else -1)
    return dict(triangles_tool=tri,mesh_sha256=sha,
                closed=all(x==2 for x in edges.values()) and all(x==0 for x in orientation.values()))


def _mesh_regions(triangles, normals, indices, tolerance):
    # Connected coplanar components, NOT physical load patches.
    left=set(indices);groups=[]
    while left:
        seed=min(left);left.remove(seed);group={seed};todo=[seed]
        while todo:
            i=todo.pop()
            for j in tuple(left):
                shared=sum(any(np.linalg.norm(v-w) <= tolerance for w in triangles[j]) for v in triangles[i]) >= 2
                if shared and np.linalg.norm(normals[i]-normals[j]) <= 1e-7 and abs(float(normals[i]@(triangles[j,0]-triangles[i,0]))) <= tolerance:
                    left.remove(j);group.add(j);todo.append(j)
        groups.append(tuple(sorted(group)))
    return groups


def _mesh_membership(point, triangles, normals, tolerance):
    found=[]
    for i,(tri,n) in enumerate(zip(triangles,normals)):
        residual=float(n@(point-tri[0]))
        if abs(residual)>tolerance:continue
        edges=np.array([tri[1]-tri[0],tri[2]-tri[0]]).T
        uv=np.linalg.lstsq(edges,point-tri[0],rcond=None)[0]
        bary=np.r_[1-uv.sum(),uv]
        if np.min(bary)>=-1e-9:
            found.append(dict(triangle_index=i,barycentric=bary.tolist(),plane_residual_m=residual,
                              normal_tool=n.tolist(),triangle_edge_boundary=bool(np.min(bary)<1e-8)))
    return found


def _face_edge_distance(primitive, point):
    local=rotation(primitive.shape.rpy).T@(point-primitive.shape.position)
    if primitive.shape.kind!='box':return None
    gaps=np.asarray(primitive.shape.dimensions)/2-np.abs(local)
    return float(np.sort(gaps)[1])  # smallest is the face plane itself.


def validate_contact_geometry(candidate, spec, geometry, gripper, *, tolerances=None, palm=None, sample_density=3,
                              sampling_domain='legacy_footprint'):
    """Evidence only. No contacts added/removed and no original statuses changed."""
    started=perf_counter();tol=tolerances or GeometryTolerances()
    if not isinstance(tol,GeometryTolerances) or type(sample_density) is not int or sample_density<2:
        raise ValueError('explicit tolerances and sample density >=2 required')
    if sampling_domain not in ('legacy_footprint','full_inward_projection'):
        raise ValueError('explicit supported sampling domain required')
    candidate_hash=candidate_sha256([candidate])
    spec.assert_candidate_identity(candidate.id,candidate_hash)
    object_hash=digest(dict(asset=geometry.asset_sha256,version=geometry.model_version,profile=geometry.profile,
                            reference_point_m=list(geometry.reference_point_m),
                            primitives=[asdict(p) for p in geometry.primitives]))
    gripper_hash=digest(asdict(gripper))
    for key, actual in [('object_asset_sha256',geometry.asset_sha256),('panda_sha256',gripper.panda_sha256)]:
        if key in spec.provenance and spec.provenance[key]!=actual:raise ValueError('asset identity mismatch: '+key)
    if 'finger_mesh_sha256' in spec.provenance and dict(spec.provenance['finger_mesh_sha256']) != {f.finger_id:f.sha256 for f in gripper.fingers}:
        raise ValueError('finger mesh identity mismatch')
    if palm is not None and palm['mesh_sha256'] != gripper.palm_sha256:raise ValueError('palm asset identity mismatch')
    r=quaternion_matrix(candidate.grasp_pose.quaternion);center=np.asarray(candidate.grasp_pose.position)
    closing=r[:,1];q_open=candidate.gripper_width/2
    inputs=dict(algorithm_version=VERSION,candidate_hash=candidate_hash,spec=digest(spec.to_dict()),mug=object_hash,gripper=gripper_hash,
                tolerances=asdict(tol),sample_density=sample_density,sampling_domain=sampling_domain,
                palm=None if palm is None else digest(dict(sha256=palm['mesh_sha256'],triangles=palm['triangles_tool'].tolist(),closed=palm.get('closed',False))))
    result=dict(algorithm_version=VERSION,candidate_id=candidate.id,candidate_hash=candidate_hash,
        contact_spec_hash=inputs['spec'],input_hash=digest(inputs),mug_geometry_hash=object_hash,gripper_geometry_hash=gripper_hash,
        geometry_assets=dict(mug_sha256=geometry.asset_sha256,panda_sha256=gripper.panda_sha256,
            finger_sha256={f.finger_id:f.sha256 for f in gripper.fingers},palm_sha256=gripper.palm_sha256),
        frame='mug',length_unit='m',tolerances=asdict(tol),contact_spec_status=spec.status.value,
        mesh_region_definition=dict(inward_normal_y_threshold=.9,coplanar_normal_difference_tolerance=1e-7,
            meaning='connected coplanar triangle components; not pressure/load patches'),
        candidate_pose_mug=asdict(candidate.grasp_pose),closing_direction_mug=closing.tolist(),approach_direction_mug=r[:,2].tolist(),
        classification='insufficient_evidence',evidence_status='partial',contacts=[],finger_groups=[],
        assumptions=['fixed rigid mug; independently translating Panda finger joints',
            'declared triangle meshes and URDF primitives; no Bullet margins/convexification',
            'numerical tie does not establish pressure, compliance, force closure or independent load capacity'],
        limitations=['no pregrasp swept-arm/hand trajectory validation','no material deformation or contact-force evidence',
                     'raw triangle geometry differs from possible Bullet convexification and margins',
                     'closed oriented meshes treated as non-self-intersecting solids for containment'],
        queries=0,query_definition='instrumented union membership/rays, primitive broad phases, triangle-SAT pairs and clip queries; excludes preprocessing, metadata and mesh-face lookups',timings_s={})
    if not 0<q_open<=min(f.upper_m for f in gripper.fingers):
        raise ValueError('opening outside declared Panda joint limits')
    cached={};t=perf_counter()
    for f in gripper.fingers:cached[f.finger_id]=prepare_mesh(f)
    result['timings_s']['mesh_preparation']=perf_counter()-t
    invalid=False;ambiguous=False
    for i,contact in enumerate(spec.contacts):
        finger=gripper.finger(contact.finger_id.value);tri,normals,closed=cached[finger.finger_id]
        sign=1 if finger.finger_id=='left' else -1
        p=np.asarray(contact.position_m);tool=r.T@(p-center);inner=finger.inner_face(float(tool[0]),float(tool[2]))
        q=None if inner is None else sign*(tool[1]-inner)
        point0=tool.copy()
        if q is not None:point0[1]-=sign*q
        mesh_hits=[] if q is None else _mesh_membership(point0,tri,normals,tol.surface_m)
        try:
            primitive=geometry.primitive(contact.surface_id.rsplit(':',1)[0])
            valid=on_surface(primitive,p,contact.normal_outward,contact.surface_id,tolerance=tol.surface_m)
            membership=containing_primitives(geometry,[p],strict=True,tolerance=tol.surface_m)[0]
            covered=[v.primitive_id for v,b in zip(geometry.primitives,membership) if b and v.primitive_id!=primitive.primitive_id]
            valid=valid and not covered
            edge=_face_edge_distance(primitive,p)
        except (StopIteration,ValueError):
            primitive=None;valid=False;edge=None;covered=[]
        facing=sign*float(np.dot(contact.normal_outward,closing))
        good=valid and bool(mesh_hits) and q is not None and finger.lower_m-tol.surface_m<=q<=q_open+tol.surface_m
        near_edge=edge is not None and edge<=tol.surface_m*tol.boundary_factor
        invalid |= not good;ambiguous |= near_edge or facing<=tol.tangent_cosine
        result['contacts'].append(dict(contact_index=i,finger_id=finger.finger_id,capacity_group=contact.capacity_group_id,
            position_mug_m=list(contact.position_m),normal_outward=list(contact.normal_outward),surface_id=contact.surface_id,
            source=contact.source.value,tool_position_m=tool.tolist(),finger_mesh_point_at_q0_tool_m=point0.tolist(),
            joint_position_m=None if q is None else float(q),contact_displacement_m=None if q is None else float(q_open-q),
            finger_triangle_hits=mesh_hits,surface_valid=valid,covered_by_other_primitives=covered,mesh_supported=bool(mesh_hits),
            edge_distance_m=edge,near_edge=near_edge,normal_closing_cosine=facing,
            position_uncertainty_m=None,uncertainty_reason='no calibrated geometry/pose uncertainty available'))
    if spec.contacts and all(c['surface_valid'] for c in result['contacts']):
        seams=rim_contact_diagnostics(spec.contacts,geometry)
        for c,row in zip(result['contacts'],seams['contacts']):
            c['facet_seam']=row
            if row['nearest_exposed_facet_seam_m'] is not None and row['nearest_exposed_facet_seam_m']<=tol.surface_m*tol.boundary_factor:
                ambiguous=True
    sweeps={};group_states=[]
    for finger in gripper.fingers:
        name=finger.finger_id;sign=1 if name=='left' else -1;tri,normals,closed=cached[name]
        contacts=[c for c in result['contacts'] if c['finger_id']==name]
        known=[c for c in contacts if c['joint_position_m'] is not None]
        if not known:group=dict(classification='insufficient_evidence',reason='no supported predicted contacts',first_joint_position_m=q_open)
        else:group=displacement_evidence([c['joint_position_m'] for c in known],q_open,
                    tolerance_m=tol.displacement_m,boundary_factor=tol.boundary_factor)
        inward=[i for i,n in enumerate(normals) if sign*n[1]<=-.9]
        regions=_mesh_regions(tri,normals,inward,tol.surface_m)
        for c in contacts:
            c['finger_mesh_regions']=[k for k,indices in enumerate(regions) if any(h['triangle_index'] in indices for h in c['finger_triangle_hits'])]
        intervals=[]
        for c in known:
            projections=[abs(h['normal_tool'][1]) for h in c['finger_triangle_hits']]
            if projections and min(projections)>tol.tangent_cosine:
                # For the existing point to lie on its triangle plane, joint q
                # must stay in this numerical surface-distance interval. This
                # is distinct from the legacy 1um sampled-ray tie threshold.
                delta_q=tol.surface_m/min(projections)
                intervals.append((c['joint_position_m']-delta_q,c['joint_position_m']+delta_q))
        rigid=dict(classification='insufficient_evidence',reason='missing unique supported mesh plane')
        if len(intervals)==len(contacts) and contacts:
            lower=max(v[0] for v in intervals);upper=min(v[1] for v in intervals)
            rigid=dict(classification='geometry_conflict' if lower>upper else 'geometrically_compatible',
                       shared_joint_interval_m=[lower,upper],interval_empty=lower>upper,
                       point_joint_intervals_m=[list(v) for v in intervals],surface_tolerance_m=tol.surface_m,
                       meaning='declared triangle-plane support intervals, not deformation or pressure')
            if lower<=upper:
                common=(lower+upper)/2
                fits=all(_mesh_membership(r.T@(np.array(c['position_mug_m'])-center)-[0,sign*common,0],tri,normals,tol.surface_m)
                         for c in contacts)
                if not fits:rigid.update(classification='insufficient_evidence',reason='shared plane interval lacks verified triangle membership')
                rigid['common_joint_position_m']=common
        pairs=[]
        for a,b in itertools.combinations(contacts,2):
            na,nb=np.array(a['normal_outward']),np.array(b['normal_outward'])
            primitive_a=a['surface_id'].rsplit(':',1)[0];primitive_b=b['surface_id'].rsplit(':',1)[0]
            relation=wall_pair_diagnostics(geometry,primitive_a,primitive_b,z_m=(a['position_mug_m'][2]+b['position_mug_m'][2])/2)
            pairs.append(dict(contact_indices=[a['contact_index'],b['contact_index']],
                distance_m=float(np.linalg.norm(np.array(a['position_mug_m'])-b['position_mug_m'])),
                normal_angle_deg=float(np.degrees(np.arctan2(np.linalg.norm(np.cross(na,nb)),na@nb))),
                same_mesh_region=bool(set(a['finger_mesh_regions'])&set(b['finger_mesh_regions'])),facet_relation=relation))
        stop=group['first_joint_position_m'];travel=max(0.,q_open-stop)
        transforms={}
        for label,q in [('zero_joint',0.),('opening',q_open),('predicted_first',stop)]:
            transform=np.eye(4);transform[:3,:3]=r@rotation(finger.collision_rpy)
            transform[:3,3]=center+r@(np.asarray(finger.joint_origin_tool_m)+[0,sign*q,0])
            transforms[label]=transform.tolist()
        # Additional deterministic mesh-supported rays ONLY diagnose omitted first
        # contacts. They are never appended to ContactSpec or used as force nodes.
        footprint=np.asarray(gripper.pad_samples)[:,:2]
        if sampling_domain=='full_inward_projection':
            footprint=tri[inward][:,:,(0,2)].reshape(-1,2)
        samples=[];opening_penetrations=[]
        for x in np.linspace(footprint[:,0].min(),footprint[:,0].max(),sample_density):
            for z in np.linspace(footprint[:,1].min(),footprint[:,1].max(),sample_density):
                y=finger.inner_face(float(x),float(z))
                if y is None:continue
                origin=center+r[:,0]*x+r[:,2]*z
                point=origin+(sign*q_open+y)*closing
                membership=containing_primitives(geometry,[point],strict=True,tolerance=tol.surface_m)[0]
                result['queries']+=1
                if np.any(membership):opening_penetrations.append(dict(position_mug_m=point.tolist(),primitives=[p.primitive_id for p,b in zip(geometry.primitives,membership) if b]))
                hits=union_intersections(geometry,origin,closing);result['queries']+=1
                reachable=[(sign*(h.t_m-y),h) for h in hits if finger.lower_m<=sign*(h.t_m-y)<=q_open]
                if reachable:
                    q,h=max(reachable,key=lambda v:v[0]);samples.append(dict(q_m=float(q),surface=h.surface_id,
                        ambiguous=h.ambiguous,normal_closing_cosine=sign*float(np.dot(h.normal_outward,closing)),
                        displacement_m=float(q_open-q)))
        dense_first=max(samples,key=lambda x:x['q_m']) if samples else None
        opened=(tri+np.array([0.,sign*q_open,0.]))@r.T+center
        sweep=mesh_primitive_sweep(opened,-sign*closing,travel,geometry,tolerance_m=tol.surface_m,closed=closed)
        result['queries']+=sweep['geometry_queries'];sweeps[name]=sweep
        group.update(finger_id=name,contact_indices=[c['contact_index'] for c in contacts],pairs=pairs,
            rigid_contact_configuration=rigid,
            transforms_mesh_to_mug=transforms,
            coplanar_mesh_regions=[list(x) for x in regions],dense_sample_count=len(samples),dense_first=dense_first,
            opening_penetrations=opening_penetrations,mesh_sweep=sweep)
        for c in known:
            delta=stop-c['joint_position_m'];c['closing_gap_at_group_first_m']=delta
            c['normal_gap_at_group_first_m']=delta*c['normal_closing_cosine']
        group_states.append(group['classification']);result['finger_groups'].append(group)
    palm_sweep=None
    if palm is not None:
        palm_tri=np.asarray(palm['triangles_tool'])@r.T+center
        palm_sweep=mesh_primitive_sweep(palm_tri,np.zeros(3),0.,geometry,tolerance_m=tol.surface_m,closed=palm.get('closed',False))
        result['queries']+=palm_sweep['geometry_queries']
    result['palm_geometry']=palm_sweep if palm_sweep is not None else dict(status='unknown',reason='palm mesh not supplied')
    q_values=[g['first_joint_position_m'] for g in result['finger_groups']]
    result['joint_compatibility']=dict(independent_joint_positions_m=q_values,
        symmetric_joint_position_compatible=abs(q_values[0]-q_values[1])<=tol.displacement_m,
        all_predicted_contacts_have_common_independent_configuration=all(g['rigid_contact_configuration']['classification']=='geometrically_compatible' for g in result['finger_groups']),
        note='independent rigid joint configuration, not symmetric motor tracking or force equilibrium')
    early=[];unclear=[]
    for g in result['finger_groups']:
        target=q_open-g['first_joint_position_m'];sweep=g['mesh_sweep']
        if g['rigid_contact_configuration']['classification']=='geometry_conflict' and g['classification']!='sequential_contact':
            early.append('no shared rigid joint coordinate within surface tolerance:'+g['finger_id'])
        if g['opening_penetrations']:early.append('sampled opening penetration:'+g['finger_id'])
        if sweep['definite_first'] and sweep['definite_first']['displacement_m']<target-tol.displacement_m:
            early.append('full triangle surface intersects box before predicted group:'+g['finger_id'])
        if sweep['definite_first'] and sweep['definite_first']['displacement_m']==0:
            early.append('opening mesh/box intersection:'+g['finger_id'])
        if sweep['unresolved']:unclear.append('incomplete full sweep:'+g['finger_id'])
        if (sweep['possible_first'] and sweep['possible_first']['displacement_m']<target-tol.displacement_m
                and (not sweep['definite_first'] or sweep['definite_first']['displacement_m']>=target-tol.displacement_m)):
            unclear.append('possible earlier boundary contact:'+g['finger_id'])
        if g['dense_first'] and g['dense_first']['q_m']>g['first_joint_position_m']+tol.displacement_m:
            if not g['dense_first']['ambiguous'] and g['dense_first']['normal_closing_cosine']>tol.tangent_cosine:
                early.append('denser rays find earlier contact:'+g['finger_id'])
            else:unclear.append('earlier ambiguous dense boundary event:'+g['finger_id'])
    if palm_sweep and palm_sweep['definite_first']:early.append('palm/box intersection at candidate grasp pose')
    if palm_sweep and palm_sweep['possible_first'] and not palm_sweep['definite_first']:
        unclear.append('palm boundary contact without strict penetration witness')
    if palm_sweep is None or palm_sweep['unresolved']:unclear.append('palm clearance incomplete')
    if invalid or early:state=Simultaneity.CONFLICT
    elif 'sequential_contact' in group_states:state=Simultaneity.SEQUENTIAL
    elif ambiguous or unclear or 'insufficient_evidence' in group_states:state=Simultaneity.INSUFFICIENT
    else:state=Simultaneity.COMPATIBLE
    result.update(classification=state.value,evidence_status='known_conflict' if state is Simultaneity.CONFLICT else
        ('complete_under_declared_scope' if state is Simultaneity.COMPATIBLE else 'partial'),
        reasons=early+unclear+(['surface/mesh/travel inconsistency'] if invalid else [])+
        (['edge/seam/near-tangent ambiguity'] if ambiguous else []),
        tie_source='legacy adapter 1e-6m is a numerical first-ray tie, not deformation',sample_density=sample_density,
        sampling_domain=sampling_domain,
        elapsed_s=perf_counter()-started)
    return GeometryEvidence(result)
