"""Setup only: downloads open weights; the application itself stays offline."""
import os
os.environ['HF_HUB_OFFLINE']='0'
os.environ['TRANSFORMERS_OFFLINE']='0'
from pathlib import Path
import argparse,hashlib,json,subprocess
from huggingface_hub import snapshot_download,HfApi
from urllib.parse import quote

REPOS={'embeddinggemma-2':'google/embeddinggemma-2','minicpm-ocr':'openbmb/MiniCPM-OCR-1.0','minicpm-v':'openbmb/MiniCPM-V-4.7-1B'}
LFM3_REPO='LiquidAI/LFM3-VL-2B-MLX-4bit'
LFM3_REVISION='4146d7fa689833cfa7a06c474e748350b458f41e'
LFM3_RUNTIME_COMMIT='918763c6875c80e82f9f48344dfad4763346fea8'
LFM3_WEIGHT_SHA256='58268dfdf441ae4020d2a52fc97a814645e8a9624946e2d490403d4dcd71a255'


def _sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def _git_blob_sha1(path):
    size=path.stat().st_size
    digest=hashlib.sha1(f'blob {size}\0'.encode())
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def _verified(path,item):
    if not path.is_file() or path.stat().st_size != int(item['size']):
        return False
    lfs=item.get('lfs') or {}
    if lfs.get('oid'):
        return _sha256(path) == lfs['oid']
    return _git_blob_sha1(path) == item.get('oid')


def download_lfm3(target):
    """Fetch the pinned public MLX snapshot and verify each Hugging Face file object."""
    api=f'https://huggingface.co/api/models/{LFM3_REPO}/tree/{LFM3_REVISION}?recursive=true&expand=true'
    payload=subprocess.check_output(['curl','-4','--fail','--silent','--show-error','--location',api],text=True)
    items=json.loads(payload)
    target.mkdir(parents=True,exist_ok=True)
    verified_files={}
    for item in items:
        if item.get('type')!='file':
            continue
        name=item['path']
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError(f'Unsafe path returned by model repository: {name}')
        destination=target/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        expected_size=int(item['size'])
        if _verified(destination,item):
            print(f'Verified {name} ({expected_size} bytes)',flush=True)
        else:
            temporary=destination.with_name(destination.name+'.partial')
            if temporary.exists() and temporary.stat().st_size>expected_size:
                temporary.unlink()
            url=f'https://huggingface.co/{LFM3_REPO}/resolve/{LFM3_REVISION}/{quote(name,safe="/")}'
            print(f'Downloading {name} ({expected_size} bytes)',flush=True)
            subprocess.run(['curl','-4','--fail','--silent','--show-error','--location','--retry','3',
                            '--retry-all-errors','--retry-delay','2','--continue-at','-','--output',str(temporary),url],check=True)
            if not _verified(temporary,item):
                temporary.unlink(missing_ok=True)
                raise RuntimeError(f'File verification failed for {name}; retry the pinned download.')
            temporary.replace(destination)
        verified_files[name]={'bytes':destination.stat().st_size,'sha256':_sha256(destination)}
    if verified_files.get('model.safetensors',{}).get('sha256') != LFM3_WEIGHT_SHA256:
        raise RuntimeError('LFM3-VL model.safetensors does not match the expected pinned SHA-256.')
    source={
        'repo':LFM3_REPO,
        'revision':LFM3_REVISION,
        'runtime':'mlx-vlm',
        'runtime_commit':LFM3_RUNTIME_COMMIT,
        'quantization':{
            'method':'Quantization-Aware Distillation (QAD)',
            'bits':4,'group_size':32,'mode':'affine','token_embeddings_bits':8,
            'precision_note':'BF16 tensors are retained per the vendor checkpoint; this is a mixed-precision model.',
        },
        'files':verified_files,
    }
    (target/'source.json').write_text(json.dumps(source,indent=2),encoding='utf-8')
    print(f'Pinned LFM3 snapshot ready at {target}',flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--only',choices=list(REPOS))
    parser.add_argument('--vision',choices=('lfm3',),help='Download the pinned experimental LFM3-VL MLX snapshot.')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    if args.vision:
        if args.only:
            parser.error('Use --vision lfm3 separately from --only.')
        download_lfm3(root/'.models'/'lfm3-vl-2b-mlx-4bit')
        print('LFM3-VL is ready for local offline inference with the pinned experimental MLX-VLM environment.')
        return
    for name,repo in REPOS.items():
        if args.only and name!=args.only:continue
        print(f'Downloading {repo}...',flush=True)
        info=HfApi().model_info(repo)
        target=root/'.models'/name
        snapshot_download(repo,revision=info.sha,local_dir=target,allow_patterns=['*.json','*.safetensors','*.model','*.jinja'])
        (target/'source.json').write_text(json.dumps({'repo':repo,'revision':info.sha},indent=2),encoding='utf8')
    print('Ready. Models can now run without an internet connection.')

if __name__=='__main__':main()
