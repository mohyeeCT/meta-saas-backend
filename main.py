from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import meta, jobs, settings

app = FastAPI(
    title="Meta Copy Production API",
    description="Generate title tags, meta descriptions, and H1s at scale",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://copypilot.app",
        "https://meta.copypilot.app",
        "https://meta-saas-frontend.vercel.app",
    ],
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
