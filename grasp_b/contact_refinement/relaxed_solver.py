"""Experimental TASK-LEVEL joint minimax/lexicographic LPs, not strict GWS.

Each actual solve contains all distinct required demands and one shared R/u.
Bitwise-identical demands may share a block; every original sample is retained
and revalidated. No scenario is dropped based on score, geometry or outcome.
"""
from dataclasses import asdict, dataclass
import hashlib
import importlib.metadata
import json
from pathlib import Path
from time import perf_counter
import warnings

import numpy as np
from scipy import sparse
from scipy.optimize import linprog

from grasp_b.wrench_v2.contacts import _freeze_json, _plain
from grasp_b.wrench_v2.constraints import validate_constraints, ConstraintStatus
from .contact_regions import digest

VERSION = 'task_joint_minimax_lex_linf_v1'
NAMESPACE = 'part_b_contact_refinement_task_lex_v1'


def _identity(value):
    if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('explicit SHA256 identity required')


def _vector(value,size):
    raw=np.asarray(value)
    if raw.shape!=(size,) or raw.dtype.kind not in 'fiu' or not np.isfinite(raw).all():
        raise ValueError('finite SI vector required')
    return tuple(float(x) for x in raw)


@dataclass(frozen=True,kw_only=True)
class TaskLexConfig:
    reference_force_n: float
    characteristic_length_m: float
    residual_slack_absolute: float = 1e-7
    residual_slack_relative: float = 0.
    primal_absolute: float = 1e-7
    dual_absolute: float = 1e-7
    objective_gap_absolute: float = 1e-7
    relative_tolerance: float = 1e-9
    backend_feasibility: float = 1e-9
    backend_small_matrix_value: float = 1e-12
    deduplicate_exact_demands: bool = True

    def __post_init__(self):
        for name,value in asdict(self).items():
            if name=='deduplicate_exact_demands':
                if type(value) is not bool:raise ValueError('explicit deduplication flag required')
                continue
            if isinstance(value,bool) or not np.isfinite(value) or value<0:
                raise ValueError('finite nonnegative solver configuration required')
            if value==0 and name not in ('residual_slack_absolute','residual_slack_relative','relative_tolerance'):
                raise ValueError('positive scale/tolerance required')
        if self.backend_feasibility<1e-10:raise ValueError('HiGHS tolerance below supported range')
        if self.backend_small_matrix_value<1e-12:raise ValueError('HiGHS small coefficient threshold below supported range')

    @property
    def scale(self):
        return np.r_[np.ones(3)/self.reference_force_n,np.ones(3)/(self.reference_force_n*self.characteristic_length_m)]


@dataclass(frozen=True)
class RequiredWrench:
    scenario_id: str
    wrench: tuple | None
    status: str = 'complete'

    def __post_init__(self):
        if not isinstance(self.scenario_id,str) or not self.scenario_id.strip():raise ValueError('scenario ID required')
        if self.status not in ('complete','missing_inertia','missing_parameters','unknown_motion','unsupported','numerical_error'):
            raise ValueError('explicit required-scenario status required')
        if self.wrench is not None:object.__setattr__(self,'wrench',_vector(self.wrench,6))
        if self.status=='complete' and self.wrench is None:raise ValueError('complete scenario needs wrench')


@dataclass(frozen=True,kw_only=True)
class TaskDemands:
    candidate_id: str
    candidate_hash: str
    task: str
    method: str
    object_identity: str
    common_context_identity: str
    source_identity: str
    scenarios: tuple
    reference_point_m: tuple
    frame: str = 'mug'

    def __post_init__(self):
        if not self.candidate_id or not self.task or self.method not in ('relaxed_m1_task_lex_v1','relaxed_m2_task_lex_v1'):
            raise ValueError('candidate/task/independent method identity required')
        for value in (self.candidate_hash,self.object_identity,self.common_context_identity,self.source_identity):_identity(value)
        scenarios=tuple(self.scenarios)
        if any(not isinstance(x,RequiredWrench) for x in scenarios) or len({x.scenario_id for x in scenarios})!=len(scenarios):
            raise ValueError('unique typed required scenarios required')
        object.__setattr__(self,'scenarios',scenarios)
        object.__setattr__(self,'reference_point_m',_vector(self.reference_point_m,3))
        if self.frame!='mug':raise ValueError('mug-local wrench frame required')


