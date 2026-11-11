from __future__ import annotations

import csv
import io
import json
import os
import signal
import subprocess
import threading
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app.audio import assemble, ffmpeg, inspect_audio, normalize_audio, sha256

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("TTS_LAB_DATA", ROOT / "data"))
for folder in [DATA / "runs", DATA / "references", DATA / "exports"]:
    folder.mkdir(parents=True, exist_ok=True)
ENGINES = {x["id"]: x for x in json.loads((ROOT / "config/engines.json").read_text())}
EMOTIONS = {"neutral":"", "happy":"开心、轻快而真诚", "sad":"难过、克制而低沉", "angry":"生气、坚定但吐字清楚", "afraid":"紧张、害怕", "disgusted":"厌恶", "melancholic":"忧郁、若有所思", "surprised":"惊讶", "calm":"平静、温柔而清晰"}
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-queue")
LOCK = threading.RLock()
ACTIVE: dict[str, subprocess.Popen] = {}


def now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def uid() -> str:
    return uuid.uuid4().hex[:16]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    with LOCK:
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temp.replace(path)


def item_path(kind: str, item: str) -> Path:
    if not item.isalnum() or len(item) > 40:
        raise HTTPException(400, "无效记录编号")
    result = DATA / kind / item
    if not result.is_dir():
        raise HTTPException(404, "找不到这条记录")
    return result


def records(kind: str, filename: str) -> list[dict]:
    result = []
    for p in (DATA / kind).glob(f"*/{filename}"):
        try:
            item = read_json(p)
            if kind == "runs":
                for sample in item.get("results", []):
                    check = p.parent / sample["id"] / "asr-check.json"
                    if check.is_file():
                        diagnostic = read_json(check)
                        if diagnostic.get("audio_sha256") == sample["metrics"]["sha256"]:
                            sample["asr"] = diagnostic
            result.append(item)
        except (OSError, json.JSONDecodeError): pass
    return sorted(result, key=lambda x: x.get("created_at", ""), reverse=True)


def engine_state(engine: dict) -> dict:
    folder = ROOT / engine["model_dir"]
    ready = all((folder / name).is_file() for name in engine["required_files"])
    ready = ready and (ROOT / "models" / (engine["id"] + ".download.json")).is_file()
    if engine["id"] != "breeze2":
        ready = ready and (ROOT / "models/auxiliary.download.json").is_file()
    env = ROOT / engine["environment"] / ".venv"
    package = "mlx" if engine["id"] != "breeze2" else "qwen_tts"
    installed = any((env / "lib").glob(f"python*/site-packages/{package}"))
    evidence = DATA / "verification.json"
    verified = read_json(evidence).get(engine["id"]) if evidence.exists() else None
    if verified and verified.get("artifact_revision") != engine.get("artifact_revision"):
        verified = None
    return {**engine, "installed": installed, "model_ready": ready,
            "status": "ready" if ready and installed else "installing",
            "verified": verified}


class GenerateRequest(BaseModel):
    engines: list[Literal["index2", "index25", "breeze2"]] = Field(min_length=1, max_length=3)
    text: str = Field(min_length=1, max_length=2000)
    reference_id: str | None = None
    emotion_reference_id: str | None = None
    language: Literal["zh", "en", "ja", "es", "ar"] = "zh"
    emotion: Literal["neutral", "happy", "sad", "angry", "afraid", "disgusted", "melancholic", "surprised", "calm"] = "neutral"
    emotion_strength: float = Field(default=0.65, ge=0, le=1)
    instruction: str = Field(default="", max_length=800)
    seed: int = Field(default=42, ge=0, le=2147483647)
    speed: float = Field(default=1, ge=0.5, le=2)
    title: str = Field(default="配音实验", max_length=120)

    @model_validator(mode="after")
    def check(self):
        self.text = self.text.strip()
        self.engines = list(dict.fromkeys(self.engines))
        if not self.text:
            raise ValueError("请输入配音文本")
        if any(e != "breeze2" for e in self.engines) and not self.reference_id:
            raise ValueError("IndexTTS 需要参考音色；可先使用 Breeze 声音设计建立音色")
        for engine in self.engines:
            if self.language not in ENGINES[engine]["languages"]:
                raise ValueError(f"{ENGINES[engine]['name']} 不支持当前所选语言")
        if self.emotion_reference_id and self.emotion != "neutral":
            raise ValueError("情绪参考和预设情绪请选择其中一种")
        if self.emotion_reference_id and "breeze2" in self.engines:
            raise ValueError("独立情绪参考只支持 IndexTTS；Breeze 请使用导演指令")
        if self.instruction and any(e != "breeze2" for e in self.engines):
            raise ValueError("自由导演指令仅适用于 Breeze；跨模型比较请使用共同情绪预设")
        if "breeze2" in self.engines and len(self.text) > 350:
            raise ValueError("Breeze 单段请控制在 350 字内，长文请在剧本页面分成多段")
        return self


