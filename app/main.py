"""FastAPI service: POST /ask, GET /health."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .agent import Agent
from .llm import ExtractiveBaseline, GeminiLLM
from .retriever import Retriever

CORPUS = os.environ.get("CORPUS_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "sections.json"))


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="A question about Kubernetes.")
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class Source(BaseModel):
    id: str
    url: str
    title: str
    heading: str


class AskResponse(BaseModel):
    answer: str
    refused: bool
    category: str = ""
    citations: list[str] = []
    sources: list[Source] = []
    steps: int = 0


def build_agent():
    backend = os.environ.get("AGENT_BACKEND", "extractive")
    llm = GeminiLLM() if backend == "gemini" else ExtractiveBaseline()
    return Agent(Retriever.from_file(CORPUS), llm)


def create_app(agent=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.agent = agent or build_agent()
        yield

    app = FastAPI(title="Kubernetes docs agent", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        a = app.state.agent
        return {"status": "ok", "backend": a.llm.name, "sections": len(a.retriever.sections)}

    @app.post("/ask", response_model=AskResponse)
    def ask(req: AskRequest):
        r = app.state.agent.ask(req.question, req.session_id)
        return AskResponse(answer=r.answer, refused=r.refused, category=r.category, citations=r.citations,
                           sources=r.sources, steps=r.steps)

    return app


app = create_app()
