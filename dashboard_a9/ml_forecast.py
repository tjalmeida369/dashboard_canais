from __future__ import annotations

from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st


DATA_PATH = (
    Path(__file__).resolve().parents[1]
    / "dados_preprocessados"
    / "projecao_vendas_ml.parquet"
)

CHANNEL_ORDER = [
    "Televendas Ativo",
    "Televendas Receptivo",
    "S2S+DAC",
    "E-Commerce",
    "Consultivo Remoto",
    "Hospitality PME",
]


@st.cache_data(show_spinner=False, max_entries=2)
def _load_forecast(path: str, modified_at: float) -> pd.DataFrame:
    del modified_at
    frame = pd.read_parquet(path)
    frame["MES_REF"] = pd.to_datetime(frame["MES_REF"], errors="coerce")
    frame["VALOR"] = pd.to_numeric(frame["VALOR"], errors="coerce").fillna(0.0)
    return frame


def _format_number(value: object) -> str:
    number = pd.to_numeric(value, errors="coerce")
    number = 0.0 if pd.isna(number) else float(number)
    return f"{number:,.0f}".replace(",", ".")


def _format_percentage(value: object) -> str:
    number = pd.to_numeric(value, errors="coerce")
    number = 0.0 if pd.isna(number) else float(number)
    return f"{number:+.1f}%".replace(".", ",")


def _percentage_class(value: object) -> str:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number) or abs(float(number)) < 0.05:
        return "status-neutral"
    return "status-positive" if float(number) > 0 else "status-negative"


