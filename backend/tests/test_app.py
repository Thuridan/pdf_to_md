#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sanity check do app FastAPI: sobe reaproveitando pdf_to_md.py e inicializa o motor_pool no startup."""

from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import tracemalloc
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pdf_to_md as m  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from starlette.datastructures import UploadFile  # noqa: E402

from backend.src.routes import api  # noqa: E402
from backend.src.services import jobs, motor_pool  # noqa: E402
from backend.src.app import app  # noqa: E402


class TestApp(unittest.TestCase):
    def test_health_reporta_ok_e_versao_do_pacote(self):
        with TestClient(app) as cliente:
            resposta = cliente.get("/api/health")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"status": "ok", "version": m.__version__})

    def test_motor_e_selecionado_no_startup_via_lifespan(self):
        with TestClient(app) as cliente:
            resposta = cliente.get("/api/motor")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn(resposta.json()["engine"], ("docling", "simples"))

    def test_motor_devolve_503_se_pool_nao_inicializado(self):
        """BUG-21: motor_pool.obter_motor() levanta RuntimeError se
        inicializar() nao rodou - a rota nao tratava isso, virando 500."""
        with TestClient(app) as cliente:
            # o lifespan (dentro do "with" acima) ja chamou inicializar();
            # desfaz isso DEPOIS, simulando o pool nunca ter sido preenchido.
            motor_original = motor_pool._motor
            cfg_original = motor_pool._cfg
            motor_pool._motor = None
            motor_pool._cfg = None
            try:
                resposta = cliente.get("/api/motor")
                self.assertEqual(resposta.status_code, 503)
            finally:
                motor_pool._motor = motor_original
                motor_pool._cfg = cfg_original

    def test_raiz_serve_o_frontend_estatico(self):
        with TestClient(app) as cliente:
            resposta = cliente.get("/")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("text/html", resposta.headers["content-type"])
        self.assertIn("pdf", resposta.text.lower())

    def test_mount_estatico_nao_esconde_as_rotas_de_api(self):
        # api/* e definida ANTES do mount em "/" - precisa continuar vencendo.
        with TestClient(app) as cliente:
            resposta = cliente.get("/api/health")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["status"], "ok")

    def test_startup_sem_motor_loga_mensagem_acionavel_e_continua_falhando(self):
        """BUG-27 (bugs-2.md): motor_pool.inicializar() pode levantar
        ErroConversao numa instalacao sem nenhum motor (`pip install
        --no-deps`, ambiente quebrado, remocao manual) - improvavel apos o
        BUG-02 (pypdfium2 obrigatorio), mas nao impossivel. O startup deve
        continuar falhando (fail-fast, nao sobe degradado), so com uma
        mensagem que diz o que instalar em vez de so o traceback cru."""
        with patch.object(
            motor_pool, "inicializar", side_effect=m.ErroConversao("nenhum motor")
        ):
            with self.assertLogs("backend.src.app", level="ERROR") as captura:
                with self.assertRaises(m.ErroConversao):
                    with TestClient(app):
                        pass
        self.assertTrue(any("pip install" in msg for msg in captura.output))


