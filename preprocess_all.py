from __future__ import annotations

import argparse
import gc
import os
import re
import subprocess
import unicodedata
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings(
    "ignore",
    message="Workbook contains no default style, apply openpyxl's default",
    category=UserWarning,
    module="openpyxl.styles.stylesheet",
)


ROOT_DIR = Path(__file__).resolve().parent
RAW_DIR_CANDIDATES = [
    ROOT_DIR / "Arquivos_Dashboard",
    Path(
        r"C:\Users\F270665\Claro SA\USER-Canais Estratégicos - Canais Estratégicos\Relatórios\Arquivos - Dashboard Canais Estratégicos\Arquivos_Dashboard"
    ),
    ROOT_DIR,
]
OUT_DIR_DEFAULT = ROOT_DIR / "dados_preprocessados"

MONTHS_PT = {
    1: "jan",
    2: "fev",
    3: "mar",
    4: "abr",
    5: "mai",
    6: "jun",
    7: "jul",
    8: "ago",
    9: "set",
    10: "out",
    11: "nov",
    12: "dez",
}
PRIMARY_BASE_USECOLS = [
    "REGIONAL",
    "DSC_REGIONAL_CMV",
    "CANAL_PLAN",
    "DSC_CANAL",
    "dat_tratada",
    "mes_ano",
    "DSC_INDICADOR",
    "DSC_MOTIVO_STS",
    "COD_PLATAFORMA",
    "DAT_MOVIMENTO2",
    "DAT_MOVIMENTO",
    "DAT_MOVIMENTO_2",
    "PERIODO",
    "QTDE",
    "DESAFIO_QTD",
    "TEND_QTD",
    "ID_AFILIADOS",
    "ID_AFILIADO",
    "ORIGEM_AFILIADOS",
    "ORIGEM_AFILIADO",
    "DAT_MÊS",
    "ANO",
    "dia_semana",
]
BACKLOG_CANAIS_PERMITIDOS = [
    "DAC",
    "DAC Adequacao de Pacote",
    "Hospitality PME",
    "Internet",
    "Ativo Aquisicao Direto",
    "Ativo Aquisicao Indireto",
    "Ativo Rentabilizacao Indireto",
    "Receptivo",
    "Receptivo Rentabilizacao Exclusivo",
    "Inside Sales",
    "Consultivo Remoto",
]
# Ordem oficial do funil FIXA/E-Commerce usada para sobrescrever a ordem bruta do Excel.
FUNIL_FIXA_INDICADORES_CONFIG = [
    ("INVESTIMENTO", "INVESTIMENTO", 1),
    ("SESSOES", "SESSÕES", 2),
    ("PORTEIRA_CEP", "PORTEIRA CEP", 3),
    ("DADOS_PESSOAIS", "DADOS PESSOAIS", 4),
    ("ENDERECO", "ENDEREÇO", 5),
    ("PAGAMENTO", "PAGAMENTO", 6),
    ("PEDIDOS_TOTAL", "PEDIDOS_TOTAL", 7),
    ("REJEITADO", "REJEITADO", 8),
    ("VENDA_BRUTA", "VENDA BRUTA", 9),
    ("DESISTENCIA", "DESISTÊNCIA", 10),
    ("INSTALACAO", "INSTALAÇÃO", 11),
]
FUNIL_FIXA_INDICADOR_LABELS = {chave: label for chave, label, _ in FUNIL_FIXA_INDICADORES_CONFIG}
FUNIL_FIXA_INDICADOR_ORDENS = {chave: ordem for chave, _, ordem in FUNIL_FIXA_INDICADORES_CONFIG}
CONVERGENCIA_COL_ALIASES = {
    "DAT_MOVIMENTO": ("DAT_MOVIMENTO", "DATA_MOVIMENTO", "DATA", "PERIODO"),
    "DSC_REGIONAL": ("DSC_REGIONAL", "REGIONAL", "DSC_REGIONAL_CMV"),
    "DSC_CANAL_VENDA": ("DSC_CANAL_VENDA", "CANAL_PLAN", "DSC_CANAL", "CANAL"),
    "DSC_TIPO_ORIGEM": ("DSC_TIPO_ORIGEM", "COD_PLATAFORMA", "PRODUTO", "PLATAFORMA"),
    "QTDE": ("QTDE", "QTD"),
    "QTDE_CNPJ8": ("QTDE_CNPJ8", "QTD_CNPJ8", "QTDE_CLIENTES", "CLIENTES"),
    "FLG_VENDA_CONVERGENTE": ("FLG_VENDA_CONVERGENTE", "FLAG_VENDA_CONVERGENTE", "VENDA_CONVERGENTE"),
    "FLG_NOVO": ("FLG_NOVO", "FLAG_NOVO"),
    "FLG_NOVO_NOVO": ("FLG_NOVO_NOVO", "FLAG_NOVO_NOVO", "FLG_NOVO-NOVO"),
}
ALLOWED_CANAIS_VENDA_COTACOES = {
    "CORPPME",
    "CORPLP",
    "NETPME",
}
HOME_ANALITICA_INDICADORES_NORM = {
    "PEDIDOS",
    "LIGACOES",
    "VENDA BRUTA",
    "VENDAS BRUTAS",
    "GROSS BRUTO",
    "GROSS LIQUIDO",
    "INSTALACAO",
    "INSTALADOS",
    "INSTAL",
}


def normalizar_chave_visual(texto: object) -> str:
    base = unicodedata.normalize("NFKD", str(texto or ""))
    base = base.encode("ASCII", "ignore").decode("ASCII").lower()
    return re.sub(r"[^a-z0-9]+", " ", base).strip()


BACKLOG_MAPEAMENTO_CANAIS_NORM = {
    normalizar_chave_visual("DAC"): "S2S+DAC",
    normalizar_chave_visual("DAC Adequacao de Pacote"): "S2S+DAC",
    normalizar_chave_visual("Hospitality PME"): "Hospitality PME",
    normalizar_chave_visual("Internet"): "E-Commerce",
    normalizar_chave_visual("Ativo Aquisicao Direto"): "Televendas Ativo",
    normalizar_chave_visual("Ativo Aquisicao Indireto"): "Televendas Ativo",
    normalizar_chave_visual("Ativo Rentabilizacao Indireto"): "Televendas Ativo",
    normalizar_chave_visual("Receptivo"): "Televendas Receptivo",
    normalizar_chave_visual("Receptivo Rentabilizacao Exclusivo"): "Televendas Receptivo",
    normalizar_chave_visual("Inside Sales"): "Consultivo Remoto",
    normalizar_chave_visual("Consultivo Remoto"): "Consultivo Remoto",
}


def normalizar_numerico_serie(serie: object) -> pd.Series:
    if not isinstance(serie, pd.Series):
        serie = pd.Series(serie)
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_numeric(serie, errors="coerce")
    s = serie.astype(str).str.strip()
    s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    s = s.str.replace(r"[^0-9\.-]", "", regex=True)
    return pd.to_numeric(s, errors="coerce")


def compactar_colunas_categoricas(df: pd.DataFrame, colunas: list[str] | tuple[str, ...]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    for coluna in colunas:
        if coluna not in df.columns:
            continue
        serie = df[coluna]
        if isinstance(serie.dtype, pd.CategoricalDtype):
            continue
        if not (pd.api.types.is_object_dtype(serie) or pd.api.types.is_string_dtype(serie)):
            continue
        try:
            total = int(len(serie))
            nunique = int(serie.nunique(dropna=False))
            if total > 0 and nunique <= max(64, int(total * 0.50)):
                df[coluna] = serie.astype("category")
        except Exception:
            continue
    return df


def formatar_mes_ano(data_valor: object) -> str | None:
    if pd.isna(data_valor):
        return None
    ts = pd.Timestamp(data_valor)
    return f"{MONTHS_PT.get(int(ts.month), 'jan')}/{ts.strftime('%y')}"


def normalizar_texto_chave(valor: object) -> str:
    if pd.isna(valor):
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = texto.encode("ASCII", "ignore").decode("ASCII")
    texto = texto.strip().upper()
    texto = re.sub(r"[^A-Z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


CANAL_CONSULTIVO_REMOTO = "Consultivo Remoto"
CANAL_CONSULTIVO_REMOTO_ALIASES = {"INSIDE SALES", "CONSULTIVO REMOTO", "CONSULTIVO REMOVO"}


def normalizar_canal_plan(valor: object) -> str:
    texto = normalizar_texto_chave(valor)
    if not texto:
        return ""
    if texto in CANAL_CONSULTIVO_REMOTO_ALIASES:
        return CANAL_CONSULTIVO_REMOTO
    return str(valor).strip()


def normalizar_plataforma_chave(valor: object) -> str:
    texto = normalizar_texto_chave(valor)
    if "FIXA" in texto:
        return "FIXA"
    if ("MOVEL" in texto) or ("MOBILE" in texto):
        return "CONTA"
    if "CONTA" in texto:
        return "CONTA"
    return texto


def normalizar_rotulo_produto(valor: object) -> str:
    base = normalizar_texto_chave(valor)
    if not base:
        return ""
    if "FIXA" in base:
        return "FIXA"
    if ("MOVEL" in base) or ("MOBILE" in base):
        return "CONTA"
    if "CONTA" in base:
        return "CONTA"
    if "CLICK" in base and "CALL" in base:
        return "CLICK TO CALL"
    if base == "CTC":
        return "CLICK TO CALL"
    return base


def mapear_indicador_canonico(valor: object) -> str:
    texto = normalizar_texto_chave(valor)
    if not texto:
        return ""
    if "LIGAC" in texto:
        return "LIGACOES"
    if "PEDID" in texto:
        return "PEDIDOS"
    if "INSTAL" in texto:
        return "INSTALACAO"
    if "GROSS" in texto and "LIQ" in texto:
        return "GROSS LIQUIDO"
    if "GROSS" in texto and "BRUT" in texto:
        return "GROSS BRUTO"
    if "VEND" in texto and "BRUT" in texto:
        return "VENDA BRUTA"
    return texto


def normalizar_segmento_funil_fixa(valor: object) -> str:
    chave = normalizar_texto_chave(valor)
    if chave == "RESIDENCIAL CABO":
        return "PF"
    if chave == "PF":
        return "PF"
    if chave == "PME":
        return "PME"
    return str(valor).strip()


def salvar_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, compression="snappy")


def localizar_diretorio_bruto(raw_dir: str | None) -> Path:
    if raw_dir:
        path = Path(raw_dir).expanduser().resolve()
        if path.exists():
            return path
    for candidato in RAW_DIR_CANDIDATES:
        if candidato.exists():
            return candidato
    raise FileNotFoundError("Nenhum diretorio bruto encontrado.")


def resolver_arquivo(raw_dir: Path, nome_arquivo: str) -> Path:
    candidatos = [
        raw_dir / nome_arquivo,
        ROOT_DIR / "Arquivos_Dashboard" / nome_arquivo,
        ROOT_DIR / nome_arquivo,
    ]
    for candidato in candidatos:
        if candidato.exists():
            return candidato
    return candidatos[0]


def resolver_arquivo_convergencia(raw_dir: Path) -> Path:
    nome_arquivo = "base_convergencia.xlsx"
    candidatos = [
        raw_dir / nome_arquivo,
        ROOT_DIR / "Arquivos_Dashboard" / nome_arquivo,
        ROOT_DIR / nome_arquivo,
        Path.home() / "OneDrive - Claro SA" / "Documentos" / "Extração_VDI" / "FÍSICOS_MOBILIDADE" / nome_arquivo,
        Path(r"C:\Users\F270665\OneDrive - Claro SA\Documentos\Extração_VDI\FÍSICOS_MOBILIDADE\base_convergencia.xlsx"),
    ]
    for candidato in candidatos:
        if candidato.exists():
            return candidato
    return candidatos[0]


def materializar_excel_para_pandas(path: Path) -> Path:
    """Garante leitura de arquivos em pastas sincronizadas que o Python nao consegue abrir direto."""
    try:
        with open(path, "rb") as arquivo_teste:
            arquivo_teste.read(1)
        return path
    except OSError:
        tmp_dir = ROOT_DIR / ".tmp_preprocess_read"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{path.stem}_materializado{path.suffix}"
        env = os.environ.copy()
        env["SRC_PATH_PREPROCESS"] = str(path)
        env["DST_PATH_PREPROCESS"] = str(tmp_path)
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Copy-Item -LiteralPath $env:SRC_PATH_PREPROCESS -Destination $env:DST_PATH_PREPROCESS -Force",
            ],
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
        return tmp_path


