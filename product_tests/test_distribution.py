"""Distribution inputs and bundled routes must not depend on checkout layout."""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoresearch import api, cli

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_backend", ROOT / "packaging/backend.py"
)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


def release_tree(root):
    for name in (
        "pyproject.toml",
        "LICENSE",
        "packaging/backend.py",
        "packaging/README.md",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public input")
    package = root / "product/autoresearch"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VERSION = 'test'")
    web = root / "web/dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text('<script src="/assets/app.js"></script>')
    (web / "assets/app.js").write_text("console.log('public UI')")
    demo = root / "examples/jvm-streaming"
    demo.mkdir(parents=True)
    for name in backend.DEMO_FILES:
        (demo / name).write_text("public fixture")
    return package, web, demo


def test_release_stages_only_public_runtime_inputs(tmp_path):
    package, web, demo = release_tree(tmp_path)
    for directory in (
        tmp_path / "meta",
        package / "__pycache__",
        web.parent / "node_modules",
    ):
        directory.mkdir(parents=True)
        (directory / "private.txt").write_text("must not ship")
    (demo / "benchmark.json").write_text("private measurements")
    (package / ".env").write_text("SECRET=do-not-ship")
    with backend._stage(tmp_path) as staging:
        files = {
            p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()
        }
        assert "product/autoresearch/_web/assets/app.js" in files
        assert "product/autoresearch/_demo/StreamWorkload.java" in files
        assert not any(
            "private" in p
            or "node_modules" in p
            or ".env" in p
            or "benchmark.json" in p
            for p in files
        )
        assert len([p for p in files if "/_demo/" in p]) == len(backend.DEMO_FILES)
    assert not (package / "_web").exists(), "Build must not mutate package source"


def test_release_missing_ui_and_unexpected_asset_fail_closed(tmp_path):
    _, web, _ = release_tree(tmp_path)
    (web / "index.html").unlink()
    with pytest.raises(ValueError, match="Production UI is missing"):
        with backend._stage(tmp_path):
            pass
    (web / "index.html").write_text("UI")
    (web / "assets/app.js.map").write_text("private source paths")
    with pytest.raises(ValueError, match="file type"):
        with backend._stage(tmp_path):
            pass


def test_release_rejects_symlinks_and_rebuilds_from_bundled_sdist(tmp_path):
    package, web, demo = release_tree(tmp_path)
    (web / "secret.txt").symlink_to(tmp_path / "LICENSE")
    with pytest.raises(ValueError, match="Unexpected release UI input"):
        with backend._stage(tmp_path):
            pass
    (web / "secret.txt").unlink()
    web.rename(package / "_web")
    demo.rename(package / "_demo")
    with backend._stage(tmp_path) as staging:
        assert (staging / "product/autoresearch/_web/index.html").is_file()
        assert (staging / "product/autoresearch/_demo/campaign.json").is_file()


def test_bundled_ui_and_explicit_override_are_cwd_independent(tmp_path, monkeypatch):
    installed = tmp_path / "site-packages/autoresearch"
    bundled = installed / "_web"
    bundled.mkdir(parents=True)
    (bundled / "index.html").write_text("bundled production UI")
    monkeypatch.setattr(api, "__file__", str(installed / "api.py"))
    monkeypatch.delenv("AUTORESEARCH_WEB_DIST", raising=False)
    unrelated = tmp_path / "empty-cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    with TestClient(api.create_app(tmp_path / "data1")) as client:
        assert client.get("/").text == "bundled production UI"
        assert client.get("/api/campaigns").json() == []
    override = tmp_path / "override"
    override.mkdir()
    (override / "index.html").write_text("explicit UI override")
    monkeypatch.setenv("AUTORESEARCH_WEB_DIST", str(override))
    with TestClient(api.create_app(tmp_path / "data2")) as client:
        assert client.get("/").text == "explicit UI override"
    monkeypatch.setenv("AUTORESEARCH_WEB_DIST", str(tmp_path / "missing"))
    with TestClient(api.create_app(tmp_path / "data3")) as client:
        assert client.get("/").json()["product"] == "Autoresearch"


def test_cli_demo_uses_bundled_fixture(tmp_path, monkeypatch):
    installed = tmp_path / "site-packages/autoresearch"
    bundled = installed / "_demo"
    bundled.mkdir(parents=True)
    (bundled / "README.md").write_text("bundled public fixture")
    monkeypatch.setattr(cli, "__file__", str(installed / "cli.py"))
    destination = tmp_path / "demo"
    cli.main(["demo", str(destination)])
    assert (destination / "README.md").read_text() == "bundled public fixture"
    assert (destination / ".git").is_dir()
