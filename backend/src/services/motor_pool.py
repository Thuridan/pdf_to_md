#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend.src.services.motor_pool - Instancia unica do motor de conversao para o processo.

Chamado uma vez no startup do FastAPI (lifespan de webapp.main). O motor
Docling e caro para carregar (layout model + TableFormer + OCR) - por isso
so existe UMA instancia por processo, reaproveitada por todos os jobs
(mesma garantia que pdf_to_md.executar() ja da para um lote via CLI).
"""

from __future__ import annotations

import pdf_to_md as m

_motor: m.MotorBase | None = None
_cfg: m.Config | None = None


def inicializar(cfg: m.Config | None = None) -> m.MotorBase:
    """Seleciona e guarda a instancia unica do motor para o processo inteiro.

    Nao forca carga eager dos modelos do Docling: MotorDocling continua
    carregando-os sob demanda na primeira conversao real (ver
    MotorDocling._obter_converter) - aqui so decidimos QUAL motor usar.
    """
    global _motor, _cfg
    _cfg = cfg if cfg is not None else m.Config()
    m.aplicar_ambiente(_cfg)
    _motor = m.selecionar_motor(_cfg)
    return _motor


def obter_motor() -> m.MotorBase:
    if _motor is None:
        raise RuntimeError(
            "motor_pool nao inicializado - chame motor_pool.inicializar() no startup do app."
        )
    return _motor


def obter_config() -> m.Config:
    if _cfg is None:
        raise RuntimeError(
            "motor_pool nao inicializado - chame motor_pool.inicializar() no startup do app."
        )
    return _cfg
