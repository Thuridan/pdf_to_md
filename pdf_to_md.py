#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_to_md.py - Conversor de PDF para Markdown.

Motor principal: Docling (layout + TableFormer + OCR).
Motor alternativo: pypdfium2 (extracao de camada de texto, sem modelos de IA).

Uso rapido:
    python pdf_to_md.py -i documento.pdf
    python pdf_to_md.py -i ./entrada -o ./saida -r --lang pt
    python pdf_to_md.py                      # modo interativo

Codigos de saida: 0 = tudo certo | 1 = houve falha | 2 = erro de uso | 130 = interrompido
"""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

__version__ = "2.1.0"

EXIT_OK = 0
EXIT_FALHA = 1
EXIT_USO = 2
EXIT_INTERROMPIDO = 130

SUFIXOS_PDF = {".pdf"}
SUFIXOS_MD = {".md", ".markdown"}

LOG = logging.getLogger("pdf2md")

# auto | cpu | mps | xpu | cuda | cuda:N
DEVICE_VALIDO = __import__("re").compile(r"^(auto|cpu|mps|xpu|cuda(:\d+)?)$")

# Codigos de idioma por motor de OCR. RapidOCR usa ISO 639-1 e aceita apenas um
# idioma por execucao; EasyOCR usa ISO 639-1; Tesseract usa ISO 639-2 (3 letras).
_MAPA_TESSERACT = {
    "pt": "por", "en": "eng", "es": "spa", "fr": "fra",
    "de": "deu", "it": "ita", "nl": "nld",
}


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
@dataclass
class Config:
    """Parametros de conversao, desacoplados do argparse para permitir teste."""

    engine: str = "auto"            # auto | docling | simples
    ocr: bool = True
    ocr_engine: str = "rapidocr"    # rapidocr | easyocr | tesseract
    ocr_backend: str = "onnxruntime"  # so p/ rapidocr: onnxruntime|openvino|paddle|torch
    lang: list[str] = field(default_factory=lambda: ["pt"])
    tables: str = "accurate"        # accurate | fast | none
    threads: int = 4
    device: str = "auto"            # auto | cpu | cuda | cuda:N | mps | xpu
    offline: bool = False
    artifacts: Path | None = None
    timeout: float | None = None
    overwrite: bool = False
    dry_run: bool = False
    max_pages: int | None = None    # None = sem limite (comportamento original)
    jobs: int = 1                   # paralelismo de lote; so tem efeito no motor 'simples'


def aplicar_ambiente(cfg: Config) -> None:
    """Define variaveis de ambiente ANTES de qualquer import de torch/onnx.

    As bibliotecas de tensores leem essas variaveis apenas no momento do import.
    Por isso o import do Docling e feito de forma preguicosa (dentro do motor).
    """
    n = str(max(1, cfg.threads))
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[var] = n
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    if cfg.offline:
        # Impede o Hugging Face Hub de checar atualizacoes de modelos.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    if "torch" in sys.modules:
        LOG.warning(
            "torch ja estava importado; limite de %s threads pode nao ter efeito.", n
        )


# ---------------------------------------------------------------------------
# Motores de conversao
# ---------------------------------------------------------------------------
class ErroConversao(RuntimeError):
    """Falha ao converter um documento especifico."""


class PdfIlegivel(RuntimeError):
    """contar_paginas() nao conseguiu abrir o PDF com o pypdfium2 instalado.

    Distinto de "biblioteca ausente" (que devolve None e segue permissivo):
    aqui o pypdfium2 esta disponivel e ainda assim rejeitou o arquivo, o que
    e sinal de PDF corrompido/adversarial - com --max-pages ativo, o
    chamador deve tratar isso como falha em vez de pular a pre-checagem.
    """


# Sentinela interna para o page_break_placeholder do docling_core - o valor
# literal nunca aparece no markdown final. docling_core NAO interpola o
# numero da pagina nesse parametro: e uma string fixa, a mesma em toda
# quebra (rodada 5, TAREFA-1 - verificado lendo
# docling_core/transforms/serializer/markdown.py: MarkdownDocSerializer
# guarda prev_page/next_page no marcador interno, mas serialize_doc() so usa
# self.params.page_break_placeholder, descartando os dois numeros).
# Por isso a numeracao e feita aqui, em _numerar_paginas(), contando as
# quebras em ordem - elas aparecem sempre em ordem crescente de pagina.
_SENTINELA_QUEBRA_DE_PAGINA = "\x00_PDFTOMD_QUEBRA_DE_PAGINA_\x00"


def _marcador_pagina(numero: int) -> str:
    """Formato do marcador de pagina de origem - usado pelos dois motores,
    para a saida ficar identica independente de qual gerou o arquivo."""
    return f"<!-- página {numero} -->"


def _numerar_paginas(markdown: str, sentinela: str = _SENTINELA_QUEBRA_DE_PAGINA) -> str:
    """Troca os marcadores de quebra de pagina (literais, sem numero) por
    marcadores numerados sequenciais, e marca tambem o inicio da pagina 1
    (que o docling_core nao marca - so ha marcador nas quebras ENTRE
    paginas). Cada marcador fica isolado por linhas em branco, entao nao
    pode corromper uma tabela ou bloco de codigo cuja sintaxe dependa de
    estar sem interrupcao - a quebra do docling_core so aparece ENTRE partes
    ja serializadas (cada tabela/paragrafo e uma unidade), nunca no meio de
    uma."""
    if sentinela not in markdown:
        return f"{_marcador_pagina(1)}\n\n{markdown}"
    partes = markdown.split(sentinela)
    pedacos = [_marcador_pagina(1), "\n\n", partes[0]]
    for indice, parte in enumerate(partes[1:], start=2):
        pedacos.append(f"\n\n{_marcador_pagina(indice)}\n\n")
        pedacos.append(parte)
    return "".join(pedacos)


class MotorBase:
    nome = "base"

    def disponivel(self) -> tuple[bool, str]:
        raise NotImplementedError

    def converter(self, pdf: Path, *, ocr: bool | None = None) -> str:
        """`ocr=None` usa o padrao do motor (self.cfg.ocr); um bool explicito
        sobrepoe SO para esta chamada, sem mutar self.cfg (rodada 3,
        TAREFA-4: liga o override por job que converter_arquivo() ja
        calcula em cfg.ocr a este metodo - antes desse parametro existir,
        converter_arquivo() calculava um Config por job mas nunca o
        repassava pra ca, entao o override nao tinha efeito algum)."""
        raise NotImplementedError


class MotorDocling(MotorBase):
    """Motor de alta fidelidade: layout, tabelas (TableFormer) e OCR."""

    nome = "docling"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # Um DocumentConverter por MODO DE OCR (True/False), nao um unico
        # self._conv (rodada 3, TAREFA-4): do_ocr e campo de
        # PdfPipelineOptions, nao argumento de convert() - trocar OCR por
        # job exige um converter por modo, cada um com seu proprio
        # layout+TableFormer carregado. Ambos preguiçosos: nenhum e criado
        # no startup, so quando um job daquele modo realmente aparece.
        self._convs: dict[bool, object] = {}  # DocumentConverter (import preguicoso)

    def disponivel(self) -> tuple[bool, str]:
        try:
            import importlib.util

            if importlib.util.find_spec("docling") is None:
                return False, "pacote 'docling' nao instalado"
        except Exception as exc:  # pragma: no cover - defensivo
            return False, f"erro ao inspecionar docling: {exc}"
        return True, ""

    # -- construcao das opcoes ------------------------------------------------
    def _opcoes_ocr(self):
        from docling.datamodel import pipeline_options as po

        alvo = self.cfg.ocr_engine
        idiomas = list(self.cfg.lang) or ["pt"]

        if alvo == "rapidocr":
            if len(idiomas) > 1:
                LOG.warning(
                    "RapidOCR usa um idioma por execucao; usando %r e ignorando %s.",
                    idiomas[0], idiomas[1:],
                )
            return po.RapidOcrOptions(
                lang=[idiomas[0]], backend=self.cfg.ocr_backend
            )
        if alvo == "easyocr":
            return po.EasyOcrOptions(lang=idiomas)
        if alvo == "tesseract":
            codigos = [_MAPA_TESSERACT.get(i, i) for i in idiomas]
            return po.TesseractOcrOptions(lang=codigos)
        raise ValueError(f"Motor de OCR desconhecido: {alvo!r}")

    def _opcoes_pipeline(self, ocr: bool):
        from docling.datamodel import pipeline_options as po

        try:
            from docling.datamodel.accelerator_options import (
                AcceleratorDevice, AcceleratorOptions,
            )
        except ImportError:  # versoes antigas reexportavam em pipeline_options
            AcceleratorDevice = po.AcceleratorDevice
            AcceleratorOptions = po.AcceleratorOptions

        opts = po.PdfPipelineOptions()

        # Passa a string crua: o Docling aceita "auto", "cpu", "cuda", "cuda:N",
        # "mps" e "xpu". Versoes antigas tipavam o campo como enum, dai o fallback.
        try:
            opts.accelerator_options = AcceleratorOptions(
                num_threads=max(1, self.cfg.threads), device=self.cfg.device
            )
        except Exception:
            base_dev = self.cfg.device.split(":")[0].upper()
            if not hasattr(AcceleratorDevice, base_dev):
                raise ErroConversao(
                    f"Dispositivo {self.cfg.device!r} nao suportado por esta versao do Docling."
                )
            opts.accelerator_options = AcceleratorOptions(
                num_threads=max(1, self.cfg.threads),
                device=getattr(AcceleratorDevice, base_dev),
            )

        opts.do_ocr = ocr
        if ocr:
            opts.ocr_options = self._opcoes_ocr()

        if self.cfg.tables == "none":
            opts.do_table_structure = False
        else:
            opts.do_table_structure = True
            opts.table_structure_options.mode = (
                po.TableFormerMode.ACCURATE if self.cfg.tables == "accurate"
                else po.TableFormerMode.FAST
            )
            # Casa celulas com o texto nativo do PDF quando ele existe.
            opts.table_structure_options.do_cell_matching = True

        if self.cfg.artifacts:
            opts.artifacts_path = str(self.cfg.artifacts)
        if self.cfg.timeout:
            opts.document_timeout = float(self.cfg.timeout)

        return opts

    def _obter_converter(self, ocr: bool):
        if ocr not in self._convs:
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import (
                DocumentConverter, PdfFormatOption,
            )

            LOG.info(
                "Carregando modelos do Docling (modo ocr=%s, pode demorar na 1a "
                "conversao nesse modo)...", ocr,
            )
            self._convs[ocr] = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=self._opcoes_pipeline(ocr)
                    )
                }
            )
        return self._convs[ocr]

    def converter(self, pdf: Path, *, ocr: bool | None = None) -> str:
        efetivo = self.cfg.ocr if ocr is None else ocr
        resultado = self._obter_converter(efetivo).convert(str(pdf))

        status = getattr(resultado, "status", None)
        rotulo = getattr(status, "value", str(status or "")).lower()
        if rotulo in {"failure", "skipped"}:
            erros = getattr(resultado, "errors", None) or []
            detalhe = "; ".join(str(e) for e in erros[:3]) or "sem detalhes"
            raise ErroConversao(f"Docling retornou status '{rotulo}': {detalhe}")
        if rotulo == "partial_success":
            LOG.warning("%s: conversao parcial, parte do conteudo pode faltar.", pdf.name)

        markdown = resultado.document.export_to_markdown(
            page_break_placeholder=_SENTINELA_QUEBRA_DE_PAGINA
        )
        return _numerar_paginas(markdown)


class MotorSimples(MotorBase):
    """Motor leve: le a camada de texto do PDF. Sem OCR e sem tabelas."""

    nome = "simples"

    def disponivel(self) -> tuple[bool, str]:
        try:
            import importlib.util

            if importlib.util.find_spec("pypdfium2") is None:
                return False, "pacote 'pypdfium2' nao instalado"
        except Exception as exc:  # pragma: no cover - defensivo
            return False, f"erro ao inspecionar pypdfium2: {exc}"
        return True, ""

    def converter(self, pdf: Path, *, ocr: bool | None = None) -> str:
        # MotorSimples nunca faz OCR (so le a camada de texto nativa) - o
        # parametro existe so pra manter a mesma assinatura de MotorBase.
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf))
        try:
            partes: list[str] = []
            for i in range(len(doc)):
                pagina = doc[i]
                texto = pagina.get_textpage().get_text_bounded() or ""
                # PDFium devolve CRLF; normaliza para nao gerar .md misto.
                texto = texto.replace("\r\n", "\n").replace("\r", "\n").strip()
                if texto:
                    # Numero real da pagina (1-indexado) - paginas em
                    # branco (sem texto) nao geram marcador, o mesmo
                    # comportamento do MotorDocling (que so marca quebras
                    # entre paginas com conteudo serializado).
                    partes.append(f"{_marcador_pagina(i + 1)}\n\n{texto}")
            if not partes:
                raise ErroConversao(
                    "nenhum texto extraivel (PDF digitalizado?) - use --engine docling com --ocr"
                )
            return "\n\n".join(partes) + "\n"
        finally:
            doc.close()


def selecionar_motor(cfg: Config) -> MotorBase:
    """Escolhe o motor conforme a configuracao e o que esta instalado."""
    docling, simples = MotorDocling(cfg), MotorSimples()

    if cfg.engine == "docling":
        ok, motivo = docling.disponivel()
        if not ok:
            raise ErroConversao(
                f"Motor 'docling' indisponivel ({motivo}). "
                "Instale com: pip install 'docling[rapidocr]'"
            )
        return docling

    if cfg.engine == "simples":
        ok, motivo = simples.disponivel()
        if not ok:
            raise ErroConversao(
                f"Motor 'simples' indisponivel ({motivo}). Instale com: pip install pypdfium2"
            )
        return simples

    ok, motivo = docling.disponivel()
    if ok:
        return docling
    ok_s, motivo_s = simples.disponivel()
    if ok_s:
        LOG.warning("Docling indisponivel (%s); usando motor 'simples' (sem OCR/tabelas).", motivo)
        return simples
    raise ErroConversao(
        f"Nenhum motor disponivel (docling: {motivo}; simples: {motivo_s})."
    )


# ---------------------------------------------------------------------------
# Resolucao de caminhos
# ---------------------------------------------------------------------------
def parece_diretorio(destino: Path | str) -> bool:
    """Heuristica: alvo e diretorio se existir como tal, terminar com barra
    ou nao tiver extensao de Markdown.

    Corrige o caso em que '-o saida/docs' (ainda inexistente) era tratado como
    arquivo, gerando um arquivo sem extensao chamado 'docs'.
    """
    texto = str(destino)
    p = Path(texto)
    if p.is_dir():
        return True
    if p.is_file():
        return False
    if texto.endswith(("/", "\\", os.sep)):
        return True
    return p.suffix.lower() not in SUFIXOS_MD


def resolver_saida(
    pdf: Path,
    destino: Path | None,
    *,
    base: Path | None = None,
    multiplas: bool = False,
) -> Path:
    """Calcula o caminho .md de saida para um PDF.

    - sem destino  -> mesmo diretorio do PDF
    - destino dir  -> preserva a subarvore relativa a `base` (modo recursivo)
    - destino file -> so permitido com uma unica entrada
    """
    nome = pdf.with_suffix(".md").name

    if destino is None:
        return pdf.with_suffix(".md")

    if multiplas or parece_diretorio(destino):
        raiz = Path(destino)
        if base is not None:
            try:
                relativo = pdf.parent.resolve().relative_to(base.resolve())
                return raiz / relativo / nome
            except ValueError:
                pass
        return raiz / nome

    return Path(destino)


def coletar_pdfs(
    entradas: Sequence[str], recursivo: bool
) -> tuple[list[tuple[Path, Path | None]], list[str]]:
    """Expande arquivos e diretorios em uma lista de (pdf, diretorio_base)."""
    encontrados: list[tuple[Path, Path | None]] = []
    problemas: list[str] = []
    vistos: set[Path] = set()

    for bruto in entradas:
        alvo = Path(bruto).expanduser()
        if alvo.is_dir():
            padrao = "**/*" if recursivo else "*"
            achados = sorted(
                p for p in alvo.glob(padrao)
                if p.is_file() and p.suffix.lower() in SUFIXOS_PDF
            )
            if not achados:
                problemas.append(f"nenhum PDF encontrado em '{alvo}'")
            for p in achados:
                chave = p.resolve()
                if chave not in vistos:
                    vistos.add(chave)
                    encontrados.append((p, alvo))
        elif alvo.is_file():
            if alvo.suffix.lower() not in SUFIXOS_PDF:
                problemas.append(f"'{alvo}' nao e um PDF")
                continue
            chave = alvo.resolve()
            if chave not in vistos:
                vistos.add(chave)
                encontrados.append((alvo, None))
        else:
            problemas.append(f"'{bruto}' nao foi encontrado")

    return encontrados, problemas


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------
@dataclass
class Resultado:
    pdf: Path
    saida: Path
    status: str          # ok | pulado | erro
    mensagem: str = ""
    segundos: float = 0.0
    caracteres: int = 0


_AVISO_MAX_PAGES_EMITIDO = False


def contar_paginas(pdf: Path) -> int | None:
    """Conta paginas de forma barata, sem carregar os modelos pesados.

    Devolve None se a biblioteca pypdfium2 nao estiver instalada (pre-checagem
    pulada, aviso emitido uma vez) - esse caso e permissivo de proposito.
    Levanta PdfIlegivel se o pypdfium2 estiver instalado mas nao conseguir
    abrir o arquivo; esse caso NAO deve ser tratado como "pular a
    pre-checagem" (ver converter_arquivo).
    """
    global _AVISO_MAX_PAGES_EMITIDO
    try:
        import pypdfium2 as pdfium
    except ImportError:
        if not _AVISO_MAX_PAGES_EMITIDO:
            LOG.warning(
                "pypdfium2 nao instalado; --max-pages nao pode pre-checar "
                "o numero de paginas (pip install pypdfium2)."
            )
            _AVISO_MAX_PAGES_EMITIDO = True
        return None

    try:
        doc = pdfium.PdfDocument(str(pdf))
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception as exc:
        raise PdfIlegivel(f"pypdfium2 nao conseguiu abrir o PDF: {exc}") from exc


# Parametros de amostragem calibrados contra dois documentos reais (ver
# relatorio da rodada 3, TAREFA-3): um manual nativo grande (1310 paginas,
# texto rico em toda pagina amostrada) e um documento digitalizado real
# (26 paginas, zero texto extraivel em TODAS as paginas). Nao houve um
# terceiro documento misto organico disponivel - o caso misto foi montado
# combinando paginas reais dos dois anteriores (ver relatorio).
_OCR_AMOSTRAS_PADRAO = 12
_OCR_MIN_CHARS_PAGINA = 40
# Regra de maioria (>=50%), nao "qualquer pagina com texto": os dois erros
# possiveis nao custam o mesmo. Falso negativo (roda OCR num PDF nativo)
# so desperdica tempo - a saida sai correta. Falso positivo (pula OCR num
# PDF digitalizado) produz saida vazia/incompleta, silenciosamente. Por
# isso o limiar e conservador a favor de RODAR OCR quando incerto, em vez
# de otimizar para evitar OCR desnecessario.
_OCR_PROPORCAO_MINIMA = 0.5


def tem_camada_de_texto(
    pdf: Path,
    *,
    amostras: int = _OCR_AMOSTRAS_PADRAO,
    min_chars_pagina: int = _OCR_MIN_CHARS_PAGINA,
    proporcao_minima: float = _OCR_PROPORCAO_MINIMA,
) -> bool:
    """Decide, sem OCR nem modelos pesados, se o PDF ja tem uma camada de
    texto nativa (documento gerado por editor) ou precisa de OCR
    (digitalizado/escaneado).

    Amostra ate `amostras` paginas DISTRIBUIDAS ao longo do documento (nao
    as primeiras N - capa e sumario nao representam o miolo do documento) e
    decide pela PROPORCAO de paginas amostradas que trouxeram conteudo
    substantivo (>= min_chars_pagina caracteres apos strip), nao por
    "qualquer pagina com texto". Isso tolera paginas legitimamente sem
    texto num documento nativo (diagramas, folhas de separacao) sem
    empurrar a decisao para "digitalizado" - só uma MAIORIA de paginas sem
    texto (proporcao abaixo de `proporcao_minima`) faz isso.

    Levanta PdfIlegivel nas mesmas condicoes que contar_paginas().
    """
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(pdf))
    except Exception as exc:
        raise PdfIlegivel(f"pypdfium2 nao conseguiu abrir o PDF: {exc}") from exc

    try:
        total = len(doc)
        if total == 0:
            return False
        n = min(amostras, total)
        # indices distribuidos uniformemente de 0 a total-1 (inclusive nas
        # duas pontas quando n>1); set() descarta duplicatas em documentos
        # curtos (n proximo de total).
        if n == 1:
            indices = [total // 2]
        else:
            indices = sorted({round(i * (total - 1) / (n - 1)) for i in range(n)})

        com_texto = 0
        for i in indices:
            texto = doc[i].get_textpage().get_text_range()
            if len(texto.strip()) >= min_chars_pagina:
                com_texto += 1

        return (com_texto / len(indices)) >= proporcao_minima
    finally:
        doc.close()


def escrever_atomico(destino: Path, conteudo: str) -> None:
    """Grava via arquivo temporario + rename, evitando .md truncado se falhar."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".tmp")
    try:
        temporario.write_text(conteudo, encoding="utf-8")
        temporario.replace(destino)
    finally:
        if temporario.exists():
            temporario.unlink(missing_ok=True)


