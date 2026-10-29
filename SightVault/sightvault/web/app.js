'use strict';
const $ = id => document.getElementById(id);
let autoTemporal=false, mode='hybrid', view='memory', selected=null, jobId=null, currentResults=[], requestId=0, lastQuery='', visibleLimit=0;
const escapeHtml = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtDate = value => new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'});
const duration = value => value>=1000 ? `${(value/1000).toFixed(2)} s` : `${Math.round(value)} ms`;
async function api(url, options={}) {
  const response=await fetch(url,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});
  let data; try{data=await response.json();}catch{throw Error('The local server returned an unreadable response.');}
  if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));
  return data;
}
function notice(message){$('notice').textContent=message||'';$('notice').hidden=!message;}
let toastTimer;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,3500);}
async function refreshStatus(){
  try {
    const data=await api('/api/status');
    $('asset-count').textContent=data.total.toLocaleString();$('nav-count').textContent=data.total;$('indexed-metric').textContent=data.total;
    const names={embedding:'EmbeddingGemma 2',ocr:'MiniCPM-OCR 1.0',vision:'MiniCPM-V 4.7 1B'};
    $('model-status').innerHTML=Object.entries(data.models).map(([name,state])=>`<div><b>${names[name]}</b><br>${escapeHtml(state.loaded?`Loaded · ${state.device}`:state.available?'Downloaded · loads on demand':'Not downloaded')}${state.error?`<br>${escapeHtml(state.error.slice(0,120))}`:''}</div>`).join('');
    $('model-status').innerHTML+=`<div>${data.with_embeddings} embeddings · ${data.with_ocr} OCR records</div>`;
    return data;
  }catch(error){notice('Could not reach the local app. '+error.message);}
}
function setView(next){view=next;$('memory-nav').classList.toggle('active',view==='memory');$('timeline-nav').classList.toggle('active',view==='timeline');$('view-label').textContent=view==='memory'?'Visual memory':'Timeline';$('results').classList.toggle('timeline',view==='timeline');}
function renderCards(results, query){
  currentResults=results;
  $('results').innerHTML=results.map((row,i)=>`<article class="card ${i===0&&query&&$('sort').value==='relevance'?'best':''}"><button class="image-wrap" data-open="${row.id}" aria-label="Open ${escapeHtml(row.filename)}"><img src="/api/assets/${row.id}/image?thumbnail=true" alt="${escapeHtml(row.filename)}" loading="lazy"><span class="rank-badge">${i===0&&query&&$('sort').value==='relevance'?'✦ BEST MATCH':$('sort').value==='oldest'&&i===0&&query?'◷ EARLIEST MATCH':String(i+1).padStart(2,'0')}</span></button><div class="card-body"><h3 class="card-title" title="${escapeHtml(row.filename)}">${escapeHtml(row.filename)}</h3><div class="card-time">◷ ${escapeHtml(fmtDate(row.timestamp))}</div><div class="match-row">${row.exact_match?'<span class="match-badge">✓ OCR MATCH</span>':''}${row.semantic_score!==null&&row.semantic_score!==undefined?`<span class="semantic-badge">Similarity ${row.semantic_score.toFixed(3)}</span>`:''}${!query?`<span class="semantic-badge">${row.width} × ${row.height}</span>`:''}</div><p class="ocr-preview">${escapeHtml(row.ocr_preview||row.ocr_text||'No text detected in this image.')}</p><div class="card-footer"><span>LOCAL EVIDENCE</span><button class="open-evidence" data-open="${row.id}">Open & analyze ↗</button></div></div></article>`).join('');
  $('empty').hidden=results.length>0;
  if(!results.length){$('empty').querySelector('h3').textContent=query?'Nothing matched this search.':'Make room for a better memory.';$('empty').querySelector('p').innerHTML=query?'Try fewer words, another search mode or a wider date range.':'Add a folder of screenshots, slides or photos.<br>Or explore a collection made for the demo.';$('empty-demo').hidden=!!query;}
}
async function search(extra={}){
  const serial=++requestId;
  let query=$('query').value.trim();
  let contextNote='';
  const contextual=/^(when did (this|it) (first )?(happen|occur|appear)\??|when was this first)\s*$/i.test(query);
  // A new typed query must not inherit an automatic First-occurrence/timeline sort from the previous one.
  if(extra.fresh&&!contextual){if(query!==lastQuery&&autoTemporal){$('sort').value='relevance';setView('memory');}autoTemporal=false;}
  delete extra.fresh;
  if (/^(when did (this|it) (first )?(happen|occur|appear)\??|when was this first)\s*$/i.test(query)) {
    if(!lastQuery){notice('Search for something first, then choose First occurrence.');return;}
    query=lastQuery;$('sort').value='oldest';autoTemporal=true;contextNote='Using the previous search to find the earliest indexed match.';
  }
  lastQuery=query;
  if(!extra.more)visibleLimit=query||extra.similar_id?5:12;
  delete extra.more;
  const params=new URLSearchParams({q:query,mode,sort:$('sort').value,top_k:String(visibleLimit),...extra});
  // Dates are interpreted in the browser's local timezone, matching how timestamps are displayed.
  const localMidnight=(value,addDays=0)=>{const [y,m,d]=value.split('-').map(Number);return new Date(y,m-1,d+addDays).toISOString();};
  if($('date-start').value)params.set('start',localMidnight($('date-start').value));
  if($('date-end').value)params.set('end',localMidnight($('date-end').value,1));
  $('search-button').disabled=true;$('search-button').textContent='Searching…';$('results').classList.add('loading');notice('');
  try{
    const data=await api('/api/search?'+params);
    if(serial!==requestId)return;
    if(data.sort!==params.get('sort'))autoTemporal=true;
    $('sort').value=data.sort;
    if(data.sort==='oldest'&&query)setView('timeline');
    renderCards(data.results,query||extra.similar_id);
    $('more-results').hidden=data.results.length>=data.total_matches||data.results.length>=100||!!extra.similar_id;
    $('latency-metric').textContent=duration(data.latency_ms);
    $('results-title').textContent=extra.similar_id?'Visually related':$('sort').value==='oldest'&&query?'First in your visual memory':query?'Found in your memory':view==='timeline'?'Your timeline':'Your collection';
    $('results-meta').textContent=`${data.results.length} of ${data.total_matches} ${query?'candidates':'images'} · ${duration(data.latency_ms)}${extra.similar_id?' · image similarity':query?` · ${mode==='exact'?'OCR + filename':mode==='semantic'?'direct image embeddings':'visual meaning + text'}`:''}`;
    $('temporal-note').hidden=!data.temporal_note;$('temporal-note').textContent=data.temporal_note||'';
    $('first-button').disabled=!query;
    notice(data.warning||contextNote);
  }catch(error){if(serial===requestId)notice(error.message);}
  finally{if(serial===requestId){$('search-button').disabled=false;$('search-button').innerHTML='Search <span>↵</span>';$('results').classList.remove('loading');}}
}
async function indexFolder(demo=false){
  if(jobId)return;
  notice('');$('index-button').disabled=$('demo-button').disabled=$('empty-demo').disabled=true;
  try{
    const data=await api('/api/index',{method:'POST',body:JSON.stringify({folder:$('folder').value,demo,ocr:$('ocr-backend').value})});
    jobId=data.job_id;$('job-panel').hidden=false;$('cancel-job').hidden=false;pollJob();
  }catch(error){notice(error.message);$('index-button').disabled=$('demo-button').disabled=$('empty-demo').disabled=false;}
}
async function pollJob(){
  if(!jobId)return;
  try{
    const job=await api('/api/jobs/'+jobId);
    $('job-title').textContent=job.state==='running'?'Building your memory':job.state==='complete'?'Indexing complete':job.state==='cancelled'?'Indexing stopped':'Indexing failed';
    $('job-progress').max=job.total||1;$('job-progress').value=job.processed||0;
    $('job-current').textContent=job.current||'Loading models…';
    $('job-detail').textContent=`${job.processed}/${job.total} · ${job.indexed} new · ${job.skipped} skipped · ${job.failed} failed · ${Math.round(job.elapsed_s)} s`;
    if(job.state==='running'){setTimeout(pollJob,1200);return;}
    jobId=null;$('cancel-job').hidden=true;$('index-button').disabled=$('demo-button').disabled=$('empty-demo').disabled=false;
    await refreshStatus();await search();
    if(job.error||job.warnings?.length||job.errors?.length)notice([job.error,...(job.warnings||[]),...(job.errors||[]).map(e=>`${e.file}: ${e.message}`)].filter(Boolean).join(' · '));
    else toast(`${job.indexed} images added. ${job.skipped} already indexed.`);
  }catch(error){notice('Lost contact with the indexing job: '+error.message);jobId=null;$('index-button').disabled=$('demo-button').disabled=$('empty-demo').disabled=false;}
}
async function openDetail(id){
  try{
    selected=await api('/api/assets/'+id);
    $('detail-title').textContent=selected.filename;$('detail-image').src=`/api/assets/${id}/image`;$('image-link').href=`/api/assets/${id}/image`;
    $('detail-image').alt=selected.filename;
    $('detail-meta').innerHTML=`<strong>${escapeHtml(fmtDate(selected.timestamp))}</strong> · ${escapeHtml(selected.timestamp_source)}<br>${Number(selected.width)} × ${Number(selected.height)} · ${escapeHtml(selected.filepath)}${selected.source_exists===false?' <em>(original moved or deleted; saved copy shown)</em>':''}<br>Indexed ${escapeHtml(fmtDate(selected.indexed_at))}`;
    $('detail-ocr').textContent=selected.ocr_text||'No text was extracted.';$('ocr-label').textContent=selected.ocr_backend;
    $('analysis-result').innerHTML='<div class="analysis-placeholder">The image comes first.<br>The explanation comes next.</div>';
    $('analyze-button').disabled=false;$('analyze-button').innerHTML='Analyze with MiniCPM-V <span>↗</span>';
    $('detail-dialog').showModal();
  }catch(error){notice(error.message);}
}
let analysisSerial=0;
async function analyze(){
  if(!selected)return;
  const id=selected.id,serial=++analysisSerial,question=$('question').value.trim();
  if(!question){toast('Enter a question about the image.');return;}
  $('analyze-button').disabled=true;$('analyze-button').textContent='Reading the evidence…';$('analysis-result').textContent='Loading MiniCPM-V locally. The first question takes longer while the model loads.';
  try{
    const data=await api('/api/analyze',{method:'POST',body:JSON.stringify({asset_id:id,question})});
    if(selected?.id!==id||serial!==analysisSerial)return;
    $('analysis-result').textContent=data.answer;
    if(data.ocr_backend_note){const n=document.createElement('div');n.className='analysis-evidence';n.textContent=data.ocr_backend_note;$('analysis-result').append(n);}
    const evidence=document.createElement('div');evidence.className='analysis-evidence';evidence.textContent=`Source: ${data.evidence.filename} · ${data.model} · ${duration(data.latency_ms)}`;$('analysis-result').append(evidence);
    refreshStatus();
  }catch(error){if(selected?.id===id&&serial===analysisSerial)$('analysis-result').textContent=error.message;}
  finally{if(selected?.id===id&&serial===analysisSerial){$('analyze-button').disabled=false;$('analyze-button').innerHTML='Analyze with MiniCPM-V <span>↗</span>';}}
}
$('search-form').addEventListener('submit',event=>{event.preventDefault();search({fresh:true});});
$('index-button').onclick=()=>indexFolder();$('demo-button').onclick=$('empty-demo').onclick=()=>indexFolder(true);
$('cancel-job').onclick=async()=>{if(jobId){await api('/api/jobs/'+jobId+'/cancel',{method:'POST',body:'{}'});toast('Stopping after the current image.');}};
document.querySelectorAll('[data-query]').forEach(button=>button.onclick=()=>{$('query').value=button.dataset.query;$('sort').value='relevance';setView('memory');autoTemporal=false;search();});
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{mode=button.dataset.mode;document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('selected',b===button));search();});
$('sort').onchange=()=>search();$('date-start').onchange=$('date-end').onchange=()=>search();
$('more-results').onclick=()=>{visibleLimit=Math.min(visibleLimit+12,100);search({more:true});};
$('clear-filters').onclick=()=>{$('date-start').value=$('date-end').value='';search();};
$('date-start').title=$('date-end').title='Dates use your local timezone';
$('memory-nav').onclick=()=>{setView('memory');$('sort').value='relevance';search();};
$('timeline-nav').onclick=()=>{setView('timeline');$('sort').value='oldest';search();};
$('first-button').onclick=()=>{$('sort').value='oldest';setView('timeline');autoTemporal=true;search();};
$('results').onclick=event=>{const target=event.target.closest('[data-open]');if(target)openDetail(Number(target.dataset.open));};
$('close-detail').onclick=()=>$('detail-dialog').close();
$('detail-dialog').addEventListener('close',()=>{analysisSerial++;selected=null;});
$('detail-dialog').addEventListener('click',event=>{if(event.target===$('detail-dialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
$('analyze-button').onclick=analyze;
$('copy-ocr').onclick=async()=>{try{await navigator.clipboard.writeText(selected?.ocr_text||'');toast('OCR text copied.');}catch{toast('Copy unavailable. Select the extracted text to copy it.');}};
$('similar-button').onclick=()=>{if(!selected)return;const id=selected.id;$('detail-dialog').close();$('query').value='';$('sort').value='relevance';setView('memory');search({similar_id:id});};
document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)&&!$('detail-dialog').open){event.preventDefault();$('query').focus();}});
(async()=>{await refreshStatus();await search();})();
