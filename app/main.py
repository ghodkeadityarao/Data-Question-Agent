"""FastAPI service: POST /ask, GET /health."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .agent import Agent
from .llm import GeminiLLM, NoModel
from .sqlguard import schema_text

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "store.db"))


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="A question about the store data.")
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class AskResponse(BaseModel):
    answer: str
    refused: bool
    category: str = ""
    sql: str = ""
    columns: list[str] = []
    rows: list[list] = []
    truncated: bool = False
    steps: int = 0
    queries_run: int = 0
    queries_blocked: int = 0


def build_agent():
    llm = GeminiLLM(schema_text(DB_PATH)) if os.environ.get("GEMINI_API_KEY") else NoModel()
    return Agent(DB_PATH, llm)


def create_app(agent=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.agent = agent or build_agent()
        yield

    app = FastAPI(title="Data question agent", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "backend": app.state.agent.llm.name}

    @app.post("/ask", response_model=AskResponse)
    def ask(req: AskRequest):
        r = app.state.agent.ask(req.question, req.session_id)
        return AskResponse(answer=r.answer, refused=r.refused, category=r.category, sql=r.sql, columns=r.columns,
                           rows=r.rows, truncated=r.truncated, steps=r.steps, queries_run=r.queries_run,
                           queries_blocked=r.queries_blocked)

    return app


app = create_app()
