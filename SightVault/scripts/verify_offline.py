"""Verify actual model inference with outbound socket connections blocked."""
from pathlib import Path
import sys,socket,time,json
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

original_connect=socket.socket.connect
def blocked(self,address):
    if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1','localhost'):
        return original_connect(self,address)
    raise RuntimeError('Outbound network disabled for SightVault offline verification')
socket.socket.connect=blocked

from PIL import Image
from sightvault.config import Config
from sightvault.models import Models
from sightvault.database import Database
from sightvault.search import Search
config=Config();models=Models(config);search=Search(Database(config.db_path),models)
import torch,psutil
torch.cuda.reset_peak_memory_stats() if torch.cuda.is_available() else None
report={'outbound_network_connections_blocked':True,'loopback_allowed_for_local_runtime':True,'requested_device':config.device,'isolation_method':'in-process socket.connect guard; not an OS firewall'}
t=time.perf_counter();result=search.run('GPU memory problem',mode='semantic')
report['semantic_search']={'top_result':result['results'][0]['filename'],'latency_ms':result['latency_ms']}
image=Image.open(config.root/'samples'/'terminal_cuda_01.png').convert('RGB')
t=time.perf_counter();ocr=models.ocr(image,'minicpm');report['ocr']={**ocr,'seconds':round(time.perf_counter()-t,2)}
t=time.perf_counter();answer=models.analyze(image,'What probably caused this error?',ocr['text']);report['analysis']={'answer':answer,'seconds':round(time.perf_counter()-t,2)}
report['peak_gpu_allocated_gib']=round(torch.cuda.max_memory_allocated()/1024**3,2) if torch.cuda.is_available() else None
report['models']=models.status()
report['mps_allocated_gib']=round(torch.mps.current_allocated_memory()/1024**3,3) if torch.backends.mps.is_available() else None
try:
    import mlx.core as mx
    report['mlx_peak_gib']=round(mx.get_peak_memory()/1024**3,3)
except ImportError: pass
report['process_ram_gib']=round(psutil.Process().memory_info().rss/1024**3,2)
report['passed']=bool('cuda' in report['semantic_search']['top_result'] and 'CUDA' in ocr['text'] and answer)
(config.root/'qa/MAC_OFFLINE_VERIFICATION.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print(json.dumps(report,indent=2))
sys.exit(0 if report['passed'] else 1)
