"""Bounded live API QA: concurrent search/status requests during real image analysis."""
from concurrent.futures import ThreadPoolExecutor,as_completed
import argparse
import os
from pathlib import Path
import json,time,urllib.request
ROOT=Path(__file__).resolve().parent.parent

def request(base,path,body=None):
    started=time.perf_counter()
    req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body else None,
                               headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=180) as response:
        data=json.load(response)
    return {'path':path,'started':started,'finished':time.perf_counter(),'data':data}

def identity_matches(data,expected_backend,expected_model,expected_root):
    vision=data.get('models',{}).get('vision',{})
    return ((expected_backend is None or vision.get('backend')==expected_backend)
            and (expected_model is None or vision.get('model_id')==expected_model)
            and (expected_root is None or data.get('instance_root')==expected_root))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default=os.environ.get('SIGHTVAULT_QA_BASE','http://127.0.0.1:7861'))
    parser.add_argument('--expect-backend',choices=('minicpm','lfm3','omni'))
    parser.add_argument('--expect-model')
    parser.add_argument('--expect-root')
    parser.add_argument('--output',type=Path,default=ROOT/'qa'/'concurrency-experimental.json')
    args=parser.parse_args();base=args.base_url.rstrip('/')
    status=request(base,'/api/status')['data']
    identity=identity_matches(status,args.expect_backend,args.expect_model,args.expect_root)
    if status.get('total')!=62 or not identity:
        raise RuntimeError(f'Wrong QA instance/model identity: total={status.get("total")}, root={status.get("instance_root")}, vision={status.get("models",{}).get("vision")}')
    expected_backend=args.expect_backend or status['models']['vision'].get('backend')
    expected_model=args.expect_model or status['models']['vision'].get('model_id')
    expected_root=args.expect_root or status.get('instance_root')
    warm=request(base,'/api/search?q=GPU%20memory%20problem&mode=semantic')
    asset=warm['data']['results'][0]['id']
    # Warm generation before measuring lock contention rather than model imports.
    warm_analysis=request(base,'/api/analyze',{'asset_id':asset,'question':'Explain the visible memory error.'})['data']
    warm_identity=(warm_analysis.get('backend')==expected_backend and warm_analysis.get('model')==status['models']['vision'].get('name'))
    analysis_started=time.perf_counter()
    with ThreadPoolExecutor(max_workers=9) as pool:
        analysis=pool.submit(request,base,'/api/analyze',{'asset_id':asset,'question':'Describe the visible error and distinguish visible facts from likely causes in about 100 words.'})
        paths=['/api/search?q=GPU%20memory%20problem&mode=semantic']*20+['/api/search?q=ERESOLVE&mode=exact']*10+['/api/status']*10
        futures=[pool.submit(request,base,path) for path in paths]
        responses=[f.result() for f in as_completed(futures)]
        generated_request=analysis.result()
        generated=generated_request['data']
    passed=bool(identity and warm_identity)
    for r in responses:
        if r['path']=='/api/status':passed &= r['data']['total']==62 and identity_matches(r['data'],expected_backend,expected_model,expected_root)
        else:
            prefix='terminal_eresolve' if 'ERESOLVE' in r['path'] else 'terminal_cuda'
            passed &= bool(r['data']['results']) and r['data']['results'][0]['filename'].startswith(prefix)
    semantic=[r for r in responses if 'mode=semantic' in r['path']]
    completed_during=sum(r['finished']<generated_request['finished'] for r in semantic)
    evidence=generated.get('evidence',{})
    analysis_identity=(generated.get('backend')==expected_backend
                       and generated.get('model')==status['models']['vision'].get('name')
                       and str(evidence.get('filename','')).startswith('terminal_cuda'))
    passed &= bool(generated.get('answer')) and completed_during>0 and analysis_identity
    analysis_evidence={key:evidence.get(key) for key in ('id','filename','sha256')}
    safe_analysis={key:value for key,value in generated.items() if key!='evidence'}
    safe_analysis['evidence']=analysis_evidence
    report={'requests':len(responses),'all_response_checks_passed':bool(passed),'semantic_completed_before_analysis':completed_during,
            'analysis_seconds':generated_request['finished']-generated_request['started'],'total_seconds':time.perf_counter()-analysis_started,
            'latency_ms':[round((r['finished']-r['started'])*1000,1) for r in responses],
            'instance_root':'.' if expected_root==str(ROOT) else '<external-instance-root>',
            'instance_root_verified':identity,'vision_identity':{'backend':expected_backend,'model_id':expected_model,'model':status['models']['vision'].get('name'),'runtime':status['models']['vision'].get('runtime')},
            'status_identity_passed':identity,'warm_analysis_identity_passed':warm_identity,'analysis_identity_passed':analysis_identity,
            'analysis':safe_analysis,'passed':bool(passed)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('analysis','latency_ms')},indent=2))
    return 0 if passed else 1
if __name__=='__main__':raise SystemExit(main())
