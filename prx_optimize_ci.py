#!/usr/bin/env python3
"""GitHub Actions entry (SAFE v2). Secrets wahi: PRX_SA_JSON, PRX_FOLDER_ID."""
import os, sys
import prx_optimize as p

sa = os.environ.get('PRX_SA_JSON', '').strip()
fid = os.environ.get('PRX_FOLDER_ID', '').strip()
ex = [x.strip() for x in (os.environ.get('PRX_EXCLUDE') or 'Feature plan,CA motivation').split(',') if x.strip()]
only = [x.strip() for x in (os.environ.get('PRX_ONLY') or '').split(',') if x.strip()]
mode = (os.environ.get('PRX_MODE') or 'apply').strip()
limit = int(os.environ['PRX_LIMIT']) if (os.environ.get('PRX_LIMIT') or '').strip().isdigit() else None
sh = (os.environ.get('PRX_SHARD') or '').strip()
shard = tuple(int(x) for x in sh.split('/')) if '/' in sh else None
if not sa or not fid:
    sys.exit('Secret set nahi hai: PRX_SA_JSON aur PRX_FOLDER_ID (Settings > Secrets and variables > Actions).')
ok, fixed, err = p.process_drive(fid, mode=mode, limit=limit, sa_json=sa, exclude=ex, only=only, shard=shard,
                                 workdir=os.path.join(os.getcwd(), 'work'))
print('RESULT mode=%s ok=%d fixed=%d err=%d' % (mode, ok, fixed, err))
sys.exit(1 if err else 0)
