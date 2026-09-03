#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend.src.services.jobs - Registro de jobs e fila de conversao (worker unico sequencial).

Reaproveita pdf_to_md.converter_arquivo() (checagens, escrita atomica) contra
a UNICA instancia de motor de motor_pool - mesma garantia que a CLI ja da
para um lote: um motor, processado sequencialmente. Sem Celery/Redis: o
gargalo real e a instancia de modelo compartilhada, nao "quantos jobs cabem"
(ver plano de arquitetura da v3.0).
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pdf_to_md as m
from backend.src.services import motor_pool

DIR_UPLOADS = Path(__file__).resolve().parents[2] / "uploads"

STATUS_VALIDOS = {"na_fila", "processando", "concluido", "erro"}

_SENTINEL = object()

LOG = logging.getLogger(__name__)

# Estimativa de progresso: o Docling nao expoe callback nativo por pagina, entao
# "pagina atual" e uma projecao por tempo decorrido (elapsed / segundos_por_pagina).
# A media movel comeca com um palpite razoavel e se ajusta a cada job concluido -
# e so um proxy de UX, por isso a API sempre marca esse numero como "estimado".
_SEGUNDOS_POR_PAGINA_PADRAO = 2.0
_ALPHA_EMA = 0.3
_segundos_por_pagina = _SEGUNDOS_POR_PAGINA_PADRAO


@dataclass
class Job:
    id: str
    nome_original: str
    caminho_pdf: Path
    status: str = "na_fila"
    criado_em: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    mensagem_erro: str = ""
    caminho_saida: Path | None = None
    paginas_totais: int | None = None
    tamanho_bytes: int = 0
    iniciado_em: datetime | None = None
    segundos: float = 0.0

    def to_dict(self, *, posicao_na_fila: int | None = None) -> dict:
        pagina_estimada: float | None = None
        estimado = False

        if self.status == "processando" and self.paginas_totais and self.iniciado_em:
            decorrido = (datetime.now(timezone.utc) - self.iniciado_em).total_seconds()
            pagina_estimada = min(
                float(self.paginas_totais), decorrido / _segundos_por_pagina
            )
            estimado = True
        elif self.status == "concluido" and self.paginas_totais:
            pagina_estimada = float(self.paginas_totais)

        dados = {
            "id": self.id,
            "nome_original": self.nome_original,
            "status": self.status,
            "criado_em": self.criado_em.isoformat(),
            "paginas_totais": self.paginas_totais,
            "tamanho_bytes": self.tamanho_bytes,
            "pagina_estimada": pagina_estimada,
            "estimado": estimado,
            "mensagem_erro": self.mensagem_erro or None,
        }
        if posicao_na_fila is not None:
            dados["posicao_na_fila"] = posicao_na_fila
        return dados