def encontrar_coluna_por_alias(colunas, *aliases: str) -> str | None:
    mapa_colunas: dict[str, str] = {}
    for coluna in list(colunas or []):
        chave = normalizar_chave_visual(coluna)
        if chave and chave not in mapa_colunas:
            mapa_colunas[chave] = coluna
    for alias in aliases:
        coluna_real = mapa_colunas.get(normalizar_chave_visual(alias))
        if coluna_real:
            return coluna_real
    return None


def normalizar_produto_convergencia(valor: object) -> str:
    texto = normalizar_texto_chave(valor)
    if not texto:
        return ""
    if "FIXA BRUTA" in texto:
        return "IGNORAR"
    if "FIXA" in texto:
        return "FIXA"
    if "MOVEL" in texto or "MOBILE" in texto or "CONTA" in texto:
        return "CONTA"
    return texto


def build_performance_base(df_base: pd.DataFrame) -> pd.DataFrame:
    colunas_saida = [
        "REGIONAL",
        "CANAL_PLAN",
        "CANAL_NORM",
        "COD_PLATAFORMA",
        "PLATAFORMA_NORM",
        "DSC_INDICADOR",
        "INDICADOR_NORM",
        "INDICADOR_CANONICO",
        "dat_tratada",
        "ANO_REF",
        "QTDE",
        "DESAFIO_QTD",
        "TEND_QTD",
    ]
    if df_base is None or df_base.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_work = df_base.copy()
    for coluna in ["REGIONAL", "CANAL_PLAN", "COD_PLATAFORMA", "DSC_INDICADOR", "dat_tratada"]:
        if coluna in df_work.columns:
            df_work[coluna] = df_work[coluna].astype(str).str.strip()

    df_work["REGIONAL"] = df_work["REGIONAL"].astype(str).str.strip().str[:3].str.upper()
    df_work["CANAL_PLAN"] = df_work["CANAL_PLAN"].apply(normalizar_canal_plan)
    df_work["COD_PLATAFORMA"] = df_work["COD_PLATAFORMA"].astype(str).str.strip().str.upper()
    df_work["DSC_INDICADOR"] = df_work["DSC_INDICADOR"].astype(str).str.strip()
    df_work["dat_tratada"] = df_work["dat_tratada"].astype(str).str.strip().str.lower()
    df_work = df_work[df_work["dat_tratada"].str.match(r"^[a-z]{3}/\d{2}$", na=False)].copy()
    if df_work.empty:
        return pd.DataFrame(columns=colunas_saida)

    for coluna in ["QTDE", "DESAFIO_QTD", "TEND_QTD"]:
        if coluna not in df_work.columns:
            df_work[coluna] = 0
        df_work[coluna] = normalizar_numerico_serie(df_work[coluna]).fillna(0)

    df_work["CANAL_NORM"] = df_work["CANAL_PLAN"].apply(normalizar_texto_chave)
    df_work["PLATAFORMA_NORM"] = df_work["COD_PLATAFORMA"].apply(normalizar_plataforma_chave)
    df_work["INDICADOR_NORM"] = df_work["DSC_INDICADOR"].apply(normalizar_texto_chave)
    df_work["INDICADOR_CANONICO"] = df_work["DSC_INDICADOR"].apply(mapear_indicador_canonico)
    df_work["ANO_REF"] = df_work["dat_tratada"].str.split("/").str[1].fillna("")

    df_saida = (
        df_work.groupby(
            [
                "REGIONAL",
                "CANAL_PLAN",
                "CANAL_NORM",
                "COD_PLATAFORMA",
                "PLATAFORMA_NORM",
                "DSC_INDICADOR",
                "INDICADOR_NORM",
                "INDICADOR_CANONICO",
                "dat_tratada",
                "ANO_REF",
            ],
            as_index=False,
            observed=True,
            dropna=False,
        )[["QTDE", "DESAFIO_QTD", "TEND_QTD"]]
        .sum()
    )
    compactar_colunas_categoricas(
        df_saida,
        [
            "REGIONAL",
            "CANAL_PLAN",
            "CANAL_NORM",
            "COD_PLATAFORMA",
            "PLATAFORMA_NORM",
            "DSC_INDICADOR",
            "INDICADOR_NORM",
            "INDICADOR_CANONICO",
            "dat_tratada",
            "ANO_REF",
        ],
    )
    return df_saida[colunas_saida]


def build_analitica_diaria(df_base: pd.DataFrame) -> pd.DataFrame:
    colunas_saida = [
        "CANAL_PLAN",
        "COD_PLATAFORMA",
        "REGIONAL",
        "dat_tratada",
        "MES_NORM",
        "QTDE",
        "DESAFIO_QTD",
        "TEND_QTD",
        "DAT_MOVIMENTO2",
        "DATA_DIA",
        "DSC_INDICADOR",
        "DSC_IND_NORM",
        "IND_NORM",
    ]
    if df_base is None or df_base.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_work = df_base[
        [
            col
            for col in [
                "CANAL_PLAN",
                "COD_PLATAFORMA",
                "REGIONAL",
                "dat_tratada",
                "QTDE",
                "DESAFIO_QTD",
                "TEND_QTD",
                "DAT_MOVIMENTO2",
                "DSC_INDICADOR",
            ]
            if col in df_base.columns
        ]
    ].copy()
    df_work["DAT_MOVIMENTO2"] = pd.to_datetime(df_work["DAT_MOVIMENTO2"], errors="coerce")
    df_work = df_work[df_work["DAT_MOVIMENTO2"].notna()].copy()
    if df_work.empty:
        return pd.DataFrame(columns=colunas_saida)

    for coluna in ["CANAL_PLAN", "COD_PLATAFORMA", "REGIONAL", "dat_tratada", "DSC_INDICADOR"]:
        df_work[coluna] = df_work[coluna].astype(str).str.strip()
    df_work["CANAL_PLAN"] = df_work["CANAL_PLAN"].apply(normalizar_canal_plan)
    for coluna in ["QTDE", "DESAFIO_QTD", "TEND_QTD"]:
        df_work[coluna] = normalizar_numerico_serie(df_work[coluna]).fillna(0)

    df_work["COD_PLATAFORMA"] = df_work["COD_PLATAFORMA"].apply(normalizar_plataforma_chave)
    df_work["REGIONAL"] = df_work["REGIONAL"].astype(str).str.strip().str[:3].str.upper()
    df_work["DATA_DIA"] = df_work["DAT_MOVIMENTO2"].dt.normalize()
    df_work["MES_NORM"] = df_work["dat_tratada"].astype(str).str.strip().str.lower()
    df_work["DSC_IND_NORM"] = df_work["DSC_INDICADOR"].apply(normalizar_texto_chave)
    df_work["IND_NORM"] = df_work["DSC_IND_NORM"]

    df_saida = (
        df_work.groupby(
            [
                "CANAL_PLAN",
                "COD_PLATAFORMA",
                "REGIONAL",
                "dat_tratada",
                "MES_NORM",
                "DATA_DIA",
                "DSC_INDICADOR",
                "DSC_IND_NORM",
                "IND_NORM",
            ],
            as_index=False,
            observed=True,
            dropna=False,
        )[["QTDE", "DESAFIO_QTD", "TEND_QTD"]]
        .sum()
    )
    df_saida["DAT_MOVIMENTO2"] = pd.to_datetime(df_saida["DATA_DIA"], errors="coerce")
    compactar_colunas_categoricas(
        df_saida,
        [
            "CANAL_PLAN",
            "COD_PLATAFORMA",
            "REGIONAL",
            "dat_tratada",
            "MES_NORM",
            "DSC_INDICADOR",
            "DSC_IND_NORM",
            "IND_NORM",
        ],
    )
    return df_saida[colunas_saida]


