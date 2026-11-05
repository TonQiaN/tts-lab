"""Rebuild isolated, pinned local environments without cloud services."""
import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd=ROOT):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="下载并校验三套权重及编码器")
    args = parser.parse_args()
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise SystemExit("本方案需要 Apple Silicon Mac。")
    for executable in ("uv", "git", "ffmpeg", "sox"):
        if not shutil.which(executable):
            raise SystemExit(f"缺少 {executable}；先执行 brew install uv git ffmpeg sox")
    run(["uv", "sync", "--frozen"])
    (ROOT / "vendor").mkdir(exist_ok=True)
    for source in json.loads((ROOT / "config/upstreams.json").read_text()):
        target = ROOT / source["directory"]
        if not target.exists():
            run(["git", "clone", "--no-checkout", source["url"], target])
            run(["git", "checkout", "--detach", source["commit"]], target)
        current = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target, text=True).strip()
        if current != source["commit"]:
            raise SystemExit(f"{target} 版本不符；请先保留本地修改，再检出 {source['commit']}")
        shutil.copyfile(ROOT / source["lock"], target / "uv.lock")
        if "breeze" in target.name:
            run(["uv", "sync", "--frozen", "--no-dev"], target)
        else:
            run(["uv", "sync", "--frozen", "--extra", "v2", "--extra", "v25", "--extra", "webui"], target)
    if args.download:
        for engine in ("breeze2", "index25", "index2"):
            run([ROOT / ".venv/bin/python", ROOT / "scripts/download_models.py", engine])
        run([ROOT / ".venv/bin/python", ROOT / "scripts/download_auxiliary.py"])
    print("环境准备完成。双击桌面项目中的 启动语音实验室.command。")


if __name__ == "__main__":
    main()
