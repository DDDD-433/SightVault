"""Convert verified pinned MiniCPM sources to local MLX mixed 4-bit checkpoints."""
from pathlib import Path
import sys,json,hashlib,gc
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
ROOT=Path(__file__).resolve().parent.parent
from importlib.metadata import version
import subprocess
subprocess.run([sys.executable,str(ROOT/'handoff/verify_transfer.py'),'--models'],check=True)
from mlx_vlm import convert
import mlx.core as mx
manifest=json.loads((ROOT/'handoff/MODEL_MANIFEST.json').read_text())
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--only',choices=('minicpm-ocr','minicpm-v'));args=parser.parse_args()
for name in ('minicpm-ocr','minicpm-v'):
    if args.only and args.only != name: continue
    target=ROOT/'.models'/(name+'-mlx-4bit')
    convert(str(ROOT/'.models'/name), str(target), quantize=True, q_bits=4,
            q_group_size=64, q_mode='affine', dtype='bfloat16', trust_remote_code=False)
    source={**manifest['sources'][name], 'runtime':'mlx-vlm', 'runtime_version':version('mlx-vlm'),
            'mlx_version':version('mlx'), 'quantization':{'bits':4,'group_size':64,'mode':'affine'},
            'precision_note':'Mixed precision; runtime model predicate preserves vision, mergers and sensitive layers.',
            'files':{p.name:{'bytes':p.stat().st_size,'sha256':hashlib.file_digest(p.open('rb'),'sha256').hexdigest()} for p in target.glob('*.safetensors')}}
    (target/'source.json').write_text(json.dumps(source,indent=2))
    print(json.dumps(source),flush=True)
    gc.collect();mx.clear_cache()
