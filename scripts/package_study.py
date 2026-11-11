"""Export a complete study as a portable offline listening page and named audio."""
from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import sys
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.audio import ffmpeg, inspect_audio, sha256, timecode

MOODS={'neutral':'自然','happy':'开心','sad':'难过','angry':'生气','calm':'平静','surprised':'惊讶','afraid':'害怕','melancholic':'忧郁','disgusted':'厌恶'}
ENGINES={'index2':'IndexTTS2','index25':'IndexTTS2.5','breeze2':'BreezeTTS2'}


def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))


def montage(rows,root,target):
    chunks,cues,cursor=[],[],0.
    for i,row in enumerate(rows,1):
        audio,sr=sf.read(root/row['matched'],dtype='float32')
        assert sr==24000 and audio.ndim==1
        label=row.get('label') or f"{row['reference_code']} · {row['emotion_name']} · {row['engine_name']}"
        cues.append({'start':round(cursor,3),'end':round(cursor+len(audio)/sr,3),'label':label})
        chunks.append(audio);cursor+=len(audio)/sr
        if i<len(rows):chunks.append(np.zeros(24000,dtype=np.float32));cursor+=1
    temporary=target.with_suffix('.wav')
    sf.write(temporary,np.concatenate(chunks),24000,subtype='PCM_16')
    ffmpeg('-i',str(temporary),'-codec:a','libmp3lame','-q:a','2',str(target))
    temporary.unlink()
    target.with_suffix('.srt').write_text('\n'.join(f"{i}\n{timecode(c['start'])} --> {timecode(c['end'])}\n{c['label']}\n" for i,c in enumerate(cues,1)))
    target.with_suffix('.txt').write_text('\n'.join(f"{timecode(c['start'])}  {c['label']}" for c in cues))
    return {'path':str(target.relative_to(root)),'duration':round(cursor,3),'segments':len(rows),'cues':cues}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args()
    folder=ROOT/'data/runs'/args.job;job=read(folder/'job.json')
    if job.get('metadata',{}).get('text_items'):
        raise RuntimeError('Multiple texts require scripts/package_text_study.py to preserve all outputs')
    expected=Counter((s['request']['reference_id'],s['engine'],s['request']['emotion'],s['request']['text']) for s in job['segments'])
    actual=Counter((r['reference_id'],r['engine'],r['emotion'],r['text']) for r in job['results'])
    if job['status']!='complete' or expected!=actual or job.get('failures'):
        raise RuntimeError('Study is not complete and balanced; export halted')
    root=args.destination.resolve();root.mkdir(parents=True,exist_ok=True)
    records=root/'04_制作记录';records.mkdir(exist_ok=True)
    previous=records/'manifest.json'
    if previous.exists() and read(previous)['job_id']!=args.job:raise RuntimeError('Destination belongs to another study')
    refs=[]
    for i,ref_id in enumerate(dict.fromkeys(s['request']['reference_id'] for s in job['segments']),1):
        ref=read(ROOT/'data/references'/ref_id/'reference.json');code=ref.get('reference_code',f'R{i:03}')
        target=root/'01_参考音色'/f'{code}.wav';target.parent.mkdir(exist_ok=True)
        shutil.copy2(ROOT/'data/references'/ref_id/'audio.wav',target)
        target.with_suffix('.txt').write_text(ref['transcript'])
        refs.append({'id':ref_id,'code':code,'name':ref['name'],'duration':ref['audio']['duration'],'transcript':ref['transcript'],
                     'audio':str(target.relative_to(root)),'sha256':sha256(target),'source_sha256':ref.get('source_sha256'),
                     'transcript_review':ref.get('transcript_review')})
    moods=list(dict.fromkeys(s['request']['emotion'] for s in job['segments']))
    ref_order={r['id']:i for i,r in enumerate(refs)};engine_order={e:i for i,e in enumerate(ENGINES)}
    results=sorted(job['results'],key=lambda r:(ref_order[r['reference_id']],moods.index(r['emotion']),engine_order[r['engine']]))
    exported=[];validation=[]
    for r in results:
        code=refs[ref_order[r['reference_id']]]['code'];mood=r['emotion'];source=folder/r['id']
        target=root/'02_克隆结果'/code/f'{moods.index(mood)+1:02}_{MOODS[mood]}'
        target.mkdir(parents=True,exist_ok=True);name=ENGINES[r['engine']]
        original=target/f'{name}_原始.wav';matched=target/f'{name}_统一响度.wav';mp3=target/f'{name}.mp3'
        shutil.copy2(source/'original.wav',original);shutil.copy2(source/'matched.wav',matched)
        assert sha256(original)==r['metrics']['sha256']
        ffmpeg('-i',str(matched),'-codec:a','libmp3lame','-q:a','2',str(mp3))
        checks={}
        for label,path in [('original',original),('matched',matched),('mp3',mp3)]:
            info=inspect_audio(path);checks[label]={k:info[k] for k in ['duration','sample_rate','channels','peak_dbfs','clipping_ratio','sha256']}
        asr_path=source/'asr-check.json';asr=read(asr_path) if asr_path.exists() else None
        item={**{k:r[k] for k in ['id','engine','engine_name','reference_id','emotion','seed','text','language','wall_seconds','rtf_warm','rating','parameters']},
              'reference_code':code,'emotion_name':MOODS[mood],'duration':r['metrics']['duration'],
              'original':str(original.relative_to(root)),'matched':str(matched.relative_to(root)),'mp3':str(mp3.relative_to(root)),
              'audio_sha256':r['metrics']['sha256'],'diagnostic_cer':asr['diagnostic_cer'] if asr else None,'asr':asr,
              'duplicate_moods':[MOODS[other['emotion']] for other in results if other['id']!=r['id'] and other['engine']==r['engine'] and other['reference_id']==r['reference_id'] and other['metrics']['sha256']==r['metrics']['sha256']]}
        exported.append(item);validation.append({'id':r['id'],'files':checks})
    live=f'http://127.0.0.1:8765/compare?job={args.job}&ref={refs[0]["id"]}&mood=neutral'
    bundle={'job_id':args.job,'title':job['title'],'text':job['segments'][0]['request']['text'],'references':refs,
            'moods':[{'id':m,'name':MOODS[m]} for m in moods],'results':exported,'live_url':live,
            'generation_count':len(exported),'subjective_ratings':'unrated; listener must assess similarity, naturalness and emotion',
            'timing_policy':'warm generation excludes initial model load and ffmpeg; not comparable with earlier cold-start results',
            'montages':[],'unique_audio_count':len({r['audio_sha256'] for r in exported}),
            'duplicate_note':'Exact SHA256 duplicates are preserved and marked. Local upstream emotion matrices have one identical happy/surprised prototype row; some references select that pair.'}
    montage_dir=root/'03_连续试听';montage_dir.mkdir(exist_ok=True)
    for ref in refs:
        rows=[r for r in exported if r['reference_id']==ref['id']]
        bundle['montages'].append(montage(rows,root,montage_dir/f'{ref["code"]}_27条_逐语气三模型.mp3'))
    bundle['montages'].append(montage([r for r in exported if r['reference_id']==refs[0]['id'] and r['emotion']=='neutral'],root,root/'00_先听_R001自然_三模型.mp3'))
    write(records/'manifest.json',bundle);write(records/'文件核验.json',validation)
    audit=ROOT/'data/study-emotion-matrix-audit.json'
    if audit.exists() and read(audit).get('job_id')==args.job:shutil.copy2(audit,records/'Index情绪向量核对.json')
    columns=['reference_code','engine_name','emotion_name','duration','wall_seconds','rtf_warm','seed','diagnostic_cer','duplicate_moods','naturalness','clarity','similarity','emotion','notes','original','matched','mp3','audio_sha256']
    with (records/'对照与评分表.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
        for r in exported:writer.writerow({k:(r.get('rating') or {}).get(k,'') if k in ['naturalness','clarity','similarity','emotion','notes'] else r.get(k,'') for k in columns})
    page_data={'references':refs,'moods':bundle['moods'],'results':[{k:r[k] for k in ['id','reference_id','emotion','emotion_name','engine_name','duration','original','matched','mp3','diagnostic_cer','duplicate_moods']} for r in exported],
               'text':bundle['text'],'live_url':live}
    template=(ROOT/'app/static/study_offline.html').read_text()
    (root/'打开试听.html').write_text(template.replace('__STUDY_DATA__',json.dumps(page_data,ensure_ascii=False).replace('</','<\\/')))
    (root/'使用说明.txt').write_text(f'''双击「打开试听.html」，选择参考片段和语气，三个模型并排试听。
点击「依次播放当前组」，可自动播放当前三条；选择「全部语气」可连续听同一参考的27条。
默认是统一响度MP3，原始WAV和统一响度WAV均已保留。原始参考在01_参考音色。
03_连续试听每个参考有一条27段合辑，顺序为每种语气依次IndexTTS2、IndexTTS2.5、BreezeTTS2；对应SRT/TXT标明时间和模型。
本次台词：{bundle['text']}
本次共{len(exported)}条，10段参考×3模型×9种语气。均为本机实际生成，无云GPU费用。
本次不同原始音频SHA256共{bundle['unique_audio_count']}个。重复输出已标注并保留；上游Index情绪矩阵存在开心/惊讶共用的向量，部分参考因此得到相同结果。
参考文字保留原逐字记录，自动ASR识别可能省略口头语或识别错英文，复核记录见manifest。
自动转写差异并非人工确认的错字率，也不是音质排名。评分表留空供真实试听。
完整参数、模型版本、音频校验与自动转写在04_制作记录。文件夹包含真人声音，请按原授权范围使用。
在线工作台（需本机服务启动）：{live}
''')
    archive=root.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for p in sorted(root.rglob('*')):
            if p.is_file():z.write(p,str(p.relative_to(root.parent)))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        count=len(z.infolist())
    print(json.dumps({'folder':str(root),'archive':str(archive),'clips':len(exported),'files':count,'zip_bytes':archive.stat().st_size},ensure_ascii=False))


if __name__=='__main__':main()
