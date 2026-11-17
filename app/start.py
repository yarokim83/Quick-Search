import os,sys,json,runpy,unicodedata,ctypes
from pathlib import Path
from ctypes import wintypes
BASE=Path(__file__).resolve().parent
DATA=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData'/'Local')))/'HPNT_Search_Standalone'
CONFIG=DATA/'windows_config.json'

def message(text,title='HPNT 검색'):
 try:ctypes.windll.user32.MessageBoxW(None,str(text),title,0)
 except Exception:print(text,flush=True)

def choose_root():
 class BrowseInfo(ctypes.Structure):
  _fields_=[('hwndOwner',wintypes.HWND),('pidlRoot',ctypes.c_void_p),('pszDisplayName',wintypes.LPWSTR),('lpszTitle',wintypes.LPCWSTR),('ulFlags',wintypes.UINT),('lpfn',ctypes.c_void_p),('lParam',ctypes.c_ssize_t),('iImage',ctypes.c_int)]
 try:
  ole=ctypes.windll.ole32;shell=ctypes.windll.shell32
  ole.CoInitialize(None)
  shell.SHBrowseForFolderW.argtypes=[ctypes.POINTER(BrowseInfo)];shell.SHBrowseForFolderW.restype=ctypes.c_void_p
  shell.SHGetPathFromIDListW.argtypes=[ctypes.c_void_p,wintypes.LPWSTR];shell.SHGetPathFromIDListW.restype=wintypes.BOOL
  ole.CoTaskMemFree.argtypes=[ctypes.c_void_p]
  bi=BrowseInfo();bi.lpszTitle='Google Drive 안의 HPNT_업무 폴더를 선택하세요';bi.ulFlags=0x0001|0x0040
  pidl=shell.SHBrowseForFolderW(ctypes.byref(bi));chosen=''
  if pidl:
   buf=ctypes.create_unicode_buffer(32768)
   if shell.SHGetPathFromIDListW(pidl,buf):chosen=buf.value
   ole.CoTaskMemFree(pidl)
  ole.CoUninitialize()
  if chosen:return Path(chosen)
 except Exception:pass
 if sys.stdin is None:
  message('HPNT_업무 폴더 선택이 취소되었습니다.');raise SystemExit(1)
 print('폴더 선택을 취소했거나 창을 열 수 없습니다. 전체 경로를 입력하거나 Enter로 종료하세요.',flush=True)
 raw=input('HPNT_업무 폴더 경로: ').strip().strip('"')
 if not raw:raise SystemExit('폴더 선택이 취소되었습니다.')
 return Path(raw)

try:
 DATA.mkdir(parents=True,exist_ok=True)
 log_path=DATA/'server.log'
 if log_path.exists() and log_path.stat().st_size>2_000_000:
  try:log_path.unlink()
  except OSError:pass
 if sys.stdout is None or sys.stderr is None:
  log_file=open(log_path,'a',encoding='utf-8',buffering=1)
  if sys.stdout is None:sys.stdout=log_file
  if sys.stderr is None:sys.stderr=log_file
 config=json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else {}
 root=Path(config['root']) if config.get('root') else choose_root()
 if not root.is_dir() or unicodedata.normalize('NFC',root.name)!='HPNT_업무':
  message('선택한 HPNT_업무 폴더에 접근할 수 없습니다. Google Drive 연결과 폴더 이름을 확인해 주세요.');raise SystemExit(1)
 if not CONFIG.exists():CONFIG.write_text(json.dumps({'root':str(root)},ensure_ascii=False,indent=2),encoding='utf-8')
 os.environ['HPNT_ROOT']=str(root);os.environ['HPNT_DB']=str(DATA/'catalog.sqlite');os.environ['HPNT_OPEN_BROWSER']='1';os.chdir(BASE);sys.path.insert(0,str(BASE));sys.argv=['server.py']
 print('검색앱을 실행합니다. 이 창을 닫으면 검색이 중지됩니다.',flush=True)
 runpy.run_path(str(BASE/'server.py'),run_name='__main__')
except KeyboardInterrupt:pass
except OSError as e:
 message('검색앱을 시작하지 못했습니다. 이미 검색앱이 켜져 있거나 포트가 사용 중일 수 있습니다.\n'+str(e));raise
