"""Development Phase 1 only. There is deliberately no physical dispatch option."""
import argparse
import json
from .phase1 import run_phase1, TASKS, SEEDS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--tasks', nargs='+', choices=TASKS, default=list(TASKS))
    parser.add_argument('--seeds', nargs='+', type=int, choices=SEEDS, default=list(SEEDS))
    args = parser.parse_args()
    print(json.dumps(run_phase1(args.output_dir, tasks=tuple(args.tasks), seeds=tuple(args.seeds)), indent=2))


if __name__ == '__main__':
    main()
