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

from dotenv import load_dotenv

load_dotenv()

from agent.client import GroqClient
from api.routes import AppState, create_app
from detect.rules import DetectConfig
from pipeline.run import PipelineConfig

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