@dataclass(frozen=True)
class TaskLexResult:
    payload: object

    def __post_init__(self):
        d=json.loads(json.dumps(_plain(self.payload),allow_nan=False))
        if d.get('version')!=VERSION or d.get('namespace')!=NAMESPACE:raise ValueError('experimental namespace required')
        previous=d.pop('result_hash',None);actual=digest(d)
        if previous is not None and previous!=actual:raise ValueError('result checksum mismatch')
        d['result_hash']=actual;object.__setattr__(self,'payload',_freeze_json(d))

    def to_dict(self):return _plain(self.payload)
    def to_json(self):return json.dumps(self.to_dict(),sort_keys=True,separators=(',',':'),allow_nan=False)

    @classmethod
    def from_json(cls,text):
        def unique(pairs):
            d={}
            for k,v in pairs:
                if k in d:raise ValueError('duplicate JSON key')
                d[k]=v
            return d
        def reject(value):raise ValueError('nonfinite JSON')
        d=json.loads(text,object_pairs_hook=unique,parse_constant=reject)
        if 'result_hash' not in d:raise ValueError('stored result hash required')
        return cls(d)


class ExactTaskCache:
    """Explicit process-local cache, bound to full input/config/backend/source.

    No class/global shared state. Old timings are never counted as a new solve.
    """
    def __init__(self):self.records={};self.hits=0;self.misses=0


def _arrays(a,b,bounds):
    arrays=[]
    for x in (a,b,bounds):
        raw=np.asarray(x)
        if raw.dtype.kind not in 'fiu' or not np.isfinite(raw).all():raise ValueError('finite physical matrices required')
        arrays.append(np.array(raw,dtype=float,copy=True))
    a,b,bounds=arrays
    if a.ndim!=2 or a.shape[0]!=6 or a.shape[1]==0 or b.ndim!=2 or b.shape[1]!=a.shape[1] or bounds.shape!=(b.shape[0],):
        raise ValueError('A/B/b shape mismatch')
    if np.any(b<0) or np.any(bounds<0) or np.any(b.sum(axis=0)<=0):raise ValueError('shared bounded capacities required')
    return a,b,bounds


def _unique(demands,enabled):
    unique=[];indices=[];lookup={}
    for scenario in demands.scenarios:
        key=np.asarray(scenario.wrench,dtype='<f8').tobytes()
        if not enabled or key not in lookup:
            index=len(unique);unique.append(scenario.wrench)
            if enabled:lookup[key]=index
        else:index=lookup[key]
        indices.append(index)
    return np.array(unique),indices


def build_joint_problem(a,b,bounds,wrenches,config,*,stage,radius=None):
    """Sparse joint block LP: n*K+1 variables and K*(12+m) inequalities."""
    c=config.scale[:,None]*a;d=np.asarray(wrenches)*config.scale
    per=sparse.csr_matrix(np.vstack((c,-c,b)))
    k=len(wrenches);base=sparse.block_diag([per]*k,format='csr')
    shared=np.r_[-np.ones(12),np.zeros(len(bounds))] if stage==1 else np.r_[np.zeros(12),-bounds]
    matrix=sparse.hstack((base,sparse.csr_matrix(np.tile(shared,k)[:,None])),format='csr')
    rhs=np.concatenate([np.r_[x,-x,bounds] if stage==1 else np.r_[x+radius,-x+radius,np.zeros(len(bounds))] for x in d])
    cost=np.r_[np.zeros(a.shape[1]*k),1.]
    variable_bounds=[(0,None)]*(a.shape[1]*k)+[(0,None) if stage==1 else (0,1)]
    return matrix,rhs,cost,variable_bounds