def filtrar_regra_cotacoes_novas_linhas(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=getattr(df, "columns", []))
    atividades_norm = df["LISTA_ATIVIDADES"].astype(str).map(normalizar_chave_visual)
    canal_venda_norm = df["CANAL_DE_VENDA"].astype(str).map(normalizar_chave_visual).str.upper()
    mask_regra = (
        df["QTD_NOVAS_LINHAS_ATIVAR"].ne(0)
        & df["QTD_LINHAS_VOZ"].ne(0)
        & canal_venda_norm.isin(ALLOWED_CANAIS_VENDA_COTACOES)
        & (
            atividades_norm.str.contains(r"\bnovo\b", regex=True, na=False)
            | atividades_norm.str.contains(r"\bincremento de linhas\b", regex=True, na=False)
        )
    )
    return df.loc[mask_regra].copy()


def prepare_base_principal(raw_dir: Path, out_dir: Path) -> pd.DataFrame:
    path = resolver_arquivo(raw_dir, "base_final_trt_new3.xlsx")
    header_df = pd.read_excel(path, nrows=0)
    colunas_disponiveis = list(getattr(header_df, "columns", []))
    colunas_leitura = [col for col in PRIMARY_BASE_USECOLS if col in colunas_disponiveis]
    df = pd.read_excel(path, usecols=colunas_leitura or None)

    rename_map: dict[str, str] = {}
    if "REGIONAL" not in df.columns and "DSC_REGIONAL_CMV" in df.columns:
        rename_map["DSC_REGIONAL_CMV"] = "REGIONAL"
    if "CANAL_PLAN" not in df.columns and "DSC_CANAL" in df.columns:
        rename_map["DSC_CANAL"] = "CANAL_PLAN"
    if "DAT_MOVIMENTO2" not in df.columns:
        for coluna_alt in ["DAT_MOVIMENTO", "DAT_MOVIMENTO_2", "PERIODO"]:
            if coluna_alt in df.columns:
                rename_map[coluna_alt] = "DAT_MOVIMENTO2"
                break
    if "ID_AFILIADOS" not in df.columns and "ID_AFILIADO" in df.columns:
        rename_map["ID_AFILIADO"] = "ID_AFILIADOS"
    if "ORIGEM_AFILIADOS" not in df.columns and "ORIGEM_AFILIADO" in df.columns:
        rename_map["ORIGEM_AFILIADO"] = "ORIGEM_AFILIADOS"
    if rename_map:
        df = df.rename(columns=rename_map)

    df["DAT_MOVIMENTO2"] = pd.to_datetime(df.get("DAT_MOVIMENTO2"), errors="coerce")
    df["REGIONAL"] = df.get("REGIONAL", "").astype("string").str.strip().str[:3].str.upper()
    df["CANAL_PLAN"] = df.get("CANAL_PLAN", "").astype("string").str.strip().map(normalizar_canal_plan).astype("string")
    df["DSC_INDICADOR"] = df.get("DSC_INDICADOR", "").astype("string").str.strip()
    df["DSC_MOTIVO_STS"] = df.get("DSC_MOTIVO_STS", "").astype("string").str.strip()
    df["COD_PLATAFORMA"] = df.get("COD_PLATAFORMA", "").astype("string").str.strip()
    df["ID_AFILIADOS"] = df.get("ID_AFILIADOS", "").astype("string").str.strip()
    df["ORIGEM_AFILIADOS"] = df.get("ORIGEM_AFILIADOS", "").astype("string").str.strip()

    for coluna in ["QTDE", "DESAFIO_QTD", "TEND_QTD"]:
        if coluna not in df.columns:
            df[coluna] = 0
        df[coluna] = normalizar_numerico_serie(df[coluna]).fillna(0)

    if "mes_ano" not in df.columns:
        df["mes_ano"] = df["DAT_MOVIMENTO2"].apply(formatar_mes_ano)
    else:
        df["mes_ano"] = df["mes_ano"].astype("string").str.strip().str.lower()
        mask_mes_vazio = df["mes_ano"].isin(["", "nan", "none", "<na>"])
        df.loc[mask_mes_vazio, "mes_ano"] = df.loc[mask_mes_vazio, "DAT_MOVIMENTO2"].apply(formatar_mes_ano)

    if "dat_tratada" not in df.columns:
        df["dat_tratada"] = df["mes_ano"]
    else:
        df["dat_tratada"] = df["dat_tratada"].astype("string").str.strip().str.lower()
        mask_data_vazio = df["dat_tratada"].isin(["", "nan", "none", "<na>"])
        df.loc[mask_data_vazio, "dat_tratada"] = df.loc[mask_data_vazio, "mes_ano"]

    if "ANO" not in df.columns:
        df["ANO"] = df["DAT_MOVIMENTO2"].dt.year
    else:
        df["ANO"] = normalizar_numerico_serie(df["ANO"])
        mask_ano_vazio = df["ANO"].isna()
        df.loc[mask_ano_vazio, "ANO"] = df.loc[mask_ano_vazio, "DAT_MOVIMENTO2"].dt.year

    if "DAT_MÊS" not in df.columns:
        df["DAT_MÊS"] = df["DAT_MOVIMENTO2"].dt.month
    else:
        df["DAT_MÊS"] = normalizar_numerico_serie(df["DAT_MÊS"])
        mask_mes_num_vazio = df["DAT_MÊS"].isna()
        df.loc[mask_mes_num_vazio, "DAT_MÊS"] = df.loc[mask_mes_num_vazio, "DAT_MOVIMENTO2"].dt.month

    if "dia_semana" not in df.columns:
        df["dia_semana"] = df["DAT_MOVIMENTO2"].dt.day_name().str.lower()
    else:
        df["dia_semana"] = df["dia_semana"].astype("string").str.strip().str.lower()

    df = df[
        [
            col
            for col in [
                "REGIONAL",
                "CANAL_PLAN",
                "dat_tratada",
                "mes_ano",
                "DSC_INDICADOR",
                "DSC_MOTIVO_STS",
                "COD_PLATAFORMA",
                "DAT_MOVIMENTO2",
                "QTDE",
                "DESAFIO_QTD",
                "TEND_QTD",
                "ANO",
                "DAT_MÊS",
                "dia_semana",
                "ID_AFILIADOS",
                "ORIGEM_AFILIADOS",
            ]
            if col in df.columns
        ]
    ].copy()
    compactar_colunas_categoricas(
        df,
        [
            "REGIONAL",
            "CANAL_PLAN",
            "dat_tratada",
            "mes_ano",
            "DSC_INDICADOR",
            "DSC_MOTIVO_STS",
            "COD_PLATAFORMA",
            "dia_semana",
            "ID_AFILIADOS",
            "ORIGEM_AFILIADOS",
        ],
    )
    salvar_parquet(df, out_dir / "base_principal.parquet")
    print(f"[ok] base_principal.parquet -> {len(df):,} linhas")
    return df