class ScriptRequest(BaseModel):
    title: str = Field(default="多角色配音", max_length=120)
    segments: list[GenerateRequest] = Field(min_length=1, max_length=30)
    gap_ms: int = Field(default=200, ge=0, le=3000)

    @model_validator(mode="after")
    def check(self):
        if any(len(x.engines) != 1 for x in self.segments):
            raise ValueError("剧本每行请选择一个模型")
        return self


class Rating(BaseModel):
    naturalness: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    similarity: int | None = Field(default=None, ge=1, le=5)
    emotion: int | None = Field(default=None, ge=1, le=5)
    notes: str = Field(default="", max_length=2000)
    blind: bool = False


def get_ref(ref_id: str | None) -> tuple[Path | None, dict | None]:
    if not ref_id: return None, None
    folder = item_path("references", ref_id)
    return folder / "audio.wav", read_json(folder / "reference.json")


def build_command(engine_id: str, req: GenerateRequest, output: Path) -> tuple[list[str], Path, dict]:
    engine = ENGINES[engine_id]
    env = ROOT / engine["environment"]
    model = ROOT / engine["model_dir"]
    ref, meta = get_ref(req.reference_id)
    params = {"seed": req.seed, "precision": engine["backend"], "model_revision": engine["revision"],
              "artifact_repo": engine["artifact_repo"], "artifact_revision": engine["artifact_revision"],
              "reference_sha256": meta["audio"]["sha256"] if meta else None,
              "reference_type": meta.get("source_type") if meta else None,
              "speed_postprocess": req.speed, "runtime_policy": "fresh-process; wall time includes loading"}
    if engine_id == "breeze2":
        instruction = req.instruction.strip()
        if req.emotion != "neutral":
            instruction = (instruction + " " if instruction else "保持音色一致，") + "用" + EMOTIONS[req.emotion] + "的语气朗读。"
        if not ref and not instruction:
            instruction = "一位自然、清晰、温暖的中文旁白。" if req.language == "zh" else "A natural, clear, warm English narrator."
        if req.language == "en" and not req.instruction and req.emotion != "neutral":
            instruction = f"Keep the speaker identity. Speak with a {req.emotion} delivery."
        cmd = [str(env / ".venv/bin/python"), "infer.py", str(model), "--text", req.text,
               "--output", str(output), "--seed", str(req.seed), "--cfg-scale", "4" if instruction else "1",
               "--max-new-tokens", "1500", "--audio-device", "auto"]
        if instruction: cmd += ["--instruction", instruction]
        if ref:
            if not meta.get("transcript", "").strip():
                raise ValueError("Breeze 克隆需要参考录音的准确文字稿，请先编辑音色信息")
            cmd += ["--ref-audio", str(ref), "--ref-text", meta["transcript"]]
        params.update(instruction=instruction, cfg_scale=4 if instruction else 1,
                      mode="direction" if ref and instruction else "clone" if ref else "design")
    else:
        cmd = [str(env / ".venv/bin/mlx-indextts"), "generate", "--model", str(model),
               "--ref-audio", str(ref), "--text", req.text, "--output", str(output),
               "--language", req.language, "--seed", str(req.seed), "--no-denoise-ref",
               "--diffusion-steps", "25", "--max-text-tokens", "40" if engine_id == "index25" else "100", "--max-tokens",
               "1000" if engine_id == "index25" else "1500", "--memory-limit", "28"]
        if req.emotion != "neutral":
            cmd += ["--emotion", f"{req.emotion}:{req.emotion_strength}", "--emo-alpha", str(req.emotion_strength)]
        if req.emotion_reference_id:
            emotion_ref, emotion_meta = get_ref(req.emotion_reference_id)
            cmd += ["--emotion-ref-audio", str(emotion_ref), "--emo-alpha", str(req.emotion_strength)]
            params["emotion_reference_sha256"] = emotion_meta["audio"]["sha256"]
        params.update(emotion=req.emotion, emotion_strength=req.emotion_strength, diffusion_steps=25,
                      max_tokens=1000 if engine_id == "index25" else 1500,
                      max_text_tokens=40 if engine_id == "index25" else 100, mode="clone")
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=env, capture_output=True, text=True)
    params["adapter_commit"] = rev.stdout.strip()
    params["dependency_lock_sha256"] = sha256(env / "uv.lock") if (env / "uv.lock").exists() else None
    probe = "import importlib.metadata as m,json;print(json.dumps({p:m.version(p) for p in ('mlx','mlx-metal','torch','transformers','numpy')}))"
    versions = subprocess.run([str(env / ".venv/bin/python"), "-c", probe], capture_output=True, text=True)
    params["environment_packages"] = json.loads(versions.stdout) if versions.returncode == 0 else {"status":"未读取"}
    return cmd, env, params


