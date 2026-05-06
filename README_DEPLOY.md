# Dashboard Final A9

Esta pasta contém a versão limpa do dashboard para subir no Streamlit Cloud.

## Arquivo principal

Use este arquivo como entrada do Streamlit:

```text
dash_final_a9.py
```

Se você subir a pasta `dash_final` inteira dentro de um repositório maior, use:

```text
dash_final/dash_final_a9.py
```

## Estrutura incluída

- `dash_final_a9.py`: app principal.
- `dashboard_a9/`: código modular do dashboard.
- `dados_preprocessados/`: bases Parquet otimizadas.
- `base_convergencia.xlsx`: base usada pela visão de convergência.
- `.streamlit/config.toml`: tema/configuração do Streamlit.
- `requirements.txt`: dependências para o Streamlit Cloud.
- `preprocess_all.py`: script de geração das bases Parquet.
- `validate_preprocessed_data.py`: validação das bases preprocessadas.

## Observação

Para atualizar dados, rode o `preprocess_all.py` fora do Streamlit Cloud e suba novamente os arquivos da pasta `dados_preprocessados/`.
