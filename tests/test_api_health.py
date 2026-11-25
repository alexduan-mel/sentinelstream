from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app


def test_healthz_reports_build_version(tmp_path: Path) -> None:
    client = TestClient(create_app(version="abc1234", web_dist=tmp_path / "missing"))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "abc1234"}


def test_healthz_version_comes_from_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SENTINELSTREAM_VERSION", "deadbeef")
    client = TestClient(create_app(web_dist=tmp_path / "missing"))

    assert client.get("/healthz").json()["version"] == "deadbeef"


def test_serves_built_web_ui_without_shadowing_api(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>SentinelStream</title>", encoding="utf-8")
    client = TestClient(create_app(version="v", web_dist=tmp_path))

    assert "SentinelStream" in client.get("/").text
    assert client.get("/healthz").json()["status"] == "ok"


def test_no_web_ui_when_build_is_absent(tmp_path: Path) -> None:
    client = TestClient(create_app(version="v", web_dist=tmp_path / "missing"))

    assert client.get("/").status_code == 404


def test_client_side_routes_get_the_web_ui_but_api_and_assets_still_404(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>SentinelStream</title>", encoding="utf-8")
    client = TestClient(create_app(version="v", web_dist=tmp_path))

    for path in ("/tasks", "/login", "/alerts/0000320193-26-000001"):
        response = client.get(path)
        assert response.status_code == 200 and "SentinelStream" in response.text, path
    assert client.get("/assets/missing.js").status_code == 404
    api_miss = client.get("/api/no-such-route")
    assert api_miss.status_code == 404 and api_miss.json() == {"detail": "Not Found"}
