"""Real local MLX failure/recovery plus EXIF ingestion, using disposable files only."""
from pathlib import Path
import sys,os,json,tempfile,shutil,gc,socket,hashlib
import argparse
ROOT=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=ROOT/'qa'/'model-recovery-experimental.json')
ARGS=parser.parse_args()
sys.path.insert(0,os.environ.get('SIGHTVAULT_TEST_SOURCE',str(ROOT)))
original=socket.socket.connect
def guarded(self,address):
    if isinstance(address,tuple) and address[0] in ('127.0.0.1','localhost','::1'):return original(self,address)
    raise RuntimeError('Outbound sockets disabled in model recovery QA')
socket.socket.connect=guarded
from PIL import Image
from sightvault.config import Config
from sightvault.models import Models
from sightvault.database import Database
from sightvault.ingestion import Ingestor
import mlx.core as mx
with tempfile.TemporaryDirectory(prefix='sightvault-model-recovery-') as folder:
    work=Path(folder);weights=work/'models';weights.mkdir();source=ROOT/'.models'
    for name in ('embeddinggemma-2','minicpm-v-mlx-4bit'):(weights/name).symlink_to(source/name,target_is_directory=True)
    bad=weights/'minicpm-ocr-mlx-4bit';bad.mkdir()
    for p in (source/'minicpm-ocr-mlx-4bit').iterdir():
        if p.is_file() and p.name!='model.safetensors':shutil.copy2(p,bad/p.name)
    (bad/'model.safetensors').write_bytes(b'INTENTIONALLY INVALID DISPOSABLE QA WEIGHTS')
    cfg=Config(root=ROOT,data=work/'data',models=weights);cfg.prepare();m=Models(cfg)
    image=Image.open(ROOT/'samples/terminal_cuda_01.png').convert('RGB')
    fallback=m.ocr(image,'auto');failed_state=m.status()['ocr']
    assert fallback['backend']=='RapidOCR (local)' and 'fallback' in fallback['error'].lower()
    assert not failed_state['loaded'] and failed_state['device'] is None
    safe_temp=str(work)
    safe_fallback={**fallback,'error':fallback['error'].replace(safe_temp,'<temp>')}
    failed_state={**failed_state,'error':str(failed_state.get('error') or '').replace(safe_temp,'<temp>')}
    (bad/'model.safetensors').unlink();(bad/'model.safetensors').symlink_to(source/'minicpm-ocr-mlx-4bit/model.safetensors')
    recovered=m.ocr(image,'minicpm');recovered_state=m.status()['ocr']
    assert 'CUDA' in recovered['text'] and recovered_state['device']=='mlx (Metal GPU)' and recovered_state['error'] is None
    answer=m.analyze(image,'What probably caused the visible error?',recovered['text'])
    assert 'memory' in answer.lower()
    final_generators=m.status()
    m._vlm=None;m.active.pop('vision',None);gc.collect();mx.clear_cache()
    pics=work/'photos';pics.mkdir();exif=Image.Exif();details=exif.get_ifd(0x8769)
    details[36867]='2019:06:15 12:00:00';details[36881]='+02:00'
    image.save(pics/'camera.jpg',exif=exif.tobytes())
    db=Database(cfg.db_path);ingested=Ingestor(cfg,db,m).run(pics,ocr_backend='rapid');row=db.all()[0]
    assert ingested['indexed']==1 and row['timestamp']=='2019-06-15T10:00:00+00:00'
    assert row['timestamp_source']=='EXIF capture time (with timezone)' and row['embedding']
    source_root=Path(sys.modules['sightvault.models'].__file__).parent.parent
    report={'outbound_socket_guard':True,'fault_injection':'invalid safetensors in a disposable model directory; this is a load-failure test, not an OOM injection; original weights untouched',
            'fallback':{'backend':safe_fallback['backend'],'error':safe_fallback['error'],'text_characters':len(fallback['text']),
                        'text_sha256':hashlib.sha256(fallback['text'].encode()).hexdigest()},
            'failed_ocr_state':failed_state,'recovered_ocr_state':recovered_state,
            'vision_answer':answer,'generator_status':final_generators,'exif_timestamp':row['timestamp'],
            'exif_source':row['timestamp_source'],'embedding_bytes':len(row['embedding']),
            'source_sha256':{name:hashlib.sha256((source_root/'sightvault'/name).read_bytes()).hexdigest() for name in ('models.py','metadata.py','ingestion.py','database.py')},'passed':True}
    ARGS.output.parent.mkdir(parents=True,exist_ok=True)
    ARGS.output.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
