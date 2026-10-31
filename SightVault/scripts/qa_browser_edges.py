"""Normal-CSP UI regression checks for async evidence, pagination and date boundaries."""
import argparse,asyncio,json,re,sys
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from playwright.async_api import async_playwright,expect
ROOT=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('base_url',nargs='?',default='http://127.0.0.1:7861')
parser.add_argument('--expect-backend',choices=('minicpm','lfm3','omni'))
parser.add_argument('--expect-model')
parser.add_argument('--expect-root')
parser.add_argument('--output',type=Path,default=ROOT/'qa'/'normal-csp-experimental.json')
ARGS=parser.parse_args();BASE=ARGS.base_url
async def main():
    checks={};requests=[];errors=[];identity={}
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        context=await browser.new_context(timezone_id='America/New_York',viewport={'width':1440,'height':900})
        page=await context.new_page()
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:requests.append(r.url) if '/api/search?' in r.url else None)
        async with page.expect_response(lambda response:'/api/search?' in response.url) as initial_search:
            await page.goto(BASE)
        await (await initial_search.value).json()
        identity=await page.evaluate("async()=>await (await fetch('/api/status')).json()")
        vision=identity.get('models',{}).get('vision',{})
        checks['api_vision_identity_consistent']=identity.get('vision_backend')==vision.get('backend') and identity.get('vision_model')==vision.get('name')
        if ARGS.expect_backend: checks['expected_backend']=vision.get('backend')==ARGS.expect_backend
        if ARGS.expect_model: checks['expected_model_id']=vision.get('model_id')==ARGS.expect_model
        if ARGS.expect_root: checks['expected_instance_root']=identity.get('instance_root')==ARGS.expect_root
        if ARGS.expect_model:
            display_name=vision.get('name')
            await expect(page.locator('#analyze-button')).to_contain_text(display_name)
            checks['ui_selected_model_label']=display_name in await page.locator('#analysis-description').inner_text()
        await expect(page.locator('.card')).to_have_count(12)
        async with page.expect_response(lambda response:'mode=exact' in response.url and 'q=' in response.url) as exact_mode_search:
            await page.locator('[data-mode="exact"]').click()
        await (await exact_mode_search.value).json()
        await page.locator('#query').fill('demo')
        async with page.expect_response(lambda response:'q=demo' in response.url and 'mode=exact' in response.url) as demo_search:
            await page.locator('#query').press('Enter')
        await (await demo_search.value).json()
        await expect(page.locator('.card')).to_have_count(5)
        await page.locator('#query').fill('ERESOLVE')
        await page.locator('#more-results').click()
        await expect(page.locator('.card')).to_have_count(17)
        checks['pagination_uses_submitted_query']=parse_qs(urlparse(requests[-1]).query)['q']==['demo']
        cards=page.locator('.image-wrap');first=await cards.nth(0).get_attribute('data-open');second=await cards.nth(1).get_attribute('data-open')
        second_name=await page.locator('.card-title').nth(1).inner_text()
        async def delay(route):
            response=await route.fetch()
            await asyncio.sleep(.5 if route.request.url.endswith('/'+first) else .02)
            await route.fulfill(response=response)
        await page.route(re.compile(r'/api/assets/\d+$'),delay)
        await cards.nth(0).click();await cards.nth(1).click()
        await expect(page.locator('#detail-title')).to_have_text(second_name)
        await asyncio.sleep(.65)
        checks['latest_evidence_request_wins']=await page.locator('#detail-title').inner_text()==second_name
        await page.keyboard.press('Escape')
        await page.locator('#date-start').fill('0050-03-01');await page.locator('#date-start').dispatch_event('change')
        await asyncio.sleep(.2)
        checks['early_year_preserved']=parse_qs(urlparse(requests[-1]).query)['start'][0].startswith('0050-03-01')
        await page.locator('#date-start').fill('2026-03-08');await page.locator('#date-end').fill('2026-03-08');await page.locator('#date-end').dispatch_event('change')
        await asyncio.sleep(.2)
        bounds=parse_qs(urlparse(requests[-1]).query)
        checks['dst_local_day_is_23_hours']=bounds['start']==['2026-03-08T05:00:00.000Z'] and bounds['end']==['2026-03-09T04:00:00.000Z']
        checks['no_page_errors']=not errors
        await browser.close()
    root_matches=identity.get('instance_root')==str(ROOT)
    report={'normal_csp':True,'instance_root':'.' if root_matches else '<external-instance-root>',
            'instance_root_verified':root_matches,
            'vision_identity':{'backend':identity.get('vision_backend'),'model':identity.get('vision_model'),
                               'model_id':identity.get('models',{}).get('vision',{}).get('model_id')},
            'checks':checks,'page_errors':errors,'passed':all(checks.values())}
    ARGS.output.parent.mkdir(parents=True,exist_ok=True)
    ARGS.output.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
