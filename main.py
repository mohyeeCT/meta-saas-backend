import re

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routers import meta, jobs, settings

_ALLOWED_ORIGINS = {
    "https://copypilot.app",
    "https://meta.copypilot.app",
    "https://meta-saas-frontend.vercel.app",
}
_ALLOWED_ORIGIN_RE = re.compile(
    r"https://copypilot-platform(?:-[a-z0-9-]+)?-mohyeects-projects\.vercel\.app"
)


def _is_allowed_origin(origin: str | None) -> bool:
    return bool(origin and (origin in _ALLOWED_ORIGINS or _ALLOWED_ORIGIN_RE.fullmatch(origin)))


app = FastAPI(
    title="Meta Copy Production API",
    description="Generate title tags, meta descriptions, and H1s at scale",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_ALLOWED_ORIGINS),
    allow_origin_regex=_ALLOWED_ORIGIN_RE.pattern,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router, prefix="/api/meta", tags=["meta"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    """Inject safe CORS headers on unhandled 500s for known CopyPilot origins."""
    headers = {}
    origin = request.headers.get("origin")
    if _is_allowed_origin(origin):
        headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
        }
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=headers,
    )
