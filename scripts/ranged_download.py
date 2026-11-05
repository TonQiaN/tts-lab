"""Verified bounded HTTP range downloads with reusable chunks on unstable links."""
import concurrent.futures
import hashlib
import os
import subprocess
import time
from pathlib import Path


def direct_url(url):
    for _ in range(5):
        r = subprocess.run(["curl","--http1.1","-sSI","--connect-timeout","10","--max-time","30",url],capture_output=True,text=True)
        if r.returncode == 0:
            locations=[s.split(":",1)[1].strip() for s in r.stdout.splitlines() if s.lower().startswith("location:")]
            if locations:
                return "https://huggingface.co"+locations[-1] if locations[-1].startswith("/") else locations[-1]
            return url
        time.sleep(2)
    return url


def download(url: str, path: Path, size: int, expected_hash: str | None = None, workers: int = 6):
    path=Path(path)
    if path.is_file() and path.stat().st_size==size:
        if not expected_hash or digest(path)==expected_hash:return
    partial=path.with_name(path.name+".partial")
    partial.parent.mkdir(parents=True,exist_ok=True)
    offset=partial.stat().st_size if partial.exists() else 0
    chunks=path.with_name(path.name+".chunks")
    chunks.mkdir(exist_ok=True)
    cdn=direct_url(url)
    step=4*1024*1024
    ranges=[(start,min(start+step,size)-1) for start in range(offset,size,step)]
    completed=0

    def fetch(pair):
        start,end=pair
        target=chunks/f"{start}-{end}"
        if target.exists() and target.stat().st_size==end-start+1:return target
        part_url=cdn
        for attempt in range(50):
            if attempt and attempt%8==0:
                part_url=direct_url(url+('&' if '?' in url else '?')+'download=true&refresh='+str(int(time.time())))
            temp=target.with_suffix('.tmp')
            r=subprocess.run(["curl","--http1.1","-fsSL","--connect-timeout","10","--max-time","60","--range",f"{start}-{end}","-o",str(temp),"-w","%{http_code}",part_url],capture_output=True,text=True)
            if r.returncode==0 and r.stdout.strip()=="206" and temp.stat().st_size==end-start+1:
                temp.replace(target)
                return target
            if attempt%8==7:
                print(f"Retry range {path.name} {start}: curl={r.returncode}, HTTP={r.stdout.strip()}, bytes={temp.stat().st_size if temp.exists() else 0}",flush=True)
            time.sleep(min(attempt+1,5))
        raise RuntimeError(f"Range retries exhausted for {path.name} at {start}")

    # Consume in order so the contiguous .partial always advances after each part.
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool, partial.open('ab') as out:
        for chunk in pool.map(fetch,ranges):
            with chunk.open('rb') as f:
                for b in iter(lambda:f.read(1024*1024),b''):out.write(b)
            out.flush()
            chunk.unlink()
            completed+=1
            if completed%10==0 or completed==len(ranges):
                print(f"{path.name}: {out.tell()}/{size} bytes",flush=True)
    if partial.stat().st_size!=size or (expected_hash and digest(partial)!=expected_hash):
        raise RuntimeError(f"Integrity mismatch: {path.name}")
    partial.replace(path)
    try:chunks.rmdir()
    except OSError:pass


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()
