"""Health endpoint used by the container and the first deploy.

The RAG and agent routes land in later stories. This module is enough for the
image to start and for CI to build it.
"""

from fastapi import FastAPI

app = FastAPI(title="DocPilot")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
