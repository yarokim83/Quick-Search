import sys,json,pathlib,datetime,math,zipfile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent/'vendor'))
p=pathlib.Path(sys.argv[1]);idx=int(sys.argv[2]);start=int(sys.argv[3]);assert 0<=idx<500 and 1<=start<=20001
size=50;cols=30

def text(v):
 if v is None:return ''
 if isinstance(v,(datetime.datetime,datetime.date,datetime.time)):return v.isoformat(sep=' ') if isinstance(v,datetime.datetime) else v.isoformat()
 if isinstance(v,float) and not math.isfinite(v):return ''
 return str(v)[:500]
if p.suffix.lower() in ('.xlsx','.xlsm'):
 import openpyxl
 with zipfile.ZipFile(p) as z:
  images=[x for x in z.infolist() if x.filename.startswith('xl/media/') and pathlib.Path(x.filename).suffix.lower() in ('.png','.jpg','.jpeg','.gif') and x.file_size<=10000000][:12]
 wb=openpyxl.load_workbook(p,read_only=True,data_only=True,keep_links=False)
 try:
  names=wb.sheetnames[:500]
  if idx>=len(names):raise IndexError('sheet')
  ws=wb[names[idx]];total=ws.max_row or 0
  rows=[[text(v) for v in row] for row in ws.iter_rows(min_row=start,max_row=start+size-1,max_col=cols,values_only=True)]
 finally:wb.close()
elif p.suffix.lower()=='.xls':
 import xlrd
 images=[];wb=xlrd.open_workbook(str(p),on_demand=True);names=wb.sheet_names()[:500]
 if idx>=len(names):raise IndexError('sheet')
 ws=wb.sheet_by_index(idx);total=ws.nrows;rows=[[text(ws.cell_value(i,j)) if j<ws.ncols else '' for j in range(cols)] for i in range(start-1,min(start-1+size,total))];wb.release_resources()
else:raise ValueError('unsupported')
while rows and not any(rows[-1]):rows.pop()
width=max((max((i+1 for i,x in enumerate(r) if x),default=0) for r in rows),default=0);width=max(width,1);rows=[r[:width] for r in rows]
print(json.dumps({'sheets':names,'sheet':idx,'start':start,'rows':rows,'has_more':total>start+size-1,'column_limit':cols,'page_size':size,'images':len(images)},ensure_ascii=False))
