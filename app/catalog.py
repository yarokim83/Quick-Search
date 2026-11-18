import sqlite3,os,json,unicodedata,re,time,argparse,subprocess,sys,datetime
from pathlib import Path
BASE=Path(__file__).resolve().parent;DB=Path(os.environ.get('HPNT_DB',str(BASE/'catalog.sqlite')))
INVENTORY=BASE.parent/'HPNT_업무_파일인벤토리.json'
ROOT=Path(os.environ['HPNT_ROOT'])
BODY_EXTENSIONS = (".pdf", ".xlsx", ".xlsm", ".xls", ".txt", ".md", ".csv", ".json", ".log", ".docx")
IGNORED_PREFIXES = (
    "HPNT_Search_Windows",
    "HPNT_검색_Windows",
    "HPNT_Windows_Codex",
    "HPNT_검색프로젝트",
)
IGNORED_EXACT_DIRS = frozenset(("__pycache__", "app_update_backups", "app_backup_before_ui_update", "dist", "backups", "scripts"))
IGNORED_FILENAMES = frozenset(("desktop.ini", "thumbs.db", "hpnt_search.exe"))


def is_ignored_segment(name):
    """Return True if a path segment belongs to app bundles or OS/Office metadata."""
    if not name or name.startswith(".") or name.startswith("~$"):
        return True
    if name in IGNORED_EXACT_DIRS or name.lower() in IGNORED_FILENAMES:
        return True
    return name.startswith(IGNORED_PREFIXES)


def is_ignored_relpath(rel_path):
    """Return True if any segment in a normalized relative path should be excluded."""
    return any(is_ignored_segment(part) for part in norm(rel_path).split("/"))


def initial_status(extension, flags):
 if extension not in BODY_EXTENSIONS:
  return "unsupported"
 return "cloud" if flags & 0x40000000 else "pending"

def norm(s):return unicodedata.normalize('NFC',s.as_posix() if isinstance(s,Path) else str(s))
def clean(s):return unicodedata.normalize('NFKC',s).lower()
def grams(s):return ' '.join(sorted({'g'+s[i:i+2].encode().hex() for i in range(len(s)-1)}))
def connect():
 c=sqlite3.connect(DB,timeout=30);c.row_factory=sqlite3.Row;c.execute('pragma journal_mode=WAL');return c
def cleanup_ignored_docs(c):
 """Remove previously indexed app packages and OS/Office temporary files from the catalog."""
 if c.execute("select 1 from sqlite_master where type='table' and name='folders'").fetchone():
  stale_fids = [r['id'] for r in c.execute('select id,path from folders') if is_ignored_relpath(r['path'])]
  for i in range(0, len(stale_fids), 500):
   batch = stale_fids[i:i+500]
   ph = ','.join('?' for _ in batch)
   c.execute(f'delete from folders where id in ({ph})', batch)
 stale_ids = [r['id'] for r in c.execute('select id,path from docs') if is_ignored_relpath(r['path'])]
 if not stale_ids:
  return 0
 for i in range(0, len(stale_ids), 500):
  batch = stale_ids[i:i+500]
  ph = ','.join('?' for _ in batch)
  c.execute(f'delete from search where rowid in ({ph})', batch)
  c.execute(f'delete from chunks where doc_id in ({ph})', batch)
  c.execute(f'delete from curated_history where doc_id in ({ph})', batch)
  c.execute(f'delete from photo_aliases where source_id in ({ph}) or target_id in ({ph})', batch + batch)
  c.execute(f'delete from docs where id in ({ph})', batch)
 return len(stale_ids)