def prepare_base_principal_mensal(df_base: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    if df_base.empty:
        df_saida = pd.DataFrame()
    else:
        df_saida = (
            df_base.groupby(
                [
                    "REGIONAL",
                    "CANAL_PLAN",
                    "COD_PLATAFORMA",
                    "DSC_INDICADOR",
                    "DSC_MOTIVO_STS",
                    "dat_tratada",
                    "mes_ano",
                    "ANO",
                    "DAT_MÊS",
                ],
                as_index=False,
                observed=True,
                dropna=False,
            )[["QTDE", "DESAFIO_QTD", "TEND_QTD"]]
            .sum()
        )
        df_max_data = (
            df_base.groupby(
                [
                    "REGIONAL",
                    "CANAL_PLAN",
                    "COD_PLATAFORMA",
                    "DSC_INDICADOR",
                    "DSC_MOTIVO_STS",
                    "dat_tratada",
                    "mes_ano",
                    "ANO",
                    "DAT_MÊS",
                ],
                as_index=False,
                observed=True,
                dropna=False,
            )["DAT_MOVIMENTO2"]
            .max()
        )
        df_saida = df_saida.merge(
            df_max_data,
            on=[
                "REGIONAL",
                "CANAL_PLAN",
                "COD_PLATAFORMA",
                "DSC_INDICADOR",
                "DSC_MOTIVO_STS",
                "dat_tratada",
                "mes_ano",
                "ANO",
                "DAT_MÊS",
            ],
            how="left",
        )
        compactar_colunas_categoricas(
            df_saida,
            [
                "REGIONAL",
                "CANAL_PLAN",
                "COD_PLATAFORMA",
                "DSC_INDICADOR",
                "DSC_MOTIVO_STS",
                "dat_tratada",
                "mes_ano",
            ],
        )
    salvar_parquet(df_saida, out_dir / "base_principal_mensal.parquet")
    print(f"[ok] base_principal_mensal.parquet -> {len(df_saida):,} linhas")
    return df_saida


def prepare_pedidos_ecommerce(df_base: pd.DataFrame, out_dir: Path) -> None:
    if df_base.empty:
        df_saida = pd.DataFrame()
    else:
        mask = (
            df_base["CANAL_PLAN"].astype(str).str.strip().eq("E-Commerce")
            & df_base["DSC_INDICADOR"].astype(str).str.strip().str.upper().eq("PEDIDOS")
        )
        df_filtrado = df_base.loc[mask].copy()
        df_saida = (
            df_filtrado.groupby(
                [
                    "REGIONAL",
                    "CANAL_PLAN",
                    "COD_PLATAFORMA",
                    "DSC_INDICADOR",
                    "dat_tratada",
                    "mes_ano",
                    "ANO",
                    "DAT_MÊS",
                    "ID_AFILIADOS",
                    "ORIGEM_AFILIADOS",
                ],
                as_index=False,
                observed=True,
                dropna=False,
            )[["QTDE", "DESAFIO_QTD", "TEND_QTD"]]
            .sum()
        )
        df_max_data = (
            df_filtrado.groupby(
                [
                    "REGIONAL",
                    "CANAL_PLAN",
                    "COD_PLATAFORMA",
                    "DSC_INDICADOR",
                    "dat_tratada",
                    "mes_ano",
                    "ANO",
                    "DAT_MÊS",
                    "ID_AFILIADOS",
                    "ORIGEM_AFILIADOS",
                ],
                as_index=False,
                observed=True,
                dropna=False,
            )["DAT_MOVIMENTO2"]
            .max()
        )
        df_saida = df_saida.merge(
            df_max_data,
            on=[
                "REGIONAL",
                "CANAL_PLAN",
                "COD_PLATAFORMA",
                "DSC_INDICADOR",
                "dat_tratada",
                "mes_ano",
                "ANO",
                "DAT_MÊS",
                "ID_AFILIADOS",
                "ORIGEM_AFILIADOS",
            ],
            how="left",
        )
        compactar_colunas_categoricas(
            df_saida,
            [
                "REGIONAL",
                "CANAL_PLAN",
                "COD_PLATAFORMA",
                "DSC_INDICADOR",
                "dat_tratada",
                "mes_ano",
                "ID_AFILIADOS",
                "ORIGEM_AFILIADOS",
            ],
        )
    salvar_parquet(df_saida, out_dir / "pedidos_ecommerce.parquet")
    print(f"[ok] pedidos_ecommerce.parquet -> {len(df_saida):,} linhas")


def prepare_ativados_base(df_mensal: pd.DataFrame, out_dir: Path) -> None:
    if df_mensal.empty:
        df_saida = pd.DataFrame()
    else:
        ind_norm = df_mensal["DSC_INDICADOR"].astype(str).apply(normalizar_texto_chave)
        mask = ind_norm.str.contains("GROSS LIQ", na=False) | ind_norm.str.contains("INSTAL", na=False)
        df_saida = df_mensal.loc[mask].copy()
        compactar_colunas_categoricas(
            df_saida,
            [
                "REGIONAL",
                "CANAL_PLAN",
                "COD_PLATAFORMA",
                "DSC_INDICADOR",
                "DSC_MOTIVO_STS",
                "dat_tratada",
                "mes_ano",
            ],
        )
    salvar_parquet(df_saida, out_dir / "ativados_base.parquet")
    print(f"[ok] ativados_base.parquet -> {len(df_saida):,} linhas")


def prepare_gross_motivo_status(df_mensal: pd.DataFrame, out_dir: Path) -> None:
    colunas_saida = ["dat_tratada", "CANAL_PLAN", "REGIONAL", "MOTIVO_STS", "QTDE"]
    colunas_minimas = {
        "dat_tratada",
        "CANAL_PLAN",
        "REGIONAL",
        "COD_PLATAFORMA",
        "DSC_INDICADOR",
        "DSC_MOTIVO_STS",
        "QTDE",
    }
    if df_mensal is None or df_mensal.empty or not colunas_minimas.issubset(set(df_mensal.columns)):
        df_saida = pd.DataFrame(columns=colunas_saida)
    else:
        df_work = df_mensal[list(colunas_minimas)].copy()
        for coluna in ["dat_tratada", "CANAL_PLAN", "REGIONAL", "COD_PLATAFORMA", "DSC_INDICADOR", "DSC_MOTIVO_STS"]:
            df_work[coluna] = df_work[coluna].astype(str).str.strip()

        df_work["dat_tratada"] = df_work["dat_tratada"].astype(str).str.strip().str.lower()
        df_work = df_work[df_work["dat_tratada"].str.match(r"^[a-z]{3}/\d{2}$", na=False)].copy()

        if df_work.empty:
            df_saida = pd.DataFrame(columns=colunas_saida)
        else:
            df_work["COD_PLATAFORMA"] = df_work["COD_PLATAFORMA"].apply(normalizar_rotulo_produto)
            indicador_norm = df_work["DSC_INDICADOR"].map(normalizar_chave_visual)
            mask_gross_conta = (
                df_work["COD_PLATAFORMA"].eq("CONTA")
                & indicador_norm.str.contains("gross", na=False)
                & indicador_norm.str.contains("liq", na=False)
            )
            df_work = df_work.loc[mask_gross_conta].copy()

            if df_work.empty:
                df_saida = pd.DataFrame(columns=colunas_saida)
            else:
                df_work["QTDE"] = normalizar_numerico_serie(df_work["QTDE"]).fillna(0)
                df_work["CANAL_PLAN"] = df_work["CANAL_PLAN"].replace({"": "Canal nao informado", "nan": "Canal nao informado"})
                df_work["REGIONAL"] = df_work["REGIONAL"].replace({"": "N/I", "nan": "N/I"})
                df_work["MOTIVO_STS"] = (
                    df_work["DSC_MOTIVO_STS"]
                    .replace({"": "Nao informado", "nan": "Nao informado", "None": "Nao informado", "NULL": "Nao informado"})
                    .astype(str)
                    .str.strip()
                )
                df_work = df_work.loc[df_work["QTDE"].ne(0)].copy()
                if df_work.empty:
                    df_saida = pd.DataFrame(columns=colunas_saida)
                else:
                    df_saida = df_work[colunas_saida].copy()
                    compactar_colunas_categoricas(df_saida, ["dat_tratada", "CANAL_PLAN", "REGIONAL", "MOTIVO_STS"])

    salvar_parquet(df_saida, out_dir / "gross_motivo_status.parquet")
    print(f"[ok] gross_motivo_status.parquet -> {len(df_saida):,} linhas")


def prepare_base_performance(df_base: pd.DataFrame, out_dir: Path) -> None:
    df_saida = build_performance_base(df_base)
    salvar_parquet(df_saida, out_dir / "base_performance_mensal.parquet")
    print(f"[ok] base_performance_mensal.parquet -> {len(df_saida):,} linhas")


def prepare_analitica(df_base: pd.DataFrame, out_dir: Path) -> None:
    df_saida = build_analitica_diaria(df_base)
    salvar_parquet(df_saida, out_dir / "analitica_diaria.parquet")
    print(f"[ok] analitica_diaria.parquet -> {len(df_saida):,} linhas")

    if df_saida.empty:
        df_home_diaria = pd.DataFrame()
        df_home_mensal = pd.DataFrame()
    else:
        ind_norm = df_saida["IND_NORM"].astype(str).str.strip().str.upper()
        df_home_diaria = df_saida.loc[ind_norm.isin(HOME_ANALITICA_INDICADORES_NORM)].copy()
        if not df_home_diaria.empty:
            compactar_colunas_categoricas(
                df_home_diaria,
                [
                    "CANAL_PLAN",
                    "COD_PLATAFORMA",
                    "REGIONAL",
                    "dat_tratada",
                    "MES_NORM",
                    "DSC_INDICADOR",
                    "DSC_IND_NORM",
                    "IND_NORM",
                ],
            )
            df_home_mensal = (
                df_home_diaria.groupby(
                    [
                        "CANAL_PLAN",
                        "COD_PLATAFORMA",
                        "REGIONAL",
                        "dat_tratada",
                        "MES_NORM",
                        "DSC_INDICADOR",
                        "DSC_IND_NORM",
                        "IND_NORM",
                    ],
                    as_index=False,
                    observed=True,
                    dropna=False,
                )[["QTDE", "DESAFIO_QTD", "TEND_QTD"]]
                .sum()
            )
            compactar_colunas_categoricas(
                df_home_mensal,
                [
                    "CANAL_PLAN",
                    "COD_PLATAFORMA",
                    "REGIONAL",
                    "dat_tratada",
                    "MES_NORM",
                    "DSC_INDICADOR",
                    "DSC_IND_NORM",
                    "IND_NORM",
                ],
            )
        else:
            df_home_mensal = pd.DataFrame()

    salvar_parquet(df_home_diaria, out_dir / "home_analitica_diaria.parquet")
    print(f"[ok] home_analitica_diaria.parquet -> {len(df_home_diaria):,} linhas")
    salvar_parquet(df_home_mensal, out_dir / "home_analitica_mensal.parquet")
    print(f"[ok] home_analitica_mensal.parquet -> {len(df_home_mensal):,} linhas")


def prepare_ligacoes(raw_dir: Path, out_dir: Path) -> pd.DataFrame:
    path = resolver_arquivo(raw_dir, "televendas_ligacoes2.xlsx")
    header_df = pd.read_excel(path, nrows=0)
    colunas_disponiveis = set(header_df.columns)
    coluna_data = next((col for col in ["DATA_MOVIMENTO", "DAT_MOVIMENTO", "DAT_MOVIMENTO2", "PERIODO"] if col in colunas_disponiveis), None)
    colunas_leitura = ["QTD", "CABEADO", coluna_data]
    for col_opcional in ["DSC_REGIONAL_CMV", "REGIONAL", "TELEFONE", "CANAL_PLAN", "COD_PLATAFORMA"]:
        if col_opcional in colunas_disponiveis and col_opcional not in colunas_leitura:
            colunas_leitura.append(col_opcional)
    df = pd.read_excel(path, usecols=[col for col in colunas_leitura if col])

    serie_data = pd.to_datetime(df[coluna_data], errors="coerce")
    df = df.loc[serie_data.notna()].copy()
    df["DAT_MOVIMENTO2"] = serie_data.loc[df.index]
    df["DATA_DIA"] = df["DAT_MOVIMENTO2"].dt.normalize()
    df["mes_ano"] = df["DAT_MOVIMENTO2"].apply(formatar_mes_ano)
    df["dat_tratada"] = df["mes_ano"]
    col_reg = next((c for c in ["DSC_REGIONAL_CMV", "REGIONAL"] if c in df.columns), None)
    df["REGIONAL"] = (
        df[col_reg].astype(str).str.strip().str[:3].str.upper() if col_reg else pd.Series("", index=df.index, dtype="string")
    )
    df["QTDE"] = normalizar_numerico_serie(df["QTD"]).fillna(0)
    df["CABEADO"] = df.get("CABEADO", "").astype("string").str.strip()
    df["TELEFONE"] = df.get("TELEFONE", "").astype("string").str.strip()
    df["TIPO_CHAMADA"] = np.where(
        df["TELEFONE"].str.contains("0960|8449", regex=True, na=False),
        "Click to Call",
        "DEMAIS",
    )
    df["FLAG_FIXA"] = df["CABEADO"].astype(str).str.strip().str.upper().isin({"SIM", "S", "TRUE", "1", "FIXA"})
    df["COD_PLATAFORMA"] = np.where(df["FLAG_FIXA"], "FIXA", "CONTA")
    df["TIPO"] = np.where(df["FLAG_FIXA"], "FIXA", np.where(df["TIPO_CHAMADA"].eq("DEMAIS"), "CONTA", "CLICK TO CALL"))
    df["CANAL_PLAN"] = "Televendas Receptivo"
    df["DSC_INDICADOR"] = "LIGACOES"
    df["DESAFIO_QTD"] = 0

    df_detalhe = (
        df.groupby(
            [
                "DAT_MOVIMENTO2",
                "DATA_DIA",
                "mes_ano",
                "dat_tratada",
                "REGIONAL",
                "CANAL_PLAN",
                "COD_PLATAFORMA",
                "DSC_INDICADOR",
                "CABEADO",
                "TIPO_CHAMADA",
                "TIPO",
                "FLAG_FIXA",
            ],
            as_index=False,
            observed=True,
            dropna=False,
        )[["QTDE", "DESAFIO_QTD"]]
        .sum()
    )
    df_detalhe["TELEFONE"] = ""
    compactar_colunas_categoricas(
        df_detalhe,
        [
            "mes_ano",
            "dat_tratada",
            "REGIONAL",
            "CANAL_PLAN",
            "COD_PLATAFORMA",
            "DSC_INDICADOR",
            "CABEADO",
            "TIPO_CHAMADA",
            "TIPO",
        ],
    )
    salvar_parquet(df_detalhe, out_dir / "ligacoes_receptivo.parquet")
    print(f"[ok] ligacoes_receptivo.parquet -> {len(df_detalhe):,} linhas")

    df_month = df_detalhe.copy()
    df_month["ANO"] = pd.to_datetime(df_month["DAT_MOVIMENTO2"], errors="coerce").dt.year.astype("Int64")
    df_month["MES_NUM"] = pd.to_datetime(df_month["DAT_MOVIMENTO2"], errors="coerce").dt.month.astype("Int64")
    df_month["TOTAL_QTD"] = df_month["QTDE"]
    df_month["FIXA_QTD"] = np.where(df_month["FLAG_FIXA"].astype(bool), df_month["QTDE"], 0)
    df_month["CONTA_QTD"] = np.where(df_month["TIPO_CHAMADA"].astype(str).eq("DEMAIS"), df_month["QTDE"], 0)
    df_month["CTC_QTD"] = np.where(df_month["TIPO_CHAMADA"].astype(str).eq("Click to Call"), df_month["QTDE"], 0)
    agg_reg = (
        df_month.groupby(["REGIONAL", "mes_ano", "ANO", "MES_NUM"], as_index=False, observed=True, dropna=False)[
            ["TOTAL_QTD", "FIXA_QTD", "CONTA_QTD", "CTC_QTD"]
        ].sum()
    )
    agg_total = (
        agg_reg.groupby(["mes_ano", "ANO", "MES_NUM"], as_index=False, observed=True, dropna=False)[
            ["TOTAL_QTD", "FIXA_QTD", "CONTA_QTD", "CTC_QTD"]
        ].sum()
    )
    agg_total["REGIONAL"] = "Todas"
    df_lig_mensal = pd.concat([agg_reg, agg_total], ignore_index=True)
    compactar_colunas_categoricas(df_lig_mensal, ["REGIONAL", "mes_ano"])
    salvar_parquet(df_lig_mensal, out_dir / "ligacoes_mensal_agregado.parquet")
    print(f"[ok] ligacoes_mensal_agregado.parquet -> {len(df_lig_mensal):,} linhas")

    mask_fixa = df_detalhe["FLAG_FIXA"].astype(bool)
    mask_conta = df_detalhe["TIPO_CHAMADA"].astype(str).eq("DEMAIS")
    df_lig_perf_src = pd.concat(
        [
            df_detalhe.loc[mask_fixa].assign(COD_PLATAFORMA="FIXA", TEND_QTD=lambda x: x["QTDE"]),
            df_detalhe.loc[mask_conta].assign(COD_PLATAFORMA="CONTA", TEND_QTD=lambda x: x["QTDE"]),
        ],
        ignore_index=True,
    )[
        [
            "REGIONAL",
            "CANAL_PLAN",
            "COD_PLATAFORMA",
            "DSC_INDICADOR",
            "dat_tratada",
            "QTDE",
            "DESAFIO_QTD",
            "TEND_QTD",
        ]
    ]
    df_lig_perf = build_performance_base(df_lig_perf_src)
    salvar_parquet(df_lig_perf, out_dir / "ligacoes_performance_mensal.parquet")
    print(f"[ok] ligacoes_performance_mensal.parquet -> {len(df_lig_perf):,} linhas")
    return df_detalhe


def prepare_evolucao_mensal(df_base_mensal: pd.DataFrame, df_ligacoes: pd.DataFrame, out_dir: Path) -> None:
    colunas_saida = [
        "Ano",
        "Mês",
        "Mês_Num",
        "Valor",
        "Tipo",
        "Produto",
        "Regional",
        "Canal",
        "Indicador",
        "Periodo",
        "Tipo_Chamada",
    ]

    partes: list[pd.DataFrame] = []

    if df_base_mensal is not None and not df_base_mensal.empty:
        df_base = df_base_mensal.copy()
        df_base["REGIONAL"] = df_base["REGIONAL"].astype("string").str.strip().str[:3].str.upper()
        df_base["CANAL_PLAN"] = df_base["CANAL_PLAN"].astype("string").str.strip().map(normalizar_canal_plan).astype("string")
        df_base["COD_PLATAFORMA"] = df_base["COD_PLATAFORMA"].apply(normalizar_rotulo_produto)
        df_base["DSC_INDICADOR"] = df_base["DSC_INDICADOR"].astype("string").str.strip()
        df_base["dat_tratada"] = df_base["dat_tratada"].astype("string").str.strip().str.lower()
        df_base["INDICADOR_CANONICO"] = df_base["DSC_INDICADOR"].apply(mapear_indicador_canonico)
        df_base["ANO"] = pd.to_numeric(df_base.get("ANO"), errors="coerce")
        df_base["MES_NUM"] = pd.to_numeric(df_base.get("DAT_MÃŠS"), errors="coerce")
        if "DAT_MOVIMENTO2" in df_base.columns:
            datas_base = pd.to_datetime(df_base["DAT_MOVIMENTO2"], errors="coerce")
            df_base.loc[df_base["ANO"].isna(), "ANO"] = datas_base.dt.year
            df_base.loc[df_base["MES_NUM"].isna(), "MES_NUM"] = datas_base.dt.month

        df_base = df_base[
            df_base["ANO"].isin([2025, 2026]) &
            df_base["MES_NUM"].between(1, 12)
        ].copy()
        if not df_base.empty:
            df_base["ANO"] = df_base["ANO"].astype(int)
            df_base["MES_NUM"] = df_base["MES_NUM"].astype(int)
            df_base["Mês"] = df_base["MES_NUM"].map(MONTHS_PT)
            df_base["Periodo"] = df_base["dat_tratada"].astype("string").str.strip().str.lower()
            mask_ligacoes_base = (
                df_base["INDICADOR_CANONICO"].eq("LIGACOES") &
                df_base["CANAL_PLAN"].eq("Televendas Receptivo")
            )
            df_base_sem_lig = df_base.loc[~mask_ligacoes_base].copy()

            dims_base = [
                "ANO",
                "Mês",
                "MES_NUM",
                "COD_PLATAFORMA",
                "REGIONAL",
                "CANAL_PLAN",
                "DSC_INDICADOR",
                "Periodo",
            ]
            for tipo_saida, coluna_valor in [
                ("Real", "QTDE"),
                ("Tend", "TEND_QTD"),
                ("Orç", "DESAFIO_QTD"),
            ]:
                df_tipo = (
                    df_base_sem_lig.groupby(dims_base, as_index=False, observed=True, dropna=False)[[coluna_valor]]
                    .sum()
                    .rename(
                        columns={
                            "ANO": "Ano",
                            "MES_NUM": "Mês_Num",
                            "COD_PLATAFORMA": "Produto",
                            "REGIONAL": "Regional",
                            "CANAL_PLAN": "Canal",
                            "DSC_INDICADOR": "Indicador",
                            coluna_valor: "Valor",
                        }
                    )
                )
                if not df_tipo.empty:
                    df_tipo["Tipo"] = tipo_saida
                    df_tipo["Tipo_Chamada"] = "Todos"
                    partes.append(df_tipo[colunas_saida].copy())

            df_lig_meta = df_base.loc[mask_ligacoes_base].copy()
            dims_lig_meta = [
                "ANO",
                "Mês",
                "MES_NUM",
                "COD_PLATAFORMA",
                "REGIONAL",
                "CANAL_PLAN",
                "DSC_INDICADOR",
                "Periodo",
            ]
            for tipo_saida, coluna_valor in [
                ("Tend", "TEND_QTD"),
                ("Orç", "DESAFIO_QTD"),
            ]:
                df_tipo = (
                    df_lig_meta.groupby(dims_lig_meta, as_index=False, observed=True, dropna=False)[[coluna_valor]]
                    .sum()
                    .rename(
                        columns={
                            "ANO": "Ano",
                            "MES_NUM": "Mês_Num",
                            "COD_PLATAFORMA": "Produto",
                            "REGIONAL": "Regional",
                            "CANAL_PLAN": "Canal",
                            "DSC_INDICADOR": "Indicador",
                            coluna_valor: "Valor",
                        }
                    )
                )
                if not df_tipo.empty:
                    df_tipo["Tipo"] = tipo_saida
                    df_tipo["Tipo_Chamada"] = "Todos"
                    partes.append(df_tipo[colunas_saida].copy())

    if df_ligacoes is not None and not df_ligacoes.empty:
        df_lig = df_ligacoes.copy()
        df_lig["DAT_MOVIMENTO2"] = pd.to_datetime(df_lig["DAT_MOVIMENTO2"], errors="coerce")
        df_lig = df_lig[df_lig["DAT_MOVIMENTO2"].notna()].copy()
        if not df_lig.empty:
            df_lig["Ano"] = df_lig["DAT_MOVIMENTO2"].dt.year.astype(int)
            df_lig["Mês_Num"] = df_lig["DAT_MOVIMENTO2"].dt.month.astype(int)
            df_lig = df_lig[df_lig["Ano"].isin([2025, 2026])].copy()
            df_lig["Mês"] = df_lig["Mês_Num"].map(MONTHS_PT)
            df_lig["Produto"] = df_lig["COD_PLATAFORMA"].apply(normalizar_rotulo_produto)
            df_lig["Regional"] = df_lig["REGIONAL"].astype("string").str.strip().str[:3].str.upper()
            df_lig["Canal"] = df_lig["CANAL_PLAN"].astype("string").str.strip().map(normalizar_canal_plan).astype("string")
            df_lig["Indicador"] = df_lig["DSC_INDICADOR"].astype("string").str.strip()
            df_lig["Periodo"] = df_lig["dat_tratada"].astype("string").str.strip().str.lower()
            df_lig["Tipo_Chamada"] = df_lig["TIPO_CHAMADA"].astype("string").str.strip().replace({"": "Todos"})
            df_lig["QTDE"] = pd.to_numeric(df_lig["QTDE"], errors="coerce").fillna(0)

            df_lig_real = (
                df_lig.groupby(
                    [
                        "Ano",
                        "Mês",
                        "Mês_Num",
                        "Produto",
                        "Regional",
                        "Canal",
                        "Indicador",
                        "Periodo",
                        "Tipo_Chamada",
                    ],
                    as_index=False,
                    observed=True,
                    dropna=False,
                )[["QTDE"]]
                .sum()
                .rename(columns={"QTDE": "Valor"})
            )
            if not df_lig_real.empty:
                df_lig_real["Tipo"] = "Real"
                partes.append(df_lig_real[colunas_saida].copy())

    if partes:
        df_saida = pd.concat(partes, ignore_index=True)
        df_saida["Valor"] = pd.to_numeric(df_saida["Valor"], errors="coerce").fillna(0)
        df_saida["Ano"] = pd.to_numeric(df_saida["Ano"], errors="coerce").astype(int)
        df_saida["Mês_Num"] = pd.to_numeric(df_saida["Mês_Num"], errors="coerce").astype(int)
        for coluna in ["Mês", "Tipo", "Produto", "Regional", "Canal", "Indicador", "Periodo", "Tipo_Chamada"]:
            df_saida[coluna] = df_saida[coluna].astype("string").str.strip()
        compactar_colunas_categoricas(
            df_saida,
            ["Mês", "Tipo", "Produto", "Regional", "Canal", "Indicador", "Periodo", "Tipo_Chamada"],
        )
    else:
        df_saida = pd.DataFrame(columns=colunas_saida)

    salvar_parquet(df_saida, out_dir / "evolucao_mensal.parquet")
    salvar_parquet(df_saida, out_dir / "evolucao_mensal_agregado.parquet")
    print(f"[ok] evolucao_mensal.parquet -> {len(df_saida):,} linhas")


def prepare_cotacoes(raw_dir: Path, out_dir: Path) -> None:
    path = resolver_arquivo(raw_dir, "RelatorioFluxoVidaCotacao.xlsx")
    header_df = pd.read_excel(path, nrows=0)

    def find_alias(colunas: pd.Index, *aliases: str) -> str | None:
        mapa = {normalizar_texto_chave(col): col for col in colunas}
        for alias in aliases:
            real = mapa.get(normalizar_texto_chave(alias))
            if real:
                return real
        return None

    coluna_cotacao = find_alias(header_df.columns, "FÚNIL FIXA", "COTACAO", "COTAÇÃO")
    coluna_data = find_alias(header_df.columns, "DATA CRIAÇÃO COTAÇÃO", "DATA CRIACAO COTACAO")
    coluna_canal_venda = find_alias(header_df.columns, "CANAL DE VENDA")
    coluna_canal = find_alias(header_df.columns, "CANAL_PLAN", "CANAL PLAN", "CANAL_TERRITORIO")
    coluna_regional = find_alias(
        header_df.columns,
        "REGIONAL",
        "REGIONAL CRIADOR COTAÇÃO",
        "REGIONAL CRIADOR COTACAO",
        "REGIONAL CLIENTE",
        "REGIONAL_TERRITORIO",
    )
    coluna_status = find_alias(header_df.columns, "STATUS ATUAL")
    coluna_novas_linhas = find_alias(header_df.columns, "QUANTIDADE NOVAS LINHAS A SEREM ATIVADAS")
    coluna_linhas_voz = find_alias(header_df.columns, "QTD LINHAS VOZ")
    coluna_lista_atividades = find_alias(header_df.columns, "LISTA ATIVIDADES")

    colunas = [
        col
        for col in [
            coluna_cotacao,
            coluna_data,
            coluna_canal_venda,
            coluna_canal,
            coluna_regional,
            coluna_status,
            coluna_novas_linhas,
            coluna_linhas_voz,
            coluna_lista_atividades,
        ]
        if col
    ]
    df = pd.read_excel(path, usecols=colunas)
    rename_map = {
        coluna_data: "DATA_CRIACAO_COTACAO",
        coluna_canal_venda: "CANAL_DE_VENDA",
        coluna_canal: "CANAL_PLAN",
        coluna_regional: "REGIONAL",
        coluna_novas_linhas: "QTD_NOVAS_LINHAS_ATIVAR",
        coluna_linhas_voz: "QTD_LINHAS_VOZ",
        coluna_lista_atividades: "LISTA_ATIVIDADES",
    }
    if coluna_cotacao:
        rename_map[coluna_cotacao] = "COTACAO_ID"
    if coluna_status:
        rename_map[coluna_status] = "STATUS_ATUAL"
    df = df.rename(columns=rename_map)

    if "COTACAO_ID" not in df.columns:
        df["COTACAO_ID"] = ""
    if "STATUS_ATUAL" not in df.columns:
        df["STATUS_ATUAL"] = ""

    df["COTACAO_ID"] = (
        df["COTACAO_ID"].astype(str).str.strip().replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "NULL": pd.NA})
    )
    df["DATA_CRIACAO_COTACAO"] = pd.to_datetime(df["DATA_CRIACAO_COTACAO"], errors="coerce")
    df["CANAL_DE_VENDA"] = df["CANAL_DE_VENDA"].astype(str).str.strip()
    df["CANAL_PLAN"] = df["CANAL_PLAN"].apply(normalizar_canal_plan)
    df["REGIONAL"] = df["REGIONAL"].astype(str).str.strip().str.upper().str[:3].replace({"": "N/I", "NAN": "N/I"})
    df["STATUS_ATUAL"] = (
        df["STATUS_ATUAL"].astype(str).str.strip().replace({"": "Status nao informado", "nan": "Status nao informado"})
    )
    df["QTD_NOVAS_LINHAS_ATIVAR"] = normalizar_numerico_serie(df["QTD_NOVAS_LINHAS_ATIVAR"]).fillna(0)
    df["QTD_LINHAS_VOZ"] = normalizar_numerico_serie(df["QTD_LINHAS_VOZ"]).fillna(0)
    df["LISTA_ATIVIDADES"] = df["LISTA_ATIVIDADES"].astype(str).str.strip()

    df = filtrar_regra_cotacoes_novas_linhas(df)
    df = df[df["DATA_CRIACAO_COTACAO"].notna()].copy()
    if df.empty:
        salvar_parquet(pd.DataFrame(), out_dir / "cotacoes_agregado.parquet")
        print("[ok] cotacoes_agregado.parquet -> 0 linhas")
        return

    df["mes_ano"] = df["DATA_CRIACAO_COTACAO"].apply(formatar_mes_ano)
    df["dat_tratada"] = df["mes_ano"]
    df_agg = (
        df.groupby(
            ["mes_ano", "dat_tratada", "CANAL_PLAN", "REGIONAL", "STATUS_ATUAL"],
            as_index=False,
            observed=True,
            dropna=False,
        )
        .agg(
            DATA_CRIACAO_COTACAO=("DATA_CRIACAO_COTACAO", "max"),
            VALOR_NOVAS_LINHAS=("QTD_NOVAS_LINHAS_ATIVAR", "sum"),
            QTD_COTACOES_UNICAS=("COTACAO_ID", pd.Series.nunique),
        )
    )
    compactar_colunas_categoricas(df_agg, ["mes_ano", "dat_tratada", "CANAL_PLAN", "REGIONAL", "STATUS_ATUAL"])
    salvar_parquet(df_agg, out_dir / "cotacoes_agregado.parquet")
    print(f"[ok] cotacoes_agregado.parquet -> {len(df_agg):,} linhas")


