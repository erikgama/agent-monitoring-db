"""Public demo entry point with access to demo data only."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .app import create_app


def create_public_demo() -> FastAPI:
    app = create_app(mode="demo")

    @app.middleware("http")
    async def hide_repository_resources(request: Request, call_next):
        if request.url.path.startswith("/api/resources/"):
            return JSONResponse(
                {"detail": "public_demo_resources_disabled"}, status_code=404
            )
        return await call_next(request)

    return app
