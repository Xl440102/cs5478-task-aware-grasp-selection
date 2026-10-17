"""Reproducible sequence smoke/regression; not selector-comparison evidence."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from grasp_a.contracts import load_candidates
from grasp_a.sequence import run_sequence
from grasp_a.sequence_contracts import SequenceConfig, SEQUENCE_TASKS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks",nargs="+",choices=SEQUENCE_TASKS,default=list(SEQUENCE_TASKS))
    parser.add_argument("--seeds",nargs="+",type=int,default=[0,1,2])
    parser.add_argument("--samples",type=int,default=256)
    parser.add_argument("--substeps",type=int,default=2)
    parser.add_argument("--out-dir",default="results/local/sequence_validation")
    parser.add_argument("--media",action="store_true")
    parser.add_argument("--media-first",action="store_true",help="Record first seed of each task only")
    parser.add_argument("--landmarks",action=argparse.BooleanOptionalAction,
                        default=True,help="A/B video overlays; --no-landmarks records plain videos")
    args = parser.parse_args()
    directory = Path(args.out_dir)
    directory.mkdir(parents=True,exist_ok=True)
    candidates = load_candidates(Path(__file__).resolve().parents[1]/"candidates.example.json")
    config = SequenceConfig(fluid_particles=args.samples,fluid_substeps=args.substeps)
    rows = []
    for task in args.tasks:
        for seed in args.seeds:
            result = run_sequence(task,seed,"G02",candidates,config=config,
                                  media_dir=directory/"media" if args.media or
                                  (args.media_first and seed == args.seeds[0]) else None,
                                  show_landmarks=args.landmarks)
            path = directory/f"{task}_seed{seed}.json"
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
            row = dict(task_id=task,seed=seed,success=result["success"],status=result["status"],
                failure_reason=result["failure_reason"],terminal_checks=result["terminal_checks"],
                water=result["final_evidence"].get("water"),result_file=path.name)
            rows.append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
    (directory/"summary.json").write_text(json.dumps(dict(config=asdict(config),
        controller_version=result["controller_version"],evaluation_version=result["evaluation_version"],
        purpose="G02 physical smoke/regression, NOT selector comparison",trials=rows),
        ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")


if __name__ == "__main__": main()
