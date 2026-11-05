"""Resume the three large wheels before finishing the locked uv installation."""
import subprocess
import tomllib
from pathlib import Path
from ranged_download import download

ROOT=Path(__file__).resolve().parents[1]
env=ROOT/'vendor/breeze-tts-mlx'
wheel_dir=ROOT/'models/wheels'
wheel_dir.mkdir(exist_ok=True)
lock=tomllib.loads((env/'uv.lock').read_text())
for package in lock['package']:
    if package['name'] not in ['torch','mlx-metal','onnxruntime']:continue
    choices=[w for w in package.get('wheels',[]) if 'macosx' in w['url'] and 'arm64' in w['url'] and ('-cp312-' in w['url'] or '-py3-none-' in w['url'])]
    if not choices:raise RuntimeError(f"No compatible wheel for {package['name']}")
    wheel=sorted(choices,key=lambda w:tuple(int(n) for n in w['url'].split('macosx_')[1].split('_arm64')[0].split('_')),reverse=True)[0]
    path=wheel_dir/wheel['url'].split('/')[-1]
    print('Fetching '+path.name,flush=True)
    download(wheel['url'],path,wheel['size'],wheel['hash'].split(':',1)[1],workers=6)
    subprocess.run(['/opt/homebrew/bin/uv','pip','install','--python',str(env/'.venv/bin/python'),'--no-deps',str(path)],check=True)
requirements=ROOT/'config/locks/breeze-requirements.txt'
subprocess.run(['/opt/homebrew/bin/uv','export','--frozen','--no-dev','--no-emit-project','--format','requirements-txt','--output-file',str(requirements)],cwd=env,check=True,stdout=subprocess.DEVNULL)
subprocess.run(['/opt/homebrew/bin/uv','pip','install','--python',str(env/'.venv/bin/python'),'--require-hashes','-r',str(requirements),'--find-links',str(wheel_dir)],cwd=env,check=True)
print('BREEZE ENVIRONMENT COMPLETE',flush=True)
