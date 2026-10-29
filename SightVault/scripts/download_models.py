"""Setup only: downloads open weights; the application itself stays offline."""
import os
os.environ['HF_HUB_OFFLINE']='0'
os.environ['TRANSFORMERS_OFFLINE']='0'
from pathlib import Path
import argparse,json
from huggingface_hub import snapshot_download,HfApi

REPOS={'embeddinggemma-2':'google/embeddinggemma-2','minicpm-ocr':'openbmb/MiniCPM-OCR-1.0','minicpm-v':'openbmb/MiniCPM-V-4.7-1B'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--only',choices=list(REPOS));args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    for name,repo in REPOS.items():
        if args.only and name!=args.only:continue
        print(f'Downloading {repo}...',flush=True)
        info=HfApi().model_info(repo)
        target=root/'.models'/name
        snapshot_download(repo,revision=info.sha,local_dir=target,allow_patterns=['*.json','*.safetensors','*.model','*.jinja'])
        (target/'source.json').write_text(json.dumps({'repo':repo,'revision':info.sha},indent=2),encoding='utf8')
    print('Ready. Models can now run without an internet connection.')

if __name__=='__main__':main()
