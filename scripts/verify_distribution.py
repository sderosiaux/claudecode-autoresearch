#!/usr/bin/env python3
"""Install a wheel outside the checkout and smoke-check its real server and CLI."""

from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen
import zipfile


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {"src", "href"} and value and value.startswith("/"):
                self.paths.add(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    output = (
        args.output.resolve()
        if args.output
        else Path(tempfile.mkdtemp(prefix="autoresearch-clean-install-"))
    )
    output.mkdir(parents=True, exist_ok=True)
    workspace = output / "empty-cwd"
    workspace.mkdir()
    environment = dict(os.environ)
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "AUTORESEARCH_WEB_DIST",
        "AUTORESEARCH_AUTH_TOKEN",
    ):
        environment.pop(key, None)
    commands = []

    def command(argv):
        commands.append([str(item) for item in argv])
        result = subprocess.run(
            argv,
            cwd=workspace,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        return result.stdout

    with zipfile.ZipFile(wheel) as archive:
        entries = archive.namelist()
        assert "autoresearch/_web/index.html" in entries
        assert "autoresearch/_demo/StreamWorkload.java" in entries
        assert not any(
            part
            in {"meta", "node_modules", ".autoresearch-data", "__pycache__", ".env"}
            for entry in entries
            for part in Path(entry).parts
        )
    venv = output / "venv"
    command(["uv", "venv", "--python", "3.12", str(venv)])
    python = venv / "bin/python"
    command(["uv", "pip", "install", "--python", str(python), str(wheel)])
    dependencies = command(
        ["uv", "pip", "freeze", "--python", str(python)]
    ).splitlines()
    executable = venv / "bin/autoresearch"
    installed_module = command(
        [str(python), "-c", "import autoresearch; print(autoresearch.__file__)"]
    ).strip()
    assert Path(installed_module).is_relative_to(venv)
    help_output = command([str(executable), "--help"])
    assert "serve" in help_output and "demo" in help_output
    fixture = workspace / "public-jvm-fixture"
    command([str(executable), "demo", str(fixture)])
    fixture_revision = command(["git", "-C", str(fixture), "rev-parse", "HEAD"]).strip()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    data = output / "new-data"
    assert not data.exists()
    server_command = [
        str(executable),
        "serve",
        "--data-dir",
        str(data),
        "--port",
        str(port),
    ]
    commands.append(server_command)
    assets = {}
    with (output / "server.log").open("w") as log:
        server = subprocess.Popen(
            server_command, cwd=workspace, env=environment, stdout=log, stderr=log
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Installed server exited; see server.log")
                try:
                    with urlopen(base + "/api/campaigns", timeout=1) as response:
                        assert json.load(response) == []
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Installed server did not become ready"
                        ) from None
                    time.sleep(0.1)
            with urlopen(base + "/", timeout=5) as response:
                assert response.headers.get_content_type() == "text/html"
                html = response.read().decode()
            assert '<div id="root">' in html
            references = References()
            references.feed(html)
            assert any(path.endswith(".js") for path in references.paths)
            pending = set(references.paths)
            while pending:
                path = pending.pop()
                with urlopen(base + path, timeout=5) as response:
                    content = response.read()
                    assert content
                    assets[path] = {
                        "bytes": len(content),
                        "content_type": response.headers.get_content_type(),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                if path.endswith(".css"):
                    for value in re.findall(
                        r"url\([\"']?([^\)\"']+)", content.decode()
                    ):
                        if value.startswith("/assets/") and value not in assets:
                            pending.add(value)
            for name in ("font-licenses.txt", "icon-license.txt"):
                with urlopen(base + "/" + name, timeout=5) as response:
                    assert response.read()
            capabilities = json.loads(
                command([str(executable), "--url", base, "capabilities"])
            )
            assert capabilities["version"] == "0.1.0.dev0"
            created = json.loads(
                command(
                    [
                        str(executable),
                        "--url",
                        base,
                        "init",
                        str(fixture / "campaign.json"),
                    ]
                )
            )
            cid = created["campaign"]["id"]
            listed = json.loads(command([str(executable), "--url", base, "list"]))
            assert [item["id"] for item in listed] == [cid]
            detail = json.loads(
                command([str(executable), "--url", base, "status", cid])
            )
            assert detail["campaign"]["base_revision"] == fixture_revision
            assert detail["campaign"]["state"] == "draft"
            assert detail["budget"]["engaged"] == detail["budget"]["consumed"] == 0
            assert detail["experiments"] == []
            with urlopen(base + f"/api/campaigns/{cid}", timeout=5) as response:
                assert json.load(response)["campaign"]["id"] == cid
            (output / "campaign.json").write_text(json.dumps(detail, indent=2) + "\n")
            (output / "index.html").write_text(html)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    proof = {
        "wheel": str(wheel),
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "wheel_entries": entries,
        "installed_module": installed_module,
        "dependencies": dependencies,
        "working_directory": str(workspace),
        "data_directory": str(data),
        "fixture_revision": fixture_revision,
        "campaign_id": cid,
        "assets": assets,
        "commands": commands,
        "checks": {
            "bundled_ui": True,
            "linked_assets": True,
            "licenses": True,
            "fresh_venv_outside_checkout": True,
            "fresh_empty_data_directory": True,
            "cli_demo_committed": True,
            "cli_api_campaign_agree": True,
            "zero_model_spend": True,
            "no_benchmark_execution": True,
        },
    }
    (output / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(
        json.dumps(
            {
                "proof": str(output / "proof.json"),
                "checks": proof["checks"],
                "assets": len(assets),
            }
        )
    )


if __name__ == "__main__":
    main()