def _build_product_table(frame: pd.DataFrame, product: str) -> str:
    product_data = frame[frame["COD_PLATAFORMA"].eq(product)].copy()
    if product_data.empty:
        return ""

    months = (
        product_data[["MES_REF", "MES_LABEL", "TIPO_VALOR"]]
        .drop_duplicates()
        .sort_values("MES_REF")
    )
    current = months[months["TIPO_VALOR"].eq("PREVISAO")]
    current_month = current.iloc[-1]["MES_LABEL"] if not current.empty else months.iloc[-1]["MES_LABEL"]
    month_labels = months["MES_LABEL"].tolist()
    current_year = int(pd.to_datetime(current.iloc[-1]["MES_REF"]).year) if not current.empty else int(pd.Timestamp.now().year)
    previous_year = current_year - 1

    pivot = product_data.pivot_table(
        index="CANAL_PLAN",
        columns="MES_LABEL",
        values="VALOR",
        aggfunc="sum",
        fill_value=0.0,
    ).reindex(columns=month_labels, fill_value=0.0)
    ordered = [channel for channel in CHANNEL_ORDER if channel in pivot.index]
    ordered.extend(channel for channel in pivot.index if channel not in ordered)
    pivot = pivot.reindex(ordered)
    pivot.loc["TOTAL"] = pivot.sum(axis=0)
    pivot = pivot.reindex(["TOTAL", *ordered])

    metric_columns = [
        "MES_ANTERIOR_REAL",
        "MES_ANO_ANTERIOR_REAL",
        "ORC_MES",
        "YTD_ANTERIOR",
        "YTD_ATUAL",
        "YTD_ORC",
    ]
    metrics = (
        product_data.sort_values("MES_REF")
        .groupby("CANAL_PLAN", observed=True)[metric_columns]
        .first()
        .reindex(ordered)
    )
    total_metrics = metrics.sum(axis=0)
    metrics.loc["TOTAL"] = total_metrics
    metrics = metrics.reindex(["TOTAL", *ordered])
    current_values = pivot[current_month]
    metrics["MOM"] = (current_values / metrics["MES_ANTERIOR_REAL"].replace(0, pd.NA) - 1.0) * 100.0
    metrics["YOY"] = (current_values / metrics["MES_ANO_ANTERIOR_REAL"].replace(0, pd.NA) - 1.0) * 100.0
    metrics["PREV_VS_ORC"] = (current_values / metrics["ORC_MES"].replace(0, pd.NA) - 1.0) * 100.0
    metrics["YTD_VS_ANTERIOR"] = (
        metrics["YTD_ATUAL"] / metrics["YTD_ANTERIOR"].replace(0, pd.NA) - 1.0
    ) * 100.0
    metrics["YTD_VS_ORC"] = (
        metrics["YTD_ATUAL"] / metrics["YTD_ORC"].replace(0, pd.NA) - 1.0
    ) * 100.0
    metrics = metrics.fillna(0.0)

    comparison_columns = [
        ("MOM", "MoM", "forecast-variation", True),
        ("YOY", "YoY", "forecast-variation", True),
        ("ORC_MES", f"OR&Ccedil;<br>{escape(str(current_month).upper())}", "forecast-budget", False),
        ("PREV_VS_ORC", "PREV<br>vs<br>OR&Ccedil;", "forecast-variation", True),
        ("YTD_ANTERIOR", f"YTD{str(previous_year)[-2:]}", "forecast-ytd", False),
        ("YTD_ATUAL", f"YTD{str(current_year)[-2:]}", "forecast-ytd", False),
        ("YTD_ORC", "YTD_OR&Ccedil;", "forecast-budget", False),
        (
            "YTD_VS_ANTERIOR",
            f"YTD{str(current_year)[-2:]}<br>vs<br>YTD{str(previous_year)[-2:]}",
            "forecast-variation",
            True,
        ),
        ("YTD_VS_ORC", f"YTD{str(current_year)[-2:]}<br>vs<br>OR&Ccedil;", "forecast-variation", True),
    ]

    header_cells = []
    for label in month_labels:
        if label == current_month:
            header_cells.append(
                f'<th class="forecast-current">PREVIS&Atilde;O<br>{escape(str(label).upper())}</th>'
            )
        else:
            header_cells.append(f"<th>{escape(str(label).upper())}</th>")
    for _, label, css_class, _ in comparison_columns:
        header_cells.append(f'<th class="{css_class}">{label}</th>')

    body_rows = []
    for channel, row in pivot.iterrows():
        is_total = channel == "TOTAL"
        row_class = "forecast-total" if is_total else ""
        cells = [f'<td class="forecast-channel">{escape(str(channel))}</td>']
        for label in month_labels:
            cell_class = "forecast-current" if label == current_month else ""
            cells.append(f'<td class="{cell_class}">{_format_number(row[label])}</td>')
        for key, _, css_class, is_percentage in comparison_columns:
            value = metrics.loc[channel, key]
            status_class = _percentage_class(value) if is_percentage and not is_total else ""
            formatted = _format_percentage(value) if is_percentage else _format_number(value)
            cells.append(f'<td class="{css_class} {status_class}">{formatted}</td>')
        body_rows.append(f'<tr class="{row_class}">{"".join(cells)}</tr>')

    colgroup = (
        '<colgroup><col style="width:10.5%;">'
        + ''.join('<col style="width:3.6%;">' for _ in month_labels)
        + ''.join(
            f'<col style="width:{5.1 if is_percentage else 4.3}%;">'
            for _, _, _, is_percentage in comparison_columns
        )
        + '</colgroup>'
    )

    return f"""
    <div class="forecast-table-wrap">
      <table class="forecast-table">
        {colgroup}
        <thead><tr><th class="forecast-channel">CANAL</th>{''.join(header_cells)}</tr></thead>
        <tbody>{''.join(body_rows)}</tbody>
      </table>
    </div>
    """


