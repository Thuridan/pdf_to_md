#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite de testes de pdf_to_md.py.

Cobre: resolucao de caminhos, coleta em lote, gravacao atomica, codigos de
saida, selecao de motor, construcao das opcoes do Docling (via stub) e uma
conversao real ponta a ponta com pypdfium2.
"""

from __future__ import annotations

import importlib.machinery
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pdf_to_md as m  # noqa: E402


# ---------------------------------------------------------------------------
# Utilitarios
# ---------------------------------------------------------------------------
def gerar_pdf(caminho: Path, linhas: list[str], paginas: int = 1) -> Path:
    from fpdf import FPDF

    pdf = FPDF()
    for p in range(paginas):
        pdf.add_page()
        pdf.set_font("helvetica", size=12)
        for linha in linhas:
            pdf.cell(0, 8, f"{linha} (p{p + 1})", new_x="LMARGIN", new_y="NEXT")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(caminho))
    return caminho


def instalar_stub_docling(*, status="success", markdown="# Convertido\n\ntexto",
                          erro: Exception | None = None) -> dict:
    """Injeta um docling falso em sys.modules e devolve o registro de chamadas."""
    registro: dict = {"opcoes": None, "convertidos": [], "instancias": 0}

    def modulo(nome: str) -> types.ModuleType:
        mod = types.ModuleType(nome)
        mod.__spec__ = importlib.machinery.ModuleSpec(nome, None)
        return mod

    docling = modulo("docling")
    docling.__path__ = []
    datamodel = modulo("docling.datamodel")
    datamodel.__path__ = []
    po = modulo("docling.datamodel.pipeline_options")
    accel = modulo("docling.datamodel.accelerator_options")
    base = modulo("docling.datamodel.base_models")
    dc = modulo("docling.document_converter")

    class _Enum(str):
        pass

    class TableFormerMode:
        FAST = _Enum("fast")
        ACCURATE = _Enum("accurate")

    class AcceleratorDevice:
        AUTO = _Enum("auto")
        CPU = _Enum("cpu")
        CUDA = _Enum("cuda")
        MPS = _Enum("mps")

    class AcceleratorOptions:
        def __init__(self, num_threads=4, device=None):
            self.num_threads, self.device = num_threads, device

    class _Ocr:
        def __init__(self, lang=None):
            self.lang = lang

    class RapidOcrOptions(_Ocr):
        kind = "rapidocr"

        def __init__(self, lang=None, backend="onnxruntime"):
            super().__init__(lang)
            self.backend = backend

    class EasyOcrOptions(_Ocr):
        kind = "easyocr"

    class TesseractOcrOptions(_Ocr):
        kind = "tesserocr"

    class _Tabelas:
        def __init__(self):
            self.mode = TableFormerMode.FAST
            self.do_cell_matching = False

    class PdfPipelineOptions:
        def __init__(self):
            self.do_ocr = True
            self.do_table_structure = True
            self.ocr_options = None
            self.table_structure_options = _Tabelas()
            self.accelerator_options = None
            self.artifacts_path = None
            self.document_timeout = None

    for k, v in dict(
        PdfPipelineOptions=PdfPipelineOptions, RapidOcrOptions=RapidOcrOptions,
        EasyOcrOptions=EasyOcrOptions, TesseractOcrOptions=TesseractOcrOptions,
        TableFormerMode=TableFormerMode, AcceleratorDevice=AcceleratorDevice,
        AcceleratorOptions=AcceleratorOptions,
    ).items():
        setattr(po, k, v)
    accel.AcceleratorDevice, accel.AcceleratorOptions = AcceleratorDevice, AcceleratorOptions

    class InputFormat:
        PDF = _Enum("pdf")

    base.InputFormat = InputFormat

    class PdfFormatOption:
        def __init__(self, pipeline_options=None):
            registro["opcoes"] = pipeline_options
            self.pipeline_options = pipeline_options

    class _Doc:
        def export_to_markdown(self):
            return markdown

    class _Res:
        def __init__(self):
            self.status = _Enum(status)
            self.document = _Doc()
            self.errors = ["detalhe do erro"]

    class DocumentConverter:
        def __init__(self, format_options=None):
            registro["instancias"] += 1
            self.format_options = format_options

        def convert(self, caminho):
            registro["convertidos"].append(caminho)
            if erro:
                raise erro
            return _Res()

    dc.DocumentConverter, dc.PdfFormatOption = DocumentConverter, PdfFormatOption
    docling.datamodel = datamodel
    datamodel.pipeline_options, datamodel.accelerator_options = po, accel
    datamodel.base_models = base
    docling.document_converter = dc

    for nome, mod in {
        "docling": docling, "docling.datamodel": datamodel,
        "docling.datamodel.pipeline_options": po,
        "docling.datamodel.accelerator_options": accel,
        "docling.datamodel.base_models": base,
        "docling.document_converter": dc,
    }.items():
        sys.modules[nome] = mod
    return registro


def remover_stub_docling() -> None:
    for nome in [n for n in sys.modules if n == "docling" or n.startswith("docling.")]:
        del sys.modules[nome]


class BaseTemp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        remover_stub_docling()


# ---------------------------------------------------------------------------
# 1. Resolucao de caminhos
# ---------------------------------------------------------------------------
class TestCaminhos(BaseTemp):
    def test_saida_padrao_ao_lado_do_pdf(self):
        pdf = self.tmp / "sub" / "a.pdf"
        self.assertEqual(m.resolver_saida(pdf, None), self.tmp / "sub" / "a.md")

    def test_saida_arquivo_explicito(self):
        pdf = self.tmp / "a.pdf"
        alvo = self.tmp / "outro.md"
        self.assertEqual(m.resolver_saida(pdf, alvo), alvo)

    def test_diretorio_existente(self):
        d = self.tmp / "out"
        d.mkdir()
        self.assertEqual(m.resolver_saida(self.tmp / "a.pdf", d), d / "a.md")

    def test_diretorio_inexistente_sem_extensao(self):
        """Regressao: '-o saida/docs' inexistente virava um ARQUIVO 'docs'."""
        alvo = self.tmp / "saida" / "docs"
        self.assertEqual(m.resolver_saida(self.tmp / "a.pdf", alvo), alvo / "a.md")

    def test_barra_final_forca_diretorio(self):
        alvo = str(self.tmp / "novo") + os.sep
        self.assertEqual(m.resolver_saida(self.tmp / "a.pdf", alvo), Path(alvo) / "a.md")

    def test_espelha_subarvore_no_modo_recursivo(self):
        base = self.tmp / "in"
        pdf = base / "n1" / "n2" / "a.pdf"
        destino = self.tmp / "out"
        pdf.parent.mkdir(parents=True)
        esperado = destino / "n1" / "n2" / "a.md"
        self.assertEqual(
            m.resolver_saida(pdf, destino, base=base, multiplas=True), esperado
        )

    def test_multiplas_entradas_tratam_destino_como_diretorio(self):
        alvo = self.tmp / "x.md"
        self.assertEqual(
            m.resolver_saida(self.tmp / "a.pdf", alvo, multiplas=True), alvo / "a.md"
        )

    def test_parece_diretorio(self):
        self.assertTrue(m.parece_diretorio(self.tmp / "algo"))
        self.assertFalse(m.parece_diretorio(self.tmp / "algo.md"))
        self.assertFalse(m.parece_diretorio(self.tmp / "algo.markdown"))


# ---------------------------------------------------------------------------
# 2. Coleta de entradas
# ---------------------------------------------------------------------------
class TestColeta(BaseTemp):
    def _montar(self):
        gerar_pdf(self.tmp / "a.pdf", ["A"])
        gerar_pdf(self.tmp / "sub" / "b.pdf", ["B"])
        (self.tmp / "nota.txt").write_text("x", encoding="utf-8")

    def test_arquivo_unico(self):
        self._montar()
        achados, probs = m.coletar_pdfs([str(self.tmp / "a.pdf")], False)
        self.assertEqual(len(achados), 1)
        self.assertEqual(probs, [])

    def test_diretorio_nao_recursivo(self):
        self._montar()
        achados, _ = m.coletar_pdfs([str(self.tmp)], False)
        self.assertEqual([p.name for p, _ in achados], ["a.pdf"])

    def test_diretorio_recursivo(self):
        self._montar()
        achados, _ = m.coletar_pdfs([str(self.tmp)], True)
        self.assertEqual(sorted(p.name for p, _ in achados), ["a.pdf", "b.pdf"])

    def test_deduplica_entradas_repetidas(self):
        self._montar()
        alvo = str(self.tmp / "a.pdf")
        achados, _ = m.coletar_pdfs([alvo, alvo, str(self.tmp)], False)
        self.assertEqual(len(achados), 1)

    def test_reporta_inexistente_e_nao_pdf(self):
        self._montar()
        achados, probs = m.coletar_pdfs(
            [str(self.tmp / "nao_existe.pdf"), str(self.tmp / "nota.txt")], False
        )
        self.assertEqual(achados, [])
        self.assertEqual(len(probs), 2)

    def test_extensao_maiuscula(self):
        gerar_pdf(self.tmp / "MAIUSCULO.PDF", ["X"])
        achados, _ = m.coletar_pdfs([str(self.tmp)], False)
        self.assertEqual(len(achados), 1)


# ---------------------------------------------------------------------------
# 3. Gravacao e conversao unitaria
# ---------------------------------------------------------------------------
class MotorFake(m.MotorBase):
    nome = "fake"

    def __init__(self, texto="# ok\n", excecao=None):
        self.texto, self.excecao, self.chamadas = texto, excecao, []

    def disponivel(self):
        return True, ""

    def converter(self, pdf):
        self.chamadas.append(pdf)
        if self.excecao:
            raise self.excecao
        return self.texto


class TestConversaoUnitaria(BaseTemp):
    def test_escrita_atomica_sem_deixar_tmp(self):
        alvo = self.tmp / "d" / "x.md"
        m.escrever_atomico(alvo, "conteudo")
        self.assertEqual(alvo.read_text(encoding="utf-8"), "conteudo")
        self.assertEqual(list((self.tmp / "d").glob("*.tmp")), [])

    def test_falha_nao_deixa_arquivo_parcial(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        saida = self.tmp / "a.md"
        r = m.converter_arquivo(pdf, saida, MotorFake(excecao=ValueError("boom")), m.Config())
        self.assertEqual(r.status, "erro")
        self.assertIn("boom", r.mensagem)
        self.assertFalse(saida.exists())

    def test_conteudo_vazio_e_erro(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        r = m.converter_arquivo(pdf, self.tmp / "a.md", MotorFake(texto="   \n"), m.Config())
        self.assertEqual(r.status, "erro")
        self.assertFalse((self.tmp / "a.md").exists())

    def test_pula_existente_sem_overwrite(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        saida = self.tmp / "a.md"
        saida.write_text("antigo", encoding="utf-8")
        r = m.converter_arquivo(pdf, saida, MotorFake(), m.Config())
        self.assertEqual(r.status, "pulado")
        self.assertEqual(saida.read_text(encoding="utf-8"), "antigo")

    def test_overwrite_substitui(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        saida = self.tmp / "a.md"
        saida.write_text("antigo", encoding="utf-8")
        r = m.converter_arquivo(pdf, saida, MotorFake(), m.Config(overwrite=True))
        self.assertEqual(r.status, "ok")
        self.assertEqual(saida.read_text(encoding="utf-8"), "# ok\n")

    def test_bloqueia_saida_igual_a_entrada(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        r = m.converter_arquivo(pdf, pdf, MotorFake(), m.Config(overwrite=True))
        self.assertEqual(r.status, "erro")
        self.assertIn("coincide", r.mensagem)

    def test_dry_run_nao_grava(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        motor = MotorFake()
        r = m.converter_arquivo(pdf, self.tmp / "a.md", motor, m.Config(dry_run=True))
        self.assertEqual(r.status, "ok")
        self.assertFalse((self.tmp / "a.md").exists())
        self.assertEqual(motor.chamadas, [])


# ---------------------------------------------------------------------------
# 4. Motor Docling (via stub)
# ---------------------------------------------------------------------------
class TestMotorDocling(BaseTemp):
    def test_opcoes_padrao_pt(self):
        reg = instalar_stub_docling()
        motor = m.MotorDocling(m.Config(lang=["pt"], threads=8, tables="accurate"))
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        self.assertEqual(motor.converter(pdf), "# Convertido\n\ntexto")
        o = reg["opcoes"]
        self.assertTrue(o.do_ocr)
        self.assertEqual(o.ocr_options.kind, "rapidocr")
        self.assertEqual(o.ocr_options.lang, ["pt"])
        self.assertTrue(o.do_table_structure)
        self.assertEqual(str(o.table_structure_options.mode), "accurate")
        self.assertTrue(o.table_structure_options.do_cell_matching)
        self.assertEqual(o.accelerator_options.num_threads, 8)

    def test_rapidocr_reduz_para_um_idioma(self):
        reg = instalar_stub_docling()
        motor = m.MotorDocling(m.Config(lang=["pt", "en"]))
        motor.converter(gerar_pdf(self.tmp / "a.pdf", ["A"]))
        self.assertEqual(reg["opcoes"].ocr_options.lang, ["pt"])

    def test_tesseract_converte_codigo_iso(self):
        reg = instalar_stub_docling()
        motor = m.MotorDocling(m.Config(ocr_engine="tesseract", lang=["pt", "en"]))
        motor.converter(gerar_pdf(self.tmp / "a.pdf", ["A"]))
        self.assertEqual(reg["opcoes"].ocr_options.lang, ["por", "eng"])

    def test_easyocr_mantem_lista(self):
        reg = instalar_stub_docling()
        m.MotorDocling(m.Config(ocr_engine="easyocr", lang=["pt", "en"])).converter(
            gerar_pdf(self.tmp / "a.pdf", ["A"])
        )
        self.assertEqual(reg["opcoes"].ocr_options.lang, ["pt", "en"])

    def test_no_ocr_e_sem_tabelas(self):
        reg = instalar_stub_docling()
        m.MotorDocling(m.Config(ocr=False, tables="none")).converter(
            gerar_pdf(self.tmp / "a.pdf", ["A"])
        )
        self.assertFalse(reg["opcoes"].do_ocr)
        self.assertFalse(reg["opcoes"].do_table_structure)

    def test_artifacts_e_timeout(self):
        reg = instalar_stub_docling()
        m.MotorDocling(m.Config(artifacts=Path("/modelos"), timeout=90)).converter(
            gerar_pdf(self.tmp / "a.pdf", ["A"])
        )
        self.assertEqual(reg["opcoes"].artifacts_path, "/modelos")
        self.assertEqual(reg["opcoes"].document_timeout, 90.0)

    def test_status_failure_vira_erro(self):
        instalar_stub_docling(status="failure")
        motor = m.MotorDocling(m.Config())
        with self.assertRaises(m.ErroConversao) as ctx:
            motor.converter(gerar_pdf(self.tmp / "a.pdf", ["A"]))
        self.assertIn("failure", str(ctx.exception))

    def test_partial_success_ainda_entrega(self):
        instalar_stub_docling(status="partial_success")
        motor = m.MotorDocling(m.Config())
        self.assertIn("Convertido", motor.converter(gerar_pdf(self.tmp / "a.pdf", ["A"])))

    def test_converter_reaproveitado_no_lote(self):
        """Modelos devem ser carregados uma unica vez para N arquivos."""
        reg = instalar_stub_docling()
        motor = m.MotorDocling(m.Config())
        for i in range(3):
            motor.converter(gerar_pdf(self.tmp / f"{i}.pdf", ["A"]))
        self.assertEqual(reg["instancias"], 1)
        self.assertEqual(len(reg["convertidos"]), 3)


# ---------------------------------------------------------------------------
# 5. Selecao de motor
# ---------------------------------------------------------------------------
class TestSelecaoMotor(BaseTemp):
    def test_auto_prefere_docling(self):
        instalar_stub_docling()
        self.assertEqual(m.selecionar_motor(m.Config()).nome, "docling")

    def test_auto_cai_para_simples(self):
        remover_stub_docling()
        self.assertEqual(m.selecionar_motor(m.Config()).nome, "simples")

    def test_docling_explicito_falha_com_mensagem_util(self):
        remover_stub_docling()
        with self.assertRaises(m.ErroConversao) as ctx:
            m.selecionar_motor(m.Config(engine="docling"))
        self.assertIn("pip install", str(ctx.exception))

    def test_simples_explicito(self):
        self.assertEqual(m.selecionar_motor(m.Config(engine="simples")).nome, "simples")


# ---------------------------------------------------------------------------
# 6. Ambiente
# ---------------------------------------------------------------------------
class TestAmbiente(unittest.TestCase):
    def setUp(self):
        self.backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.backup)

    def test_threads_aplicadas(self):
        m.aplicar_ambiente(m.Config(threads=6))
        self.assertEqual(os.environ["OMP_NUM_THREADS"], "6")
        self.assertEqual(os.environ["MKL_NUM_THREADS"], "6")

    def test_offline_desligado_por_padrao(self):
        os.environ.pop("HF_HUB_OFFLINE", None)
        m.aplicar_ambiente(m.Config())
        self.assertNotIn("HF_HUB_OFFLINE", os.environ)

    def test_offline_ligado(self):
        m.aplicar_ambiente(m.Config(offline=True))
        self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")


try:
    import tomllib
except ImportError:  # Python 3.10 nao tem tomllib na stdlib.
    tomllib = None


@unittest.skipUnless(tomllib is not None, "requer tomllib (Python 3.11+)")
class TestEmpacotamento(unittest.TestCase):
    """BUG-02: sem NENHUM motor instalado, a aplicacao (CLI ou web) nao sobe
    (selecionar_motor levanta ErroConversao). pypdfium2 precisa ser dependencia
    obrigatoria, nao so um extra, e o extra `web` precisa garantir um motor."""

    @classmethod
    def setUpClass(cls):
        caminho = Path(__file__).resolve().parent / "pyproject.toml"
        cls.pyproject = tomllib.loads(caminho.read_text(encoding="utf-8"))

    def test_pypdfium2_e_dependencia_obrigatoria(self):
        obrigatorias = self.pyproject["project"]["dependencies"]
        self.assertTrue(
            any("pypdfium2" in dep for dep in obrigatorias),
            f"pypdfium2 deveria estar em [project.dependencies], visto: {obrigatorias}",
        )

    def test_extra_web_garante_um_motor_disponivel(self):
        web = self.pyproject["project"]["optional-dependencies"]["web"]
        self.assertTrue(
            any("pdf-to-md[simples]" in dep or "pypdfium2" in dep for dep in web),
            f"extra 'web' deveria trazer o motor 'simples' de brinde, visto: {web}",
        )

    def test_versao_e_dinamica_a_partir_de_pdf_to_md_dunder_version(self):
        """BUG-15: a versao chegou a estar declarada em 4 lugares
        independentes (pyproject.toml, __version__, um comentario e o HTML do
        frontend) - uma unica fonte de verdade evita que voltem a divergir."""
        projeto = self.pyproject["project"]
        self.assertNotIn(
            "version", projeto,
            "version estatica em [project] junto com dynamic=['version'] "
            "e invalido (pip rejeita o build)",
        )
        self.assertIn("version", projeto.get("dynamic", []))
        self.assertEqual(
            self.pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "pdf_to_md.__version__",
        )

    def test_extra_dev_traz_o_necessario_para_rodar_backend_tests(self):
        """BUG-16: dependencies.md recomendava `pip install ".[docling,dev]"`
        para rodar backend/tests/, mas `dev` so trazia fpdf2 - sem fastapi
        (backend/tests/test_app.py) nem httpx2 (exigido pelo proprio
        starlette.testclient em tempo de import), a colecao dos testes
        falhava antes mesmo de rodar um so caso."""
        dev = self.pyproject["project"]["optional-dependencies"]["dev"]
        self.assertTrue(any("fpdf2" in dep for dep in dev))
        self.assertTrue(
            any("pdf-to-md[web]" in dep or "fastapi" in dep for dep in dev),
            f"extra 'dev' precisa trazer fastapi (para backend/tests/test_app.py), visto: {dev}",
        )
        self.assertTrue(
            any("httpx2" in dep for dep in dev),
            f"extra 'dev' precisa trazer httpx2 (exigido por starlette.testclient), visto: {dev}",
        )

    def test_frontend_nao_tem_versao_fixa_no_html(self):
        html = (Path(__file__).resolve().parent / "frontend" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("v3.0", html)
        self.assertIn('id="app-version"', html)


# ---------------------------------------------------------------------------
# 7. CLI ponta a ponta (motor real pypdfium2)
# ---------------------------------------------------------------------------
class TestCLI(BaseTemp):
    def _rodar(self, *args) -> int:
        return m.main(["--engine", "simples", "-q", *args])

    def test_conversao_real_de_um_pdf(self):
        pdf = gerar_pdf(self.tmp / "doc.pdf", ["Relatorio SECINTEL", "Linha dois"])
        self.assertEqual(self._rodar("-i", str(pdf)), m.EXIT_OK)
        md = (self.tmp / "doc.md").read_text(encoding="utf-8")
        self.assertIn("Relatorio SECINTEL", md)

    def test_multiplas_paginas_separadas(self):
        pdf = gerar_pdf(self.tmp / "multi.pdf", ["Conteudo"], paginas=3)
        self.assertEqual(self._rodar("-i", str(pdf)), m.EXIT_OK)
        md = (self.tmp / "multi.md").read_text(encoding="utf-8")
        self.assertEqual(md.count("---"), 2)
        self.assertIn("(p3)", md)

    def test_lote_recursivo_espelha_estrutura(self):
        gerar_pdf(self.tmp / "in" / "a.pdf", ["A"])
        gerar_pdf(self.tmp / "in" / "n1" / "b.pdf", ["B"])
        rc = self._rodar("-i", str(self.tmp / "in"), "-o", str(self.tmp / "out"), "-r")
        self.assertEqual(rc, m.EXIT_OK)
        self.assertTrue((self.tmp / "out" / "a.md").exists())
        self.assertTrue((self.tmp / "out" / "n1" / "b.md").exists())

    def test_entrada_inexistente_retorna_falha(self):
        self.assertEqual(self._rodar("-i", str(self.tmp / "nada.pdf")), m.EXIT_FALHA)

    def test_threads_invalidas_retorna_uso(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        self.assertEqual(self._rodar("-i", str(pdf), "--threads", "0"), m.EXIT_USO)

    def test_timeout_invalido_retorna_uso(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        self.assertEqual(self._rodar("-i", str(pdf), "--timeout", "0"), m.EXIT_USO)

    def test_arquivo_alvo_com_multiplas_entradas_e_erro(self):
        gerar_pdf(self.tmp / "in" / "a.pdf", ["A"])
        gerar_pdf(self.tmp / "in" / "b.pdf", ["B"])
        rc = self._rodar("-i", str(self.tmp / "in"), "-o", str(self.tmp / "unico.md"))
        self.assertEqual(rc, m.EXIT_FALHA)

    def test_dry_run_nao_cria_arquivos(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        self.assertEqual(self._rodar("-i", str(pdf), "--dry-run"), m.EXIT_OK)
        self.assertFalse((self.tmp / "a.md").exists())

    def test_segunda_execucao_pula_e_overwrite_refaz(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        self.assertEqual(self._rodar("-i", str(pdf)), m.EXIT_OK)
        marca = (self.tmp / "a.md")
        marca.write_text("MARCA", encoding="utf-8")
        self.assertEqual(self._rodar("-i", str(pdf)), m.EXIT_OK)
        self.assertEqual(marca.read_text(encoding="utf-8"), "MARCA")
        self.assertEqual(self._rodar("-i", str(pdf), "--overwrite"), m.EXIT_OK)
        self.assertNotEqual(marca.read_text(encoding="utf-8"), "MARCA")

    def test_pdf_sem_texto_reporta_erro(self):
        from fpdf import FPDF

        pdf = FPDF()
        pdf.add_page()
        alvo = self.tmp / "vazio.pdf"
        pdf.output(str(alvo))
        self.assertEqual(self._rodar("-i", str(alvo)), m.EXIT_FALHA)

    def test_pdf_corrompido_nao_derruba(self):
        ruim = self.tmp / "ruim.pdf"
        ruim.write_bytes(b"%PDF-1.4 lixo nao e um pdf valido")
        self.assertEqual(self._rodar("-i", str(ruim)), m.EXIT_FALHA)

    def test_acentuacao_utf8_preservada(self):
        pdf = gerar_pdf(self.tmp / "acentos.pdf", ["Configuracao de rede - Sao Paulo"])
        self.assertEqual(self._rodar("-i", str(pdf)), m.EXIT_OK)
        md = (self.tmp / "acentos.md").read_text(encoding="utf-8")
        self.assertIn("Sao Paulo", md)

    def test_nome_com_espacos(self):
        pdf = gerar_pdf(self.tmp / "meu relatorio final.pdf", ["Texto"])
        self.assertEqual(self._rodar("-i", str(pdf)), m.EXIT_OK)
        self.assertTrue((self.tmp / "meu relatorio final.md").exists())

    def test_interativo_sem_entrada_retorna_uso(self):
        original = sys.stdin
        sys.stdin = io.StringIO("\n")
        try:
            self.assertEqual(m.main(["-q", "--engine", "simples"]), m.EXIT_USO)
        finally:
            sys.stdin = original

    def test_interativo_converte(self):
        pdf = gerar_pdf(self.tmp / "int.pdf", ["Interativo"])
        original = sys.stdin
        sys.stdin = io.StringIO(f'"{pdf}"\n')
        try:
            self.assertEqual(m.main(["-q", "--engine", "simples"]), m.EXIT_OK)
        finally:
            sys.stdin = original
        self.assertIn("Interativo", (self.tmp / "int.md").read_text(encoding="utf-8"))

    def test_lote_com_docling_stub(self):
        instalar_stub_docling(markdown="# Doc\n\ncorpo")
        gerar_pdf(self.tmp / "in" / "a.pdf", ["A"])
        gerar_pdf(self.tmp / "in" / "b.pdf", ["B"])
        rc = m.main(["-q", "--engine", "docling", "-i", str(self.tmp / "in"),
                     "-o", str(self.tmp / "out")])
        self.assertEqual(rc, m.EXIT_OK)
        self.assertIn("# Doc", (self.tmp / "out" / "a.md").read_text(encoding="utf-8"))
        self.assertIn("# Doc", (self.tmp / "out" / "b.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# 8. Regressoes
# ---------------------------------------------------------------------------
class TestRegressoes(BaseTemp):
    def _rodar(self, *args) -> int:
        return m.main(["--engine", "simples", "-q", *args])

    def test_colisao_de_nomes_e_bloqueada(self):
        """Regressao: dois PDFs homonimos sobrescreviam a mesma saida em silencio."""
        a = gerar_pdf(self.tmp / "a" / "contrato.pdf", ["Contrato A"])
        b = gerar_pdf(self.tmp / "b" / "contrato.pdf", ["Contrato B"])
        rc = self._rodar("-i", str(a), str(b), "-o", str(self.tmp / "out"))
        self.assertEqual(rc, m.EXIT_FALHA)
        self.assertFalse((self.tmp / "out" / "contrato.md").exists())

    def test_colisao_nao_dispara_com_subarvores_espelhadas(self):
        gerar_pdf(self.tmp / "in" / "a" / "contrato.pdf", ["A"])
        gerar_pdf(self.tmp / "in" / "b" / "contrato.pdf", ["B"])
        rc = self._rodar("-i", str(self.tmp / "in"), "-o", str(self.tmp / "out"), "-r")
        self.assertEqual(rc, m.EXIT_OK)
        self.assertTrue((self.tmp / "out" / "a" / "contrato.md").exists())
        self.assertTrue((self.tmp / "out" / "b" / "contrato.md").exists())

    def test_lote_parcial_retorna_falha_mas_salva_o_que_deu_certo(self):
        gerar_pdf(self.tmp / "in" / "bom.pdf", ["Bom"])
        (self.tmp / "in" / "ruim.pdf").write_bytes(b"%PDF-1.4 lixo")
        rc = self._rodar("-i", str(self.tmp / "in"), "-o", str(self.tmp / "out"))
        self.assertEqual(rc, m.EXIT_FALHA)
        self.assertTrue((self.tmp / "out" / "bom.md").exists())
        self.assertFalse((self.tmp / "out" / "ruim.md").exists())

    def test_diretorio_vazio_retorna_falha(self):
        vazio = self.tmp / "vazio"
        vazio.mkdir()
        self.assertEqual(self._rodar("-i", str(vazio)), m.EXIT_FALHA)

    def test_pdf_zero_bytes(self):
        alvo = self.tmp / "zero.pdf"
        alvo.write_bytes(b"")
        self.assertEqual(self._rodar("-i", str(alvo)), m.EXIT_FALHA)

    def test_quebras_de_linha_normalizadas(self):
        """Regressao: a camada de texto do PDFium devolvia CRLF."""
        pdf = gerar_pdf(self.tmp / "crlf.pdf", ["Linha um", "Linha dois"], paginas=2)
        self.assertEqual(self._rodar("-i", str(pdf)), m.EXIT_OK)
        bruto = (self.tmp / "crlf.md").read_bytes()
        self.assertNotIn(b"\r", bruto)


# ---------------------------------------------------------------------------
# 9. Aceleracao por GPU
# ---------------------------------------------------------------------------
class TestGPU(BaseTemp):
    def _opcoes(self, cfg: m.Config):
        reg = instalar_stub_docling()
        m.MotorDocling(cfg).converter(gerar_pdf(self.tmp / "a.pdf", ["A"]))
        return reg["opcoes"]

    def test_device_string_repassada_ao_docling(self):
        for dev in ["auto", "cpu", "cuda", "cuda:1", "mps", "xpu"]:
            with self.subTest(dev=dev):
                o = self._opcoes(m.Config(device=dev))
                self.assertEqual(o.accelerator_options.device, dev)

    def test_backend_do_rapidocr_repassado(self):
        o = self._opcoes(m.Config(ocr_backend="openvino"))
        self.assertEqual(o.ocr_options.backend, "openvino")

    def test_backend_padrao_onnxruntime(self):
        self.assertEqual(self._opcoes(m.Config()).ocr_options.backend, "onnxruntime")

    def test_threads_acompanham_o_dispositivo(self):
        o = self._opcoes(m.Config(device="cuda", threads=12))
        self.assertEqual(o.accelerator_options.num_threads, 12)

    def test_regex_de_device_aceita_validos(self):
        for dev in ["auto", "cpu", "cuda", "cuda:0", "cuda:3", "mps", "xpu"]:
            self.assertTrue(m.DEVICE_VALIDO.match(dev), dev)

    def test_regex_de_device_rejeita_invalidos(self):
        for dev in ["gpu", "directml", "cuda:", "cuda:x", "rocm", "CUDA", ""]:
            self.assertFalse(m.DEVICE_VALIDO.match(dev), dev)

    def test_cli_rejeita_device_invalido(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        rc = m.main(["-q", "--engine", "simples", "-i", str(pdf), "--device", "directml"])
        self.assertEqual(rc, m.EXIT_USO)

    def test_cli_aceita_cuda_indexado(self):
        instalar_stub_docling()
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        rc = m.main(["-q", "--engine", "docling", "-i", str(pdf), "--device", "cuda:1"])
        self.assertEqual(rc, m.EXIT_OK)

    def test_hardware_retorna_ok(self):
        self.assertEqual(m.main(["--hardware"]), m.EXIT_OK)


# ---------------------------------------------------------------------------
# 10. Pre-checagem de paginas (--max-pages)
# ---------------------------------------------------------------------------
class TestMaxPages(BaseTemp):
    def test_bloqueia_pdf_com_mais_paginas_que_o_limite(self):
        pdf = gerar_pdf(self.tmp / "grande.pdf", ["linha"], paginas=5)
        saida = self.tmp / "grande.md"
        rc = m.main(["--engine", "simples", "-q", "-i", str(pdf), "--max-pages", "3"])
        self.assertEqual(rc, m.EXIT_FALHA)
        self.assertFalse(saida.exists())

    def test_permite_pdf_dentro_do_limite(self):
        pdf = gerar_pdf(self.tmp / "pequeno.pdf", ["linha"], paginas=2)
        saida = self.tmp / "pequeno.md"
        rc = m.main(["--engine", "simples", "-q", "-i", str(pdf), "--max-pages", "3"])
        self.assertEqual(rc, m.EXIT_OK)
        self.assertTrue(saida.exists())

    def test_max_pages_invalido_retorna_uso(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        rc = m.main(["--engine", "simples", "-q", "-i", str(pdf), "--max-pages", "0"])
        self.assertEqual(rc, m.EXIT_USO)

    def test_pdf_corrompido_com_max_pages_ativo_e_bloqueado_pela_pre_checagem(self):
        """BUG-04: PDF ilegivel pelo pypdfium2 (nao "biblioteca ausente") tinha o
        --max-pages descartado (contar_paginas devolvia None nos dois casos),
        entao o arquivo seguia para o motor pesado mesmo acima do limite
        pretendido. Com --max-pages ativo, PDF ilegivel agora e bloqueado pela
        propria pre-checagem, sem chegar a acionar o motor real."""
        ruim = self.tmp / "ruim.pdf"
        ruim.write_bytes(b"%PDF-1.4 lixo nao e um pdf valido")
        saida = self.tmp / "ruim.md"
        motor = MotorFake()
        cfg = m.Config(max_pages=1)

        resultado = m.converter_arquivo(ruim, saida, motor, cfg)

        self.assertEqual(resultado.status, "erro")
        self.assertIn("pre-checar", resultado.mensagem)
        self.assertEqual(motor.chamadas, [])
        self.assertFalse(saida.exists())

    def test_pdf_corrompido_sem_max_pages_ainda_falha_por_conta_do_motor(self):
        """Sem --max-pages, contar_paginas nunca e chamado - PdfIlegivel so
        existe para o caminho ativado por cfg.max_pages."""
        ruim = self.tmp / "ruim.pdf"
        ruim.write_bytes(b"%PDF-1.4 lixo nao e um pdf valido")
        rc = m.main(["--engine", "simples", "-q", "-i", str(ruim)])
        self.assertEqual(rc, m.EXIT_FALHA)

    def test_contar_paginas_sem_pypdfium2_retorna_none_e_avisa_uma_vez(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        anterior = sys.modules.get("pypdfium2")
        sys.modules["pypdfium2"] = None  # forca ImportError na proxima importacao
        m._AVISO_MAX_PAGES_EMITIDO = False
        try:
            self.assertIsNone(m.contar_paginas(pdf))
        finally:
            if anterior is not None:
                sys.modules["pypdfium2"] = anterior
            else:
                del sys.modules["pypdfium2"]
            m._AVISO_MAX_PAGES_EMITIDO = False


# ---------------------------------------------------------------------------
# 11. Paralelismo de lote (--jobs)
# ---------------------------------------------------------------------------
class TestJobs(BaseTemp):
    def _gerar_lote(self, n=6):
        for i in range(n):
            gerar_pdf(self.tmp / "in" / f"doc{i}.pdf", [f"conteudo {i}"])

    def test_jobs_paralelo_gera_mesmo_resultado_que_sequencial(self):
        self._gerar_lote()
        out_seq, out_par = self.tmp / "seq", self.tmp / "par"
        rc1 = m.main(["--engine", "simples", "-q", "-i", str(self.tmp / "in"),
                      "-o", str(out_seq), "--jobs", "1"])
        rc2 = m.main(["--engine", "simples", "-q", "-i", str(self.tmp / "in"),
                      "-o", str(out_par), "--jobs", "4"])
        self.assertEqual(rc1, m.EXIT_OK)
        self.assertEqual(rc2, m.EXIT_OK)
        for i in range(6):
            self.assertEqual(
                (out_seq / f"doc{i}.md").read_text(encoding="utf-8"),
                (out_par / f"doc{i}.md").read_text(encoding="utf-8"),
            )

    def test_jobs_invalido_retorna_uso(self):
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        rc = m.main(["--engine", "simples", "-q", "-i", str(pdf), "--jobs", "0"])
        self.assertEqual(rc, m.EXIT_USO)

    def test_jobs_ignorado_para_docling_com_aviso(self):
        instalar_stub_docling()
        pdf = gerar_pdf(self.tmp / "a.pdf", ["A"])
        with self.assertLogs("pdf2md", level="WARNING") as captura:
            rc = m.main(["--engine", "docling", "-i", str(pdf), "--jobs", "4"])
        self.assertEqual(rc, m.EXIT_OK)
        self.assertTrue(any("ignorado" in msg for msg in captura.output))
