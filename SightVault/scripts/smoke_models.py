import sys,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PIL import Image
from sightvault.config import Config
from sightvault.models import Models

config=Config();models=Models(config)
image=Image.open(config.root/'samples'/'terminal_cuda_01.png').convert('RGB')
start=time.perf_counter()
vector=models.embed_image(image);query=models.embed_query('GPU memory problem')
print(json.dumps({'embedding_shape':list(vector.shape),'norm':float((vector@vector)**.5),'similarity':float(vector@query),'seconds':time.perf_counter()-start}),flush=True)
start=time.perf_counter();ocr=models.ocr(image,'rapid');print(json.dumps({'ocr':ocr,'seconds':time.perf_counter()-start}),flush=True)
start=time.perf_counter();answer=models.analyze(image,'What probably caused this error?',ocr['text']);print(json.dumps({'analysis':answer,'seconds':time.perf_counter()-start}),flush=True)
if (config.models/'minicpm-ocr'/'source.json').exists():
    start=time.perf_counter();ocr=models.ocr(image,'minicpm');print(json.dumps({'minicpm_ocr':ocr,'seconds':time.perf_counter()-start}),flush=True)
