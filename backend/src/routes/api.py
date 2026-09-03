#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend.src.routes.api - Camada HTTP (controller): valida requests e chama
backend.src.services. Nenhuma logica de negocio mora aqui."""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

import pdf_to_md
from backend.src.services import jobs, motor_pool

router = APIRouter()

# Superficie web = entrada de terceiro, ao contrario da CLI. Sem teto, um
# unico upload grande o bastante estoura a memoria do processo, e um lote
# grande o bastante ocupa a thread worker unica por muito tempo. Documentado
# em dependencies.md. TAREFA-2 (rodada 3): manuais/documentacao de fornecedor
# reais chegam a centenas de MB - 100 MiB rejeitava o caso de uso central;
# 500 MB e o valor definido pelo usuario (grava direto em disco em blocos
# desde a TAREFA-1, entao o teto nao vira estouro de memoria).
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(500 * 1000 * 1000)))
MAX_UPLOAD_ARQUIVOS = int(os.getenv("MAX_UPLOAD_FILES", "50"))
_TAMANHO_BLOCO = 1024 * 1024

# TAREFA-2 (rodada 3): a estimativa de duracao (Job.to_dict()) e sempre
# exibida na linha do job como informacao neutra; o AVISO visivel (banner)
# so aparece quando a estimativa de ALGUM job na fila ultrapassa este
# limiar - um limiar baixo vira ruido quando quase todo documento real (1000+
# paginas) o ultrapassa, e aviso que aparece sempre e aviso que ninguem le.
AVISO_ESTIMATIVA_MINUTOS = int(os.getenv("AVISO_ESTIMATIVA_MINUTOS", "30"))


def _nome_saida_seguro(nome_original: str, job_id: str) -> str:
    """Deriva um nome de arquivo de saida seguro a partir de nome_original
    (vindo do cliente, nunca sanitizado antes). `Path(...).name` ja neutraliza
    travessia POSIX (`../../etc/passwd.pdf` -> `passwd.md`), mas o servidor
    roda em Linux e pathlib nao trata `\\` como separador - um cliente
    Windows pode enviar `..\\..\\evil.pdf`, que passaria intacto para o
    arcname do zip / Content-Disposition e escaparia do diretorio de extracao
    em extratores que tratam `\\` como separador (zip-slip).

    Nao usa Path.with_suffix(): em nomes so-de-pontos ou vazios apos a
    normalizacao (`Path("").with_suffix(...)`, `Path(".").with_suffix(...)`)
    ele levanta ValueError, o que derrubaria a rota com 500 em vez de cair
    no fallback `{job_id[:8]}.md" abaixo - a troca de extensao e feita via
    string, sem tocar em with_suffix.
    """
    base = Path(nome_original.replace("\\", "/")).name
    if "." in base:
        base = base.rsplit(".", 1)[0]
    nome = f"{base}.md".lstrip(".").strip()
    return nome or f"{job_id[:8]}.md"


async def _gravar_upload_com_teto(arquivo: UploadFile, teto: int) -> Path | None:
    """Grava o upload direto em disco, em blocos de _TAMANHO_BLOCO, sem
    NUNCA materializar o arquivo inteiro em memoria (nem em bytes, nem numa
    lista de blocos) - com o teto elevado a centenas de MB (TAREFA-2), a
    materializacao antiga escalava com o tamanho do arquivo, nao com o
    numero de uploads simultaneos. O teto e imposto DURANTE a escrita:
    ultrapassou, aborta e remove o arquivo parcial - nunca fica residuo em
    DIR_UPLOADS. Devolve o caminho gravado, ou None se excedeu o teto."""
    _job_id, caminho = jobs.alocar_caminho_pdf()
    total = 0
    with caminho.open("wb") as destino:
        while True:
            bloco = await arquivo.read(_TAMANHO_BLOCO)
            if not bloco:
                break
            total += len(bloco)
            if total > teto:
                destino.close()
                caminho.unlink(missing_ok=True)
                return None
            destino.write(bloco)
    return caminho


@router.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": pdf_to_md.__version__}


@router.get("/api/motor")
def motor() -> dict:
    try:
        return {"engine": motor_pool.obter_motor().nome}
    except RuntimeError as exc:
        # motor_pool.obter_motor() levanta RuntimeError se inicializar()
        # ainda nao rodou (nao deveria acontecer com o lifespan normal do
        # app, mas e um erro diagnosticavel do cliente, nao uma falha
        # interna inesperada - 503 (servico ainda nao pronto), nao 500.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/jobs")
async def criar_jobs(
    files: list[UploadFile] = File(...),
    modo_ocr: str = Form("automatico"),
) -> dict:
    """Cria um job por PDF valido e enfileira para o worker processar.

    A resposta volta assim que os arquivos sao gravados em disco - o
    processamento em si roda em background na thread worker unica.

    modo_ocr (TAREFA-3, rodada 3): "automatico" (detecta camada de texto
    por arquivo), "sempre" ou "nunca" (forca, ignorando a deteccao) -
    aplicado ao LOTE inteiro deste upload. Um valor desconhecido cai pro
    padrao "automatico" em vez de rejeitar a requisicao - client antigo
    que nao manda o campo continua funcionando como antes.
    """
    if modo_ocr not in jobs.MODOS_OCR:
        modo_ocr = "automatico"
    criados = []
    rejeitados = []
    for arquivo in files:
        nome = arquivo.filename or "arquivo.pdf"
        if Path(nome).suffix.lower() not in pdf_to_md.SUFIXOS_PDF:
            rejeitados.append({"nome_original": nome, "motivo": "nao e um PDF"})
            continue
        if len(criados) >= MAX_UPLOAD_ARQUIVOS:
            rejeitados.append({
                "nome_original": nome,
                "motivo": f"lote excede o limite de {MAX_UPLOAD_ARQUIVOS} arquivos",
            })
            continue
        caminho_pdf = await _gravar_upload_com_teto(arquivo, MAX_UPLOAD_BYTES)
        if caminho_pdf is None:
            rejeitados.append({
                "nome_original": nome,
                "motivo": f"excede o limite de {MAX_UPLOAD_BYTES} bytes",
            })
            continue
        job = jobs.criar_job(nome, caminho_pdf, modo_ocr=modo_ocr)
        jobs.enfileirar(job.id)
        criados.append(job.to_dict())
    return {"criados": criados, "rejeitados": rejeitados}


@router.get("/api/jobs")
def listar_jobs() -> dict:
    return {
        "jobs": jobs.listar_com_progresso(),
        "aviso_estimativa_minutos": AVISO_ESTIMATIVA_MINUTOS,
    }


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
    nome_saida = _nome_saida_seguro(job.nome_original, job.id)
    return FileResponse(job.caminho_saida, media_type="text/markdown", filename=nome_saida)


@router.delete("/api/jobs/{job_id}")
def remover_job(job_id: str) -> dict:
    # Checagem de status + remocao acontecem atomicamente dentro de
    # remover_se_nao_processando() (ver JobStore) - evita o worker comecar a
    # processar o job entre um check aqui e uma remocao separada depois.
    if jobs.remover_se_nao_processando(job_id) is not None:
        return {"removido": True}

    atual = jobs.obter_store().obter(job_id)
    if atual is None:
        raise HTTPException(status_code=404, detail="job nao encontrado")
    raise HTTPException(
        status_code=409, detail="job em processamento nao pode ser removido"
    )


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

    # SpooledTemporaryFile em vez de BytesIO: acima de 10 MiB, transborda pra
    # disco em vez de dobrar o footprint de memoria do processo com um zip
    # grande (a resposta ainda nao e streaming de verdade - o zip inteiro e
    # montado antes de comecar a responder - mas o consumo de RAM fica limitado).
    buffer = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)
    usados: set[str] = set()
    algum_gravado = False
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in concluidos:
            nome = _nome_saida_seguro(job.nome_original, job.id)
            if nome in usados:
                nome = f"{job.id[:8]}-{nome}"
            usados.add(nome)
            try:
                zf.write(job.caminho_saida, arcname=nome)
            except OSError:
                # arquivo sumiu entre jobs_concluidos() e aqui (ex.: DELETE
                # concorrente) - pula em vez de derrubar o download inteiro.
                continue
            algum_gravado = True
    if not algum_gravado:
        raise HTTPException(
            status_code=404,
            detail="nenhum arquivo disponivel para baixar (removidos durante a preparacao do zip)",
        )
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="conversoes.zip"'},
    )