def converter_arquivo(pdf: Path, saida: Path, motor: MotorBase, cfg: Config) -> Resultado:
    if saida.resolve() == pdf.resolve():
        return Resultado(pdf, saida, "erro", "saida coincide com a entrada")

    if saida.exists() and not cfg.overwrite:
        return Resultado(pdf, saida, "pulado", "ja existe (use --overwrite)")

    if cfg.dry_run:
        return Resultado(pdf, saida, "ok", "simulado (--dry-run)")

    if cfg.max_pages is not None:
        try:
            paginas = contar_paginas(pdf)
        except PdfIlegivel:
            return Resultado(
                pdf, saida, "erro",
                "nao foi possivel pre-checar o numero de paginas",
            )
        if paginas is not None and paginas > cfg.max_pages:
            # max_pages e usado tanto pela CLI (--max-pages, opt-in) quanto
            # pela app web (MAX_UPLOAD_PAGES, ligado por padrao como teto de
            # protecao contra entrada patologica - nao politica de uso: ver
            # dependencies.md). A mensagem nomeia os dois ajustes porque essa
            # funcao nao sabe qual interface a chamou.
            return Resultado(
                pdf, saida, "erro",
                f"{paginas} paginas excede o teto de protecao max_pages={cfg.max_pages} "
                "(ajustavel via --max-pages na CLI ou MAX_UPLOAD_PAGES na app web)",
            )

    inicio = time.perf_counter()
    try:
        # ocr=cfg.ocr explicito (rodada 3, TAREFA-4): antes disso, cfg era
        # usado so pelas checagens acima (overwrite/dry_run/max_pages) e
        # NUNCA chegava ao motor - motor.converter(pdf) sempre usava
        # self.cfg.ocr do proprio motor, fixado na construcao. Pra CLI isso
        # nao mudava nada (cfg aqui e o MESMO objeto usado pra construir o
        # motor em selecionar_motor()); pra app web, e o que agora faz o
        # override por job (jobs._processar) realmente valer.
        markdown = motor.converter(pdf, ocr=cfg.ocr)
    except ErroConversao as exc:
        return Resultado(pdf, saida, "erro", str(exc), time.perf_counter() - inicio)
    except Exception as exc:
        LOG.debug("Falha em %s", pdf, exc_info=True)
        return Resultado(
            pdf, saida, "erro", f"{type(exc).__name__}: {exc}",
            time.perf_counter() - inicio,
        )

    if not markdown.strip():
        return Resultado(
            pdf, saida, "erro", "conversao retornou conteudo vazio",
            time.perf_counter() - inicio,
        )

    try:
        escrever_atomico(saida, markdown)
    except OSError as exc:
        return Resultado(pdf, saida, "erro", f"falha ao gravar: {exc}")

    return Resultado(
        pdf, saida, "ok", "", time.perf_counter() - inicio, len(markdown)
    )


