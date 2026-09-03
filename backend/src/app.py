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

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import pdf_to_md
from backend.src.routes.api import router
from backend.src.services import jobs, motor_pool

DIR_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

LOG = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seleciona o motor UMA vez para o processo inteiro (ver motor_pool).
    # pypdfium2 e dependencia obrigatoria (pyproject.toml) e o extra `web`
    # traz `simples` de brinde, entao isso nao deveria faltar numa instalacao
    # normal - mas `pip install --no-deps`, um ambiente quebrado ou uma
    # remocao manual ainda derrubam o startup aqui. Continua sendo fail-fast
    # (nao sobe degradado sem motor algum), so com uma mensagem acionavel em
    # vez de so o traceback puro do ErroConversao.
    try:
        motor_pool.inicializar()
    except pdf_to_md.ErroConversao:
        LOG.error(
            "Nenhum motor de conversao disponivel; instale com "
            "`pip install '.[docling]'` ou `pip install '.[simples]'`."
        )
        raise
    # Retoma jobs concluidos de uma execucao anterior (rodada 5, TAREFA-4)
    # ANTES de abrir o worker/aceitar requisicoes - senao uma corrida rara
    # entre um upload novo e a varredura poderia, em teoria, colidir num
    # job_id (uuid4, praticamente impossivel na pratica, mas a ordem aqui e
    # de graca).
    jobs.retomar_jobs_do_disco()
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