def update_job(job_id: str, **fields) -> dict:
    with LOCK:
        path = item_path("runs", job_id) / "job.json"
        j = read_json(path)
        j.update(fields)
        write_json(path, j)
        return j


def execute_job(job_id: str):
    folder = item_path("runs", job_id)
    job = read_json(folder / "job.json")
    if job["status"] == "cancelled": return
    if job["kind"] == "study":
        execute_study(job_id)
        return
    update_job(job_id, status="running", started_at=now())
    outputs = []
    try:
        rows = job["segments"]
        for i, row in enumerate(rows):
            if read_json(folder / "job.json")["status"] == "cancelled": return
            req = GenerateRequest(**row["request"])
            engine_id = row["engine"]
            run_id = uid()
            output_dir = folder / run_id
            output_dir.mkdir()
            output = output_dir / "original.wav"
            cmd, cwd, params = build_command(engine_id, req, output)
            update_job(job_id, progress=f"{i+1} / {len(rows)} · {ENGINES[engine_id]['name']}")
            started = time.monotonic()
            env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONFAULTHANDLER="1", HF_HUB_DISABLE_XET="1", HF_HUB_DOWNLOAD_TIMEOUT="180", TOKENIZERS_PARALLELISM="false")
            env.update(HF_HOME=str(ROOT / "models/hf"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", INDEXTTS_W2V_BERT_DIR=str(ROOT / "models/facebook-w2v-bert-2.0"))
            with (output_dir / "generation.log").open("w") as log:
                with LOCK:
                    if read_json(folder / "job.json")["status"] == "cancelled": return
                    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    ACTIVE[job_id] = p
                try: returncode = p.wait(timeout=1800)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGTERM)
                    p.wait(timeout=15)
                    raise RuntimeError("单次生成超过 30 分钟，已停止；请缩短文本或检查生成日志")
                finally: ACTIVE.pop(job_id, None)
            if read_json(folder / "job.json")["status"] == "cancelled": return
            if returncode:
                tail = (output_dir / "generation.log").read_text(errors="replace")[-1800:]
                raise RuntimeError(f"{ENGINES[engine_id]['name']} 生成失败（退出码 {returncode}），日志：\n{tail}")
            diagnostic = (output_dir / "generation.log").read_text(errors="replace")
            if "Failed to load semantic_codec weights" in diagnostic or "Failed to load CAMPPlus" in diagnostic:
                raise RuntimeError("参考编码器权重加载失败，未接受可能含随机权重的输出；请检查日志")
            elapsed = round(time.monotonic() - started, 3)
            metrics = inspect_audio(output)
            if req.speed != 1:
                original_model = output_dir / "model-output.wav"
                output.rename(original_model)
                ffmpeg("-i", str(original_model), "-af", f"atempo={req.speed}", str(output))
                metrics = inspect_audio(output)
            loudness = normalize_audio(output, output_dir / "matched.wav")
            ffmpeg("-i", str(output), "-codec:a", "libmp3lame", "-q:a", "2", str(output_dir / "audio.mp3"))
            result = {"id":run_id, "job_id":job_id, "engine":engine_id, "engine_name":ENGINES[engine_id]["name"],
                      "text":req.text, "language":req.language, "emotion":req.emotion, "seed":req.seed,
                      "reference_id":req.reference_id, "title":req.title, "parameters":params, "metrics":metrics,
                      "loudness":loudness, "wall_seconds":elapsed, "rtf_cold":round(elapsed / metrics["duration"], 3),
                      "created_at":now(), "rating":None, "license":ENGINES[engine_id]["license"]}
            write_json(output_dir / "result.json", result)
            evidence_path = DATA / "verification.json"
            with LOCK:
                evidence = read_json(evidence_path) if evidence_path.exists() else {}
                evidence[engine_id] = {"job_id":job_id, "run_id":run_id, "created_at":now(),
                                       "artifact_revision":ENGINES[engine_id]["artifact_revision"],
                                       "audio_sha256":metrics["sha256"], "duration":metrics["duration"]}
                write_json(evidence_path, evidence)
            outputs.append(result)
            update_job(job_id, results=outputs)
        if job["kind"] == "script":
            timeline = assemble([folder / x["id"] / "original.wav" for x in outputs], [x["text"] for x in outputs], folder / "script.wav", job["gap_ms"])
            update_job(job_id, timeline=timeline)
        update_job(job_id, status="complete", completed_at=now(), progress="生成完成", results=outputs)
    except Exception as exc:
        if read_json(folder / "job.json")["status"] != "cancelled":
            update_job(job_id, status="failed", error=str(exc), completed_at=now(), results=outputs)


def enqueue(requests: list[GenerateRequest], title: str, kind="compare", gap_ms=200, metadata=None) -> dict:
    with LOCK:
        if sum(j["status"] in ["queued", "running"] for j in records("runs", "job.json")) >= 20:
            raise HTTPException(429, "队列已满，请等待现有任务完成")
        rows = []
        for req in requests:
            get_ref(req.reference_id)
            get_ref(req.emotion_reference_id)
            for engine_id in req.engines:
                if engine_state(ENGINES[engine_id])["status"] != "ready":
                    raise HTTPException(409, f"{ENGINES[engine_id]['name']} 环境或模型尚未就绪")
                if engine_id == "breeze2" and req.reference_id and not get_ref(req.reference_id)[1].get("transcript"):
                    raise HTTPException(422, "Breeze 克隆需要参考音频的准确文字稿")
                rows.append({"engine": engine_id, "request": req.model_dump()})
        job_id = uid()
        folder = DATA / "runs" / job_id
        folder.mkdir()
        job = {"id":job_id, "title":title, "kind":kind, "status":"queued", "created_at":now(),
               "segments":rows, "gap_ms":gap_ms, "results":[], "progress":"等待运行"}
        if metadata: job['metadata'] = metadata
        write_json(folder / "job.json", job)
        POOL.submit(execute_job, job_id)
        return job


def execute_study(job_id: str):
    folder = item_path('runs', job_id)
    job = read_json(folder / 'job.json')
    outputs, failures = [], []
    total = len(job['segments'])
    update_job(job_id, status='running', started_at=now(), progress=f'0 / {total} · 准备批处理')
    try:
        for engine_id in dict.fromkeys(row['engine'] for row in job['segments']):
            if read_json(folder / 'job.json')['status']=='cancelled': return
            engine = ENGINES[engine_id]
            if engine_id=='breeze2' and job.get('metadata',{}).get('wait_for_reference_review'):
                reference_ids={row['request']['reference_id'] for row in job['segments'] if row['engine']=='breeze2'}
                deadline=time.monotonic()+2400
                while any(get_ref(ref)[1].get('transcript_review',{}).get('status')!='ready' for ref in reference_ids):
                    if read_json(folder/'job.json')['status']=='cancelled': return
                    if time.monotonic()>deadline: raise RuntimeError('参考文字核对尚未完成，已保留已生成音频')
                    update_job(job_id,progress=f'{len(outputs)} / {total} · 等待参考录音文字核对')
                    time.sleep(2)
            tasks = []
            for row in job['segments']:
                if row['engine'] != engine_id: continue
                request = GenerateRequest(**row['request'])
                run_id = uid()
                output_dir = folder / run_id
                output_dir.mkdir()
                _, cwd, params = build_command(engine_id, request, output_dir / 'original.wav')
                reference_path, reference = get_ref(request.reference_id)
                tasks.append({'id':run_id, 'job_id':job_id, 'request':request.model_dump(),
                              'output_dir':str(output_dir), 'reference_path':str(reference_path),
                              'reference_transcript':reference['transcript'], 'reference_name':reference['name'],
                              'engine_name':engine['name'], 'license':engine['license'], 'parameters':params})
            task_file = folder / f'tasks-{engine_id}.json'
            write_json(task_file, tasks)
            environment = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONFAULTHANDLER='1',
                               HF_HOME=str(ROOT/'models/hf'), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                               INDEXTTS_W2V_BERT_DIR=str(ROOT/'models/facebook-w2v-bert-2.0'), TOKENIZERS_PARALLELISM='false')
            command = [str(cwd/'.venv/bin/python'), str(ROOT/'scripts/batch_worker.py'),
                       '--engine',engine_id, '--tasks',str(task_file), '--model',str(ROOT/engine['model_dir']),
                       '--vendor',str(cwd)]
            consumed = 0
            started = time.monotonic()
            with (folder/f'worker-{engine_id}.log').open('w') as log:
                with LOCK:
                    if read_json(folder/'job.json')['status']=='cancelled': return
                    process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=log,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                    ACTIVE[job_id] = process
                try:
                    while True:
                        if read_json(folder/'job.json')['status']=='cancelled': return
                        changed = False
                        while consumed < len(tasks):
                            task = tasks[consumed]
                            output_dir = Path(task['output_dir'])
                            success, failure = output_dir/'result.json', output_dir/'failure.json'
                            if success.exists(): outputs.append(read_json(success))
                            elif failure.exists(): failures.append(read_json(failure))
                            else: break
                            consumed += 1
                            changed = True
                        if changed:
                            update_job(job_id, results=outputs, failures=failures,
                                       progress=f'{len(outputs)} / {total} · {engine["name"]} · 失败 {len(failures)}')
                        if process.poll() is not None: break
                        if time.monotonic()-started > 3600:
                            os.killpg(process.pid, signal.SIGTERM)
                            process.wait(timeout=15)
                            raise RuntimeError(f'{engine["name"]} 批处理超时')
                        time.sleep(.4)
                finally:
                    ACTIVE.pop(job_id, None)
                for task in tasks[consumed:]:
                    output_dir = Path(task['output_dir'])
                    if (output_dir/'result.json').exists(): outputs.append(read_json(output_dir/'result.json'))
                    elif (output_dir/'failure.json').exists(): failures.append(read_json(output_dir/'failure.json'))
                    else: failures.append({'id':task['id'], 'engine':engine_id, 'reference_id':task['request']['reference_id'],
                                           'emotion':task['request']['emotion'], 'error':f'批处理退出码 {process.returncode}；输出未完成'})
            if outputs:
                result = outputs[-1]
                if result['engine']==engine_id:
                    evidence_path=DATA/'verification.json'
                    with LOCK:
                        evidence=read_json(evidence_path) if evidence_path.exists() else {}
                        evidence[engine_id]={'job_id':job_id,'run_id':result['id'],'created_at':now(),
                                             'artifact_revision':engine['artifact_revision'],'audio_sha256':result['metrics']['sha256'],
                                             'duration':result['metrics']['duration']}
                        write_json(evidence_path,evidence)
        update_job(job_id, status='partial' if failures else 'complete', results=outputs, failures=failures,
                   completed_at=now(), progress=f'{len(outputs)} / {total} · 批处理结束 · 失败 {len(failures)}')
    except Exception as exc:
        if read_json(folder/'job.json')['status']!='cancelled':
            update_job(job_id,status='failed',results=outputs,failures=failures,error=str(exc),completed_at=now())