def _converter_pool_item(item: tuple[Path, Path, MotorBase, Config]) -> Resultado:
    """Ponto de entrada de nivel de modulo p/ ProcessPoolExecutor (precisa ser picklable)."""
    pdf, saida, motor, cfg = item
    return converter_arquivo(pdf, saida, motor, cfg)


def _logar_resultado(indice: int, total: int, pdf: Path, resultado: Resultado) -> None:
    LOG.info("[%d/%d] %s", indice, total, pdf.name)
    if resultado.status == "ok":
        LOG.info("      -> %s (%.1fs)", resultado.saida, resultado.segundos)
    elif resultado.status == "pulado":
        LOG.warning("      -> pulado: %s", resultado.mensagem)
    else:
        LOG.error("      -> erro: %s", resultado.mensagem)


def executar(
    entradas: Sequence[str], destino: str | None, cfg: Config, recursivo: bool = False
) -> tuple[list[Resultado], list[str]]:
    pdfs, problemas = coletar_pdfs(entradas, recursivo)
    if not pdfs:
        return [], problemas

    multiplas = len(pdfs) > 1
    if multiplas and destino and not parece_diretorio(destino):
        problemas.append(
            f"'{destino}' parece um arquivo, mas ha {len(pdfs)} PDFs; informe um diretorio"
        )
        return [], problemas

    # Calcula todas as saidas antes de converter para detectar colisoes.
    planejado: list[tuple[Path, Path]] = [
        (
            pdf,
            resolver_saida(
                pdf, Path(destino) if destino else None, base=base, multiplas=multiplas
            ),
        )
        for pdf, base in pdfs
    ]

    ocupados: dict[Path, Path] = {}
    for pdf, saida in planejado:
        chave = Path(os.path.normcase(str(saida.absolute())))
        if chave in ocupados:
            problemas.append(
                f"'{pdf}' e '{ocupados[chave]}' gerariam o mesmo arquivo '{saida.name}'; "
                "use -r com um diretorio raiz comum ou renomeie os PDFs"
            )
        else:
            ocupados[chave] = pdf
    if len(ocupados) != len(planejado):
        return [], problemas

    motor = selecionar_motor(cfg)
    LOG.info("Motor: %s | %d arquivo(s)", motor.nome, len(pdfs))

    jobs = cfg.jobs
    if jobs > 1 and motor.nome != "simples":
        LOG.warning(
            "--jobs %d ignorado para o motor '%s': cada processo recarregaria "
            "os modelos inteiros, trocando RAM por paralelismo sem ganho real "
            "aqui. Rodando sequencial.", jobs, motor.nome,
        )
        jobs = 1

    if jobs > 1 and len(planejado) > 1:
        itens = [(pdf, saida, motor, cfg) for pdf, saida in planejado]
        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
            resultados = list(executor.map(_converter_pool_item, itens))
        for indice, ((pdf, _saida), resultado) in enumerate(zip(planejado, resultados), start=1):
            _logar_resultado(indice, len(pdfs), pdf, resultado)
        return resultados, problemas

    resultados = []
    for indice, (pdf, saida) in enumerate(planejado, start=1):
        resultado = converter_arquivo(pdf, saida, motor, cfg)
        resultados.append(resultado)
        _logar_resultado(indice, len(pdfs), pdf, resultado)

    return resultados, problemas


