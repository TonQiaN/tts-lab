"""Report artifact, adapter, and real-generation evidence; optionally hash all weights."""
import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.audio import sha256
from app.server import ENGINES, engine_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hash", action="store_true")
    args = parser.parse_args()
    problems = []
    states = []
    for engine in ENGINES.values():
        state = engine_state(engine)
        states.append({k: state[k] for k in ("id", "installed", "model_ready", "verified")})
        if state["status"] != "ready":
            problems.append(engine["id"] + " 环境或权重尚未准备完成")
    for source in json.loads((ROOT / "config/upstreams.json").read_text()):
        target = ROOT / source["directory"]
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=target, capture_output=True, text=True) if target.exists() else None
        if not result or result.stdout.strip() != source["commit"]:
            problems.append(source["directory"] + " 代码版本不匹配")
        if target.exists() and (target / "uv.lock").exists() and sha256(target / "uv.lock") != sha256(ROOT / source["lock"]):
            problems.append(source["directory"] + " 依赖锁不匹配")
    if args.hash:
        for receipt in (ROOT / "models").glob("*.download.json"):
            content = json.loads(receipt.read_text())
            items = content if isinstance(content, list) else [content]
            for item in items:
                if "directory" in item:
                    folder = ROOT / item["directory"]
                elif item["repo"] == "facebook/w2v-bert-2.0":
                    folder = ROOT / "models/facebook-w2v-bert-2.0"
                else:
                    folder = ROOT / "models/hf/hub" / ("models--" + item["repo"].replace("/", "--")) / "snapshots" / item["revision"]
                for entry in item["files"]:
                    p = folder / entry["file"]
                    if not p.is_file() or (entry.get("sha256") and sha256(p) != entry["sha256"]):
                        problems.append("校验失败: " + str(p))
    report = {"hardware":platform.machine(), "engines":states, "hash_check":args.hash, "problems":problems}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(bool(problems))


if __name__ == "__main__":
    main()
