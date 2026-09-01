#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sanity check do Step 2: uma unica instancia de motor por processo, sem carga eager dos modelos."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pdf_to_md as m  # noqa: E402

from backend.src.services import motor_pool  # noqa: E402


class TestMotorPool(unittest.TestCase):
    def tearDown(self):
        motor_pool._motor = None
        motor_pool._cfg = None

    def test_obter_motor_antes_de_inicializar_leva_erro_util(self):
        with self.assertRaises(RuntimeError):
            motor_pool.obter_motor()

    def test_inicializar_guarda_instancia_unica_reaproveitada(self):
        motor = motor_pool.inicializar(m.Config(engine="simples"))
        self.assertIs(motor_pool.obter_motor(), motor)
        self.assertIs(motor_pool.obter_motor(), motor_pool.obter_motor())

    def test_inicializar_usa_engine_da_config(self):
        motor_pool.inicializar(m.Config(engine="simples"))
        self.assertEqual(motor_pool.obter_motor().nome, "simples")

    def test_inicializar_nao_carrega_modelos_do_docling_de_forma_eager(self):
        # selecionar_motor() so decide QUAL motor usar; MotorDocling so carrega
        # os modelos de verdade na primeira conversao (._obter_converter()).
        motor = motor_pool.inicializar(m.Config(engine="auto"))
        if motor.nome == "docling":
            self.assertIsNone(motor._conv)


if __name__ == "__main__":
    unittest.main()
