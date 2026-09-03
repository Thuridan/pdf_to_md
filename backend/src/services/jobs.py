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
from dataclasses import dataclass, field, replace as _dc_replace
from datetime import datetime, timezone
from pathlib import Path

import pdf_to_md as m
from backend.src.services import motor_pool

DIR_UPLOADS = Path(__file__).resolve().parents[2] / "uploads"

STATUS_VALIDOS = {"na_fila", "processando", "concluido", "erro"}

LOG = logging.getLogger(__name__)

_SENTINEL = object()
_parar_evento = threading.Event()

# Estimativa de progresso: o Docling nao expoe callback nativo por pagina, entao
# "pagina atual" e uma projecao por tempo decorrido (elapsed / segundos_por_pagina).
# A media movel comeca com um palpite razoavel e se ajusta a cada job concluido -
# e so um proxy de UX, por isso a API sempre marca esse numero como "estimado".
#
# EMA POR MODO DE OCR (rodada 3, TAREFA-5): com OCR e sem OCR tem custo por
# pagina em ordens de grandeza diferentes (TAREFA-4 deu ao motor Docling um
# converter por modo justamente por causa dessa diferenca) - uma EMA global
# unica mistura os dois e nao converge pra nada util pra nenhum dos dois.
# Chave e o bool job.ocr (o modo EFETIVO, ja resolvido por deteccao ou
# override - nao "automatico"/"sempre"/"nunca"). As duas comecam no MESMO
# palpite inicial: nao ha medicao real desta rodada que justifique valores
# iniciais diferentes por modo sem calibrar no vazio (ver relatorio) - cada
# uma se ajusta pra sua propria realidade assim que os primeiros jobs
# daquele modo terminam.
_SEGUNDOS_POR_PAGINA_PADRAO = 2.0
_ALPHA_EMA = 0.3
_segundos_por_pagina: dict[bool, float] = {
    True: _SEGUNDOS_POR_PAGINA_PADRAO,
    False: _SEGUNDOS_POR_PAGINA_PADRAO,
}
# Quantos jobs ja alimentaram a EMA de cada modo - logo apos subir o processo
# ela vale so o palpite inicial e nao conhece a maquina; abaixo deste limiar
# a estimativa derivada dela e marcada como baixa confianca (TAREFA-2).
_AMOSTRAS_PARA_CONFIANCA = 3
_amostras_ema: dict[bool, int] = {True: 0, False: 0}


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
    # TAREFA-3: decisao de OCR efetiva para este job (detectada ou forcada
    # pelo seletor de 3 estados na UI) e sua origem, pra UI mostrar "OCR: sim
    # (detectado)" / "OCR: nao (forcado)" - auditavel quando o resultado sai
    # pior que o esperado. Ver criar_job(modo_ocr=...).
    ocr: bool = True
    ocr_origem: str = "detectado"  # "detectado" | "forcado"
    # Rodada 5, TAREFA-3: graus de confianca do Docling (None/[] antes de
    # concluir, ou se o motor nao os expoe - ver ResultadoMotor em
    # pdf_to_md.py). NAO vira erro - so um sinal pra quem for revisar.
    grau_medio: str | None = None
    paginas_grau_baixo: list[int] = field(default_factory=list)

    def to_dict(self, *, posicao_na_fila: int | None = None) -> dict:
        # EMA do MODO deste job especificamente (TAREFA-5) - OCR e sem-OCR
        # tem custo por pagina em ordens de grandeza diferentes; misturar
        # os dois numa unica media nao converge pra nada util pra nenhum.
        segundos_por_pagina = _segundos_por_pagina[self.ocr]

        pagina_estimada: float | None = None
        estimado = False

        if self.status == "processando" and self.paginas_totais and self.iniciado_em:
            decorrido = (datetime.now(timezone.utc) - self.iniciado_em).total_seconds()
            pagina_estimada = min(
                float(self.paginas_totais), decorrido / segundos_por_pagina
            )
            estimado = True
        elif self.status == "concluido" and self.paginas_totais:
            pagina_estimada = float(self.paginas_totais)

        # Estimativa de duracao total (TAREFA-2): informacao neutra, sempre
        # exposta quando o numero de paginas e conhecido - e o frontend quem
        # decide onde exibi-la (na_fila/processando) e se aciona o banner de
        # aviso acima do limiar configuravel. "Baixa confianca" enquanto
        # poucos jobs do MESMO MODO alimentaram a EMA - logo apos subir o
        # processo ela vale so o palpite inicial e nao conhece a maquina.
        estimativa_segundos: float | None = None
        if self.paginas_totais:
            estimativa_segundos = self.paginas_totais * segundos_por_pagina

        dados = {
            "id": self.id,
            "nome_original": self.nome_original,
            "status": self.status,
            "criado_em": self.criado_em.isoformat(),
            "paginas_totais": self.paginas_totais,
            "tamanho_bytes": self.tamanho_bytes,
            "pagina_estimada": pagina_estimada,
            "estimado": estimado,
            "estimativa_segundos": estimativa_segundos,
            "estimativa_baixa_confianca": _amostras_ema[self.ocr] < _AMOSTRAS_PARA_CONFIANCA,
            "ocr": self.ocr,
            "ocr_origem": self.ocr_origem,
            "grau_medio": self.grau_medio,
            "paginas_grau_baixo": self.paginas_grau_baixo,
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

    def remover_se_nao_processando(self, job_id: str) -> "Job | None":
        """Remove atomicamente (sob o mesmo lock) SE o job existir e nao
        estiver 'processando'. Devolve o Job removido, para o chamador apagar
        os arquivos em disco, ou None se nao encontrado ou em processamento -
        nesses dois casos nada e removido. Existe para fechar o TOCTOU entre
        checar o status e remover: checar-e-depois-remover como duas
        operacoes separadas dava ao worker uma janela para comecar a
        processar o job entre as duas, e o remover() subsequente apagava o
        PDF por baixo de uma conversao em andamento."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status == "processando":
                return None
            del self._jobs[job_id]
            return job

    def listar(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())


_store = JobStore()
_fila: "queue.Queue[object]" = queue.Queue()
_worker_thread: threading.Thread | None = None


def obter_store() -> JobStore:
    return _store


def alocar_caminho_pdf(*, diretorio: Path | None = None) -> tuple[str, Path]:
    """Gera um job_id novo e o caminho onde seu PDF deve ser gravado (criando
    o diretorio se preciso). Separado de criar_job() de proposito: quem grava
    os bytes de um upload real (ver api._gravar_upload_com_teto - direto do
    UploadFile, em blocos, sem materializar o arquivo inteiro em memoria)
    precisa do caminho ANTES de o Job existir, e so registra o Job depois que
    a escrita (e a checagem de teto) terminarem com sucesso."""
    alvo = diretorio or DIR_UPLOADS
    alvo.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    return job_id, alvo / f"{job_id}.pdf"


MODOS_OCR = {"automatico", "sempre", "nunca"}


def _decidir_ocr(caminho_pdf: Path, modo_ocr: str) -> tuple[bool, str]:
    """TAREFA-3: decide o OCR efetivo do job. 'sempre'/'nunca' forcam,
    ignorando a deteccao. 'automatico' (e qualquer valor desconhecido, pra
    nao quebrar um cliente antigo) roda tem_camada_de_texto() - na duvida
    (excecao/PDF ilegivel), o padrao e RODAR ocr: perder conteudo de um scan
    tratado como nativo e pior que gastar tempo rodando ocr num nativo."""
    if modo_ocr == "sempre":
        return True, "forcado"
    if modo_ocr == "nunca":
        return False, "forcado"
    try:
        tem_texto = m.tem_camada_de_texto(caminho_pdf)
    except m.PdfIlegivel:
        tem_texto = False
    return (not tem_texto), "detectado"


def criar_job(nome_original: str, caminho_pdf: Path, *, modo_ocr: str = "automatico") -> Job:
    """Registra um novo job 'na_fila' para um PDF JA GRAVADO em disco em
    caminho_pdf (ver alocar_caminho_pdf() para gerar job_id + caminho antes
    de escrever). O job_id e o nome do arquivo sem extensao.

    modo_ocr ("automatico"/"sempre"/"nunca", vindo do seletor de 3 estados
    da UI) decide job.ocr/job.ocr_origem - ver _decidir_ocr(). NOTA (rodada
    3, TAREFA-3): ate a TAREFA-4 (motor_pool com um converter por modo de
    OCR), MotorDocling cacheia UM converter por processo com o do_ocr do
    PRIMEIRO job processado - a decisao por job fica registrada aqui e e
    repassada a converter_arquivo() por _processar(), mas so tem efeito
    pratico completo no motor 'simples' (que nao faz OCR de qualquer jeito)
    ate a TAREFA-4 fechar o motor Docling.
    """
    job_id = caminho_pdf.stem
    tamanho_bytes = caminho_pdf.stat().st_size
    # Checagem barata (pypdfium2, sem carregar modelos) - mesma usada por --max-pages
    # na CLI, mas aqui e so para estimar progresso na UI: um PDF ilegivel pelo
    # pypdfium2 (m.PdfIlegivel) vira paginas_totais=None em vez de propagar -
    # quem decide se isso e motivo de erro e converter_arquivo(), via cfg.max_pages.
    try:
        paginas = m.contar_paginas(caminho_pdf)
    except m.PdfIlegivel:
        paginas = None
    ocr, ocr_origem = _decidir_ocr(caminho_pdf, modo_ocr)
    job = Job(
        id=job_id,
        nome_original=nome_original,
        caminho_pdf=caminho_pdf,
        paginas_totais=paginas,
        tamanho_bytes=tamanho_bytes,
        ocr=ocr,
        ocr_origem=ocr_origem,
    )
    _store.adicionar(job)
    return job


def enfileirar(job_id: str) -> None:
    """Poe o job na fila para o worker processar. Nunca bloqueia (queue.put e imediato)."""
    _fila.put(job_id)


def retomar_jobs_do_disco(*, diretorio: Path | None = None) -> tuple[int, int]:
    """Varre uploads/ no startup e reconstitui jobs 'concluido' para pares
    {job_id}.pdf + {job_id}.md ja existentes (rodada 5, TAREFA-4).

    Sem isso, o processo cair no meio de uma fila perde tudo da memoria: os
    .md ja gerados ficam orfaos no disco, sem nenhum job apontando pra eles
    - o usuario nao consegue mais baixa-los pela UI, mesmo com o arquivo
    pronto ali. So reconstitui jobs TERMINADOS (par completo); um .pdf SEM
    .md correspondente fica de fora, sem reenfileirar automaticamente - nao
    ha como saber se ele parou por falha de conversao ou por interrupcao no
    meio do processamento, e reprocessar as cegas arriscaria repetir uma
    falha ou desperdicar tempo num arquivo problematico. So conta e loga
    esses casos; quem decide o que fazer com eles e o usuario. Varredura
    por idade (limpar uploads antigos) fica pra rodada de isolamento - nao
    antecipada aqui.

    Limitacao registrada: nome_original nao e recuperavel do disco (o
    upload e gravado como {job_id}.pdf, sem metadados ao lado) - o job
    retomado usa o proprio nome de arquivo (job_id.pdf) como nome exibido.
    Mesma razao para ocr/ocr_origem: a decisao de OCR efetiva de quando o
    job rodou originalmente nao fica gravada em lugar nenhum, entao
    ocr_origem vira "desconhecido" em vez de inventar um valor (a UI trata
    esse caso suprimindo o rotulo de OCR, em vez de mostrar algo incerto
    como se fosse fato).

    Devolve (retomados, orfaos) para o chamador (lifespan do FastAPI) logar.
    """
    alvo = diretorio or DIR_UPLOADS
    if not alvo.is_dir():
        return 0, 0

    retomados = 0
    orfaos = 0
    for caminho_pdf in sorted(alvo.glob("*.pdf")):
        caminho_md = caminho_pdf.with_suffix(".md")
        if not caminho_md.is_file():
            orfaos += 1
            continue
        try:
            paginas = m.contar_paginas(caminho_pdf)
        except m.PdfIlegivel:
            paginas = None
        job = Job(
            id=caminho_pdf.stem,
            nome_original=caminho_pdf.name,
            caminho_pdf=caminho_pdf,
            status="concluido",
            caminho_saida=caminho_md,
            paginas_totais=paginas,
            tamanho_bytes=caminho_pdf.stat().st_size,
            ocr_origem="desconhecido",
        )
        _store.adicionar(job)
        retomados += 1

    if retomados or orfaos:
        LOG.info(
            "Retomada de jobs no startup: %d concluido(s) reconstituido(s) de "
            "%s, %d PDF(s) orfao(s) sem .md correspondente (nao reenfileirados).",
            retomados, alvo, orfaos,
        )
    return retomados, orfaos


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


def remover_se_nao_processando(job_id: str) -> Job | None:
    """Versao segura contra TOCTOU de remover(), para a rota publica
    DELETE /api/jobs/{id}: a checagem de status e a remocao do registro
    acontecem atomicamente sob o lock do JobStore (ver
    JobStore.remover_se_nao_processando). Devolve o Job removido (apos apagar
    seus arquivos em disco) ou None se nao encontrado ou 'processando'."""
    job = _store.remover_se_nao_processando(job_id)
    if job is not None:
        _apagar_arquivo(job.caminho_pdf)
        _apagar_arquivo(job.caminho_saida)
    return job


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
    """Ajusta a media movel de segundos/pagina (do MODO de OCR deste job -
    TAREFA-5) com o resultado de um job concluido. Muta os dicts em vez de
    reatribuir os nomes do modulo - sem necessidade de `global` aqui."""
    if not job.paginas_totais:
        return
    observado = job.segundos / job.paginas_totais
    atual = _segundos_por_pagina[job.ocr]
    _segundos_por_pagina[job.ocr] = _ALPHA_EMA * observado + (1 - _ALPHA_EMA) * atual
    _amostras_ema[job.ocr] += 1


def _processar(job_id: str) -> None:
    job = _store.obter(job_id)
    if job is None:
        return

    job.iniciado_em = datetime.now(timezone.utc)
    job.status = "processando"
    motor = motor_pool.obter_motor()
    # Config por job, nao o global de motor_pool direto: job.ocr (TAREFA-3)
    # pode divergir do padrao do processo. dataclasses.replace() copia sem
    # mutar o Config compartilhado (motor_pool.obter_config() devolve a
    # MESMA instancia pra todo mundo). Ate a TAREFA-4 dar ao MotorDocling um
    # converter por modo de OCR, isso e respeitado de verdade pelo motor
    # 'simples' (nao faz OCR de qualquer jeito); o Docling cacheia um unico
    # converter com o do_ocr do primeiro job processado no processo - ver
    # criar_job() e a TAREFA-4.
    cfg = _dc_replace(motor_pool.obter_config(), ocr=job.ocr)
    saida = job.caminho_pdf.with_suffix(".md")

    resultado = m.converter_arquivo(job.caminho_pdf, saida, motor, cfg)
    job.segundos = resultado.segundos
    job.grau_medio = resultado.grau_medio
    job.paginas_grau_baixo = resultado.paginas_grau_baixo
    if resultado.status in ("ok", "pulado"):
        # "pulado" (saida.md ja existia, overwrite desligado) nao e uma falha
        # do ponto de vista do usuario: o arquivo que ele queria ja esta la.
        # A mensagem "ja existe (use --overwrite)" so faz sentido no
        # contexto da CLI (onde --overwrite e uma flag que o usuario escolhe);
        # na web nao ha reenfileiramento hoje, mas se um dia houver, isso
        # evita reportar "erro" para um resultado que na pratica e sucesso.
        job.caminho_saida = resultado.saida
        job.status = "concluido"
        if resultado.status == "ok":
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
    # _fila.get() continua bloqueando indefinidamente (sem timeout/polling) -
    # zero custo extra enquanto a fila esta ociosa. O que muda e o que
    # acontece ANTES de processar cada item: se _parar_evento ja estiver
    # marcado, o item e descartado (nao processado) em vez de acionar o
    # motor. Isso e o que faz o shutdown nao esperar um backlog inteiro
    # drenar - o antigo _fila.put(_SENTINEL) colocava o sinal de parada no
    # FIM da queue.Queue (FIFO), entao com jobs na frente ele so era
    # alcancado depois de todos serem processados, e join(timeout=...)
    # sempre estourava com backlog grande o bastante (a thread continuava
    # viva, orfa, ainda mutando arquivos depois da funcao "retornar"). O
    # job JA em andamento no momento do shutdown ainda termina (o check so
    # acontece ANTES de processar o proximo), mas nada alem dele comeca.
    while True:
        item = _fila.get()
        try:
            if _parar_evento.is_set():
                return
            if item is _SENTINEL:
                continue
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
    _parar_evento.clear()
    _worker_thread = threading.Thread(target=_loop, name="pdf-to-md-worker", daemon=True)
    _worker_thread.start()


def parar_worker(timeout: float = 5.0) -> None:
    """Sinaliza a thread worker para parar e aguarda o termino (usado no shutdown do app).

    So marca _worker_thread como None se a thread realmente terminou dentro
    do timeout - do contrario ela continua rodando (orfa) e uma chamada
    subsequente a iniciar_worker() nao deve fingir que esta livre pra subir
    uma segunda thread consumindo a mesma fila.
    """
    global _worker_thread
    if _worker_thread is None:
        return
    _parar_evento.set()
    _fila.put(_SENTINEL)  # acorda o get() bloqueado, mesmo se a fila estava vazia
    _worker_thread.join(timeout=timeout)
    if _worker_thread.is_alive():
        return
    _worker_thread = None
    _drenar_fila_abandonada()


def _drenar_fila_abandonada() -> None:
    """Descarta qualquer item deixado na fila apos um shutdown que abandonou
    o backlog (ver parar_worker) - sem isso, um iniciar_worker() seguinte
    processaria job_ids de uma sessao/teste anterior, possivelmente
    apontando pra arquivos que ja nao existem mais em disco."""
    while True:
        try:
            _fila.get_nowait()
        except queue.Empty:
            return
        else:
            _fila.task_done()