def init():
 c=connect();c.executescript('''CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY,path TEXT UNIQUE,realpath TEXT,size INTEGER,mtime INTEGER,extension TEXT,category TEXT,status TEXT,body TEXT DEFAULT '',present INTEGER DEFAULT 1,flags INTEGER DEFAULT 0,error TEXT DEFAULT '',indexed_at REAL DEFAULT 0);
 CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY,path TEXT UNIQUE,realpath TEXT,mtime INTEGER,present INTEGER DEFAULT 1);
 CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(grams);
 CREATE TABLE IF NOT EXISTS chunks(doc_id INTEGER,location TEXT,text TEXT,method TEXT);
 CREATE INDEX IF NOT EXISTS chunk_doc ON chunks(doc_id);
 CREATE TABLE IF NOT EXISTS curated_history(doc_id INTEGER,old_path TEXT,old_size INTEGER,old_mtime INTEGER,location TEXT,text TEXT,method TEXT,archived_at REAL);
 CREATE TABLE IF NOT EXISTS photo_aliases(source_id INTEGER PRIMARY KEY,target_id INTEGER NOT NULL,
   size INTEGER NOT NULL,source_mtime INTEGER NOT NULL,target_mtime INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT);''')
 placeholders = ','.join('?' for _ in BODY_EXTENSIONS)
 c.execute(f"update docs set status='unsupported' where status in ('pending','cloud') and extension not in ({placeholders})", BODY_EXTENSIONS)
 cleanup_ignored_docs(c)
 c.commit();c.close()
 if os.name=='nt':migrate_windows_paths()
def state(c,key,value):c.execute('insert or replace into state values(?,?)',(key,str(value)))
def category(path):
 for cat,p in [('자재부품','자재|부품|spare|part'),('수리교체','수리|교체|교환|고장'),('보고서','보고|report')]:
  if re.search(p,path,re.I):return cat
 return '기타'
def reindex(c,id,path,body):
 c.execute('delete from search where rowid=?',(id,));c.execute('insert into search(rowid,grams) values(?,?)',(id,grams(clean(path+'\n'+body))))
def migrate_windows_paths():
 # Earlier builds stored Windows separators in relative paths. Preserve IDs and
 # notes while normalizing paths so future scans do not create duplicates.
 c=connect();rows=[dict(r) for r in c.execute('select id,path,body from docs where instr(path,char(92))>0')];conflicts=0
 for r in rows:
  new=unicodedata.normalize('NFC',r['path'].replace(chr(92),'/'))
  if c.execute('select 1 from docs where path=? and id<>?',(new,r['id'])).fetchone():conflicts+=1;continue
  c.execute('update docs set path=? where id=?',(new,r['id']));reindex(c,r['id'],new,r['body'])
 state(c,'path_migration_conflicts',conflicts);c.commit();c.close()
