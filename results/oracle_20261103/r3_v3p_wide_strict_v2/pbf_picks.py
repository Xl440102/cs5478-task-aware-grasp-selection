"""Re-run every method's pour_water pick on held-out seeds 10-29 with the default particle fluid."""
import json, time, collections
from concurrent.futures import ProcessPoolExecutor, as_completed
OUT='results/local/oracle_r3'
METHODS=['M0_RANDOM','M1_STATIC_V2','M2_TASK_RISK','M4_FAMILY_PRIOR','M5_PAPER_STYLE']
def job(args):
    seed,cid=args
    from grasp_a.contracts import load_candidates
    from grasp_a.sequence import run_sequence
    c=load_candidates(OUT+'/b_inputs/candidates.scored.json')
    t=time.time(); e=run_sequence('pour_water',seed,cid,c,scene_config='wide_strict_v2')
    w=(e.get('final_evidence') or {}).get('water') or {}
    return dict(seed=seed,candidate_id=cid,success=e['success'],failure_reason=e['failure_reason'],
                transfer=w.get('transfer_ratio'),spill=w.get('spill_ratio'),seconds=time.time()-t)
sel=json.load(open(OUT+'/selections.json'))['pour_water']
picks={}
for seed in range(10,30):
    for m in METHODS:
        s=sel.get(str(seed),{}).get(m)
        if s and 'ranking' in s: picks.setdefault((seed,s['ranking'][0]),[]).append(m)
print('unique picks',len(picks),flush=True)
rows=[]
with ProcessPoolExecutor(4) as pool:
    futs=[pool.submit(job,k) for k in picks]
    for i,f in enumerate(as_completed(futs)):
        r=f.result(); rows.append(r); print(i+1,len(futs),r['seed'],r['candidate_id'],r['success'],r['failure_reason'],flush=True)
proxy={}
for r in rows:
    ep=json.load(open(f"{OUT}/episodes/pour_water/seed{r['seed']}/{r['candidate_id']}.json"))
    r['proxy_success']=ep['success']; r['proxy_failure']=ep['failure_reason']; r['methods']=picks[(r['seed'],r['candidate_id'])]
agree=sum(r['success']==r['proxy_success'] for r in rows)
per={m:[0,0] for m in METHODS}
for r in rows:
    for m in r['methods']: per[m][0]+=r['success']; per[m][1]+=1
json.dump(dict(rows=rows,agreement=[agree,len(rows)],per_method_pbf={m:v for m,v in per.items()}),open(OUT+'/pbf_picks_seeds10-29.json','w'),indent=2)
print('agreement',agree,len(rows)); print('pbf per method',per)
