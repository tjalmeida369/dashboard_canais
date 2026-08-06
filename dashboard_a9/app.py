from __future__ import annotations

import importlib
import os
import time
import unicodedata
from pathlib import Path

from dashboard_a9.config import ACTIVE_BLOCKS_BY_FLAG, BLOCK_ORDER


CORE_PATH = Path(__file__).with_name("core.py")
CORE_CODE = compile(
    CORE_PATH.read_text(encoding="utf-8-sig"),
    filename=str(CORE_PATH),
    mode="exec",
)
PERF_LOG_ENABLED = os.getenv("DASHBOARD_PERF_LOG", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
PROFILE_ENABLED = os.getenv("DASHBOARD_PROFILE", "0").strip().lower() in {
    "1", "true", "yes", "on"
}


def _resolve_block_order(namespace: dict[str, object]) -> list[str]:
    def _normalizar_label_aba(valor: object) -> str:
        texto = unicodedata.normalize("NFKD", str(valor or ""))
        texto = texto.encode("ASCII", "ignore").decode("ASCII")
        return "".join(ch for ch in texto.upper() if ch.isalnum())

    def _blocks_por_estado_streamlit() -> list[str] | None:
        st_ref = namespace.get("st")
        session_state = getattr(st_ref, "session_state", {}) if st_ref is not None else {}
        label_ativo = _normalizar_label_aba(
            getattr(session_state, "get", lambda *_: None)("dashboard_tab_ativa")
        )
        if not label_ativo:
            return None

        if "INICIO" in label_ativo:
            return ['funil_movel', 'funil_movel_final', 'inicio']
        if "ATIVADOS" in label_ativo:
            return ['ativados']
        if "PEDIDOS" in label_ativo or "ECOMMERCE" in label_ativo:
            return ['pedidos']
        if "LIGACOES" in label_ativo or "TELEVENDAS" in label_ativo:
            return ['ligacoes']
        if "HOSPITALITY" in label_ativo or "FUNILMOVEL" in label_ativo or "EMCONSTRUCAO" in label_ativo:
            return ['hospitality']
        if "DESATIVACOES" in label_ativo:
            return ['desativacoes']
        return None

    active_flags = {
        flag_name: bool(namespace.get(flag_name, False))
        for flag_name, _ in ACTIVE_BLOCKS_BY_FLAG
    }
    active_count = sum(active_flags.values())

    # Fallback conservador: se o Streamlit nao expuser a aba ativa, preserva
    # exatamente o fluxo antigo renderizando todos os blocos.
    if active_count == 0 or active_count == len(active_flags):
        blocks_estado = _blocks_por_estado_streamlit()
        if blocks_estado:
            return blocks_estado
        return list(BLOCK_ORDER)

    selected: list[str] = []
    for flag_name, blocks in ACTIVE_BLOCKS_BY_FLAG:
        if not active_flags.get(flag_name):
            continue
        for block_name in blocks:
            if block_name in BLOCK_ORDER and block_name not in selected:
                selected.append(block_name)

    return selected or list(BLOCK_ORDER)


def run_dashboard() -> None:
    profiler = None
    if PROFILE_ENABLED:
        import cProfile

        profiler = cProfile.Profile()
        profiler.enable()

    namespace: dict[str, object] = {
        "__builtins__": __builtins__,
        "__file__": str(CORE_PATH),
        "__name__": "__dashboard_a9_core__",
    }
    inicio_core = time.perf_counter()
    exec(CORE_CODE, namespace, namespace)
    if PERF_LOG_ENABLED:
        print(f"[perf] core={time.perf_counter() - inicio_core:.3f}s")
    for block_name in _resolve_block_order(namespace):
        module_name = f'dashboard_a9.blocks.{block_name}'
        module = importlib.import_module(module_name)
        inicio_bloco = time.perf_counter()
        module.render(namespace)
        if PERF_LOG_ENABLED:
            print(f"[perf] block.{block_name}={time.perf_counter() - inicio_bloco:.3f}s")

    if profiler is not None:
        import io
        import pstats

        profiler.disable()
        saida = io.StringIO()
        pstats.Stats(profiler, stream=saida).strip_dirs().sort_stats("cumtime").print_stats(45)
        print(saida.getvalue())