@asynccontextmanager
async def lifespan(app):
    yield
    for job_id, p in list(ACTIVE.items()):
        update_job(job_id, status="interrupted", error="工作台已停止；已完成的音频保留")
        if p.poll() is None:
            try: os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError: pass
    POOL.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="TTS Lab", docs_url="/api/docs", lifespan=lifespan)


@app.middleware("http")
async def same_origin(request: Request, call_next):
    host = request.headers.get("host", "").split(":")[0]
    if host not in {"localhost", "127.0.0.1", "testserver"}:
        return JSONResponse({"detail":"仅允许本机访问"}, status_code=403)
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in {f"http://{request.headers.get('host')}"}:
        return JSONResponse({"detail":"不接受来自其他网页的操作"}, status_code=403)
    return await call_next(request)


@app.get("/api/state")
def state():
    return {"engines":[engine_state(e) for e in ENGINES.values()],
            "references":records("references", "reference.json"), "jobs":records("runs", "job.json"),
            "prompts":read_json(ROOT / "config/prompts.json"), "catalog":read_json(ROOT / "config/catalog.json"),
            "hardware":"Apple M3 Max · 64 GB", "date":now(), "cloud_spend":0}


@app.post("/api/generate")
def generate(req: GenerateRequest):
    return enqueue([req], req.title)


