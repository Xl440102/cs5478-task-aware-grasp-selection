"""D2 independent single-scenario LPs, never D1 joint builders/solutions.

Capacity rows precede residual rows. Each LP has its OWN lambda and scalar;
Stage2 receives only the declared common radius, not the joint solution.
"""
from dataclasses import asdict
import hashlib
import importlib.metadata
from pathlib import Path
from time import perf_counter
import warnings

import numpy as np
from scipy.optimize import linprog

from .contact_regions import digest

VERSION='independent_single_scenario_linf_oracle_v1'


def independent_scenario(a,b,budget,wrench,*,config,stage,radius=None,source_identity):
    start=perf_counter()
    a,b,budget,w=(np.array(x,dtype=float,copy=True) for x in (a,b,budget,wrench))
    if a.ndim!=2 or a.shape[0]!=6 or b.ndim!=2 or b.shape[1]!=a.shape[1] or budget.shape!=(len(b),) or w.shape!=(6,):
        raise ValueError('oracle physical input shape mismatch')
    if any(not np.isfinite(x).all() for x in (a,b,budget,w)) or np.any(b<0) or np.any(budget<0) or np.any(b.sum(axis=0)<=0):
        raise ValueError('finite bounded original model required')
    if stage not in (1,2) or stage==2 and (radius is None or not np.isfinite(radius) or radius<0):
        raise ValueError('explicit valid stage/radius required')
    if not isinstance(source_identity,str) or len(source_identity)!=64:raise ValueError('source identity required')
    scale=np.r_[np.ones(3)/config.reference_force_n,np.ones(3)/(config.reference_force_n*config.characteristic_length_m)]
    c=a*scale[:,None];d=w*scale;n=a.shape[1];objective=np.r_[np.zeros(n),1.]
    # Independently arranged row order; no joint variables or joint witnesses.
    if stage==1:
        matrix=np.vstack((np.column_stack((b,np.zeros(len(budget)))),np.column_stack((c,-np.ones(6))),np.column_stack((-c,-np.ones(6)))))
        rhs=np.r_[budget,d,-d];bounds=[(0,None)]*(n+1)
    else:
        matrix=np.vstack((np.column_stack((b,-budget)),np.column_stack((c,np.zeros(6))),np.column_stack((-c,np.zeros(6)))))
        rhs=np.r_[np.zeros(len(budget)),d+radius,-d+radius];bounds=[(0,None)]*n+[(0,1)]
    options=dict(presolve=False,primal_feasibility_tolerance=config.backend_feasibility,
                 dual_feasibility_tolerance=config.backend_feasibility,ipm_optimality_tolerance=config.backend_feasibility,
                 small_matrix_value=config.backend_small_matrix_value)
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore',message=r"Unrecognized options detected: \{'small_matrix_value': .*\}\. These will be passed to HiGHS verbatim\.")
        reply=linprog(objective,A_ub=matrix,b_ub=rhs,bounds=bounds,method='highs-ipm',options=options)
    result=dict(version=VERSION,source_identity=source_identity,stage=stage,radius=radius,config=asdict(config),
        solver='highs-ipm',options=options,solver_status=int(reply.status),message=str(reply.message),
        scipy_version=importlib.metadata.version('scipy'),matrix_identity=digest(dict(A=a.tolist(),B=b.tolist(),b=budget.tolist())),
        required_wrench=w.tolist(),variables=n+1,inequalities=len(rhs),equalities=None,
        status='uncertain',lp_calls=1)
    if reply.success and reply.x is not None:
        x=np.asarray(reply.x);lam=x[:-1];dual=np.asarray(reply.ineqlin.marginals);upper_dual=float(reply.upper.marginals[-1]) if stage==2 else 0.
        residual=a@lam-w;scaled=residual*scale;r=float(np.max(np.abs(scaled)));usage=b@lam
        recomputed=max(float(x[-1]),r) if stage==1 else max(float(x[-1]),max((float(usage[i]/v) for i,v in enumerate(budget) if v>0),default=0.))
        # Conservative dual lower bound over the bounded ORIGINAL polytope.
        y=np.minimum(dual,0.);beta=min(upper_dual,0.) if stage==2 else 0.
        if stage==1 and matrix[:,-1]@y>1.:y=y/float(matrix[:,-1]@y)
        reduced=matrix.T@y+np.r_[np.zeros(n),beta]-objective
        caps=np.min(np.divide(budget[:,None],b,out=np.full(b.shape,np.inf),where=b>0),axis=0)
        correction=float(caps@np.maximum(reduced[:-1],0.))+(max(0.,float(reduced[-1])) if stage==2 else 0.)
        lower=float(rhs@y+beta-correction)
        gap=recomputed-lower
        primal_violation=float(np.maximum(matrix@x-rhs,0).max())
        nonnegative=max(0.,float(-x.min()));bound_violation=max(0.,float(x[-1]-1),float(recomputed-1)) if stage==2 else 0.
        capacity_violation=float(np.maximum(usage-(budget if stage==1 else x[-1]*budget),0).max())
        dual_violation=max(0.,float(dual.max()),float((matrix.T@dual+np.r_[np.zeros(n),upper_dual]-objective).max()),upper_dual if stage==2 else 0.)
        eps=config.primal_absolute+config.relative_tolerance*max(1.,float(np.max(np.abs(rhs))))
        gap_allowance=config.objective_gap_absolute+config.relative_tolerance*max(1.,abs(lower),abs(recomputed))
        valid=(np.isfinite(x).all() and np.isfinite(dual).all() and primal_violation<=eps and nonnegative<=config.primal_absolute and
               bound_violation<=config.primal_absolute+config.relative_tolerance and
               dual_violation<=config.dual_absolute+config.relative_tolerance and -gap_allowance<=gap<=gap_allowance)
        result.update(status='verified' if valid else 'uncertain',x=x.tolist(),dual_inequality=dual.tolist(),dual_upper=upper_dual,
            nominal_objective=float(reply.fun),recomputed_objective=float(recomputed),objective_interval=[max(0.,min(lower,recomputed)),float(recomputed)],
            conservative_dual_bound=lower,dual_correction=correction,optimality_gap=gap,gap_allowance=gap_allowance,
            inequality_violation=primal_violation,nonnegativity_violation=nonnegative,capacity_violation_n=capacity_violation,
            dual_violation=dual_violation,bound_violation=bound_violation,model_residual_force_n=residual[:3].tolist(),
            model_residual_torque_nm=residual[3:].tolist(),realized_scaled_residual=r,primal_allowance=eps)
    result['runtime_s']=perf_counter()-start;result['record_hash']=digest(result)
    return result


