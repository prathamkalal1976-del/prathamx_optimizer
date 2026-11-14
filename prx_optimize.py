#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PrathamX Video Optimizer
========================
Kaam: lecture videos ko aisa banata hai ki PrathamX app ke apne player me HAR phone par turant start ho.
App ka player (browser) sirf  MP4 + H.264 video + AAC audio + "fast start" (moov file ke shuru me)  pakka chalata hai.
Ye script har video ko check karta hai aur sirf jarurat ho to hi badalta hai:

  pehle se theek      -> kuch nahi karta (skip)
  sirf index peeche   -> sirf remux (-c copy, bahut tez, quality same)       [moov end me]
  audio AC3/etc.      -> video copy, sirf audio AAC
  HEVC/H.265/MKV/...  -> H.264 me convert (CRF 23, 720p tak)

Drive mode me file ko USI file ID par replace karta hai (app ke links nahi tootte, purana version Keep-forever ho jaata hai).

SAFE VERSION (v2): .ts/.mkv jaisi non-MP4 file ka video kabhi 'copy' nahi hota (wahi green-frame ka kaaran tha), hamesha re-encode.
Har output replace se pehle VERIFY hota hai: codec, duration, faststart, poori file decode, aur 16 frames source se match.
Verify fail = Drive me kuch nahi badalta.
Default DRY-RUN hai: pehle sirf batata hai kya karega. Asli kaam ke liye go=True / --go.

