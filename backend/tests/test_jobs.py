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


class TestProcessarResultadoPulado(unittest.TestCase):
    """BUG-11: converter_arquivo() pode devolver status "pulado" (saida.md ja
    existia, overwrite desligado) - _processar() jogava isso no mesmo "else"
    de "erro", reportando "ja existe (use --overwrite)" como falha para um
    resultado que, do ponto de vista do usuario, e sucesso (o arquivo que ele
    queria ja esta la). Nao ha reenfileiramento hoje pela API publica -
    _processar() e chamado diretamente para exercitar o caso, como uma
    segunda passada hipotetica repetiria."""

    def setUp(self):
        self._motor_original = motor_pool._motor
        self._cfg_original = motor_pool._cfg
        motor_pool._motor = _MotorDeTeste()
        motor_pool._cfg = m.Config()  # overwrite=False por padrao

    def tearDown(self):
        motor_pool._motor = self._motor_original
        motor_pool._cfg = self._cfg_original

    def test_segunda_passada_com_saida_ja_existente_conta_como_concluido(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = jobs.criar_job("a.pdf", b"%PDF-1.4", diretorio=Path(tmp))
            jobs._processar(job.id)
            self.assertEqual(job.status, "concluido")

            job.status = "na_fila"  # simula um reprocessamento do mesmo job
            jobs._processar(job.id)

            self.assertEqual(job.status, "concluido")
            self.assertEqual(job.mensagem_erro, "")
            self.assertIsNotNone(job.caminho_saida)


class TestFilaSobreviveAErroInesperado(unittest.TestCase):
    """BUG-01: uma excecao nao tratada (ex.: motor_pool.obter_motor() RuntimeError)
    nao pode matar a thread worker nem travar a fila para sempre."""

    def setUp(self):
        self._obter_motor_original = motor_pool.obter_motor
        self._cfg_original = motor_pool._cfg
        motor_pool._cfg = m.Config()
        jobs.iniciar_worker()

    def tearDown(self):
        jobs.parar_worker()
        motor_pool.obter_motor = self._obter_motor_original
        motor_pool._cfg = self._cfg_original

    def test_falha_inesperada_no_meio_da_fila_nao_derruba_o_worker(self):
        motor_ok = _MotorDeTeste()
        chamadas = {"n": 0}

        def obter_motor_com_falha_no_segundo():
            chamadas["n"] += 1
            if chamadas["n"] == 2:
                raise RuntimeError("motor indisponivel (falha simulada)")
            return motor_ok

        motor_pool.obter_motor = obter_motor_com_falha_no_segundo

        with tempfile.TemporaryDirectory() as tmp:
            a = jobs.criar_job("a.pdf", b"%PDF-1.4", diretorio=Path(tmp))
            b = jobs.criar_job("b.pdf", b"%PDF-1.4", diretorio=Path(tmp))
            c = jobs.criar_job("c.pdf", b"%PDF-1.4", diretorio=Path(tmp))

            jobs.enfileirar(a.id)
            jobs.enfileirar(b.id)
            jobs.enfileirar(c.id)

            self.assertTrue(_aguardar(lambda: c.status == "concluido"))

            self.assertEqual(a.status, "concluido")
            self.assertEqual(b.status, "erro")
            self.assertEqual(b.mensagem_erro, "falha interna ao processar")
            self.assertEqual(c.status, "concluido")
            self.assertTrue(jobs._worker_thread.is_alive())


class TestOrdemDeAtribuicaoDoJob(_ComMotorStub):
    """BUG-06: _processar() setava job.status ANTES do dado que o status
    prometia (job.status = 'concluido' antes de job.caminho_saida = ...; o
    mesmo padrao valia para 'processando' antes de job.iniciado_em). Uma
    thread HTTP podia observar o status novo com o dado ainda None. A corrida
    em si nao e deterministicamente reproduzivel (ver bugs.md) - este teste
    faz o melhor esforco possivel sem sleep/instrumentacao em codigo de
    producao: usa o atraso ja existente do motor stub de teste para dar
    varias janelas de poll durante o processamento real."""

    falha = False

    def setUp(self):
        super().setUp()
        self.motor._atraso = 0.2

    def test_status_nunca_e_observado_antes_do_dado_correspondente(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = jobs.criar_job("a.pdf", b"%PDF-1.4", diretorio=Path(tmp))
            jobs.enfileirar(job.id)

            violacoes = []
            limite = time.monotonic() + 2.0
            while time.monotonic() < limite:
                status = job.status
                if status == "processando" and job.iniciado_em is None:
                    violacoes.append("processando sem iniciado_em")
                if status == "concluido" and job.caminho_saida is None:
                    violacoes.append("concluido sem caminho_saida")
                if status == "concluido":
                    break
                time.sleep(0.005)
            else:
                self.fail("job nao concluiu dentro do timeout")

            self.assertEqual(violacoes, [])
            self.assertIsNotNone(job.caminho_saida)


class TestRemoverSeNaoProcessando(unittest.TestCase):
    """BUG-12: TOCTOU entre checar job.status == 'processando' e chamar
    jobs.remover() como duas operacoes separadas - o worker podia comecar a
    processar o job nesse intervalo, e o remover() subsequente apagava o PDF
    por baixo de uma conversao em andamento (e tirava o job do store
    enquanto o worker ainda estava mutando o mesmo objeto Job)."""

    def test_recusa_remover_job_processando_e_nao_apaga_nada(self):
        store = jobs.JobStore()
        job = jobs.Job(id="1", nome_original="a.pdf", caminho_pdf=Path("a.pdf"), status="processando")
        store.adicionar(job)
        self.assertIsNone(store.remover_se_nao_processando("1"))
        self.assertIsNotNone(store.obter("1"))

    def test_remove_job_que_nao_esta_processando(self):
        store = jobs.JobStore()
        job = jobs.Job(id="1", nome_original="a.pdf", caminho_pdf=Path("a.pdf"), status="na_fila")
        store.adicionar(job)
        removido = store.remover_se_nao_processando("1")
        self.assertIs(removido, job)
        self.assertIsNone(store.obter("1"))

    def test_job_inexistente_devolve_none(self):
        self.assertIsNone(jobs.JobStore().remover_se_nao_processando("nao-existe"))


class TestRemoverSeNaoProcessandoSobContencao(unittest.TestCase):
    """Melhor esforco sob concorrencia real (worker + stub motor com atraso),
    no mesmo espirito do teste de BUG-06: nao garante disparar a janela em
    toda execucao, mas adiciona sinal real sem sleep/instrumentacao em
    codigo de producao."""

    def setUp(self):
        self._motor_original = motor_pool._motor
        self._cfg_original = motor_pool._cfg
        self.motor = _MotorDeTeste(atraso=0.03)
        motor_pool._motor = self.motor
        motor_pool._cfg = m.Config()
        jobs.iniciar_worker()

    def tearDown(self):
        jobs.parar_worker()
        motor_pool._motor = self._motor_original
        motor_pool._cfg = self._cfg_original

    def test_delete_concorrente_com_worker_nunca_apaga_pdf_de_job_processando(self):
        violacoes = []
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(30):
                job = jobs.criar_job(f"a{i}.pdf", b"%PDF-1.4", diretorio=Path(tmp))
                jobs.enfileirar(job.id)
                time.sleep(0.005)  # deixa o worker ter uma chance real de pegar o job

                removido = jobs.remover_se_nao_processando(job.id)
                if removido is None:
                    atual = jobs.obter_store().obter(job.id)
                    if atual is not None and atual.status == "processando" and not job.caminho_pdf.exists():
                        violacoes.append((i, "job processando com PDF ja apagado"))
                time.sleep(0.04)  # deixa o job em andamento terminar antes do proximo

        self.assertEqual(violacoes, [])


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


class TestRemover(unittest.TestCase):
    def setUp(self):
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmp_ctx.name)
        self._store_original = jobs._store
        jobs._store = jobs.JobStore()

    def tearDown(self):
        jobs._store = self._store_original
        self._tmp_ctx.cleanup()

    def _adicionar(self, job_id: str, status: str, *, com_saida: bool = False) -> jobs.Job:
        caminho_pdf = self._tmp / f"{job_id}.pdf"
        caminho_pdf.write_bytes(b"%PDF-1.4")
        caminho_md = None
        if com_saida:
            caminho_md = self._tmp / f"{job_id}.md"
            caminho_md.write_text("# ola\n", encoding="utf-8")
        job = jobs.Job(
            id=job_id, nome_original=f"{job_id}.pdf", caminho_pdf=caminho_pdf,
            status=status, caminho_saida=caminho_md,
        )
        jobs.obter_store().adicionar(job)
        return job

    def test_remover_apaga_pdf_e_md_e_tira_do_store(self):
        job = self._adicionar("j1", "concluido", com_saida=True)
        jobs.remover("j1")
        self.assertIsNone(jobs.obter_store().obter("j1"))
        self.assertFalse(job.caminho_pdf.exists())
        self.assertFalse(job.caminho_saida.exists())

    def test_remover_id_inexistente_nao_leva_excecao(self):
        jobs.remover("nao-existe")  # nao deve levantar

    def test_remover_job_sem_saida_so_apaga_o_pdf(self):
        job = self._adicionar("j1", "erro", com_saida=False)
        jobs.remover("j1")
        self.assertFalse(job.caminho_pdf.exists())

    def test_limpar_finalizados_remove_concluido_e_erro_mas_preserva_o_resto(self):
        self._adicionar("concluido", "concluido", com_saida=True)
        self._adicionar("erro", "erro")
        na_fila = self._adicionar("na_fila", "na_fila")
        processando = self._adicionar("processando", "processando")
        removidos = jobs.limpar_finalizados()
        self.assertEqual(removidos, 2)
        ids_restantes = {j.id for j in jobs.obter_store().listar()}
        self.assertEqual(ids_restantes, {na_fila.id, processando.id})


if __name__ == "__main__":
    unittest.main()
