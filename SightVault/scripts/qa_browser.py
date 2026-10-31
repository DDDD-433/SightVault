"""Headless browser QA of the real UI (run with a separate Playwright env).
usage: python qa_browser.py <base_url> <screenshot_dir> [--xss]"""
import argparse,json,sys,time
from pathlib import Path
from playwright.sync_api import sync_playwright
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('base_url');parser.add_argument('screenshot_dir')
parser.add_argument('--xss',action='store_true')
parser.add_argument('--expect-backend',choices=('minicpm','lfm3','omni'))
parser.add_argument('--expect-model')
parser.add_argument('--expect-root')
args=parser.parse_args();BASE=args.base_url;SHOTS=Path(args.screenshot_dir);SHOTS.mkdir(parents=True,exist_ok=True)
XSS=args.xss
log={'console_errors':[],'failed_requests':[],'http_errors':[],'checks':{}}
def check(name,cond,detail=None):
    log['checks'][name]={'ok':bool(cond),'detail':detail};print(('PASS ' if cond else 'FAIL ')+name,detail if detail is not None else '',flush=True)
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={'width':1440,'height':900},permissions=['clipboard-read','clipboard-write'],bypass_csp=True)  # app CSP forbids eval; Playwright's wait_for_function needs it (test-only)
    pg=ctx.new_page()
    pg.on('console',lambda m:m.type=='error' and log['console_errors'].append(m.text))
    pg.on('pageerror',lambda e:log['console_errors'].append(str(e)))
    pg.on('requestfailed',lambda r:log['failed_requests'].append(r.url))
    pg.on('response',lambda r:r.status>=400 and log['http_errors'].append(f'{r.status} {r.url}'))
    def wait_idle():
        pg.wait_for_function("!document.getElementById('results').classList.contains('loading') && !document.getElementById('search-button').disabled",timeout=180000)
    def first_name(): return pg.locator('.card .card-title').first.inner_text()
    def names(): return pg.locator('.card .card-title').all_inner_texts()
    def search(q,mode=None):
        if mode: pg.click(f'[data-mode="{mode}"]');wait_idle()
        pg.fill('#query',q);pg.press('#query','Enter');wait_idle()
    def broken_images(): return pg.evaluate("[...document.images].filter(i=>i.complete&&i.src&&i.naturalWidth===0).map(i=>i.src)")
    pg.goto(BASE);wait_idle()
    status=pg.evaluate("async()=>await (await fetch('/api/status')).json()")
    vision=status.get('models',{}).get('vision',{})
    check('api_vision_identity_consistent',status.get('vision_backend')==vision.get('backend') and status.get('vision_model')==vision.get('name'),
          {'status_backend':status.get('vision_backend'),'model_backend':vision.get('backend'),'status_model':status.get('vision_model'),'model_name':vision.get('name')})
    if args.expect_backend:
        check('expected_backend',vision.get('backend')==args.expect_backend,vision.get('backend'))
    if args.expect_model:
        check('expected_model_id',vision.get('model_id')==args.expect_model,vision.get('model_id'))
    if args.expect_root:
        root_matches=status.get('instance_root')==args.expect_root
        check('expected_instance_root',root_matches,'experimental worktree root matched' if root_matches else 'mismatch')
    if args.expect_model:
        display_name=vision.get('name')
        pg.wait_for_function("expected=>document.getElementById('analyze-button').textContent.includes(expected)",arg=display_name)
        check('ui_selected_model_label',display_name in pg.locator('#analysis-description').inner_text(),pg.locator('#analysis-description').inner_text())
    if XSS:
        search('ZX-4471','exact')
        check('xss_card_escaped',pg.evaluate("window.__xss===undefined && !document.querySelector('.ocr-preview b, .ocr-preview img')"),pg.locator('.ocr-preview').first.inner_text())
        pg.locator('.card [data-open]').first.click();pg.wait_for_selector('#detail-dialog[open]')
        check('xss_dialog_escaped',pg.evaluate("window.__xss===undefined && !document.querySelector('#detail-ocr b, #detail-meta img')"))
        pg.screenshot(path=str(SHOTS/'xss_escaped.png'))
        print(json.dumps(log));b.close();sys.exit(0 if all(c['ok'] for c in log['checks'].values()) else 1)
    pg.wait_for_function("document.getElementById('asset-count').textContent!=='0'")
    count=pg.inner_text('#asset-count');check('collection_count',count=='62',count)
    pg.screenshot(path=str(SHOTS/'00_home.png'))
    t=time.perf_counter();pg.click('[data-query="Find the screenshot where my GPU ran out of memory"]');wait_idle()
    check('suggestion_gpu',first_name().startswith('terminal_cuda'),[names()[:3],round(time.perf_counter()-t,2)])
    check('best_badge',pg.locator('.card.best .rank-badge').first.inner_text().strip().endswith('BEST MATCH'))
    pg.screenshot(path=str(SHOTS/'01_hybrid_gpu.png'))
    search('GPU memory problem','semantic');check('semantic_gpu',first_name().startswith('terminal_cuda'),names()[:3])
    check('semantic_no_ocr_badge',pg.locator('.match-badge').count()==0)
    search('Find ERESOLVE','hybrid');check('eresolve_top',first_name().startswith('terminal_eresolve'),names()[:3])
    check('eresolve_ocr_badge',pg.locator('.card').first.locator('.match-badge').count()==1)
    pg.screenshot(path=str(SHOTS/'02_eresolve_ocr_match.png'))
    search('ModuleNotFoundError','exact');check('exact_module',first_name().startswith('terminal_module') and len(names())==2,names())
    search('CUDA out of memory','hybrid');pg.click('#first-button');wait_idle()
    check('first_occurrence',first_name()=='terminal_cuda_01.png' and pg.is_visible('#temporal-note'),[names(),pg.inner_text('#results-title')])
    check('timeline_view',pg.inner_text('#view-label')=='Timeline')
    pg.screenshot(path=str(SHOTS/'03_first_occurrence.png'))
    # Regression: a new typed query must leave First-occurrence mode.
    search('Find the image with a red backpack','semantic')
    check('red_backpack_after_timeline',first_name()=='object_study_01.png' and pg.input_value('#sort')=='relevance',[names()[:3],pg.input_value('#sort')])
    pg.screenshot(path=str(SHOTS/'04_red_backpack.png'))
    search('When did this first happen?');check('contextual_first',first_name().startswith('object_study') and pg.input_value('#sort')=='oldest',names()[:3])
    search('Show slides about databases','semantic');check('db_slides',first_name().startswith(('lecture_normalization','lecture_erd','lecture_joins')),names())
    n0=len(names());pg.click('#more-results');wait_idle();check('show_more',len(names())>n0,[n0,len(names())])
    pg.select_option('#sort','newest');wait_idle();ts=pg.locator('.card-time').all_inner_texts();check('sort_newest_applied',pg.input_value('#sort')=='newest',ts[:2])
    pg.select_option('#sort','relevance');wait_idle()
    pg.fill('#query','');pg.press('#query','Enter');wait_idle()
    pg.fill('#date-start','2026-10-14');pg.fill('#date-end','2026-10-14');pg.dispatch_event('#date-end','change');wait_idle()
    meta=pg.inner_text('#results-meta');check('date_filter_one_day',meta.split(' of ')[1].split(' ')[0] in ('4','5'),meta)
    pg.click('#clear-filters');wait_idle()
    search('CUDA out of memory','hybrid')
    pg.locator('.card .open-evidence').first.click();pg.wait_for_selector('#detail-dialog[open]')
    pg.wait_for_function("document.getElementById('detail-image').complete&&document.getElementById('detail-image').naturalWidth>0")
    check('evidence_modal',pg.inner_text('#detail-title').startswith('terminal_cuda') and 'CUDA' in pg.inner_text('#detail-ocr'),pg.inner_text('#ocr-label'))
    pg.click('#copy-ocr');time.sleep(.5);clip=pg.evaluate('navigator.clipboard.readText()');check('ocr_copy','out of memory' in clip,len(clip))
    pg.fill('#question','What probably caused this error? Separate what is visible from your guess.')
    t=time.perf_counter()
    with pg.expect_response(lambda response:response.url.endswith('/api/analyze'),timeout=420000) as analysis_response:
        pg.click('#analyze-button')
    analysis_payload=analysis_response.value.json()
    pg.wait_for_selector('.analysis-evidence',timeout=420000);analysis_s=round(time.perf_counter()-t,1)
    ans=pg.inner_text('#analysis-result');check('analysis',('memory' in ans.lower()) and 'terminal_cuda' in ans,[analysis_s,ans[:400]])
    check('analysis_model_identity',analysis_payload.get('backend')==vision.get('backend') and analysis_payload.get('model')==vision.get('name'),
          {'backend':analysis_payload.get('backend'),'model':analysis_payload.get('model'),'runtime':analysis_payload.get('runtime')})
    check('analysis_evidence_identity',analysis_payload.get('evidence',{}).get('filename')=='terminal_cuda_01.png',analysis_payload.get('evidence',{}).get('filename'))
    log['analysis_seconds']=analysis_s;log['analysis_text']=ans
    pg.screenshot(path=str(SHOTS/'05_evidence_analysis.png'))
    pg.click('#similar-button');wait_idle();check('similar',pg.inner_text('#results-title')=='Visually related' and not pg.is_visible('#detail-dialog'),names())
    pg.screenshot(path=str(SHOTS/'06_similar.png'))
    pg.locator('.card [data-open]').first.click();pg.wait_for_selector('#detail-dialog[open]');pg.keyboard.press('Escape')
    check('escape_closes_dialog',not pg.evaluate("document.getElementById('detail-dialog').open"))
    pg.locator('h1').click();pg.keyboard.press('/');check('slash_focuses_search',pg.evaluate("document.activeElement.id")=='query')
    pg.keyboard.press('Tab');check('tab_reaches_search_button',pg.evaluate("document.activeElement.id")=='search-button')
    # Indexing via UI: re-index demo (all should be skipped; stays responsive).
    pg.click('#demo-button');pg.wait_for_function("document.getElementById('job-title').textContent==='Indexing complete'",timeout=300000)
    check('ui_reindex_skips',' 0 new' in pg.inner_text('#job-detail'),pg.inner_text('#job-detail'))
    pg.fill('#folder','C:\\definitely\\missing');pg.click('#index-button');pg.wait_for_selector('#notice:not([hidden])')
    check('bad_folder_error_visible','existing folder' in pg.inner_text('#notice'),pg.inner_text('#notice'))
    log['broken_images_desktop']=broken_images()
    m=b.new_context(viewport={'width':390,'height':844},bypass_csp=True).new_page();m.goto(BASE)
    m.wait_for_function("document.querySelectorAll('.card').length>0");m.fill('#query','Find ERESOLVE');m.press('#query','Enter')
    m.wait_for_function("!document.getElementById('results').classList.contains('loading')&&document.querySelector('.card .card-title')?.textContent.startsWith('terminal_eresolve')",timeout=60000)
    overflow=m.evaluate('document.documentElement.scrollWidth-document.documentElement.clientWidth');check('narrow_no_horizontal_overflow',overflow<=1,overflow)
    m.screenshot(path=str(SHOTS/'07_narrow_layout.png'),full_page=False)
    m.locator('.card .open-evidence').first.click();m.wait_for_selector('#detail-dialog[open]');m.screenshot(path=str(SHOTS/'08_narrow_dialog.png'))
    log['broken_images_narrow']=m.evaluate("[...document.images].filter(i=>i.complete&&i.src&&i.naturalWidth===0).map(i=>i.src)")
    expected422=any('422' in e and '/api/index' in e for e in log['http_errors'])
    console=[e for e in log['console_errors'] if not (expected422 and '422' in e)]  # the deliberate bad-folder request
    check('no_console_errors',not console,console[:5])
    check('no_failed_requests',not log['failed_requests'],log['failed_requests'][:5])
    check('no_broken_images',not log['broken_images_desktop'] and not log['broken_images_narrow'])
    unexpected=[e for e in log['http_errors'] if '/api/index' not in e]
    check('no_unexpected_http_errors',not unexpected,unexpected[:5])
    b.close()
log['provenance']={'instance_root':'.' if args.expect_root and status.get('instance_root')==args.expect_root else '<unverified>',
                   'instance_root_verified':bool(args.expect_root and status.get('instance_root')==args.expect_root)}
(SHOTS/'browser_qa.json').write_text(json.dumps(log,indent=2),encoding='utf8')
print(json.dumps({'passed':sum(c['ok'] for c in log['checks'].values()),'total':len(log['checks'])}))
sys.exit(0 if all(c['ok'] for c in log['checks'].values()) else 1)