Colab me (recommended):   notebook "PrathamX_Video_Optimizer.ipynb" kholo.
PC par local folder:      python prx_optimize.py --local "D:/lectures" [--go]
"""
import os, sys, re, json, subprocess, shutil, tempfile, argparse, time

VIDEO_EXT = ('.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v', '.3gp', '.ts', '.flv', '.wmv')
BAD_PROFILES = ('High 10', 'High 4:2:2', 'High 4:4:4 Predictive', 'High 4:4:4')
OK_BRANDS = ('isom', 'iso2', 'iso4', 'iso5', 'iso6', 'mp41', 'mp42', 'avc1', 'M4V ', 'dash')


def run(cmd, **kw):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def have(tool):
    return shutil.which(tool) is not None


# ---------- MP4 box reader: moov shuru me hai ya end me? (sirf header padhta hai) ----------
def u32(b, o): return int.from_bytes(b[o:o + 4], 'big')


def mp4_layout(read):
    """read(start, end_inclusive) -> bytes. Return (brand, moov_first, total) ya (None,None,total) agar MP4 nahi."""
    head = read(0, 31)
    if len(head) < 12 or head[4:8] != b'ftyp':
        return None, None, 0
    brand = head[8:12].decode('latin1')
    off, moov_first, hops = 0, None, 0
    while hops < 12:
        hops += 1
        h = head if off == 0 else read(off, off + 31)
        if len(h) < 8:
            break
        size, typ = u32(h, 0), h[4:8]
        if size == 1 and len(h) >= 16:
            size = int.from_bytes(h[8:16], 'big')
        if typ == b'moov':
            if moov_first is None: moov_first = True
            break
        if typ == b'mdat':
            if moov_first is None: moov_first = False
        if size < 8:
            break
        off += size
    return brand, moov_first, off


def local_reader(path):
    f = open(path, 'rb')
    def read(s, e):
        f.seek(s); return f.read(e - s + 1)
    return read


# ---------- ffprobe + plan ----------
def probe(src, headers=None):
    cmd = ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams']
    if headers: cmd += ['-headers', headers]
    r = run(cmd + [src])
    if r.returncode != 0:
        raise RuntimeError('ffprobe fail: ' + r.stderr.strip()[:200])
    return json.loads(r.stdout)


def make_plan(info, brand, moov_first):
    vs = [s for s in info['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    as_ = [s for s in info['streams'] if s.get('codec_type') == 'audio']
    if not vs:
        return 'skip', 'video stream nahi mili'
    v, a = vs[0], (as_[0] if as_ else None)
    v_ok = (v.get('codec_name') == 'h264' and v.get('pix_fmt', 'yuv420p') in ('yuv420p', 'yuvj420p')
            and v.get('profile', '') not in BAD_PROFILES)
    a_ok = a is None or a.get('codec_name') == 'aac'
    c_ok = brand in OK_BRANDS and moov_first is True
    desc = 'video=%s audio=%s %s' % (v.get('codec_name'), a.get('codec_name') if a else '-',
                                     ('mp4,faststart' if c_ok else ('mp4,moov-end' if brand else 'non-mp4')))
    if v_ok and a_ok and c_ok: return 'skip', desc + ' -> pehle se theek'
    if brand not in OK_BRANDS and brand != 'qt  ':   # .ts/.mkv/.avi...: video copy NAHI, hamesha re-encode
        return 'transcode', desc + ' [non-mp4: re-encode]'
    if v_ok and a_ok: return 'remux', desc
    if v_ok: return 'audio', desc
    return 'transcode', desc


def build_cmd(src, dst, plan, vinfo_w):
    c = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-fflags', '+genpts', '-i', src, '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn',
         '-map_metadata', '-1']
    if plan == 'remux':
        c += ['-c', 'copy', '-tag:v', 'avc1']
    elif plan == 'audio':
        c += ['-c:v', 'copy', '-tag:v', 'avc1', '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-af', 'aresample=async=1:first_pts=0']
    else:
        c += ['-c:v', 'libx264', '-preset', os.environ.get('PRX_PRESET', 'ultrafast'), '-crf', os.environ.get('PRX_CRF', '18'), '-pix_fmt', 'yuv420p', '-profile:v', 'high',
              '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-af', 'aresample=async=1:first_pts=0']
        mw = int(os.environ.get('PRX_MAX_W', '0') or 0)   # 0 = resolution bilkul wahi (koi chhota nahi)
        if mw and vinfo_w and vinfo_w > mw:
            c += ['-vf', "scale='min(%d,iw)':-2" % mw]
    c += ['-max_muxing_queue_size', '4096', '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', dst]
    return c


def convert(src, dst, plan, info, headers=None):
    dur = float(info['format'].get('duration') or 0)
    vw = next((s.get('width') for s in info['streams'] if s.get('codec_type') == 'video'), 0)
    cmd = build_cmd(src, dst, plan, vw)
    if headers:  # http source with auth header
        cmd[cmd.index('-i'):cmd.index('-i')] = ['-headers', headers]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    last = -10
    for line in p.stdout:
        if line.startswith('out_time_ms=') and dur:
            try: pct = int(line.split('=')[1]) / 1e6 / dur * 100
            except ValueError: continue
            if pct - last >= 10 and pct <= 100:
                last = pct; print('      ... %d%%' % min(99, pct), flush=True)
    err = p.stderr.read(); p.wait()
    if p.returncode != 0:
        raise RuntimeError('ffmpeg fail: ' + err.strip()[-300:])


def frame(path, t):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', '%.2f' % t, '-i', path, '-frames:v', '1', '-vf', 'scale=64:36',
                        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return r.stdout if len(r.stdout) == 64 * 36 * 3 else None


def green_frac(b):
    n = bad = 0
    for i in range(0, len(b), 3):
        n += 1
        if b[i + 1] > b[i] + 40 and b[i + 1] > b[i + 2] + 40: bad += 1
    return bad / max(n, 1)


def compare_frames(src, dst, dur, samples=16):
    bad = 0
    for k in range(samples):
        t = dur * (0.02 + 0.96 * k / (samples - 1))
        fd = frame(dst, t)
        if fd is None: raise RuntimeError('output frame nahi mila @%.0fs' % t)
        fs, gd = frame(src, t), green_frac(fd)
        if fs is None:
            bad += gd > 0.5; continue
        diff = sum(abs(x - y) for x, y in zip(fs, fd)) / len(fs)
        if (gd > 0.5 and green_frac(fs) < 0.5) or diff > 40: bad += 1
    if bad > 2: raise RuntimeError('frames source se match nahi (%d/%d kharab)' % (bad, samples))


def verify(dst, info_in, src=None):
    info = probe(dst)
    brand, mf, _ = mp4_layout(local_reader(dst))
    plan, desc = make_plan(info, brand, mf)
    d_in, d_out = float(info_in['format'].get('duration') or 0), float(info['format'].get('duration') or 0)
    if plan != 'skip':
        raise RuntimeError('output verify fail (%s: %s)' % (plan, desc))
    if d_in and d_out < d_in - max(3.0, d_in * 0.01):   # chhota = content kata. (lamba ho sakta hai: joda hua .ts ka probe adhoora padhta hai)
        raise RuntimeError('output chhota hai: %.1fs vs %.1fs' % (d_out, d_in))
    if d_in and d_out > d_in + max(3.0, d_in * 0.01): print('   note: output (%.0fs) source-probe (%.0fs) se lamba, joda hua .ts ho sakta hai' % (d_out, d_in))
    r = run(['ffmpeg', '-v', 'error', '-xerror', '-i', dst, '-f', 'null', '-'])   # poori file decode
    if r.returncode or r.stderr.strip(): raise RuntimeError('decode error: ' + r.stderr.strip()[:200])
    if src and d_out: compare_frames(src, dst, d_out)
    return desc


def extract_id(s):
    """Poora Drive link ya sirf ID, dono chalte hain (?usp=drive_link jaisa hissa hata deta hai)."""
    s = (s or '').strip().strip('\'"')
    m = re.search(r'/folders/([A-Za-z0-9_-]+)', s) or re.search(r'[?&]id=([A-Za-z0-9_-]+)', s)
    return m.group(1) if m else re.split(r'[?&#/]', s)[0]


# ---------- Drive layer (Colab) ----------
class Drive:
    SCOPE = 'https://www.googleapis.com/auth/drive'

    def __init__(self, sa_json=None):
        from google.auth.transport.requests import AuthorizedSession
        from googleapiclient.discovery import build
        sa = sa_json
        if not sa:  # Colab Secrets (left sidebar ki chaabi wala icon): naam PRX_SA_JSON
            try:
                from google.colab import userdata
                sa = userdata.get('PRX_SA_JSON')
            except Exception:
                sa = None
        if sa:  # POPUP-FREE: Service Account (Editor access chahiye)
            from google.oauth2 import service_account
            info = json.loads(sa) if isinstance(sa, str) else sa
            self.creds = service_account.Credentials.from_service_account_info(info, scopes=[self.SCOPE])
            self.who = 'Service Account: ' + info.get('client_email', '?')
        else:  # Google popup login
            import google.auth
            try:
                from google.colab import auth
                auth.authenticate_user()
            except ImportError:
                pass
            except Exception as e:
                sys.exit('\nGoogle login popup fail hua: %s\n\n'
                         'Popup wala login nahi chal raha to Service Account tarika use karo (README me "Tarika A2"): '
                         'Colab Secrets me PRX_SA_JSON daalo aur folder SA email ko EDITOR share karo.' % str(e)[:200])
            self.creds, _ = google.auth.default(scopes=[self.SCOPE])
            self.who = 'Google login'
        self.svc = build('drive', 'v3', credentials=self.creds, cache_discovery=False)
        self.sess = AuthorizedSession(self.creds)

    def folder_check(self, fid):
        try:
            r = self.svc.files().get(fileId=fid, fields='name,capabilities/canEdit', supportsAllDrives=True).execute()
        except Exception as e:
            sys.exit('Folder nahi dikha (%s). Folder ID sahi hai? Aur is login (%s) ko folder share hai?' % (str(e)[:120], self.who))
        ce = r.get('capabilities', {}).get('canEdit', False)
        print('Login: %s\nFolder: %s  |  edit permission: %s' % (self.who, r['name'], 'HAAN' if ce else 'NAHI (sirf Viewer)'))
        return ce

    def walk(self, folder, path=''):
        tok = None
        while True:
            r = self.svc.files().list(q="'%s' in parents and trashed=false" % folder, pageSize=200, pageToken=tok,
                                      fields='nextPageToken,files(id,name,mimeType,size,properties,capabilities/canEdit)',
                                      supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            for f in r.get('files', []):
                if f['mimeType'] == 'application/vnd.google-apps.folder':
                    yield from self.walk(f['id'], path + f['name'] + '/')
                elif f['mimeType'].startswith('video/') or f['name'].lower().endswith(VIDEO_EXT):
                    f['path'] = path + f['name']; yield f
            tok = r.get('nextPageToken')
            if not tok: break

    def url(self, fid): return 'https://www.googleapis.com/drive/v3/files/%s?alt=media&supportsAllDrives=true' % fid

    def headers(self):
        from google.auth.transport.requests import Request
        if not self.creds.valid: self.creds.refresh(Request())
        return 'Authorization: Bearer %s\r\n' % self.creds.token

    def reader(self, fid):
        def read(s, e):
            r = self.sess.get(self.url(fid), headers={'Range': 'bytes=%d-%d' % (s, e)})
            return r.content if r.status_code in (200, 206) else b''
        return read

    def download(self, fid, path):
        from googleapiclient.http import MediaIoBaseDownload
        with open(path, 'wb') as fh:
            d = MediaIoBaseDownload(fh, self.svc.files().get_media(fileId=fid, supportsAllDrives=True), chunksize=128 * 1024 * 1024)
            done = False
            while not done: _, done = d.next_chunk(num_retries=5)

    def revisions(self, fid):
        return self.svc.revisions().list(fileId=fid, fields='revisions(id,modifiedTime,size)').execute().get('revisions', [])

    def download_rev(self, fid, rid, path):
        from googleapiclient.http import MediaIoBaseDownload
        with open(path, 'wb') as fh:
            d = MediaIoBaseDownload(fh, self.svc.revisions().get_media(fileId=fid, revisionId=rid), chunksize=128 * 1024 * 1024)
            done = False
            while not done: _, done = d.next_chunk(num_retries=5)

    def keep(self, fid, rid):
        try: self.svc.revisions().update(fileId=fid, revisionId=rid, body={'keepForever': True}).execute()
        except Exception as e: print('   warning: keepForever nahi hua:', str(e)[:100])

    def mark_fail(self, fid, size):   # fail hui file har run me dobara na chale (file badlegi to size badlega, tab phir try)
        try: self.svc.files().update(fileId=fid, body={'properties': {'prxFail': str(size)}}, supportsAllDrives=True, fields='id').execute()
        except Exception as e: print('   (fail-marker nahi laga: %s)' % str(e)[:80])

    def replace(self, fid, path, new_name=None):
        from googleapiclient.http import MediaFileUpload
        try:   # replace se pehle current (original) version hamesha ke liye save
            revs = self.revisions(fid)
            if revs: self.svc.revisions().update(fileId=fid, revisionId=revs[-1]['id'], body={'keepForever': True}).execute()
        except Exception as e: print('   warning: original keepForever nahi hua:', str(e)[:120])
        body = {'mimeType': 'video/mp4', 'properties': {'prxConv': '2', 'prxFail': None}}
        if new_name: body['name'] = new_name
        m = MediaFileUpload(path, mimetype='video/mp4', resumable=True, chunksize=128 * 1024 * 1024)
        r = self.svc.files().update(fileId=fid, body=body, media_body=m, supportsAllDrives=True, fields='id,size').execute(num_retries=5)
        if int(r.get('size', -1)) != os.path.getsize(path): raise RuntimeError('upload size mismatch')


def fmt_mb(n): return '%.0f MB' % (int(n) / 1048576) if n else '?'


def fix_one(src, info, plan, workdir):
    """convert + verify. remux/audio fail ho to re-encode try karta hai. Dono fail = error (Drive untouched)."""
    dst, last = os.path.join(workdir, 'out.mp4'), None
    for pl in ([plan, 'transcode'] if plan != 'transcode' else ['transcode']):
        try:
            print('   %s...' % pl); convert(src, dst, pl, info)
            return dst, verify(dst, info, src)
        except Exception as e:
            last = e; print('   %s fail: %s' % (pl, str(e)[:200]))
            if os.path.exists(dst): os.remove(dst)
    raise last


def process_drive(folder_id, mode='plan', limit=None, workdir='/content/prx_work', sa_json=None, exclude=None, only=None, budget_min=300, shard=None):
    """mode: plan = sirf batao | test = convert+verify, Drive untouched | apply = replace | recover = purane version se wapas banao (only zaroori)"""
    for t in ('ffmpeg', 'ffprobe'):
        if not have(t): sys.exit(t + ' nahi mila.')
    folder_id = extract_id(folder_id)
    if not folder_id or 'YAHAN' in folder_id.upper(): sys.exit('FOLDER_ID khaali hai.')
    only = [x.strip().lower() for x in (only or []) if x.strip()]
    if mode == 'recover' and not only: sys.exit('recover mode me "only" (file ka naam) dena zaroori hai.')
    os.makedirs(workdir, exist_ok=True)
    dr = Drive(sa_json); can_edit = dr.folder_check(folder_id)
    if mode in ('apply', 'recover') and not can_edit: sys.exit('Sirf Viewer access hai. Folder ko Service Account ko EDITOR share karo.')
    if mode == 'test' and limit is None: limit = 1
    ex = [x.lower() for x in (exclude or []) if x]
    n_fix = n_ok = n_err = 0; rows = []; t_all = time.time()
    print('Folder scan ho raha hai...\n')
    idx = -1
    for f in sorted(dr.walk(folder_id), key=lambda x: x['path']):
        pl = f['path'].lower()
        if ex and any(x in pl for x in ex): continue
        if only and not any(x in pl for x in only): continue
        idx += 1
        if shard and idx % shard[1] != shard[0]: continue   # parallel job: har job apna hissa leta hai
        if mode == 'apply' and f.get('properties', {}).get('prxFail') == str(f.get('size')):
            print('SKIP', f['path'], '| pehle verify fail hui thi (file badlegi tab dobara). Haath se: mode=test/recover'); continue
        if limit is not None and n_fix >= limit: print('limit pura (%d). Baaki baad me.' % limit); break
        tag = '%s  [%s]' % (f['path'], fmt_mb(f.get('size')))
        src, dst = os.path.join(workdir, 'in.bin'), os.path.join(workdir, 'out.mp4')
        try:
            if mode == 'recover':
                revs = dr.revisions(f['id'])
                if len(revs) < 2: print('?  ', tag, '-> purana version nahi mila'); n_err += 1; continue
                print('>> ', tag, '| RECOVER (purane version se)'); t0 = time.time()
                dr.keep(f['id'], revs[-2]['id']); dr.download_rev(f['id'], revs[-2]['id'], src)
                info = probe(src); brand, mf, _ = mp4_layout(local_reader(src)); plan, desc = make_plan(info, brand, mf)
                if plan == 'skip': dst, v = src, desc
                else: dst, v = fix_one(src, info, plan, workdir)
            else:
                info = probe(dr.url(f['id']), dr.headers())
                brand, mf, _ = mp4_layout(dr.reader(f['id'])); plan, desc = make_plan(info, brand, mf)
                if plan == 'skip': print('OK', tag, '|', desc); n_ok += 1; continue
                print('>> ', tag, '|', desc, '=> %s' % plan.upper())
                if mode == 'plan': n_fix += 1; continue
                est = float(info['format'].get('duration') or 0) / 60 / (2 if plan == 'transcode' else 20)   # re-encode ~2x realtime maan ke
                if mode == 'apply' and (time.time() - t_all) / 60 + est > budget_min: print('   time kam hai (~%.0f min chahiye), agle run me' % est); continue
                if not f.get('capabilities', {}).get('canEdit', True) and mode != 'test': print('   SKIP: edit permission nahi'); n_err += 1; continue
                t0 = time.time(); print('   download...'); dr.download(f['id'], src)
                dst, v = fix_one(src, info, plan, workdir)
            print('   verify ok:', v)
            if mode == 'test':
                print('   TEST-OK (Drive me kuch nahi badla)'); rows.append((f['path'], 'TEST-OK', plan)); n_fix += 1; continue
            print('   Drive me wapas (same file ID)...')
            nm = f['name'] if f['name'].lower().endswith('.mp4') else os.path.splitext(f['name'])[0] + '.mp4'
            dr.replace(f['id'], dst, nm if nm != f['name'] else None)
            print('   DONE (%.0f min)' % ((time.time() - t0) / 60)); rows.append((f['path'], 'DONE', plan)); n_fix += 1
        except Exception as e:
            print('   FAIL (Drive me kuch nahi badla):', str(e)[:300]); rows.append((f['path'], 'FAIL', str(e)[:120])); n_err += 1
            if mode == 'apply': dr.mark_fail(f['id'], f.get('size'))
        finally:
            for p in (src, dst):
                if os.path.exists(p): os.remove(p)
    print('\nSummary: theek-pehle-se=%d, %s=%d, error=%d' % (n_ok, 'badli' if mode in ('apply', 'recover') else 'check/badalni', n_fix, n_err))
    if rows and os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as o:
            o.write('## PrathamX Video Optimizer (%s)\n| File | Status | Detail |\n|---|---|---|\n' % mode + '\n'.join('| %s | %s | %s |' % r for r in rows) + '\n')
    return n_ok, n_fix, n_err


# ---------- Local folder mode (PC) ----------
def process_local(folder, go=False, inplace=False):
    out_root = folder if inplace else os.path.join(folder, 'optimized')
    n = 0
    for root, _, files in os.walk(folder):
        if os.path.abspath(root).startswith(os.path.abspath(os.path.join(folder, 'optimized'))): continue
        for name in sorted(files):
            if not name.lower().endswith(VIDEO_EXT): continue
            p = os.path.join(root, name)
            info = probe(p); brand, mf, _ = mp4_layout(local_reader(p)); plan, desc = make_plan(info, brand, mf)
            if plan == 'skip': print('OK ', p, '|', desc); continue
            print('>> ', p, '|', desc, '=> %s' % plan.upper())
            if not go: continue
            rel = os.path.relpath(os.path.splitext(p)[0] + '.mp4', folder); dst = os.path.join(out_root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + '.part.mp4'
            wd = tempfile.mkdtemp(); out, v = fix_one(p, info, plan, wd); print('   verify ok:', v)
            shutil.move(out, tmp); shutil.rmtree(wd, ignore_errors=True)
            os.replace(tmp, dst); n += 1; print('   ->', dst)
    print('\nDone. %d file(s) badli.' % n)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='PrathamX Video Optimizer')
    ap.add_argument('--local', help='PC folder jisme videos hain')
    ap.add_argument('--drive-folder', help='Drive folder ID (Colab me)')
    ap.add_argument('--go', action='store_true', help='asli kaam (default: sirf dry-run)')
    ap.add_argument('--inplace', action='store_true', help='local mode: original ki jagah likho')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--mode', default='plan', choices=['plan', 'test', 'apply', 'recover'])
    ap.add_argument('--only', default='')
    a = ap.parse_args()
    if a.local: process_local(a.local, a.go, a.inplace)
    elif a.drive_folder: process_drive(a.drive_folder, 'apply' if a.go else a.mode, a.limit, only=a.only.split(','))
    else: ap.print_help()
