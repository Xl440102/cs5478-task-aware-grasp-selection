# Re-score the real M3/A1 picks (results/c_evaluation_20261106) against another oracle table.
# Usage: python3 an.py <dir under results/oracle_20261103>
import json,collections,sys
R='results/oracle_20261103/'+sys.argv[1]
S=json.load(open('results/oracle_20261103/r3_v3p_wide_strict_v2/selections.json'))
def ep(t,s,c):
    try: return json.load(open(f'{R}/episodes/{t}/seed{s}/{c}.json'))
    except FileNotFoundError: return None
for t in ['transport_place','handover_transfer','pour_water']:
    print('==',t)
    for m in ['M3_CODEX','A1_CODEX']:
        same=0; maxs=0; succ=0; fails=collections.Counter(); fam=collections.Counter()
        for s in range(10,30):
            d=json.load(open(f'results/c_evaluation_20261106/trials/{m}/{t}/seed{s}.json'))
            pub=json.loads(d['messages'][1]['content'])['candidates']
            sc={c['id']:c['geometry_score'] for c in pub}
            reg={c['id']:c for c in pub}
            pick=d['selection']['selected_id'] if 'selected_id' in d['selection'] else d['selection']['ranking'][0]
            m1=S[t][str(s)]['M1_STATIC_V2']['ranking'][0]
            same+= pick==m1; maxs+= sc[pick]==max(sc.values())
            e=ep(t,s,pick)
            if e is None: fails['missing']+=1; continue
            succ+=bool(e['success'])
            if not e['success']: fails[(e['failure_reason'] or e['status'])[:40]]+=1
        print(m,'succ',succ,'=M1pick',same,'pick has max score',maxs, dict(fails))
