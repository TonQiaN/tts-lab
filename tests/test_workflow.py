import hashlib
import sys

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import server
from app.audio import assemble, inspect_audio, normalize_audio


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA", tmp_path)
    for d in ["runs", "references", "exports"]: (tmp_path / d).mkdir()
    return tmp_path


def test_require_reference_and_reject_unsupported_controls():
    with pytest.raises(ValidationError): server.GenerateRequest(engines=["index2"], text="你好")
    with pytest.raises(ValidationError): server.GenerateRequest(engines=["breeze2"], text="test", language="ja")
    with pytest.raises(ValidationError): server.GenerateRequest(engines=["index25"], reference_id="a", text="你好", instruction="laugh")
    with pytest.raises(ValidationError): server.GenerateRequest(engines=["breeze2"], text=" ")
    assert server.GenerateRequest(engines=["breeze2"], text="你好").seed == 42


def test_loopback_and_origin_boundaries(data):
    client = TestClient(server.app)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health", headers={"Host":"evil.test"}).status_code == 403
    assert client.post("/api/generate", headers={"Origin":"https://evil.test"}, json={"engines":["breeze2"],"text":"hi"}).status_code == 403
    assert client.get("/api/audio/a/../../passwd/original").status_code in [400,404,422]


def test_failed_generation_is_not_marked_complete(data, monkeypatch):
    job_id = "failuretest"
    folder = data / "runs" / job_id
    folder.mkdir()
    req = server.GenerateRequest(engines=["breeze2"], text="test")
    server.write_json(folder / "job.json", {"id":job_id, "status":"queued", "kind":"compare", "results":[], "segments":[{"engine":"breeze2","request":req.model_dump()}]})
    monkeypatch.setattr(server, "build_command", lambda *args: ([sys.executable,"-c","raise SystemExit(3)"], data, {}))
    server.execute_job(job_id)
    result = server.read_json(folder / "job.json")
    assert result["status"] == "failed"
    assert result["results"] == []
    assert "生成失败" in result["error"]


def test_audio_matching_preserves_source_and_script_timing(tmp_path):
    sr = 24000
    t = np.arange(sr*3) / sr
    signal = (.08*np.sin(2*np.pi*210*t)*(0.6+0.4*np.sin(2*np.pi*3*t))).astype(np.float32)
    source = tmp_path / "original.wav"
    sf.write(source, signal, sr)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    metrics = inspect_audio(source)
    assert metrics["duration"] == 3
    norm = normalize_audio(source, tmp_path / "matched.wav")
    assert norm["target_lufs"] == -16
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    result = assemble([source,source], ["第一句", "第二句"], tmp_path / "script.wav", 200)
    assert result["duration"] == 6.2
    assert "00:00:03,200 --> 00:00:06,200" in (tmp_path / "script.srt").read_text()


def test_silence_is_rejected(tmp_path):
    path = tmp_path / "silence.wav"
    sf.write(path,np.zeros(24000),24000)
    with pytest.raises(ValueError,match="静音"): inspect_audio(path)


def test_breeze_transcript_required(data):
    folder=data / "references" / "ref1"
    folder.mkdir()
    server.write_json(folder / "reference.json", {"audio":{"sha256":"abc"},"transcript":""})
    req=server.GenerateRequest(engines=["breeze2"],text="hi",reference_id="ref1")
    with pytest.raises(ValueError,match="准确文字稿"): server.build_command("breeze2", req, data / "out.wav")


def test_reference_upload_and_edit(data):
    import io
    buffer=io.BytesIO()
    t=np.arange(24000*3)/24000
    sf.write(buffer,.1*np.sin(2*np.pi*220*t),24000,format="WAV")
    client=TestClient(server.app)
    r=client.post("/api/references",files={"file":("test.wav",buffer.getvalue(),"audio/wav")},data={"name":"测试","transcript":"你好"})
    assert r.status_code==200,r.text
    ref_id=r.json()["id"]
    assert client.get(f"/api/reference-audio/{ref_id}").status_code==200
    r=client.put(f"/api/references/{ref_id}",json={"name":"已修改","transcript":"准确原文"})
    assert r.json()["transcript"]=="准确原文"


def test_emotion_sweep_keeps_comparison_conditions(data, monkeypatch):
    captured = []
    def capture(requests, title, kind):
        captured.extend(requests)
        return {"kind":kind, "count":len(requests)}
    monkeypatch.setattr(server, "enqueue", capture)
    client = TestClient(server.app)
    response = client.post("/api/emotions", json={"request":{"engines":["breeze2"], "text":"相同的测试文本", "seed":123}, "emotions":["neutral","happy","sad"]})
    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert {r.text for r in captured} == {"相同的测试文本"}
    assert {r.seed for r in captured} == {123}
    assert [r.emotion for r in captured] == ["neutral","happy","sad"]


