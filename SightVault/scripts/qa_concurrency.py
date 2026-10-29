"""Bounded live API QA: concurrent search/status requests during real image analysis."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import json,time,urllib.request
BASE='http://127.0.0.1:7861'
ROOT=Path(__file__).resolve().parent.parent

def request(path,body=None):
    started=time.perf_counter()
    req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body else None,
                               headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=180) as response:
        data=json.load(response)
    return {'path':path,'started':started,'finished':time.perf_counter(),'data':data}

def main():
    warm=request('/api/search?q=GPU%20memory%20problem&mode=semantic')
    asset=warm['data']['results'][0]['id']
    # Warm generation before measuring lock contention rather than model imports.
    request('/api/analyze',{'asset_id':asset,'question':'Explain the visible memory error.'})
    analysis_started=time.perf_counter()
    with ThreadPoolExecutor(max_workers=9) as pool:
        analysis=pool.submit(request,'/api/analyze',{'asset_id':asset,'question':'Describe the visible error and distinguish visible facts from likely causes in about 100 words.'})
        paths=['/api/search?q=GPU%20memory%20problem&mode=semantic']*20+['/api/search?q=ERESOLVE&mode=exact']*10+['/api/status']*10
        futures=[pool.submit(request,path) for path in paths]
        responses=[f.result() for f in as_completed(futures)]
        generated=analysis.result()
    passed=True
    for r in responses:
        if r['path']=='/api/status':passed &= r['data']['total']==62
        else:
            prefix='terminal_eresolve' if 'ERESOLVE' in r['path'] else 'terminal_cuda'
            passed &= bool(r['data']['results']) and r['data']['results'][0]['filename'].startswith(prefix)
    semantic=[r for r in responses if 'mode=semantic' in r['path']]
    completed_during=sum(r['finished']<generated['finished'] for r in semantic)
    passed &= bool(generated['data']['answer']) and completed_during>0
    report={'requests':len(responses),'all_response_checks_passed':bool(passed),'semantic_completed_before_analysis':completed_during,
            'analysis_seconds':generated['finished']-generated['started'],'total_seconds':time.perf_counter()-analysis_started,
            'latency_ms':[round((r['finished']-r['started'])*1000,1) for r in responses],
            'analysis':generated['data'],'passed':bool(passed)}
    (ROOT/'qa/independent-review/concurrency.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('analysis','latency_ms')},indent=2))
    return 0 if passed else 1
if __name__=='__main__':raise SystemExit(main())
