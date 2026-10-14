"""DocPilot API. Health is public. Document management lives under /admin."""

from fastapi import FastAPI

from docpilot.admin.auth import LoginGuard
from docpilot.admin.routes import router as admin_router
from docpilot.config import Settings
from docpilot.documents.service import DocumentService
from docpilot.documents.store import DocumentStore, build_store
from docpilot.ingest import build_ingestor


def create_app(settings: Settings | None = None, store: DocumentStore | None = None) -> FastAPI:
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
    app.include_router(admin_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
