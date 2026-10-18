"""DocPilot API. Health and questions are public. Document management lives under /admin."""

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from docpilot.admin.auth import LoginGuard
from docpilot.admin.bootstrap import AdminLogin, build_login_store
from docpilot.admin.routes import router as admin_router
from docpilot.agent.routes import router as agent_router
from docpilot.agent.service import Pilot, build_pilot
from docpilot.analytics import bind_measurement_id, reset_measurement_id
from docpilot.ask.limits import DemoLimits
from docpilot.ask.routes import router as ask_router
from docpilot.ask.service import Asker, build_chat
from docpilot.config import Settings
from docpilot.documents.service import DocumentService
from docpilot.documents.store import DocumentStore, build_store
from docpilot.ingest import build_ingestor
from docpilot.observe import configure_logging, configure_tracing
from docpilot.retrieve.retriever import build_retriever


def _watch_index(app: FastAPI) -> None:
    if getattr(app.state, "index_watch_started", False):
        return
    app.state.index_watch_started = True
    stop = threading.Event()
    app.state.index_stop = stop
    thread = threading.Thread(
        target=app.state.documents.watch_incomplete,
        args=(stop,),
        name="docpilot-index",
        daemon=True,
    )
    app.state.index_thread = thread
    thread.start()


def create_app(
    settings: Settings | None = None,
    store: DocumentStore | None = None,
    asker: Asker | None = None,
    pilot: Pilot | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    configure_logging()
    configure_tracing(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _watch_index(app)
        try:
            yield
        finally:
            stop = getattr(app.state, "index_stop", None)
            thread = getattr(app.state, "index_thread", None)
            if isinstance(stop, threading.Event):
                stop.set()
            if isinstance(thread, threading.Thread):
                thread.join(timeout=2)

    app = FastAPI(title="DocPilot", lifespan=lifespan)
    login = AdminLogin(build_login_store(resolved), resolved)
    app.state.admin_login = login
    app.state.settings = login.settings
    app.state.documents = DocumentService(
        store or build_store(resolved),
        resolved,
        ingestor=build_ingestor(resolved),
    )
    app.state.login_guard = LoginGuard(
        max_failures=resolved.login_max_failures,
        window_seconds=resolved.login_window_seconds,
        lock_seconds=resolved.login_lock_seconds,
    )
    app.state.limits = DemoLimits(
        max_question_chars=resolved.max_question_chars,
        rate_limit=resolved.ask_rate_limit,
        window_seconds=resolved.ask_rate_window_seconds,
        daily_token_cap=resolved.daily_token_cap,
    )
    resolved_asker = asker or Asker(build_retriever(resolved), build_chat(resolved))
    app.state.asker = resolved_asker
    app.state.pilot = pilot or build_pilot(resolved, resolved_asker.retriever)

    @app.middleware("http")
    async def bind_google_analytics(request: Request, call_next):  # type: ignore[no-untyped-def]
        token = bind_measurement_id(request.app.state.settings.ga_measurement_id)
        try:
            return await call_next(request)
        finally:
            reset_measurement_id(token)

    app.include_router(ask_router)
    app.include_router(agent_router)
    app.include_router(admin_router)
    assets = Path("assets")
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
