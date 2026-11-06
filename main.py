"""Entry point for `uvicorn main:app` and Vercel's Python runtime.

If the app cannot start (bad env on a serverless deploy), serve a tiny app that
reports the startup error at /api/health instead of a bare 500, so the cause
is visible without digging through platform logs. Secrets are never echoed.
"""
try:
    from app.main import app  # noqa: F401
except Exception as _exc:  # pragma: no cover - exercised only on broken deploys
    import traceback

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    _tb = traceback.format_exc()[-3000:]
    app = FastAPI(title="AI-Shtat Pay (startup failed)")

    @app.get("/{path:path}")
    def _startup_error(path: str = "") -> JSONResponse:
        return JSONResponse({"ok": False, "startup_error": f"{type(_exc).__name__}: {_exc}", "traceback": _tb}, 500)
