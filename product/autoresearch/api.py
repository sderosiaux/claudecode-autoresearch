"""Local web/API surface. No background task is owned by a browser request."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import hashlib
import os
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles

from .engine import Engine
from .models import (
    CampaignCreate,
    ExperimentCreate,
    Approval,
    ProtocolUpdate,
    PublishRequest,
)
from .store import Conflict
from .provider import ProviderError
from .publisher import PublicationError


def create_app(
    data_dir: Path | str | None = None,
    *,
    engine: Engine | None = None,
    token: str | None = None,
):
    owned = engine is None
    supervisor = engine or Engine(data_dir or Path.cwd() / ".autoresearch-data")

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owned:
            supervisor.close()

    app = FastAPI(
        title="Autoresearch Workbench", version="0.1.0.dev0", lifespan=lifespan
    )
    app.state.engine = supervisor

    @app.middleware("http")
    async def protect_local_api(request: Request, call_next):
        # Protect localhost from cross-site drive-by command execution, including
        # DNS rebinding. No permissive CORS. Vite preserves localhost Host.
        host = request.url.hostname or ""
        if not token and host not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return JSONResponse(
                {"detail": "Host is not an allowed local address"}, status_code=403
            )
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit

            parsed = urlsplit(origin)
            if parsed.hostname != host or parsed.scheme not in {"http", "https"}:
                return JSONResponse(
                    {"detail": "Cross-origin requests are not allowed"}, status_code=403
                )
        if token and request.url.path.startswith("/api/"):
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied, "Bearer " + token):
                return JSONResponse(
                    {"detail": "Authentication required"}, status_code=401
                )
        length = request.headers.get("content-length")
        if length and (not length.isdecimal() or int(length) > 1_000_000):
            return JSONResponse(
                {"detail": "Request exceeds size limit"}, status_code=413
            )
        key = (
            request.headers.get("idempotency-key")
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}
            else None
        )
        if key:
            if len(key) > 160:
                return JSONResponse(
                    {"detail": "Idempotency key too long"}, status_code=400
                )
            body = await request.body()
            if len(body) > 1_000_000:
                return JSONResponse(
                    {"detail": "Request exceeds size limit"}, status_code=413
                )
            fingerprint = hashlib.sha256(
                request.method.encode() + str(request.url.path).encode() + body
            ).hexdigest()
            try:
                previous = supervisor.store.admit_command(key, fingerprint)
            except Conflict as exc:
                return JSONResponse({"detail": str(exc)}, status_code=409)
            if previous:
                return Response(
                    previous["response"],
                    status_code=previous["http_status"],
                    media_type=previous["content_type"],
                )
        response = await call_next(request)
        if key:
            payload = b"".join([part async for part in response.body_iterator])
            supervisor.store.finish_command(
                key,
                payload.decode(),
                response.status_code,
                response.headers.get("content-type", "application/json"),
            )
            response = Response(
                payload,
                status_code=response.status_code,
                headers=dict(response.headers),
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Object not found"}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(PublicationError)
    async def publication_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/capabilities")
    def capabilities():
        return supervisor.capabilities()

    @app.get("/api/campaigns")
    def campaigns():
        return supervisor.store.campaigns()

    @app.post("/api/campaigns", status_code=201)
    def create(spec: CampaignCreate):
        return supervisor.create(spec)

    @app.get("/api/campaigns/{cid}")
    def detail(cid: str):
        return supervisor.detail(cid)

    @app.put("/api/campaigns/{cid}/protocol")
    def protocol(cid: str, payload: ProtocolUpdate):
        return supervisor.set_protocol(cid, payload.protocol)

    @app.post("/api/campaigns/{cid}/assess")
    def assess(cid: str):
        return supervisor.assess(cid)

    @app.post("/api/campaigns/{cid}/prepare")
    def prepare(cid: str):
        return supervisor.prepare(cid)

    @app.post("/api/campaigns/{cid}/profile")
    def profile(cid: str):
        return supervisor.profile(cid)

    @app.post("/api/campaigns/{cid}/approve")
    def approve(cid: str, payload: Approval):
        return supervisor.approve(
            cid,
            payload.protocol_hash,
            authority=payload.authority,
            rationale=payload.rationale,
        )

    @app.post("/api/campaigns/{cid}/run")
    def run(cid: str):
        return supervisor.run(cid)

    @app.post("/api/campaigns/{cid}/pause")
    def pause(cid: str):
        return supervisor.pause(cid)

    @app.post("/api/campaigns/{cid}/stop")
    def stop(cid: str):
        return supervisor.stop(cid)

    @app.post("/api/campaigns/{cid}/resume")
    def resume(cid: str):
        return supervisor.resume(cid)

    @app.post("/api/campaigns/{cid}/reconcile")
    def reconcile(cid: str):
        return supervisor.reconcile(cid)

    @app.post("/api/campaigns/{cid}/experiments", status_code=201)
    def experiment(cid: str, payload: ExperimentCreate):
        supervisor.add_experiment(cid, payload)
        return supervisor.detail(cid)

    @app.get("/api/campaigns/{cid}/events")
    def events(cid: str, after: int = 0):
        return supervisor.store.events(cid, after)

    @app.get("/api/campaigns/{cid}/report", response_class=PlainTextResponse)
    def report(cid: str):
        return supervisor.report(cid)

    @app.get("/api/campaigns/{cid}/reproduce")
    def reproduce(cid: str, experiment_id: str | None = None):
        return supervisor.reproduction(cid, experiment_id)

    @app.get("/api/campaigns/{cid}/draft")
    def draft(cid: str, experiment_id: str):
        return supervisor.draft(cid, experiment_id)

    @app.post("/api/campaigns/{cid}/publish")
    def publish(cid: str, payload: PublishRequest):
        return supervisor.publish(cid, payload)

    @app.get("/api/campaigns/{cid}/export")
    def export(cid: str):
        return Response(
            supervisor.export(cid),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="campaign-{cid}.zip"'
            },
        )

    @app.get("/api/campaigns/{cid}/artifacts/{artifact_path:path}")
    def artifact(cid: str, artifact_path: str):
        supervisor.store.get(cid)
        root = (supervisor.root / cid / "artifacts").resolve()
        path = (root / artifact_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "Artifact not found")
        return FileResponse(
            path, filename=path.name, media_type="application/octet-stream"
        )

    # Installed wheels carry the UI; editable installs retain the source build.
    # An explicit override remains authoritative, including a missing directory.
    bundled_frontend = Path(__file__).resolve().parent / "_web"
    source_frontend = Path(__file__).resolve().parents[2] / "web" / "dist"
    frontend = Path(
        os.environ.get(
            "AUTORESEARCH_WEB_DIST",
            str(bundled_frontend if bundled_frontend.is_dir() else source_frontend),
        )
    )
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="ui")
    else:

        @app.get("/")
        def index():
            return {
                "product": "Autoresearch",
                "ui": "Run npm ci && npm run build in web/",
                "api": "/docs",
            }

    return app