@app.post("/api/script")
def script(req: ScriptRequest):
    return enqueue(req.segments, req.title, "script", req.gap_ms)


class EmotionSweepRequest(BaseModel):
    request: GenerateRequest
    emotions: list[Literal["neutral", "happy", "sad", "angry", "calm", "surprised"]] = Field(
        default=["neutral", "happy", "sad", "angry", "calm"], min_length=2, max_length=6)


@app.post("/api/emotions")
def emotion_sweep(req: EmotionSweepRequest):
    if req.request.emotion_reference_id:
        raise HTTPException(422, "多语气对照请先关闭独立情绪参考")
    requests = [GenerateRequest(**{**req.request.model_dump(), "emotion":mood})
                for mood in dict.fromkeys(req.emotions)]
    return enqueue(requests, "多语气对照 · " + req.request.text[:20], "emotions")


class StudyRequest(BaseModel):
    reference_ids: list[str] = Field(min_length=1, max_length=10)
    engines: list[Literal['index2','index25','breeze2']] = Field(default=['index2','index25','breeze2'], min_length=1,max_length=3)
    emotions: list[Literal['neutral','happy','sad','angry','calm','surprised','afraid','melancholic','disgusted']] = Field(
        default=['neutral','happy','sad','angry','calm','surprised','afraid','melancholic','disgusted'], min_length=1,max_length=9)
    text: str = Field(min_length=1,max_length=350)
    title: str = Field(default='批量音色与语气对照',max_length=120)
    language: Literal['zh','en','ja','es','ar'] = 'zh'
    seed: int = Field(default=42,ge=0,le=2147483647)
    emotion_strength: float = Field(default=.65,ge=0,le=1)
    wait_for_reference_review: bool = False