class IndependentOracleCache:
    """Explicit immutable-summary cache; source/stage/k/capacity/config bound."""
    def __init__(self):self.records={};self.hits=0;self.misses=0

    def solve(self,a,b,budget,wrench,*,config,stage,radius,source_identity,model_identity,writer=None):
        identity=dict(namespace=VERSION,source=source_identity,model=model_identity,stage=stage,radius=radius,
            wrench_hex=[float(v).hex() for v in wrench],config=asdict(config),scipy=importlib.metadata.version('scipy'),
            code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            matrices=digest(dict(A=np.asarray(a).tolist(),B=np.asarray(b).tolist(),b=np.asarray(budget).tolist())))
        key=digest(identity)
        if key in self.records:
            summary=self.records[key]
            if summary.get('input_hash')!=key or summary.get('checksum')!=digest({k:v for k,v in summary.items() if k!='checksum'}):
                raise ValueError('independent oracle cache corruption')
            self.hits+=1;return dict(summary)
        record=independent_scenario(a,b,budget,wrench,config=config,stage=stage,radius=radius,source_identity=source_identity)
        record['cache_input_hash']=key
        if writer:writer(record)
        summary={k:v for k,v in record.items() if k not in ('x','dual_inequality','required_wrench','config','options')}
        summary['input_hash']=key;summary['checksum']=digest(summary)
        if key in self.records:raise ValueError('refuse overwrite of oracle cache')
        self.records[key]=dict(summary);self.misses+=1
        return dict(summary)
