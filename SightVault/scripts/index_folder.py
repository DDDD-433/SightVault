import argparse, json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from sightvault.config import Config
from sightvault.database import Database
from sightvault.ingestion import Ingestor
from sightvault.models import Models

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('folder')
    parser.add_argument('--ocr',choices=['auto','minicpm','rapid','off'],default='auto')
    parser.add_argument('--force',action='store_true')
    args=parser.parse_args()
    config=Config();config.prepare()
    def progress(r):print(f"{r['processed']}/{r['total']} | {r['indexed']} new | {r['failed']} failed | {r['elapsed_s']:.1f}s | {r['current']}",flush=True)
    result=Ingestor(config,Database(config.db_path),Models(config)).run(args.folder,progress,args.ocr,force=args.force)
    print(json.dumps(result,indent=2))
    return int(result['failed']>0)

if __name__=='__main__':sys.exit(main())