def test_success_evidence_rating_and_export_survive_readback(data, monkeypatch):
    import json
    import zipfile
    from pathlib import Path
    job_id = "persisttest"
    folder = data / "runs" / job_id
    folder.mkdir()
    req = server.GenerateRequest(engines=["breeze2"], text="验证持久化")
    server.write_json(folder / "job.json", {"id":job_id,"title":"持久化测试", "status":"queued", "kind":"compare", "results":[], "segments":[{"engine":"breeze2","request":req.model_dump()}]})
    def command(engine, request, output):
        code = "import numpy as np,soundfile as sf; t=np.arange(72000)/24000; sf.write(" + repr(str(output)) + ",.08*np.sin(2*np.pi*210*t),24000)"
        return [sys.executable,"-c",code], data, {"precision":"test"}
    monkeypatch.setattr(server, "build_command", command)
    server.execute_job(job_id)
    job = server.read_json(folder / "job.json")
    assert job["status"] == "complete"
    result = job["results"][0]
    evidence = server.read_json(data / "verification.json")["breeze2"]
    assert evidence["audio_sha256"] == result["metrics"]["sha256"]
    client = TestClient(server.app)
    endpoint = f"/api/results/{job_id}/{result['id']}/rating"
    response = client.post(endpoint, json={"naturalness":4,"clarity":5,"notes":"测试评分持久化，不是模型评分","blind":True})
    assert response.status_code == 200
    assert server.read_json(folder / "job.json")["results"][0]["rating"]["clarity"] == 5
    export = client.get(f"/api/jobs/{job_id}/export")
    assert export.status_code == 200
    import io
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        assert "scores.csv" in archive.namelist()
        assert f"{result['id']}/matched.wav" in archive.namelist()
        saved = json.loads(archive.read(f"{result['id']}/result.json"))
        assert saved["rating"]["blind"] is True


def test_external_import_preserves_unknown_parameters_and_group_text(data):
    import io
    buffer = io.BytesIO()
    t = np.arange(72000) / 24000
    sf.write(buffer, .08*np.sin(2*np.pi*210*t), 24000, format="WAV")
    client = TestClient(server.app)
    response = client.post("/api/import", files={"file":("external.wav",buffer.getvalue(),"audio/wav")},
                           data={"engine_name":"外部引擎测试", "text":"相同原文"})
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["results"][0]["wall_seconds"] is None
    assert job["results"][0]["rtf_cold"] is None
    assert job["results"][0]["reference_id"] is None
    mismatch = client.post("/api/import", files={"file":("external.wav",buffer.getvalue(),"audio/wav")},
                           data={"engine_name":"另一个引擎", "text":"不同原文", "job_id":job["id"]})
    assert mismatch.status_code == 422


def test_study_covers_each_reference_engine_and_emotion(data, monkeypatch):
    captured={}
    def capture(requests,title,kind,metadata=None):
        captured['requests']=requests
        captured['metadata']=metadata
        return {'count':sum(len(r.engines) for r in requests),'kind':kind}
    monkeypatch.setattr(server,'enqueue',capture)
    client=TestClient(server.app)
    response=client.post('/api/studies',json={'reference_ids':['ref1','ref2','ref1'],'text':'相同的测试台词。','wait_for_reference_review':True})
    assert response.status_code==200,response.text
    assert response.json()=={'count':54,'kind':'study'}
    assert {r.reference_id for r in captured['requests']}=={'ref1','ref2'}
    assert len({r.emotion for r in captured['requests']})==9
    assert {r.seed for r in captured['requests']}=={42}
    assert captured['metadata']['wait_for_reference_review'] is True


def test_study_rejects_incompatible_languages(data,monkeypatch):
    client=TestClient(server.app)
    response=client.post('/api/studies',json={'reference_ids':['ref1'],'language':'ja','text':'こんにちは。'})
    assert response.status_code==422


def test_text_study_keeps_clone_reference_separate_from_comparison_source(data,monkeypatch):
    folder=data/'references/source2';folder.mkdir()
    server.write_json(folder/'reference.json',{'transcript':'对，对，我说的就是这个环境。'})
    captured={}
    def capture(requests,title,kind,metadata=None):
        captured.update(requests=requests,metadata=metadata)
        return {'kind':kind,'count':len(requests)}
    monkeypatch.setattr(server,'enqueue',capture)
    response=TestClient(server.app).post('/api/text-studies',json={'reference_id':'fixedR001','items':[
        {'code':'S001','title':'原声逐字对照','text':'对，对，我说的就是这个环境。','source_reference_id':'source2'},
        {'code':'N001','title':'新文本','text':'你先慢慢说。'}]})
    assert response.status_code==200,response.text
    assert response.json()=={'kind':'study','count':4}
    assert {r.reference_id for r in captured['requests']}=={'fixedR001'}
    assert {r.emotion for r in captured['requests']}=={'neutral','calm'}
    assert {tuple(r.engines) for r in captured['requests']}=={('index2',)}
    assert captured['metadata']['text_items'][0]['source_reference_id']=='source2'
    assert captured['requests'][0].text=='对，对，我说的就是这个环境。'


def test_text_study_rejects_rewritten_source_and_duplicate_codes(data,monkeypatch):
    folder=data/'references/source2';folder.mkdir()
    server.write_json(folder/'reference.json',{'transcript':'嗯，对，对。'})
    client=TestClient(server.app)
    response=client.post('/api/text-studies',json={'reference_id':'fixedR001','items':[
        {'code':'S001','title':'原声','text':'对。','source_reference_id':'source2'}]})
    assert response.status_code==422
    assert '逐字稿完全一致' in response.text
    response=client.post('/api/text-studies',json={'reference_id':'fixedR001','items':[
        {'code':'N001','title':'甲','text':'你好。'},{'code':'N001','title':'乙','text':'再见。'}]})
    assert response.status_code==422
    assert '编号不能重复' in response.text
