from __future__ import annotations

import importlib

from . import core
from .config import BLOCK_ORDER
from .navigation import setup_navigation


def run_dashboard() -> None:
    namespace = vars(core)
    setup_navigation(namespace)
    for block_name in BLOCK_ORDER:
        modulo = importlib.import_module(f"dashboard_producao.blocks.{block_name}")
        modulo.render(namespace)
