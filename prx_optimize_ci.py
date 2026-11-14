#!/usr/bin/env python3
"""GitHub Actions entry (SAFE v3 TURBO). Secrets wahi: PRX_SA_JSON, PRX_FOLDER_ID.
  python prx_optimize_ci.py --plan N   -> setup job: baaki files ko N jobs me barabar baanta hai (GITHUB_OUTPUT me likhta hai)
  python prx_optimize_ci.py            -> optimize job: apna hissa convert karta hai
"""
import os, sys, json
import prx_optimize as p

sa = os.environ.get('PRX_SA_JSON', '').strip()
fid = os.environ.get('PRX_FOLDER_ID', '').strip()
ex = [x.strip() for x in (os.environ.get('PRX_EXCLUDE') or 'Feature plan,CA motivation').split(',') if x.strip()]
only = [x.strip() for x in (os.environ.get('PRX_ONLY') or '').split(',') if x.strip()]
mode = (os.environ.get('PRX_MODE') or 'apply').strip()
limit = int(os.environ['PRX_LIMIT']) if (os.environ.get('PRX_LIMIT') or '').strip().isdigit() else None
if not sa or not fid:
    sys.exit('Secret set nahi hai: PRX_SA_JSON aur PRX_FOLDER_ID (Settings > Secrets and variables > Actions).')

if len(sys.argv) > 2 and sys.argv[1] == '--plan':
    n = max(1, int(sys.argv[2]))
    groups = p.plan_shards(fid, n, sa_json=sa, exclude=ex, only=only)
    assign = {i: g for g, ids in enumerate(groups) for i in ids}
    k = len(groups)
    out = {'n': str(k), 'shards': json.dumps(list(range(max(k, 1)))), 'assign': json.dumps(assign, separators=(',', ':')) if len(json.dumps(assign)) < 200000 else ''}
    print('PLAN: pending=%d jobs=%d' % (len(assign), k))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as o:
            o.write(''.join('%s=%s\n' % kv for kv in out.items()))
    sys.exit(0)

sh = (os.environ.get('PRX_SHARD') or '').strip()
shard = tuple(int(x) for x in sh.split('/')) if '/' in sh else None
assign = None
raw = (os.environ.get('PRX_ASSIGN') or '').strip()
if raw and shard and mode == 'apply':
    try:
        m = json.loads(raw)
        assign = {i for i, g in m.items() if int(g) == shard[0]}
        print('Mere hisse me %d file (size ke hisaab se barabar baanti hui)' % len(assign))
    except Exception as e:
        print('assign padh nahi paya (%s) -> purana tarika' % str(e)[:80])
ok, fixed, err = p.process_drive(fid, mode=mode, limit=limit, sa_json=sa, exclude=ex, only=only, shard=shard, assign=assign,
                                 workdir=os.path.join(os.getcwd(), 'work'))
print('RESULT mode=%s ok=%d fixed=%d err=%d' % (mode, ok, fixed, err))
sys.exit(1 if err else 0)
