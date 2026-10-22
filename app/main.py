from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import Agent, agent_from_env
from .liara_client import LiaraAPIError

app = FastAPI(title="Liara Copilot")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

_agent: Agent | None = None


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = agent_from_env()
    return _agent


class ChatIn(BaseModel):
    message: str
    app: str | None = None


class ConfirmIn(BaseModel):
    action_id: str
    approve: bool


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/chat")
def chat(payload: ChatIn):
    try:
        resp = get_agent().handle(payload.message, app_hint=payload.app)
    except LiaraAPIError as e:
        return {"reply": e.message, "cards": [], "pending_action": None}
    return {"reply": resp.reply, "cards": resp.cards, "pending_action": resp.pending_action}


@app.post("/api/confirm")
def confirm(payload: ConfirmIn):
    try:
        resp = get_agent().confirm(payload.action_id, payload.approve)
    except LiaraAPIError as e:
        return {"reply": e.message, "cards": []}
    return {"reply": resp.reply, "cards": resp.cards}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
