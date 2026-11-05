"""Local ASR diagnostic, independent of subjective voice and emotion ratings."""
import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["HF_HUB_OFFLINE"] = "1"


def normalized(text):
    from opencc import OpenCC
    text = re.sub(r"\[(?:笑|叹气|咳嗽|清嗓子)\]|\((?:laugh|sigh|cough|clears throat)\)", "", text, flags=re.I)
    text = OpenCC("t2s").convert(text)
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(c for c in text if c.isalnum())


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        nxt = [i]
        for j, cb in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j-1] + (ca != cb)))
        row = nxt
    return row[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", action="append", help="只检查指定实验，可重复传入")
    args = parser.parse_args()
    import mlx_whisper
    results = []
    for path in sorted((ROOT / "data/runs").glob("*/job.json")):
        job = json.loads(path.read_text())
        if args.job and job["id"] not in args.job:
            continue
        if job["status"] in {"queued", "running"}:
            continue
        for result in job["results"]:
            folder = path.parent / result["id"]
            transcript = mlx_whisper.transcribe(str(folder / "original.wav"), path_or_hf_repo=str(ROOT / "models/whisper-base-mlx"), language=None if result['language'] == 'unknown' else result['language'], fp16=True, temperature=0, verbose=False)
            expected, actual = normalized(result["text"]), normalized(transcript["text"])
            record = {"job_id":job["id"], "run_id":result["id"], "model":result["engine_name"],
                      "emotion":result["emotion"], "requested_text":result["text"], "asr_text":transcript["text"],
                      "audio_sha256":result["metrics"]["sha256"],
                      "diagnostic_cer":round(distance(expected,actual)/max(1,len(expected)),4),
                      "asr_model":"mlx-community/whisper-base-mlx", "asr_revision":"1e3e249fb8d01c655324bd6841b1deadffd6d04c",
                      "normalization":"移除声音事件标签、NFKC、小写、繁体转简体、去除标点及空白",
                      "note":"Whisper base 自动转写，仅辅助发现漏字复读；含 ASR 本身错误，不作为音质或模型排名。"}
            check_path = folder / "asr-check.json"
            temporary = check_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2))
            temporary.replace(check_path)
            print(json.dumps(record,ensure_ascii=False),flush=True)
            results.append(record)
    (ROOT / "data/asr-check.json").write_text(json.dumps(results,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
