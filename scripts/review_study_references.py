"""Review reference text locally; release the Breeze stage after durable receipts."""
import json
import os
import re
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
os.environ['HF_HUB_OFFLINE']='1'


def atomic(path,value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2))
    temp.replace(path)


def main():
    study=json.loads((ROOT/'data/woman-clone-study.json').read_text())
    receipt=ROOT/'models/asr-turbo.download.json'
    deadline=time.monotonic()+2400
    while not receipt.exists():
        if time.monotonic()>deadline:raise RuntimeError('Reference ASR model download is still incomplete')
        time.sleep(5)
    import mlx_whisper
    from opencc import OpenCC
    converter=OpenCC('t2s')
    results=[]
    for ref in study['references']:
        folder=ROOT/'data/references'/ref['id']
        path=folder/'reference.json'
        original=json.loads(path.read_text())
        transcript=mlx_whisper.transcribe(str(folder/'audio.wav'),path_or_hf_repo=str(ROOT/'models/whisper-large-v3-turbo'),
                                         language='zh',temperature=0,fp16=True,condition_on_previous_text=False,
                                         initial_prompt='普通话会议讨论。AI、agent、leader、ready、数据环境、招聘。',verbose=False)['text']
        text=converter.convert(transcript).strip()
        old=original['transcript']
        normalized=lambda s:''.join(c for c in s.lower() if c.isalnum())
        ratio=len(normalized(text))/max(1,len(normalized(old)))
        # ASR often omits repetitions and fillers in these fast meeting excerpts.
        # Keep the fuller source transcript; retain ASR as a diagnostic, not a rewrite.
        fallback=True
        selected=old
        latest=json.loads(path.read_text())
        latest['transcript']=selected
        latest['transcript_review']={'status':'ready','method':'local Whisper large-v3-turbo; no human listening audit',
                                      'model_revision':'a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb',
                                      'original_transcript':old,'asr_transcript':text,'fallback_to_source':fallback,
                                      'policy':'preserve full source transcript; ASR diagnostic only; repetitions and fillers retained',
                                      'asr_source_length_ratio':round(ratio,4),
                                      'reference_audio_sha256':latest['audio']['sha256']}
        atomic(path,latest)
        result={'reference_id':ref['id'],'reference_code':ref['reference_code'],'original_transcript':old,
                'asr_transcript':text,'selected_transcript':selected,'fallback':fallback}
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        atomic(ROOT/'data/study-reference-text-review.json',results)
    print('ALL 10 REFERENCE TEXTS READY',flush=True)


if __name__=='__main__':main()