class TestCriarJobsEndpoint(unittest.TestCase):
    """Forca engine='simples' no lifespan: os jobs aqui rodam de verdade em
    background (Step 4), e o motor 'simples' e leve/deterministico - nada de
    Docling real (pesado, carrega modelos) disparando durante os testes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir_original = jobs.DIR_UPLOADS
        jobs.DIR_UPLOADS = Path(self._tmp.name)

        self._inicializar_original = motor_pool.inicializar
        self._patcher = patch.object(
            motor_pool, "inicializar",
            lambda cfg=None: self._inicializar_original(m.Config(engine="simples")),
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        jobs.DIR_UPLOADS = self._dir_original
        self._tmp.cleanup()

    def test_upload_de_pdf_valido_cria_job_na_fila(self):
        with TestClient(app) as cliente:
            resposta = cliente.post(
                "/api/jobs",
                files={"files": ("relatorio.pdf", b"%PDF-1.4 conteudo", "application/pdf")},
            )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo["criados"]), 1)
        self.assertEqual(corpo["rejeitados"], [])
        criado = corpo["criados"][0]
        self.assertEqual(criado["status"], "na_fila")
        self.assertEqual(criado["nome_original"], "relatorio.pdf")
        self.assertIsNotNone(jobs.obter_store().obter(criado["id"]))

    def test_upload_de_arquivo_nao_pdf_e_rejeitado(self):
        with TestClient(app) as cliente:
            resposta = cliente.post(
                "/api/jobs",
                files={"files": ("nota.txt", b"nao e pdf", "text/plain")},
            )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["criados"], [])
        self.assertEqual(len(corpo["rejeitados"]), 1)
        self.assertEqual(corpo["rejeitados"][0]["nome_original"], "nota.txt")

    def test_lote_misto_separa_criados_de_rejeitados(self):
        with TestClient(app) as cliente:
            resposta = cliente.post(
                "/api/jobs",
                files=[
                    ("files", ("a.pdf", b"%PDF-1.4", "application/pdf")),
                    ("files", ("b.txt", b"nao e pdf", "text/plain")),
                ],
            )
        corpo = resposta.json()
        self.assertEqual(len(corpo["criados"]), 1)
        self.assertEqual(len(corpo["rejeitados"]), 1)

    def test_listar_jobs_inclui_o_job_recem_criado(self):
        with TestClient(app) as cliente:
            criado = cliente.post(
                "/api/jobs",
                files={"files": ("relatorio.pdf", b"%PDF-1.4", "application/pdf")},
            ).json()["criados"][0]
            resposta = cliente.get("/api/jobs")

        self.assertEqual(resposta.status_code, 200)
        ids = [j["id"] for j in resposta.json()["jobs"]]
        self.assertIn(criado["id"], ids)

    def test_obter_job_por_id_devolve_status_e_progresso(self):
        with TestClient(app) as cliente:
            criado = cliente.post(
                "/api/jobs",
                files={"files": ("relatorio.pdf", b"%PDF-1.4", "application/pdf")},
            ).json()["criados"][0]
            resposta = cliente.get(f"/api/jobs/{criado['id']}")

        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["id"], criado["id"])
        self.assertIn(corpo["status"], ("na_fila", "processando", "concluido", "erro"))
        self.assertIn("pagina_estimada", corpo)
        self.assertIn("estimado", corpo)

    def test_obter_job_inexistente_devolve_404(self):
        with TestClient(app) as cliente:
            resposta = cliente.get("/api/jobs/nao-existe")
        self.assertEqual(resposta.status_code, 404)


class TestGravacaoDeUploadEmDisco(unittest.TestCase):
    """TAREFA-1 (rodada 3): api.criar_jobs() acumulava o upload inteiro em
    bytes (`await arquivo.read()`) antes de gravar em disco - com o teto
    elevado a centenas de MB (TAREFA-2), uploads simultaneos escalam RAM
    linearmente com o tamanho do arquivo, nao com o numero de uploads.
    _gravar_upload_com_teto() agora grava direto em disco em blocos."""

    TAMANHO = 20 * 1024 * 1024  # 20 MiB - grande o bastante pra evidenciar a
    # diferenca (pico antigo ~2x isso; novo pico fica perto de 1 bloco), sem
    # deixar o teste lento.

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir_original = jobs.DIR_UPLOADS
        jobs.DIR_UPLOADS = Path(self._tmp.name)

        self._origem = Path(self._tmp.name) / "origem.bin"
        with self._origem.open("wb") as f:
            restante = self.TAMANHO
            while restante > 0:
                bloco = min(1024 * 1024, restante)
                f.write(b"X" * bloco)
                restante -= bloco

    def tearDown(self):
        jobs.DIR_UPLOADS = self._dir_original
        self._tmp.cleanup()

    def test_gravacao_nao_materializa_o_upload_inteiro_em_memoria(self):
        async def rodar():
            with self._origem.open("rb") as handle:
                arquivo = UploadFile(file=handle, filename="grande.pdf")
                tracemalloc.start()
                try:
                    return await api._gravar_upload_com_teto(arquivo, api.MAX_UPLOAD_BYTES)
                finally:
                    _atual, self.pico = tracemalloc.get_traced_memory()
                    tracemalloc.stop()

        caminho = asyncio.run(rodar())

        self.assertIsNotNone(caminho)
        self.assertEqual(caminho.stat().st_size, self.TAMANHO)
        # pico bem abaixo do tamanho do arquivo (nao ~2x, como a versao que
        # acumulava bytes) - uma folga generosa (metade do arquivo) evita um
        # teste flaky por overhead incidental do interpretador/tracemalloc.
        self.assertLess(self.pico, self.TAMANHO / 2)

    def test_tamanho_bytes_do_job_vem_do_arquivo_gravado(self):
        async def rodar():
            with self._origem.open("rb") as handle:
                arquivo = UploadFile(file=handle, filename="grande.pdf")
                return await api._gravar_upload_com_teto(arquivo, api.MAX_UPLOAD_BYTES)

        caminho = asyncio.run(rodar())
        job = jobs.criar_job("grande.pdf", caminho)
        self.assertEqual(job.tamanho_bytes, self.TAMANHO)

    def test_arquivo_acima_do_teto_nao_deixa_residuo(self):
        async def rodar():
            with self._origem.open("rb") as handle:
                arquivo = UploadFile(file=handle, filename="grande.pdf")
                return await api._gravar_upload_com_teto(arquivo, self.TAMANHO // 2)

        resultado = asyncio.run(rodar())

        self.assertIsNone(resultado)
        self.assertEqual(list(jobs.DIR_UPLOADS.glob("*.pdf")), [])


class TestLimitesDeUpload(unittest.TestCase):
    """BUG-03: sem teto de tamanho por arquivo e de quantidade por lote, um
    upload grande o bastante estoura a memoria do processo e ocupa a thread
    worker unica indefinidamente. Tetos pequenos aqui so para o teste ser
    rapido - o padrao de producao fica em MAX_UPLOAD_BYTES/MAX_UPLOAD_FILES."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir_original = jobs.DIR_UPLOADS
        jobs.DIR_UPLOADS = Path(self._tmp.name)

        self._inicializar_original = motor_pool.inicializar
        self._patcher = patch.object(
            motor_pool, "inicializar",
            lambda cfg=None: self._inicializar_original(m.Config(engine="simples")),
        )
        self._patcher.start()

        self._bytes_original = api.MAX_UPLOAD_BYTES
        self._arquivos_original = api.MAX_UPLOAD_ARQUIVOS
        api.MAX_UPLOAD_BYTES = 1024
        api.MAX_UPLOAD_ARQUIVOS = 2

    def tearDown(self):
        api.MAX_UPLOAD_BYTES = self._bytes_original
        api.MAX_UPLOAD_ARQUIVOS = self._arquivos_original
        self._patcher.stop()
        jobs.DIR_UPLOADS = self._dir_original
        self._tmp.cleanup()

    def test_arquivo_acima_do_teto_de_tamanho_e_rejeitado_sem_gravar_nada(self):
        with TestClient(app) as cliente:
            resposta = cliente.post(
                "/api/jobs",
                files={"files": ("grande.pdf", b"X" * 2048, "application/pdf")},
            )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["criados"], [])
        self.assertEqual(len(corpo["rejeitados"]), 1)
        self.assertIn("1024", corpo["rejeitados"][0]["motivo"])
        self.assertEqual(list(Path(self._tmp.name).glob("*.pdf")), [])

    def test_lote_acima_do_teto_de_quantidade_rejeita_o_excedente(self):
        with TestClient(app) as cliente:
            resposta = cliente.post(
                "/api/jobs",
                files=[
                    ("files", ("a.pdf", b"%PDF-1.4", "application/pdf")),
                    ("files", ("b.pdf", b"%PDF-1.4", "application/pdf")),
                    ("files", ("c.pdf", b"%PDF-1.4", "application/pdf")),
                ],
            )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo["criados"]), 2)
        self.assertEqual(len(corpo["rejeitados"]), 1)
        self.assertEqual(corpo["rejeitados"][0]["nome_original"], "c.pdf")


