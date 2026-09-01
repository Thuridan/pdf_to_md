#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend.src.app - App FastAPI da v3.0, construido sobre pdf_to_md.py.

Uso rapido:
    uvicorn backend.src.app:app --reload
    curl http://127.0.0.1:8000/api/health
    curl http://127.0.0.1:8000/api/motor
    curl http://127.0.0.1:8000/api/jobs
    curl -OJ http://127.0.0.1:8000/api/jobs/{id}/download
    curl -OJ http://127.0.0.1:8000/api/download-zip
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import pdf_to_md
from backend.src.routes.api import router
from backend.src.services import jobs, motor_pool

DIR_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seleciona o motor UMA vez para o processo inteiro (ver motor_pool).
    motor_pool.inicializar()
    jobs.iniciar_worker()
    try:
        yield
    finally:
        jobs.parar_worker()


app = FastAPI(title="pdf_to_md", version=pdf_to_md.__version__, lifespan=lifespan)
app.include_router(router)

# Montado por ultimo: rotas /api/* acima ja capturam esses caminhos primeiro,
# entao o mount so serve o que sobra (index.html, app.js, app.css).
app.mount("/", StaticFiles(directory=DIR_FRONTEND, html=True), name="static")