@app.post('/api/studies')
def study(req: StudyRequest):
    requests=[]
    try:
        for reference in dict.fromkeys(req.reference_ids):
            for emotion in dict.fromkeys(req.emotions):
                requests.append(GenerateRequest(engines=list(dict.fromkeys(req.engines)), text=req.text,
                                                reference_id=reference, emotion=emotion, language=req.language,
                                                seed=req.seed, emotion_strength=req.emotion_strength, title=req.title))
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    return enqueue(requests,req.title,'study',metadata={'wait_for_reference_review':req.wait_for_reference_review})


class TextStudyItem(BaseModel):
    code: str = Field(pattern=r'^[A-Z][A-Z0-9_-]{1,15}$')
    title: str = Field(min_length=1,max_length=80)
    text: str = Field(min_length=1,max_length=2000)
    source_reference_id: str | None = None


class TextStudyRequest(BaseModel):
    reference_id: str
    items: list[TextStudyItem] = Field(min_length=1,max_length=60)
    engines: list[Literal['index2','index25','breeze2']] = Field(default=['index2'],min_length=1,max_length=3)
    emotions: list[Literal['neutral','calm']] = Field(default=['neutral','calm'],min_length=1,max_length=2)
    title: str = Field(default='固定音色 · 多文本与原声对照',max_length=120)
    language: Literal['zh','en','ja','es','ar'] = 'zh'
    seed: int = Field(default=42,ge=0,le=2147483647)
    emotion_strength: float = Field(default=.65,ge=0,le=1)


@app.post('/api/text-studies')
def text_study(req: TextStudyRequest):
    if len({item.code for item in req.items})!=len(req.items):
        raise HTTPException(422,'文本编号不能重复')
    requests=[]
    try:
        for item in req.items:
            if item.source_reference_id:
                _,source=get_ref(item.source_reference_id)
                if item.text.strip()!=source['transcript'].strip():
                    raise HTTPException(422,'原声对照文本必须与该录音保存的逐字稿完全一致')
            for mood in dict.fromkeys(req.emotions):
                requests.append(GenerateRequest(engines=list(dict.fromkeys(req.engines)),text=item.text,
                                                reference_id=req.reference_id,emotion=mood,language=req.language,
                                                seed=req.seed,emotion_strength=req.emotion_strength,
                                                title=f'{item.code} · {item.title}'))
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    return enqueue(requests,req.title,'study',metadata={'text_items':[item.model_dump() for item in req.items],
                                                     'fixed_reference_id':req.reference_id,
                                                     'source_transcript_note':'已有逐字稿，保留重复与口头语；未人工逐句听校'})


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    with LOCK:
        j = read_json(item_path("runs", job_id) / "job.json")
        if j["status"] not in {"running", "queued"}:
            raise HTTPException(409, "该任务已经结束")
        update_job(job_id, status="cancelled", completed_at=now())
        p = ACTIVE.get(job_id)
        if p and p.poll() is None:
            try: os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError: pass
    return {"status":"cancelled"}


