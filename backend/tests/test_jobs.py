#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sanity check de webapp.jobs: criacao de job (Step 3) e fila/worker sequencial (Step 4)."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pdf_to_md as m  # noqa: E402

from backend.src.services import jobs, motor_pool  # noqa: E402


def _aguardar(condicao, timeout: float = 2.0) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicao():
            return True
        time.sleep(0.01)
    return condicao()


def _gerar_pdf_valido(caminho: Path, paginas: int = 1) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    for _ in range(paginas):
        pdf.add_page()
        pdf.set_font("helvetica", size=12)
        pdf.cell(0, 10, "conteudo de teste", new_x="LMARGIN", new_y="NEXT")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(caminho))


class _MotorDeTeste(m.MotorBase):
    """Motor stub: registra a ordem de chamadas, sem tocar em Docling/pypdfium2."""

    nome = "stub"

    def __init__(self, *, falha: bool = False, atraso: float = 0.05):
        self.ordem: list[str] = []
        self._falha = falha
        self._atraso = atraso

    def disponivel(self):
        return True, ""

    def converter(self, pdf: Path) -> str:
        self.ordem.append(pdf.name)
        time.sleep(self._atraso)
        if self._falha:
            raise m.ErroConversao("falha proposital do stub")
        return "# conteudo\n"


