"""Compare real direct-image/query embeddings on MPS and CPU without modifying the index."""
from pathlib import Path
import sys,json,sqlite3,time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import numpy as np
from PIL import Image
from sightvault.config import Config
from sightvault.models import Models
ROOT=Path(__file__).resolve().parent.parent
NAMES=['terminal_cuda_01.png','object_study_01.png','object_study_02.png','kitchen_photo.jpg','bridge_photo.png']
QUERIES=['GPU memory problem','Find the image with a red backpack','blue rucksack drawing']
m=Models(Config(device='mps'));results={}
for device in ('mps','cpu'):
    start=time.perf_counter()
    if device=='cpu':
        m._embedding.to('cpu').float();m.active['embedding']='cpu';m._clear_device_cache()
    images=[m.embed_image(Image.open(ROOT/'samples'/name).convert('RGB')) for name in NAMES]
    queries=[m.embed_query(q) for q in QUERIES]
    results[device]={'images':np.array(images),'queries':np.array(queries),'seconds':time.perf_counter()-start}
with sqlite3.connect((ROOT/'data/sightvault.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    rows=db.execute('select filename,embedding from assets order by id').fetchall()
matrix=np.stack([np.frombuffer(blob,dtype='<f4') for _,blob in rows])
comparisons=[]
for i,q in enumerate(QUERIES):
    scores={device:matrix@results[device]['queries'][i] for device in results}
    top={device:[rows[j][0] for j in np.argsort(-value,kind='stable')[:5]] for device,value in scores.items()}
    comparisons.append({'query':q,'top5':top,'same_top5':top['mps']==top['cpu'],
                        'max_cosine_delta':float(np.max(np.abs(scores['mps']-scores['cpu'])))})
image_agreement=np.sum(results['mps']['images']*results['cpu']['images'],axis=1)
report={'image_agreement':[{'image':name,'mps_vs_cpu_cosine':float(c)} for name,c in zip(NAMES,image_agreement)],
        'queries':comparisons,'seconds':{d:r['seconds'] for d,r in results.items()},
        'passed':bool(np.min(image_agreement)>.9999 and all(r['same_top5'] for r in comparisons))}
(ROOT/'qa/independent-review/cross-device.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2));raise SystemExit(0 if report['passed'] else 1)
