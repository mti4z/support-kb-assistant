import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.ratelimit import SlidingWindow, window
from app.tenants import create_tenant, delete_tenant

client = TestClient(app)


def test_sliding_window_blocks_then_recovers(monkeypatch):
    w = SlidingWindow()
    assert [w.hit("k", 2, 60) for _ in range(2)] == [0, 0]
    assert w.hit("k", 2, 60) > 0           # third one is refused
    assert w.hit("other", 2, 60) == 0      # another key is independent
    assert w.hit("k", 0, 60) == 0          # limit 0 = no limit
    t = [0.0]
    monkeypatch.setattr("app.ratelimit.time.monotonic", lambda: t[0])
    w2 = SlidingWindow()
    assert w2.hit("a", 1, 10) == 0 and w2.hit("a", 1, 10) > 0
    t[0] = 11.0
    assert w2.hit("a", 1, 10) == 0


def test_anonymous_ask_is_limited_per_minute(monkeypatch):
    monkeypatch.setenv("ANON_PER_MINUTE", "3")
    codes = [client.post("/ask", json={"question": "my vpn is not connecting"}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = client.post("/ask", json={"question": "my vpn is not connecting"})
    assert int(r.headers["retry-after"]) >= 1


def test_limit_is_per_visitor(monkeypatch):
    monkeypatch.setenv("ANON_PER_MINUTE", "1")
    h1, h2 = {"X-Forwarded-For": "1.1.1.1"}, {"X-Forwarded-For": "2.2.2.2"}
    assert client.get("/search?q=vpn", headers=h1).status_code == 200
    assert client.get("/search?q=vpn", headers=h1).status_code == 429
    assert client.get("/search?q=vpn", headers=h2).status_code == 200


def test_global_daily_cap_for_the_demo(monkeypatch):
    monkeypatch.setenv("ANON_GLOBAL_PER_DAY", "2")
    ok = [client.get("/search?q=vpn", headers={"X-Forwarded-For": f"9.9.9.{i}"}).status_code for i in range(4)]
    assert ok == [200, 200, 429, 429]


def test_tenant_has_its_own_limit(monkeypatch):
    monkeypatch.setenv("ANON_PER_MINUTE", "1")
    monkeypatch.setenv("TENANT_PER_MINUTE", "3")
    t, key = create_tenant("Limit test " + uuid.uuid4().hex[:6])
    try:
        codes = [client.get("/search?q=vpn", headers={"X-API-Key": key}).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        # the tenant's usage does not eat the anonymous allowance
        assert client.get("/search?q=vpn").status_code == 200
    finally:
        delete_tenant(t["id"])


def test_uploads_are_limited(monkeypatch):
    monkeypatch.setenv("UPLOADS_PER_HOUR", "2")
    t, key = create_tenant("Upload limit " + uuid.uuid4().hex[:6])
    try:
        files = lambda: [("files", ("a.txt", b"hello world notes", "text/plain"))]
        codes = [client.post("/tenant/documents", files=files(), headers={"X-API-Key": key}).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
    finally:
        delete_tenant(t["id"])
