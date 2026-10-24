"""Development diagnostic: test pour geometry WITHOUT liquid, never a water score."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from grasp_a.sequence import SequenceScene, SequenceConfig, p, np
from grasp_a.contracts import load_candidates

for x,height,angle in ((.55,.80,-110),(.55,.80,110),(.55,.85,110)):
    with SequenceScene("pour_water",config=SequenceConfig(target_xy=(x,.16),carry_height_m=height,pour_angle_deg=angle)) as scene:
        scene.initialize_liquid = lambda: None  # deliberate DRY diagnostic
        result = scene.run_sequence(load_candidates("candidates.example.json")[1])
        print(dict(dry_test=True,x=x,height=height,angle=angle,failure=result["failure_reason"],
            stages=[(r["id"],r["control_status"]) for r in result["stages"]],
            collision=result["collision_events"],cup=result["final_evidence"]["source_position"],
            target_displacement=result["final_evidence"]["target_displacement_m"],
            displacement_by_stage={k: v["evidence"]["endpoint"]["target_displacement_m"]
                                   for k,v in result["stage_evaluation"].items() if v["evidence"]}),flush=True)
