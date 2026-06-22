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
            font-size: clamp(7px, .54vw, 8.5px);
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
            font-size: clamp(8px, .61vw, 9.5px);
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
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 7px 14px;
            margin: 7px 0 15px;
            padding: 10px 12px;
            border: 1px solid #E5E7EB;
            border-left: 4px solid #790E09;
            border-radius: 9px;
            background: #FAFBFC;
            color: #4B5563;
            font: 500 10px/1.35 'Manrope', 'Segoe UI', sans-serif;
          }
          .forecast-model-summary strong {
            color: #2F3747;
            font-weight: 800;
          }
          .forecast-model-summary .forecast-model-wide {
            grid-column: 1 / -1;
          }
          @media (max-width: 900px) {
            .forecast-model-summary { grid-template-columns: 1fr; }
            .forecast-model-summary .forecast-model-wide { grid-column: auto; }
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
        frame[["COD_PLATAFORMA", "ACURACIA_BACKTEST"]]
        .drop_duplicates("COD_PLATAFORMA")
        .set_index("COD_PLATAFORMA")["ACURACIA_BACKTEST"]
        .to_dict()
    )
    cutoff = pd.to_datetime(frame["DATA_CORTE"], errors="coerce").max()
    cutoff_text = cutoff.strftime("%d/%m/%Y") if pd.notna(cutoff) else "-"
    conta_accuracy = f"{metrics.get('CONTA', 0.0):.1%}".replace(".", ",")
    fixa_accuracy = f"{metrics.get('FIXA', 0.0):.1%}".replace(".", ",")
    st.markdown(
        f"""
        <div class="forecast-model-summary">
          <div><strong>Targets:</strong> Conta = Gross L&iacute;quido; Fixa = Instala&ccedil;&atilde;o.</div>
          <div><strong>Acur&aacute;cia temporal (1-WMAPE):</strong> Conta {conta_accuracy}; Fixa {fixa_accuracy}.</div>
          <div><strong>Algoritmo selecionado:</strong> modelo h&iacute;brido de ritmo hist&oacute;rico, lags e convers&atilde;o.</div>
          <div><strong>Pesos Conta:</strong> 60% ritmo do canal, 25% M-1 e 15% m&eacute;dia dos &uacute;ltimos 3 meses.</div>
          <div><strong>Pesos Fixa:</strong> 70% do composto temporal e 30% da proje&ccedil;&atilde;o por Venda Bruta/convers&atilde;o.</div>
          <div><strong>Vari&aacute;veis avaliadas:</strong> demanda, Gross/Venda Bruta, cota&ccedil;&otilde;es, backlog, dias &uacute;teis, feriados e dia da semana.</div>
          <div class="forecast-model-wide"><strong>Sele&ccedil;&atilde;o e anti-overfit:</strong> rolling backtest dos 5 meses mais recentes, comparando o h&iacute;brido com Ridge regularizado, Random Forest regularizada e HistGradientBoosting com early stopping. N&atilde;o foi aplicado Grid Search devido ao hist&oacute;rico curto; venceu o modelo com menor erro fora da amostra.</div>
          <div class="forecast-model-wide"><strong>Corte dos dados:</strong> realizados at&eacute; {cutoff_text}; no m&ecirc;s atual, a previs&atilde;o nunca fica abaixo do realizado parcial.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
