# Success rate of every executed feasible candidate grouped by public label attribute.
# Usage: python3 attr.py <dir under results/oracle_20261103>
import json,collections,glob,sys
R='results/oracle_20261103/'+sys.argv[1]
for t in ['transport_place','handover_transfer','pour_water']:
    print('==',t)
    stats=collections.defaultdict(lambda:[0,0])
    for s in range(0,30):
        try: f=json.load(open(f'{R}/filters/{t}/seed{s}.json'))
        except FileNotFoundError: continue
        for c in f['feasible_candidates']:
            try: e=json.load(open(f'{R}/episodes/{t}/seed{s}/{c["id"]}.json'))
            except FileNotFoundError: continue
            parts=dict(p.split('=',1) for p in c['approach'].split('; '))
            keys=[('region',c['region'])]+[(k,v.split(' (')[0]) for k,v in parts.items() if k in('from','handle','mouth','closing','side')]
            keys.append(('score0',c['geometry_score']==0))
            for k in keys:
                stats[k][0]+=bool(e['success']); stats[k][1]+=1
    for k,(a,n) in sorted(stats.items()):
        print(f'  {k[0]:8s} {str(k[1])[:40]:40s} {a:4d}/{n:<4d} {a/n:.2f}')
