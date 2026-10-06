"""Step 0 stub tests. Replaced wholesale in Task 3."""

from fastapi.testclient import TestClient

import app as coach


def test_step0_fires_once_then_goes_quiet(monkeypatch):
    async def no_push(uid):
        pass

    monkeypatch.setattr(coach, "silence_push", no_push)
    client = TestClient(coach.app)
    body = {"session_id": "uid-1", "segments": [{"text": "hi there", "is_user": False}]}
    assert client.post("/webhook", json=body).json() == {"message": "coach test"}
    assert client.post("/webhook", json=body).json() == {}


def test_reenable_probe_gets_200():
    r = TestClient(coach.app).post("/webhook", json={})
    assert r.status_code == 200
    assert r.json() == {}
