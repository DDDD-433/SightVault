"""Normal-CSP UI regression checks for async evidence, pagination and date boundaries."""
import asyncio,json,re,sys
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from playwright.async_api import async_playwright,expect
ROOT=Path(__file__).resolve().parent.parent
BASE=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:7861'
async def main():
    checks={};requests=[];errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        context=await browser.new_context(timezone_id='America/New_York',viewport={'width':1440,'height':900})
        page=await context.new_page()
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:requests.append(r.url) if '/api/search?' in r.url else None)
        await page.goto(BASE)
        await expect(page.locator('.card')).to_have_count(12)
        await page.locator('[data-mode="exact"]').click()
        await page.locator('#query').fill('demo');await page.locator('#query').press('Enter')
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
    report={'normal_csp':True,'checks':checks,'page_errors':errors,'passed':all(checks.values())}
    (ROOT/'qa/independent-review/browser-races.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