class TestNomeSaidaSeguro(unittest.TestCase):
    """BUG-05: zip-slip via separador do Windows no nome_original do cliente.
    `Path(nome).with_suffix(".md").name` neutraliza travessia POSIX, mas o
    servidor roda em Linux e pathlib nao trata `\\` como separador - um
    cliente Windows podia mandar `..\\..\\evil.pdf` intacto para o arcname
    do zip / Content-Disposition."""

    CASOS_MALICIOSOS = [
        "..\\..\\..\\Windows\\System32\\evil.pdf",
        "../../etc/passwd.pdf",
        "/etc/x.pdf",
        "...pdf",
        "",
        ".",
        "..",
        "\\",
    ]

    def test_nunca_contem_separador_ou_travessia_e_nunca_e_vazio(self):
        for nome_original in self.CASOS_MALICIOSOS:
            with self.subTest(nome_original=nome_original):
                nome = api._nome_saida_seguro(nome_original, "abcd1234efgh0000")
                self.assertNotIn("/", nome)
                self.assertNotIn("\\", nome)
                self.assertNotIn("..", nome)
                self.assertTrue(nome)

    def test_nomes_normais_preservam_o_nome_base(self):
        self.assertEqual(api._nome_saida_seguro("relatorio.pdf", "x"), "relatorio.md")
        self.assertEqual(api._nome_saida_seguro("arquivo.tar.pdf", "x"), "arquivo.tar.md")

    def test_nao_levanta_excecao_para_nome_so_de_pontos_ou_vazio(self):
        # Path("").with_suffix(".md") / Path(".").with_suffix(".md") levantam
        # ValueError sem essa protecao - a rota devolveria 500.
        for nome_original in ("", ".", ".."):
            with self.subTest(nome_original=nome_original):
                api._nome_saida_seguro(nome_original, "job-id")  # nao deve levantar


