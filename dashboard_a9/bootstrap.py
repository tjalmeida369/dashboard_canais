from __future__ import annotations

import runpy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEGACY_APP_PATH = PROJECT_ROOT / "app9.py"


def run_legacy_app() -> None:
    """Executa o dashboard atual como baseline visual e funcional.

    Mantemos esta camada enquanto os blocos do motor sao extraidos para
    modulos menores. Isso garante que o dash_final_a9.py abra igual ao app9.py
    durante toda a migracao.
    """
    if not LEGACY_APP_PATH.exists():
        raise FileNotFoundError(f"Arquivo legado nao encontrado: {LEGACY_APP_PATH}")
    runpy.run_path(str(LEGACY_APP_PATH), run_name="__main__")

