"""Download pinned research artifacts; resumable, with a completion manifest."""
import argparse
import hashlib
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from ranged_download import download

ROOT = Path(__file__).resolve().parents[1]
SPECS = {
    "index2": ("vanch007/mlx-indextts2-standard-8bit", "81e890787be1b0b174b9ec3dafd89642f578b286", "IndexTTS-2-mlx-8bit"),
    "index25": ("vanch007/mlx-indextts2-2.5-8bit", "3170ceff8032ab6d0c2945e72068a03ea8c68461", "IndexTTS-2.5-mlx-8bit"),
    "breeze2": ("rishikksh20/Breeze-TTS-2-mlx", "92266ef6e15dd4be290be416dd6b0bf9a2bab81b", "Breeze-TTS-2-mlx-int8"),
    "asr": ("mlx-community/whisper-base-mlx", "1e3e249fb8d01c655324bd6841b1deadffd6d04c", "whisper-base-mlx"),
    "asr-turbo": ("mlx-community/whisper-large-v3-turbo", "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb", "whisper-large-v3-turbo"),
}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("engine", choices=SPECS)
    args = p.parse_args()
    repo, revision, folder = SPECS[args.engine]
    manifest = ROOT / "config" / "downloads.json"
    existing = json.loads(manifest.read_text()) if manifest.exists() else {}
    print(f"Downloading {repo}@{revision} to models/{folder}", flush=True)
    raw = subprocess.check_output(["curl", "--http1.1", "-fsSL", "--retry", "5", "--retry-all-errors", "--max-time", "60", f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true"])
    info = json.loads(raw)
    target = ROOT / "models" / folder
    target.mkdir(parents=True, exist_ok=True)

    def fetch(file):
        name = file["rfilename"]
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        expected_size = file.get("size")
        expected_hash = file.get("lfs", {}).get("sha256")
        def valid(p):
            if not p.is_file() or (expected_size is not None and p.stat().st_size != expected_size):
                return False
            if expected_hash:
                h = hashlib.sha256()
                with p.open("rb") as f:
                    for block in iter(lambda: f.read(8*1024*1024), b""): h.update(block)
                return h.hexdigest() == expected_hash
            return True
        if valid(path):
            print(f"Verified {name}", flush=True)
            return {"file": name, "size": path.stat().st_size, "sha256": expected_hash}
        partial = path.with_name(path.name + ".partial")
        url = f"https://huggingface.co/{repo}/resolve/{revision}/{quote(name)}"
        print(f"Fetching {name} ({expected_size} bytes)", flush=True)
        if expected_size and expected_size > 16 * 1024 * 1024:
            download(url, path, expected_size, expected_hash, workers=6)
            print(f"Verified {name}", flush=True)
            return {"file": name, "size": path.stat().st_size, "sha256": expected_hash}
        # A new curl invocation recomputes the resume offset after a broken connection.
        # curl's internal retry reuses the original offset and can discard progress.
        for attempt in range(100):
            if expected_size and partial.exists() and partial.stat().st_size == expected_size:
                break
            result = subprocess.run(["curl", "--http1.1", "-fsSL", "--connect-timeout", "15", "--max-time", "150", "-C", "-", "-o", str(partial), url])
            if result.returncode == 0:
                break
            size = partial.stat().st_size if partial.exists() else 0
            print(f"Resume {name}: {size}/{expected_size} bytes; retry {attempt+1}", flush=True)
            time.sleep(2)
        else:
            raise RuntimeError(f"Download retries exhausted: {name}")
        if not valid(partial):
            raise RuntimeError(f"Integrity check failed: {name}")
        partial.replace(path)
        print(f"Verified {name}", flush=True)
        return {"file": name, "size": path.stat().st_size, "sha256": expected_hash}

    with ThreadPoolExecutor(max_workers=1) as pool:
        files = list(pool.map(fetch, info["siblings"]))
    item = {"repo": repo, "revision": revision, "directory": f"models/{folder}", "files": files}
    # Each downloader writes a separate receipt to avoid concurrent JSON updates.
    receipt = ROOT / "models" / f"{args.engine}.download.json"
    receipt.write_text(json.dumps(item, indent=2) + "\n")
    print(f"DOWNLOAD COMPLETE: {target}", flush=True)

if __name__ == "__main__":
    main()
