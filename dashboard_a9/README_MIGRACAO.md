# Migração A9

`dash_final_a9.py` começa como baseline idêntico ao `app9.py`.

Ordem segura de extração:

1. Caminhos, cache e carregadores de Parquet.
2. Métricas puras: MoM, YoY, YTD, tendência e percentuais.
3. HTML de tabelas, mantendo CSS aprovado intacto.
4. Gráficos Plotly.
5. Renderização por aba.

Regra de ouro: cada extração deve compilar e preservar o mesmo resultado visual.

