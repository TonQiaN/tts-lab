"""One resident model, sequential independent requests, atomic per-output receipts."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.audio import ffmpeg, inspect_audio, normalize_audio


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', required=True)
    parser.add_argument('--tasks', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--vendor', type=Path, required=True)
    args = parser.parse_args()
    tasks = json.loads(args.tasks.read_text())
    sys.path.insert(0, str(args.vendor))
    load_started = time.monotonic()
    if args.engine == 'breeze2':
        import mlx.core as mx
        import torch
        import soundfile as sf
        from breeze_tts_mlx.runtime import BreezeMLXRuntime, MLXRuntimeConfig
        from breeze_tts_mlx.sampling import NumpySampler, SamplingConfig
        from breeze_tts_mlx.templates import get_template, prepare_inputs
        sampling = SamplingConfig(temperature=.9, top_k=50, top_p=1, do_sample=True)
        runtime = BreezeMLXRuntime(args.model, audio_device='auto', seed=42,
                                  config=MLXRuntimeConfig(max_new_tokens=1500, backbone_sampling=sampling, depth_sampling=sampling))
    else:
        from mlx_indextts.runtime import TTSRuntime, GenerateOptions
        runtime = TTSRuntime(memory_limit_gb=28, quantize='8')
        runtime.load(str(args.model))
    load_seconds = round(time.monotonic() - load_started, 4)
    print(json.dumps({'event':'loaded','engine':args.engine,'seconds':load_seconds}), flush=True)
    for task in tasks:
        folder = Path(task['output_dir'])
        output = folder / 'original.wav'
        request = task['request']
        params = task['parameters']
        try:
            with (folder / 'generation.log').open('w') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                started = time.monotonic()
                if args.engine == 'breeze2':
                    seed = request['seed']
                    random.seed(seed); torch.manual_seed(seed); mx.random.seed(seed)
                    runtime.sampler = NumpySampler(seed)
                    instruction = params['instruction']
                    item = {'id':task['id'], 'text':request['text'], 'speaker':'S0',
                            'ref_audio_path':task['reference_path'], 'ref_text':task['reference_transcript']}
                    if instruction: item['instruction'] = instruction
                    inputs = prepare_inputs(runtime.tokenizer, runtime.audio_tokenizer, runtime, [item],
                                            get_template('ref_edit_tata' if instruction else 'ref_clone_tata'),
                                            guidance_scale=params['cfg_scale'], guidance_scale_ref=None, guidance_scale_ins=None)
                    with sf.SoundFile(output, mode='w', samplerate=runtime.sample_rate, channels=1, subtype='PCM_16') as f:
                        for chunk in runtime.iter_audio_chunks(inputs, request_id=task['id']):
                            f.write(chunk.audio)
                else:
                    emotion = None if request['emotion']=='neutral' else {request['emotion']:request['emotion_strength']}
                    options = GenerateOptions(max_tokens=params['max_tokens'], max_text_tokens=params['max_text_tokens'],
                                              diffusion_steps=25, interval_silence=200, emotion=emotion,
                                              emo_alpha=request['emotion_strength'], seed=request['seed'], language=request['language'],
                                              denoise_ref_audio=False, denoise_emotion_ref_audio=False)
                    runtime.generate(request['text'], task['reference_path'], str(output), model=str(args.model), options=options)
                elapsed = round(time.monotonic() - started, 4)
            diagnostic = (folder / 'generation.log').read_text(errors='replace')
            if 'Failed to load semantic_codec weights' in diagnostic or 'Failed to load CAMPPlus' in diagnostic:
                raise RuntimeError('Reference encoder weights failed to load')
            metrics = inspect_audio(output)
            loudness = normalize_audio(output, folder / 'matched.wav')
            ffmpeg('-i', str(output), '-codec:a', 'libmp3lame', '-q:a', '2', str(folder / 'audio.mp3'))
            result = {'id':task['id'], 'job_id':task['job_id'], 'engine':args.engine, 'engine_name':task['engine_name'],
                      'text':request['text'], 'language':request['language'], 'emotion':request['emotion'], 'seed':request['seed'],
                      'reference_id':request['reference_id'], 'reference_name':task['reference_name'],
                      'title':request['title'], 'parameters':{**params,'runtime_policy':'resident model; per-output timing excludes initial model load and ffmpeg',
                                                           'model_load_seconds':load_seconds},
                      'metrics':metrics, 'loudness':loudness, 'wall_seconds':elapsed, 'rtf_cold':None,
                      'rtf_warm':round(elapsed/metrics['duration'],4), 'timing_kind':'warm',
                      'created_at':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
                      'rating':None, 'license':task['license']}
            atomic_json(folder / 'result.json', result)
            print(json.dumps({'event':'complete','id':task['id'],'seconds':elapsed}), flush=True)
        except Exception as exc:
            atomic_json(folder / 'failure.json', {'id':task['id'],'engine':args.engine,'reference_id':request['reference_id'],
                                                 'emotion':request['emotion'],'error':str(exc),'traceback':traceback.format_exc()})
            print(json.dumps({'event':'failed','id':task['id'],'error':str(exc)}), flush=True)


if __name__ == '__main__':
    main()
