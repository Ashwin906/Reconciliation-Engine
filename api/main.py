"""
Runnable entrypoint: `uvicorn api.main:app --reload`.

Starts EMPTY -- no seeded merchant, no placeholder dataset, no fake
numbers anywhere. The dashboard has nothing to show until someone runs a
simulation (POST /simulate/{slug}, or the "Watch it happen" screen),
which then becomes the application's real state.

That is deliberate. A seeded demo dataset would sit alongside the
simulator showing numbers that merely RESEMBLED it, and a viewer would
have no way to tell which screen reflected which run. With no seed, every
figure on every screen is traceable to a scenario the viewer chose and
watched execute -- there is no third source of numbers to be confused
about.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agent.client import GroqClient
from api.routes import AppState, create_app
from detect.rules import DetectConfig
from pipeline.run import PipelineConfig
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

RATE_CARD = {"upi": 0, "card": 200, "amex": 300}


def _empty_state() -> AppState:
    # T4 investigation only runs if a real key is present. Without one,
    # llm_client stays None and every residue item honestly stays
    # unresolved -- DECISIONS.md D-08: an unconfigured LLM degrades to
    # more honest residue, never to an unsafe fallback that pretends
    # investigation happened. GroqClient() with no transport argument
    # defaults to RequestsLLMTransport, which reads GROQ_API_KEY from
    # the environment itself (agent/client.py) -- constructed here only
    # when the key actually exists, so a missing key never produces a
    # client that would just raise on its first real call.
    llm_client = GroqClient() if os.environ.get("GROQ_API_KEY") else None

    return AppState(
        pipeline_cfg=PipelineConfig(detect_cfg=DetectConfig(rate_card_bps=RATE_CARD)),
        llm_client=llm_client,
    )


app = create_app(_empty_state())

# Mount frontend if web/dist exists
_BASE_DIR = Path(__file__).resolve().parent.parent
_DIST_DIR = _BASE_DIR / "web" / "dist"

if _DIST_DIR.exists():
    _assets_dir = _DIST_DIR / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.get("/landing", include_in_schema=False)
    @app.get("/landing.html", include_in_schema=False)
    async def serve_landing():
        landing_file = _DIST_DIR / "landing.html"
        if landing_file.exists():
            return FileResponse(str(landing_file))
        root_landing = _BASE_DIR / "landing.html"
        if root_landing.exists():
            return FileResponse(str(root_landing))
        return FileResponse(str(_DIST_DIR / "index.html"))

    @app.get("/pitch", include_in_schema=False)
    @app.get("/pitch-script.html", include_in_schema=False)
    async def serve_pitch():
        pitch_file = _DIST_DIR / "pitch-script.html"
        if pitch_file.exists():
            return FileResponse(str(pitch_file))
        root_pitch = _BASE_DIR / "pitch-script.html"
        if root_pitch.exists():
            return FileResponse(str(root_pitch))
        return FileResponse(str(_DIST_DIR / "index.html"))

    @app.get("/video", include_in_schema=False)
    @app.get("/intro-video.html", include_in_schema=False)
    async def serve_video():
        video_file = _DIST_DIR / "intro-video.html"
        if video_file.exists():
            return FileResponse(str(video_file))
        root_video = _BASE_DIR / "intro-video.html"
        if root_video.exists():
            return FileResponse(str(root_video))
        return FileResponse(str(_DIST_DIR / "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        file_path = _DIST_DIR / full_path
        if full_path and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_DIST_DIR / "index.html"))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=False)