class JobStore:
    """Registro em memoria dos jobs. Protegido por lock: lido pela API e
    escrito pelo worker, em threads diferentes."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def adicionar(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def obter(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def remover(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def listar(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())


_store = JobStore()
_fila: "queue.Queue[object]" = queue.Queue()
_worker_thread: threading.Thread | None = None


def obter_store() -> JobStore:
    return _store


def criar_job(nome_original: str, conteudo: bytes, *, diretorio: Path | None = None) -> Job:
    """Grava o PDF em disco e registra um novo job com status 'na_fila'."""
    alvo = diretorio or DIR_UPLOADS
    alvo.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    caminho = alvo / f"{job_id}.pdf"
    caminho.write_bytes(conteudo)
    # Checagem barata (pypdfium2, sem carregar modelos) - mesma usada por --max-pages
    # na CLI. Devolve None se o PDF nao puder ser aberto; o motor real reporta o erro.
    paginas = m.contar_paginas(caminho)
    job = Job(
        id=job_id,
        nome_original=nome_original,
        caminho_pdf=caminho,
        paginas_totais=paginas,
        tamanho_bytes=len(conteudo),
    )
    _store.adicionar(job)
    return job


def enfileirar(job_id: str) -> None:
    """Poe o job na fila para o worker processar. Nunca bloqueia (queue.put e imediato)."""
    _fila.put(job_id)


def jobs_concluidos(ids: list[str] | None = None) -> list[Job]:
    """Jobs com status 'concluido' (e saida gravada), em ordem de criacao.

    Sem `ids`: todos os concluidos (usado pelo "Baixar tudo"). Com `ids`:
    so os concluidos entre os informados (ids pendentes/inexistentes sao
    ignorados silenciosamente - quem decide o que baixar e o chamador).
    """
    candidatos = _jobs_ordenados()
    if ids is not None:
        permitidos = set(ids)
        candidatos = [j for j in candidatos if j.id in permitidos]
    return [j for j in candidatos if j.status == "concluido" and j.caminho_saida is not None]


def remover(job_id: str) -> None:
    """Apaga os arquivos em disco (PDF de entrada e .md de saida, se existirem)
    e remove o job do registro. Assume que o chamador ja validou existencia e
    que o status nao e 'processando' - mesmo padrao de checagem que baixar_job
    ja faz na rota antes de servir o arquivo."""
    job = _store.obter(job_id)
    if job is None:
        return
    _apagar_arquivo(job.caminho_pdf)
    _apagar_arquivo(job.caminho_saida)
    _store.remover(job_id)


def limpar_finalizados() -> int:
    """Remove todos os jobs 'concluido' ou 'erro' (e seus arquivos). Devolve
    quantos foram removidos - usado pelo botao 'Limpar finalizados' da UI."""
    removidos = 0
    for job in _jobs_ordenados():
        if job.status in ("concluido", "erro"):
            remover(job.id)
            removidos += 1
    return removidos


def _apagar_arquivo(caminho: Path | None) -> None:
    if caminho is not None:
        caminho.unlink(missing_ok=True)


def _jobs_ordenados() -> list[Job]:
    return sorted(_store.listar(), key=lambda j: j.criado_em)


def _posicoes_na_fila() -> dict[str, int]:
    na_fila = [j for j in _jobs_ordenados() if j.status == "na_fila"]
    return {j.id: i + 1 for i, j in enumerate(na_fila)}


def listar_com_progresso() -> list[dict]:
    posicoes = _posicoes_na_fila()
    return [j.to_dict(posicao_na_fila=posicoes.get(j.id)) for j in _jobs_ordenados()]


def obter_com_progresso(job_id: str) -> dict | None:
    job = _store.obter(job_id)
    if job is None:
        return None
    return job.to_dict(posicao_na_fila=_posicoes_na_fila().get(job_id))


def _atualizar_estimativa(job: Job) -> None:
    """Ajusta a media movel de segundos/pagina com o resultado de um job concluido."""
    global _segundos_por_pagina
    if not job.paginas_totais:
        return
    observado = job.segundos / job.paginas_totais
    _segundos_por_pagina = _ALPHA_EMA * observado + (1 - _ALPHA_EMA) * _segundos_por_pagina


def _processar(job_id: str) -> None:
    job = _store.obter(job_id)
    if job is None:
        return

    job.status = "processando"
    job.iniciado_em = datetime.now(timezone.utc)
    motor = motor_pool.obter_motor()
    cfg = motor_pool.obter_config()
    saida = job.caminho_pdf.with_suffix(".md")

    resultado = m.converter_arquivo(job.caminho_pdf, saida, motor, cfg)
    job.segundos = resultado.segundos
    if resultado.status == "ok":
        job.status = "concluido"
        job.caminho_saida = resultado.saida
        _atualizar_estimativa(job)
    else:
        job.status = "erro"
        job.mensagem_erro = resultado.mensagem


def _marcar_erro(job_id: str, mensagem: str) -> None:
    job = _store.obter(job_id)
    if job is not None:
        job.status = "erro"
        job.mensagem_erro = mensagem


def _loop() -> None:
    while True:
        item = _fila.get()
        try:
            if item is _SENTINEL:
                return
            _processar(item)  # type: ignore[arg-type]
        except Exception:
            LOG.exception("Falha inesperada ao processar %r", item)
            _marcar_erro(item, "falha interna ao processar")  # type: ignore[arg-type]
        finally:
            _fila.task_done()


def iniciar_worker() -> None:
    """Inicia a thread worker unica, se ainda nao estiver rodando (idempotente)."""
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(target=_loop, name="pdf-to-md-worker", daemon=True)
    _worker_thread.start()


def parar_worker(timeout: float = 5.0) -> None:
    """Sinaliza a thread worker para parar e aguarda o termino (usado no shutdown do app)."""
    global _worker_thread
    if _worker_thread is None:
        return
    _fila.put(_SENTINEL)
    _worker_thread.join(timeout=timeout)
    _worker_thread = None
