"""Isolated ingestion QA with real models. Never touches data/ or samples/: uses its own DB and folders."""
import json,os,shutil,subprocess,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
WORK=Path(sys.argv[1] if len(sys.argv)>1 else Path(tempfile.gettempdir())/'sightvault_ingest_qa').resolve()
if WORK.exists(): shutil.rmtree(WORK)
os.environ['SIGHTVAULT_DATA']=str(WORK/'data')
sys.path.insert(0,str(ROOT))
from PIL import Image, ImageDraw
from sightvault.config import Config
from sightvault.database import Database, decode_vector
from sightvault.ingestion import Ingestor
from sightvault.models import Models
from sightvault.search import Search

cfg=Config();cfg.prepare();assert cfg.data==WORK/'data'
db=Database(cfg.db_path);models=Models(cfg);ing=Ingestor(cfg,db,models)
pics=WORK/'pics';pics.mkdir(parents=True);empty=WORK/'empty';empty.mkdir()
for name in ('terminal_cuda_01.png','terminal_eresolve_01.png','receipt_022.png','object_study_01.png','kitchen_photo.jpg'):
    shutil.copy2(ROOT/'samples'/name,pics/name)
shutil.copy2(ROOT/'samples'/'terminal_cuda_01.png',pics/'cuda_copy.png')      # duplicate content
im=Image.new('RGB',(900,300),'white');ImageDraw.Draw(im).text((20,120),'QA marker ZX-4471 <script>alert(1)</script>',fill='black');im.save(pics/'html_marker.webp')
Image.open(ROOT/'samples'/'lecture_erd_01.png').convert('RGB').save(pics/'erd.jpg',quality=88)
(pics/'corrupt.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'garbage'*50)
data=(ROOT/'samples'/'receipt_023.png').read_bytes();(pics/'truncated.png').write_bytes(data[:len(data)//2])
(pics/'notes.txt').write_text('not an image');(pics/'clip.gif').write_bytes(b'GIF89a')
out={}
t=time.perf_counter();r=ing.run(pics,ocr_backend='rapid');out['first_run']={k:r[k] for k in ('total','indexed','skipped','duplicates','failed','unsupported','elapsed_s')};out['first_run']['errors']=r['errors'];out['first_run']['warnings']=r['warnings']
rows=db.all();out['stored']=[{'file':a['filename'],'ocr_chars':len(a['ocr_text']),'ocr_backend':a['ocr_backend'],'embedding_dims':len(decode_vector(a['embedding'])) if a['embedding'] else 0,
    'thumb':(cfg.data/'thumbs'/a['thumbnail_path']).is_file(),'image':(cfg.data/'images'/a['image_path']).is_file(),'timestamp':a['timestamp'],'source':a['timestamp_source']} for a in rows]
r=ing.run(pics,ocr_backend='rapid');out['rerun']={k:r[k] for k in ('indexed','updated','skipped','failed')}
# modify + rename
m=Image.open(pics/'object_study_01.png').convert('RGB');ImageDraw.Draw(m).rectangle((0,0,200,200),fill='blue');m.save(pics/'object_study_01.png')
(pics/'receipt_022.png').rename(pics/'receipt_renamed.png')
r=ing.run(pics,ocr_backend='rapid');out['after_modify_rename']={k:r.get(k) for k in ('indexed','updated','skipped','duplicates','missing','warnings')}
out['empty_folder']=ing.run(empty)['total']
try: ing.run(WORK/'does_not_exist');out['missing_folder']='no error (BUG)'
except ValueError as e: out['missing_folder']=str(e)
# exact-text retrieval of the new marker; HTML text stored verbatim (escaped by UI)
s=Search(db,models);out['marker_search']=[x['filename'] for x in s.run('ZX-4471',mode='exact')['results']]
out['semantic_in_test_db']=[x['filename'] for x in s.run('cash register slip',mode='semantic')['results'][:3]]
# cancellation
big=WORK/'big';big.mkdir()
for n in range(12): Image.new('RGB',(320,200),(n*20,90,160)).save(big/f'c{n:02d}.png')
cancel=threading.Event();seen=[]
def prog(p):
    seen.append(p['processed'])
    if p['processed']>=3: cancel.set()
r=ing.run(big,prog,'off',cancel);out['cancel']={'cancelled':r['cancelled'],'processed':r['processed'],'indexed':r['indexed']}
r=ing.run(big,None,'off');out['resume_after_cancel']={'indexed':r['indexed'],'skipped':r['skipped'],'updated':r['updated']}
# hard interruption: kill a subprocess mid-run, then recover
crash=WORK/'crash';crash.mkdir()
for n in range(10): Image.new('RGB',(640,400),(200,n*25,40)).save(crash/f'k{n:02d}.png')
env=dict(os.environ,SIGHTVAULT_DATA=str(WORK/'data'))
p=subprocess.Popen([sys.executable,str(ROOT/'scripts'/'index_folder.py'),str(crash),'--ocr','rapid'],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
lines=[]
for line in p.stdout:
    lines.append(line)
    if line.startswith('3/10'): p.kill();break
p.wait()
before=len([a for a in db.all() if a['folder']==str(crash)])
r=ing.run(crash,ocr_backend='rapid')
out['interrupt_recovery']={'rows_before_resume':before,'resumed_indexed':r['indexed'],'skipped':r['skipped'],'failed':r['failed'],
    'partial_files_left':[str(x.name) for x in cfg.data.rglob('*.partial')],'total_rows':len([a for a in db.all() if a['folder']==str(crash)])}
# MiniCPM-OCR (real) on one image in the isolated DB
one=WORK/'one';one.mkdir();shutil.copy2(ROOT/'samples'/'terminal_module_01.png',one/'module.png')
t=time.perf_counter();r=ing.run(one,ocr_backend='minicpm')
a=next(x for x in db.all() if x['folder']==str(one))
out['minicpm_ocr_ingest']={'seconds':round(time.perf_counter()-t,1),'backend':a['ocr_backend'],'has_ModuleNotFoundError':'ModuleNotFoundError' in a['ocr_text'],'text_head':a['ocr_text'][:160],'errors':r['errors']}
out['db_stats']=db.stats()
print(json.dumps(out,indent=2))
(ROOT/'qa'/'mac_ingestion_qa.json').parent.mkdir(exist_ok=True)
(ROOT/'qa'/'mac_ingestion_qa.json').write_text(json.dumps(out,indent=2),encoding='utf8')
