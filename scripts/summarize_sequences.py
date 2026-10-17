"""Export compact auditable evidence without checking large videos into Git."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from grasp_a.sequence_contracts import SEQUENCE_TASKS, task_spec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",default="results/local/sequence_v1/final")
    parser.add_argument("--liquid",default="results/local/sequence_v1/liquid_validation.json")
    parser.add_argument("--out",default="results/sequence_v1_20261017/summary.json")
    args = parser.parse_args()
    rows = []
    for task in SEQUENCE_TASKS:
        for seed in (0,1,2):
            file = Path(args.input)/f"{task}_seed{seed}.json"
            result = json.loads(file.read_text(encoding="utf-8"))
            # Refuse mixed definitions, instead of relabeling stale evidence.
            if result["task_spec"] != json.loads(json.dumps(task_spec(task))):
                raise ValueError(f"stale task definition: {file}")
            rows.append({k:result[k] for k in ("task_id","seed","selected_id","success",
                "status","failure_reason","elapsed_s","candidate_sha256","task_spec_sha256",
                "success_checks","terminal_checks","final_evidence","max_fluid_compression")})
    counts = {task:dict(success=sum(r["success"] for r in rows if r["task_id"]==task),trials=3)
              for task in SEQUENCE_TASKS}
    payload = dict(purpose="G02 executor smoke/regression; not a selector comparison or real-water validation",
        controller_version="staged_sequences_v1",evaluation_version="physical_outcomes_v1",
        scene_version="hollow_cup_sequences_v1",counts=counts,trials=rows,
        task_definitions={task:task_spec(task) for task in SEQUENCE_TASKS},
        fluid_only_validation=json.loads(Path(args.liquid).read_text(encoding="utf-8")),
        full_raw_results=args.input,raw_results_are_local=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(counts),flush=True)


if __name__ == "__main__": main()