def prepare_backlog(raw_dir: Path, out_dir: Path) -> None:
    path = resolver_arquivo(raw_dir, "backlog_consolidado.csv")
    usecols = [
        "SK_DATA",
        "NR_CONTRATO",
        "NM_VISAO_ANALISE",
        "NM_REGIONAL",
        "NM_CANAL_VENDA_SUBGRUPO",
        "NOME_OS_TIPO_STATUS_AGENDA",
        "DT_AGENDA_ORDEM_SERVICO",
    ]
    df = pd.read_csv(path, usecols=usecols, low_memory=False)
    for coluna in usecols:
        if coluna in df.columns:
            df[coluna] = df[coluna].astype("string").str.strip()

    canais_permitidos_norm = {normalizar_chave_visual(v) for v in BACKLOG_CANAIS_PERMITIDOS}
    canais_backlog_norm = df["NM_CANAL_VENDA_SUBGRUPO"].map(normalizar_chave_visual)
    filtro = (
        df["NM_VISAO_ANALISE"].map(normalizar_chave_visual).eq(normalizar_chave_visual("Novos Domicilios"))
        & canais_backlog_norm.isin(canais_permitidos_norm)
    )
    df = df.loc[filtro].copy()
    df["NM_CANAL_VENDA_SUBGRUPO"] = (
        canais_backlog_norm.loc[df.index]
        .map(BACKLOG_MAPEAMENTO_CANAIS_NORM)
        .fillna(df["NM_CANAL_VENDA_SUBGRUPO"].astype("string").str.strip())
        .astype("string")
        .str.strip()
    )
    df["NR_CONTRATO"] = (
        df["NR_CONTRATO"]
        .astype("string")
        .str.replace(r"\.0$", "", regex=True)
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "NULL": pd.NA})
    )
    df["NM_REGIONAL"] = df["NM_REGIONAL"].astype("string").str.strip().replace({"": "Não Informado", "nan": "Não Informado"})
    df["NOME_OS_TIPO_STATUS_AGENDA"] = (
        df["NOME_OS_TIPO_STATUS_AGENDA"]
        .astype("string")
        .str.strip()
        .replace({"": "Não Informado", "nan": "Não Informado", "None": "Não Informado", "NULL": "Não Informado"})
    )

    data_sk = pd.to_datetime(
        df["SK_DATA"].astype("string").str.replace(r"\.0$", "", regex=True).str.zfill(8),
        format="%Y%m%d",
        errors="coerce",
    )
    data_agenda = pd.to_datetime(df["DT_AGENDA_ORDEM_SERVICO"], errors="coerce")
    df["DATA_BACKLOG_REF"] = data_sk.combine_first(data_agenda)
    df = df[df["NM_CANAL_VENDA_SUBGRUPO"].notna() & df["NR_CONTRATO"].notna() & df["DATA_BACKLOG_REF"].notna()].copy()
    df["MES_ANO"] = df["DATA_BACKLOG_REF"].apply(formatar_mes_ano)
    df_agg = (
        df.groupby(
            ["NM_REGIONAL", "NM_CANAL_VENDA_SUBGRUPO", "MES_ANO", "NOME_OS_TIPO_STATUS_AGENDA"],
            as_index=False,
            observed=True,
            dropna=False,
        )["NR_CONTRATO"]
        .nunique()
        .rename(columns={"NR_CONTRATO": "QTD_CONTRATOS"})
    )
    compactar_colunas_categoricas(
        df_agg,
        ["NM_REGIONAL", "NM_CANAL_VENDA_SUBGRUPO", "MES_ANO", "NOME_OS_TIPO_STATUS_AGENDA"],
    )
    salvar_parquet(df_agg, out_dir / "backlog_consolidado_limpo.parquet")
    print(f"[ok] backlog_consolidado_limpo.parquet -> {len(df_agg):,} linhas")


