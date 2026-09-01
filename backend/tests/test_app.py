#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sanity check do app FastAPI: sobe reaproveitando pdf_to_md.py e inicializa o motor_pool no startup."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pdf_to_md as m  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
