"""Fluid-only numerical sensitivity check, NOT robot/task success evidence.

Prescribes an analytic cup motion to isolate retention/open-rim flow. Does
not create rigid beads, spawn/delete liquid or adjust motion from mass truth.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from grasp_a.fluid import PositionBasedFluid, Vessel


def trial(count,dt):
    source = Vessel((.55,.115,.80),(0,0,0,1),body_id=1)
    target = Vessel((.55,.16,.645),(0,0,0,1),inner_radius=.055,outer_radius=.058,body_id=2)
    fluid = PositionBasedFluid(source,count=count,iterations=8)
    start = time.perf_counter()
    static = None
    for step in range(round(3/dt)):
        t = step*dt
        angle = math.radians(-110)*min(1,max(0,(t-.5)/1))
        source = Vessel(source.position,(math.sin(angle/2),0,0,math.cos(angle/2)),body_id=1)
        fluid.step(dt,[source,target])
        if step == round(.5/dt)-1:
            static = fluid.mass_accounting(source,target)
    final = fluid.mass_accounting(source,target)
    return dict(samples=count,dt_s=dt,iterations=8,static_check=static,final_check=final,
                maximum_compression=fluid.max_compression_seen,wall_seconds=time.perf_counter()-start)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",default="results/local/sequence_v1/liquid_validation.json")
    args = parser.parse_args()
    rows = []
    for count,dt in ((128,1/480),(256,1/480),(256,1/960),(512,1/480)):
        row = trial(count,dt)
        rows.append(row)
        print(json.dumps(row),flush=True)
    path = Path(args.out)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(dict(purpose="fluid-only imposed-motion sensitivity; not robot task score",
        fluid_version=PositionBasedFluid.version,trials=rows),indent=2,allow_nan=False)+"\n",encoding="utf-8")


if __name__ == "__main__": main()