class TestCriarJob(unittest.TestCase):
    def test_grava_pdf_em_disco_e_registra_com_status_na_fila(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = jobs.criar_job("relatorio.pdf", b"%PDF-1.4 conteudo", diretorio=Path(tmp))

            self.assertEqual(job.status, "na_fila")
            self.assertEqual(job.nome_original, "relatorio.pdf")
            self.assertTrue(job.caminho_pdf.exists())
            self.assertEqual(job.caminho_pdf.read_bytes(), b"%PDF-1.4 conteudo")
            self.assertEqual(jobs.obter_store().obter(job.id), job)

    def test_ids_gerados_sao_unicos(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = jobs.criar_job("a.pdf", b"x", diretorio=Path(tmp))
            b = jobs.criar_job("b.pdf", b"y", diretorio=Path(tmp))
            self.assertNotEqual(a.id, b.id)


class TestJobStore(unittest.TestCase):
    def test_listar_devolve_todos_os_jobs_adicionados(self):
        store = jobs.JobStore()
        j1 = jobs.Job(id="1", nome_original="a.pdf", caminho_pdf=Path("a.pdf"))
        j2 = jobs.Job(id="2", nome_original="b.pdf", caminho_pdf=Path("b.pdf"))
        store.adicionar(j1)
        store.adicionar(j2)
        self.assertCountEqual(store.listar(), [j1, j2])

    def test_obter_de_id_inexistente_devolve_none(self):
        self.assertIsNone(jobs.JobStore().obter("nao-existe"))


class _ComMotorStub(unittest.TestCase):
    """Base: substitui o motor do motor_pool por um stub e sobe/derruba o worker."""

    falha = False

    def setUp(self):
        self._motor_original = motor_pool._motor
        self._cfg_original = motor_pool._cfg
        self._ema_original = jobs._segundos_por_pagina
        self.motor = _MotorDeTeste(falha=self.falha)
        motor_pool._motor = self.motor
        motor_pool._cfg = m.Config()
        jobs.iniciar_worker()

    def tearDown(self):
        jobs.parar_worker()
        motor_pool._motor = self._motor_original
        motor_pool._cfg = self._cfg_original
        jobs._segundos_por_pagina = self._ema_original


class TestFilaProcessamentoOk(_ComMotorStub):
    def test_dois_jobs_processam_em_serie_na_ordem_de_chegada(self):
        with tempfile.TemporaryDirectory() as tmp:
            j1 = jobs.criar_job("a.pdf", b"%PDF-1.4", diretorio=Path(tmp))
            j2 = jobs.criar_job("b.pdf", b"%PDF-1.4", diretorio=Path(tmp))

            # enfileirar() nunca bloqueia - as duas chamadas voltam na hora,
            # mesmo com o worker ainda processando o primeiro job.
            jobs.enfileirar(j1.id)
            jobs.enfileirar(j2.id)

            self.assertTrue(_aguardar(lambda: j2.status == "concluido"))

            self.assertEqual(j1.status, "concluido")
            self.assertEqual(j2.status, "concluido")
            self.assertEqual(j1.caminho_saida, j1.caminho_pdf.with_suffix(".md"))
            self.assertEqual(j1.caminho_saida.read_text(encoding="utf-8"), "# conteudo\n")
            # prova a ordem serial: b so comeca depois que a termina de "processar".
            self.assertEqual(self.motor.ordem, [f"{j1.id}.pdf", f"{j2.id}.pdf"])


class TestFilaProcessamentoErro(_ComMotorStub):
    falha = True

    def test_job_com_erro_de_conversao_fica_com_status_erro(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = jobs.criar_job("ruim.pdf", b"%PDF-1.4", diretorio=Path(tmp))
            jobs.enfileirar(job.id)

            self.assertTrue(_aguardar(lambda: job.status == "erro"))
            self.assertIn("falha proposital do stub", job.mensagem_erro)
            self.assertIsNone(job.caminho_saida)


class TestFilaAtualizaProgresso(_ComMotorStub):
    """Step 5, ponta a ponta pelo worker real: paginas_totais e a media movel."""

    def test_job_concluido_preenche_paginas_totais_e_progresso_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho_pdf = Path(tmp) / "fonte.pdf"
            _gerar_pdf_valido(caminho_pdf, paginas=2)

            job = jobs.criar_job(
                "relatorio.pdf", caminho_pdf.read_bytes(), diretorio=Path(tmp) / "uploads"
            )
            self.assertEqual(job.paginas_totais, 2)

            jobs.enfileirar(job.id)
            self.assertTrue(_aguardar(lambda: job.status == "concluido"))

            self.assertGreater(job.segundos, 0)
            dados = job.to_dict()
            self.assertEqual(dados["pagina_estimada"], 2.0)
            self.assertFalse(dados["estimado"])


class TestCriarJobPaginas(unittest.TestCase):
    def test_paginas_totais_preenchido_via_contar_paginas(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho_pdf = Path(tmp) / "fonte.pdf"
            _gerar_pdf_valido(caminho_pdf, paginas=3)
            job = jobs.criar_job(
                "doc.pdf", caminho_pdf.read_bytes(), diretorio=Path(tmp) / "uploads"
            )
            self.assertEqual(job.paginas_totais, 3)

    def test_pdf_invalido_deixa_paginas_totais_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = jobs.criar_job("ruim.pdf", b"nao e um pdf de verdade", diretorio=Path(tmp))
            self.assertIsNone(job.paginas_totais)


class TestProgressoDict(unittest.TestCase):
    def setUp(self):
        self._ema_original = jobs._segundos_por_pagina
        jobs._segundos_por_pagina = 2.0

    def tearDown(self):
        jobs._segundos_por_pagina = self._ema_original

    def test_na_fila_reporta_total_de_paginas_sem_estimativa(self):
        job = jobs.Job(id="1", nome_original="a.pdf", caminho_pdf=Path("a.pdf"), paginas_totais=10)
        dados = job.to_dict()
        self.assertIsNone(dados["pagina_estimada"])
        self.assertFalse(dados["estimado"])
        self.assertEqual(dados["paginas_totais"], 10)

    def test_processando_estima_pagina_por_tempo_decorrido(self):
        agora = datetime.now(timezone.utc)
        job = jobs.Job(
            id="1", nome_original="a.pdf", caminho_pdf=Path("a.pdf"),
            status="processando", paginas_totais=10,
            iniciado_em=agora - timedelta(seconds=4),
        )
        dados = job.to_dict()
        self.assertTrue(dados["estimado"])
        self.assertAlmostEqual(dados["pagina_estimada"], 2.0, delta=0.3)  # 4s / 2s-por-pagina

    def test_estimativa_nunca_ultrapassa_o_total_de_paginas(self):
        agora = datetime.now(timezone.utc)
        job = jobs.Job(
            id="1", nome_original="a.pdf", caminho_pdf=Path("a.pdf"),
            status="processando", paginas_totais=3,
            iniciado_em=agora - timedelta(seconds=100),
        )
        dados = job.to_dict()
        self.assertEqual(dados["pagina_estimada"], 3.0)

    def test_concluido_reporta_pagina_estimada_no_total_sem_marcar_como_estimativa(self):
        job = jobs.Job(
            id="1", nome_original="a.pdf", caminho_pdf=Path("a.pdf"),
            status="concluido", paginas_totais=5,
        )
        dados = job.to_dict()
        self.assertEqual(dados["pagina_estimada"], 5.0)
        self.assertFalse(dados["estimado"])

    def test_erro_reporta_mensagem_e_sem_pagina_estimada(self):
        job = jobs.Job(
            id="1", nome_original="a.pdf", caminho_pdf=Path("a.pdf"),
            status="erro", mensagem_erro="falhou feio",
        )
        dados = job.to_dict()
        self.assertIsNone(dados["pagina_estimada"])
        self.assertEqual(dados["mensagem_erro"], "falhou feio")


class TestAtualizarEstimativa(unittest.TestCase):
    def setUp(self):
        self._ema_original = jobs._segundos_por_pagina

    def tearDown(self):
        jobs._segundos_por_pagina = self._ema_original

    def test_media_movel_se_aproxima_do_tempo_observado(self):
        jobs._segundos_por_pagina = 2.0
        job = jobs.Job(
            id="1", nome_original="a.pdf", caminho_pdf=Path("a.pdf"),
            paginas_totais=10, segundos=10.0,  # 1s/pagina observado
        )
        jobs._atualizar_estimativa(job)
        # alpha=0.3: nova = 0.3*1.0 + 0.7*2.0 = 1.7
        self.assertAlmostEqual(jobs._segundos_por_pagina, 1.7, places=4)

    def test_sem_paginas_totais_nao_altera_a_media(self):
        jobs._segundos_por_pagina = 2.0
        job = jobs.Job(
            id="1", nome_original="a.pdf", caminho_pdf=Path("a.pdf"),
            paginas_totais=None, segundos=5.0,
        )
        jobs._atualizar_estimativa(job)
        self.assertEqual(jobs._segundos_por_pagina, 2.0)


class TestPosicaoNaFila(unittest.TestCase):
    def setUp(self):
        self._store_original = jobs._store
        jobs._store = jobs.JobStore()

    def tearDown(self):
        jobs._store = self._store_original

    def test_posicoes_seguem_ordem_de_criacao_entre_os_na_fila(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = jobs.criar_job("a.pdf", b"x", diretorio=Path(tmp))
            b = jobs.criar_job("b.pdf", b"y", diretorio=Path(tmp))
            c = jobs.criar_job("c.pdf", b"z", diretorio=Path(tmp))
            b.status = "processando"  # sai da contagem de na_fila

            posicoes = jobs._posicoes_na_fila()
            self.assertEqual(posicoes[a.id], 1)
            self.assertEqual(posicoes[c.id], 2)
            self.assertNotIn(b.id, posicoes)

    def test_listar_com_progresso_inclui_posicao_so_para_na_fila(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs.criar_job("a.pdf", b"x", diretorio=Path(tmp))
            lista = jobs.listar_com_progresso()
            self.assertEqual(lista[0]["posicao_na_fila"], 1)

    def test_obter_com_progresso_de_id_inexistente_devolve_none(self):
        self.assertIsNone(jobs.obter_com_progresso("nao-existe"))


class TestJobsConcluidos(unittest.TestCase):
    def setUp(self):
        self._store_original = jobs._store
        jobs._store = jobs.JobStore()

    def tearDown(self):
        jobs._store = self._store_original

    def _adicionar(self, job_id: str, status: str, *, com_saida: bool = True) -> jobs.Job:
        job = jobs.Job(
            id=job_id,
            nome_original=f"{job_id}.pdf",
            caminho_pdf=Path(f"{job_id}.pdf"),
            status=status,
            caminho_saida=Path(f"{job_id}.md") if com_saida else None,
        )
        jobs.obter_store().adicionar(job)
        return job

    def test_so_devolve_jobs_com_status_concluido(self):
        ok = self._adicionar("ok", "concluido")
        self._adicionar("fila", "na_fila")
        self._adicionar("proc", "processando")
        self._adicionar("erro", "erro")
        self.assertEqual(jobs.jobs_concluidos(), [ok])

    def test_ignora_concluido_sem_caminho_de_saida(self):
        self._adicionar("sem-saida", "concluido", com_saida=False)
        self.assertEqual(jobs.jobs_concluidos(), [])

    def test_filtra_por_ids_informados(self):
        a = self._adicionar("a", "concluido")
        self._adicionar("b", "concluido")
        self.assertEqual(jobs.jobs_concluidos([a.id]), [a])

    def test_ids_pendentes_ou_inexistentes_sao_ignorados(self):
        self._adicionar("a", "na_fila")
        self.assertEqual(jobs.jobs_concluidos(["a", "nao-existe"]), [])


if __name__ == "__main__":
    unittest.main()
