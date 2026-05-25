from __future__ import annotations

from dashboard_a9.runtime import execute_block


BLOCK_CODE = """
with tab5:
    if tab_funil_movel_ativa:
        render_bloco_hospitality()
"""


def render(namespace: dict[str, object]) -> None:
    execute_block(BLOCK_CODE, namespace, 'hospitality')