def render_ml_forecast(title_builder=None) -> None:
    if not DATA_PATH.exists():
        st.info("Execute o pipeline offline de previsao para publicar esta analise.")
        return

    frame = _load_forecast(str(DATA_PATH), DATA_PATH.stat().st_mtime)
    if frame.empty:
        st.info("Ainda nao ha previsoes publicadas para esta analise.")
        return

    if callable(title_builder):
        st.markdown(
            title_builder(
                "PROJEÇÃO DE ATIVADOS COM MACHINE LEARNING",
                "trend",
                extra_style="margin-top:20px;",
            ),
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="forecast-title">PROJE&Ccedil;&Atilde;O DE ATIVADOS COM MACHINE LEARNING</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        """
        <style>
          .forecast-title {
            margin: 20px 0 10px;
            color: #2F3747;
            font: 800 17px/1.2 'Manrope', 'Segoe UI', sans-serif;
            letter-spacing: .01em;
          }
          .forecast-subtitle {
            margin: -4px 0 12px;
            color: #6B7280;
            font: 500 11px/1.35 'Manrope', 'Segoe UI', sans-serif;
          }
          .forecast-table-wrap {
            width: 100%;
            overflow-x: hidden;
            border: 2px solid #790E09;
            border-radius: 12px;
            background: linear-gradient(180deg, #FFFFFF 0%, #FFF7F6 100%);
            box-shadow: 0 6px 18px rgba(121,14,9,.12);
            margin: 8px 0 14px;
          }
          .forecast-table {
            width: 100%;
            min-width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
            font-family: 'Manrope', 'Segoe UI', sans-serif;
            font-variant-numeric: tabular-nums;
          }
          .forecast-table th {
            padding: 4px 1px;
            background: linear-gradient(135deg, #790E09 0%, #5A0A06 100%);
            color: #FFFFFF;
            border-right: 1px solid rgba(255,255,255,.90);
            font-size: clamp(8.1px, .60vw, 9.4px);
            font-weight: 800;
            line-height: 1.02;
            text-align: center;
            text-transform: uppercase;
            white-space: normal;
          }
          .forecast-table th.forecast-current {
            background: linear-gradient(135deg, #6B7280 0%, #475569 100%);
          }
          .forecast-table th.forecast-variation {
            background: linear-gradient(135deg, #5A6268 0%, #3E444A 100%);
          }
          .forecast-table th.forecast-ytd {
            background: linear-gradient(135deg, #D45D44 0%, #A23B36 100%);
          }
          .forecast-table th.forecast-budget {
            background: linear-gradient(135deg, #6B7280 0%, #475569 100%);
          }
          .forecast-table th.forecast-channel {
            text-align: left;
            padding-left: 6px;
            background: linear-gradient(135deg, #6C0C08 0%, #4A0704 100%);
          }
          .forecast-table td {
            padding: 4px 2px;
            border-right: 1px solid #FFFFFF;
            border-bottom: 1px solid #FFFFFF;
            color: #2F3747;
            font-size: clamp(8.6px, .66vw, 10.2px);
            line-height: 1.08;
            text-align: right;
            white-space: nowrap;
          }
          .forecast-table td.forecast-channel {
            text-align: left;
            font-weight: 600;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .forecast-table td.forecast-current {
            background: linear-gradient(180deg, rgba(100,116,139,.095) 0%, rgba(100,116,139,.035) 100%);
            color: #334155;
            font-weight: 600;
          }
          .forecast-table td.forecast-ytd {
            background: linear-gradient(180deg, rgba(47,55,71,.06) 0%, rgba(47,55,71,.025) 100%);
            color: #1F2937;
            font-weight: 600;
          }
          .forecast-table td.forecast-budget {
            background: linear-gradient(180deg, rgba(100,116,139,.095) 0%, rgba(100,116,139,.035) 100%);
            color: #334155;
            font-weight: 600;
          }
          .forecast-table td.forecast-variation {
            position: relative;
            padding-left: 13px;
            font-weight: 600;
          }
          .forecast-table td.forecast-variation.status-positive {
            color: #1B5E20;
          }
          .forecast-table td.forecast-variation.status-negative {
            color: #B71C1C;
          }
          .forecast-table td.forecast-variation.status-neutral {
            color: #475569;
          }
          .forecast-table tr:not(.forecast-total) td.forecast-variation.status-positive::before {
            content: "▲";
            position: absolute;
            left: 3px;
            color: #2E7D32;
            font-size: 7px;
          }
          .forecast-table tr:not(.forecast-total) td.forecast-variation.status-negative::before {
            content: "▼";
            position: absolute;
            left: 3px;
            color: #C62828;
            font-size: 7px;
          }
          .forecast-table tr:nth-child(even):not(.forecast-total) td {
            background-color: #FDF3F2;
          }
          .forecast-table tr.forecast-total td {
            background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%);
            color: #FFFFFF !important;
            font-weight: 700 !important;
            border-bottom: 2px solid #A23B36;
          }
          .forecast-table tr.forecast-total td::before {
            content: none !important;
          }
          .forecast-model-summary {
            margin: 8px 0 16px;
            padding: 14px;
            border: 1px solid #E5E7EB;
            border-top: 4px solid #790E09;
            border-radius: 12px;
            background: #FFFFFF;
            box-shadow: 0 7px 20px rgba(31,41,55,.07);
            color: #4B5563;
            font: 500 10.5px/1.42 'Manrope', 'Segoe UI', sans-serif;
          }
          .forecast-model-summary strong {
            color: #2F3747;
            font-weight: 800;
          }
          .forecast-model-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 14px;
            margin-bottom: 11px;
          }
          .forecast-model-eyebrow {
            color: #790E09;
            font-size: 8.5px;
            font-weight: 850;
            letter-spacing: .11em;
            text-transform: uppercase;
          }
          .forecast-model-title {
            margin-top: 2px;
            color: #1F2937;
            font-size: 14px;
            font-weight: 850;
          }
          .forecast-model-description {
            margin-top: 3px;
            color: #6B7280;
            max-width: 820px;
          }
          .forecast-model-chip {
            flex: 0 0 auto;
            padding: 5px 9px;
            border-radius: 999px;
            background: #FFF1EF;
            color: #790E09;
            border: 1px solid #F2D1CD;
            font-size: 8.5px;
            font-weight: 800;
            letter-spacing: .04em;
            text-transform: uppercase;
          }
          .forecast-model-scores,
          .forecast-model-recipes {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 9px;
          }
          .forecast-model-score {
            display: grid;
            grid-template-columns: auto 1fr;
            align-items: center;
            gap: 10px;
            padding: 10px 11px;
            border-radius: 9px;
            background: #FAFBFC;
            border: 1px solid #E5E7EB;
          }
          .forecast-model-score-value {
            color: #790E09;
            font-size: 21px;
            line-height: 1;
            font-weight: 900;
            font-variant-numeric: tabular-nums;
          }
          .forecast-model-score-label {
            color: #1F2937;
            font-size: 10px;
            font-weight: 800;
          }
          .forecast-model-score-detail {
            margin-top: 2px;
            color: #6B7280;
            font-size: 9px;
          }
          .forecast-model-flow {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 7px;
            margin: 10px 0;
          }
          .forecast-model-step {
            min-height: 74px;
            padding: 8px 9px;
            border-radius: 8px;
            background: #FFFFFF;
            border: 1px solid #E5E7EB;
          }
          .forecast-model-step-number {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 18px;
            height: 18px;
            margin-bottom: 5px;
            border-radius: 50%;
            background: #790E09;
            color: #FFFFFF;
            font-size: 8px;
            font-weight: 850;
          }
          .forecast-model-step strong {
            display: block;
            margin-bottom: 2px;
            font-size: 9.5px;
          }
          .forecast-model-recipe {
            padding: 9px 10px;
            border-radius: 8px;
            background: #FFF8F7;
            border: 1px solid #F0D7D4;
          }
          .forecast-model-recipe-title {
            margin-bottom: 3px;
            color: #790E09;
            font-size: 9.5px;
            font-weight: 850;
            text-transform: uppercase;
            letter-spacing: .05em;
          }
          .forecast-model-note {
            margin-top: 9px;
            padding: 9px 10px;
            border-radius: 8px;
            background: #F8FAFC;
            border-left: 3px solid #64748B;
          }
          .forecast-model-considerations {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 7px;
            margin-top: 10px;
          }
          .forecast-model-considerations-title {
            grid-column: 1 / -1;
            color: #2F3747;
            font-size: 10.5px;
            font-weight: 850;
          }
          .forecast-model-consideration {
            padding: 8px 9px;
            border-radius: 8px;
            background: #FAFBFC;
            border: 1px solid #E5E7EB;
          }
          .forecast-model-consideration strong {
            display: block;
            margin-bottom: 2px;
            color: #790E09;
            font-size: 9px;
            text-transform: uppercase;
            letter-spacing: .04em;
          }
          .forecast-model-footer {
            display: flex;
            flex-wrap: wrap;
            gap: 6px 14px;
            margin-top: 9px;
            padding-top: 8px;
            border-top: 1px solid #E5E7EB;
            color: #64748B;
            font-size: 9px;
          }
          @media (max-width: 900px) {
            .forecast-model-scores,
            .forecast-model-recipes { grid-template-columns: 1fr; }
            .forecast-model-flow { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .forecast-model-considerations { grid-template-columns: 1fr; }
            .forecast-model-considerations-title { grid-column: auto; }
            .forecast-model-head { display: block; }
            .forecast-model-chip { display: inline-block; margin-top: 7px; }
          }
        </style>
        <div class="forecast-subtitle">Fechamento mensal estimado por canal; meses encerrados mostram o realizado e o m&ecirc;s atual mostra a previs&atilde;o.</div>
        """,
        unsafe_allow_html=True,
    )

    for product in ("CONTA", "FIXA"):
        if callable(title_builder):
            st.markdown(
                title_builder(
                    product,
                    product,
                    "subsection-title",
                    extra_style="margin-top:12px;",
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="forecast-product-label">{escape(product)}</div>',
                unsafe_allow_html=True,
            )
        table_html = _build_product_table(frame, product)
        if table_html:
            st.markdown(table_html, unsafe_allow_html=True)

    metrics = (
        frame[
            [
                "COD_PLATAFORMA",
                "ACURACIA_BACKTEST",
                "ACURACIA_CONSOLIDADA_BACKTEST",
                "ACURACIA_RECENTE_BACKTEST",
                "ACURACIA_CONSOLIDADA_RECENTE_BACKTEST",
            ]
        ]
        .drop_duplicates("COD_PLATAFORMA")
        .set_index("COD_PLATAFORMA")
    )
    cutoff = pd.to_datetime(frame["DATA_CORTE"], errors="coerce").max()
    cutoff_text = cutoff.strftime("%d/%m/%Y") if pd.notna(cutoff) else "-"
    def metric_text(product: str, column: str) -> str:
        value = float(metrics.loc[product, column]) if product in metrics.index else 0.0
        return f"{value:.1%}".replace(".", ",")

    conta_detail = metric_text("CONTA", "ACURACIA_BACKTEST")
    conta_total = metric_text("CONTA", "ACURACIA_CONSOLIDADA_BACKTEST")
    fixa_detail = metric_text("FIXA", "ACURACIA_BACKTEST")
    fixa_total = metric_text("FIXA", "ACURACIA_CONSOLIDADA_BACKTEST")
    st.markdown(
        f"""
        <div class="forecast-model-summary">
          <div class="forecast-model-head">
            <div>
              <div class="forecast-model-eyebrow">Metodologia preditiva</div>
              <div class="forecast-model-title">Como o modelo chega &agrave; previs&atilde;o</div>
              <div class="forecast-model-description">A proje&ccedil;&atilde;o estima o fechamento mensal de cada canal combinando o realizado parcial, o comportamento hist&oacute;rico e os principais sinais da opera&ccedil;&atilde;o.</div>
            </div>
            <div class="forecast-model-chip">Treino offline</div>
          </div>

          <div class="forecast-model-scores">
            <div class="forecast-model-score">
              <div class="forecast-model-score-value">{conta_detail}</div>
              <div><div class="forecast-model-score-label">CONTA &middot; por canal</div><div class="forecast-model-score-detail">Gross L&iacute;quido &middot; consolidado {conta_total}</div></div>
            </div>
            <div class="forecast-model-score">
              <div class="forecast-model-score-value">{fixa_detail}</div>
              <div><div class="forecast-model-score-label">FIXA &middot; por canal</div><div class="forecast-model-score-detail">Instala&ccedil;&atilde;o &middot; consolidado {fixa_total}</div></div>
            </div>
          </div>

          <div class="forecast-model-flow">
            <div class="forecast-model-step"><span class="forecast-model-step-number">1</span><strong>Observa o realizado</strong>Usa o volume parcial j&aacute; entregue por cada canal no m&ecirc;s.</div>
            <div class="forecast-model-step"><span class="forecast-model-step-number">2</span><strong>Compara o ritmo</strong>Localiza meses anteriores no mesmo est&aacute;gio de dias &uacute;teis.</div>
            <div class="forecast-model-step"><span class="forecast-model-step-number">3</span><strong>Adiciona contexto</strong>Considera demanda, upstream, convers&atilde;o e calend&aacute;rio comercial.</div>
            <div class="forecast-model-step"><span class="forecast-model-step-number">4</span><strong>Valida no futuro</strong>Simula meses ainda n&atilde;o vistos e mede o erro fora da amostra.</div>
          </div>

          <div class="forecast-model-recipes">
            <div class="forecast-model-recipe"><div class="forecast-model-recipe-title">F&oacute;rmula vencedora &middot; Conta</div><strong>80%</strong> do NNLS treinado separadamente para cada canal + <strong>20%</strong> do ritmo hist&oacute;rico conservador, com corre&ccedil;&atilde;o de metade do vi&eacute;s mediano observado nos testes anteriores. O ritmo de apoio combina 60% pace, 25% M-1 e 15% m&eacute;dia de 3 meses.</div>
            <div class="forecast-model-recipe"><div class="forecast-model-recipe-title">F&oacute;rmula vencedora &middot; Fixa</div><strong>70%</strong> do composto temporal + <strong>30%</strong> da Venda Bruta projetada multiplicada pela convers&atilde;o hist&oacute;rica.</div>
          </div>

          <div class="forecast-model-considerations">
            <div class="forecast-model-considerations-title">Considera&ccedil;&otilde;es usadas na base de treinamento</div>
            <div class="forecast-model-consideration"><strong>Calend&aacute;rio comercial</strong>Total e saldo de dias &uacute;teis, quantidade de segundas a sextas, feriados nacionais e dias-ponte.</div>
            <div class="forecast-model-consideration"><strong>Demanda</strong>Pedidos do E-Commerce e liga&ccedil;&otilde;es do Televendas Receptivo. Canais sem demanda usam os demais sinais dispon&iacute;veis.</div>
            <div class="forecast-model-consideration"><strong>Funil de vendas</strong>Gross Bruto para Conta, Venda Bruta para Fixa e suas convers&otilde;es hist&oacute;ricas at&eacute; o target final.</div>
            <div class="forecast-model-consideration"><strong>Cota&ccedil;&otilde;es</strong>Quantidade de cota&ccedil;&otilde;es e novas linhas cotadas, com defasagem de um m&ecirc;s e m&eacute;dia m&oacute;vel de tr&ecirc;s meses.</div>
            <div class="forecast-model-consideration"><strong>Backlog Fixa</strong>Contratos em backlog, m&eacute;dia de tr&ecirc;s meses e contratos sem agenda, utilizados como sinal do potencial de instala&ccedil;&atilde;o.</div>
            <div class="forecast-model-consideration"><strong>Hist&oacute;rico e escopo</strong>Lags de 1, 2, 3 e 12 meses, m&eacute;dias de 3 e 6 meses e sazonalidade. A previs&atilde;o &eacute; por canal e produto, sem abertura regional.</div>
          </div>

          <div class="forecast-model-note"><strong>Controle de overfit:</strong> o rolling backtest usa os 5 meses mais recentes, sempre treinando apenas com informa&ccedil;&otilde;es anteriores ao m&ecirc;s testado. NNLS com coeficientes positivos, Ridge regularizado, Random Forest e HistGradientBoosting com early stopping participaram; venceu o modelo com menor WMAPE fora da amostra. Grid Search amplo foi evitado porque o hist&oacute;rico mensal ainda &eacute; curto.</div>

          <div class="forecast-model-footer">
            <span><strong>Acur&aacute;cia:</strong> 1 - WMAPE</span>
            <span><strong>Vari&aacute;veis avaliadas:</strong> demanda, Gross/Venda Bruta, cota&ccedil;&otilde;es, backlog, dias &uacute;teis, feriados e dia da semana</span>
            <span><strong>Corte:</strong> {cutoff_text}</span>
            <span><strong>Regra de seguran&ccedil;a:</strong> previs&atilde;o &ge; realizado parcial</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