async def save_upload(file: UploadFile, path: Path):
    size = 0
    with path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 50 * 1024 * 1024:
                path.unlink(missing_ok=True)
                raise HTTPException(413, "音频文件请控制在 50 MB 内")
            out.write(chunk)


@app.post("/api/references")
async def upload_ref(file: UploadFile = File(...), name: str = Form(...), transcript: str = Form(""), source_type: str = Form("user")):
    ref_id = uid()
    folder = DATA / "references" / ref_id
    folder.mkdir()
    raw = folder / "upload"
    try:
        await save_upload(file, raw)
        ffmpeg("-i", str(raw), "-t", "31", "-ar", "24000", "-ac", "1", str(folder / "audio.wav"))
        meta = inspect_audio(folder / "audio.wav")
        if meta["duration"] < 2 or meta["duration"] > 30.05:
            raise HTTPException(422, "参考音频需要 2–30 秒，建议 5–15 秒干净人声")
        item = {"id":ref_id, "name":name[:100], "transcript":transcript[:3000],
                "source_type":"synthetic" if source_type == "synthetic" else "user", "created_at":now(), "audio":meta}
        write_json(folder / "reference.json", item)
        raw.unlink(missing_ok=True)
        return item
    except HTTPException: raise
    except Exception as exc: raise HTTPException(422, f"不能读取音频：{str(exc)[:250]}")