def resumir(resultados: Iterable[Resultado]) -> dict[str, int]:
    resumo = {"ok": 0, "pulado": 0, "erro": 0}
    for r in resultados:
        resumo[r.status] = resumo.get(r.status, 0) + 1
    return resumo


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf_to_md.py",
        description="Converte PDFs em Markdown preservando estrutura e tabelas.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  pdf_to_md.py -i relatorio.pdf\n"
            "  pdf_to_md.py -i ./pdfs -o ./md -r --overwrite\n"
            "  pdf_to_md.py -i doc.pdf --no-ocr --tables fast\n"
            "  pdf_to_md.py -i scan.pdf --ocr-engine tesseract --lang pt en\n"
        ),
    )
    p.add_argument("-i", "--input", nargs="+", metavar="CAMINHO",
                   help="PDF(s) ou diretorio(s) de entrada.")
    p.add_argument("-o", "--output", metavar="CAMINHO",
                   help="Arquivo .md (1 entrada) ou diretorio de saida.")
    p.add_argument("-r", "--recursive", action="store_true",
                   help="Percorre subdiretorios.")
    p.add_argument("--engine", choices=["auto", "docling", "simples"], default="auto",
                   help="Motor de conversao (padrao: auto).")

    g = p.add_mutually_exclusive_group()
    g.add_argument("--ocr", dest="ocr", action="store_true", default=True,
                   help="Ativa OCR (padrao).")
    g.add_argument("--no-ocr", dest="ocr", action="store_false",
                   help="Desativa OCR - bem mais rapido em PDFs digitais.")

    p.add_argument("--ocr-engine", choices=["rapidocr", "easyocr", "tesseract"],
                   default="rapidocr", help="Motor de OCR (padrao: rapidocr).")
    p.add_argument("--lang", nargs="+", default=["pt"], metavar="COD",
                   help="Idiomas do OCR em ISO 639-1 (padrao: pt).")
    p.add_argument("--tables", choices=["accurate", "fast", "none"], default="accurate",
                   help="Qualidade da reconstrucao de tabelas (padrao: accurate).")
    p.add_argument("--threads", type=int, default=4, metavar="N",
                   help="Limite de threads de CPU (padrao: 4).")
    p.add_argument("--device", default="auto", metavar="DEV",
                   help="auto | cpu | cuda | cuda:N | mps | xpu (padrao: auto). "
                        "Atencao: DirectML no OCR so e ativado com 'auto'.")
    p.add_argument("--ocr-backend",
                   choices=["onnxruntime", "openvino", "paddle", "torch"],
                   default="onnxruntime",
                   help="Backend de inferencia do RapidOCR (padrao: onnxruntime).")
    p.add_argument("--hardware", action="store_true",
                   help="Mostra os aceleradores detectados e sai.")
    p.add_argument("--offline", action="store_true",
                   help="Bloqueia consultas ao Hugging Face Hub.")
    p.add_argument("--artifacts", metavar="DIR",
                   help="Diretorio local com os modelos do Docling.")
    p.add_argument("--timeout", type=float, metavar="SEG",
                   help="Tempo maximo por documento.")
    p.add_argument("--max-pages", type=int, metavar="N",
                   help="Recusa PDFs com mais de N paginas antes de converter "
                        "(checagem barata via pypdfium2; sem limite por padrao).")
    p.add_argument("--jobs", type=int, default=1, metavar="N",
                   help="PDFs convertidos em paralelo (padrao: 1). So tem efeito "
                        "com --engine simples; ignorado com docling.")
    p.add_argument("--overwrite", action="store_true",
                   help="Sobrescreve arquivos .md existentes.")
    p.add_argument("--dry-run", action="store_true",
                   help="Mostra o que seria feito, sem converter.")
    p.add_argument("-v", "--verbose", action="store_true", help="Log detalhado.")
    p.add_argument("-q", "--quiet", action="store_true", help="Apenas erros.")
    p.add_argument("--version", action="version", version=f"pdf_to_md {__version__}")
    return p


