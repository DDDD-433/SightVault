import argparse
import logging
import os
os.environ.setdefault('HF_HUB_OFFLINE','1')
os.environ.setdefault('TRANSFORMERS_OFFLINE','1')
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')

def main():
    parser=argparse.ArgumentParser(description='SightVault local visual memory')
    parser.add_argument('--port',type=int,default=7860)
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    import uvicorn
    from sightvault.server import create_app
    print(f'\nSightVault: http://127.0.0.1:{args.port}\nLocal files. Local models. No cloud inference.\n',flush=True)
    uvicorn.run(create_app(),host='127.0.0.1',port=args.port,log_level='info')

if __name__=='__main__': main()