def scan():
 c=connect()
 c.execute('CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY,path TEXT UNIQUE,realpath TEXT,mtime INTEGER,present INTEGER DEFAULT 1)')
 old={r['path']:dict(r) for r in c.execute('select * from docs')}
 old_folders={r['path']:dict(r) for r in c.execute('select * from folders')}
 seen=set();seen_folders=set();visited_dirs=set();errors=[];changed=0;last_commit_changed=0
 state(c,'scan_running','1');state(c,'scan_seen','0');state(c,'worker_error','');c.commit()
 def onerror(e):errors.append(str(e))
 for dp,ds,fs in os.walk(ROOT,followlinks=False,onerror=onerror):
  ds[:]=[d for d in ds if not is_ignored_segment(d) and not (Path(dp)/d).is_symlink()]
  rel_dp=norm(Path(dp).relative_to(ROOT))
  visited_dirs.add('' if rel_dp=='.' else rel_dp)
  for d in ds:
   sub=Path(dp)/d
   try:
    st_d=sub.lstat()
    rel_d=norm(sub.relative_to(ROOT));seen_folders.add(rel_d)
    rf=old_folders.get(rel_d);rp_d=str(sub)
    if not rf:
     c.execute('insert into folders(path,realpath,mtime,present) values(?,?,?,1)',(rel_d,rp_d,st_d.st_mtime_ns));changed+=1
    elif (rf['realpath'],rf['mtime'],rf['present'])!=(rp_d,st_d.st_mtime_ns,1):
     c.execute('update folders set realpath=?,mtime=?,present=1 where id=?',(rp_d,st_d.st_mtime_ns,rf['id']))
   except OSError as e:errors.append(str(e))
  for fn in fs:
   if is_ignored_segment(fn):continue
   p=Path(dp)/fn
   try:
    s=p.lstat()
    if p.is_symlink() or not p.is_file():continue
    path=norm(p.relative_to(ROOT));seen.add(path);r=old.get(path);flags=getattr(s,'st_flags',0);ext=p.suffix.lower()
    if not r:
     status=initial_status(ext, flags)
     cur=c.execute('insert into docs(path,realpath,size,mtime,extension,category,status,flags) values(?,?,?,?,?,?,?,?)',(path,str(p),s.st_size,s.st_mtime_ns,ext,category(path),status,flags));reindex(c,cur.lastrowid,path,'');changed+=1
    elif (r['size'],r['mtime'])!=(s.st_size,s.st_mtime_ns):
     c.execute("insert into curated_history select ?,?,?,?,location,text,method,? from chunks where doc_id=? and method in ('vision-title','시각 확인','제조사 도면목록 참조','파일명 및 폴더 기준','경로 및 사용자 보존 결정','apple-vision-local')",(r['id'],path,r['size'],r['mtime'],time.time(),r['id']))
     c.execute('update docs set realpath=?,size=?,mtime=?,flags=?,present=1,status=?,body=?,error=?,indexed_at=0 where id=?',(str(p),s.st_size,s.st_mtime_ns,flags,initial_status(ext, flags),'','',r['id']));c.execute('delete from chunks where doc_id=?',(r['id'],));reindex(c,r['id'],path,'');changed+=1
    else:
     status=initial_status(ext, flags) if r['status'] in ('pending', 'cloud') else r['status']
     rp=str(p)
     if (r['realpath'],r['flags'],r['present'],r['status'])!=(rp,flags,1,status):
      c.execute('update docs set realpath=?,flags=?,present=1,status=? where id=?',(rp,flags,status,r['id']))
   except OSError as e:errors.append(str(e))
   seen_count=len(seen)
   if (changed-last_commit_changed)>=100 or (seen_count and seen_count%500==0):
    state(c,'scan_seen',seen_count);c.commit();last_commit_changed=changed
 if not errors:
  for fpath,rf in old_folders.items():
   if rf['present'] and fpath not in seen_folders:
    parent_rel=fpath.rsplit('/',1)[0] if '/' in fpath else ''
    if parent_rel in visited_dirs:
     c.execute('update folders set present=0 where id=?',(rf['id'],))
 # Cloud providers can show an incomplete directory without reporting an error.
 # Keep unobserved records until their absence is independently confirmed.
 missing=sum(1 for path,r in old.items() if r['present'] and path not in seen)
 state(c,'missing_unconfirmed',missing)
 state(c,'last_scan',time.time());state(c,'scan_errors',json.dumps(errors,ensure_ascii=False));state(c,'changed',changed);state(c,'scan_seen',len(seen));state(c,'scan_running','0');c.commit();c.close();return changed

def seed():
 old=BASE.parent/'HPNT_표본_검색色인_v02.sqlite'
 if os.environ.get('HPNT_ROOT'):return
 if not old.exists():return
 c=connect()
 if c.execute("select 1 from state where key='seeded'").fetchone():c.close();return
 source=sqlite3.connect(old);groups={}
 for path,cat,loc,text,method in source.execute('select path,category,location,text,method from chunks'):groups.setdefault(path,[]).append((loc,text,method))
 original={f['path']:f for f in json.loads(INVENTORY.read_text(encoding='utf-8'))['files']}
 for path,parts in groups.items():
  r=c.execute('select * from docs where path=?',(path,)).fetchone();f=original.get(path)
  if not r or not f:continue
  timestamp=datetime.datetime.fromisoformat(f['modified']).timestamp()
  if r['size']!=f['bytes'] or abs(r['mtime']/1e9-timestamp)>.001:continue
  body='\n'.join(t for _,t,_ in parts);status='partial' if any('vision' in m for _,_,m in parts) else 'ready'
  c.executemany('insert into chunks values(?,?,?,?)',[(r['id'],loc,t,m) for loc,t,m in parts]);c.execute('update docs set body=?,status=?,indexed_at=? where id=?',(body,status,time.time(),r['id']));reindex(c,r['id'],path,body)
 state(c,'seeded',1);c.commit();c.close();source.close()