def prepare_migracoes(raw_dir: Path, out_dir: Path) -> None:
    path = resolver_arquivo(raw_dir, "ANALITICO_MIGRACOES_fev26.xlsx")
    df = pd.read_excel(path, usecols=["DAT_REFERENCIA", "DSC_REGIONAL_CMV", "QTDE_FINAL"])
    df = df.rename(columns={"DSC_REGIONAL_CMV": "REGIONAL", "QTDE_FINAL": "QTDE"})
    df["DAT_REFERENCIA"] = pd.to_datetime(df["DAT_REFERENCIA"], format="mixed", errors="coerce", dayfirst=True)
    df["REGIONAL"] = df["REGIONAL"].astype("string").str.strip().str.upper().str[:3]
    df["QTDE"] = normalizar_numerico_serie(df["QTDE"]).fillna(0)
    df = df[df["DAT_REFERENCIA"].notna() & df["REGIONAL"].notna()].copy()
    df["MES_ANO"] = df["DAT_REFERENCIA"].apply(formatar_mes_ano)
    df_agg = df.groupby(["REGIONAL", "MES_ANO"], as_index=False, observed=True, dropna=False)["QTDE"].sum()
    compactar_colunas_categoricas(df_agg, ["REGIONAL", "MES_ANO"])
    salvar_parquet(df_agg, out_dir / "migracoes_pme.parquet")
    print(f"[ok] migracoes_pme.parquet -> {len(df_agg):,} linhas")


