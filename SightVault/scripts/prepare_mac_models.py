"""Convert verified pinned MiniCPM sources to local MLX mixed 4-bit checkpoints."""
from pathlib import Path
import argparse,gc,hashlib,json,sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'handoff'))
NAMES=('minicpm-ocr','minicpm-v')

def load_manifest():
    return json.loads((ROOT/'handoff/MODEL_MANIFEST.json').read_text())

def verify_sources(manifest, model_root):
    """Check every required pinned source file before converting anything."""
    from verify_transfer import required_models, verify_model_files
    checked, failures = verify_model_files(manifest, model_root, required_models(manifest))
    return {'checked':checked,'failures':failures,'passed':not failures}

def convert_one(manifest, model_root, name, target):
    from importlib.metadata import version
    from mlx_vlm import convert
    import mlx.core as mx
    target=Path(target)
    convert(str(Path(model_root)/name), str(target), quantize=True, q_bits=4,
            q_group_size=64, q_mode='affine', dtype='bfloat16', trust_remote_code=False)
    files={}
    for p in target.glob('*.safetensors'):
        with p.open('rb') as stream:
            files[p.name]={'bytes':p.stat().st_size,'sha256':hashlib.file_digest(stream,'sha256').hexdigest()}
    source={**manifest['sources'][name], 'runtime':'mlx-vlm', 'runtime_version':version('mlx-vlm'),
            'mlx_version':version('mlx'), 'quantization':{'bits':4,'group_size':64,'mode':'affine'},
            'precision_note':'Mixed precision; runtime model predicate preserves vision, mergers and sensitive layers.',
            'files':files}
    (target/'source.json').write_text(json.dumps(source,indent=2))
    gc.collect();mx.clear_cache()
    return source

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only',choices=NAMES)
    parser.add_argument('--model-root',type=Path,default=ROOT/'.models',help='Folder containing the pinned sources (default: .models)')
    args=parser.parse_args(argv)
    model_root=args.model_root.expanduser()
    manifest=load_manifest()
    report=verify_sources(manifest, model_root)
    if not report['passed']:
        print(json.dumps(report,indent=2),flush=True)
        return 1
    for name in NAMES:
        if args.only and args.only != name: continue
        source=convert_one(manifest, model_root, name, model_root/(name+'-mlx-4bit'))
        print(json.dumps(source),flush=True)
    return 0

if __name__=='__main__':sys.exit(main())
