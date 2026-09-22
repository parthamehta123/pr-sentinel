"""Human queue, trace viewer and cost dashboard.

Deliberately small and server-rendered. This exists so that the three questions
the design promised to answer have somewhere to be answered: what is waiting for
a human, what did this review actually do, and what is it costing.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import get_settings
from ..db import pool
from ..db.repositories import metrics
from ..db.repositories import reviews as review_repo
from ..logging import configure_logging

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(get_settings().log_level)
    await pool.get_pool()
    yield
    await pool.close_pool()


app = FastAPI(title="pr-sentinel dashboard", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "queue": await metrics.open_hitl(50),
            "costs": await metrics.cost_by_agent(24),
            "spend_today": await metrics.spend_today_usd(),
            "stats": await metrics.escalation_rate(24),
            "latency": await metrics.latency_snapshot(),
            "caps": get_settings(),
        },
    )


@app.get("/review/{review_id}", response_class=HTMLResponse)
async def review_detail(request: Request, review_id: uuid.UUID) -> HTMLResponse:
    findings = await pool.fetch(
        "SELECT * FROM findings WHERE review_id = $1 AND merged_into IS NULL "
        "ORDER BY severity, confidence DESC",
        review_id,
    )
    review = await pool.fetchrow("SELECT * FROM reviews WHERE id = $1", review_id)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="review.html",
        context={
            "review": dict(review) if review else None,
            "findings": [dict(f) for f in findings],
            "trace": await metrics.trace(review_id),
            "review_id": review_id,
        },
    )


@app.post("/hitl/{item_id}/decide")
async def decide(
    item_id: uuid.UUID,
    verdict: str = Form(...),
    actor: str = Form("operator"),
    note: str = Form(""),
) -> RedirectResponse:
    state = "approved" if verdict == "accepted" else "rejected"
    row = await pool.fetchrow(
        "UPDATE hitl_items SET state = $2, decided_by = $3, decided_at = now(), note = $4 "
        "WHERE id = $1 AND state = 'open' RETURNING review_id",
        item_id,
        state,
        actor,
        note,
    )
    if row:
        # Every human decision is feedback. Nothing acts on a single one — the
        # minimum-evidence rule in ADR-0007 is what stops one grumpy afternoon
        # from retraining the reviewer.
        await review_repo.record_feedback(row["review_id"], None, "hitl_gate", verdict, actor, note or None)
    return RedirectResponse("/", status_code=303)
