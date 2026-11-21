"""Read-only Phase2 preflight/request CLI. No execution or approval command."""
import argparse
import json
from pathlib import Path
from .phase2 import load_frozen,prepare_manifests,inspect_batch,authorization_request
from grasp_b.wrench_v2.episode_store import EpisodeStore


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--store-root',required=True,help='inspect only; never created by this command')
    args=parser.parse_args()
    target=Path(args.output_dir)
    if target.exists():raise FileExistsError('new output required')
    bundle=load_frozen();canary,remainder=prepare_manifests(bundle)
    inspections={m['batch_id']:inspect_batch(m,args.store_root) for m in (canary,remainder)}
    store=EpisodeStore(target)
    for n,p in [('phase1_validation.json',bundle['validation']),('canary_manifest.json',canary),('remainder_manifest.json',remainder),
        ('authorization_request.json',{m['batch_id']:authorization_request(m) for m in (canary,remainder)}),
        ('recovery_audit.json',inspections),('physical_identity.json',{k:v for m in (canary,remainder) for k,v in m['episodes'].items()}),
        ('source_asset_dependency_manifest.json',dict(adapter_sources=canary['adapter_sources'],physical_resources=bundle['protocol']['physical_config']['resources']))]:
        store.write_once(n,p)
    print(json.dumps(dict(preflight_generated=True,physical_execution_authorized=False,canary_episodes=3,remainder_episodes=17),indent=2))


if __name__=='__main__':main()
