"""order-worker: consumidor SQS + endpoint /health (sem rotas públicas)."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import Settings
from app.db import make_engine
from app.worker import Worker

log = logging.getLogger("worker.app")


def create_app(settings: Settings | None = None, engine=None, worker: Worker | None = None, start_worker=True):
    settings = settings or Settings.from_env()
    engine = engine or make_engine(settings, "order-worker")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        w = worker or Worker(engine, settings)
        app.state.worker = w
        if start_worker:
            w.start()
        yield
        if start_worker:
            log.info("encerrando: aguardando o ciclo atual do consumidor")
            w.stop()
        engine.dispose()

    app = FastAPI(title="Setup Pronto - order-worker", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        checks = {}
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception as exc:
            checks["db"] = f"erro: {exc.__class__.__name__}"
        ok_loop, idade = app.state.worker.saudavel()
        checks["consumidor"] = "ok" if ok_loop else f"parado há {idade}s"
        ok = checks["db"] == "ok" and ok_loop
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "status": "ok" if ok else "erro",
                "service": "order-worker",
                "checks": checks,
                "mensagens_processadas": app.state.worker.processadas,
            },
        )

    return app
