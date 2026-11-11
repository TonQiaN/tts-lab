'use strict';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = {engines:[],references:[],jobs:[],prompts:[],catalog:[]};
let selected = location.pathname.split('/')[2] || 'index25';
if (!['index2','index25','breeze2'].includes(selected)) selected = 'index25';
let page = location.pathname === '/compare' ? 'compare' : 'studio';
let currentJob = new URLSearchParams(location.search).get('job') || '', order = [], previousKey = '', timer;
let comparisonNeedsRender = false;
let compareReference=new URLSearchParams(location.search).get('ref')||'', compareMood=new URLSearchParams(location.search).get('mood')||'', filterJob='';
let compareText=new URLSearchParams(location.search).get('text')||'';
const ratingDrafts = new Map();
const labels = {studio:'配音工作室',compare:'对比试听',voices:'我的音色',script:'剧本与多角色',catalog:'模型与整合包'};

async function api(path, data, method='POST') {
  const options = {method};
  if (data instanceof FormData) options.body = data;
  else if (data !== undefined) {options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(data);}
  const res = await fetch(path, options);
  if (!res.ok) {const x=await res.json().catch(()=>({detail:res.statusText})); throw new Error(Array.isArray(x.detail)?x.detail.map(e=>e.msg).join('；'):x.detail||'操作失败');}
  return res.json();
}
function toast(msg) {clearTimeout(timer);$('toast').textContent=msg;$('toast').style.display='block';timer=setTimeout(()=>$('toast').style.display='none',6500);}
function showPage(name) {
  page=name; document.querySelectorAll('.page').forEach(el=>el.classList.toggle('active',el.id===name));
  document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.page===name));
  $('breadcrumb').textContent='工作空间 / '+labels[name];
  if(name==='compare') renderCompare(); if(name==='voices')renderVoices();
  window.scrollTo({top:0,behavior:'smooth'});
}
function engineOptions(value) {return state.engines.map(e=>`<option value="${e.id}" ${e.id===value?'selected':''}>${esc(e.name)}</option>`).join('');}
function referenceOptions(value,empty='不使用参考') {return `<option value="">${empty}</option>`+state.references.map(r=>`<option value="${r.id}" ${r.id===value?'selected':''}>${esc(r.name)}</option>`).join('');}
function renderEngines(){
  $('engines').innerHTML=state.engines.map(e=>`<button class="engine-card ${e.id===selected?'selected':''}" data-engine="${e.id}" aria-pressed="${e.id===selected}"><div class="engine-top"><span>${e.short}</span><span class="badge ${e.status==='ready'?'ready':''}">${e.status==='ready'?(e.verified?'已生成验证':'可开始生成'):'准备中'}</span><span class="engine-dot"></span></div><h3>${esc(e.name)}</h3><p>${e.capabilities.slice(0,3).map(esc).join(' · ')}</p></button>`).join('');
  document.querySelectorAll('[data-engine]').forEach(el=>el.onclick=()=>chooseEngine(el.dataset.engine));
}
function chooseEngine(engine) {selected=engine;$('all-engines').checked=false;history.replaceState(null,'','/studio/'+engine);renderEngines();updateControls();}
function updateControls(){
  const all=$('all-engines').checked;
  $('instruction-wrap').classList.toggle('hidden',selected!=='breeze2'||all);
  $('emotion-ref').disabled=selected==='breeze2'||all;
  $('strength').disabled=selected==='breeze2'&&!all;
  if(selected==='breeze2'||all)$('emotion-ref').value='';
  $('text-hint').textContent=selected==='breeze2'&&!all?'支持 [笑]、[叹气] 等行内声音事件':'需要参考音色；同一组实验使用相同文本';
  $('license-note').textContent=selected==='breeze2'||all?'BreezeTTS2 模型及自托管输出限研究、非商业使用。任务依次运行。':'任务依次运行；首次加载可能较慢。模型许可详见来源说明。';
}
function renderReferences(){
  for(const id of ['reference','emotion-ref']) {const value=$(id).value;$(id).innerHTML=referenceOptions(value,id==='reference'?'选择音色 / 不使用参考':'不使用');}
  referencePreview();
}
function referencePreview(){
  const r=state.references.find(x=>x.id===$('reference').value);
  $('reference-preview').innerHTML=r?`<audio controls preload="none" src="/api/reference-audio/${r.id}" aria-label="试听参考音色"></audio><p class="muted">${r.source_type==='synthetic'?'合成演示音色':'自定义参考录音'} · ${r.audio.duration.toFixed(1)} 秒<br>${esc(r.transcript.slice(0,62))}${r.transcript.length>62?'…':''}</p>`:'<p class="muted">上传 5–15 秒的清晰人声，或者先用 Breeze 设计一个演示音色。</p>';
}
function reqFromForm(){
  const all=$('all-engines').checked;
  return {engines:all?['index2','index25','breeze2']:[selected],text:$('text').value,reference_id:$('reference').value||null,
    emotion_reference_id:$('emotion-ref').disabled?null:$('emotion-ref').value||null,language:$('language').value,emotion:$('emotion').value,
    instruction:selected==='breeze2'&&!all?$('instruction').value:'',emotion_strength:Number($('strength').value),seed:Number($('seed').value),speed:Number($('speed').value),
    title:(all?'三模型对比 · ':state.engines.find(e=>e.id===selected).name+' · ')+$('text').value.slice(0,20)};
}
const statusText={queued:'排队中',running:'生成中',complete:'已完成',partial:'部分完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
const emotionText={neutral:'自然',happy:'开心',sad:'难过',angry:'生气',calm:'平静',surprised:'惊讶',afraid:'害怕',melancholic:'忧郁',disgusted:'厌恶',unknown:'未提供'};
function renderJobs(){
  $('jobs').innerHTML=state.jobs.length?state.jobs.slice(0,12).map(j=>`<div class="job"><div><div class="job-title">${esc(j.title)}</div><div class="job-meta">${esc(j.created_at.replace('T',' ').slice(0,19))} · ${esc(statusText[j.status]||j.status)} · ${esc(j.progress||'')} ${j.results.length?'· '+j.results.length+' 条音频':''}</div>${j.error?`<details class="job-error"><summary>查看失败原因</summary>${esc(j.error)}</details>`:''}</div><div class="job-actions">${['running','queued'].includes(j.status)?`<button class="text-button cancel-job" data-id="${j.id}">停止</button>`:''}${j.results.length?`<button class="secondary listen-job" data-id="${j.id}">试听 ↗</button>`:''}${j.timeline?`<a class="text-button" href="/api/jobs/${j.id}/timeline/wav">整段 WAV ↓</a><a class="text-button" href="/api/jobs/${j.id}/timeline/srt">SRT ↓</a>`:''}</div></div>`).join(''):'<div class="empty">这里会留下你的第一组声音实验。</div>';
  document.querySelectorAll('.listen-job').forEach(el=>el.onclick=()=>{currentJob=el.dataset.id;order=[];showPage('compare');});
  document.querySelectorAll('.cancel-job').forEach(el=>el.onclick=async()=>{try{await api(`/api/jobs/${el.dataset.id}/cancel`,{});await refresh();}catch(e){toast(e.message);}});
  $('run-count').textContent=state.jobs.reduce((n,j)=>n+j.results.length,0);
}
function ratingSelect(key,name,value,required=false){return `<label>${name}<select data-rating="${key}"><option value="">${required?'请选择':'不适用 / 未评'}</option>${[1,2,3,4,5].map(n=>`<option value="${n}" ${value===n?'selected':''}>${n} · ${['','很差','较差','一般','较好','很好'][n]}</option>`).join('')}</select></label>`;}
function renderComparisonList(){
  $('comparison-job').innerHTML=state.jobs.filter(j=>j.results.length).map(j=>`<option value="${j.id}" ${j.id===currentJob?'selected':''}>${esc(j.title)} · ${j.results.length} 条</option>`).join('');
}
function ratingFromCard(card){
  const data={blind:$('blind').checked,notes:card.querySelector('.rating-notes').value};
  card.querySelectorAll('[data-rating]').forEach(input=>data[input.dataset.rating]=input.value?Number(input.value):null);
  return data;
}
function renderCompare(){
  comparisonNeedsRender=false;
  const jobs=state.jobs.filter(j=>j.results.length);
  if(!jobs.some(j=>j.id===currentJob)){currentJob=jobs[0]?.id||'';order=[];}
  renderComparisonList();
  const job=jobs.find(j=>j.id===currentJob);
  if(!job){$('results').innerHTML='<div class="empty">还没有生成结果。完成一组配音后，就可以在这里并排试听。</div>';$('comparison-text').textContent='';return;}
  const refs=[...new Set(job.segments.map(s=>s.request.reference_id).filter(Boolean))];
  const moods=[...new Set(job.segments.map(s=>s.request.emotion))];
  const textItems=job.metadata?.text_items||[], textMode=textItems.length>0;
  const filtered=job.kind==='study';
  $('comparison-filters').classList.toggle('hidden',!filtered);
  if(filterJob!==job.id){
    if(filtered){if(!refs.includes(compareReference)&&compareReference!=='all')compareReference=refs[0]||'';if(!moods.includes(compareMood)&&compareMood!=='all')compareMood=textMode?'all':moods[0]||'';}
    else{compareReference='';compareMood='';}
    if(textMode&&!textItems.some(item=>item.code===compareText)&&compareText!=='all')compareText=textItems[0].code;
    if(!textMode)compareText='';
    filterJob=job.id;
  }
  $('comparison-text-wrap').classList.toggle('hidden',!textMode);
  $('next-text').classList.toggle('hidden',!textMode);
  $('comparison-text-filter').innerHTML='<option value="all" '+(compareText==='all'?'selected':'')+'>全部片段文本</option>'+textItems.map(item=>`<option value="${esc(item.code)}" ${compareText===item.code?'selected':''}>${esc(item.code+' · '+item.title)}</option>`).join('');
  const selectedText=textItems.find(item=>item.code===compareText);
  $('comparison-ref-filter').innerHTML='<option value="all" '+(compareReference==='all'?'selected':'')+'>全部参考音色</option>'+refs.map(id=>`<option value="${id}" ${compareReference===id?'selected':''}>${esc(state.references.find(r=>r.id===id)?.name||id)}</option>`).join('');
  $('comparison-mood-filter').innerHTML='<option value="all" '+(compareMood==='all'?'selected':'')+'>全部语气</option>'+moods.map(mood=>`<option value="${mood}" ${compareMood===mood?'selected':''}>${esc(emotionText[mood]||mood)}</option>`).join('');
  let samples=job.results.filter(r=>(!filtered||!compareReference||compareReference==='all'||r.reference_id===compareReference)&&(!filtered||!compareMood||compareMood==='all'||r.emotion===compareMood)&&(!selectedText||r.text===selectedText.text.trim()));
  if(filtered)samples.sort((a,b)=>refs.indexOf(a.reference_id)-refs.indexOf(b.reference_id)||moods.indexOf(a.emotion)-moods.indexOf(b.emotion)||['index2','index25','breeze2'].indexOf(a.engine)-['index2','index25','breeze2'].indexOf(b.engine));
  $('comparison-progress').textContent=`已生成 ${job.results.length} / ${job.segments.length} · 当前${textMode?'克隆 ':''} ${samples.length} 条${selectedText?.source_reference_id?' · 原声 1 条':''}${job.failures?.length?' · 失败 '+job.failures.length:''}`;
  history.replaceState(null,'','/compare?job='+encodeURIComponent(currentJob)+(compareReference?'&ref='+encodeURIComponent(compareReference):'')+(compareMood?'&mood='+encodeURIComponent(compareMood):'')+(compareText?'&text='+encodeURIComponent(compareText):''));
  if(order.length!==samples.length||order.some(id=>!samples.some(r=>r.id===id)))order=samples.map(r=>r.id);
  const blind=$('blind').checked,matched=$('matched').checked;
  const texts=[...new Set(samples.length?samples.map(r=>r.text):job.segments.map(s=>s.request.text))];
  $('comparison-text').textContent=(selectedText?selectedText.code+' · '+selectedText.title+'：':texts.length===1?'共同文本：':'剧本台词：')+(textMode&&!selectedText?`${textItems.length} 段文本；选择单段可与原声并排对照。`:selectedText?selectedText.text:texts.join(' / '))+(job.results.some(r=>r.engine==='external')?' · 外部音频的参考音色与生成参数未核实':'');
  const ref=state.references.find(r=>r.id===((compareReference&&compareReference!=='all')?compareReference:samples[0]?.reference_id));
  $('comparison-reference').innerHTML=ref?`<details class="muted"><summary>${textMode?'固定克隆参考':'参考音色'} · ${esc(ref.name)}${ref.source_type==='synthetic'?'（合成音色）':''}</summary><audio style="max-width:400px" controls preload="none" src="/api/reference-audio/${ref.id}"></audio><p>${esc(ref.transcript)}</p></details>`:'';
  const source=selectedText?.source_reference_id?state.references.find(r=>r.id===selectedText.source_reference_id):null;
  const sourceCard=source?`<article class="result source-result"><span class="tag">${esc(selectedText.code)} · 原录音对照</span><h3>原声录音 · ${esc(source.reference_code||'')}</h3><span class="muted">${esc(source.name)} · ${source.audio.duration.toFixed(2)} 秒</span><div class="waveform" aria-hidden="true">${source.audio.waveform.filter((_,n)=>n%2===0).map(v=>`<i style="height:${Math.max(3,v*100)}%"></i>`).join('')}</div><audio class="result-audio" controls preload="none" src="/api/reference-audio/${source.id}?variant=${matched?'matched':'original'}" aria-label="试听原声"></audio><p class="muted">${esc(job.metadata.source_transcript_note||'原声对应保存的逐字稿。')}</p><p class="muted">此卡为同文对照原声；右侧克隆始终使用固定参考音色。</p><div class="result-links"><a href="/api/reference-audio/${source.id}" download>原声 WAV ↓</a><a href="/api/reference-audio/${source.id}?variant=matched" download>统一响度 WAV ↓</a></div></article>`:'';
  $('results').innerHTML=sourceCard+(order.length?order.map((id,i)=>{
    const r=job.results.find(r=>r.id===id),rate=ratingDrafts.get(r.id)||r.rating||{},m=r.metrics;
    const refCode=state.references.find(ref=>ref.id===r.reference_id)?.reference_code||r.reference_name||'';
    const duplicates=filtered?job.results.filter(other=>other.id!==r.id&&other.engine===r.engine&&other.reference_id===r.reference_id&&other.metrics.sha256===r.metrics.sha256):[];
    return `<article class="result" data-run="${r.id}"><span class="tag">TAKE ${String(i+1).padStart(2,'0')}${filtered?' · '+esc(refCode)+' · '+esc(emotionText[r.emotion]||r.emotion):''}${textMode?' · '+esc(r.title):''}</span><h3>${blind?'声音 '+String(i+1):esc(r.engine_name)+' · '+esc(emotionText[r.emotion]||r.emotion)}</h3><span class="muted">${blind?'先完成评分，再揭晓模型':esc(r.parameters.precision)}</span><div class="waveform" aria-hidden="true">${m.waveform.filter((_,n)=>n%2===0).map(v=>`<i style="height:${Math.max(3,v*100)}%"></i>`).join('')}</div><audio class="result-audio" controls preload="none" src="/api/audio/${job.id}/${id}/${matched?'matched':'original'}" aria-label="试听声音 ${i+1}"></audio>
      ${blind?'':`<div class="metrics"><div class="metric">音频时长<strong>${m.duration.toFixed(1)} s</strong></div><div class="metric">${r.timing_kind==='warm'?'生成耗时 · 常驻':'总耗时 · 含加载'}<strong>${r.wall_seconds==null?'未提供':r.wall_seconds.toFixed(1)+' s'}</strong></div><div class="metric">${r.timing_kind==='warm'?'RTF · 常驻':'RTF · 含加载'}<strong>${(r.rtf_warm??r.rtf_cold)==null?'未提供':(r.rtf_warm??r.rtf_cold).toFixed(2)}</strong></div><div class="metric">原始响度<strong>${r.loudness.input_lufs.toFixed(1)} LUFS</strong></div></div>`}
      ${duplicates.length?`<p class="muted">与同模型「${duplicates.map(other=>esc(emotionText[other.emotion]||other.emotion)).join('、')}」结果的音频完全相同。</p>`:''}
      ${r.asr?`<details><summary>辅助转写 · 请结合原文试听</summary><p>${esc(r.asr.asr_text)}</p><small>${esc(r.asr.asr_model?.includes('large-v3-turbo')?'本机 Whisper large-v3-turbo':'本机 Whisper base')}，识别可能有误。</small></details>`:''}
      <div class="rating-grid">${ratingSelect('naturalness','自然度',rate.naturalness,true)}${ratingSelect('clarity','清晰度',rate.clarity,true)}${ratingSelect('similarity','音色相似度',rate.similarity)}${ratingSelect('emotion','情绪符合度',rate.emotion)}</div><textarea class="rating-notes" rows="2" placeholder="漏字、停顿、气息感……">${esc(rate.notes||'')}</textarea><button class="secondary save-rating" data-id="${id}">${r.rating?'更新评分':'保存评分'}</button><div class="result-links"><a href="/api/audio/${job.id}/${id}/original" download>WAV ↓</a><a href="/api/audio/${job.id}/${id}/mp3" download>MP3 ↓</a><button class="text-button use-reference" data-id="${id}">存为音色</button></div>${blind?'':`<details><summary>本次参数与来源</summary><pre>${esc(JSON.stringify(r.parameters,null,2))}</pre><p>${esc(r.license)}</p></details>`}</article>`;
  }).join(''):'<div class="empty">当前音色与语气还在生成，结果会自动出现在这里。</div>');
  document.querySelectorAll('.result[data-run]').forEach(card=>{
    const remember=()=>ratingDrafts.set(card.dataset.run,ratingFromCard(card));
    card.querySelectorAll('[data-rating]').forEach(input=>input.onchange=remember);
    card.querySelector('.rating-notes').oninput=remember;
  });
  document.querySelectorAll('.save-rating').forEach(el=>el.onclick=async()=>{
    const card=el.closest('.result'),data=ratingFromCard(card);
    if(!data.naturalness||!data.clarity)return toast('请至少给自然度与清晰度评分。');
    try{await api(`/api/results/${currentJob}/${el.dataset.id}/rating`,data);ratingDrafts.delete(el.dataset.id);el.textContent='已保存 ✓';toast('评分已保存到本机。');}catch(e){toast(e.message);}
  });
  document.querySelectorAll('.use-reference').forEach(el=>el.onclick=async()=>{
    try{const r=await api(`/api/results/${currentJob}/${el.dataset.id}/reference`,{});await refresh();$('reference').value=r.id;referencePreview();toast('已保存为合成音色。请核对逐字稿后用于克隆。');}catch(e){toast(e.message);}
  });
  document.querySelectorAll('audio').forEach(audio=>audio.addEventListener('play',()=>document.querySelectorAll('audio').forEach(other=>{if(other!==audio)other.pause();})));
}
function renderVoices(){
  $('voice-list').innerHTML=state.references.length?state.references.map(r=>`<div class="voice" data-ref="${r.id}"><span class="badge">${r.source_type==='synthetic'?'合成音色':'自定义录音'}</span><h3>${esc(r.name)}</h3><p>${r.audio.duration.toFixed(1)} 秒 · ${r.audio.sample_rate} Hz · 本机保存</p><audio controls preload="none" src="/api/reference-audio/${r.id}"></audio><label>音色名称<input class="ref-name" value="${esc(r.name)}" maxlength="100"></label><label>准确逐字稿<textarea class="ref-transcript" rows="4">${esc(r.transcript)}</textarea></label><div class="generate-row"><button class="secondary save-ref" data-id="${r.id}">保存修改</button><button class="text-button choose-ref" data-id="${r.id}">用于配音 ↗</button></div></div>`).join(''):'<div class="empty">添加你自己的录音，或在 Breeze 生成后点击「存为音色」。</div>';
  document.querySelectorAll('.save-ref').forEach(el=>el.onclick=async()=>{const card=el.closest('.voice');try{await api(`/api/references/${el.dataset.id}`,{name:card.querySelector('.ref-name').value,transcript:card.querySelector('.ref-transcript').value},'PUT');toast('音色信息已更新。');await refresh();}catch(e){toast(e.message);}});
  document.querySelectorAll('.choose-ref').forEach(el=>el.onclick=()=>{$('reference').value=el.dataset.id;referencePreview();showPage('studio');});
}
function renderCatalog(){
  $('catalog-list').innerHTML=state.catalog.map(c=>`<article class="catalog-card"><span class="tag">${esc(c.type)}</span><h3>${esc(c.name)}</h3><p>${esc(c.platform)}</p><span class="badge">${esc(c.status)}</span><p>${esc(c.note)}</p><a href="${esc(c.url)}" target="_blank" rel="noreferrer">项目与说明 ↗</a></article>`).join('');
}
function addSegment(){
  const el=document.createElement('div');el.className='segment';
  el.innerHTML=`<div class="segment-controls"><select class="segment-engine" aria-label="台词模型">${engineOptions(selected)}</select><select class="segment-ref" aria-label="台词音色">${referenceOptions($('reference').value,'音色 / Breeze 设计')}</select><select class="segment-emotion" aria-label="台词情绪">${$('emotion').innerHTML}</select><button class="text-button remove-segment">删除</button></div><textarea class="segment-text" rows="3" placeholder="输入这一段台词" aria-label="角色台词"></textarea>`;
  el.querySelector('.remove-segment').onclick=()=>el.remove();$('segments').append(el);
}
async function refresh(){
  try{
    const next=await api('/api/state',undefined,'GET');
    const key=JSON.stringify([next.engines.map(e=>[e.status,e.verified]),next.references.map(r=>[r.id,r.name,r.transcript]),next.jobs.map(j=>[j.id,j.status,j.progress,j.results.length])]);
    const refsChanged=JSON.stringify(next.references)!==JSON.stringify(state.references);
    const jobsChanged=JSON.stringify(next.jobs.map(j=>[j.id,j.status,j.results.length]))!==JSON.stringify(state.jobs.map(j=>[j.id,j.status,j.results.length]));
    const comparisonChanged=JSON.stringify(next.jobs.find(j=>j.id===currentJob)?.results)!==JSON.stringify(state.jobs.find(j=>j.id===currentJob)?.results);
    const first=!state.engines.length;state=next;
    $('connection').innerHTML='<i class="online-dot"></i> 本机连接正常';
    if(key!==previousKey){renderEngines();renderJobs();if(refsChanged||first)renderReferences();if(page==='voices'&&refsChanged)renderVoices();previousKey=key;}
    if(first){$('prompt').innerHTML='<option value="">选择测试文本</option>'+state.prompts.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('');renderCatalog();updateControls();showPage(page);addSegment();}
    if(page==='compare'&&jobsChanged)renderComparisonList();
    if(comparisonChanged)comparisonNeedsRender=true;
    if(page==='compare'&&comparisonNeedsRender&&![...document.querySelectorAll('#compare audio')].some(a=>!a.paused&&!a.ended))renderCompare();
  }catch(e){$('connection').textContent='连接中断 · 请打开启动器';}
}
document.querySelectorAll('.nav').forEach(el=>el.onclick=()=>showPage(el.dataset.page));
$('go-compare').onclick=()=>showPage('compare');$('manage-voices').onclick=()=>showPage('voices');
for(const id of ['upload-voice','new-voice'])$(id).onclick=()=>$('voice-dialog').showModal();
$('close-dialog').onclick=()=>$('voice-dialog').close();
$('reference').onchange=referencePreview;$('all-engines').onchange=updateControls;
$('text').setAttribute('aria-label','配音文本');
$('text').oninput=()=>$('char-count').textContent=$('text').value.length+' / 2000';$('text').oninput();
$('prompt').onchange=()=>{const p=state.prompts.find(p=>p.id===$('prompt').value);if(!p)return;$('text').value=p.text;$('language').value=p.language;$('emotion').value=p.emotion;$('text').oninput();if(p.id==='vocal')chooseEngine('breeze2');if(p.language==='ja')chooseEngine('index25');};
$('generate').onclick=async()=>{const b=$('generate');b.disabled=true;try{const j=await api('/api/generate',reqFromForm());currentJob=j.id;order=[];toast('已加入本机队列。可以在最近任务查看进度。');await refresh();}catch(e){toast(e.message);}finally{b.disabled=false;}};
$('generate-emotions').onclick=async()=>{const b=$('generate-emotions');b.disabled=true;try{const j=await api('/api/emotions',{request:reqFromForm()});currentJob=j.id;order=[];toast('多语气对照已加入队列。每条使用相同文本、音色和种子。');await refresh();}catch(e){toast(e.message);}finally{b.disabled=false;}};
$('voice-form').onsubmit=async(e)=>{e.preventDefault();const b=e.target.querySelector('[type=submit]');b.disabled=true;try{const r=await api('/api/references',new FormData(e.target));$('voice-dialog').close();e.target.reset();await refresh();$('reference').value=r.id;referencePreview();toast('音色已保存。');}catch(e){toast(e.message);}finally{b.disabled=false;}};
$('comparison-job').onchange=()=>{currentJob=$('comparison-job').value;compareReference='';compareMood='';compareText='';order=[];renderCompare();};
$('comparison-text-filter').onchange=()=>{compareText=$('comparison-text-filter').value;order=[];renderCompare();};
$('next-text').onclick=()=>{const values=[...$('comparison-text-filter').options].map(o=>o.value).filter(value=>value&&value!=='all');compareText=values[(values.indexOf(compareText)+1)%values.length]||'';order=[];renderCompare();};
$('comparison-ref-filter').onchange=()=>{compareReference=$('comparison-ref-filter').value;order=[];renderCompare();};
$('comparison-mood-filter').onchange=()=>{compareMood=$('comparison-mood-filter').value;order=[];renderCompare();};
$('next-mood').onclick=()=>{const values=[...$('comparison-mood-filter').options].map(o=>o.value).filter(value=>value&&value!=='all');compareMood=values[(values.indexOf(compareMood)+1)%values.length]||'';order=[];renderCompare();};
for(const id of ['matched','blind'])$(id).onchange=()=>renderCompare();
$('shuffle').onclick=()=>{for(let i=order.length-1;i>0;i--){const a=new Uint32Array(1);crypto.getRandomValues(a);const j=a[0]%(i+1);[order[i],order[j]]=[order[j],order[i]];}renderCompare();};
$('play-all').onclick=()=>{const players=[...document.querySelectorAll('.result-audio')];if(!players.length)return;players.forEach((p,i)=>p.onended=()=>{if(players[i+1])players[i+1].play().catch(()=>toast('浏览器已暂停连续播放，请手动播放下一条。'));});players[0].currentTime=0;players[0].play().catch(()=>toast('请点击音频播放器开始试听。'));};
$('export').onclick=()=>{if(currentJob)location.href=`/api/jobs/${currentJob}/export`;};
$('import-open').onclick=()=>{const job=state.jobs.find(j=>j.id===currentJob);$('import-form').elements.text.value=job?.results[0]?.text||'';$('import-dialog').showModal();};
$('import-close').onclick=()=>$('import-dialog').close();
$('import-form').onsubmit=async(e)=>{e.preventDefault();const b=e.target.querySelector('[type=submit]');b.disabled=true;try{const data=new FormData(e.target);if(data.get('join')==='yes'){if(!currentJob)throw new Error('请先选择一个实验组。');data.set('job_id',currentJob);}const job=await api('/api/import',data);currentJob=job.id;order=[];$('import-dialog').close();e.target.reset();await refresh();renderCompare();toast('已导入。外部结果的速度数据标记为未提供。');}catch(err){toast(err.message);}finally{b.disabled=false;}};
$('add-segment').onclick=addSegment;
$('generate-script').onclick=async()=>{const segments=[...document.querySelectorAll('.segment')].map(el=>({engines:[el.querySelector('.segment-engine').value],reference_id:el.querySelector('.segment-ref').value||null,text:el.querySelector('.segment-text').value,emotion:el.querySelector('.segment-emotion').value,language:'zh',seed:42}));try{const j=await api('/api/script',{title:$('script-title').value,segments,gap_ms:Number($('gap').value)});currentJob=j.id;toast('剧本已加入队列，完成后可下载整段音频和字幕。');await refresh();showPage('studio');}catch(e){toast(e.message);}};
refresh();setInterval(refresh,4000);