def relatar_hardware() -> None:
    """Diagnostica quais aceleradores estao realmente disponiveis nesta maquina."""
    print("Aceleradores detectados")
    print("-" * 46)

    try:
        import torch
    except ImportError:
        print("  torch          : nao instalado (estagios de layout/tabelas ficam na CPU)")
    else:
        print(f"  torch          : {torch.__version__}")
        cuda = torch.backends.cuda.is_built() and torch.cuda.is_available()
        print(f"  CUDA (NVIDIA)  : {'sim' if cuda else 'nao'}", end="")
        if cuda:
            nomes = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
            print(f"  -> {', '.join(nomes)}", end="")
        print()
        mps = torch.backends.mps.is_built() and torch.backends.mps.is_available()
        print(f"  MPS (Apple)    : {'sim' if mps else 'nao'}")
        xpu = hasattr(torch, "xpu") and torch.xpu.is_available()
        print(f"  XPU (Intel)    : {'sim' if xpu else 'nao'}")

    try:
        import onnxruntime as ort
    except ImportError:
        print("  onnxruntime    : nao instalado (sem aceleracao de OCR por GPU)")
    else:
        provedores = ort.get_available_providers()
        print(f"  onnxruntime    : {ort.__version__}")
        for rotulo, ep in (("DirectML", "DmlExecutionProvider"),
                           ("CUDA EP", "CUDAExecutionProvider"),
                           ("OpenVINO", "OpenVINOExecutionProvider"),
                           ("ROCm EP", "ROCMExecutionProvider")):
            print(f"    {rotulo:<12} : {'sim' if ep in provedores else 'nao'}")

    print("-" * 46)
    print("Layout/tabelas usam torch (cuda/mps/xpu). OCR usa o backend do RapidOCR.")
    print("DirectML no OCR exige onnxruntime-directml E --device auto.")


