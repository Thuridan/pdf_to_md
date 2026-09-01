#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend.src.routes.api - Camada HTTP (controller): valida requests e chama
backend.src.services. Nenhuma logica de negocio mora aqui."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

import pdf_to_md
from backend.src.services import jobs, motor_pool

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": pdf_to_md.__version__}


@router.get("/api/motor")
def motor() -> dict:
    return {"engine": motor_pool.obter_motor().nome}


@router.post("/api/jobs")
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


@router.get("/api/jobs")
def listar_jobs() -> dict:
    return {"jobs": jobs.listar_com_progresso()}


@router.get("/api/jobs/{job_id}")
def obter_job(job_id: str) -> dict:
    dados = jobs.obter_com_progresso(job_id)
    if dados is None:
        raise HTTPException(status_code=404, detail="job nao encontrado")
    return dados


@router.get("/api/jobs/{job_id}/download")
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


@router.delete("/api/jobs/{job_id}")
def remover_job(job_id: str) -> dict:
    job = jobs.obter_store().obter(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job nao encontrado")
    if job.status == "processando":
        raise HTTPException(
            status_code=409, detail="job em processamento nao pode ser removido"
        )
    jobs.remover(job_id)
    return {"removido": True}


@router.delete("/api/jobs")
def limpar_jobs_finalizados() -> dict:
    """Remove todos os jobs 'concluido'/'erro' (e seus arquivos) - botao
    'Limpar finalizados' da UI. Jobs 'na_fila'/'processando' nao sao afetados."""
    return {"removidos": jobs.limpar_finalizados()}


@router.get("/api/download-zip")
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
