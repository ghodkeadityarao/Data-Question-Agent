"""FastAPI service: the chat page, POST /ask, GET /schema, GET /health."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent
from .limits import DailyBudget, RateLimiter
from .llm import GeminiLLM, NoModel
from .sqlguard import schema_text

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "..", "data", "store.db"))
STATIC = os.path.join(HERE, "..", "static")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


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


def client_key(request, trust_proxy):
    """The visitor's address. Behind one trusted proxy, the last X-Forwarded-For entry is the one the proxy added."""
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def create_app(agent=None, limiter=None, budget=None, trust_proxy=None):
    trust_proxy = os.environ.get("TRUST_PROXY") == "1" if trust_proxy is None else trust_proxy
    limiter = limiter or RateLimiter(int(os.environ.get("REQUESTS_PER_MINUTE", 15)))
    budget = budget or DailyBudget(int(os.environ.get("QUESTIONS_PER_DAY", 1000)))

    @asynccontextmanager
    async def lifespan(app):
        app.state.agent = agent or build_agent()
        yield

    app = FastAPI(title="Data question agent", version="0.2.0", lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/", include_in_schema=False)
    def page():
        return FileResponse(os.path.join(STATIC, "index.html"))

    @app.get("/health")
    def health():
        return {"status": "ok", "backend": app.state.agent.llm.name}

    @app.get("/schema")
    def schema():
        return {"tables": [line for line in schema_text(DB_PATH).splitlines()]}

    @app.post("/ask", response_model=AskResponse)
    def ask(req: AskRequest, request: Request):
        if not limiter.allow(client_key(request, trust_proxy)):
            raise HTTPException(429, "Too many questions from your address. Please wait a minute.")
        if not budget.take():
            raise HTTPException(429, "Today's question budget has been used up. Please try again tomorrow.")
        r = app.state.agent.ask(req.question, req.session_id)
        return AskResponse(answer=r.answer, refused=r.refused, category=r.category, sql=r.sql, columns=r.columns,
                           rows=r.rows, truncated=r.truncated, steps=r.steps, queries_run=r.queries_run,
                           queries_blocked=r.queries_blocked)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