def _validate_reply(a,b,bounds,wrenches,config,stage,radius,problem,x,dual,upper_dual):
    matrix,rhs,cost,variable_bounds=problem;n=a.shape[1];k=len(wrenches)
    if (x.shape!=(n*k+1,) or dual.shape!=rhs.shape or not np.isfinite(x).all() or not np.isfinite(dual).all() or not np.isfinite(upper_dual)):
        return dict(valid=False,reason='malformed/nonfinite solver witness')
    coefficients=x[:-1].reshape(k,n);physical=coefficients@a.T-wrenches
    scaled=physical*config.scale;residuals=np.max(np.abs(scaled),axis=1)
    capacity=coefficients@b.T;objective=float(x[-1])
    upper=float(max(objective,float(residuals.max()))) if stage==1 else float(max(objective,max(
        (float(capacity[:,j].max()/value) for j,value in enumerate(bounds) if value>0),default=0.)))
    raw_dual_bound=float(rhs@dual+(upper_dual if stage==2 else 0.))
    # Turn approximate dual feasibility into a conservative global bound.
    # Every lambda column is bounded by a declared shared/local capacity. Small
    # reduced-cost violations may otherwise accumulate over many scenarios.
    safe_dual=np.minimum(dual,0.)
    if stage==1:
        shared_cost=float(np.asarray(matrix[:,-1].T@safe_dual).ravel()[0])
        if shared_cost>1.:safe_dual=safe_dual/shared_cost
    safe_upper=min(upper_dual,0.) if stage==2 else 0.
    safe_reduced=np.asarray(matrix.T@safe_dual).ravel()+np.r_[np.zeros(n*k),safe_upper]-cost
    per_column=np.min(np.divide(bounds[:,None],b,out=np.full(b.shape,np.inf),where=b>0),axis=0)
    bound_correction=float(np.tile(per_column,k)@np.maximum(safe_reduced[:-1],0.))
    if stage==2:bound_correction+=max(0.,float(safe_reduced[-1]))
    dual_bound=float(rhs@safe_dual+safe_upper-bound_correction)
    reduced=np.asarray(matrix.T@dual).ravel()+np.r_[np.zeros(n*k),upper_dual]-cost
    eps=config.primal_absolute;rel=config.relative_tolerance
    nonnegative=max(0.,float(-x.min()));cap_rhs=bounds if stage==1 else objective*bounds
    capacity_violation=float(np.maximum(capacity-cap_rhs,0).max())
    cap_allowance=eps+rel*max(1.,float(np.abs(cap_rhs).max()))
    residual_violation=float(np.maximum(residuals-(objective if stage==1 else radius),0).max())
    residual_allowance=eps+rel*max(1.,float(np.max(np.abs(wrenches*config.scale))),abs(objective if stage==1 else radius))
    gap=upper-dual_bound;gap_allowance=config.objective_gap_absolute+rel*max(1.,abs(upper),abs(dual_bound))
    dual_violation=max(0.,float(dual.max()),float(reduced.max()),upper_dual if stage==2 else abs(upper_dual))
    bounds_violation=max(0.,objective-1.,upper-1.) if stage==2 else 0.
    valid=(nonnegative<=eps and capacity_violation<=cap_allowance and residual_violation<=residual_allowance and
           dual_violation<=config.dual_absolute+rel*max(1.,float(np.abs(cost).max())) and
           -gap_allowance<=gap<=gap_allowance and bounds_violation<=eps+rel)
    return dict(valid=bool(valid),reason='verified_original_matrix' if valid else 'original_matrix_certificate_failed',
        objective_nominal=objective,objective_upper=upper,dual_lower_bound=dual_bound,raw_dual_lower_bound=raw_dual_bound,
        conservative_dual_correction=bound_correction,objective_interval=[max(0.,min(upper,dual_bound)),upper],
        signed_objective_gap=gap,gap_allowance=gap_allowance,capacity_violation_n=capacity_violation,capacity_allowance_n=cap_allowance,
        residual_violation_scaled=residual_violation,residual_allowance_scaled=residual_allowance,
        nonnegativity_violation_n=nonnegative,dual_violation=dual_violation,bound_violation=bounds_violation,
        per_unique_scenario_residual=residuals.tolist(),physical_residuals=physical.tolist())


def verify_stage(a,b,bounds,wrenches,config,stage_record):
    """Independent rebuild/reconstruction from saved ORIGINAL matrices/wrenches."""
    problem=build_joint_problem(a,b,bounds,wrenches,config,stage=stage_record['stage'],radius=stage_record.get('radius'))
    return _validate_reply(a,b,bounds,np.asarray(wrenches),config,stage_record['stage'],stage_record.get('radius'),problem,
        np.asarray(stage_record['x']),np.asarray(stage_record['dual_inequality']),stage_record['dual_upper_last'])


