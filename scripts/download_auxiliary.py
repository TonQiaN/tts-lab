"""Pin and cache IndexTTS reference encoders, shared by versions 2 and 2.5."""
import json
import subprocess
from pathlib import Path
from urllib.parse import quote
from ranged_download import download, digest

ROOT=Path(__file__).resolve().parents[1]
SPECS=[
    ("facebook/w2v-bert-2.0","da985ba0987f70aaeb84a80f2851cfac8c697a7b",["config.json","preprocessor_config.json","model.safetensors"], ROOT/"models/facebook-w2v-bert-2.0"),
    ("funasr/campplus","e4b6ede7ce16997aff4ae69fbca1f0175e2afede",["campplus_cn_common.bin"], None),
    ("amphion/MaskGCT","265c6cef07625665d0c28d2faafb1415562379dc",["semantic_codec/model.safetensors"], None),
]


def main():
    receipts=[]
    for repo,revision,files,local in SPECS:
        cache=ROOT/"models/hf/hub"/("models--"+repo.replace('/','--'))
        target=local or cache/"snapshots"/revision
        target.mkdir(parents=True,exist_ok=True)
        metadata=ROOT/'config/artifacts'/(repo.replace('/','--')+'.json')
        if metadata.exists():
            info=json.loads(metadata.read_text())
            if info['sha']!=revision:raise RuntimeError('Pinned metadata mismatch')
        else:
            info=json.loads(subprocess.check_output(["curl","--http1.1","-fsSL","--retry","8","--retry-all-errors","--max-time","60",f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true"]))
        checked=[]
        for f in info['siblings']:
            if f['rfilename'] not in files:continue
            path=target/f['rfilename'];path.parent.mkdir(parents=True,exist_ok=True)
            url=f"https://huggingface.co/{repo}/resolve/{revision}/{quote(f['rfilename'])}"
            expected=f.get('lfs',{}).get('sha256')
            print(f"Fetching {repo}/{f['rfilename']}",flush=True)
            if f['size']>16*1024*1024:download(url,path,f['size'],expected,workers=6)
            elif not path.exists() or path.stat().st_size!=f['size']:
                subprocess.run(['curl','--http1.1','-fsSL','--retry','8','--retry-all-errors','--max-time','120','-o',str(path),url],check=True)
            if path.stat().st_size!=f['size'] or expected and digest(path)!=expected:raise RuntimeError(f"Integrity mismatch {path.name}")
            checked.append({'file':f['rfilename'],'sha256':digest(path)})
        if not local:
            (cache/'refs').mkdir(exist_ok=True)
            (cache/'refs/main').write_text(revision)
        receipts.append({'repo':repo,'revision':revision,'files':checked})
    (ROOT/'models/auxiliary.download.json').write_text(json.dumps(receipts,indent=2))
    print("AUXILIARY DOWNLOAD COMPLETE",flush=True)

if __name__=='__main__':main()