def extract(path):
 from pypdf import PdfReader
 import openpyxl
 sys.path.insert(0,str(BASE/'vendor'))
 p=Path(path);before=p.stat()
 if getattr(before,'st_flags',0)&0x40000000:return {'status':'cloud','parts':[]}
 if before.st_size>20_000_000:return {'status':'large','parts':[]}
 parts=[];partial=False;ext=p.suffix.lower();total=0
 def add(loc,text,method):
  nonlocal total,partial
  text=str(text)
  if total+len(text)>500000:partial=True;text=text[:max(0,500000-total)]
  if text.strip():parts.append((loc,text,method));total+=len(text)
 if ext=='.pdf':
  reader=PdfReader(p)
  for i,page in enumerate(reader.pages):
   t=page.extract_text() or ''
   if not t.strip():partial=True
   add(f'페이지 {i+1}',t,'pdf')
   if total>=500000:partial=True;break
 elif ext in ['.xlsx','.xlsm']:
  wb=openpyxl.load_workbook(p,read_only=True,data_only=False,keep_links=False)
  for ws in wb:
   for i,row in enumerate(ws.iter_rows()):
    if i>=50000 or total>=500000:partial=True;break
    vals=[f'{cell.coordinate}={cell.value}' for cell in row if cell.value is not None]
    if vals:add(f'{ws.title} 행 {i+1}',' | '.join(vals),'xlsx')
  wb.close()
 elif ext=='.xls':
  import xlrd
  wb=xlrd.open_workbook(p)
  for ws in wb.sheets():
   for i in range(ws.nrows):
    if i>=50000 or total>=500000:partial=True;break
    add(f'{ws.name} 행 {i+1}',' | '.join(f'{xlrd.formula.colname(j)}{i+1}={ws.cell_value(i,j)}' for j in range(ws.ncols) if ws.cell_value(i,j)!=''),'xls')
 elif ext in ['.txt','.md','.csv','.json','.log']:
  data=p.read_bytes()[:2000000];t=None
  for encoding in ['utf-8-sig','utf-16','cp949']:
   try:t=data.decode(encoding);break
   except UnicodeError:pass
  if t is None:raise ValueError('문자 인코딩 확인 필요')
  add('본문',t,'text')
 elif ext in ['.docx']:
  from docx import Document
  doc=Document(p)
  for i,para in enumerate(doc.paragraphs):add(f'문단 {i+1}',para.text,'docx')
  for i,table in enumerate(doc.tables):
   for j,row in enumerate(table.rows):add(f'표 {i+1} 행 {j+1}',' | '.join(cell.text for cell in row.cells),'docx')
 else:return {'status':'unsupported','parts':[]}
 after=p.stat()
 if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('처리 중 파일 변경: 다음 갱신 필요')
 return {'status':('partial' if partial else 'ready') if parts else 'ocr_needed' if ext=='.pdf' else 'empty','parts':parts}

def process(limit):
 c=connect();rows=c.execute("select * from docs where present=1 and status='pending' order by size,id limit ?",(limit,)).fetchall()
 for r in rows:
  try:
   state(c,'current',r['path']);c.commit()
   run=subprocess.run([sys.executable,str(BASE/'catalog.py'),'--extract',r['realpath']],capture_output=True,text=True,timeout=25)
   if run.returncode:raise ValueError(run.stderr[-700:])
   obj=json.loads(run.stdout);parts=obj['parts'];body='\n'.join(t for _,t,_ in parts)
   s=Path(r['realpath']).stat()
   if (s.st_size,s.st_mtime_ns)!=(r['size'],r['mtime']):continue
   c.execute('delete from chunks where doc_id=?',(r['id'],));c.executemany('insert into chunks values(?,?,?,?)',[(r['id'],a,b,m) for a,b,m in parts]);c.execute('update docs set body=?,status=?,error=?,indexed_at=? where id=?',(body,obj['status'],'',time.time(),r['id']));reindex(c,r['id'],r['path'],body)
  except Exception as e:c.execute("update docs set status='failed',error=? where id=?",(str(e)[:700],r['id']))
  c.commit()
 state(c,'current','');state(c,'last_batch',time.time());c.commit();c.close();return len(rows)
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--extract');a.add_argument('--batch',type=int,default=100);a.add_argument('--scan-only',action='store_true');args=a.parse_args()
 if args.extract:
  print(json.dumps(extract(args.extract),ensure_ascii=False));sys.exit()
 init();scan();seed()
 if not args.scan_only:process(args.batch)