def prepare_desativados(raw_dir: Path, out_dir: Path) -> None:
    path = resolver_arquivo(raw_dir, "base_final_churn.xlsx")
    header_df = pd.read_excel(path, nrows=0)
    colunas_disponiveis = set(header_df.columns)
    col_data = "DAT_MOVIMENTO" if "DAT_MOVIMENTO" in colunas_disponiveis else ("MES_MOVIMENTO" if "MES_MOVIMENTO" in colunas_disponiveis else None)
    usecols = [
        "COD_PLATAFORMA",
        "DSC_REGIONAL_CMV",
        "QTDE_AJUSTADA",
        "FLG_SILENTE",
        "DSC_CANAL_AJUSTADO",
        "FLAG_INADIMPLENTE",
        col_data,
    ]
    df = pd.read_excel(path, usecols=[col for col in usecols if col])
    df["REGIONAL"] = df["DSC_REGIONAL_CMV"].astype(str).str.strip().str[:3].str.upper()
    df["DAT_MOVIMENTO2"] = pd.to_datetime(df[col_data], errors="coerce", dayfirst=True)
    df = df[df["DAT_MOVIMENTO2"].notna()].copy()
    df["mes_ano"] = df["DAT_MOVIMENTO2"].apply(formatar_mes_ano)
    df = df.rename(
        columns={
            "QTDE_AJUSTADA": "QTDE",
            "DSC_CANAL_AJUSTADO": "CANAL_PLAN",
            "FLAG_INADIMPLENTE": "INADIMPLENTE",
        }
    )
    df["QTDE"] = normalizar_numerico_serie(df["QTDE"]).fillna(0)
    df["FLG_SILENTE"] = normalizar_numerico_serie(df["FLG_SILENTE"]).fillna(0).astype(int)
    df["COD_PLATAFORMA"] = df["COD_PLATAFORMA"].astype(str).str.strip().str.upper()
    df["CANAL_PLAN"] = df["CANAL_PLAN"].apply(normalizar_canal_plan)
    df["QTDE_SILENTE"] = np.where(df["FLG_SILENTE"].eq(1), df["QTDE"], 0)
    inad_raw = df["INADIMPLENTE"].astype(str).str.strip().str.upper()
    inad_num = pd.to_numeric(df["INADIMPLENTE"], errors="coerce")
    mask_inad_sim = (inad_num == 1) | inad_raw.isin(["1", "SIM", "S", "TRUE", "VERDADEIRO"])
    df["INADIMPLENTE"] = np.where(mask_inad_sim, "Sim", "Não")
    df_agg = (
        df.groupby(
            ["COD_PLATAFORMA", "REGIONAL", "CANAL_PLAN", "INADIMPLENTE", "FLG_SILENTE", "mes_ano"],
            as_index=False,
            observed=True,
            dropna=False,
        )[["QTDE", "QTDE_SILENTE"]]
        .sum()
    )
    df_max_data = (
        df.groupby(
            ["COD_PLATAFORMA", "REGIONAL", "CANAL_PLAN", "INADIMPLENTE", "FLG_SILENTE", "mes_ano"],
            as_index=False,
            observed=True,
            dropna=False,
        )["DAT_MOVIMENTO2"]
        .max()
    )
    df_agg = df_agg.merge(
        df_max_data,
        on=["COD_PLATAFORMA", "REGIONAL", "CANAL_PLAN", "INADIMPLENTE", "FLG_SILENTE", "mes_ano"],
        how="left",
    )
    compactar_colunas_categoricas(
        df_agg,
        ["COD_PLATAFORMA", "REGIONAL", "CANAL_PLAN", "INADIMPLENTE", "mes_ano"],
    )
    salvar_parquet(df_agg, out_dir / "desativados_base.parquet")
    print(f"[ok] desativados_base.parquet -> {len(df_agg):,} linhas")


def prepare_funil_fixa(raw_dir: Path, out_dir: Path) -> None:
    path = resolver_arquivo(raw_dir, "base_funil_ecomm_fixa.xlsx")
    df = pd.read_excel(path)
    df = df.rename(
        columns={
            "SEGMENTO": "SEGMENTO",
            "ORIGEM_AGG": "ORIGEM_AGG",
            "CANAL_ENTRADA": "CANAL_ENTRADA",
            "INDICADOR": "INDICADOR",
            "INDICADOR_ORDEM": "INDICADOR_ORDEM",
            "PERIODO_MES": "PERIODO_MES",
            "MES_ANO": "MES_ANO",
            "MES_ANO_ORDEM": "MES_ANO_ORDEM",
            "QTDE": "QTDE",
        }
    ).copy()
    if "CANAL_ENTRADA" not in df.columns:
        df["CANAL_ENTRADA"] = "Não Informado"
    for coluna in ["SEGMENTO", "ORIGEM_AGG", "CANAL_ENTRADA", "INDICADOR"]:
        df[coluna] = df[coluna].astype(str).str.strip()
    df["SEGMENTO"] = df["SEGMENTO"].apply(normalizar_segmento_funil_fixa)
    df["INDICADOR_CHAVE"] = df["INDICADOR"].apply(normalizar_texto_chave).str.replace(" ", "_", regex=False)
    df = df[df["SEGMENTO"].isin(["PF", "PME"])].copy()
    df = df[df["INDICADOR_CHAVE"].isin(FUNIL_FIXA_INDICADOR_LABELS.keys())].copy()
    df["INDICADOR"] = df["INDICADOR_CHAVE"].map(FUNIL_FIXA_INDICADOR_LABELS)
    df["QTDE"] = normalizar_numerico_serie(df["QTDE"]).fillna(0)
    df["INDICADOR_ORDEM"] = normalizar_numerico_serie(df.get("INDICADOR_ORDEM", 999.0)).fillna(999.0)
    df["INDICADOR_ORDEM"] = df["INDICADOR_CHAVE"].map(FUNIL_FIXA_INDICADOR_ORDENS).fillna(df["INDICADOR_ORDEM"])
    df["PERIODO_MES"] = pd.to_datetime(df["PERIODO_MES"], format="mixed", errors="coerce", dayfirst=True)
    df["MES_ANO"] = np.where(
        df["PERIODO_MES"].notna(),
        df["PERIODO_MES"].apply(formatar_mes_ano),
        df.get("MES_ANO", "").astype(str).str.strip().str.lower(),
    )
    df["MES_ANO"] = pd.Series(df["MES_ANO"]).astype(str).str.strip().str.lower()
    df["MES_ANO_ORDEM"] = np.where(
        df["PERIODO_MES"].notna(),
        df["PERIODO_MES"].dt.year * 100 + df["PERIODO_MES"].dt.month,
        normalizar_numerico_serie(df.get("MES_ANO_ORDEM", 0)).fillna(0).astype(int),
    )
    df["MES_ANO_ORDEM"] = normalizar_numerico_serie(df["MES_ANO_ORDEM"]).fillna(0).astype(int)
    df["EH_TEND"] = 0
    compactar_colunas_categoricas(
        df,
        ["SEGMENTO", "ORIGEM_AGG", "CANAL_ENTRADA", "INDICADOR", "MES_ANO", "INDICADOR_CHAVE"],
    )
    salvar_parquet(df, out_dir / "funil_fixa_ecommerce.parquet")
    print(f"[ok] funil_fixa_ecommerce.parquet -> {len(df):,} linhas")


