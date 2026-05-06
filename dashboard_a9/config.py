from __future__ import annotations

BLOCK_ORDER = [
    'ativados',
    'desativacoes',
    'pedidos',
    'ligacoes',
    'funil_movel',
    'funil_movel_final',
    'inicio',
]

ACTIVE_BLOCKS_BY_FLAG = [
    ('tab_inicio_ativa', ('funil_movel', 'funil_movel_final', 'inicio')),
    ('tab_ativados_ativa', ('ativados',)),
    ('tab_desativacoes_ativa', ('desativacoes',)),
    ('tab_pedidos_ativa', ('pedidos',)),
    ('tab_ligacoes_ativa', ('ligacoes',)),
    ('tab_funil_movel_ativa', ('funil_movel', 'funil_movel_final')),
]