def verify_original_scenarios(a,b,bounds,demands,config,result):
    """Re-expand EVERY required input, checking exact dedup mapping and load.

    Independent of the matrix builder: no original demand may be projected,
    replaced, rounded or silently omitted by block assembly/cache logic.
    """
    d=result.to_dict() if isinstance(result,TaskLexResult) else result
    if d['status']!='scored_conditional':raise ValueError('complete task result required')
    mapping=d['original_to_unique'];unique=np.asarray(d['unique_required_wrenches'],dtype='<f8')
    if len(mapping)!=len(demands.scenarios):raise ValueError('missing original required scenario')
    for scenario,index in zip(demands.scenarios,mapping):
        if type(index) is not int or not 0<=index<len(unique) or np.asarray(scenario.wrench,dtype='<f8').tobytes()!=unique[index].tobytes():
            raise ValueError('non-exact demand mapping')
    n=a.shape[1];coefficients=np.asarray(d['stage2']['record']['x'][:-1]).reshape(len(unique),n)[mapping]
    original=np.array([s.wrench for s in demands.scenarios]);actual=coefficients@a.T-original
    residual=np.max(np.abs(actual*config.scale),axis=1)
    if not np.allclose(residual,d['per_original_scenario_residual_stage2'],atol=1e-12,rtol=1e-12):
        raise ValueError('original residual reconstruction mismatch')
    allowance=config.primal_absolute+config.relative_tolerance*max(1.,float(np.abs(original*config.scale).max()))
    if residual.max()>d['stage2']['record']['radius']+allowance:raise ValueError('required original scenario violates task residual radius')
    return dict(original_scenarios_verified=len(mapping),maximum_actual_residual=float(residual.max()),exact_mapping=True)


def _stage(a,b,bounds,wrenches,config,*,stage,radius=None):
    began=perf_counter();problem=build_joint_problem(a,b,bounds,wrenches,config,stage=stage,radius=radius)
    assembly=perf_counter()-began;matrix,rhs,cost,variable_bounds=problem;attempts=[];record=None
    for method,presolve in (('highs',True),('highs-ipm',False),('highs-ds',False)):
        started=perf_counter()
        try:
            options=dict(presolve=presolve,primal_feasibility_tolerance=config.backend_feasibility,
                         dual_feasibility_tolerance=config.backend_feasibility,ipm_optimality_tolerance=config.backend_feasibility,
                         small_matrix_value=config.backend_small_matrix_value)
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore',message=r"Unrecognized options detected: \{'small_matrix_value': .*\}\. These will be passed to HiGHS verbatim\.")
                reply=linprog(cost,A_ub=matrix,b_ub=rhs,bounds=variable_bounds,method=method,options=options)
            attempt=dict(method=method,presolve=presolve,status=int(reply.status),message=str(reply.message),options=options,runtime_s=perf_counter()-started)
            if reply.success and reply.x is not None:
                dual=np.array(reply.ineqlin.marginals);upper=float(reply.upper.marginals[-1]) if stage==2 else 0.
                validation=_validate_reply(a,b,bounds,wrenches,config,stage,radius,problem,np.asarray(reply.x),dual,upper)
                attempt['validation']=validation
                record=dict(stage=stage,radius=radius,x=reply.x.tolist(),dual_inequality=dual.tolist(),dual_upper_last=upper,certificate=validation)
            attempts.append(attempt)
            if record is not None and record['certificate']['valid']:break
        except Exception as exc:
            attempts.append(dict(method=method,presolve=presolve,exception=type(exc).__name__,message=str(exc),runtime_s=perf_counter()-started))
    return dict(status='verified' if record is not None and record['certificate']['valid'] else 'numerical_error',
                record=record,attempts=attempts,lp_calls=len(attempts),assembly_s=assembly,
                shape=dict(variables=len(cost),inequalities=len(rhs),equalities=0,nonzeros=matrix.nnz),
                runtime_s=perf_counter()-began,formulation='joint_all_required_scenario_blocks_one_shared_objective')


