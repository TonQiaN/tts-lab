"""Package fixed-reference text studies, with original/neutral/calm comparisons."""
from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
import sys
import zipfile
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.audio import ffmpeg, inspect_audio, normalize_audio, sha256
from package_study import montage, read, write

MOODS={'neutral':'自然','calm':'平静'}


def safe_name(value):
    return ''.join(c if c.isalnum() or c in '-_·' else '_' for c in value)[:60]


def export_take(source,folder,name):
    original=folder/f'{name}_原始.wav';matched=folder/f'{name}_统一响度.wav';mp3=folder/f'{name}.mp3'
    shutil.copy2(source,original)
    normalize_audio(original,matched)
    ffmpeg('-i',str(matched),'-codec:a','libmp3lame','-q:a','2',str(mp3))
    checks={}
    for label,path in [('original',original),('matched',matched),('mp3',mp3)]:
        info=inspect_audio(path)
        checks[label]={k:info[k] for k in ['duration','sample_rate','channels','peak_dbfs','clipping_ratio','sha256']}
    return original,matched,mp3,checks


def offline_page(bundle):
    esc=html.escape
    sections=[]
    toc=[]
    for item in bundle['items']:
        toc.append(f'<a href="#{item["code"]}">{esc(item["code"]+" · "+item["title"])}</a>')
        cards=[]
        for take in item['playlist']:
            cards.append(f'''<article><span class="tag">{esc(take['label'])}</span><h3>{esc(take['display_name'])}</h3>
<p class="muted">{take['duration']:.2f} 秒</p><audio controls preload="none" data-original="{esc(take['original'])}" data-matched="{esc(take['mp3'])}" src="{esc(take['mp3'])}"></audio>
<p class="links"><a href="{esc(take['original'])}" download>原始 WAV ↓</a><a href="{esc(take['matched'])}" download>统一响度 WAV ↓</a><a href="{esc(take['mp3'])}" download>MP3 ↓</a></p></article>''')
        source_note='<p class="muted">同文原声对照；原声文本保留已有逐字稿的重复和口头语，尚未人工逐句听校。</p>' if item.get('source') else ''
        sections.append(f'''<section id="{item['code']}" data-category="{'source' if item.get('source') else 'new'}"><div class="section-top"><div><span class="tag">{esc(item['code'])}</span><h2>{esc(item['title'])}</h2></div><button class="play-group">依次播放本组 →</button></div>
<p class="text">{esc(item['text'])}</p>{source_note}<div class="grid" style="--columns:{len(cards)}">{''.join(cards)}</div><p><a href="{esc(item['montage']['path'])}">下载本组连续对照 MP3 ↓</a> · <a href="#top">回到片段目录 ↑</a></p></section>''')
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>R001 · IndexTTS 2.0 · 原声与新文本对照</title>
<style>:root{{--ink:#25382d;--muted:#788477;--line:#dfe5da;--green:#536f59}}*{{box-sizing:border-box}}body{{margin:0;background:#f6f5f1;color:var(--ink);font:15px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;line-height:1.8}}main{{max-width:1300px;padding:35px 28px;margin:auto}}h1{{font-size:30px;line-height:1.4}}h2{{font-size:21px;margin:4px 0}}h3{{font-size:19px}}.muted,.tag{{color:var(--muted);font-size:12px}}.tag{{letter-spacing:1px}}a{{color:var(--green)}}button{{border:1px solid var(--line);border-radius:7px;padding:10px 15px;font:inherit;color:var(--ink);background:white;cursor:pointer}}button:hover{{background:#edf2e9}}.toolbar{{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:24px 0}}.toc{{display:flex;flex-wrap:wrap;gap:9px}}.toc a{{padding:5px 10px;border:1px solid var(--line);border-radius:6px;background:white;font-size:12px;text-decoration:none}}section{{padding:28px 0;border-top:1px solid var(--line);margin-top:30px;scroll-margin-top:20px}}.section-top{{display:flex;justify-content:space-between;gap:15px;align-items:center}}.text{{font-size:17px;line-height:1.9}}.grid{{display:grid;grid-template-columns:repeat(var(--columns),minmax(0,1fr));gap:18px;margin-top:20px}}article{{border:1px solid var(--line);background:white;border-radius:12px;padding:23px}}article.playing{{outline:2px solid var(--green)}}audio{{width:100%;height:38px;margin:12px 0}}.links{{display:flex;flex-wrap:wrap;gap:13px;font-size:12px}}#now{{min-height:24px;color:var(--green)}}footer{{margin-top:35px;border-top:1px solid var(--line);padding-top:20px}}@media(max-width:800px){{main{{padding:24px 18px}}h1{{font-size:25px}}.grid{{grid-template-columns:1fr}}.section-top{{align-items:flex-start}}.section-top button{{font-size:12px}}}}</style>
<main id="top"><span class="tag">FIXED VOICE / SAME TEXT COMPARISON</span><h1>R001，一个音色，两种读法。</h1><p>IndexTTS 2.0 · 自然 / 平静 · 20 段新文本 + 10 段原声逐字稿 · 60 条克隆</p>
<p class="muted">克隆参考始终固定为 R001。S001 使用参考自身的文本，S002–S010 使用同一说话人的其他录音文本。默认统一响度，原始录音与原始生成 WAV 均保留。</p>
<p><a href="{esc(bundle['live_url'])}">在工作台按片段筛选、盲听和评分 ↗</a></p><div class="toolbar"><button id="play-source">连续听 10 组原声对照</button><button id="play-new">连续听 20 组新文本</button><button id="stop">暂停</button><label><input id="original" type="checkbox"> 原始响度</label></div><p id="now" aria-live="polite">选择一组，依次听原声、自然克隆、平静克隆。</p><nav class="toc">{''.join(toc)}</nav>{''.join(sections)}
<footer class="muted">本机实际生成，无云 GPU 费用。相似度、自然度与语气差异需真实试听，未填写主观评分。自动转写检查可能有误，不能作为听校结论。<br>「03_连续试听」内含两类完整合辑及字幕时间索引；「04_制作记录」保留全部文字、参数、哈希及转写诊断。</footer></main>
<script>
'use strict';const players=[...document.querySelectorAll('audio')];function pauseAll(){{players.forEach(a=>a.pause());}}function playList(list){{pauseAll();list.forEach((a,i)=>a.onended=()=>{{if(list[i+1])list[i+1].play().catch(()=>document.getElementById('now').textContent='浏览器暂停了连续播放，请点击下一条。');else document.getElementById('now').textContent='本组已播放完毕。';}});if(list[0]){{list[0].currentTime=0;list[0].play().catch(()=>document.getElementById('now').textContent='请点击音频控件开始试听。');}}}}
players.forEach(a=>a.onplay=()=>{{players.forEach(b=>{{if(a!==b)b.pause();}});document.querySelectorAll('article').forEach(c=>c.classList.toggle('playing',c.contains(a)));document.getElementById('now').textContent='正在试听：'+a.closest('article').querySelector('.tag').textContent;}});
document.querySelectorAll('.play-group').forEach(b=>b.onclick=()=>playList([...b.closest('section').querySelectorAll('audio')]));document.getElementById('play-source').onclick=()=>playList([...document.querySelectorAll('section[data-category="source"] audio')]);document.getElementById('play-new').onclick=()=>playList([...document.querySelectorAll('section[data-category="new"] audio')]);document.getElementById('stop').onclick=pauseAll;document.getElementById('original').onchange=e=>{{pauseAll();players.forEach(a=>a.src=e.target.checked?a.dataset.original:a.dataset.matched);}};
</script></html>'''


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--job',required=True);parser.add_argument('--destination',required=True,type=Path);args=parser.parse_args()
    run=ROOT/'data/runs'/args.job;job=read(run/'job.json');items=job.get('metadata',{}).get('text_items',[])
    if not items:raise RuntimeError('No text study metadata')
    expected=Counter((r['engine'],r['request']['reference_id'],r['request']['text'],r['request']['emotion']) for r in job['segments'])
    actual=Counter((r['engine'],r['reference_id'],r['text'],r['emotion']) for r in job['results'])
    if job['status']!='complete' or expected!=actual or job.get('failures'):raise RuntimeError('Study is incomplete')
    if any(r['engine']!='index2' or r['emotion'] not in MOODS for r in job['results']):raise RuntimeError('This package requires IndexTTS 2.0 natural/calm results')
    root=args.destination.resolve();root.mkdir(parents=True,exist_ok=True);records=root/'04_制作记录';records.mkdir(exist_ok=True)
    if (records/'manifest.json').exists() and read(records/'manifest.json')['job_id']!=args.job:raise RuntimeError('Destination belongs to another study')
    fixed_id=job['metadata']['fixed_reference_id'];fixed=read(ROOT/'data/references'/fixed_id/'reference.json');reference_dir=root/'05_克隆参考';reference_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT/'data/references'/fixed_id/'audio.wav',reference_dir/'R001.wav');(reference_dir/'R001.txt').write_text(fixed['transcript'])
    bundle={'job_id':args.job,'title':job['title'],'fixed_reference_id':fixed_id,'fixed_reference':fixed,
            'generation_count':len(job['results']),'items':[],'montages':[],
            'live_url':f'http://127.0.0.1:8765/compare?job={args.job}&ref={fixed_id}&mood=all&text={items[0]["code"]}',
            'source_transcript_note':job['metadata'].get('source_transcript_note'),'subjective_ratings':'unrated'}
    checks=[];all_sources=[];all_new=[];clone_rows=[]
    for item in items:
        source_id=item.get('source_reference_id');category='01_同文原声对照' if source_id else '02_新文本'
        folder=root/category/f'{item["code"]}_{safe_name(item["title"])}';folder.mkdir(parents=True,exist_ok=True);(folder/'文本.txt').write_text(item['text'])
        entry={**item,'source':None,'results':[],'playlist':[]}
        if source_id:
            ref=read(ROOT/'data/references'/source_id/'reference.json');source=Path(ref.get('source_file',''))
            if not source.is_file():source=ROOT/'data/references'/source_id/'audio.wav'
            original,matched,mp3,check=export_take(source,folder,'原声')
            if ref.get('source_sha256') and str(source)==ref.get('source_file'):assert sha256(original)==ref['source_sha256']
            take={'id':source_id,'reference_code':ref.get('reference_code'),'display_name':'原声录音 · '+ref.get('reference_code',''),
                  'emotion_name':'原声','engine_name':'源录音','duration':check['original']['duration'],
                  'label':item['code']+' · 原声 '+ref.get('reference_code',''),
                  'original':str(original.relative_to(root)),'matched':str(matched.relative_to(root)),'mp3':str(mp3.relative_to(root)),
                  'source_metadata':ref}
            entry['source']=take;entry['playlist'].append(take);checks.append({'code':item['code'],'kind':'source','files':check})
        for mood in MOODS:
            matches=[r for r in job['results'] if r['text']==item['text'].strip() and r['emotion']==mood]
            if len(matches)!=1:raise RuntimeError('Expected one result for each exact text and delivery')
            r=matches[0];original,matched,mp3,check=export_take(run/r['id']/'original.wav',folder,MOODS[mood]+'_克隆');assert sha256(original)==r['metrics']['sha256']
            asr=run/r['id']/'asr-check.json'
            take={'id':r['id'],'reference_code':'R001','display_name':'IndexTTS 2.0 · '+MOODS[mood],
                  'emotion':mood,'emotion_name':MOODS[mood],'engine_name':'IndexTTS 2.0','duration':r['metrics']['duration'],
                  'label':item['code']+' · '+MOODS[mood]+'克隆 · 固定R001',
                  'original':str(original.relative_to(root)),'matched':str(matched.relative_to(root)),'mp3':str(mp3.relative_to(root)),
                  'text':r['text'],'parameters':r['parameters'],'seed':r['seed'],'reference_id':r['reference_id'],
                  'audio_sha256':r['metrics']['sha256'],'asr':read(asr) if asr.exists() else None,'rating':r.get('rating'),
                  'wall_seconds':r['wall_seconds'],'rtf_warm':r['rtf_warm']}
            entry['results'].append(take);entry['playlist'].append(take);checks.append({'code':item['code'],'kind':mood,'files':check});clone_rows.append({**take,'code':item['code'],'title':item['title']})
        entry['montage']=montage(entry['playlist'],root,folder/'连续对照.mp3');bundle['items'].append(entry)
        (all_sources if source_id else all_new).extend(entry['playlist'])
    montage_dir=root/'03_连续试听';montage_dir.mkdir(exist_ok=True)
    for rows,name in [(all_sources,'01_10组原声_自然_平静_完整对照'),(all_new,'02_20组新文本_自然_平静_完整对照')]:
        if rows:bundle['montages'].append(montage(rows,root,montage_dir/f'{name}.mp3'))
    for mood in MOODS:
        rows=[take for item in bundle['items'] if not item['source'] for take in item['results'] if take['emotion']==mood]
        if rows:bundle['montages'].append(montage(rows,root,montage_dir/f'{MOODS[mood]}_20段新文本.mp3'))
    bundle['montages'].append(montage(bundle['items'][0]['playlist'],root,root/'00_先听_原声_自然_平静.mp3'))
    write(records/'manifest.json',bundle);write(records/'文件核验.json',checks)
    with (records/'生成对照与评分表.csv').open('w',encoding='utf-8-sig',newline='') as f:
        columns=['code','title','emotion_name','text','duration','seed','reference_id','audio_sha256','wall_seconds','rtf_warm','asr_text','diagnostic_cer','naturalness','clarity','similarity','emotion','notes','mp3']
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
        for r in clone_rows:
            writer.writerow({k:(r.get('asr') or {}).get(k,'') if k in ['asr_text','diagnostic_cer'] else (r.get('rating') or {}).get(k,'') if k in ['naturalness','clarity','similarity','emotion','notes'] else r.get(k,'') for k in columns})
    (records/'全部输入文本.txt').write_text('\n\n'.join(f"{i['code']} · {i['title']}\n{i['text']}" for i in items))
    (root/'打开试听.html').write_text(offline_page(bundle))
    (root/'使用说明.txt').write_text('''双击「打开试听.html」，或打开本机工作台按片段筛选。
本次固定R001音色、IndexTTS 2.0、种子42、原生速度1.0；每段生成自然和平静两种语气。
01_同文原声对照：10段原声逐字稿，保留原始录音、自然克隆、平静克隆；每组有连续对照MP3及字幕。
02_新文本：20段新文本，每组都有自然和平静两版。
03_连续试听：两类完整对照合集，以及新文本的单语气合集。SRT按真实时长标明原声与克隆的边界。
04_制作记录：输入文字、模型参数、来源、音频哈希、自动转写、空白主观评分表。
05_克隆参考：实际用于全部60条生成的R001。S002等的其他原声只作对照，没有用于克隆条件。
默认统一响度−16LUFS，避免音量影响判断。原始WAV未覆盖；统一响度WAV和MP3另外保存。
原声输入使用已有逐字稿，保留口头语和重复，未经过人工逐句听校。自动转写诊断不是音质排名。
全部本机生成，不产生云GPU费用。\n'''+bundle['live_url'])
    archive=root.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for p in sorted(root.rglob('*')):
            if p.is_file():z.write(p,str(p.relative_to(root.parent)))
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    print(json.dumps({'job_id':args.job,'folder':str(root),'archive':str(archive),'clips':len(clone_rows),'original_clips':len([i for i in bundle['items'] if i['source']]),'zip_bytes':archive.stat().st_size},ensure_ascii=False))


if __name__=='__main__':main()
