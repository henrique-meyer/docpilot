"""DocPilot API. Health and questions are public. Document management lives under /admin."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from docpilot.admin.auth import LoginGuard
from docpilot.admin.routes import router as admin_router
from docpilot.agent.routes import router as agent_router
from docpilot.agent.service import Pilot, build_pilot
from docpilot.ask.limits import DemoLimits
from docpilot.ask.routes import router as ask_router
from docpilot.ask.service import Asker, build_chat
from docpilot.config import Settings
from docpilot.documents.service import DocumentService
from docpilot.documents.store import DocumentStore, build_store
from docpilot.ingest import build_ingestor
from docpilot.retrieve.retriever import build_retriever


def create_app(
    settings: Settings | None = None,
    store: DocumentStore | None = None,
    asker: Asker | None = None,
    pilot: Pilot | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    app = FastAPI(title="DocPilot")
    app.state.settings = resolved
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