def solve_task_matrices(a,b,bounds,demands,*,config,model_identity,uncertainty=None,cache=None):
    """Numerical entry for explicit fixtures or hash-validated model matrices."""
    began=perf_counter()
    if not isinstance(config,TaskLexConfig) or not isinstance(demands,TaskDemands):raise ValueError('typed task/config required')
    _identity(model_identity)
    a,b,bounds=_arrays(a,b,bounds)
    identity=dict(version=VERSION,namespace=NAMESPACE,candidate_id=demands.candidate_id,candidate_hash=demands.candidate_hash,
        task=demands.task,method=demands.method,object_identity=demands.object_identity,common_context_identity=demands.common_context_identity,
        source_identity=demands.source_identity,reference_point_m=list(demands.reference_point_m),frame=demands.frame,
        config=asdict(config),config_identity=digest(asdict(config)),model_identity=model_identity,
        numerical_matrix_identity=digest(dict(A=a.tolist(),B=b.tolist(),b=bounds.tolist())),
        required_scenarios=[asdict(x) for x in demands.scenarios],uncertainty=uncertainty or {},
        solver_version=importlib.metadata.version('scipy'),solver_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        physical_interpretation='model-unresisted-load proxy; not strict feasibility, calibrated stability or success probability')
    identity['task_demand_signature']=digest(identity['required_scenarios'])
    key=digest(identity);identity['input_hash']=key
    if cache is not None:
        if not isinstance(cache,ExactTaskCache):raise ValueError('explicit task cache required')
        if key in cache.records:
            cached=cache.records[key]
            if not isinstance(cached,TaskLexResult) or cached.payload['input_hash']!=key:
                raise ValueError('cache identity corruption')
            cache.hits+=1;return cached
        cache.misses+=1
    if not demands.scenarios or any(s.status!='complete' for s in demands.scenarios):
        state='numerical_error' if any(s.status=='numerical_error' for s in demands.scenarios) else 'missing_parameters'
        return TaskLexResult(dict(**identity,status=state,risk=None,utilization=None,lp_calls=0,reason='all required scenarios must be complete'))
    unique,mapping=_unique(demands,config.deduplicate_exact_demands)
    first=_stage(a,b,bounds,unique,config,stage=1)
    second=None;risk=utilization=None;delta=None
    if first['status']=='verified':
        risk=first['record']['certificate']['objective_upper']
        delta=config.residual_slack_absolute+config.residual_slack_relative*abs(risk)
        second=_stage(a,b,bounds,unique,config,stage=2,radius=risk+delta)
        if second['status']=='verified':utilization=second['record']['certificate']['objective_upper']
    complete=first['status']=='verified' and second is not None and second['status']=='verified'
    result=TaskLexResult(dict(**identity,status='scored_conditional' if complete else 'numerical_error',
        risk=risk if complete else None,utilization=utilization if complete else None,
        risk_interval=first['record']['certificate']['objective_interval'] if first['status']=='verified' else None,
        utilization_interval=second['record']['certificate']['objective_interval'] if complete else None,
        delta_scaled=delta,stage1=first,stage2=second,unique_required_wrenches=unique.tolist(),original_to_unique=mapping,
        scenario_count=len(demands.scenarios),unique_scenario_count=len(unique),exact_duplicate_count=len(mapping)-len(unique),
        per_original_scenario_residual_stage2=[second['record']['certificate']['per_unique_scenario_residual'][i] for i in mapping] if complete else None,
        lp_calls=first['lp_calls']+(second['lp_calls'] if second else 0),runtime_s=perf_counter()-began,
        model_status='conditional_predicted_contacts_only',actual_physical_feasibility='not_established'))
    if cache is not None:cache.records[key]=result
    return result


def solve_task(data,constraints,demands,*,config,uncertainty=None,cache=None):
    validate_constraints(data,constraints)
    if data.candidate_id!=demands.candidate_id or data.candidate_hash!=demands.candidate_hash or tuple(data.reference_point_m)!=demands.reference_point_m:
        raise ValueError('candidate/reference identity mismatch')
    if constraints.status is ConstraintStatus.MISSING_PHYSICS_PARAMETERS:
        return abstain_task(demands,config,status='missing_parameters',reason=';'.join(constraints.missing_parameters),uncertainty=uncertainty)
    return solve_task_matrices(data.matrix,constraints.matrix,constraints.bounds_n,demands,config=config,
        model_identity=digest(dict(primitive=constraints.primitive_fingerprint,capacity=constraints.to_dict())),uncertainty=uncertainty,cache=cache)


def abstain_task(demands,config,*,status,reason,uncertainty=None):
    if status not in ('geometry_invalid','geometry_unsupported','geometry_unknown','missing_parameters','numerical_error'):
        raise ValueError('explicit abstention status required')
    return TaskLexResult(dict(version=VERSION,namespace=NAMESPACE,candidate_id=demands.candidate_id,candidate_hash=demands.candidate_hash,
        task=demands.task,method=demands.method,object_identity=demands.object_identity,common_context_identity=demands.common_context_identity,
        source_identity=demands.source_identity,frame=demands.frame,reference_point_m=list(demands.reference_point_m),
        config=asdict(config),config_identity=digest(asdict(config)),task_demand_signature=digest([asdict(s) for s in demands.scenarios]),
        status=status,risk=None,utilization=None,lp_calls=0,uncertainty=uncertainty or {},reason=reason))
