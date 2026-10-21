"""FastAPI application entry point."""

from fastapi import FastAPI

app = FastAPI(title="Flaky Test Tracker", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check for the load balancer and uptime monitoring."""
    return {"status": "ok"}
