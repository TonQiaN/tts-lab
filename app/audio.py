from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def ffmpeg(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], capture_output=True, text=True, check=True, timeout=180)


def inspect_audio(path: Path) -> dict:
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("音频为空或含无效采样")
    mono = audio.mean(axis=1)
    amplitude = np.abs(mono)
    peak = float(np.max(amplitude))
    if peak < 0.00001:
        raise ValueError("生成结果为静音，请检查日志并重试")
    active = np.flatnonzero(amplitude > max(0.003, peak * 0.01))
    buckets = np.array_split(amplitude, min(160, len(amplitude)))
    return {
        "duration": round(len(audio) / sr, 4), "sample_rate": sr,
        "channels": audio.shape[1], "peak_dbfs": round(20 * np.log10(max(peak, 1e-10)), 2),
        "rms_dbfs": round(20 * np.log10(max(float(np.sqrt(np.mean(mono ** 2))), 1e-10)), 2),
        "clipping_ratio": round(float(np.mean(np.abs(audio) >= 0.999)), 6),
        "leading_silence": round(int(active[0]) / sr, 3) if len(active) else 0,
        "trailing_silence": round((len(mono) - int(active[-1]) - 1) / sr, 3) if len(active) else 0,
        "waveform": [round(float(b.max()) / max(peak, 1e-10), 3) for b in buckets],
        "sha256": sha256(path),
    }


def normalize_audio(source: Path, target: Path) -> dict:
    # Two-pass loudness matching. Originals remain untouched.
    first = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(source), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True, check=True, timeout=180)
    match = re.search(r'\{\s*"input_i".*?\}', first.stderr, re.S)
    if not match:
        raise RuntimeError("无法读取响度测量结果")
    m = json.loads(match.group())
    if any(m[k] in ("-inf", "inf", "nan") for k in ["input_i", "input_tp", "input_lra", "input_thresh"]):
        raise ValueError("音频太短或为静音，无法标准化响度")
    filt = (f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={m['input_i']}:"
            f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
            f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    ffmpeg("-i", str(source), "-af", filt, "-ar", "24000", "-ac", "1", str(target))
    return {"input_lufs": float(m["input_i"]), "target_lufs": -16, "target_true_peak": -1.5, "method": "ffmpeg loudnorm two-pass"}


def timecode(seconds: float) -> str:
    total = round(seconds * 1000)
    return f"{total//3600000:02}:{total//60000%60:02}:{total//1000%60:02},{total%1000:03}"


def assemble(paths: list[Path], texts: list[str], output: Path, gap_ms: int = 200) -> dict:
    chunks, subtitles, cursor = [], [], 0.0
    for i, (path, text) in enumerate(zip(paths, texts), 1):
        tmp = output.parent / f"segment-{i:03}.wav"
        ffmpeg("-i", str(path), "-ar", "24000", "-ac", "1", str(tmp))
        x, _ = sf.read(tmp, dtype="float32")
        duration = len(x) / 24000
        subtitles.append(f"{i}\n{timecode(cursor)} --> {timecode(cursor+duration)}\n{text}\n")
        chunks.append(x)
        cursor += duration
        if i < len(paths):
            chunks.append(np.zeros(round(gap_ms * 24), dtype=np.float32))
            cursor += gap_ms / 1000
    sf.write(output, np.concatenate(chunks), 24000, subtype="PCM_16")
    output.with_suffix(".srt").write_text("\n".join(subtitles), encoding="utf-8")
    return {"duration": round(cursor, 3), "segments": len(paths), "subtitle_type": "按生成片段时长对齐，非逐字强制对齐"}
