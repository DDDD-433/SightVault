"""Real image model-switch test; run serially outside the serving process."""
from pathlib import Path
import sys,json,time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PIL import Image
from sightvault.models import Models
from sightvault.config import Config
m=Models(Config());im=Image.open('samples/terminal_cuda_01.png').convert('RGB');rows=[]
for kind in ('ocr','vision','ocr','vision'):
    t=time.perf_counter()
    text=m._generate(kind,im,'Read the error message.' if kind=='ocr' else 'Describe the visible error.',160)
    state=m.status();other='vision' if kind=='ocr' else 'ocr'
    assert state[kind]['loaded'] and not state[other]['loaded'] and text
    rows.append({'kind':kind,'seconds':time.perf_counter()-t,'text':text,'models':state})
Path('qa/MAC_SWITCHING.json').write_text(json.dumps(rows,indent=2))
