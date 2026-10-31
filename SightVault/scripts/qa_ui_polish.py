"""Real-browser reflow, reduced-motion, escaped-highlight and missing-runtime UI acceptance.
The second URL must be the explicitly disposable malicious-text/missing-model fixture.
"""
import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True);p.add_argument('--error-base',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--screenshots',type=Path,default=ROOT/'output/playwright/ui-polish');a=p.parse_args();a.screenshots.mkdir(parents=True,exist_ok=True)
 checks={};errors=[];contexts=[]
 with sync_playwright() as pw:
  browser=pw.chromium.launch()
  for label,width,height,dpr,touch in [('desktop',1440,900,1,False),('200pct-equivalent-reflow',720,450,2,False),('narrow',390,844,1,True),('minimum',320,700,1,True)]:
   context=browser.new_context(viewport={'width':width,'height':height},device_scale_factor=dpr,is_mobile=touch,has_touch=touch,reduced_motion='reduce')
   page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)));page.goto(a.base);expect(page.locator('.card')).to_have_count(12);page.wait_for_load_state('networkidle')
   checks[label+'_reflow']=page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
   checks[label+'_reduced_motion']=page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches && getComputedStyle(document.getElementById('search-button')).transitionDuration.split(',').every(x=>parseFloat(x)===0)")
   checks[label+'_import_initially_collapsed']=not page.locator('#folder').is_visible()
   page.locator('#import-toggle').click();expect(page.locator('#folder')).to_be_visible();checks[label+'_import_expanded']=page.locator('#import-toggle').get_attribute('aria-expanded')=='true'
   page.locator('#folder').press('Escape');checks[label+'_import_escape_closes']=not page.locator('#folder').is_visible() and page.locator('#import-toggle').get_attribute('aria-expanded')=='false'
   page.locator('#settings summary').click();expect(page.locator('#model-status')).to_be_visible();checks[label+'_settings_independent']=not page.locator('#folder').is_visible()
   page.locator('#settings summary').press('Escape')
   page.locator('.open-evidence').first.click();expect(page.locator('#detail-dialog')).to_be_visible();checks[label+'_dialog_named']=page.locator('#detail-dialog').get_attribute('aria-labelledby')=='detail-title'
   checks[label+'_dialog_fits']=page.locator('#detail-dialog').evaluate('(node)=>{const r=node.getBoundingClientRect();return r.left>=-1&&r.right<=innerWidth+1}')
   page.locator('#question').scroll_into_view_if_needed();checks[label+'_question_reachable']=page.locator('#question').is_visible()
   page.screenshot(path=str(a.screenshots/(label+'.png')))
   contexts.append({'label':label,'css_viewport':[width,height],'device_scale_factor':dpr,'touch':touch});context.close()
  context=browser.new_context(permissions=['clipboard-read','clipboard-write']);page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)));page.goto(a.error_base);expect(page.locator('.card')).to_have_count(1)
  page.locator('.open-evidence').first.click();page.locator('#copy-ocr').click();expect(page.locator('#detail-status')).to_contain_text('copied');checks['in_dialog_copy_feedback']=True
  checks['clipboard_literal_text']='ZX-4471' in page.evaluate('navigator.clipboard.readText()')
  page.locator('#question').fill('');page.locator('#analyze-button').click();expect(page.locator('#detail-status')).to_contain_text('Enter a question');checks['empty_question_focus']=page.locator('#question').evaluate('(node)=>document.activeElement===node')
  page.locator('#question').fill('Describe this visible error.');page.locator('#analyze-button').click();expect(page.locator('#analysis-result [role="alert"]')).to_be_visible(timeout=30000)
  message=page.locator('#analysis-result').inner_text();checks['real_missing_runtime_error_visible']='LFM3-Omni' in message and 'could not analyze' in message
  page.screenshot(path=str(a.screenshots/'missing-runtime.png'));page.locator('#close-detail').click()
  slow=context.new_cdp_session(page);slow.send('Network.enable');slow.send('Network.emulateNetworkConditions',{'offline':False,'latency':500,'downloadThroughput':-1,'uploadThroughput':-1})
  page.locator('[data-mode="exact"]').click();expect(page.locator('#search-button')).to_be_disabled();page.locator('#query').fill('ZX-4471')
  with page.expect_response(lambda response:'/api/search?' in response.url and 'q=ZX-4471' in response.url):page.locator('#query').press('Enter')
  expect(page.locator('.ocr-preview mark').first).to_be_visible();checks['enter_submits_while_previous_request_pending']=True
  checks['escaped_highlights']=page.locator('.ocr-preview b, .ocr-preview img, .card-title img').count()==0 and page.evaluate('window.__xss===undefined')
  checks['exact_search_survives_optional_error']=page.locator('.card').count()==1 and 'ZX-4471' in page.locator('.ocr-preview').inner_text()
  page.locator('.open-evidence').first.click();checks['ocr_remains_literal']=page.locator('#detail-ocr').evaluate('(node)=>node.children.length===0 && node.textContent.includes("<img")')
  checks['no_page_errors']=not errors;browser.close()
 report={'checks':checks,'passed':all(checks.values()),'page_errors':errors,'contexts':contexts,'normal_csp':True,'zoom_limit':'720 CSS-pixel viewport at 2x DPR is a 200%-equivalent reflow proxy; native browser zoom and formal screen-reader/WCAG compliance are not claimed.','error_fixture':'Disposable one-row database containing synthetic malicious markup; models directory deliberately absent; no model inference is mocked as successful.'}
 a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'checks':checks},indent=2));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
