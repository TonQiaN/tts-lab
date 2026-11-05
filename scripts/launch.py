"""Launch only this workspace's loopback service; safe to run repeatedly."""
import argparse
import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"
PID = ROOT / "logs/server.pid"


def healthy():
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=1) as r:
            return json.load(r).get("service") == "tts-lab"
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    if args.stop:
        if PID.exists():
            pid = int(PID.read_text())
            command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
            if "app.server" in command and str(ROOT) in command:
                os.kill(pid, signal.SIGTERM)
                for _ in range(100):
                    if not healthy():
                        break
                    time.sleep(0.1)
                else:
                    raise SystemExit("工作台仍在停止，请等待当前任务退出后再启动。")
                PID.unlink(missing_ok=True)
                print("已停止 TTS Lab 工作台。")
            else:
                print("没有找到本工作区的运行进程。")
        return
    if not healthy():
        (ROOT / "logs").mkdir(exist_ok=True)
        with (ROOT / "logs/server.log").open("a") as log:
            p = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "app.server"], cwd=ROOT,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        PID.write_text(str(p.pid))
        for _ in range(40):
            if healthy(): break
            if p.poll() is not None: raise SystemExit("工作台启动失败，请查看 logs/server.log")
            time.sleep(0.25)
        else: raise SystemExit("工作台尚未响应，请查看 logs/server.log")
    print("TTS Lab: " + URL)
    if not args.no_open: subprocess.run(["open", URL], check=False)


if __name__ == "__main__": main()