class TestDownloadEndpoints(unittest.TestCase):
    """Insere jobs 'concluidos' direto no store (sem passar pelo worker/motor
    real) - Step 6 e sobre servir o que ja foi processado, nao sobre processar."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._store_original = jobs._store
        jobs._store = jobs.JobStore()

    def tearDown(self):
        jobs._store = self._store_original
        self._tmp.cleanup()

    def _job_concluido(self, job_id: str, nome_original: str, conteudo_md: str) -> jobs.Job:
        caminho_md = Path(self._tmp.name) / f"{job_id}.md"
        caminho_md.write_text(conteudo_md, encoding="utf-8")
        job = jobs.Job(
            id=job_id,
            nome_original=nome_original,
            caminho_pdf=Path(self._tmp.name) / f"{job_id}.pdf",
            status="concluido",
            caminho_saida=caminho_md,
        )
        jobs.obter_store().adicionar(job)
        return job

    def test_download_de_job_concluido_devolve_o_markdown(self):
        job = self._job_concluido("j1", "relatorio.pdf", "# Ola\n")
        with TestClient(app) as cliente:
            resposta = cliente.get(f"/api/jobs/{job.id}/download")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.text, "# Ola\n")
        self.assertIn("relatorio.md", resposta.headers.get("content-disposition", ""))

    def test_download_de_job_ainda_nao_concluido_devolve_409(self):
        job = jobs.Job(
            id="pendente", nome_original="a.pdf", caminho_pdf=Path("a.pdf"), status="na_fila"
        )
        jobs.obter_store().adicionar(job)
        with TestClient(app) as cliente:
            resposta = cliente.get(f"/api/jobs/{job.id}/download")
        self.assertEqual(resposta.status_code, 409)

    def test_download_de_job_inexistente_devolve_404(self):
        with TestClient(app) as cliente:
            resposta = cliente.get("/api/jobs/nao-existe/download")
        self.assertEqual(resposta.status_code, 404)

    def test_zip_contem_todos_os_concluidos_quando_sem_filtro(self):
        self._job_concluido("j1", "a.pdf", "conteudo a")
        self._job_concluido("j2", "b.pdf", "conteudo b")
        with TestClient(app) as cliente:
            resposta = cliente.get("/api/download-zip")
        self.assertEqual(resposta.status_code, 200)
        zf = zipfile.ZipFile(io.BytesIO(resposta.content))
        self.assertCountEqual(zf.namelist(), ["a.md", "b.md"])
        self.assertEqual(zf.read("a.md").decode("utf-8"), "conteudo a")

    def test_zip_filtra_por_ids(self):
        j1 = self._job_concluido("j1", "a.pdf", "conteudo a")
        self._job_concluido("j2", "b.pdf", "conteudo b")
        with TestClient(app) as cliente:
            resposta = cliente.get(f"/api/download-zip?ids={j1.id}")
        self.assertEqual(resposta.status_code, 200)
        zf = zipfile.ZipFile(io.BytesIO(resposta.content))
        self.assertEqual(zf.namelist(), ["a.md"])

    def test_zip_sem_jobs_concluidos_devolve_404(self):
        with TestClient(app) as cliente:
            resposta = cliente.get("/api/download-zip")
        self.assertEqual(resposta.status_code, 404)

    def test_zip_desambigua_nomes_duplicados(self):
        self._job_concluido("j1", "mesmo.pdf", "conteudo 1")
        self._job_concluido("j2", "mesmo.pdf", "conteudo 2")
        with TestClient(app) as cliente:
            resposta = cliente.get("/api/download-zip")
        zf = zipfile.ZipFile(io.BytesIO(resposta.content))
        self.assertEqual(len(zf.namelist()), 2)
        self.assertEqual(len(set(zf.namelist())), 2)

    def test_zip_nao_contem_entrada_com_separador_windows_no_arcname(self):
        self._job_concluido("j1", "..\\..\\..\\Windows\\System32\\evil.pdf", "conteudo")
        with TestClient(app) as cliente:
            resposta = cliente.get("/api/download-zip")
        zf = zipfile.ZipFile(io.BytesIO(resposta.content))
        self.assertEqual(zf.namelist(), ["evil.md"])

    def test_download_de_job_com_nome_windows_nao_leva_separador_ao_content_disposition(self):
        job = self._job_concluido("j1", "..\\..\\evil.pdf", "conteudo")
        with TestClient(app) as cliente:
            resposta = cliente.get(f"/api/jobs/{job.id}/download")
        self.assertEqual(resposta.status_code, 200)
        disposition = resposta.headers.get("content-disposition", "")
        self.assertNotIn("\\", disposition)
        self.assertIn("evil.md", disposition)

    def test_zip_pula_arquivo_que_sumiu_em_vez_de_derrubar_o_download(self):
        """BUG-13: zf.write() levantava FileNotFoundError nao tratado (viraria
        500) se um DELETE concorrente apagasse o .md entre jobs_concluidos()
        e a escrita no zip. Insere um job 'concluido' cujo caminho_saida
        aponta para um arquivo que nunca existiu, misturado com um job real."""
        self._job_concluido("presente", "ok.pdf", "conteudo ok")
        sumido = jobs.Job(
            id="sumido", nome_original="sumido.pdf",
            caminho_pdf=Path(self._tmp.name) / "sumido.pdf",
            status="concluido",
            caminho_saida=Path(self._tmp.name) / "sumido.md",  # nunca criado
        )
        jobs.obter_store().adicionar(sumido)

        with TestClient(app) as cliente:
            resposta = cliente.get("/api/download-zip")

        self.assertEqual(resposta.status_code, 200)
        zf = zipfile.ZipFile(io.BytesIO(resposta.content))
        self.assertEqual(zf.namelist(), ["ok.md"])

    def test_zip_com_todos_os_arquivos_sumidos_devolve_404(self):
        sumido = jobs.Job(
            id="sumido", nome_original="sumido.pdf",
            caminho_pdf=Path(self._tmp.name) / "sumido.pdf",
            status="concluido",
            caminho_saida=Path(self._tmp.name) / "sumido.md",  # nunca criado
        )
        jobs.obter_store().adicionar(sumido)

        with TestClient(app) as cliente:
            resposta = cliente.get("/api/download-zip")

        self.assertEqual(resposta.status_code, 404)


class TestRemoverJobsEndpoints(unittest.TestCase):
    """DELETE /api/jobs/{id} e DELETE /api/jobs (limpar finalizados) - mesmo
    padrao de insercao direta no store que TestDownloadEndpoints usa."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._store_original = jobs._store
        jobs._store = jobs.JobStore()

    def tearDown(self):
        jobs._store = self._store_original
        self._tmp.cleanup()

    def _job(self, job_id: str, status: str, *, com_saida: bool = False) -> jobs.Job:
        caminho_pdf = Path(self._tmp.name) / f"{job_id}.pdf"
        caminho_pdf.write_bytes(b"%PDF-1.4")
        caminho_md = None
        if com_saida:
            caminho_md = Path(self._tmp.name) / f"{job_id}.md"
            caminho_md.write_text("# ola\n", encoding="utf-8")
        job = jobs.Job(
            id=job_id,
            nome_original=f"{job_id}.pdf",
            caminho_pdf=caminho_pdf,
            status=status,
            caminho_saida=caminho_md,
        )
        jobs.obter_store().adicionar(job)
        return job

    def test_remove_job_concluido_e_apaga_arquivos_do_disco(self):
        job = self._job("j1", "concluido", com_saida=True)
        with TestClient(app) as cliente:
            resposta = cliente.delete(f"/api/jobs/{job.id}")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"removido": True})
        self.assertIsNone(jobs.obter_store().obter(job.id))
        self.assertFalse(job.caminho_pdf.exists())
        self.assertFalse(job.caminho_saida.exists())

    def test_remove_job_inexistente_devolve_404(self):
        with TestClient(app) as cliente:
            resposta = cliente.delete("/api/jobs/nao-existe")
        self.assertEqual(resposta.status_code, 404)

    def test_remove_job_processando_devolve_409_e_mantem_o_job(self):
        job = self._job("j1", "processando")
        with TestClient(app) as cliente:
            resposta = cliente.delete(f"/api/jobs/{job.id}")
        self.assertEqual(resposta.status_code, 409)
        self.assertIsNotNone(jobs.obter_store().obter(job.id))

    def test_limpar_finalizados_remove_so_concluidos_e_com_erro(self):
        self._job("j1", "concluido", com_saida=True)
        self._job("j2", "erro")
        na_fila = self._job("j3", "na_fila")
        processando = self._job("j4", "processando")
        with TestClient(app) as cliente:
            resposta = cliente.delete("/api/jobs")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"removidos": 2})
        ids_restantes = {j.id for j in jobs.obter_store().listar()}
        self.assertEqual(ids_restantes, {na_fila.id, processando.id})


if __name__ == "__main__":
    unittest.main()
