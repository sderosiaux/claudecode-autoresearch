"""API client and local server entry point. Mutations share the UI's engine."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import httpx
import yaml


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="autoresearch", description="Self-hosted performance experiment workbench"
    )
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--data-dir", type=Path, default=Path(".autoresearch-data"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    init = sub.add_parser("init")
    init.add_argument("manifest", type=Path)
    sub.add_parser("list")
    sub.add_parser("capabilities")
    for name in (
        "status",
        "assess",
        "prepare",
        "profile",
        "run",
        "pause",
        "stop",
        "resume",
        "reconcile",
        "compare",
        "report",
    ):
        p = sub.add_parser(name)
        p.add_argument("campaign")
    approve = sub.add_parser("approve")
    approve.add_argument("campaign")
    approve.add_argument("--protocol-hash", required=True)
    approve.add_argument(
        "--authority", choices=("operator", "delegated_agent"), default="operator"
    )
    approve.add_argument("--rationale", default="")
    protocol = sub.add_parser("protocol")
    protocol.add_argument("campaign")
    protocol.add_argument("manifest", type=Path)
    exp = sub.add_parser("experiment")
    exp.add_argument("campaign")
    exp.add_argument("patch", type=Path)
    exp.add_argument("--hypothesis", required=True)
    exp.add_argument("--strategy", default="user-proposed")
    exp.add_argument("--parent")
    exp.add_argument(
        "--origin", choices=("manual", "external_worker"), default="manual"
    )
    export = sub.add_parser("export")
    export.add_argument("campaign")
    export.add_argument("--output", type=Path, required=True)
    reproduce = sub.add_parser("reproduce")
    reproduce.add_argument("campaign")
    reproduce.add_argument("--experiment")
    draft = sub.add_parser("draft")
    draft.add_argument("campaign")
    draft.add_argument("--experiment", required=True)
    publish = sub.add_parser("publish")
    publish.add_argument("campaign")
    publish.add_argument("--experiment", required=True)
    publish.add_argument("--review-hash", required=True)
    publish.add_argument("--repository", required=True)
    publish.add_argument("--base", required=True)
    publish.add_argument("--head", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    if args.command == "serve":
        token = os.environ.get("AUTORESEARCH_AUTH_TOKEN")
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not token:
            parser.error(
                "Remote binding requires AUTORESEARCH_AUTH_TOKEN and a TLS reverse proxy"
            )
        import uvicorn
        from .api import create_app

        uvicorn.run(
            create_app(args.data_dir, token=token),
            host=args.host,
            port=args.port,
            access_log=False,
        )
        return
    if args.command == "demo":
        from .engine import git

        source = Path(__file__).resolve().parent / "_demo"
        if not source.is_dir():
            source = Path(__file__).resolve().parents[2] / "examples" / "jvm-streaming"
        if args.directory.exists():
            parser.error("Demo destination must not exist")
        shutil.copytree(source, args.directory)
        git(args.directory, "init")
        git(args.directory, "add", ".")
        git(args.directory, "commit", "-m", "Public JVM benchmark fixture")
        print(
            f"Created Git fixture at {args.directory.resolve()}. Import its campaign manifest with the repository path set to this directory."
        )
        return
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    if os.environ.get("AUTORESEARCH_AUTH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["AUTORESEARCH_AUTH_TOKEN"]
    try:
        with httpx.Client(base_url=args.url, headers=headers, timeout=30) as client:
            command = args.command
            route = (
                f"/api/campaigns/{args.campaign}"
                if hasattr(args, "campaign")
                else "/api/campaigns"
            )
            if command == "init":
                manifest = yaml.safe_load(args.manifest.read_text())
                if not Path(manifest["repository"]).is_absolute():
                    manifest["repository"] = str(
                        (args.manifest.parent / manifest["repository"]).resolve()
                    )
                response = client.post(route, json=manifest)
            elif command == "list":
                response = client.get(route)
            elif command == "capabilities":
                response = client.get("/api/capabilities")
            elif command in {"status", "compare"}:
                response = client.get(route)
            elif command in {"report", "export"}:
                response = client.get(route + "/" + command)
            elif command == "reproduce":
                response = client.get(
                    route + "/reproduce",
                    params={"experiment_id": args.experiment}
                    if args.experiment
                    else {},
                )
            elif command == "draft":
                response = client.get(
                    route + "/draft", params={"experiment_id": args.experiment}
                )
            elif command == "publish":
                response = client.post(
                    route + "/publish",
                    json={
                        "candidate_id": args.experiment,
                        "review_hash": args.review_hash,
                        "remote_repository": args.repository,
                        "base_branch": args.base,
                        "head_branch": args.head,
                    },
                )
            elif command == "approve":
                response = client.post(
                    route + "/approve",
                    json={
                        "protocol_hash": args.protocol_hash,
                        "authority": args.authority,
                        "rationale": args.rationale,
                    },
                )
            elif command == "protocol":
                response = client.put(
                    route + "/protocol",
                    json={"protocol": yaml.safe_load(args.manifest.read_text())},
                )
            elif command == "experiment":
                response = client.post(
                    route + "/experiments",
                    json={
                        "hypothesis": args.hypothesis,
                        "strategy": args.strategy,
                        "patch": args.patch.read_text(),
                        "parent_id": args.parent,
                        "origin": args.origin,
                    },
                )
            else:
                response = client.post(route + "/" + command)
            response.raise_for_status()
            if command == "export":
                args.output.write_bytes(response.content)
                print(args.output)
            elif command == "report":
                print(response.text, end="")
            else:
                print(json.dumps(response.json(), indent=2))
    except (httpx.HTTPError, ValueError, OSError) as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            print(exc.response.text, file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
