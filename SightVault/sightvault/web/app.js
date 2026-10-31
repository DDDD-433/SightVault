'use strict';
const $ = id => document.getElementById(id);
let autoTemporal=false, mode='hybrid', view='memory', selected=null, jobId=null, currentResults=[], requestId=0, lastQuery='', visibleLimit=0, visionLabel='';
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
function setVisionLabel(label){
  visionLabel=label||'';
  $('analysis-description').textContent=visionLabel?`${visionLabel} analyzes this image locally when you ask. Answers stay attached to their source.`:'The selected local vision model analyzes this image when you ask. Answers stay attached to their source.';
  $('analyze-button').textContent=visionLabel?`Analyze with ${visionLabel}`:'Analyze image';
}
let toastTimer;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,3500);}
// The page toast sits beneath the modal's top layer, so dialog feedback is also announced inside the dialog.
let dialogStatusTimer;
function dialogStatus(message){$('detail-status').textContent=message;clearTimeout(dialogStatusTimer);dialogStatusTimer=setTimeout(()=>$('detail-status').textContent='',3500);}
function feedback(message){if($('detail-dialog').open)dialogStatus(message);else toast(message);}
// Untrusted OCR text is escaped segment by segment; only <mark> is added around matched terms.
function ocrSnippet(text, terms){
  const source=String(text||'');
  const lower=source.toLowerCase();
  const usable=lower.length===source.length?[...new Set((terms||[]).map(t=>String(t).toLowerCase().trim()).filter(t=>t.length>2))].sort((a,b)=>b.length-a.length):[];
  const firstHit=usable.map(t=>lower.indexOf(t)).filter(i=>i>=0).sort((a,b)=>a-b)[0];
  const start=firstHit!==undefined&&firstHit>70?firstHit-50:0, end=Math.min(source.length,start+240);
  const piece=source.slice(start,end), lowerPiece=piece.toLowerCase(), ranges=[];
  for(const term of usable){let at=lowerPiece.indexOf(term);while(at>=0){ranges.push([at,at+term.length]);at=lowerPiece.indexOf(term,at+term.length);}}
  ranges.sort((a,b)=>a[0]-b[0]);
  let html='',pos=0;
  for(const [from,to] of ranges){if(from<pos)continue;html+=escapeHtml(piece.slice(pos,from))+'<mark>'+escapeHtml(piece.slice(from,to))+'</mark>';pos=to;}
  html+=escapeHtml(piece.slice(pos));
  return (start>0?'… ':'')+html+(end<source.length?' …':'');
}
function emptyOcrMessage(row){
  if(row.ocr_backend==='off')return 'Text extraction was skipped for this image.';
  if(row.ocr_error)return 'Text extraction failed for this image. Re-index to retry.';
  if(row.ocr_backend==='unavailable')return 'No text extracted. OCR was unavailable when this image was indexed.';
  return 'No text detected in this image.';
}
async function refreshStatus(){
  try {
    const data=await api('/api/status');
    $('asset-count').textContent=data.total.toLocaleString();$('nav-count').textContent=data.total;$('indexed-metric').textContent=data.total;
    const names={embedding:data.embedding_model,ocr:data.ocr_model};
    const vision=data.models.vision;
    setVisionLabel(vision?.name||data.vision_model);
    if(data.embedding_model)$('embedding-metric').textContent=data.embedding_model;
    $('cloud-metric').textContent=data.cloud_api_calls??'—';
    $('model-status').innerHTML=Object.entries(data.models).map(([name,state])=>`<div class="model-row"><b>${escapeHtml(state.name||names[name]||name)}</b><span class="model-state ${state.loaded?'is-loaded':''}">${escapeHtml(state.loaded?`Loaded · ${state.device}`:state.available?'Downloaded · loads on demand':state.availability_reason||'Not downloaded')}</span>${state.runtime?`<span>${escapeHtml(state.runtime+' · '+state.precision)}</span>`:''}${state.error?`<span class="model-error">${escapeHtml(state.error.slice(0,120))}</span>`:''}</div>`).join('');
    $('model-status').innerHTML+=`<div class="model-row model-totals">${Number(data.with_embeddings)} embeddings · ${Number(data.with_ocr)} OCR records</div>`;
    return data;
  }catch(error){notice('Could not reach the local app. '+error.message);}
}
function setView(next){
  view=next;
  for(const [id,name] of [['memory-nav','memory'],['timeline-nav','timeline']]){$(id).classList.toggle('active',view===name);if(view===name)$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current');}
  $('view-label').textContent=view==='memory'?'Visual memory':'Timeline';$('results').classList.toggle('timeline',view==='timeline');
}
function renderCards(results, query, highlight=false){
  currentResults=results;
  const sort=$('sort').value;
  $('results').innerHTML=results.map((row,i)=>{
    const id=Number(row.id), name=escapeHtml(row.filename);
    const best=i===0&&query&&sort==='relevance', earliest=i===0&&query&&sort==='oldest';
    const rank=best?'<span class="rank-badge">BEST MATCH</span>':earliest?'<span class="rank-badge">EARLIEST MATCH</span>':query?`<span class="rank-badge">${String(i+1).padStart(2,'0')}</span>`:'';
    const similarity=row.semantic_score!==null&&row.semantic_score!==undefined?`<span class="semantic-badge" title="Embedding similarity between the search and this image. A ranking signal, not a confidence score.">Similarity ${Number(row.semantic_score).toFixed(3)}</span>`:'';
    const text=row.ocr_text||row.ocr_preview;
    const preview=text?`<p class="ocr-preview">${ocrSnippet(text,highlight?row.matched_terms:[])}</p>`:`<p class="ocr-preview is-empty">${escapeHtml(emptyOcrMessage(row))}</p>`;
    return `<article class="card${best?' best':''}${earliest?' earliest':''}" aria-labelledby="card-title-${id}"><button class="image-wrap" type="button" data-open="${id}" tabindex="-1" aria-label="Open ${name}"><img src="/api/assets/${id}/image?thumbnail=true" alt="${name}" loading="lazy"></button><div class="card-body"><h3 class="card-title" id="card-title-${id}" title="${name}">${name}</h3><div class="card-time"><time datetime="${escapeHtml(row.timestamp)}">${escapeHtml(fmtDate(row.timestamp))}</time></div><div class="match-row">${rank}${row.exact_match?'<span class="match-badge">TEXT MATCH</span>':''}${similarity}</div>${preview}<div class="card-footer"><span class="card-dims">${Number(row.width)} × ${Number(row.height)}</span><button class="open-evidence" type="button" data-open="${id}" aria-label="Open evidence and analyze ${name}">Open &amp; analyze</button></div></div></article>`;
  }).join('');
  $('empty').hidden=results.length>0;
  if(!results.length){$('empty').querySelector('h3').textContent=query?'Nothing matched this search.':'Make room for a better memory.';$('empty').querySelector('p').innerHTML=query?'Try fewer words, another search mode or a wider date range.':'Add a folder of screenshots, slides or photos.<br>Or explore a collection made for the demo.';$('empty-demo').hidden=!!query;}
}
async function search(extra={}){
  const serial=++requestId;
  let query=extra.more?lastQuery:$('query').value.trim();
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
  const localMidnight=(value,addDays=0)=>{const [y,m,d]=value.split('-').map(Number);const moment=new Date(0);moment.setFullYear(y,m-1,d+addDays);moment.setHours(0,0,0,0);return moment.toISOString();};
  if($('date-start').value)params.set('start',localMidnight($('date-start').value));
  if($('date-end').value)params.set('end',localMidnight($('date-end').value,1));
  $('search-button').disabled=true;$('search-button').textContent='Searching…';$('results').classList.add('loading');$('results').setAttribute('aria-busy','true');notice('');
  try{
    const data=await api('/api/search?'+params);
    if(serial!==requestId)return;
    if(data.sort!==params.get('sort'))autoTemporal=true;
    $('sort').value=data.sort;
    if(data.sort==='oldest'&&query)setView('timeline');
    renderCards(data.results,query||extra.similar_id,!!query&&mode!=='semantic'&&!extra.similar_id);
    $('more-results').hidden=data.results.length>=data.total_matches||data.results.length>=100||!!extra.similar_id;
    $('latency-metric').textContent=duration(data.latency_ms);
    $('results-title').textContent=extra.similar_id?'Visually related':$('sort').value==='oldest'&&query?'First in your visual memory':query?'Found in your memory':view==='timeline'?'Your timeline':'Your collection';
    $('results-meta').textContent=`${data.results.length} of ${data.total_matches} ${query?'candidates':'images'} · ${duration(data.latency_ms)}${extra.similar_id?' · image similarity':query?` · ${mode==='exact'?'OCR + filename':mode==='semantic'?'direct image embeddings':'visual meaning + text'}`:''}`;
    $('temporal-note').hidden=!data.temporal_note;$('temporal-note').textContent=data.temporal_note||'';
    $('first-button').disabled=!query;
    notice(data.warning||contextNote);
  }catch(error){if(serial===requestId)notice(error.message);}
  finally{if(serial===requestId){$('search-button').disabled=false;$('search-button').innerHTML='Search <span aria-hidden="true">↵</span>';$('results').classList.remove('loading');$('results').removeAttribute('aria-busy');}}
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
    $('job-panel').classList.toggle('is-error',job.state==='error');
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
function placeholder(){const p=document.createElement('div');p.className='analysis-placeholder';p.textContent='The image comes first. The explanation comes next.';$('analysis-result').replaceChildren(p);}
let detailSerial=0;
async function openDetail(id){
  const serial=++detailSerial;
  document.body.classList.add('is-busy');
  try{
    const detail=await api('/api/assets/'+id);
    if(serial!==detailSerial)return;
    selected=detail;
    $('detail-title').textContent=selected.filename;$('detail-image').src=`/api/assets/${id}/image`;$('image-link').href=`/api/assets/${id}/image`;
    $('detail-image').alt=selected.filename;
    const row=(label,value,cls='')=>`<div class="meta-row ${cls}"><dt>${label}</dt><dd>${value}</dd></div>`;
    $('detail-meta').innerHTML=
      row('File date',`${escapeHtml(fmtDate(selected.timestamp))} <span class="meta-sub">${escapeHtml(selected.timestamp_source)}</span>`)+
      row('Dimensions',`${Number(selected.width)} × ${Number(selected.height)} px`)+
      row('Indexed',escapeHtml(fmtDate(selected.indexed_at)))+
      row('Source file',`<code class="meta-path">${escapeHtml(selected.filepath)}</code>${selected.source_exists===false?'<em class="meta-flag">Original moved or deleted; saved copy shown</em>':''}`,'meta-wide');
    $('detail-ocr').textContent=selected.ocr_text||emptyOcrMessage(selected);$('detail-ocr').classList.toggle('is-empty',!selected.ocr_text);$('ocr-label').textContent=selected.ocr_backend;
    placeholder();$('analysis-result').removeAttribute('aria-busy');$('detail-status').textContent='';
    $('analyze-button').disabled=false;setVisionLabel(visionLabel);
    $('detail-dialog').showModal();
  }catch(error){if(serial===detailSerial){notice(error.message);toast(error.message);}}
  finally{document.body.classList.remove('is-busy');}
}
let analysisSerial=0;
async function analyze(){
  if(!selected)return;
  const id=selected.id,serial=++analysisSerial,question=$('question').value.trim();
  if(!question){feedback('Enter a question about the image.');$('question').focus();return;}
  const box=$('analysis-result');
  const loading=document.createElement('div');loading.className='analysis-loading';loading.textContent=`Loading ${visionLabel||'the local vision model'} locally. The first question takes longer while the model loads.`;
  $('analyze-button').disabled=true;$('analyze-button').textContent='Reading the evidence…';box.setAttribute('aria-busy','true');box.replaceChildren(loading);
  try{
    const data=await api('/api/analyze',{method:'POST',body:JSON.stringify({asset_id:id,question})});
    if(selected?.id!==id||serial!==analysisSerial)return;
    const answer=document.createElement('div');answer.className='analysis-answer';answer.textContent=data.answer;box.replaceChildren(answer);
    if(data.ocr_backend_note){const n=document.createElement('div');n.className='analysis-evidence';n.textContent=data.ocr_backend_note;box.append(n);}
    const evidence=document.createElement('div');evidence.className='analysis-evidence';evidence.textContent=`Source: ${data.evidence.filename} · ${data.model} · ${duration(data.latency_ms)}`;box.append(evidence);
    refreshStatus();
  }catch(error){if(selected?.id===id&&serial===analysisSerial){const e=document.createElement('div');e.className='analysis-error';e.setAttribute('role','alert');e.textContent=error.message;box.replaceChildren(e);}}
  finally{if(selected?.id===id&&serial===analysisSerial){box.removeAttribute('aria-busy');$('analyze-button').disabled=false;setVisionLabel(visionLabel);}}
}
$('search-form').addEventListener('submit',event=>{event.preventDefault();search({fresh:true});});
// Enter must still submit the latest query while a previous mode/search request is pending.
$('query').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();search({fresh:true});}});
$('index-button').onclick=()=>indexFolder();$('demo-button').onclick=$('empty-demo').onclick=()=>indexFolder(true);
$('cancel-job').onclick=async()=>{if(jobId){await api('/api/jobs/'+jobId+'/cancel',{method:'POST',body:'{}'});toast('Stopping after the current image.');}};
document.querySelectorAll('[data-query]').forEach(button=>button.onclick=()=>{$('query').value=button.dataset.query;$('sort').value='relevance';setView('memory');autoTemporal=false;search();});
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{mode=button.dataset.mode;document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});search();});
$('sort').onchange=()=>search();$('date-start').onchange=$('date-end').onchange=()=>search();
$('more-results').onclick=()=>{visibleLimit=Math.min(visibleLimit+12,100);search({more:true});};
$('clear-filters').onclick=()=>{$('date-start').value=$('date-end').value='';search();};
$('date-start').title=$('date-end').title='Dates use your local timezone';
$('memory-nav').onclick=()=>{setView('memory');$('sort').value='relevance';search();};
$('timeline-nav').onclick=()=>{setView('timeline');$('sort').value='oldest';search();};
$('first-button').onclick=()=>{$('sort').value='oldest';setView('timeline');autoTemporal=true;search();};
$('results').onclick=event=>{const target=event.target.closest('[data-open]');if(target)openDetail(Number(target.dataset.open));};
$('close-detail').onclick=()=>$('detail-dialog').close();
$('detail-dialog').addEventListener('close',()=>{analysisSerial++;detailSerial++;selected=null;});
// Close on a backdrop click only when the press also started on the backdrop, so drag-selecting text never closes the dialog.
let backdropPress=false;
$('detail-dialog').addEventListener('pointerdown',event=>{backdropPress=event.target===$('detail-dialog');});
$('detail-dialog').addEventListener('click',event=>{if(event.target===$('detail-dialog')&&backdropPress){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}backdropPress=false;});
$('analyze-button').onclick=analyze;
$('question').addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)&&!$('analyze-button').disabled){event.preventDefault();analyze();}});
$('copy-ocr').onclick=async()=>{try{await navigator.clipboard.writeText(selected?.ocr_text||'');feedback('OCR text copied.');}catch{feedback('Copy unavailable. Select the extracted text to copy it.');}};
$('similar-button').onclick=()=>{if(!selected)return;const id=selected.id;$('detail-dialog').close();$('query').value='';$('sort').value='relevance';setView('memory');search({similar_id:id}).then(()=>{if(!$('detail-dialog').open)$('results-title').focus();});};
document.addEventListener('keydown',event=>{const el=document.activeElement;if(event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!['INPUT','TEXTAREA','SELECT'].includes(el.tagName)&&!el.isContentEditable&&!$('detail-dialog').open){event.preventDefault();$('query').focus();}});
(async()=>{await refreshStatus();await search();})();