def prepare_tend_funil_fixa(raw_dir: Path, out_dir: Path) -> None:
    path = resolver_arquivo(raw_dir, "tend_funil_ecom.xlsx")
    df = pd.read_excel(path)
    df = df.rename(columns={"PERIODO": "PERIODO_MES"}).copy()
    df["SEGMENTO"] = df["SEGMENTO"].apply(normalizar_segmento_funil_fixa)
    df["INDICADOR_CHAVE"] = df["INDICADOR"].apply(normalizar_texto_chave).str.replace(" ", "_", regex=False)
    df = df[df["SEGMENTO"].isin(["PF", "PME"])].copy()
    df = df[df["INDICADOR_CHAVE"].isin(FUNIL_FIXA_INDICADOR_LABELS.keys())].copy()
    df["INDICADOR"] = df["INDICADOR_CHAVE"].map(FUNIL_FIXA_INDICADOR_LABELS)
    df["INDICADOR_ORDEM"] = df["INDICADOR_CHAVE"].map(FUNIL_FIXA_INDICADOR_ORDENS).fillna(999.0)
    df["PERIODO_MES"] = pd.to_datetime(df["PERIODO_MES"], format="mixed", errors="coerce", dayfirst=True)
    df = df[df["PERIODO_MES"].notna()].copy()
    df["QTDE"] = normalizar_numerico_serie(df["QTDE"]).fillna(0)
    df["MES_ANO"] = df["PERIODO_MES"].apply(formatar_mes_ano).astype(str).str.strip().str.lower()
    df["MES_ANO_ORDEM"] = (df["PERIODO_MES"].dt.year * 100 + df["PERIODO_MES"].dt.month).astype(int)
    compactar_colunas_categoricas(df, ["SEGMENTO", "INDICADOR", "MES_ANO", "INDICADOR_CHAVE"])
    salvar_parquet(df, out_dir / "tend_funil_fixa.parquet")
    print(f"[ok] tend_funil_fixa.parquet -> {len(df):,} linhas")


def prepare_convergencia(raw_dir: Path, out_dir: Path) -> None:
    path = resolver_arquivo_convergencia(raw_dir)
    if not path.exists():
        print(f"[warn] base_convergencia.xlsx nao encontrada: {path}")
        return
    try:
        path_leitura = materializar_excel_para_pandas(path)
    except Exception as exc:
        print(f"[warn] base_convergencia.xlsx indisponivel para leitura ({exc}).")
        return

    try:
        header_df = pd.read_excel(path_leitura, nrows=0)
    except Exception as exc:
        print(f"[warn] nao foi possivel ler cabecalho da base_convergencia.xlsx ({exc}).")
        return
    rename_map = {}
    usecols = []
    for destino, aliases in CONVERGENCIA_COL_ALIASES.items():
        coluna_real = encontrar_coluna_por_alias(header_df.columns, *aliases)
        if coluna_real:
            rename_map[coluna_real] = destino
            usecols.append(coluna_real)

    obrigatorias = {"DAT_MOVIMENTO", "DSC_REGIONAL", "DSC_CANAL_VENDA", "DSC_TIPO_ORIGEM", "QTDE", "QTDE_CNPJ8"}
    if not obrigatorias.issubset(set(rename_map.values())):
        print(f"[warn] base_convergencia.xlsx sem colunas obrigatorias: {path}")
        return

    try:
        df = pd.read_excel(path_leitura, usecols=usecols)
    except Exception as exc:
        print(f"[warn] nao foi possivel ler base_convergencia.xlsx ({exc}).")
        return
    if df.empty:
        print("[warn] base_convergencia.xlsx vazia.")
        return

    df = df.rename(columns=rename_map)
    for coluna in CONVERGENCIA_COL_ALIASES:
        if coluna not in df.columns:
            df[coluna] = ""

    df["DATA_DIA"] = pd.to_datetime(
        df["DAT_MOVIMENTO"],
        errors="coerce",
        dayfirst=True,
        format="mixed",
    ).dt.normalize()
    df = df[df["DATA_DIA"].notna()].copy()
    if df.empty:
        print("[warn] base_convergencia.xlsx sem datas validas.")
        return

    df["mes_ano"] = df["DATA_DIA"].apply(formatar_mes_ano)
    df["REGIONAL"] = df["DSC_REGIONAL"].astype(str).str.strip().str[:3].str.upper()
    df["CANAL_PLAN"] = df["DSC_CANAL_VENDA"].map(normalizar_canal_plan).astype("string").str.strip()
    df["COD_PLATAFORMA"] = df["DSC_TIPO_ORIGEM"].apply(normalizar_produto_convergencia)
    df = df[df["COD_PLATAFORMA"].isin(["FIXA", "CONTA"])].copy()
    if df.empty:
        print("[warn] base_convergencia.xlsx sem produtos FIXA/CONTA.")
        return

    df["QTDE"] = normalizar_numerico_serie(df["QTDE"]).fillna(0.0)
    df["QTDE_CNPJ8"] = normalizar_numerico_serie(df["QTDE_CNPJ8"]).fillna(0.0)
    df["FLAG_CONV"] = df["FLG_VENDA_CONVERGENTE"].apply(normalizar_texto_chave).eq("CONV")
    df["FLAG_NOVO"] = df["FLG_NOVO"].apply(normalizar_texto_chave).eq("NOVO")
    df["FLAG_NOVO_NOVO"] = df["FLG_NOVO_NOVO"].apply(normalizar_texto_chave).eq("NOVO NOVO")

    colunas_saida = [
        "DATA_DIA", "mes_ano", "REGIONAL", "CANAL_PLAN", "COD_PLATAFORMA",
        "QTDE", "QTDE_CNPJ8", "FLAG_CONV", "FLAG_NOVO", "FLAG_NOVO_NOVO"
    ]
    df_saida = df[colunas_saida].copy()
    compactar_colunas_categoricas(df_saida, ["mes_ano", "REGIONAL", "CANAL_PLAN", "COD_PLATAFORMA"])
    salvar_parquet(df_saida, out_dir / "convergencia_base.parquet")
    print(f"[ok] convergencia_base.parquet -> {len(df_saida):,} linhas")

    chaves_mensal = ["REGIONAL", "CANAL_PLAN", "COD_PLATAFORMA", "mes_ano"]
    df_mensal = (
        df_saida.groupby(chaves_mensal, as_index=False, observed=True)[["QTDE_CNPJ8", "QTDE"]]
        .sum()
        .rename(columns={"QTDE_CNPJ8": "TOTAL", "QTDE": "LINHAS"})
    )
    for flag_col, destino in [
        ("FLAG_NOVO", "NOVO"),
        ("FLAG_NOVO_NOVO", "NOVO_NOVO"),
        ("FLAG_CONV", "CONV"),
    ]:
        df_flag = df_saida[df_saida[flag_col].astype(bool)]
        if df_flag.empty:
            df_mensal[destino] = 0.0
            continue
        agg_flag = (
            df_flag.groupby(chaves_mensal, as_index=False, observed=True)["QTDE_CNPJ8"]
            .sum()
            .rename(columns={"QTDE_CNPJ8": destino})
        )
        df_mensal = df_mensal.merge(agg_flag, on=chaves_mensal, how="left")
        df_mensal[destino] = normalizar_numerico_serie(df_mensal[destino]).fillna(0.0)
    df_mensal["QTDE_CNPJ8"] = df_mensal["TOTAL"]
    df_mensal["QTDE"] = df_mensal["LINHAS"]
    compactar_colunas_categoricas(df_mensal, ["mes_ano", "REGIONAL", "CANAL_PLAN", "COD_PLATAFORMA"])
    salvar_parquet(df_mensal, out_dir / "convergencia_mensal.parquet")
    print(f"[ok] convergencia_mensal.parquet -> {len(df_mensal):,} linhas")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera datasets Parquet preprocessados para o dashboard Streamlit.")
    parser.add_argument("--raw-dir", default=None, help="Diretorio com os arquivos brutos.")
    parser.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT), help="Diretorio de saida dos Parquets.")
    args = parser.parse_args()

    raw_dir = localizar_diretorio_bruto(args.raw_dir)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[info] RAW_DIR = {raw_dir}")
    print(f"[info] OUT_DIR = {out_dir}")

    df_base = prepare_base_principal(raw_dir, out_dir)
    df_base_mensal = prepare_base_principal_mensal(df_base, out_dir)
    prepare_pedidos_ecommerce(df_base, out_dir)
    prepare_ativados_base(df_base_mensal, out_dir)
    prepare_gross_motivo_status(df_base_mensal, out_dir)
    prepare_base_performance(df_base, out_dir)
    prepare_analitica(df_base, out_dir)
    df_ligacoes = prepare_ligacoes(raw_dir, out_dir)
    prepare_evolucao_mensal(df_base_mensal, df_ligacoes, out_dir)
    prepare_cotacoes(raw_dir, out_dir)
    prepare_backlog(raw_dir, out_dir)
    prepare_migracoes(raw_dir, out_dir)
    prepare_desativados(raw_dir, out_dir)
    prepare_funil_fixa(raw_dir, out_dir)
    prepare_tend_funil_fixa(raw_dir, out_dir)
    prepare_convergencia(raw_dir, out_dir)

    del df_base, df_base_mensal, df_ligacoes
    gc.collect()
    print("[done] Preprocessamento concluido.")


if __name__ == "__main__":
    main()
