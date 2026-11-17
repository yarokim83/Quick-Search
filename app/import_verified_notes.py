import pathlib,os,sys,json,hashlib,time,collections
BASE=pathlib.Path(__file__).resolve().parent;DATA=pathlib.Path(os.environ.get('LOCALAPPDATA',str(pathlib.Path.home()/'AppData'/'Local')))/'HPNT_Search_Standalone';config=json.loads((DATA/'windows_config.json').read_text(encoding='utf-8'));os.environ['HPNT_ROOT']=config['root'];os.environ['HPNT_DB']=str(DATA/'catalog.sqlite');sys.path.insert(0,str(BASE));import catalog
root=catalog.ROOT.resolve(strict=True);data=json.loads((BASE/'verified_notes.json').read_text(encoding='utf-8'));notes=collections.defaultdict(list)
for n in data['notes']:notes[n['id']].append(n)
c=catalog.connect();c.execute('create table if not exists imported_mac_notes(path text,sha256 text,primary key(path,sha256))');c.commit();counts=collections.Counter()
for item in data['fingerprints']:
 path=item['path'];rows=notes[item['id']]
 if c.execute('select 1 from imported_mac_notes where path=? and sha256=?',(path,item['sha256'])).fetchone():counts['이미 반영']+=1;continue
 r=c.execute('select * from docs where path=? and present=1',(path,)).fetchone()
 if not r:counts['검색 목록에 없음']+=1;continue
 try:
  p=pathlib.Path(r['realpath']);resolved=p.resolve(strict=True)
  if p.is_symlink() or not resolved.is_relative_to(root):counts['경로 확인 필요']+=1;continue
  before=p.stat()
  if before.st_size!=item['size'] or (before.st_size,before.st_mtime_ns)!=(r['size'],r['mtime']):counts['파일 상태 다름']+=1;continue
  with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
  after=p.stat()
  if digest!=item['sha256'] or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):counts['내용 다름 또는 변경 중']+=1;continue
  c.execute('begin immediate');fresh=c.execute('select * from docs where id=? and path=? and present=1',(r['id'],path)).fetchone()
  if not fresh or (fresh['size'],fresh['mtime'])!=(r['size'],r['mtime']):c.rollback();counts['검색 목록 변경']+=1;continue
  chunks=[(r['id'],n['location'],n['text'],n['method']) for n in rows];c.executemany('insert into chunks values(?,?,?,?)',chunks);extra='\n'.join(n['text'] for n in rows);body=(fresh['body']+'\n'+extra).strip();c.execute("update docs set body=?,status='partial',indexed_at=? where id=?",(body,time.time(),r['id']));catalog.reindex(c,r['id'],path,body);c.execute('insert into imported_mac_notes values(?,?)',(path,item['sha256']));c.commit();counts['내용 일치 반영']+=1
 except (OSError,ValueError) as e:
  c.rollback();counts['읽기 실패']+=1
print('검색 정보 대조 결과:',dict(counts));c.close()
