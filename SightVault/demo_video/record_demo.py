import asyncio,json,time
from pathlib import Path
from playwright.async_api import async_playwright,expect
ROOT=Path(__file__).resolve().parent.parent;WORK=ROOT/'demo_video';BASE='http://127.0.0.1:7861'
async def main():
 story=json.loads((WORK/'timeline.json').read_text());events=[];errors=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch()
  context=await browser.new_context(viewport={'width':1600,'height':780},record_video_dir=str(WORK/'raw'),record_video_size={'width':1600,'height':780},timezone_id='America/New_York',bypass_csp=True)
  page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
  async def install_pointer():
   await page.evaluate('''() => {const c=document.createElement('div');c.id='film-pointer';c.style.cssText='position:fixed;left:1300px;top:350px;width:27px;height:32px;z-index:2147483646;pointer-events:none;filter:drop-shadow(0 2px 3px #000)';c.innerHTML='<svg viewBox="0 0 24 30"><path d="M2 1V23L8 17L13 27L17 25L12 15H22Z" fill="white" stroke="#102012" stroke-width="1.5"/></svg>';document.body.append(c);document.addEventListener('mousemove',e=>{c.style.left=e.clientX+'px';c.style.top=e.clientY+'px'});}''')
  async def goto():
   await page.goto(BASE);await expect(page.locator('.card')).to_have_count(12);await install_pointer()
  async def marker():
   await page.evaluate("() => {const d=document.createElement('div');d.id='film-marker';d.style.cssText='position:fixed;inset:0;z-index:2147483647;background:rgb(255,0,255)';document.body.append(d)}")
   await page.wait_for_timeout(360)
   await page.evaluate("document.getElementById('film-marker').remove()")
  async def point(locator):
   box=await locator.bounding_box()
   if box:await page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2,steps=24)
   await page.wait_for_timeout(180)
  async def click(selector):
   locator=page.locator(selector);await point(locator);await locator.click()
  async def idle():await expect(page.locator('#search-button')).to_be_enabled(timeout=180000)
  async def search(query,mode=None):
   if mode:await click(f'[data-mode="{mode}"]');await idle()
   await click('#query');await page.locator('#query').fill('');await page.locator('#query').press_sequentially(query,delay=42);await page.locator('#query').press('Enter');await idle();await page.wait_for_timeout(400)
  async def prepare(scene):
   if scene['id'] in ('01_home','02_semantic'):await goto()
   if scene['id']=='05_analysis':
    await click('.card .open-evidence >> nth=0');await expect(page.locator('#detail-dialog')).to_be_visible()
   if scene['id']=='07_private':
    await page.keyboard.press('Escape');await page.locator('#query').fill('');await page.locator('#query').press('Enter');await idle()
  async def action(scene):
   ident=scene['id']
   if ident=='01_home':
    await page.wait_for_timeout(1600);await click('#demo-button');await expect(page.locator('#job-title')).to_have_text('Indexing complete',timeout=300000)
   elif ident=='02_semantic':
    await search('GPU memory problem','semantic');assert (await page.locator('.card-title').first.inner_text()).startswith('terminal_cuda')
    await point(page.locator('.card').first)
   elif ident=='03_exact':
    await search('Find ERESOLVE','hybrid');assert (await page.locator('.card-title').first.inner_text()).startswith('terminal_eresolve')
    await page.wait_for_timeout(1100);await click('.card .open-evidence >> nth=0');await expect(page.locator('#detail-dialog')).to_be_visible();await page.wait_for_timeout(1800)
    await page.locator('#detail-dialog details').evaluate("d=>d.open=true")
    await page.mouse.move(650,550);await page.mouse.wheel(0,210)
   elif ident=='04_timeline':
    await page.keyboard.press('Escape');await search('CUDA out of memory','hybrid');await click('#first-button');await idle()
    assert await page.locator('.card-title').first.inner_text()=='terminal_cuda_01.png';await point(page.locator('.card-time').first)
   elif ident=='05_analysis':
    await click('#question');await page.locator('#question').fill('');await page.locator('#question').press_sequentially('What probably caused this error? Separate visible facts from likely explanations.',delay=22)
    await click('#analyze-button');await expect(page.locator('.analysis-evidence')).to_be_visible(timeout=180000)
    assert 'memory' in (await page.locator('#analysis-result').inner_text()).lower();await point(page.locator('#analysis-result'))
   elif ident=='06_visual':
    await page.keyboard.press('Escape');await search('Find the image with a red backpack','semantic')
    names=await page.locator('.card-title').all_inner_texts();assert names[0]=='object_study_01.png' and 'object_study_02.png' in names[:3]
    await point(page.locator('.card').first);await page.wait_for_timeout(2200);await click('.card .open-evidence >> nth=0')
   elif ident=='07_private':
    await click('.side-bottom details summary');await page.locator('#model-status').scroll_into_view_if_needed();await point(page.locator('#model-status'))
  for scene in story:
   if scene['kind']!='screen':continue
   await prepare(scene);await marker();start=time.monotonic();await action(scene)
   remaining=scene['duration']-(time.monotonic()-start)
   if remaining<0:raise RuntimeError(f"Scene {scene['id']} overran by {-remaining:.2f}s")
   await page.wait_for_timeout(remaining*1000);await page.screenshot(path=str(WORK/(scene['id']+'.png')));await marker();await page.wait_for_timeout(220)
   events.append({'id':scene['id'],'duration_target':scene['duration'],'actual_seconds':time.monotonic()-start-.36})
   print(scene['id'],'recorded',flush=True)
  video=page.video;await context.close();path=await video.path();await browser.close()
 (WORK/'recording.json').write_text(json.dumps({'video':str(path),'scenes':events,'page_errors':errors,'recording_only_overlays':['cursor','trim markers'],'server_ui_and_model_results':'real, unmodified','csp_bypass_for_presentation_overlays_only':True},indent=2));assert not errors
 print(path,flush=True)
asyncio.run(main())