def configurar_log(verbose: bool, quiet: bool) -> None:
    nivel = logging.DEBUG if verbose else logging.ERROR if quiet else logging.INFO
    logging.basicConfig(level=nivel, format="%(levelname)s: %(message)s", force=True)


def perguntar_entrada() -> list[str] | None:
    print("=" * 60)
    print(f" Conversor PDF -> Markdown  v{__version__}")
    print("=" * 60)
    try:
        resposta = input("Arquivo ou pasta com PDFs: ").strip().strip('"').strip("'")
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return [resposta] if resposta else None


def main(argv: Sequence[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    configurar_log(args.verbose, args.quiet)

    if args.hardware:
        relatar_hardware()
        return EXIT_OK

    if not DEVICE_VALIDO.match(args.device):
        LOG.error("--device invalido: %r. Use auto, cpu, cuda, cuda:N, mps ou xpu.",
                  args.device)
        return EXIT_USO
    if args.threads < 1:
        LOG.error("--threads deve ser >= 1.")
        return EXIT_USO
    if args.timeout is not None and args.timeout <= 0:
        LOG.error("--timeout deve ser maior que zero.")
        return EXIT_USO
    if args.max_pages is not None and args.max_pages < 1:
        LOG.error("--max-pages deve ser >= 1.")
        return EXIT_USO
    if args.jobs < 1:
        LOG.error("--jobs deve ser >= 1.")
        return EXIT_USO

    entradas = args.input or perguntar_entrada()
    if not entradas:
        LOG.error("Nenhuma entrada informada.")
        return EXIT_USO

    cfg = Config(
        engine=args.engine,
        ocr=args.ocr,
        ocr_engine=args.ocr_engine,
        ocr_backend=args.ocr_backend,
        lang=list(args.lang),
        tables=args.tables,
        threads=args.threads,
        device=args.device,
        offline=args.offline,
        artifacts=Path(args.artifacts).expanduser() if args.artifacts else None,
        timeout=args.timeout,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        max_pages=args.max_pages,
        jobs=args.jobs,
    )
    aplicar_ambiente(cfg)

    try:
        resultados, problemas = executar(entradas, args.output, cfg, args.recursive)
    except ErroConversao as exc:
        LOG.error("%s", exc)
        return EXIT_FALHA
    except KeyboardInterrupt:
        LOG.warning("Interrompido pelo usuario.")
        return EXIT_INTERROMPIDO

    for aviso in problemas:
        LOG.error("%s", aviso)

    if not resultados:
        return EXIT_FALHA

    resumo = resumir(resultados)
    LOG.info(
        "Concluido: %d convertido(s), %d pulado(s), %d com erro.",
        resumo["ok"], resumo["pulado"], resumo["erro"],
    )
    return EXIT_FALHA if (resumo["erro"] or problemas) else EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(EXIT_INTERROMPIDO)
