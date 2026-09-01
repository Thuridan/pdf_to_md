#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""webapp.main - App FastAPI da v3.0, construido sobre pdf_to_md.py.

Uso rapido:
    uvicorn webapp.main:app --reload
    curl http://127.0.0.1:8000/api/health
    curl http://127.0.0.1:8000/api/motor
    curl http://127.0.0.1:8000/api/jobs
    curl -OJ http://127.0.0.1:8000/api/jobs/{id}/download
    curl -OJ http://127.0.0.1:8000/api/download-zip
"""

from __future__ import annotations

import io
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import pdf_to_md
from webapp import jobs, motor_pool


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


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": pdf_to_md.__version__}


@app.get("/api/motor")
def motor() -> dict:
    return {"engine": motor_pool.obter_motor().nome}


@app.post("/api/jobs")
async def criar_jobs(files: list[UploadFile] = File(...)) -> dict:
    """Cria um job por PDF valido e enfileira para o worker processar.

    A resposta volta assim que os arquivos sao gravados em disco - o
    processamento em si roda em background na thread worker unica.
    """
    criados = []
    rejeitados = []
    for arquivo in files:
        nome = arquivo.filename or "arquivo.pdf"
        if Path(nome).suffix.lower() not in pdf_to_md.SUFIXOS_PDF:
            rejeitados.append({"nome_original": nome, "motivo": "nao e um PDF"})
            continue
        conteudo = await arquivo.read()
        job = jobs.criar_job(nome, conteudo)
        jobs.enfileirar(job.id)
        criados.append(job.to_dict())
    return {"criados": criados, "rejeitados": rejeitados}


@app.get("/api/jobs")
def listar_jobs() -> dict:
    return {"jobs": jobs.listar_com_progresso()}


@app.get("/api/jobs/{job_id}")
def obter_job(job_id: str) -> dict:
    dados = jobs.obter_com_progresso(job_id)
    if dados is None:
        raise HTTPException(status_code=404, detail="job nao encontrado")
    return dados


@app.get("/api/jobs/{job_id}/download")
def baixar_job(job_id: str) -> FileResponse:
    job = jobs.obter_store().obter(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job nao encontrado")
    if job.status != "concluido" or job.caminho_saida is None:
        raise HTTPException(
            status_code=409,
            detail=f"job ainda nao concluido (status atual: {job.status})",
        )
    nome_saida = Path(job.nome_original).with_suffix(".md").name
    return FileResponse(job.caminho_saida, media_type="text/markdown", filename=nome_saida)


@app.get("/api/download-zip")
def baixar_zip(ids: str | None = None) -> StreamingResponse:
    """Zip em memoria com os .md dos jobs concluidos. Sem `ids`, pega todos
    os concluidos (botao "Baixar tudo"); com `ids` (separados por virgula),
    so os concluidos entre os informados."""
    lista_ids = [i for i in ids.split(",") if i] if ids else None
    concluidos = jobs.jobs_concluidos(lista_ids)
    if not concluidos:
        raise HTTPException(status_code=404, detail="nenhum job concluido para baixar")

    buffer = io.BytesIO()
    usados: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in concluidos:
            nome = Path(job.nome_original).with_suffix(".md").name
            if nome in usados:
                nome = f"{job.id[:8]}-{nome}"
            usados.add(nome)
            zf.write(job.caminho_saida, arcname=nome)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="conversoes.zip"'},
    )


# Montado por ultimo: rotas /api/* acima ja capturam esses caminhos primeiro,
# entao o mount so serve o que sobra (index.html, app.js, app.css).
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