class ReferenceEdit(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    transcript: str = Field(default="", max_length=3000)


@app.put("/api/references/{ref_id}")
def edit_reference(ref_id: str, req: ReferenceEdit):
    path = item_path("references", ref_id) / "reference.json"
    item = read_json(path)
    item.update(req.model_dump())
    write_json(path, item)
    return item


def result_path(job_id: str, run_id: str) -> Path:
    folder = item_path("runs", job_id)
    if not run_id.isalnum(): raise HTTPException(400, "无效结果编号")
    path = folder / run_id
    if not (path / "result.json").is_file(): raise HTTPException(404, "结果尚不存在")
    return path


@app.post("/api/results/{job_id}/{run_id}/reference")
def use_as_reference(job_id: str, run_id: str):
    path = result_path(job_id, run_id)
    result = read_json(path / "result.json")
    if not 2 <= result["metrics"]["duration"] <= 30:
        raise HTTPException(422, "请选取 2–30 秒的完整生成片段作为音色")
    ref_id = uid()
    folder = DATA / "references" / ref_id
    folder.mkdir()
    ffmpeg("-i", str(path / "original.wav"), "-ar", "24000", "-ac", "1", str(folder / "audio.wav"))
    item = {"id":ref_id, "name":f"合成音色 · {result['engine_name']} · {ref_id[:4]}",
            "transcript":result["text"], "source_type":"synthetic", "created_at":now(),
            "source_job":job_id, "source_run":run_id, "audio":inspect_audio(folder / "audio.wav"),
            "transcript_note":"来自生成请求；用于克隆前请试听确认无漏字或复读"}
    write_json(folder / "reference.json", item)
    return item


@app.post("/api/results/{job_id}/{run_id}/rating")
def rate(job_id: str, run_id: str, rating: Rating):
    path = result_path(job_id, run_id) / "result.json"
    with LOCK:
        result = read_json(path)
        result["rating"] = {**rating.model_dump(), "updated_at":now()}
        write_json(path, result)
        job_file = item_path("runs", job_id) / "job.json"
        job = read_json(job_file)
        job["results"] = [result if x["id"] == run_id else x for x in job["results"]]
        write_json(job_file, job)
    return result["rating"]


@app.get("/api/audio/{job_id}/{run_id}/{variant}")
def audio_file(job_id: str, run_id: str, variant: Literal["original", "matched", "mp3"]):
    folder = result_path(job_id, run_id)
    name = "audio.mp3" if variant == "mp3" else f"{variant}.wav"
    return FileResponse(folder / name, media_type="audio/mpeg" if variant == "mp3" else "audio/wav")


@app.get("/api/reference-audio/{ref_id}")
def reference_audio(ref_id: str,variant: Literal['original','matched']='original'):
    folder=item_path('references',ref_id)
    path=folder/'audio.wav'
    if variant=='matched':
        path=folder/'matched.wav'
        with LOCK:
            if not path.is_file():normalize_audio(folder/'audio.wav',path)
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/import")
async def import_audio(file: UploadFile = File(...), engine_name: str = Form(...), text: str = Form(...), job_id: str = Form("")):
    if not engine_name.strip() or not text.strip():
        raise HTTPException(422, "请填写模型名称和对应文本")
    if len(text)>5000 or len(engine_name)>100: raise HTTPException(422,"模型名称或文本过长")
    if job_id:
        folder = item_path("runs", job_id)
        job = read_json(folder / "job.json")
        if job["status"] in {"queued","running"}: raise HTTPException(409,"请等待当前实验结束再导入")
        if any(r["text"].strip()!=text.strip() for r in job["results"]): raise HTTPException(422,"加入同一对照组时，文本需要完全一致")
    else:
        job_id=uid();folder=DATA/"runs"/job_id;folder.mkdir()
        job={"id":job_id,"title":"外部对比 · "+text[:20],"kind":"import","status":"complete","created_at":now(),"results":[],"segments":[]}
    run_id=uid();out=folder/run_id;out.mkdir()
    raw=out/"upload"
    await save_upload(file,raw)
    try:
        ffmpeg("-i",str(raw),"-ar","24000","-ac","1",str(out/"original.wav"))
        metrics=inspect_audio(out/"original.wav")
        if metrics['duration']>600: raise ValueError('单条导入音频请控制在 10 分钟以内')
        loudness=normalize_audio(out/"original.wav",out/"matched.wav")
        ffmpeg("-i",str(out/"original.wav"),"-codec:a","libmp3lame","-q:a","2",str(out/"audio.mp3"))
    except Exception as exc: raise HTTPException(422,f"导入失败：{str(exc)[:250]}")
    result={"id":run_id,"job_id":job_id,"engine":"external","engine_name":engine_name.strip(),"text":text.strip(),"language":"unknown","emotion":"unknown","seed":None,"reference_id":None,"title":job['title'],"parameters":{"precision":"外部导入 · 参数未提供","source":"user import"},"metrics":metrics,"loudness":loudness,"wall_seconds":None,"rtf_cold":None,"created_at":now(),"rating":None,"license":"外部模型许可需自行核对"}
    write_json(out/"result.json",result)
    with LOCK:
        if (folder/"job.json").exists():job=read_json(folder/"job.json")
        job['results'].append(result)
        write_json(folder/"job.json",job)
    raw.unlink(missing_ok=True)
    return job


@app.get("/api/jobs/{job_id}/timeline/{kind}")
def timeline(job_id: str, kind: Literal["wav", "srt"]):
    path = item_path("runs", job_id) / f"script.{kind}"
    if not path.is_file(): raise HTTPException(404, "合成剧本尚未完成")
    return FileResponse(path, filename=path.name)


@app.get("/api/jobs/{job_id}/export")
def export(job_id: str):
    folder = item_path("runs", job_id)
    job = read_json(folder / "job.json")
    if job["status"] in {"running", "queued"}: raise HTTPException(409, "请等待任务结束再导出")
    target = DATA / "exports" / f"{job_id}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix in {".json", ".wav", ".mp3", ".srt", ".log"}:
                z.write(p, p.relative_to(folder))
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["model", "text", "seconds", "model_process_seconds", "rtf_cold", "naturalness", "clarity", "similarity", "emotion", "notes", "reference_name", "reference_id", "delivery", "timing_kind", "rtf_warm", "model_load_seconds"])
        for r in job["results"]:
            rating = r.get("rating") or {}
            writer.writerow([r["engine_name"], r["text"], r["metrics"]["duration"], r["wall_seconds"], r["rtf_cold"], *[rating.get(k, "") for k in ["naturalness", "clarity", "similarity", "emotion", "notes"]], r.get('reference_name',''),r.get('reference_id',''),r['emotion'],r.get('timing_kind','cold'),r.get('rtf_warm',''),r['parameters'].get('model_load_seconds','')])
        z.writestr("scores.csv", "\ufeff" + stream.getvalue())
    return FileResponse(target, filename=f"tts-lab-{job_id}.zip")


@app.get("/api/health")
def health(): return {"status":"ok", "service":"tts-lab", "version":"0.1.0"}


app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")


@app.get("/")
@app.get("/studio/{engine_id}")
@app.get("/compare")
def homepage(): return FileResponse(ROOT / "app/static/index.html")


def recover_jobs():
    for j in records("runs", "job.json"):
        if j["status"] in {"queued", "running"}:
            update_job(j["id"], status="interrupted", error="工作台已重启；已有音频保留，请重新提交未完成任务")


if __name__ == "__main__":
    import uvicorn
    recover_jobs()
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TTS_LAB_PORT", "8765")))
