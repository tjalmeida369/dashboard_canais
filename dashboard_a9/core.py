import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
import streamlit.components.v1 as components
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from collections import OrderedDict
import json
import locale
import re
import shutil
import tempfile
import gc
import time
import unicodedata
from io import StringIO
from html import escape
from textwrap import dedent
base_template = go.layout.Template(
    layout=go.Layout(
        template="plotly_white",
        title=go.layout.Title(font=dict(size=16, color="#333333", family="Segoe UI"))
    )
)
px.defaults.template = base_template
px.defaults.color_discrete_sequence = ["#D12405", "#961009", "#AEAFAF", "#16FDE6", "#EB6969", "#DEEAF4"]

try:
    locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
except:
    pass

def formatar_numero_brasileiro(valor, casas_decimais=0):
    """
    Formata número no padrão brasileiro:
    - Ponto como separador de milhar
    - Vírgula como separador decimal
    """
    if pd.isna(valor) or valor is None:
        return "0"
    
    try:
        valor_float = float(valor)
        
        valor_arredondado = round(valor_float, casas_decimais)
        
        if casas_decimais == 0:
            parte_inteira = int(valor_arredondado)
            valor_formatado = f"{abs(parte_inteira):,}"
            valor_formatado = valor_formatado.replace(",", ".")
            if parte_inteira < 0:
                valor_formatado = f"-{valor_formatado}"
            return valor_formatado
        
        sinal = '-' if valor_arredondado < 0 else ''
        valor_abs = abs(valor_arredondado)
        
        parte_inteira = int(valor_abs)
        parte_decimal = valor_abs - parte_inteira
        
        parte_inteira_fmt = f"{parte_inteira:,}".replace(",", ".")
        
        if casas_decimais > 0:
            format_str = f"{{:.{casas_decimais}f}}"
            parte_decimal_str = format_str.format(parte_decimal)
            parte_decimal_fmt = parte_decimal_str[2:] if parte_decimal_str.startswith('0.') else parte_decimal_str
        else:
            parte_decimal_fmt = ""
        
        if parte_decimal_fmt:
            return f"{sinal}{parte_inteira_fmt},{parte_decimal_fmt}"
        else:
            return f"{sinal}{parte_inteira_fmt}"
            
    except Exception as e:
        print(f"Erro ao formatar {valor}: {e}")
        return str(valor)

def normalizar_numerico_serie(serie):
    """
    Normaliza series numéricas incluindo textos em formato BR
    (ex.: 2.659,40 -> 2659.40).
    """
    if not isinstance(serie, pd.Series):
        serie = pd.Series(serie)
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_numeric(serie, errors='coerce')
    s = serie.astype(str).str.strip()
    s = s.str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
    s = s.str.replace(r'[^0-9\.-]', '', regex=True)
    return pd.to_numeric(s, errors='coerce')

def normalizar_chave_visual(texto: str) -> str:
    """Normaliza textos para buscar ícones sem depender de acentuação."""
    base = unicodedata.normalize("NFKD", str(texto or ""))
    base = base.encode("ASCII", "ignore").decode("ASCII").lower()
    base = re.sub(r'[^a-z0-9]+', ' ', base).strip()
    return base


def normalizar_texto_chave(valor) -> str:
    """Normaliza textos de regra de negócio para comparações estáveis."""
    if pd.isna(valor):
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = texto.encode("ASCII", "ignore").decode("ASCII")
    texto = texto.strip().upper()
    texto = re.sub(r"[^A-Z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


CANAL_CONSULTIVO_REMOTO = "Consultivo Remoto"
CANAL_CONSULTIVO_REMOTO_ALIASES = {"INSIDE SALES", "CONSULTIVO REMOTO", "CONSULTIVO REMOVO"}


def normalizar_canal_plan(valor) -> str:
    """Canonicaliza o canal Consultivo Remoto, preservando aliases historicos."""
    texto = normalizar_texto_chave(valor)
    if not texto:
        return ""
    if texto in CANAL_CONSULTIVO_REMOTO_ALIASES:
        return CANAL_CONSULTIVO_REMOTO
    return str(valor).strip()


def encontrar_coluna_por_alias(colunas, *aliases: str) -> str | None:
    """Localiza uma coluna por alias, ignorando acentos e pequenas variacoes de nome."""
    mapa_colunas: dict[str, str] = {}
    for coluna in colunas:
        chave = normalizar_chave_visual(coluna)
        if chave and chave not in mapa_colunas:
            mapa_colunas[chave] = coluna

    for alias in aliases:
        coluna_real = mapa_colunas.get(normalizar_chave_visual(alias))
        if coluna_real:
            return coluna_real
    return None

CACHE_MAX_ENTRIES_LARGE = 2
CACHE_MAX_ENTRIES_MEDIUM = 4
CACHE_MAX_ENTRIES_VIEW = 4
CACHE_MAX_ENTRIES_FILTERS = 2
CACHE_SESSION_VARIATIONS = 1
SESSION_CACHE_MAX_TEXT_CHARS = 220_000
SESSION_CACHE_MAX_CONTAINER_ITEMS = 6
SESSION_CACHE_TTL_SECONDS = 900
COTACOES_CACHE_VERSION = "2026-04-02-cotacoes-otimizadas-v7"
ALLOWED_CANAIS_VENDA_COTACOES = {
    normalizar_chave_visual("CORPPME"),
    normalizar_chave_visual("CORPLP"),
    normalizar_chave_visual("NETPME"),
}
HTML_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_RUN_CSS_RENDERED: set[str] = set()
if not hasattr(st, "_dashboard_markdown_original"):
    setattr(st, "_dashboard_markdown_original", st.markdown)
_ST_MARKDOWN_ORIGINAL = getattr(st, "_dashboard_markdown_original")
st.markdown = _ST_MARKDOWN_ORIGINAL
setattr(st, "_dashboard_export_original_markdown", _ST_MARKDOWN_ORIGINAL)


def serializar_dataframe_cache(df: pd.DataFrame | None) -> str:
    """Serializa DataFrame de forma estável para uso em chaves de cache."""
    if df is None or df.empty:
        return pd.DataFrame().to_json(date_format="iso", orient="split")
    return df.to_json(date_format="iso", orient="split")


def desserializar_dataframe_cache(df_json: str) -> pd.DataFrame:
    """Desserializa DataFrame previamente convertido para JSON orient=split."""
    if not df_json:
        return pd.DataFrame()
    return pd.read_json(StringIO(df_json), orient="split", dtype=False)


def _desempacotar_item_cache_session(item):
    """Compatibiliza itens legados do cache com a nova tupla (valor, timestamp)."""
    if isinstance(item, tuple) and len(item) == 2:
        valor, ts = item
        if isinstance(ts, (int, float)):
            return valor, float(ts)
    if isinstance(item, dict) and "value" in item:
        ts = item.get("ts")
        if isinstance(ts, (int, float)):
            return item.get("value"), float(ts)
        return item.get("value"), None
    return item, None


_RUNTIME_CLEANUP_KEEP = {
    "df",
    "home_inicio_ctx",
    "opcoes_filtros_globais",
}


def limpar_objetos_runtime_dashboard(*preservar_extra: str) -> None:
    """Remove referências globais pesadas criadas durante o rerun.

    Em scripts Streamlit, variáveis de topo continuam referenciadas após o
    término do rerun. Limpar DataFrames e figuras já renderizados reduz o
    crescimento de memória sem alterar a camada visual.
    """
    preservar = _RUNTIME_CLEANUP_KEEP.union(str(item) for item in preservar_extra)
    removidos = 0
    globais_ref = globals()

    for nome, valor in list(globais_ref.items()):
        if nome in preservar or nome.startswith("__"):
            continue

        remover = isinstance(valor, (pd.DataFrame, pd.Series, pd.Index, go.Figure))
        if not remover and isinstance(valor, np.ndarray):
            remover = bool(getattr(valor, "nbytes", 0) >= 64 * 1024)
        if not remover and isinstance(valor, str):
            nome_lower = nome.lower()
            remover = (
                len(valor) > SESSION_CACHE_MAX_TEXT_CHARS and
                (
                    nome_lower.startswith("html") or
                    nome_lower.endswith("_html") or
                    "tabela_html" in nome_lower
                )
            )

        if remover:
            try:
                del globais_ref[nome]
                removidos += 1
            except Exception:
                pass

    if removidos:
        gc.collect()


def fragmento_dashboard(func=None, **fragment_kwargs):
    """Usa st.fragment quando disponível, mantendo fallback seguro para versões antigas."""
    decorator_fragmento = getattr(st, "fragment", None)
    if decorator_fragmento is None:
        if func is not None:
            return func

        def _decorador_sem_fragmento(func_ref):
            return func_ref

        return _decorador_sem_fragmento

    if func is not None:
        return decorator_fragmento(func, **fragment_kwargs)
    return decorator_fragmento(**fragment_kwargs)

def _normalizar_chave_cache_session(cache_key):
    try:
        hash(cache_key)
        return cache_key
    except Exception:
        return repr(cache_key)


def _valor_cacheavel_session(valor, profundidade: int = 0) -> bool:
    """Permite no cache em sessão apenas payloads leves, priorizando HTML já pronto."""
    if valor is None or isinstance(valor, (bool, int, float, np.number)):
        return True

    if isinstance(valor, (str, bytes)):
        return len(valor) <= SESSION_CACHE_MAX_TEXT_CHARS

    if isinstance(valor, (pd.DataFrame, pd.Series, np.ndarray)):
        return False

    if hasattr(valor, "to_plotly_json"):
        return False

    if profundidade >= 2:
        return False

    if isinstance(valor, dict):
        if len(valor) > SESSION_CACHE_MAX_CONTAINER_ITEMS:
            return False
        total_texto = 0
        for item in valor.values():
            if isinstance(item, (str, bytes)):
                total_texto += len(item)
                if total_texto > SESSION_CACHE_MAX_TEXT_CHARS:
                    return False
            if not _valor_cacheavel_session(item, profundidade + 1):
                return False
        return True

    if isinstance(valor, (list, tuple)):
        if len(valor) > SESSION_CACHE_MAX_CONTAINER_ITEMS:
            return False
        return all(_valor_cacheavel_session(item, profundidade + 1) for item in valor)

    return False


def obter_cache_session_dashboard(
    cache_id: str,
    cache_key,
    calcular_fn,
    max_variacoes: int = CACHE_SESSION_VARIATIONS
):
    """Memoiza resultados pesados por chave simples, preservando poucas variações recentes por bloco."""
    agora = time.time()
    cache_raiz = st.session_state.setdefault("_dashboard_session_result_cache", OrderedDict())
    if not isinstance(cache_raiz, OrderedDict):
        cache_raiz = OrderedDict(cache_raiz)

    chave_normalizada = _normalizar_chave_cache_session(cache_key)
    bucket = cache_raiz.get(cache_id)
    if isinstance(bucket, dict) and not isinstance(bucket, OrderedDict) and {"key", "value"}.issubset(bucket.keys()):
        bucket = OrderedDict([(bucket.get("key"), bucket.get("value"))])
    elif not isinstance(bucket, OrderedDict):
        bucket = OrderedDict(bucket) if isinstance(bucket, dict) else OrderedDict()

    expiradas = []
    for chave_bucket, item_bucket in list(bucket.items()):
        _, ts_bucket = _desempacotar_item_cache_session(item_bucket)
        if ts_bucket is not None and (agora - ts_bucket) > SESSION_CACHE_TTL_SECONDS:
            expiradas.append(chave_bucket)
    for chave_expirada in expiradas:
        bucket.pop(chave_expirada, None)

    if chave_normalizada in bucket:
        valor, ts = _desempacotar_item_cache_session(bucket.pop(chave_normalizada))
        if ts is None or (agora - ts) <= SESSION_CACHE_TTL_SECONDS:
            bucket[chave_normalizada] = (valor, agora)
            cache_raiz.move_to_end(cache_id)
            cache_raiz[cache_id] = bucket
            st.session_state["_dashboard_session_result_cache"] = cache_raiz
            return valor

    valor = calcular_fn()
    if not _valor_cacheavel_session(valor):
        cache_raiz[cache_id] = bucket
        cache_raiz.move_to_end(cache_id)
        while len(cache_raiz) > CACHE_MAX_ENTRIES_VIEW:
            cache_raiz.popitem(last=False)
        st.session_state["_dashboard_session_result_cache"] = cache_raiz
        return valor

    bucket[chave_normalizada] = (valor, agora)
    while len(bucket) > max(1, int(max_variacoes or 1)):
        bucket.popitem(last=False)

    cache_raiz[cache_id] = bucket
    cache_raiz.move_to_end(cache_id)
    while len(cache_raiz) > CACHE_MAX_ENTRIES_VIEW:
        cache_raiz.popitem(last=False)

    st.session_state["_dashboard_session_result_cache"] = cache_raiz
    return valor


def renderizar_html_otimizado(html: object, markdown_kwargs: dict | None = None) -> None:
    """Renderiza HTML pesado reutilizando blocos <style> iguais apenas uma vez por execução."""
    if html is None:
        return
    html_texto = str(html)
    if not html_texto.strip():
        return

    kwargs_corpo = {"unsafe_allow_html": True}
    if isinstance(markdown_kwargs, dict):
        kwargs_corpo.update(markdown_kwargs)
        kwargs_corpo["unsafe_allow_html"] = True

    estilos = HTML_STYLE_BLOCK_RE.findall(html_texto)
    corpo = HTML_STYLE_BLOCK_RE.sub("", html_texto)
    for estilo in estilos:
        if estilo not in _RUN_CSS_RENDERED:
            _ST_MARKDOWN_ORIGINAL(estilo, unsafe_allow_html=True)
            _RUN_CSS_RENDERED.add(estilo)

    if corpo.strip():
        _ST_MARKDOWN_ORIGINAL(corpo, **kwargs_corpo)

def obter_opcao_preferida_dashboard(
    options,
    preferidos,
    fallback=None
):
    """Seleciona uma opção preferida preservando robustez contra variações de escrita."""
    opcoes = list(options or [])
    if not opcoes:
        return fallback

    if not isinstance(preferidos, (list, tuple, set)):
        preferidos = [preferidos]

    mapa_normalizado = {
        normalizar_chave_visual(str(opcao)): opcao
        for opcao in opcoes
    }
    for preferido in preferidos:
        chave_preferida = normalizar_chave_visual(str(preferido or ""))
        if chave_preferida and chave_preferida in mapa_normalizado:
            return mapa_normalizado[chave_preferida]

    if fallback in opcoes:
        return fallback
    return opcoes[0]


def formatar_data_atualizacao_dashboard(path_ref: str | Path | None) -> str:
    """Formata a última atualização de um arquivo no padrão dd/mm."""
    if not path_ref:
        return "--/--"
    try:
        path_obj = Path(path_ref)
        if not path_obj.exists():
            return "--/--"
        return datetime.fromtimestamp(path_obj.stat().st_mtime).strftime("%d/%m")
    except Exception:
        return "--/--"


def montar_segmento_data_atualizacao_html(
    path_ref: str | Path | None,
    rotulo: str = "Dados atualizados até"
) -> str:
    data_formatada = formatar_data_atualizacao_dashboard(path_ref)
    return f"""
        <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:13px; color:#333333; font-weight:600;">{escape(str(rotulo))}:</span>
            <span style="font-size:14px; color:#790E09; font-weight:700;
                    background: rgba(121, 14, 9, 0.09); padding: 6px 15px; border-radius: 20px;">
                {escape(data_formatada)}
            </span>
        </div>
    """

PRIMARY_BASE_USECOLS = [
    'REGIONAL', 'DSC_REGIONAL_CMV',
    'CANAL_PLAN', 'DSC_CANAL',
    'dat_tratada', 'mes_ano',
    'DSC_INDICADOR', 'DSC_MOTIVO_STS', 'COD_PLATAFORMA',
    'DAT_MOVIMENTO2', 'DAT_MOVIMENTO', 'DAT_MOVIMENTO_2', 'PERIODO',
    'QTDE', 'DESAFIO_QTD', 'TEND_QTD',
    'ID_AFILIADOS', 'ID_AFILIADO',
    'ORIGEM_AFILIADOS', 'ORIGEM_AFILIADO'
]

def compactar_colunas_categoricas(
    df: pd.DataFrame,
    colunas: list[str] | tuple[str, ...]
) -> pd.DataFrame:
    """Compacta colunas dimensionais repetitivas para reduzir memória sem alterar valores."""
    if df is None or df.empty:
        return df

    # Evita SettingWithCopyWarning quando a função recebe slices filtrados.
    df = df.copy(deep=False)

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
                # Evita atribuição direta de tipo categórico sobre colunas com
                # StringDtype (pandas extension). Em versões recentes do pandas
                # isso dispara um FutureWarning e será erro no futuro.
                # Solução: garantir que a coluna alvo seja do tipo compatível
                # (object) antes de atribuir a série categórica.
                try:
                    if pd.api.types.is_string_dtype(df[coluna]) and not isinstance(df[coluna].dtype, pd.CategoricalDtype):
                        df[coluna] = df[coluna].astype(object)
                except Exception:
                    pass
                df.loc[:, coluna] = serie.astype("category")
        except Exception:
            continue
    return df

def get_kpi_icon_svg(icon_hint: str | None = None) -> str:
    """Retorna SVG inline simples para reforçar a leitura visual dos KPIs."""
    chave = normalizar_chave_visual(icon_hint or "")
    if "click" in chave:
        icon_name = "cursor"
    elif "target" in chave or "total" in chave:
        icon_name = "target"
    elif "pedido" in chave or "commerce" in chave or "carrinho" in chave:
        icon_name = "cart"
    elif "fixa" in chave or "liga" in chave or "chamada" in chave or "phone" in chave:
        icon_name = "phone"
    elif "conta" in chave or "movel" in chave or "mobile" in chave:
        icon_name = "mobile"
    elif "trend" in chave or "tend" in chave:
        icon_name = "spark"
    else:
        icon_name = "grid"

    icon_library = {
        "grid": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<rect x="3" y="3" width="7" height="7" rx="1.6"></rect>'
            '<rect x="14" y="3" width="7" height="7" rx="1.6"></rect>'
            '<rect x="3" y="14" width="7" height="7" rx="1.6"></rect>'
            '<rect x="14" y="14" width="7" height="7" rx="1.6"></rect>'
            '</svg>'
        ),
        "phone": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M22 16.92v2a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07A19.45 19.45 0 0 1 5.15 12.8 '
            '2 2 0 0 1 4 11.09 19.8 19.8 0 0 1 .92 2.18 2 2 0 0 1 2.91 0h2A2 2 0 0 1 6.9 1.72c.12.9.33 1.78.62 '
            '2.62a2 2 0 0 1-.45 2.11L5.91 7.91a16 16 0 0 0 6.18 6.18l1.46-1.16a2 2 0 0 1 2.11-.45c.84.29 1.72.5 '
            '2.62.62A2 2 0 0 1 22 16.92z"></path>'
            '</svg>'
        ),
        "mobile": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<rect x="7" y="2.5" width="10" height="19" rx="2.4"></rect>'
            '<path d="M11 18.5h2"></path>'
            '</svg>'
        ),
        "cart": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<circle cx="9" cy="20" r="1.7"></circle>'
            '<circle cx="18" cy="20" r="1.7"></circle>'
            '<path d="M3 4h2l2.2 10.2a1 1 0 0 0 1 .8h8.9a1 1 0 0 0 1-.78L20 7H6.2"></path>'
            '</svg>'
        ),
        "target": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<circle cx="12" cy="12" r="8.5"></circle>'
            '<circle cx="12" cy="12" r="4.2"></circle>'
            '<path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3"></path>'
            '</svg>'
        ),
        "cursor": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M5 4.5v14l4.2-3.2 3 5.2 2.5-1.4-3-5.2 5.8-.8L5 4.5z"></path>'
            '</svg>'
        ),
        "spark": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M4 16l4.5-5 3.5 3 6-8"></path>'
            '<path d="M14.5 6h3.5v3.5"></path>'
            '</svg>'
        ),
    }
    return icon_library.get(icon_name, icon_library["grid"])

st.set_page_config(page_title="Dashboard - Canais Estratégicos", layout="wide")
KPI_PILL_VARIANT = "contrast"  # Opcoes: "clean" ou "contrast"

DASHBOARD_APP_DIR = Path(__file__).resolve().parent
DASHBOARD_PROJECT_ROOT = DASHBOARD_APP_DIR.parent
DASHBOARD_FILES_DIR_PROD = Path(
    r""
)
DASHBOARD_FILES_DIR_LOCAL = DASHBOARD_PROJECT_ROOT
DASHBOARD_FILES_DIR = (
    DASHBOARD_FILES_DIR_PROD
    if DASHBOARD_FILES_DIR_PROD.exists()
    else (DASHBOARD_FILES_DIR_LOCAL if DASHBOARD_FILES_DIR_LOCAL.exists() else DASHBOARD_APP_DIR)
)
DASHBOARD_LEGACY_MOBILITY_DIR = Path(
    r""
)
DASHBOARD_FILE_SEARCH_DIRS = (
    DASHBOARD_FILES_DIR,
    DASHBOARD_FILES_DIR_LOCAL,
    DASHBOARD_FILES_DIR_PROD,
    DASHBOARD_PROJECT_ROOT,
    DASHBOARD_APP_DIR,
)

def resolver_arquivo_dashboard(nome_arquivo: str | Path, *fallbacks: str | Path) -> Path:
    """Resolve arquivos do dashboard priorizando a pasta Arquivos_Dashboard."""
    candidatos: list[Path] = []
    vistos: set[str] = set()

    def adicionar_candidato(candidato: str | Path | None) -> None:
        if candidato is None:
            return
        path_obj = Path(candidato)
        paths_para_incluir = (
            [path_obj]
            if path_obj.is_absolute()
            else [Path(base_dir) / path_obj for base_dir in DASHBOARD_FILE_SEARCH_DIRS]
        )
        for item in paths_para_incluir:
            chave = str(item).strip().lower()
            if chave and chave not in vistos:
                candidatos.append(item)
                vistos.add(chave)

    adicionar_candidato(nome_arquivo)
    for fallback in fallbacks:
        adicionar_candidato(fallback)

    for candidato in candidatos:
        if candidato.exists():
            return candidato
    return candidatos[0] if candidatos else Path(nome_arquivo)


DASHBOARD_PREPROCESSED_SUBDIR = DASHBOARD_PROJECT_ROOT / "dados_preprocessados"


def resolver_arquivo_preprocessado(nome_arquivo: str | Path, *fallbacks: str | Path) -> Path:
    """Resolve arquivos preprocessados priorizando a subpasta dados_preprocessados."""
    candidatos: list[Path] = []
    nome_path = Path(nome_arquivo)
    candidatos.append(DASHBOARD_PREPROCESSED_SUBDIR / nome_path)
    candidatos.append(nome_path)

    for fallback in fallbacks:
        fallback_path = Path(fallback)
        if fallback_path.is_absolute():
            candidatos.append(fallback_path)
        else:
            candidatos.append(DASHBOARD_PREPROCESSED_SUBDIR / fallback_path)
            candidatos.append(fallback_path)

    if not candidatos:
        return Path(nome_arquivo)
    return resolver_arquivo_dashboard(candidatos[0], *candidatos[1:])

#LOGO_FILE_PATH = resolver_arquivo_dashboard("logo_claro_empresas.png")
OBS_RESULTADO_FILE_PATH = resolver_arquivo_dashboard("obs_resultado_canais.txt")

_EXPORT_WRAPPER_NAMES = {
    "_plotly_chart_com_exportacao",
    "_markdown_com_exportacao_tabela",
    "_components_html_com_exportacao",
}

def _desembrulhar_metodo_exportacao(metodo_atual, nome_global_original: str):
    """Remove camadas antigas de wrapper mantidas pelo rerun do Streamlit."""
    metodo = metodo_atual
    visitados: set[int] = set()
    for _ in range(12):
        if metodo is None:
            return metodo_atual
        metodo_id = id(metodo)
        if metodo_id in visitados:
            return metodo
        visitados.add(metodo_id)

        candidatos = []
        try:
            original_attr = getattr(metodo, "_dashboard_export_original", None)
            if original_attr is not None and original_attr is not metodo:
                candidatos.append(original_attr)
        except Exception:
            pass

        try:
            if getattr(metodo, "__name__", "") in _EXPORT_WRAPPER_NAMES:
                original_global = getattr(metodo, "__globals__", {}).get(nome_global_original)
                if original_global is not None and original_global is not metodo:
                    candidatos.append(original_global)
        except Exception:
            pass

        if not candidatos:
            return metodo
        metodo = candidatos[0]
    return metodo

EXPORTAR_GRAFICOS_ALTA_QUALIDADE = True
PLOTLY_DOWNLOAD_SCALE = 4
_CURRENT_ST_PLOTLY_CHART = st.plotly_chart
_CURRENT_ST_MARKDOWN = st.markdown
_CURRENT_COMPONENTS_HTML = components.html
_ORIGINAL_ST_PLOTLY_CHART = _desembrulhar_metodo_exportacao(
    getattr(st, "_dashboard_export_original_plotly_chart", None) or _CURRENT_ST_PLOTLY_CHART,
    "_ORIGINAL_ST_PLOTLY_CHART",
)
_ORIGINAL_ST_MARKDOWN = _desembrulhar_metodo_exportacao(
    getattr(st, "_dashboard_export_original_markdown", None) or _CURRENT_ST_MARKDOWN,
    "_ORIGINAL_ST_MARKDOWN",
)
_ORIGINAL_COMPONENTS_HTML = _desembrulhar_metodo_exportacao(
    getattr(components, "_dashboard_export_original_html", None) or _CURRENT_COMPONENTS_HTML,
    "_ORIGINAL_COMPONENTS_HTML",
)
setattr(st, "_dashboard_export_original_plotly_chart", _ORIGINAL_ST_PLOTLY_CHART)
setattr(st, "_dashboard_export_original_markdown", _ORIGINAL_ST_MARKDOWN)
setattr(components, "_dashboard_export_original_html", _ORIGINAL_COMPONENTS_HTML)
_EXPORT_CHART_COUNTER = 0

def _nome_arquivo_exportacao(texto: str, fallback: str) -> str:
    """Gera nomes seguros para arquivos baixados no dashboard."""
    nome = normalizar_chave_visual(texto or fallback).replace(" ", "_")
    nome = re.sub(r"_+", "_", nome).strip("_")
    return nome or fallback

def _nome_grafico_plotly(fig) -> str:
    """Extrai um nome amigável do título do gráfico, quando existir."""
    global _EXPORT_CHART_COUNTER
    _EXPORT_CHART_COUNTER += 1
    fallback = f"grafico_dashboard_{_EXPORT_CHART_COUNTER:03d}"
    try:
        titulo = getattr(getattr(fig, "layout", None), "title", None)
        texto_titulo = getattr(titulo, "text", "") or ""
        texto_titulo = re.sub(r"<[^>]+>", " ", str(texto_titulo))
        return _nome_arquivo_exportacao(texto_titulo, fallback)
    except Exception:
        return fallback

def _config_plotly_exportacao(fig, config_usuario: dict | None) -> dict:
    """Ativa o download de imagem em alta resolução em todos os gráficos Plotly."""
    config = dict(config_usuario or {})
    to_image = dict(config.get("toImageButtonOptions") or {})
    to_image.setdefault("format", "png")
    to_image.setdefault("scale", PLOTLY_DOWNLOAD_SCALE)
    to_image.setdefault("filename", _nome_grafico_plotly(fig))
    config["toImageButtonOptions"] = to_image
    config["displayModeBar"] = config.get("displayModeBar") or "hover"
    if config["displayModeBar"] is False:
        config["displayModeBar"] = "hover"
    config["displaylogo"] = False
    config.setdefault("responsive", True)
    return config


def obter_bases_funil_home() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve as bases do funil da capa sem depender da ordem de execução das abas."""
    base_funil_home = globals().get("base_funil_cotacoes", pd.DataFrame())
    if base_funil_home is None or getattr(base_funil_home, "empty", True):
        base_funil_home = globals().get("df_perf_base", pd.DataFrame())
    if (base_funil_home is None or getattr(base_funil_home, "empty", True)) and "preparar_base_performance" in globals() and "df" in globals():
        try:
            df_origem = globals().get("df", pd.DataFrame())
            if df_origem is not None and not getattr(df_origem, "empty", True):
                base_funil_home = preparar_base_performance(df_origem)
        except Exception:
            base_funil_home = pd.DataFrame()

    df_cotacoes_home = globals().get("df_cotacoes_base", pd.DataFrame())
    if df_cotacoes_home is None or getattr(df_cotacoes_home, "empty", True):
        try:
            cotacoes_path_ref = globals().get("COTACOES_FILE_PATH", None)
            if cotacoes_path_ref:
                cotacoes_path = resolver_arquivo_dashboard(cotacoes_path_ref, "RelatorioFluxoVidaCotacao.xlsx")
                cotacoes_path = cotacoes_path if Path(cotacoes_path).exists() else None
                if cotacoes_path is not None:
                    cotacoes_mtime = Path(cotacoes_path).stat().st_mtime
                    df_cotacoes_home = load_cotacoes_data(
                        str(cotacoes_path),
                        cotacoes_mtime,
                        COTACOES_CACHE_VERSION
                    )
        except Exception:
            df_cotacoes_home = pd.DataFrame()

    if base_funil_home is None:
        base_funil_home = pd.DataFrame()
    if df_cotacoes_home is None:
        df_cotacoes_home = pd.DataFrame()
    return base_funil_home, df_cotacoes_home

def _aplicar_autoscale_inicial_linhas(fig) -> None:
    """Evita gráficos de linha iniciarem com range travado após filtros."""
    try:
        traces = list(getattr(fig, "data", []) or [])
        tem_linha = any(
            str(getattr(trace, "type", "") or "").lower() in {"scatter", "scattergl"} and
            ("lines" in str(getattr(trace, "mode", "") or "lines").lower())
            for trace in traces
        )
        tem_barra = any(str(getattr(trace, "type", "") or "").lower() == "bar" for trace in traces)
        if not tem_linha or tem_barra:
            return
        fig.update_xaxes(autorange=True, fixedrange=False)
        fig.update_yaxes(autorange=True, fixedrange=False)
    except Exception:
        return

def _plotly_chart_com_exportacao(*args, **kwargs):
    """Wrapper de teste para disponibilizar download em alta qualidade nos gráficos."""
    fig = args[0] if args else kwargs.get("figure_or_data", None)
    _aplicar_autoscale_inicial_linhas(fig)
    if EXPORTAR_GRAFICOS_ALTA_QUALIDADE:
        kwargs["config"] = _config_plotly_exportacao(fig, kwargs.get("config"))
    return _ORIGINAL_ST_PLOTLY_CHART(*args, **kwargs)

_plotly_chart_com_exportacao._dashboard_export_original = _ORIGINAL_ST_PLOTLY_CHART
st.plotly_chart = _plotly_chart_com_exportacao
st.markdown = _ORIGINAL_ST_MARKDOWN
components.html = _ORIGINAL_COMPONENTS_HTML

st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700;800&family=Sora:wght@600;700;800&display=swap');
        * {font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;}
        html, body, .block-container {
            background: radial-gradient(circle at 20% 20%, rgba(255,40,0,0.05), transparent 35%),
                        radial-gradient(circle at 80% 10%, rgba(121,14,9,0.06), transparent 30%),
                        radial-gradient(circle at 50% 70%, rgba(90,10,6,0.05), transparent 40%),
                        #ffffff;
        }
        .block-container {padding-top: 15px;}
        .css-18e3th9 {padding-top: 10px;}
        
        .main-title {
            font-size: 52px;
            font-weight: 900;
            background: linear-gradient(135deg, #FF2800 0%, #790E09 50%, #5A0A06 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            text-align: center;
            margin: 10px 0 20px 0;
            letter-spacing: -1.5px;
            position: relative;
            padding: 30px 0 40px 0;
            font-family: 'Segoe UI', 'Roboto', 'Helvetica Neue', sans-serif;
            text-shadow: 0 4px 12px rgba(121, 14, 9, 0.15);
            line-height: 1.1;
        }
        
        .main-title::before {
            content: '';
            position: absolute;
            top: 0;
            left: 50%;
            transform: translateX(-50%);
            width: 180px;
            height: 6px;
            background: linear-gradient(90deg, #FF2800, #790E09, #5A0A06);
            border-radius: 6px;
            box-shadow: 0 4px 12px rgba(255, 40, 0, 0.3);
        }
        
        .main-title::after {
            content: 'DASHBOARD ANALÍTICO | PERFORMANCE DE CANAIS';
            position: absolute;
            bottom: 15px;
            left: 50%;
            transform: translateX(-50%);
            font-size: 14px;
            font-weight: 700;
            color: #666666;
            letter-spacing: 3px;
            text-transform: uppercase;
            background: none;
            -webkit-text-fill-color: #666666;
            opacity: 0.9;
            padding: 8px 30px;
            background: linear-gradient(90deg, rgba(255, 40, 0, 0.05), rgba(121, 14, 9, 0.05));
            border-radius: 25px;
            border: 1px solid rgba(121, 14, 9, 0.1);
        }
        
        .section-title {
            font-size: 32px;
            font-weight: 800;
            color: #333333;
            margin: 40px 0 25px 0;
            padding: 18px 0 18px 30px;
            position: relative;
            background: linear-gradient(90deg, rgba(255, 40, 0, 0.12) 0%, transparent 100%);
            border-left: 5px solid #FF2800;
            border-radius: 0 12px 12px 0;
            box-shadow: 0 6px 20px rgba(255, 40, 0, 0.1);
            display: flex;
            align-items: center;
            gap: 15px;
        }
        
        .section-title::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 5px;
            height: 100%;
            background: linear-gradient(180deg, #FF2800 0%, #790E09 100%);
            border-radius: 5px;
        }
        
        .section-icon {
            font-size: 28px;
            background: linear-gradient(135deg, #FF2800, #790E09);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .subsection-title {
            font-size: 24px;
            font-weight: 700;
            color: #333333;
            margin: 35px 0 20px 0;
            padding: 15px 0 15px 25px;
            position: relative;
            border-left: 4px solid #FF2800;
            background: linear-gradient(90deg, rgba(255, 40, 0, 0.08) 0%, transparent 100%);
            border-radius: 0 10px 10px 0;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .card-title {
            font-size: 20px;
            font-weight: 700;
            color: #333333;
            margin-bottom: 20px;
            text-align: center;
            padding: 16px;
            background: linear-gradient(135deg, #FFFFFF 0%, #F8F9FA 100%);
            border-radius: 16px;
            border: 2px solid #E9ECEF;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.08);
            position: relative;
            overflow: hidden;
        }
        
        .kpi-card-dinamico {
            background: linear-gradient(145deg, #FFFFFF, #F8F9FA);
            border-radius: 16px;
            padding: 6px;
            box-shadow: 
                0 8px 25px rgba(121, 14, 9, 0.12),
                0 3px 10px rgba(0, 0, 0, 0.06),
                inset 0 1px 0 rgba(255, 255, 255, 0.9);
            margin: 4px 0;
            border: 2px solid #F0F0F0;
            position: relative;
            overflow: hidden;
            transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.1);
            min-height: 70px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }
        
        .kpi-card-dinamico:hover {
            transform: translateY(-6px);
            box-shadow: 
                0 15px 35px rgba(121, 14, 9, 0.18),
                0 5px 15px rgba(0, 0, 0, 0.1),
                inset 0 1px 0 rgba(255, 255, 255, 0.95);
            border-color: #FF2800;
        }
        
        .kpi-card-dinamico::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 5px;
            background: linear-gradient(90deg, #FF2800, #790E09, #5A0A06);
            border-radius: 16px 16px 0 0;
        }
        
        .kpi-title-dinamico {
            font-size: 18px !important;
            background: linear-gradient(135deg, #FF2800 0%, #790E09 50%, #5A0A06 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 15px !important;
            text-align: center;
            font-weight: 900;
            position: relative;
            padding-bottom: 10px;
            letter-spacing: 0.5px;
        }
        
        .kpi-title-dinamico::after {
            content: '';
            position: absolute;
            bottom: 0;
            left: 50%;
            transform: translateX(-50%);
            width: 50px;
            height: 3px;
            background: linear-gradient(90deg, #FF2800, #790E09);
            border-radius: 3px;
        }
        
        .kpi-block-dinamico {
            background: linear-gradient(135deg, #FFFFFF, #F8F9FA);
            border-radius: 12px;
            padding: 7px 5px;
            text-align: center;
            border: 2px solid #E9ECEF;
            box-shadow: 0 3px 10px rgba(0, 0, 0, 0.05);
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }
        
        .kpi-block-dinamico:hover {
            transform: translateY(-3px);
            box-shadow: 0 6px 20px rgba(121, 14, 9, 0.15);
            border-color: #FF2800;
        }
        
        .kpi-block-dinamico::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, #5A0A06, #790E09, #FF2800);
            opacity: 0.8;
        }
        
        .kpi-value-dinamico {
            font-size: 28px !important;
            color: #333333;
            font-weight: 900;
            margin: 10px 0 !important;
            line-height: 1.2;
            text-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
            font-family: 'Segoe UI', 'Roboto', sans-serif;
            background: linear-gradient(135deg, #333333, #555555);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        /* Reforço de hierarquia visual para legendas em gráficos */
        .js-plotly-plot .plotly .legend text {font-weight: 700 !important;}
        .js-plotly-plot .plotly .main-svg .gtitle {font-size: 16px !important; font-weight: 800 !important;}
        
        .kpi-variacao-item {
            font-size: 10px !important;
            font-weight: 800;
            padding: 4px 8px !important;
            border-radius: 12px;
            display: inline-block;
            background: rgba(255, 255, 255, 0.95);
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);
            border: 1.5px solid rgba(0, 0, 0, 0.05);
            min-height: 22px;
            display: flex;
            align-items: center;
            justify-content: center;
            letter-spacing: 0.3px;
            text-align: center;
        }
        
        .variacao-positiva {
            color: #1B5E20 !important;
            background: linear-gradient(135deg, rgba(232, 245, 233, 1), rgba(200, 230, 201, 1)) !important;
            border: 1.5px solid #4CAF50 !important;
        }
        
        .variacao-negativa {
            color: #C62828 !important;
            background: linear-gradient(135deg, rgba(255, 235, 238, 1), rgba(255, 205, 210, 1)) !important;
            border: 1.5px solid #F44336 !important;
        }
        
        .stTabs [data-baseweb="tab-list"] {
            gap: 6px;
            background: #F8F9FA;
            padding: 8px;
            border-radius: 16px;
            margin-bottom: 30px;
            border: 2px solid #E9ECEF;
        }
        
        .stTabs [data-baseweb="tab"] {
            background: linear-gradient(135deg, #FFFFFF, #F8F9FA) !important;
            border-radius: 12px;
            padding: 15px 28px;
            font-weight: 700;
            color: #666666 !important;
            border: 2px solid #E9ECEF !important;
            transition: all 0.3s ease;
            font-size: 16px;
            box-shadow: 0 3px 10px rgba(0, 0, 0, 0.05);
        }
        
        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, #FF2800 0%, #790E09 100%) !important;
            color: white !important;
            border: 2px solid rgba(255, 255, 255, 0.3) !important;
            box-shadow: 0 6px 20px rgba(255, 40, 0, 0.3);
            font-weight: 800;
            transform: scale(1.05);
        }
        
        /* Controles de filtro (select / multiselect) */
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
            min-height: 46px;
            border: 2px solid #A23B36 !important;
            border-radius: 12px !important;
            padding: 8px 12px !important;
            font-weight: 700 !important;
            background: linear-gradient(135deg, #FF5434 0%, #7A120C 100%) !important;
            box-shadow: 0 6px 16px rgba(121, 14, 9, 0.25) !important;
            transition: all 0.2s ease;
            display: flex !important;
            align-items: center !important;
            overflow: visible !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] > div > div,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div > div {
            overflow: visible !important;
            min-height: 22px !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] span,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] span {
            line-height: 1.25 !important;
            max-height: none !important;
            white-space: normal !important;
            text-overflow: clip !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover,
        [data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within > div,
        [data-testid="stMultiSelect"] div[data-baseweb="select"]:focus-within > div {
            border-color: #FF9D8A !important;
            box-shadow: 0 8px 20px rgba(255, 84, 52, 0.35) !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] > div *,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div * {
            color: #FFFFFF !important;
            -webkit-text-fill-color: #FFFFFF !important;
            fill: #FFFFFF !important;
            font-weight: 700 !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] input::placeholder,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] input::placeholder {
            color: #FFD5CB !important;
            -webkit-text-fill-color: #FFD5CB !important;
            opacity: 1 !important;
        }

        /* Menu dropdown dos filtros */
        div[data-baseweb="popover"] ul[role="listbox"] {
            background: #FFFFFF !important;
            border: 1.5px solid #A23B36 !important;
            border-radius: 12px !important;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.12) !important;
            padding: 4px !important;
        }

        div[data-baseweb="popover"] li[role="option"] {
            color: #333333 !important;
            font-weight: 600 !important;
            border-radius: 8px !important;
            background: #FFFFFF !important;
        }

        div[data-baseweb="popover"] li[role="option"]:hover {
            background: rgba(255, 84, 52, 0.10) !important;
            color: #7A120C !important;
        }

        div[data-baseweb="popover"] li[role="option"][aria-selected="true"] {
            background: rgba(121, 14, 9, 0.14) !important;
            color: #5A0A06 !important;
            font-weight: 800 !important;
        }

        /* Chips selecionados do multiselect */
        [data-testid="stMultiSelect"] span[data-baseweb="tag"] {
            background: linear-gradient(135deg, #FF5434, #7A120C) !important;
            border: none !important;
            border-radius: 10px !important;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2) !important;
            padding: 2px 8px !important;
        }

        [data-testid="stMultiSelect"] span[data-baseweb="tag"] *,
        [data-testid="stMultiSelect"] span[data-baseweb="tag"] svg {
            color: #FFFFFF !important;
            fill: #FFFFFF !important;
            -webkit-text-fill-color: #FFFFFF !important;
            font-weight: 800 !important;
        }
        
        .stButton > button {
            background: linear-gradient(135deg, #FF2800 0%, #790E09 100%);
            color: white;
            border: none;
            border-radius: 12px;
            padding: 12px 24px;
            font-weight: 700;
            font-size: 14px;
            letter-spacing: 0.5px;
            transition: all 0.3s ease;
            box-shadow: 0 6px 20px rgba(255, 40, 0, 0.25);
            border: 2px solid rgba(255, 255, 255, 0.15);
        }
        
        .stButton > button:hover {
            background: linear-gradient(135deg, #790E09 0%, #5A0A06 100%);
            color: white;
            box-shadow: 0 8px 25px rgba(121, 14, 9, 0.4);
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.3);
        }
        
        [data-testid="stMetric"] {
            background: linear-gradient(135deg, #FFFFFF, #F8F9FA);
            padding: 24px;
            border-radius: 16px;
            border: 2px solid #E9ECEF;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.08);
        }
        
        [data-testid="stMetricValue"] {
            font-weight: 900;
            color: #333333;
            font-size: 32px !important;
            background: linear-gradient(135deg, #333333, #555555);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .filter-container {
            background: linear-gradient(135deg, #FFFFFF, #F8F9FA);
            border-radius: 16px;
            padding: 20px;
            margin: 20px 0;
            border: 2px solid #E9ECEF;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.08);
        }
        
        .filter-title {
            font-size: 16px;
            font-weight: 700;
            color: #333333;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #E9ECEF;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .filter-label-standard {
            font-size: 12px;
            font-weight: 800;
            color: #6B1A14;
            letter-spacing: 0.6px;
            text-transform: uppercase;
            margin: 0 0 8px 0;
            line-height: 1.1;
        }

        .analitico-corte-info {
            margin: 8px 0 14px 0;
            padding: 10px 14px;
            border-radius: 12px;
            border: 1px solid #F0D8D5;
            background: linear-gradient(90deg, #FFF7F6 0%, #FFFFFF 100%);
            color: #243041;
            font-family: 'Manrope', 'Segoe UI', sans-serif;
            font-size: 14px;
            line-height: 1.4;
            box-shadow: 0 2px 8px rgba(121, 14, 9, 0.08);
        }

        .analitico-corte-info .analitico-corte-label {
            color: #790E09;
            font-weight: 800;
        }

        .analitico-corte-info .analitico-corte-date {
            color: #1E293B;
            font-weight: 700;
        }

        .analitico-corte-info .analitico-corte-sep {
            color: #94A3B8;
            padding: 0 4px;
        }

        .analitico-corte-info .analitico-corte-days {
            color: #334155;
            font-weight: 700;
            letter-spacing: 0.2px;
        }

        [data-testid="stWidgetLabel"] p {
            font-size: 12px !important;
            font-weight: 800 !important;
            color: #6B1A14 !important;
            letter-spacing: 0.4px !important;
            text-transform: uppercase;
            margin: 0 !important;
            line-height: 1.15 !important;
        }

        [data-testid="stWidgetLabel"] {
            margin-bottom: 8px !important;
            padding-bottom: 0 !important;
        }

        [data-testid="stSelectbox"],
        [data-testid="stMultiSelect"] {
            margin: 0 0 10px 0 !important;
        }

        [data-testid="stSelectbox"] > div,
        [data-testid="stMultiSelect"] > div {
            margin-top: 0 !important;
        }
        
        .info-box {
            background: linear-gradient(135deg, rgba(255, 40, 0, 0.05), rgba(121, 14, 9, 0.05));
            border-radius: 12px;
            padding: 20px;
            border: 2px solid rgba(255, 40, 0, 0.1);
            margin: 20px 0;
        }
        
        .info-box-title {
            font-size: 14px;
            font-weight: 700;
            color: #FF2800;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        .footer-logo {
            background: linear-gradient(135deg, #000000 0%, #1A1A1A 100%);
            padding: 30px;
            text-align: center;
            margin: 60px -20px -20px -20px;
            border-top: 4px solid #FF2800;
            position: relative;
        }
        
        .footer-logo::before {
            content: '';
            position: absolute;
            top: 0;
            left: 50%;
            transform: translateX(-50%);
            width: 200px;
            height: 3px;
            background: linear-gradient(90deg, transparent, #FF2800, #790E09, transparent);
        }
        
        .logo-text {
            color: #FFFFFF;
            font-size: 14px;
            margin-top: 15px;
            font-family: 'Segoe UI', sans-serif;
            opacity: 0.9;
            letter-spacing: 1px;
        }
        
        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .animate-fade-in-up {
            animation: fadeInUp 0.6s ease-out;
        }
        
        @media (max-width: 768px) {
            .main-title {
                font-size: 36px;
                padding: 20px 0 30px 0;
            }
            
            .main-title::after {
                font-size: 12px;
                padding: 6px 20px;
                letter-spacing: 2px;
            }
            
            .section-title {
                font-size: 24px;
                padding: 15px 0 15px 20px;
            }
            
            .kpi-value-dinamico {
                font-size: 24px !important;
            }
            
            .kpi-variacao-item {
                font-size: 9px !important;
                padding: 3px 6px !important;
            }
        }
        
        /* Tamanho padronizado dos títulos dos gráficos */
        .js-plotly-plot .plotly .main-svg .gtitle {
              font-size: 16px !important;
        }

        /* =========================
           VISUAL SYSTEM OVERRIDES
           ========================= */
        :root {
            --ds-primary: #790E09;
            --ds-primary-soft: #A23B36;
            --ds-primary-deep: #4A0704;
            --ds-bg: #F7F8FA;
            --ds-surface: #FFFFFF;
            --ds-surface-soft: #FAFBFC;
            --ds-ivory: #FFFCF8;
            --ds-text: #1F2937;
            --ds-text-muted: #4B5563;
            --ds-border: #E5E7EB;
            --ds-border-strong: #D1D5DB;
            --ds-gold: #C8A86C;
            --ds-gold-soft: #E6D5B2;
            --ds-gold-wash: rgba(200, 168, 108, 0.18);
            --ds-positive: #1B5E20;
            --ds-negative: #C62828;
        }

        html, body, .block-container {
            background: var(--ds-bg) !important;
        }

        .block-container {
            max-width: 1550px;
        }

        .main-title {
            font-size: 44px;
            text-shadow: none;
            margin-bottom: 14px;
        }

        .main-title::after {
            letter-spacing: 1.6px;
            font-size: 12px;
            border: 1px solid var(--ds-border);
            background: #FFFFFF;
        }

        .section-title {
            font-size: 28px;
            color: var(--ds-text);
            background: var(--ds-surface);
            border: 1px solid var(--ds-border);
            border-left: 5px solid var(--ds-primary);
            border-radius: 12px;
            box-shadow: none;
            margin: 28px 0 18px 0;
            padding: 14px 18px;
            gap: 10px;
        }

        .section-title::before {
            display: none;
        }

        .subsection-title,
        .card-title {
            background: var(--ds-surface);
            border: 1px solid var(--ds-border);
            box-shadow: none;
        }

        .kpi-card-dinamico,
        .kpi-block-dinamico,
        [data-testid="stMetric"] {
            background:
                radial-gradient(circle at 16% 10%, rgba(255, 255, 255, 0.98) 0%, rgba(255, 255, 255, 0) 28%),
                radial-gradient(circle at top right, rgba(200, 168, 108, 0.14) 0%, rgba(200, 168, 108, 0) 30%),
                linear-gradient(180deg, rgba(255, 252, 248, 0.995) 0%, rgba(248, 250, 252, 0.988) 72%, rgba(244, 247, 250, 0.982) 100%),
                linear-gradient(135deg, rgba(162, 59, 54, 0.05) 0%, rgba(162, 59, 54, 0.00) 46%) !important;
            border: 1px solid rgba(200, 168, 108, 0.20) !important;
            border-radius: 18px !important;
            box-shadow:
                0 1px 2px rgba(16, 24, 40, 0.04),
                0 14px 28px rgba(16, 24, 40, 0.075),
                0 4px 12px rgba(90, 10, 6, 0.05),
                inset 0 1px 0 rgba(255, 255, 255, 0.96),
                inset 0 0 0 1px rgba(255, 255, 255, 0.58) !important;
            position: relative;
            isolation: isolate;
        }

        .kpi-card-dinamico {
            overflow: hidden;
            min-height: 64px !important;
            padding: 5px !important;
            margin: 3px 0 !important;
            outline: 1px solid rgba(255, 255, 255, 0.62);
            outline-offset: -1px;
        }

        .kpi-card-stack-soft-left {
            left: -6px;
        }

        .kpi-card-stack-soft-right {
            left: 6px;
        }

        .kpi-card-dinamico > * {
            position: relative;
            z-index: 1;
        }

        .kpi-block-dinamico {
            border-radius: 14px !important;
            padding: 7px 6px !important;
            min-height: 100%;
            background:
                radial-gradient(circle at top center, rgba(255, 255, 255, 0.82) 0%, rgba(255, 255, 255, 0) 52%),
                linear-gradient(180deg, rgba(255, 255, 255, 0.985) 0%, rgba(248, 250, 252, 0.97) 100%) !important;
            border-color: rgba(226, 232, 240, 0.92) !important;
            box-shadow:
                0 6px 14px rgba(15, 23, 42, 0.05),
                inset 0 1px 0 rgba(255, 255, 255, 0.96) !important;
            outline: 1px solid rgba(255, 255, 255, 0.56);
            outline-offset: -1px;
        }

        .kpi-card-dinamico::before,
        .kpi-block-dinamico::before {
            height: 2px;
            background: linear-gradient(90deg, rgba(200, 168, 108, 0.95) 0%, #D96A5F 16%, var(--ds-primary) 44%, #6B0D09 82%, rgba(200, 168, 108, 0.95) 100%);
            opacity: 1;
        }

        .kpi-card-dinamico::after {
            content: "";
            position: absolute;
            top: 0;
            right: 0;
            width: 48%;
            height: 100%;
            background:
                radial-gradient(circle at top right, rgba(200, 168, 108, 0.18), rgba(200, 168, 108, 0.00) 44%),
                radial-gradient(circle at 88% 18%, rgba(162, 59, 54, 0.10), rgba(162, 59, 54, 0.00) 50%),
                linear-gradient(135deg, rgba(255, 255, 255, 0.26), rgba(255, 255, 255, 0.00) 42%),
                repeating-linear-gradient(135deg, rgba(200, 168, 108, 0.032) 0 7px, rgba(200, 168, 108, 0.00) 7px 14px);
            pointer-events: none;
            z-index: 0;
        }

        .kpi-block-dinamico::after {
            content: "";
            position: absolute;
            inset: 1px;
            border-radius: 13px;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.44), rgba(255, 255, 255, 0) 42%),
                linear-gradient(180deg, rgba(255, 255, 255, 0.18), rgba(255, 255, 255, 0)),
                linear-gradient(135deg, rgba(200, 168, 108, 0.045), rgba(200, 168, 108, 0));
            pointer-events: none;
            z-index: 0;
        }

        .kpi-block-dinamico > * {
            position: relative;
            z-index: 1;
        }

        .kpi-card-dinamico:hover,
        .kpi-block-dinamico:hover {
            transform: translateY(-2px);
            border-color: rgba(200, 168, 108, 0.32) !important;
            box-shadow:
                0 4px 10px rgba(16, 24, 40, 0.06),
                0 16px 26px rgba(16, 24, 40, 0.10),
                0 0 0 1px rgba(200, 168, 108, 0.08) !important;
        }

        .kpi-title-dinamico {
            background: linear-gradient(180deg, #7A1E19 0%, #5A0A06 100%) !important;
            color: #6B1A14 !important;
            -webkit-background-clip: text !important;
            background-clip: text !important;
            -webkit-text-fill-color: transparent !important;
            font-size: 11px !important;
            font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif !important;
            font-weight: 800 !important;
            letter-spacing: 1.35px;
            margin-bottom: 7px !important;
            text-transform: uppercase;
            text-align: center;
            padding-bottom: 7px;
            line-height: 1.15;
        }

        .kpi-title-dinamico::after {
            height: 2px;
            width: 24px;
            background: linear-gradient(90deg, rgba(200, 168, 108, 0.10), var(--ds-gold), var(--ds-primary), rgba(200, 168, 108, 0.10));
        }

        .kpi-value-dinamico {
            font-size: 23px !important;
            color: #1F2937 !important;
            background: linear-gradient(135deg, #243041 0%, #1E293B 56%, #5A0A06 100%) !important;
            -webkit-background-clip: text !important;
            background-clip: text !important;
            -webkit-text-fill-color: transparent !important;
            font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif !important;
            font-weight: 900 !important;
            text-shadow: none;
            letter-spacing: -0.65px;
            line-height: 1;
            font-variant-numeric: tabular-nums;
        }

        .kpi-variacao-item {
            position: relative;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            white-space: nowrap;
            border-radius: 999px;
            border: 1px solid #DCE3EC !important;
            min-height: 23px;
            padding: 3px 9px !important;
            font-family: 'Manrope', 'Segoe UI', sans-serif !important;
            font-weight: 700 !important;
            letter-spacing: 0.2px;
            color: #475569 !important;
            background: linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%) !important;
            box-shadow:
                inset 0 1px 0 rgba(255, 255, 255, 0.9),
                0 1px 2px rgba(15, 23, 42, 0.08);
        }

        .kpi-variacao-item::before {
            content: "";
            width: 7px;
            height: 7px;
            border-radius: 999px;
            background: #94A3B8;
            box-shadow: 0 0 0 2px rgba(148, 163, 184, 0.18);
            flex: 0 0 auto;
        }

        .kpi-meta-line {
            display: flex;
            align-items: center;
            justify-content: center;
            flex-wrap: wrap;
            gap: 4px;
            font-size: 11px;
            color: #64748B;
            margin: 4px 0 3px 0;
            line-height: 1.25;
            font-weight: 600;
            font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        }

        .kpi-meta-label {
            font-weight: 800;
            font-size: 9px;
            letter-spacing: 0.9px;
            text-transform: uppercase;
            color: #7A1E19;
            cursor: help;
            transition: color 0.2s ease;
            font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        }

        .kpi-meta-label:hover {
            color: #641411;
        }

        .kpi-meta-items {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex-wrap: wrap;
            gap: 6px;
        }

        .kpi-meta-items-break {
            flex-direction: column;
            width: 100%;
        }

        .kpi-meta-chip {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            min-height: 23px;
            padding: 3px 9px;
            border-radius: 11px;
            border: 1px solid rgba(226, 232, 240, 0.92);
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.74), rgba(255, 255, 255, 0) 48%),
                linear-gradient(180deg, #FFFFFF 0%, #FBFCFD 100%),
                linear-gradient(135deg, rgba(200, 168, 108, 0.032), rgba(200, 168, 108, 0));
            box-shadow:
                inset 0 1px 0 rgba(255, 255, 255, 0.92),
                0 3px 8px rgba(15, 23, 42, 0.04);
            white-space: nowrap;
        }

        .kpi-meta-chip-anterior {
            border-color: rgba(100, 116, 139, 0.20);
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.76), rgba(255, 255, 255, 0) 48%),
                linear-gradient(180deg, #FFFFFF 0%, #F5F8FC 100%);
        }

        .kpi-meta-chip-orc {
            border-color: rgba(200, 168, 108, 0.28);
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.78), rgba(255, 255, 255, 0) 48%),
                linear-gradient(180deg, #FFFBF6 0%, #FBEFDB 100%);
        }

        .kpi-meta-chip-silentes {
            border-color: rgba(180, 35, 24, 0.18);
            background: linear-gradient(180deg, #FFF8F7 0%, #FEEDEA 100%);
        }

        .kpi-meta-value {
            font-size: 11px;
            font-weight: 800;
            color: #0F172A;
            font-variant-numeric: tabular-nums;
        }

        .kpi-parcial-note {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            width: fit-content;
            min-height: 18px;
            padding: 2px 8px;
            border-radius: 999px;
            border: 1px solid rgba(162, 59, 54, 0.16);
            background: linear-gradient(180deg, #FFF9F8 0%, #FFF2EF 100%);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.85);
            font-size: 9.5px;
            color: #7A1E19;
            font-weight: 800;
            margin: 0 auto;
            font-family: 'Manrope', 'Segoe UI', sans-serif !important;
            backdrop-filter: blur(6px) saturate(1.04);
        }

        .kpi-parcial-note::before {
            content: "";
            width: 6px;
            height: 6px;
            border-radius: 999px;
            background: #B42318;
            box-shadow: 0 0 0 2px rgba(180, 35, 24, 0.12);
            flex: 0 0 auto;
        }

        .kpi-value-wrap {
            position: relative;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.26rem;
            width: fit-content;
            line-height: 1;
            overflow: visible;
            padding: 1px 9px;
            border-radius: 12px;
            border: 1px solid rgba(200, 168, 108, 0.16);
            background:
                radial-gradient(circle at top center, rgba(255, 255, 255, 0.78) 0%, rgba(255, 255, 255, 0) 58%),
                linear-gradient(180deg, rgba(255, 255, 255, 0.985) 0%, rgba(250, 251, 253, 0.97) 100%),
                linear-gradient(135deg, rgba(200, 168, 108, 0.035), rgba(200, 168, 108, 0.00));
            box-shadow:
                inset 0 1px 0 rgba(255, 255, 255, 0.95),
                0 3px 8px rgba(15, 23, 42, 0.04),
                0 1px 0 rgba(200, 168, 108, 0.04);
            margin: 0 auto 2px auto;
            backdrop-filter: blur(6px) saturate(1.04);
        }

        .kpi-value-wrap .kpi-value-dinamico {
            display: block;
            text-align: center;
        }

        .kpi-value-wrap > * {
            position: relative;
            z-index: 1;
        }

        .kpi-value-wrap::before {
            content: "";
            position: absolute;
            inset: 1px;
            border-radius: 11px;
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.60), rgba(255, 255, 255, 0.02));
            pointer-events: none;
        }

        .kpi-value-wrap::after {
            content: "";
            position: absolute;
            left: 14%;
            right: 14%;
            top: 0;
            height: 1px;
            background: linear-gradient(90deg, transparent, rgba(200, 168, 108, 0.45), transparent);
            pointer-events: none;
        }

        .kpi-tooltip {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 31px;
            height: 17px;
            padding: 0 6px;
            border-radius: 999px;
            font-size: 7.5px;
            font-weight: 900;
            letter-spacing: 0.8px;
            white-space: nowrap;
            color: #FFFFFF !important;
            -webkit-text-fill-color: #FFFFFF !important;
            background: linear-gradient(135deg, #B63A33 0%, #790E09 100%) !important;
            border: 1px solid rgba(230, 213, 178, 0.72);
            box-shadow:
                0 4px 10px rgba(121, 14, 9, 0.25),
                0 0 0 1px rgba(200, 168, 108, 0.10);
            cursor: help;
            line-height: 1;
            font-family: 'Manrope', 'Segoe UI', sans-serif !important;
            text-shadow: 0 1px 0 rgba(61, 7, 4, 0.22);
        }

        .kpi-tooltip-inline {
            position: absolute;
            left: 100%;
            top: 3px;
            transform: translate(8px, 0);
            z-index: 3;
        }

        .kpi-block-label {
            display: flex;
            align-items: center;
            justify-content: center;
            width: fit-content;
            min-height: 19px;
            padding: 2px 8px;
            border-radius: 999px;
            gap: 5px;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.76), rgba(255, 255, 255, 0) 46%),
                linear-gradient(180deg, rgba(255, 252, 248, 0.98) 0%, rgba(255, 247, 240, 0.94) 100%),
                linear-gradient(135deg, rgba(200, 168, 108, 0.10), rgba(162, 59, 54, 0.04));
            border: 1px solid rgba(200, 168, 108, 0.20);
            color: #7A1E19;
            font-size: 9.5px;
            font-weight: 800;
            letter-spacing: 0.75px;
            text-transform: uppercase;
            margin: 0 auto 5px auto;
            box-shadow:
                inset 0 1px 0 rgba(255, 255, 255, 0.88),
                0 2px 5px rgba(15, 23, 42, 0.04);
        }

        .kpi-block-label::before {
            content: "";
            width: 5px;
            height: 5px;
            border-radius: 999px;
            background: linear-gradient(135deg, var(--ds-gold) 0%, #D96A5F 38%, #790E09 100%);
            box-shadow: 0 0 0 2px rgba(200, 168, 108, 0.14);
            flex: 0 0 auto;
        }

        .kpi-grid-dual {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }

        .kpi-title-dinamico {
            display: flex !important;
            align-items: center;
            justify-content: center;
            gap: 9px;
            background: none !important;
            color: #5A0A06 !important;
            -webkit-text-fill-color: initial !important;
        }

        .kpi-title-text {
            display: inline-flex;
            align-items: center;
            background: linear-gradient(180deg, #7A1E19 0%, #5A0A06 100%);
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .kpi-title-icon,
        .kpi-block-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 auto;
        }

        .kpi-title-icon {
            width: 32px;
            height: 32px;
            border-radius: 12px;
            background: linear-gradient(135deg, #FF2800 0%, #790E09 100%);
            color: #FFFFFF !important;
            box-shadow: 0 8px 18px rgba(121, 14, 9, 0.18);
        }

        .kpi-title-icon svg {
            width: 17px;
            height: 17px;
        }

        .kpi-block-label {
            display: inline-flex !important;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }

        .kpi-block-label::before {
            display: none !important;
        }

        .kpi-block-icon {
            width: 16px;
            height: 16px;
            border-radius: 999px;
            background: linear-gradient(135deg, rgba(255, 40, 0, 0.12), rgba(121, 14, 9, 0.18));
            color: #790E09 !important;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.42);
        }

        .kpi-block-icon svg {
            width: 10px;
            height: 10px;
        }

        .kpi-value-dinamico {
            font-variant-numeric: tabular-nums;
        }

        .kpi-tooltip {
            gap: 4px;
            min-width: auto;
            padding: 0 7px;
        }

        .kpi-tooltip svg {
            width: 10px;
            height: 10px;
        }

        .variacao-positiva {
            color: #166534 !important;
            background: linear-gradient(180deg, #F4FBF6 0%, #E8F5ED 100%) !important;
            border-color: rgba(22, 101, 52, 0.30) !important;
        }

        .variacao-positiva::before {
            background: #22C55E;
            box-shadow: 0 0 0 2px rgba(34, 197, 94, 0.20);
        }

        .variacao-negativa {
            color: #B42318 !important;
            background: linear-gradient(180deg, #FFF6F5 0%, #FDECEC 100%) !important;
            border-color: rgba(180, 35, 24, 0.30) !important;
        }

        .variacao-negativa::before {
            background: #DC2626;
            box-shadow: 0 0 0 2px rgba(220, 38, 38, 0.20);
        }

        .variacao-neutra {
            color: #475569 !important;
            background: linear-gradient(180deg, #F8FAFC 0%, #EEF2F7 100%) !important;
            border-color: rgba(71, 85, 105, 0.25) !important;
        }

        .variacao-neutra::before {
            background: #94A3B8;
            box-shadow: 0 0 0 2px rgba(148, 163, 184, 0.20);
        }

        .evo-monthly-panel {
            position: relative;
            overflow: hidden;
            display: block;
            padding: 7px 9px;
            margin: 8px 0 10px 0;
            border-radius: 12px;
            border: 1px solid rgba(121, 14, 9, 0.14);
            background:
                linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(246,248,252,0.98) 100%),
                linear-gradient(135deg, rgba(162,59,54,0.04) 0%, rgba(162,59,54,0.00) 46%);
            box-shadow:
                0 1px 2px rgba(16, 24, 40, 0.04),
                0 10px 24px rgba(16, 24, 40, 0.07),
                inset 0 1px 0 rgba(255, 255, 255, 0.96);
        }

        .evo-monthly-panel::before {
            content: "";
            position: absolute;
            inset: 0 auto 0 0;
            width: 3px;
            background: linear-gradient(180deg, #D96A5F 0%, #A23B36 58%, #6B0D09 100%);
        }

        .evo-monthly-summary {
            position: relative;
            z-index: 1;
            flex: 1 1 250px;
            min-width: 215px;
            padding: 10px 12px;
            border-radius: 12px;
            border: 1px solid rgba(121, 14, 9, 0.14);
            border-top: 3px solid #790E09;
            background:
                linear-gradient(180deg, rgba(255,255,255,0.99) 0%, rgba(255,249,248,0.98) 100%);
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,0.96),
                0 6px 16px rgba(121, 14, 9, 0.06);
        }

        .evo-monthly-summary-label {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            font-size: 9px;
            font-weight: 800;
            letter-spacing: 0.9px;
            text-transform: uppercase;
            color: #7A1E19;
            margin-bottom: 5px;
        }

        .evo-monthly-summary-label::before {
            content: "";
            width: 7px;
            height: 7px;
            border-radius: 999px;
            background: #A23B36;
            box-shadow: 0 0 0 2px rgba(162, 59, 54, 0.14);
            flex: 0 0 auto;
        }

        .evo-monthly-summary-value {
            font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif;
            font-size: 19px;
            font-weight: 800;
            line-height: 1.1;
            letter-spacing: -0.3px;
            color: #1F2937;
            word-break: break-word;
        }

        .evo-monthly-summary-aux {
            margin-top: 6px;
            font-size: 10px;
            font-weight: 600;
            line-height: 1.3;
            color: #64748B;
            word-break: break-word;
        }

        .evo-monthly-context {
            position: relative;
            z-index: 1;
            display: flex;
            align-items: center;
            gap: 6px;
            flex-wrap: nowrap;
            min-width: 0;
            margin: 0 0 7px 0;
            padding: 0 1px;
        }

        .evo-monthly-context-chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            min-width: 0;
            max-width: calc(33.33% - 4px);
            padding: 4px 8px;
            border-radius: 999px;
            border: 1px solid rgba(121, 14, 9, 0.12);
            background: linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(252,248,248,0.98) 100%);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.96);
        }

        .evo-monthly-context-label {
            flex: 0 0 auto;
            font-size: 8px;
            font-weight: 800;
            letter-spacing: 0.10em;
            text-transform: uppercase;
            color: #7A1E19;
            line-height: 1;
        }

        .evo-monthly-context-value {
            min-width: 0;
            flex: 1 1 auto;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-size: 10px;
            font-weight: 700;
            color: #334155;
            line-height: 1.1;
        }

        .evo-monthly-legends {
            position: relative;
            z-index: 1;
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            align-items: stretch;
            gap: 7px;
            width: 100%;
        }

        .evo-monthly-legend-card {
            min-width: 0;
            padding: 6px 8px;
            border-radius: 10px;
            border: 1px solid rgba(121, 14, 9, 0.12);
            background: linear-gradient(180deg, #FFFFFF 0%, #FFF9F8 100%);
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,0.96),
                0 3px 10px rgba(15, 23, 42, 0.04);
        }

        .evo-monthly-legend-head {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 3px;
        }

        .evo-monthly-legend-dot {
            width: 9px;
            height: 9px;
            border-radius: 999px;
            border: 1.5px solid #FFFFFF;
            box-shadow: 0 1px 3px rgba(15,23,42,0.10);
            flex: 0 0 auto;
        }

        .evo-monthly-legend-dot.is-orc {
            border-radius: 3px;
        }

        .evo-monthly-legend-title {
            font-size: 8px;
            font-weight: 800;
            letter-spacing: 0.10em;
            text-transform: uppercase;
            color: #7A1E19;
            line-height: 1.1;
        }

        .evo-monthly-legend-body {
            display: flex;
            align-items: center;
            gap: 5px;
            flex-wrap: wrap;
            min-width: 0;
        }

        .evo-monthly-legend-text {
            font-size: 12px;
            font-weight: 800;
            line-height: 1.12;
            color: #334155;
        }

        .evo-monthly-legend-divider {
            font-size: 10px;
            font-weight: 700;
            color: #94A3B8;
            line-height: 1;
        }

        .evo-monthly-legend-note {
            font-size: 10px;
            font-weight: 600;
            color: #64748B;
            line-height: 1.2;
            min-width: 0;
            flex: 1 1 auto;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .stTabs [data-baseweb="tab-list"] {
            position: relative;
            overflow: hidden;
            background: linear-gradient(180deg, #FFFFFF 0%, #F4F6F9 100%);
            border: 1px solid #E2E8F0;
            border-radius: 16px;
            box-shadow:
                0 1px 1px rgba(16, 24, 40, 0.04),
                0 10px 24px rgba(16, 24, 40, 0.08),
                inset 0 1px 0 rgba(255, 255, 255, 0.95);
            padding: 7px;
            gap: 8px;
            margin-bottom: 22px;
            backdrop-filter: blur(4px);
        }

        .stTabs [data-baseweb="tab-list"]::before {
            content: "";
            position: absolute;
            inset: 0;
            background: linear-gradient(120deg, rgba(255,255,255,0.45) 0%, rgba(255,255,255,0.00) 35%);
            pointer-events: none;
        }

        .stTabs [data-baseweb="tab"] {
            position: relative;
            overflow: hidden;
            background: transparent !important;
            color: #4B5563 !important;
            border: 1px solid transparent !important;
            box-shadow: none !important;
            border-radius: 12px;
            padding: 10px 18px;
            min-height: 43px;
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 0.2px;
            transition: all 0.22s ease;
            z-index: 1;
        }

        .stTabs [data-baseweb="tab"]::before {
            content: "";
            position: absolute;
            left: 10px;
            right: 10px;
            top: 7px;
            height: 36%;
            border-radius: 10px;
            background: linear-gradient(180deg, rgba(255,255,255,0.55), rgba(255,255,255,0));
            opacity: 0;
            transition: opacity 0.22s ease;
            pointer-events: none;
        }

        .stTabs [data-baseweb="tab"]:hover {
            background: #FFFFFF !important;
            color: #1F2937 !important;
            border-color: #E4EAF2 !important;
            box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08) !important;
            transform: translateY(-1px);
        }

        .stTabs [data-baseweb="tab"]:hover::before {
            opacity: 1;
        }

        .stTabs [data-baseweb="tab"]:focus-visible {
            outline: none !important;
            box-shadow: 0 0 0 3px rgba(121, 14, 9, 0.16) !important;
        }

        .stTabs [data-baseweb="tab"]::after {
            content: "";
            position: absolute;
            left: 14px;
            right: 14px;
            bottom: 5px;
            height: 2px;
            background: transparent;
            border-radius: 2px;
            transition: all 0.22s ease;
        }

        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, #B2463F 0%, #8D1A12 55%, #790E09 100%) !important;
            color: #FFFFFF !important;
            border-color: #790E09 !important;
            box-shadow:
                0 8px 18px rgba(121, 14, 9, 0.28),
                inset 0 1px 0 rgba(255, 255, 255, 0.20) !important;
            transform: translateY(-1px);
        }

        .stTabs [aria-selected="true"]::before {
            opacity: 1;
            background: linear-gradient(180deg, rgba(255,255,255,0.28), rgba(255,255,255,0));
        }

        .stTabs [aria-selected="true"]::after {
            background: rgba(255, 255, 255, 0.95);
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
            min-height: 46px;
            padding: 7px 12px !important;
            border: 1.5px solid var(--ds-border-strong) !important;
            border-radius: 10px !important;
            background: #FFFFFF !important;
            box-shadow: none !important;
            align-items: center !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover,
        [data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within > div,
        [data-testid="stMultiSelect"] div[data-baseweb="select"]:focus-within > div {
            border-color: var(--ds-primary-soft) !important;
            box-shadow: 0 0 0 3px rgba(121, 14, 9, 0.10) !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] > div *,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div * {
            color: var(--ds-text) !important;
            -webkit-text-fill-color: var(--ds-text) !important;
            font-weight: 600 !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] span,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] span {
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
            line-height: 1.25 !important;
            max-height: none !important;
        }

        [data-testid="stMultiSelect"] span[data-baseweb="tag"] {
            background: var(--ds-primary) !important;
            border: none !important;
            box-shadow: none !important;
        }

        [data-testid="stMultiSelect"] span[data-baseweb="tag"] * {
            color: #FFFFFF !important;
            -webkit-text-fill-color: #FFFFFF !important;
        }

        [data-testid="stWidgetLabel"] p {
            color: var(--ds-text-muted) !important;
            letter-spacing: 0.3px !important;
        }

        .filter-title {
            color: var(--ds-text);
            border-bottom: 1px solid var(--ds-border);
            margin-bottom: 10px;
            padding-bottom: 8px;
        }

        .info-box {
            background: var(--ds-surface) !important;
            border: 1px solid var(--ds-border) !important;
            box-shadow: none !important;
        }

        .info-box-title {
            color: var(--ds-primary);
            letter-spacing: 0.7px;
        }

        .stButton > button {
            background: var(--ds-primary);
            border: 1px solid var(--ds-primary);
            box-shadow: none;
        }

        .stButton > button:hover {
            background: #5A0A06;
            border-color: #5A0A06;
            box-shadow: none;
            transform: none;
        }

        [data-testid="stDownloadButton"] > button {
            width: 100% !important;
            min-height: 40px !important;
            border-radius: 10px !important;
            border: 1.5px solid var(--ds-primary-soft) !important;
            background: #FFFFFF !important;
            color: var(--ds-primary) !important;
            box-shadow: none !important;
            font-weight: 700 !important;
            transition: all 0.2s ease !important;
        }

        [data-testid="stDownloadButton"] > button:hover {
            border-color: var(--ds-primary) !important;
            background: #FAF3F2 !important;
            color: #5A0A06 !important;
            box-shadow: 0 2px 8px rgba(121, 14, 9, 0.12) !important;
        }

        [data-testid="stDownloadButton"] > button:focus {
            outline: none !important;
            box-shadow: 0 0 0 3px rgba(121, 14, 9, 0.14) !important;
        }

        [data-testid="stDownloadButton"] > button p {
            font-size: 13px !important;
            font-weight: 700 !important;
            letter-spacing: 0.1px !important;
            color: inherit !important;
        }

        [data-testid="stCaptionContainer"] {
            margin-top: 4px !important;
            padding: 10px 12px !important;
            border: 1px solid var(--ds-border) !important;
            border-left: 3px solid var(--ds-primary-soft) !important;
            border-radius: 10px !important;
            background: #FFFFFF !important;
        }

        [data-testid="stCaptionContainer"] p {
            color: var(--ds-text-muted) !important;
            font-size: 12px !important;
            line-height: 1.4 !important;
            margin: 0 !important;
        }

        [data-testid="stCaptionContainer"] strong {
            color: var(--ds-text) !important;
        }

        /* Sidebar filters: keep layout compact and prevent chip overflow */
        [data-testid="stSidebar"] [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
            min-height: 44px !important;
            max-height: 52px !important;
            overflow: hidden !important;
            align-items: center !important;
        }

        [data-testid="stSidebar"] [data-testid="stMultiSelect"] div[data-baseweb="select"] > div > div {
            display: flex !important;
            flex-wrap: nowrap !important;
            gap: 6px !important;
            overflow: hidden !important;
            align-items: center !important;
        }

        [data-testid="stSidebar"] [data-testid="stMultiSelect"] span[data-baseweb="tag"] {
            max-width: 120px !important;
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            padding: 2px 8px !important;
        }

        /* Filtros com fundo vermelho: garantir texto branco */
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
            background: linear-gradient(135deg, #FF5434 0%, #7A120C 100%) !important;
            border-color: #A23B36 !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] > div *,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div *,
        [data-testid="stSelectbox"] div[data-baseweb="select"] span,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] span,
        [data-testid="stSelectbox"] div[data-baseweb="select"] input,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] input {
            color: #FFFFFF !important;
            -webkit-text-fill-color: #FFFFFF !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] input::placeholder,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] input::placeholder {
            color: #FFD5CB !important;
            -webkit-text-fill-color: #FFD5CB !important;
            opacity: 1 !important;
        }

        /* Compactacao geral de espacamento (sem alterar estrutura dos blocos) */
        .block-container {
            padding-top: 9px !important;
            padding-bottom: 11px !important;
        }

        div[data-testid="stVerticalBlock"] {
            gap: 0.42rem !important;
        }

        div[data-testid="stHorizontalBlock"] {
            gap: 0.86rem !important;
        }

        .element-container,
        div[data-testid="stElementContainer"] {
            margin-bottom: 0.20rem !important;
        }

        .main-title {
            margin: 8px 0 14px 0 !important;
            padding: 22px 0 36px 0 !important;
            line-height: 1.14 !important;
        }

        /* Evita sobreposição do subtítulo com o título principal */
        .main-title::after {
            position: static !important;
            display: block !important;
            transform: none !important;
            left: auto !important;
            bottom: auto !important;
            margin: 10px auto 0 auto !important;
            width: fit-content;
        }

        .section-title {
            margin: 20px 0 11px 0 !important;
            padding: 11px 14px !important;
        }

        .subsection-title {
            margin: 14px 0 9px 0 !important;
            padding: 10px 12px !important;
        }

        .card-title {
            margin: 0 0 11px 0 !important;
            padding: 11px 12px !important;
        }

        .filter-title {
            margin: 0 0 10px 0 !important;
            padding-bottom: 6px !important;
        }

        /* Microajuste: mais respiro apenas nos filtros */
        [data-testid="stWidgetLabel"] {
            margin-bottom: 10px !important;
        }

        [data-testid="stWidgetLabel"] p {
            margin: 0 !important;
            line-height: 1.2 !important;
        }

        [data-testid="stSelectbox"],
        [data-testid="stMultiSelect"] {
            margin: 0 0 12px 0 !important;
        }

        [data-testid="stSelectbox"] > div,
        [data-testid="stMultiSelect"] > div {
            margin-top: 2px !important;
        }

        [data-testid="stSidebar"] [data-testid="stSelectbox"],
        [data-testid="stSidebar"] [data-testid="stMultiSelect"] {
            margin-bottom: 14px !important;
        }

        .info-box {
            margin: 11px 0 !important;
            padding: 12px 14px !important;
        }

        .stTabs [data-baseweb="tab-list"] {
            margin-bottom: 12px !important;
            padding: 7px !important;
        }

        .stPlotlyChart,
        div[data-testid="stPlotlyChart"] {
            margin-top: 0.10rem !important;
            margin-bottom: 0.34rem !important;
        }

        [data-testid="stDataFrame"] {
            margin-top: 0.16rem !important;
            margin-bottom: 0.30rem !important;
        }

        [data-testid="stMarkdownContainer"] p {
            margin-bottom: 0.36rem !important;
        }

        hr {
            margin: 0.46rem 0 !important;
        }

        @media (max-width: 768px) {
            .section-title {
                font-size: 22px;
                padding: 11px 13px;
                margin: 17px 0 10px 0;
            }

            .kpi-card-dinamico {
                min-height: 48px;
            }

            .kpi-grid-dual {
                grid-template-columns: 1fr;
                gap: 10px;
            }

            .kpi-title-icon {
                width: 28px;
                height: 28px;
            }

            .kpi-card-stack-soft-left,
            .kpi-card-stack-soft-right {
                left: 0;
            }

            .analitico-corte-info {
                font-size: 12px;
                padding: 8px 10px;
            }
        }
    </style>
""", unsafe_allow_html=True)

st.markdown("""
    <style>
        @media not all {
        /* Executive visual layer for the regional tables in ATIVADOS, PEDIDOS, LIGACOES and DESATIVACOES. */
        body .tabela-container-melhorada,
        body .tabela-container-pedidos,
        body .tabela-container-ligacoes,
        body .tabela-container-desativados {
            border: 1px solid rgba(121, 14, 9, 0.74) !important;
            border-radius: 4px !important;
            background: linear-gradient(180deg, #FFFFFF 0%, #FFF8F7 100%) !important;
            box-shadow:
                0 16px 34px rgba(90, 10, 6, 0.13),
                0 3px 10px rgba(15, 23, 42, 0.07),
                inset 0 0 0 1px rgba(255, 255, 255, 0.92) !important;
            font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        }

        body .tabela-container-melhorada::after,
        body .tabela-container-pedidos::after {
            content: none !important;
            display: none !important;
        }

        body table.tabela-melhorada,
        body table.tabela-pedidos,
        body table.tabela-ligacoes,
        body table.tabela-desativados {
            font-family: 'Manrope', 'Segoe UI', sans-serif !important;
            font-variant-numeric: tabular-nums !important;
        }

        body table.tabela-melhorada th,
        body table.tabela-pedidos th,
        body table.tabela-ligacoes th,
        body table.tabela-desativados th {
            border-right: 1px solid rgba(255, 255, 255, 0.24) !important;
            border-bottom: 1px solid rgba(61, 7, 4, 0.92) !important;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.14) !important;
            text-shadow: 0 1px 0 rgba(0, 0, 0, 0.18) !important;
            font-weight: 800 !important;
            letter-spacing: 0.22px !important;
        }

        body table.tabela-melhorada th:first-child,
        body table.tabela-pedidos th:first-child,
        body table.tabela-ligacoes th:first-child,
        body table.tabela-desativados th:first-child {
            border-top-left-radius: 3px !important;
        }

        body table.tabela-melhorada th:last-child,
        body table.tabela-pedidos th:last-child,
        body table.tabela-ligacoes th:last-child,
        body table.tabela-desativados th:last-child {
            border-top-right-radius: 3px !important;
        }

        body table.tabela-melhorada th.col-total-anual,
        body table.tabela-pedidos th.col-total-anual-pedidos,
        body table.tabela-ligacoes th.col-total-anual,
        body table.tabela-desativados th.col-total-anual-desativados {
            background: linear-gradient(135deg, #A4342D 0%, #7A130E 100%) !important;
            box-shadow: inset 0 -2px 0 rgba(255, 40, 0, 0.26) !important;
        }

        body table.tabela-melhorada th.col-mes-2025,
        body table.tabela-pedidos th.col-mes-pedidos,
        body table.tabela-ligacoes th.col-mes-2025,
        body table.tabela-desativados th.col-mes-2025-desativados {
            background: linear-gradient(135deg, #790E09 0%, #5A0A06 100%) !important;
        }

        body table.tabela-melhorada th.col-mes,
        body table.tabela-melhorada th.col-real-mes,
        body table.tabela-pedidos th.col-mes-2026-pedidos,
        body table.tabela-pedidos th.col-real-jan26-pedidos,
        body table.tabela-ligacoes th.col-mes-2026,
        body table.tabela-ligacoes th.col-real-mes,
        body table.tabela-desativados th.col-real-mes-desativados {
            background: linear-gradient(135deg, #8F1B14 0%, #6C0C08 100%) !important;
        }

        body table.tabela-melhorada th.col-tend,
        body table.tabela-pedidos th.col-tend-pedidos {
            background: linear-gradient(135deg, #6B7280 0%, #475569 100%) !important;
            box-shadow: inset 0 -2px 0 rgba(255, 255, 255, 0.14) !important;
        }

        body table.tabela-melhorada th.col-meta,
        body table.tabela-pedidos th.col-meta-pedidos,
        body table.tabela-ligacoes th.col-meta-mes {
            background: linear-gradient(135deg, #A4342D 0%, #7A130E 100%) !important;
        }

        body table.tabela-melhorada th.col-alcance,
        body table.tabela-melhorada th.col-variacao,
        body table.tabela-pedidos th.col-alcance-pedidos,
        body table.tabela-pedidos th.col-variacao-pedidos,
        body table.tabela-ligacoes th.col-alcance,
        body table.tabela-ligacoes th.col-variacao,
        body table.tabela-desativados th.col-variacao-desativados {
            background: linear-gradient(135deg, #5A6268 0%, #3E444A 100%) !important;
            box-shadow: inset 0 -2px 0 rgba(255, 255, 255, 0.12) !important;
        }

        body table.tabela-melhorada td,
        body table.tabela-pedidos td,
        body table.tabela-ligacoes td,
        body table.tabela-desativados td {
            border-bottom: 1px solid rgba(121, 14, 9, 0.07) !important;
            border-right: 1px solid rgba(121, 14, 9, 0.055) !important;
            color: #2F3747 !important;
        }

        body table.tabela-melhorada td {
            font-size: 10px !important;
            line-height: 1.15 !important;
        }

        body table.tabela-pedidos td,
        body table.tabela-ligacoes td,
        body table.tabela-desativados td {
            font-size: 10.6px !important;
            line-height: 1.18 !important;
        }

        body table.tabela-melhorada tr:not(.linha-total-melhorada) td:first-child,
        body table.tabela-pedidos tr:not(.linha-total-pedidos) td:first-child,
        body table.tabela-ligacoes tr:not(.linha-total-ligacoes) td:first-child,
        body table.tabela-desativados tr:not(.linha-total-desativados) td:first-child {
            color: #2F3747 !important;
            font-weight: 800 !important;
            box-shadow: inset 3px 0 0 rgba(255, 40, 0, 0.26), 4px 0 10px rgba(90, 10, 6, 0.025) !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada:nth-child(even) td,
        body table.tabela-pedidos tr.linha-regional-pedidos:nth-child(even) td,
        body table.tabela-ligacoes tr.linha-regional-ligacoes:nth-child(even) td,
        body table.tabela-desativados tr.linha-regional-desativados:nth-child(even) td {
            background: #FFFFFF !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada:nth-child(odd) td,
        body table.tabela-pedidos tr.linha-regional-pedidos:nth-child(odd) td,
        body table.tabela-ligacoes tr.linha-regional-ligacoes:nth-child(odd) td,
        body table.tabela-desativados tr.linha-regional-desativados:nth-child(odd) td {
            background: #FFF7F6 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada:hover td,
        body table.tabela-pedidos tr.linha-regional-pedidos:hover td,
        body table.tabela-ligacoes tr.linha-regional-ligacoes:hover td,
        body table.tabela-desativados tr.linha-regional-desativados:hover td {
            background: linear-gradient(90deg, #FFF3F0 0%, #FFF8F7 100%) !important;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.70) !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada:hover td:first-child,
        body table.tabela-pedidos tr.linha-regional-pedidos:hover td:first-child,
        body table.tabela-ligacoes tr.linha-regional-ligacoes:hover td:first-child,
        body table.tabela-desativados tr.linha-regional-desativados:hover td:first-child {
            box-shadow: inset 3px 0 0 #FF2800, 4px 0 10px rgba(90, 10, 6, 0.035) !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.col-total-anual,
        body table.tabela-pedidos tr.linha-regional-pedidos td.col-total-anual-pedidos,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-total-anual,
        body table.tabela-desativados tr.linha-regional-desativados td.col-total-anual-desativados {
            background: linear-gradient(180deg, #F3F5F7 0%, #E9EDF1 100%) !important;
            color: #1F2937 !important;
            font-weight: 850 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.col-mes,
        body table.tabela-melhorada tr.linha-regional-melhorada td.col-real-mes,
        body table.tabela-pedidos tr.linha-regional-pedidos td.col-mes-2026-pedidos,
        body table.tabela-pedidos tr.linha-regional-pedidos td.col-real-jan26-pedidos,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-mes-2026,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-real-mes,
        body table.tabela-desativados tr.linha-regional-desativados td.col-real-mes-desativados {
            background: linear-gradient(180deg, #F7F8FA 0%, #EEF1F4 100%) !important;
            color: #1F2937 !important;
            font-weight: 800 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.col-tend,
        body table.tabela-pedidos tr.linha-regional-pedidos td.col-tend-pedidos {
            background: linear-gradient(180deg, #F7F8FA 0%, #EEF1F4 100%) !important;
            color: #2F3747 !important;
            font-weight: 850 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.col-meta,
        body table.tabela-pedidos tr.linha-regional-pedidos td.col-meta-pedidos,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-meta-mes {
            background: linear-gradient(180deg, #FFF0ED 0%, #F8D9D4 100%) !important;
            color: #6B1F1A !important;
            font-weight: 850 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.col-alcance,
        body table.tabela-melhorada tr.linha-regional-melhorada td.col-variacao,
        body table.tabela-pedidos tr.linha-regional-pedidos td.col-alcance-pedidos,
        body table.tabela-pedidos tr.linha-regional-pedidos td.col-variacao-pedidos,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-alcance,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-variacao,
        body table.tabela-desativados tr.linha-regional-desativados td.col-variacao-desativados {
            background: linear-gradient(180deg, #F7F8FA 0%, #EEF1F4 100%) !important;
            border-left: 1px solid rgba(100, 116, 139, 0.16) !important;
            border-right: 1px solid rgba(100, 116, 139, 0.12) !important;
            font-weight: 850 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.percentual-positivo,
        body table.tabela-pedidos tr.linha-regional-pedidos td.percentual-positivo-pedidos,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.valor-positivo,
        body table.tabela-desativados tr.linha-regional-desativados td.percentual-positivo-desativados {
            color: #1B5E20 !important;
            background: linear-gradient(180deg, #F2FAF4 0%, #EAF6EE 100%) !important;
            font-weight: 900 !important;
            position: relative !important;
            padding-left: 16px !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.percentual-negativo,
        body table.tabela-pedidos tr.linha-regional-pedidos td.percentual-negativo-pedidos,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.valor-negativo,
        body table.tabela-desativados tr.linha-regional-desativados td.percentual-negativo-desativados {
            color: #B71C1C !important;
            background: linear-gradient(180deg, #FFF3F1 0%, #FBE4E0 100%) !important;
            font-weight: 900 !important;
            position: relative !important;
            padding-left: 16px !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.percentual-neutro,
        body table.tabela-pedidos tr.linha-regional-pedidos td.percentual-neutro-pedidos,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.valor-neutro,
        body table.tabela-desativados tr.linha-regional-desativados td.percentual-neutro-desativados {
            color: #475569 !important;
            background: linear-gradient(180deg, #F7F8FA 0%, #EEF1F4 100%) !important;
            font-weight: 850 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.percentual-positivo::before,
        body table.tabela-pedidos tr.linha-regional-pedidos td.percentual-positivo-pedidos::before,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.valor-positivo::before,
        body table.tabela-desativados tr.linha-regional-desativados td.percentual-positivo-desativados::before {
            content: "▲" !important;
            position: absolute !important;
            left: 5px !important;
            top: 50% !important;
            transform: translateY(-50%) !important;
            font-size: 8px !important;
            font-weight: 900 !important;
            color: #2E7D32 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.percentual-negativo::before,
        body table.tabela-pedidos tr.linha-regional-pedidos td.percentual-negativo-pedidos::before,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.valor-negativo::before,
        body table.tabela-desativados tr.linha-regional-desativados td.percentual-negativo-desativados::before {
            content: "▼" !important;
            position: absolute !important;
            left: 5px !important;
            top: 50% !important;
            transform: translateY(-50%) !important;
            font-size: 8px !important;
            font-weight: 900 !important;
            color: #C62828 !important;
        }

        body table.tabela-melhorada tr.linha-total-melhorada td,
        body table.tabela-pedidos tr.linha-total-pedidos td,
        body table.tabela-ligacoes tr.linha-total-ligacoes td,
        body table.tabela-desativados tr.linha-total-desativados td {
            background: linear-gradient(180deg, #5A0A06 0%, #330504 100%) !important;
            color: #FFFFFF !important;
            font-weight: 900 !important;
            border-right: 1px solid rgba(255, 255, 255, 0.15) !important;
            border-top: 1px solid rgba(255, 255, 255, 0.22) !important;
            border-bottom: 1px solid rgba(61, 7, 4, 0.92) !important;
        }

        body table.tabela-melhorada tr.linha-total-melhorada td:first-child,
        body table.tabela-pedidos tr.linha-total-pedidos td:first-child,
        body table.tabela-ligacoes tr.linha-total-ligacoes td:first-child,
        body table.tabela-desativados tr.linha-total-desativados td:first-child {
            box-shadow: inset 4px 0 0 rgba(255, 40, 0, 0.58) !important;
        }

        body table.tabela-melhorada tr.linha-total-melhorada td::before,
        body table.tabela-pedidos tr.linha-total-pedidos td::before,
        body table.tabela-ligacoes tr.linha-total-ligacoes td::before,
        body table.tabela-desativados tr.linha-total-desativados td::before {
            content: "" !important;
        }

        body table.tabela-melhorada td.performance-excelente,
        body table.tabela-melhorada td.performance-critica,
        body table.tabela-pedidos td.performance-excelente-pedidos,
        body table.tabela-pedidos td.performance-critica-pedidos {
            animation: none !important;
        }
        }

        /* Clean regional table layer: alinhado ao visual de PEDIDOS POR REGIONAL. */
        body .tabela-container-melhorada,
        body .tabela-container-ligacoes,
        body .tabela-container-desativados {
            width: 100% !important;
            max-height: 650px !important;
            overflow-y: auto !important;
            overflow-x: auto !important;
            border: 2px solid #790E09 !important;
            border-radius: 10px !important;
            box-shadow: 0 4px 20px rgba(121, 14, 9, 0.15) !important;
            background: #FFFFFF !important;
            font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        }

        body .tabela-container-melhorada::after,
        body .tabela-container-ligacoes::after,
        body .tabela-container-desativados::after {
            content: none !important;
            display: none !important;
        }

        body table.tabela-melhorada,
        body table.tabela-ligacoes,
        body table.tabela-desativados {
            width: max-content !important;
            min-width: 100% !important;
            border-collapse: collapse !important;
            border-spacing: 0 !important;
            table-layout: auto !important;
            font-size: 9px !important;
            line-height: 1.04 !important;
            font-family: 'Manrope', 'Segoe UI', sans-serif !important;
            font-variant-numeric: tabular-nums !important;
        }

        body table.tabela-melhorada th,
        body table.tabela-ligacoes th,
        body table.tabela-desativados th {
            background: linear-gradient(135deg, #790E09 0%, #5A0A06 100%) !important;
            color: #FFFFFF !important;
            font-weight: 600 !important;
            padding: 5px 4px !important;
            text-align: center !important;
            border-bottom: 3px solid #5A0A06 !important;
            border-right: 1px solid #FFFFFF !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            word-break: break-word !important;
            font-size: 9px !important;
            letter-spacing: 0.5px !important;
            text-transform: uppercase !important;
            box-shadow: none !important;
            text-shadow: none !important;
        }

        body table.tabela-melhorada td,
        body table.tabela-ligacoes td,
        body table.tabela-desativados td {
            padding: 3.6px 4px !important;
            font-size: 9.3px !important;
            line-height: 1.12 !important;
            font-weight: 400 !important;
            color: #2F3747 !important;
            text-align: right !important;
            border-bottom: 1px solid #FFFFFF !important;
            border-right: 1px solid #FFFFFF !important;
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            box-shadow: none !important;
        }

        body table.tabela-melhorada tr:not(.linha-total-melhorada) td:first-child,
        body table.tabela-ligacoes tr:not(.linha-total-ligacoes) td:first-child,
        body table.tabela-desativados tr:not(.linha-total-desativados) td:first-child {
            text-align: left !important;
            font-weight: 400 !important;
            color: #333333 !important;
            background: transparent !important;
            padding-left: 7px !important;
            box-shadow: none !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada:nth-child(even) td,
        body table.tabela-ligacoes tr.linha-regional-ligacoes:nth-child(even) td,
        body table.tabela-desativados tr.linha-regional-desativados:nth-child(even) td {
            background: linear-gradient(135deg, #FCFCFD 0%, #F7F8FA 100%) !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada:nth-child(odd) td,
        body table.tabela-ligacoes tr.linha-regional-ligacoes:nth-child(odd) td,
        body table.tabela-desativados tr.linha-regional-desativados:nth-child(odd) td {
            background: linear-gradient(135deg, #FFFFFF 0%, #FAFBFC 100%) !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada:hover td,
        body table.tabela-ligacoes tr.linha-regional-ligacoes:hover td,
        body table.tabela-desativados tr.linha-regional-desativados:hover td {
            background: linear-gradient(135deg, #FFF6F3 0%, #FAF0ED 100%) !important;
            box-shadow: inset 0 0 0 1px rgba(162, 59, 54, 0.12) !important;
            transform: none !important;
        }

        body table.tabela-melhorada tr.linha-total-melhorada td,
        body table.tabela-ligacoes tr.linha-total-ligacoes td,
        body table.tabela-desativados tr.linha-total-desativados td {
            background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
            color: #FFFFFF !important;
            font-weight: 400 !important;
            font-size: 9.5px !important;
            border-right: 1px solid rgba(255, 255, 255, 0.1) !important;
            box-shadow: none !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.col-total-anual,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-total-anual,
        body table.tabela-desativados tr.linha-regional-desativados td.col-total-anual-desativados {
            background: linear-gradient(180deg, rgba(47, 55, 71, 0.045) 0%, rgba(47, 55, 71, 0.018) 100%) !important;
            color: #1F2937 !important;
            font-weight: 600 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.col-mes,
        body table.tabela-melhorada tr.linha-regional-melhorada td.col-real-mes,
        body table.tabela-melhorada tr.linha-regional-melhorada td.col-tend,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-mes-2026,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-real-mes,
        body table.tabela-desativados tr.linha-regional-desativados td.col-real-mes-desativados {
            background: linear-gradient(180deg, rgba(47, 55, 71, 0.06) 0%, rgba(47, 55, 71, 0.025) 100%) !important;
            color: #1F2937 !important;
            font-weight: 600 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.col-meta,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-meta-mes {
            background: linear-gradient(180deg, rgba(121, 14, 9, 0.06) 0%, rgba(121, 14, 9, 0.022) 100%) !important;
            color: #6B1F1A !important;
            font-weight: 600 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.col-alcance,
        body table.tabela-melhorada tr.linha-regional-melhorada td.col-variacao,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-alcance,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.col-variacao,
        body table.tabela-desativados tr.linha-regional-desativados td.col-variacao-desativados {
            background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
            font-weight: 600 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.percentual-positivo,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.valor-positivo,
        body table.tabela-desativados tr.linha-regional-desativados td.percentual-positivo-desativados {
            color: #1B5E20 !important;
            background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
            font-weight: 700 !important;
        }

        body table.tabela-melhorada tr.linha-regional-melhorada td.percentual-negativo,
        body table.tabela-ligacoes tr.linha-regional-ligacoes td.valor-negativo,
        body table.tabela-desativados tr.linha-regional-desativados td.percentual-negativo-desativados {
            color: #C62828 !important;
            background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
            font-weight: 700 !important;
        }

        body table.tabela-melhorada td.performance-excelente,
        body table.tabela-melhorada td.performance-critica,
        body table.tabela-ligacoes td.performance-excelente,
        body table.tabela-ligacoes td.performance-boa,
        body table.tabela-ligacoes td.performance-media,
        body table.tabela-ligacoes td.performance-ruim,
        body table.tabela-ligacoes td.performance-critica {
            animation: none !important;
            box-shadow: none !important;
        }
    </style>
""", unsafe_allow_html=True)

if KPI_PILL_VARIANT == "clean":
    kpi_pill_style = """
    <style>
        .kpi-variacao-item {
            border-radius: 999px !important;
            border-width: 1px !important;
            min-height: 24px !important;
            padding: 3px 9px !important;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.78), rgba(255, 255, 255, 0) 46%),
                linear-gradient(180deg, #FFFCF8 0%, #F7FAFD 100%) !important;
            color: #475569 !important;
            box-shadow:
                inset 0 1px 0 rgba(255, 255, 255, 0.94),
                0 3px 8px rgba(15, 23, 42, 0.06),
                0 0 0 1px rgba(255, 255, 255, 0.42) !important;
            transition: transform 0.18s ease, box-shadow 0.18s ease !important;
            font-weight: 800 !important;
            letter-spacing: 0.25px !important;
            border-color: rgba(200, 168, 108, 0.16) !important;
        }
        .kpi-variacao-item::before {
            content: "" !important;
            width: 6px !important;
            height: 6px !important;
            border-radius: 999px !important;
            background: #94A3B8 !important;
            box-shadow: 0 0 0 2px rgba(148, 163, 184, 0.20) !important;
            flex: 0 0 auto !important;
        }
        .kpi-variacao-item:hover {
            transform: translateY(-1px);
            box-shadow:
                inset 0 1px 0 rgba(255, 255, 255, 0.97),
                0 5px 12px rgba(15, 23, 42, 0.10),
                0 0 0 1px rgba(162, 59, 54, 0.04) !important;
        }
        .variacao-positiva {
            color: #166534 !important;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.66), rgba(255, 255, 255, 0) 45%),
                linear-gradient(180deg, #F8FDF9 0%, #E8F5ED 100%) !important;
            border-color: rgba(22, 101, 52, 0.28) !important;
        }
        .variacao-positiva::before {
            background: #16A34A !important;
            box-shadow: 0 0 0 2px rgba(22, 163, 74, 0.22) !important;
        }
        .variacao-negativa {
            color: #B42318 !important;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.66), rgba(255, 255, 255, 0) 45%),
                linear-gradient(180deg, #FFFAF8 0%, #FDE8E6 100%) !important;
            border-color: rgba(180, 35, 24, 0.30) !important;
        }
        .variacao-negativa::before {
            background: #DC2626 !important;
            box-shadow: 0 0 0 2px rgba(220, 38, 38, 0.20) !important;
        }
        .variacao-neutra {
            color: #475569 !important;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.66), rgba(255, 255, 255, 0) 45%),
                linear-gradient(180deg, #FCFCFB 0%, #EEF2F7 100%) !important;
            border-color: rgba(200, 168, 108, 0.18) !important;
        }
        .variacao-neutra::before {
            background: #94A3B8 !important;
            box-shadow: 0 0 0 2px rgba(148, 163, 184, 0.20) !important;
        }
    </style>
    """
else:
    kpi_pill_style = """
    <style>
        .kpi-variacao-item {
            border-radius: 999px !important;
            border-width: 1px !important;
            min-height: 24px !important;
            padding: 3px 9px !important;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.78), rgba(255, 255, 255, 0) 46%),
                linear-gradient(180deg, #FFFCF8 0%, #F7FAFD 100%) !important;
            color: #475569 !important;
            box-shadow:
                inset 0 1px 0 rgba(255, 255, 255, 0.95),
                0 3px 8px rgba(15, 23, 42, 0.09),
                0 0 0 1px rgba(255, 255, 255, 0.46) !important;
            transition: transform 0.18s ease, box-shadow 0.18s ease !important;
            font-weight: 800 !important;
            letter-spacing: 0.25px !important;
            border-color: rgba(200, 168, 108, 0.18) !important;
        }
        .kpi-variacao-item::before {
            content: "" !important;
            width: 6px !important;
            height: 6px !important;
            border-radius: 999px !important;
            background: #94A3B8 !important;
            box-shadow: 0 0 0 2px rgba(148, 163, 184, 0.20) !important;
            flex: 0 0 auto !important;
        }
        .kpi-variacao-item:hover {
            transform: translateY(-1px);
            box-shadow:
                inset 0 1px 0 rgba(255, 255, 255, 0.97),
                0 5px 14px rgba(15, 23, 42, 0.12),
                0 0 0 1px rgba(162, 59, 54, 0.05) !important;
        }
        .variacao-positiva {
            color: #14532D !important;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.70), rgba(255, 255, 255, 0) 46%),
                linear-gradient(180deg, #F2FBF4 0%, #D8F0E1 100%) !important;
            border-color: rgba(22, 101, 52, 0.38) !important;
        }
        .variacao-positiva::before {
            background: #16A34A !important;
            box-shadow: 0 0 0 2px rgba(22, 163, 74, 0.28) !important;
        }
        .variacao-negativa {
            color: #991B1B !important;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.70), rgba(255, 255, 255, 0) 46%),
                linear-gradient(180deg, #FFF5F4 0%, #FCD8D6 100%) !important;
            border-color: rgba(185, 28, 28, 0.42) !important;
        }
        .variacao-negativa::before {
            background: #DC2626 !important;
            box-shadow: 0 0 0 2px rgba(220, 38, 38, 0.30) !important;
        }
        .variacao-neutra {
            color: #334155 !important;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.70), rgba(255, 255, 255, 0) 46%),
                linear-gradient(180deg, #FAFBFB 0%, #E5EBF2 100%) !important;
            border-color: rgba(200, 168, 108, 0.22) !important;
        }
        .variacao-neutra::before {
            background: #64748B !important;
            box-shadow: 0 0 0 2px rgba(100, 116, 139, 0.25) !important;
        }
    </style>
    """

st.markdown(kpi_pill_style, unsafe_allow_html=True)

st.markdown(
    """
    <style>
    :root {
        --ui-red: #FF2800;
        --ui-red-deep: #790E09;
        --ui-red-dark: #5A0A06;
        --ui-ink: #312B2A;
        --ui-muted: #7A6B69;
        --ui-border: rgba(121, 14, 9, 0.12);
        --ui-border-strong: rgba(121, 14, 9, 0.20);
        --ui-soft-bg: #FFF8F7;
        --ui-panel-bg: linear-gradient(180deg, #FFFFFF 0%, #FCFBFB 100%);
        --ui-shadow-soft: 0 0.85rem 2.2rem rgba(121, 14, 9, 0.08);
        --ui-shadow-card: 0 0.65rem 1.65rem rgba(121, 14, 9, 0.08);
        --ui-shadow-focus: 0 0 0 0.18rem rgba(255, 40, 0, 0.12);
    }

    .dashboard-hero-divider {
        position: relative;
        display: flex;
        align-items: center;
        justify-content: center;
        max-width: 38rem;
        margin: 0.45rem auto 1.35rem auto;
        padding: 0 0.4rem;
    }

    .dashboard-hero-divider-line {
        width: 100%;
        height: 0.14rem;
        border-radius: 999px;
        background: linear-gradient(
            90deg,
            rgba(255, 40, 0, 0.00) 0%,
            rgba(255, 40, 0, 0.18) 16%,
            rgba(255, 40, 0, 0.92) 50%,
            rgba(121, 14, 9, 0.18) 84%,
            rgba(121, 14, 9, 0.00) 100%
        );
        box-shadow: 0 0.35rem 1rem rgba(121, 14, 9, 0.14);
    }

    .dashboard-hero-divider-badge {
        position: absolute;
        display: inline-flex;
        align-items: center;
        gap: 0.36rem;
        padding: 0.28rem 0.88rem;
        border-radius: 999px;
        border: 1px solid rgba(121, 14, 9, 0.14);
        background: linear-gradient(180deg, #FFFFFF 0%, #FFF7F6 100%);
        box-shadow:
            0 0.45rem 1rem rgba(121, 14, 9, 0.10),
            inset 0 1px 0 rgba(255, 255, 255, 0.92);
    }

    .dashboard-hero-divider-badge i {
        display: block;
        width: 0.42rem;
        height: 0.42rem;
        border-radius: 999px;
        background: linear-gradient(135deg, #FF2800 0%, #790E09 100%);
        box-shadow: 0 0 0 0.15rem rgba(255, 40, 0, 0.08);
    }

    .dashboard-hero-divider-badge i:nth-child(2) {
        width: 0.5rem;
        height: 0.5rem;
        box-shadow: 0 0 0 0.18rem rgba(121, 14, 9, 0.09);
    }

    hr {
        border: none !important;
        height: 0.12rem !important;
        margin: 0.95rem 0 1.05rem 0 !important;
        background: linear-gradient(
            90deg,
            rgba(255, 40, 0, 0.00) 0%,
            rgba(255, 40, 0, 0.14) 18%,
            rgba(255, 40, 0, 0.62) 50%,
            rgba(121, 14, 9, 0.14) 82%,
            rgba(121, 14, 9, 0.00) 100%
        ) !important;
        border-radius: 999px !important;
    }

    .section-title,
    .subsection-title,
    .card-title {
        font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif !important;
        color: var(--ui-ink) !important;
        letter-spacing: -0.015em !important;
        position: relative;
        overflow: hidden;
        border: 1px solid var(--ui-border) !important;
    }

    .section-title {
        display: flex !important;
        align-items: flex-start !important;
        flex-wrap: nowrap !important;
        gap: 0.85rem !important;
        margin: 1.85rem 0 1.05rem 0 !important;
        padding: 1rem 1.18rem 0.94rem 1.14rem !important;
        border-left: 0.34rem solid var(--ui-red) !important;
        border-radius: 1.15rem !important;
        background: linear-gradient(90deg, rgba(255, 40, 0, 0.08) 0%, rgba(255, 255, 255, 0.96) 52%, #FFFDFD 100%) !important;
        box-shadow: var(--ui-shadow-soft) !important;
        font-size: clamp(1.22rem, 1.75vw, 1.72rem) !important;
        font-weight: 800 !important;
        line-height: 1.06 !important;
    }

    .section-title::before {
        width: 0.34rem !important;
        border-radius: 999px !important;
        background: linear-gradient(180deg, #FF2800 0%, #790E09 100%) !important;
    }

    .section-title::after,
    .subsection-title::after,
    .card-title::after {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(115deg, rgba(255, 255, 255, 0.34), rgba(255, 255, 255, 0.00) 34%);
        pointer-events: none;
    }

    .subsection-title {
        display: flex !important;
        align-items: flex-start !important;
        gap: 0.78rem !important;
        margin: 1.15rem 0 0.75rem 0 !important;
        padding: 0.8rem 0.92rem !important;
        border-left: 0.28rem solid var(--ui-red) !important;
        border-radius: 0.95rem !important;
        background: linear-gradient(90deg, rgba(255, 40, 0, 0.06) 0%, rgba(255, 255, 255, 0.98) 62%, #FFFFFF 100%) !important;
        box-shadow: 0 0.6rem 1.4rem rgba(121, 14, 9, 0.07) !important;
        font-size: clamp(1.02rem, 1.35vw, 1.2rem) !important;
        font-weight: 800 !important;
    }

    .card-title {
        display: flex !important;
        align-items: flex-start !important;
        justify-content: flex-start !important;
        gap: 0.7rem !important;
        margin: 0 0 0.82rem 0 !important;
        padding: 0.84rem 0.92rem !important;
        border-radius: 0.95rem !important;
        background: var(--ui-panel-bg) !important;
        box-shadow: 0 0.5rem 1.2rem rgba(121, 14, 9, 0.06) !important;
        text-align: left !important;
        font-size: 0.98rem !important;
        font-weight: 800 !important;
        min-height: 3.4rem !important;
    }

    .section-title .title-copy,
    .subsection-title .title-copy,
    .card-title .title-copy {
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        align-items: flex-start !important;
        min-width: 0 !important;
        flex: 1 1 auto !important;
    }

    .section-title .title-main,
    .subsection-title .title-main,
    .card-title .title-main {
        display: block !important;
        width: 100% !important;
        min-width: 0 !important;
        color: inherit !important;
    }

    .section-title .title-main {
        line-height: 1.06 !important;
    }

    .subsection-title .title-main,
    .card-title .title-main {
        line-height: 1.14 !important;
    }

    .card-title.has-subtitle {
        min-height: 4.15rem !important;
    }

    .subsection-title.has-subtitle {
        min-height: 3.9rem !important;
    }

    .section-title > span:first-child,
    .subsection-title > span:first-child,
    .card-title > span:first-child,
    .section-icon {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        width: 2.35rem !important;
        height: 2.35rem !important;
        flex: 0 0 2.35rem !important;
        border-radius: 0.9rem !important;
        background: linear-gradient(180deg, rgba(255, 40, 0, 0.13) 0%, rgba(121, 14, 9, 0.06) 100%) !important;
        border: 1px solid rgba(121, 14, 9, 0.12) !important;
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.92),
            0 0.35rem 0.95rem rgba(121, 14, 9, 0.09) !important;
        font-size: 1.05rem !important;
    }

    .subsection-title > span:first-child {
        width: 2.05rem !important;
        height: 2.05rem !important;
        flex-basis: 2.05rem !important;
        border-radius: 0.78rem !important;
        font-size: 0.94rem !important;
    }

    .card-title > span:first-child {
        width: 1.9rem !important;
        height: 1.9rem !important;
        flex-basis: 1.9rem !important;
        border-radius: 0.72rem !important;
    }

    .section-title .title-subtitle {
        width: 100%;
        margin: 0.2rem 0 0 0;
        font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        font-size: 0.72rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.12em !important;
        text-transform: uppercase !important;
        color: var(--ui-muted) !important;
        line-height: 1.35 !important;
    }

    .subsection-title .title-subtitle,
    .card-title .title-subtitle {
        font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        color: var(--ui-muted) !important;
        margin-top: 0.18rem !important;
        font-size: 0.73rem !important;
        font-weight: 700 !important;
        line-height: 1.28 !important;
        letter-spacing: 0.04em !important;
        text-transform: uppercase !important;
    }

    .filter-title {
        display: flex !important;
        align-items: center !important;
        gap: 0.62rem !important;
        margin: 0 0 0.92rem 0 !important;
        padding-bottom: 0.6rem !important;
        border-bottom: 1px solid rgba(121, 14, 9, 0.14) !important;
        font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif !important;
        font-size: 0.92rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.01em !important;
        color: var(--ui-ink) !important;
        position: relative;
    }

    .filter-title::after {
        content: "";
        position: absolute;
        left: 0;
        bottom: -1px;
        width: 4.1rem;
        height: 2px;
        border-radius: 999px;
        background: linear-gradient(90deg, #FF2800 0%, #790E09 100%);
    }

    .filter-container,
    .info-box,
    .analitico-corte-info,
    [data-testid="stExpander"] {
        border-radius: 1rem !important;
        border: 1px solid var(--ui-border) !important;
        background: var(--ui-panel-bg) !important;
        box-shadow: var(--ui-shadow-card) !important;
    }

    .filter-label-standard,
    [data-testid="stWidgetLabel"] p {
        font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        font-size: 0.72rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.1em !important;
        text-transform: uppercase !important;
        color: var(--ui-red-deep) !important;
        line-height: 1.2 !important;
        margin: 0 0 0.42rem 0 !important;
    }

    [data-testid="stWidgetLabel"] {
        margin-bottom: 0.42rem !important;
    }

    [data-testid="stSelectbox"],
    [data-testid="stMultiSelect"] {
        margin: 0 0 0.78rem 0 !important;
    }

    [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
        min-height: 2.95rem !important;
        border: 1px solid var(--ui-border-strong) !important;
        border-radius: 0.95rem !important;
        padding: 0.42rem 0.82rem !important;
        background: linear-gradient(180deg, #FFFFFF 0%, #FCF8F8 100%) !important;
        box-shadow: 0 0.35rem 0.95rem rgba(121, 14, 9, 0.08) !important;
        transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.18s ease !important;
    }

    [data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover,
    [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover,
    [data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within > div,
    [data-testid="stMultiSelect"] div[data-baseweb="select"]:focus-within > div {
        border-color: var(--ui-red) !important;
        box-shadow: var(--ui-shadow-focus), 0 0.65rem 1.3rem rgba(121, 14, 9, 0.10) !important;
        transform: translateY(-1px);
    }

    [data-testid="stSelectbox"] div[data-baseweb="select"] > div *,
    [data-testid="stMultiSelect"] div[data-baseweb="select"] > div *,
    [data-testid="stSelectbox"] div[data-baseweb="select"] span,
    [data-testid="stMultiSelect"] div[data-baseweb="select"] span {
        color: var(--ui-ink) !important;
        -webkit-text-fill-color: var(--ui-ink) !important;
        fill: #6A5755 !important;
        font-weight: 700 !important;
    }

    [data-testid="stSelectbox"] div[data-baseweb="select"] input::placeholder,
    [data-testid="stMultiSelect"] div[data-baseweb="select"] input::placeholder {
        color: rgba(122, 107, 105, 0.82) !important;
        -webkit-text-fill-color: rgba(122, 107, 105, 0.82) !important;
    }

    div[data-baseweb="popover"] ul[role="listbox"] {
        border-radius: 0.95rem !important;
        border: 1px solid rgba(121, 14, 9, 0.18) !important;
        background: linear-gradient(180deg, #FFFFFF 0%, #FCFBFB 100%) !important;
        box-shadow: 0 1rem 2rem rgba(121, 14, 9, 0.14) !important;
        padding: 0.35rem !important;
    }

    div[data-baseweb="popover"] li[role="option"] {
        border-radius: 0.7rem !important;
        font-weight: 700 !important;
        color: var(--ui-ink) !important;
        background: transparent !important;
        transition: background-color 0.16s ease, color 0.16s ease, transform 0.16s ease;
    }

    div[data-baseweb="popover"] li[role="option"]:hover {
        background: rgba(255, 40, 0, 0.08) !important;
        color: var(--ui-red-deep) !important;
        transform: translateX(1px);
    }

    div[data-baseweb="popover"] li[role="option"][aria-selected="true"] {
        background: rgba(121, 14, 9, 0.10) !important;
        color: var(--ui-red-dark) !important;
    }

    [data-testid="stMultiSelect"] span[data-baseweb="tag"] {
        border: 1px solid rgba(121, 14, 9, 0.14) !important;
        border-radius: 999px !important;
        background: linear-gradient(135deg, rgba(255, 40, 0, 0.12), rgba(121, 14, 9, 0.16)) !important;
        box-shadow: 0 0.15rem 0.45rem rgba(121, 14, 9, 0.08) !important;
        padding: 0.12rem 0.28rem 0.12rem 0.58rem !important;
    }

    [data-testid="stMultiSelect"] span[data-baseweb="tag"] *,
    [data-testid="stMultiSelect"] span[data-baseweb="tag"] svg {
        color: var(--ui-red-deep) !important;
        fill: var(--ui-red-deep) !important;
        -webkit-text-fill-color: var(--ui-red-deep) !important;
        font-weight: 800 !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        position: relative;
        overflow: hidden;
        background: linear-gradient(180deg, #FFFFFF 0%, #F7F5F5 100%) !important;
        border: 1px solid var(--ui-border) !important;
        border-radius: 1.12rem !important;
        box-shadow: var(--ui-shadow-card) !important;
        padding: 0.42rem !important;
        gap: 0.45rem !important;
        margin: 0.38rem 0 1.1rem 0 !important;
    }

    .stTabs [data-baseweb="tab-list"]::before {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(120deg, rgba(255, 255, 255, 0.42) 0%, rgba(255, 255, 255, 0.00) 36%);
        pointer-events: none;
    }

    .stTabs [data-baseweb="tab"] {
        position: relative;
        overflow: hidden;
        min-height: 2.58rem !important;
        padding: 0.54rem 1rem !important;
        border-radius: 0.88rem !important;
        border: 1px solid transparent !important;
        background: transparent !important;
        color: #5B4C4A !important;
        font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        font-size: 0.83rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.02em !important;
        transition: all 0.22s ease !important;
        box-shadow: none !important;
    }

    .stTabs [data-baseweb="tab"]::before {
        content: "";
        position: absolute;
        left: 10px;
        right: 10px;
        top: 7px;
        height: 38%;
        border-radius: 10px;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.58), rgba(255, 255, 255, 0));
        opacity: 0;
        transition: opacity 0.22s ease;
        pointer-events: none;
    }

    .stTabs [data-baseweb="tab"]::after {
        content: "";
        position: absolute;
        left: 14px;
        right: 14px;
        bottom: 5px;
        height: 2px;
        background: transparent;
        border-radius: 2px;
        transition: all 0.22s ease;
    }

    .stTabs [data-baseweb="tab"]:hover {
        background: #FFFFFF !important;
        color: #231F20 !important;
        border-color: rgba(121, 14, 9, 0.10) !important;
        box-shadow: 0 0.45rem 1rem rgba(121, 14, 9, 0.08) !important;
        transform: translateY(-1px);
    }

    .stTabs [data-baseweb="tab"]:hover::before {
        opacity: 1;
    }

    .stTabs [data-baseweb="tab"]:focus-visible {
        outline: none !important;
        box-shadow: var(--ui-shadow-focus) !important;
    }

    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #B2463F 0%, #8D1A12 55%, #790E09 100%) !important;
        color: #FFFFFF !important;
        border-color: #790E09 !important;
        box-shadow:
            0 0.8rem 1.55rem rgba(121, 14, 9, 0.24),
            inset 0 1px 0 rgba(255, 255, 255, 0.20) !important;
        transform: translateY(-1px);
    }

    .stTabs [aria-selected="true"]::before {
        opacity: 1;
    }

    .stTabs [aria-selected="true"]::after {
        background: rgba(255, 255, 255, 0.94);
    }

    .stPlotlyChart,
    div[data-testid="stPlotlyChart"] {
        border-radius: 1.08rem !important;
        border: 1px solid rgba(121, 14, 9, 0.14) !important;
        border-top: 3px solid #790E09 !important;
        background: #FFFFFF !important;
        box-shadow: 0 0.55rem 1.2rem rgba(121, 14, 9, 0.06) !important;
        padding: 0.28rem 0.34rem 0.12rem 0.34rem !important;
        overflow: visible !important;
    }

    [data-testid="stPlotlyChart"] .js-plotly-plot .plotly,
    [data-testid="stPlotlyChart"] .plot-container {
        background: transparent !important;
    }

    [data-testid="stDataFrame"] {
        border-radius: 1.08rem !important;
        border: 1px solid var(--ui-border) !important;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(255, 248, 247, 0.94) 100%) !important;
        box-shadow: var(--ui-shadow-card) !important;
        padding: 0.18rem !important;
    }

    [class^="tabela-container-"],
    [class*=" tabela-container-"],
    .tabela-container-melhorada {
        border-radius: 1.12rem !important;
        border: 1px solid var(--ui-border) !important;
        background: linear-gradient(180deg, #FFFFFF 0%, #FFF9F8 100%) !important;
        box-shadow: var(--ui-shadow-card) !important;
    }

    .js-plotly-plot .plotly .legend text {
        font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        font-weight: 800 !important;
        fill: #5A4543 !important;
    }

    .js-plotly-plot .plotly .legendtitletext {
        font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif !important;
        font-weight: 800 !important;
        fill: #790E09 !important;
    }

        .js-plotly-plot .plotly .legend rect.bg {
            fill: rgba(255, 255, 255, 0.92) !important;
            stroke: rgba(121, 14, 9, 0.12) !important;
            stroke-width: 1 !important;
        }

        .js-plotly-plot .modebar {
            opacity: 0 !important;
            pointer-events: none !important;
            transition: opacity 0.16s ease !important;
        }

        .js-plotly-plot:hover .modebar {
            opacity: 1 !important;
            pointer-events: auto !important;
        }

        .js-plotly-plot .plotly .main-svg .gtitle {
            font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif !important;
            font-weight: 800 !important;
            fill: #3E302E !important;
    }

        .evo-monthly-panel {
            border-radius: 0.92rem !important;
            border: 1px solid var(--ui-border) !important;
            background: linear-gradient(180deg, #FFFFFF 0%, #FFF8F7 100%) !important;
            box-shadow: var(--ui-shadow-soft) !important;
        }

    .evo-monthly-summary {
        border-radius: 0.95rem !important;
        border: 1px solid rgba(255, 255, 255, 0) !important;
        background: #FFFFFF !important;
        box-shadow: 0 0.45rem 0.95rem rgba(121, 14, 9, 0.05) !important;
    }

        .evo-monthly-legend-card {
            border-radius: 0.82rem !important;
            border: 1px solid rgba(255, 255, 255, 0) !important;
            background: #FFFFFF !important;
            box-shadow: 0 0.38rem 0.95rem rgba(121, 14, 9, 0.05) !important;
        }

    .evo-monthly-legend-title {
        font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        letter-spacing: 0.12em !important;
        color: #7A1E19 !important;
    }

    .evo-monthly-legend-text {
        font-weight: 800 !important;
    }

    .evo-monthly-legend-note {
        font-size: 0.62rem !important;
        line-height: 1.4 !important;
    }

    [data-testid="stSidebar"] h3 {
        font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif !important;
        font-size: 0.88rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.08em !important;
        text-transform: uppercase !important;
        color: var(--ui-red-deep) !important;
    }

    [data-testid="stSidebar"] hr {
        margin: 0.75rem 0 !important;
    }

        @media (max-width: 768px) {
        .evo-monthly-context {
            flex-wrap: wrap !important;
        }

        .evo-monthly-context-chip {
            max-width: 100% !important;
            width: 100% !important;
        }

        .dashboard-hero-divider {
            margin: 0.35rem auto 1rem auto;
        }

        .section-title {
            padding: 0.86rem 0.94rem 0.82rem 0.92rem !important;
            margin: 1.35rem 0 0.88rem 0 !important;
            gap: 0.72rem !important;
            font-size: 1.08rem !important;
        }

        .section-title .title-subtitle {
            font-size: 0.66rem !important;
        }

        .subsection-title {
            padding: 0.72rem 0.84rem !important;
            font-size: 0.94rem !important;
        }

        .card-title {
            padding: 0.76rem 0.82rem !important;
            font-size: 0.9rem !important;
            min-height: 3.2rem !important;
        }

        .card-title.has-subtitle {
            min-height: 3.95rem !important;
        }

        .evo-monthly-legends {
            grid-template-columns: 1fr !important;
        }

        .section-title > span:first-child,
        .section-icon {
            width: 2.06rem !important;
            height: 2.06rem !important;
            flex-basis: 2.06rem !important;
        }

        .subsection-title > span:first-child {
            width: 1.85rem !important;
            height: 1.85rem !important;
            flex-basis: 1.85rem !important;
        }

        .stTabs [data-baseweb="tab"] {
            min-height: 2.38rem !important;
            padding: 0.48rem 0.82rem !important;
            font-size: 0.76rem !important;
        }

        [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
            min-height: 2.72rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <style>
    .dashboard-hero-divider-line {
        height: 0.16rem !important;
        background: linear-gradient(
            90deg,
            rgba(255, 40, 0, 0.00) 0%,
            rgba(255, 40, 0, 0.28) 14%,
            rgba(255, 40, 0, 0.98) 50%,
            rgba(121, 14, 9, 0.30) 86%,
            rgba(121, 14, 9, 0.00) 100%
        ) !important;
        box-shadow: 0 0.45rem 1.15rem rgba(121, 14, 9, 0.18) !important;
    }

    .dashboard-hero-divider-badge {
        border-color: rgba(121, 14, 9, 0.18) !important;
        background:
            radial-gradient(circle at 30% 20%, rgba(255, 40, 0, 0.10), rgba(255, 40, 0, 0.00) 52%),
            linear-gradient(180deg, #FFFFFF 0%, #FFF4F2 100%) !important;
        box-shadow:
            0 0.55rem 1.2rem rgba(121, 14, 9, 0.14),
            inset 0 1px 0 rgba(255, 255, 255, 0.96) !important;
    }

    .dashboard-hero-divider-badge i {
        background: linear-gradient(135deg, #FF2800 0%, #790E09 100%) !important;
        box-shadow: 0 0 0 0.18rem rgba(255, 40, 0, 0.10) !important;
    }

    .section-title {
        border-color: rgba(121, 14, 9, 0.16) !important;
        background:
            linear-gradient(90deg, rgba(255, 40, 0, 0.18) 0%, rgba(255, 40, 0, 0.07) 18%, rgba(255, 255, 255, 0.98) 58%, #FFFFFF 100%) !important;
        box-shadow:
            0 1rem 2.3rem rgba(121, 14, 9, 0.12),
            inset 0 1px 0 rgba(255, 255, 255, 0.92) !important;
        color: #2E2322 !important;
    }

    .subsection-title {
        border-color: rgba(121, 14, 9, 0.14) !important;
        background:
            linear-gradient(90deg, rgba(255, 40, 0, 0.14) 0%, rgba(255, 40, 0, 0.05) 18%, rgba(255, 255, 255, 0.98) 64%, #FFFFFF 100%) !important;
        box-shadow:
            0 0.75rem 1.75rem rgba(121, 14, 9, 0.10),
            inset 0 1px 0 rgba(255, 255, 255, 0.92) !important;
    }

    .card-title {
        border-color: rgba(121, 14, 9, 0.14) !important;
        background:
            radial-gradient(circle at 0% 0%, rgba(255, 40, 0, 0.08) 0%, rgba(255, 40, 0, 0.00) 32%),
            linear-gradient(180deg, #FFFFFF 0%, #FFF9F8 100%) !important;
        box-shadow: 0 0.7rem 1.5rem rgba(121, 14, 9, 0.09) !important;
    }

    .section-title > span:first-child,
    .subsection-title > span:first-child,
    .card-title > span:first-child,
    .section-icon {
        background:
            radial-gradient(circle at 30% 25%, rgba(255,255,255,0.42), rgba(255,255,255,0.00) 46%),
            linear-gradient(135deg, rgba(255, 40, 0, 0.22) 0%, rgba(121, 14, 9, 0.12) 100%) !important;
        border-color: rgba(121, 14, 9, 0.16) !important;
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.96),
            0 0.45rem 1.05rem rgba(121, 14, 9, 0.12) !important;
    }

    .section-title .title-subtitle {
        color: #7A241F !important;
    }

    .subsection-title,
    .card-title,
    .filter-title {
        color: #3A2A29 !important;
    }

    .filter-title {
        border-bottom-color: rgba(121, 14, 9, 0.18) !important;
    }

    .filter-title::after {
        width: 4.6rem !important;
        height: 2.5px !important;
        background: linear-gradient(90deg, #FF2800 0%, #790E09 70%, #5A0A06 100%) !important;
        box-shadow: 0 0.22rem 0.5rem rgba(121, 14, 9, 0.18);
    }

    .filter-container,
    .info-box,
    .analitico-corte-info,
    [data-testid="stExpander"] {
        border-color: rgba(121, 14, 9, 0.16) !important;
        background:
            radial-gradient(circle at 0% 0%, rgba(255, 40, 0, 0.06) 0%, rgba(255, 40, 0, 0.00) 28%),
            linear-gradient(180deg, #FFFFFF 0%, #FFF9F8 100%) !important;
        box-shadow: 0 0.8rem 1.8rem rgba(121, 14, 9, 0.10) !important;
    }

    [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
        border-color: rgba(121, 14, 9, 0.24) !important;
        background:
            radial-gradient(circle at 12% 0%, rgba(255, 40, 0, 0.08) 0%, rgba(255, 40, 0, 0.00) 32%),
            linear-gradient(180deg, #FFFFFF 0%, #FFF8F7 100%) !important;
        box-shadow: 0 0.42rem 1.05rem rgba(121, 14, 9, 0.10) !important;
    }

    [data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover,
    [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover,
    [data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within > div,
    [data-testid="stMultiSelect"] div[data-baseweb="select"]:focus-within > div {
        border-color: #FF2800 !important;
        box-shadow:
            0 0 0 0.18rem rgba(255, 40, 0, 0.12),
            0 0.75rem 1.45rem rgba(121, 14, 9, 0.14) !important;
    }

    [data-testid="stMultiSelect"] span[data-baseweb="tag"] {
        border-color: rgba(121, 14, 9, 0.18) !important;
        background: linear-gradient(135deg, #FFEBE7 0%, #FFD9D2 100%) !important;
        box-shadow: 0 0.2rem 0.55rem rgba(121, 14, 9, 0.12) !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        border-color: rgba(121, 14, 9, 0.16) !important;
        background:
            radial-gradient(circle at 0% 0%, rgba(255, 40, 0, 0.08) 0%, rgba(255, 40, 0, 0.00) 30%),
            linear-gradient(180deg, #FFFFFF 0%, #F8F5F5 100%) !important;
        box-shadow: 0 0.85rem 1.9rem rgba(121, 14, 9, 0.10) !important;
    }

    .stTabs [data-baseweb="tab"] {
        color: #6D312C !important;
    }

    .stTabs [data-baseweb="tab"]:hover {
        color: #5A0A06 !important;
        border-color: rgba(121, 14, 9, 0.14) !important;
        box-shadow: 0 0.55rem 1.1rem rgba(121, 14, 9, 0.10) !important;
    }

    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #FF2800 0%, #A91C14 48%, #790E09 100%) !important;
        box-shadow:
            0 0.95rem 1.8rem rgba(121, 14, 9, 0.26),
            inset 0 1px 0 rgba(255, 255, 255, 0.24) !important;
    }

    .stPlotlyChart,
    div[data-testid="stPlotlyChart"] {
        border-color: rgba(121, 14, 9, 0.14) !important;
        border-top-color: #790E09 !important;
        border-top-width: 3px !important;
        background: #FFFFFF !important;
        box-shadow: 0 0.55rem 1.2rem rgba(121, 14, 9, 0.06) !important;
    }

    .evo-monthly-panel,
    .evo-monthly-summary,
    .evo-monthly-legend-card {
        border-color: rgba(121, 14, 9, 0.14) !important;
        background: #FFFFFF !important;
    }

    div[data-testid="stPopover"] > button {
        border-color: rgba(121, 14, 9, 0.18) !important;
        background: linear-gradient(135deg, #FF2800 0%, #A51A13 52%, #790E09 100%) !important;
        color: #FFFFFF !important;
        box-shadow: 0 0.7rem 1.45rem rgba(121, 14, 9, 0.20) !important;
    }

    div[data-testid="stPopover"] > button p {
        color: #FFFFFF !important;
    }

    div[data-testid="stPopover"] > button:hover {
        border-color: rgba(255, 255, 255, 0.22) !important;
        box-shadow:
            0 0 0 0.16rem rgba(255, 40, 0, 0.10),
            0 0.95rem 1.7rem rgba(121, 14, 9, 0.26) !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <style>
    .dashboard-hero-divider-line {
        height: 0.15rem !important;
        background: linear-gradient(
            90deg,
            rgba(255, 40, 0, 0.00) 0%,
            rgba(121, 14, 9, 0.20) 18%,
            rgba(121, 14, 9, 0.95) 50%,
            rgba(90, 10, 6, 0.24) 82%,
            rgba(90, 10, 6, 0.00) 100%
        ) !important;
        box-shadow: 0 0.42rem 1rem rgba(121, 14, 9, 0.16) !important;
    }

    .dashboard-hero-divider-badge {
        border: 1px solid rgba(121, 14, 9, 0.22) !important;
        background: linear-gradient(180deg, #FFFFFF 0%, #FCFBFB 100%) !important;
        box-shadow:
            0 0.55rem 1.15rem rgba(121, 14, 9, 0.12),
            inset 0 1px 0 rgba(255, 255, 255, 0.98) !important;
    }

    .dashboard-hero-divider-badge i {
        background: #790E09 !important;
        box-shadow: 0 0 0 0.14rem rgba(121, 14, 9, 0.08) !important;
    }

    .section-title,
    .subsection-title,
    .card-title {
        background: linear-gradient(180deg, #FFFFFF 0%, #FCFBFB 100%) !important;
        border-color: rgba(121, 14, 9, 0.18) !important;
        box-shadow:
            0 0.85rem 1.85rem rgba(121, 14, 9, 0.09),
            inset 0 1px 0 rgba(255, 255, 255, 0.98) !important;
    }

    .section-title {
        border-left: 0.36rem solid #790E09 !important;
    }

    .subsection-title {
        border-left: 0.3rem solid #790E09 !important;
    }

    .section-title::before,
    .subsection-title::before {
        background: linear-gradient(180deg, #790E09 0%, #5A0A06 100%) !important;
    }

    .section-title > span:first-child,
    .subsection-title > span:first-child,
    .card-title > span:first-child,
    .section-icon {
        background: linear-gradient(180deg, #FFFFFF 0%, #FBF8F8 100%) !important;
        border: 1px solid rgba(121, 14, 9, 0.20) !important;
        box-shadow:
            0 0.4rem 0.95rem rgba(121, 14, 9, 0.10),
            inset 0 1px 0 rgba(255, 255, 255, 0.98) !important;
        color: #790E09 !important;
        -webkit-text-fill-color: #790E09 !important;
        background-clip: border-box !important;
        -webkit-background-clip: border-box !important;
    }

    .section-title > span:first-child svg,
    .subsection-title > span:first-child svg,
    .card-title > span:first-child svg,
    .section-icon svg {
        width: 1.05rem !important;
        height: 1.05rem !important;
        display: block !important;
        stroke: currentColor !important;
        overflow: visible !important;
    }

    .subsection-title > span:first-child svg {
        width: 0.92rem !important;
        height: 0.92rem !important;
    }

    .section-title .title-subtitle,
    .subsection-title .title-subtitle,
    .card-title .title-subtitle {
        color: #7A1E19 !important;
    }

    .filter-title {
        color: #3A2624 !important;
        border-bottom-color: rgba(121, 14, 9, 0.18) !important;
    }

    .filter-title::after {
        width: 4.45rem !important;
        background: linear-gradient(90deg, #790E09 0%, #5A0A06 100%) !important;
        box-shadow: 0 0.18rem 0.45rem rgba(121, 14, 9, 0.18) !important;
    }

    .filter-container,
    .info-box,
    .analitico-corte-info,
    [data-testid="stExpander"] {
        background: linear-gradient(180deg, #FFFFFF 0%, #FCFBFB 100%) !important;
        border-color: rgba(121, 14, 9, 0.18) !important;
        box-shadow: 0 0.75rem 1.55rem rgba(121, 14, 9, 0.08) !important;
    }

    [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
        background: linear-gradient(180deg, #FFFFFF 0%, #FCFBFB 100%) !important;
        border: 1px solid rgba(121, 14, 9, 0.24) !important;
        box-shadow: 0 0.38rem 0.95rem rgba(121, 14, 9, 0.08) !important;
    }

    [data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover,
    [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover,
    [data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within > div,
    [data-testid="stMultiSelect"] div[data-baseweb="select"]:focus-within > div {
        border-color: #790E09 !important;
        box-shadow:
            0 0 0 0.16rem rgba(121, 14, 9, 0.10),
            0 0.7rem 1.35rem rgba(121, 14, 9, 0.12) !important;
    }

    [data-testid="stMultiSelect"] span[data-baseweb="tag"] {
        background: linear-gradient(180deg, #FFFFFF 0%, #FBF8F8 100%) !important;
        border: 1px solid rgba(121, 14, 9, 0.20) !important;
        box-shadow: 0 0.2rem 0.48rem rgba(121, 14, 9, 0.08) !important;
    }

    [data-testid="stMultiSelect"] span[data-baseweb="tag"] *,
    [data-testid="stMultiSelect"] span[data-baseweb="tag"] svg {
        color: #790E09 !important;
        fill: #790E09 !important;
        -webkit-text-fill-color: #790E09 !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        background: linear-gradient(180deg, #FFFFFF 0%, #FAF8F8 100%) !important;
        border: 1px solid rgba(121, 14, 9, 0.18) !important;
        box-shadow: 0 0.8rem 1.7rem rgba(121, 14, 9, 0.10) !important;
    }

    .stTabs [data-baseweb="tab"] {
        background: linear-gradient(180deg, #FFFFFF 0%, #FCFBFB 100%) !important;
        border: 1px solid rgba(121, 14, 9, 0.12) !important;
        color: #6B2A25 !important;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.98) !important;
    }

    .stTabs [data-baseweb="tab"]:hover {
        background: linear-gradient(180deg, #FFFFFF 0%, #F8F4F4 100%) !important;
        border-color: rgba(121, 14, 9, 0.18) !important;
        color: #5A0A06 !important;
        box-shadow: 0 0.55rem 1rem rgba(121, 14, 9, 0.10) !important;
    }

    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #790E09 0%, #66110C 52%, #5A0A06 100%) !important;
        border-color: #5A0A06 !important;
        box-shadow:
            0 0.95rem 1.8rem rgba(121, 14, 9, 0.24),
            inset 0 1px 0 rgba(255, 255, 255, 0.18) !important;
    }

    .stPlotlyChart,
    div[data-testid="stPlotlyChart"] {
        background: #FFFFFF !important;
        border: 1px solid rgba(121, 14, 9, 0.14) !important;
        border-top: 3px solid #790E09 !important;
        box-shadow: 0 0.48rem 1.05rem rgba(121, 14, 9, 0.05) !important;
    }

    .evo-monthly-panel,
    .evo-monthly-summary,
    .evo-monthly-legend-card {
        background: #FFFFFF !important;
        border-color: rgba(121, 14, 9, 0.14) !important;
        box-shadow: 0 0.45rem 0.95rem rgba(121, 14, 9, 0.05) !important;
    }

    .evo-monthly-legend-title {
        color: #790E09 !important;
    }

    div[data-testid="stPopover"] > button {
        background: linear-gradient(180deg, #FFFFFF 0%, #FCFBFB 100%) !important;
        border: 1px solid rgba(121, 14, 9, 0.22) !important;
        color: #790E09 !important;
        box-shadow:
            0 0.6rem 1.15rem rgba(121, 14, 9, 0.12),
            inset 0 1px 0 rgba(255, 255, 255, 0.98) !important;
    }

    div[data-testid="stPopover"] > button p {
        color: #790E09 !important;
    }

    div[data-testid="stPopover"] > button:hover {
        border-color: rgba(121, 14, 9, 0.28) !important;
        box-shadow:
            0 0 0 0.14rem rgba(121, 14, 9, 0.08),
            0 0.8rem 1.45rem rgba(121, 14, 9, 0.16) !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <style>
    .stTabs [data-baseweb="tab-list"] {
        display: flex !important;
        flex-wrap: nowrap !important;
        align-items: stretch !important;
        gap: 0.34rem !important;
        padding: 0.42rem !important;
        margin: 0.34rem 0 1.2rem 0 !important;
        border-radius: 1.12rem !important;
        border: 1px solid rgba(121, 14, 9, 0.18) !important;
        background:
            radial-gradient(circle at top, rgba(255, 255, 255, 0.80), rgba(255, 255, 255, 0.00) 58%),
            linear-gradient(180deg, #FFFFFF 0%, #F7F4F4 100%) !important;
        box-shadow:
            0 1rem 2rem rgba(121, 14, 9, 0.10),
            inset 0 1px 0 rgba(255, 255, 255, 0.98) !important;
        overflow-x: auto !important;
        overflow-y: hidden !important;
        scrollbar-width: thin !important;
        scrollbar-color: rgba(121, 14, 9, 0.30) transparent !important;
    }

    .stTabs [data-baseweb="tab"] {
        position: relative !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 0.46rem !important;
        flex: 1 1 0 !important;
        min-width: 0 !important;
        min-height: 2.86rem !important;
        padding: 0.58rem 0.66rem !important;
        border-radius: 0.9rem !important;
        border: 1px solid rgba(121, 14, 9, 0.16) !important;
        background: linear-gradient(180deg, #FFFFFF 0%, #FBF7F7 100%) !important;
        box-shadow:
            0 0.48rem 1rem rgba(121, 14, 9, 0.08),
            inset 0 1px 0 rgba(255, 255, 255, 0.98) !important;
        font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif !important;
        font-size: 0.67rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.04em !important;
        text-transform: uppercase !important;
        line-height: 1.08 !important;
        transition:
            transform 0.22s ease,
            box-shadow 0.22s ease,
            border-color 0.22s ease,
            background 0.22s ease !important;
        overflow: hidden !important;
    }

    .stTabs [data-baseweb="tab"]::before {
        content: none !important;
        display: none !important;
    }

    .stTabs [data-baseweb="tab"] .tab-icon-badge {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        width: 1.34rem !important;
        height: 1.34rem !important;
        flex: 0 0 1.34rem !important;
        border-radius: 999px !important;
        border: 1px solid rgba(121, 14, 9, 0.24) !important;
        background: linear-gradient(180deg, #FFFFFF 0%, #FBF8F8 100%) !important;
        box-shadow:
            0 0.32rem 0.72rem rgba(121, 14, 9, 0.10),
            inset 0 1px 0 rgba(255, 255, 255, 0.98) !important;
        color: #6A201B !important;
        -webkit-text-fill-color: #6A201B !important;
        transform: translateY(-0.5px) !important;
        transition:
            transform 0.22s ease,
            box-shadow 0.22s ease,
            background 0.22s ease,
            border-color 0.22s ease !important;
    }

    .stTabs [data-baseweb="tab"] .tab-icon-badge svg {
        width: 0.72rem !important;
        height: 0.72rem !important;
        display: block !important;
        stroke: currentColor !important;
        color: currentColor !important;
        fill: none !important;
    }

    .stTabs [data-baseweb="tab"] .tab-label-text {
        display: block !important;
        flex: 1 1 auto !important;
        min-width: 0 !important;
        text-align: center !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        line-height: 1.08 !important;
        font-size: inherit !important;
    }

    .stTabs [data-baseweb="tab"]::after {
        content: "";
        position: absolute !important;
        left: 1rem !important;
        right: 1rem !important;
        bottom: 0.42rem !important;
        height: 2px !important;
        border-radius: 999px !important;
        background: linear-gradient(
            90deg,
            rgba(121, 14, 9, 0.00) 0%,
            rgba(121, 14, 9, 0.48) 50%,
            rgba(121, 14, 9, 0.00) 100%
        ) !important;
        opacity: 0.52 !important;
        transform: scaleX(0.62) !important;
        transition: all 0.22s ease !important;
    }

    .stTabs [data-baseweb="tab"],
    .stTabs [data-baseweb="tab"] *,
    .stTabs [data-baseweb="tab"] div,
    .stTabs [data-baseweb="tab"] p,
    .stTabs [data-baseweb="tab"] span {
        color: #6A201B !important;
        fill: #6A201B !important;
        -webkit-text-fill-color: #6A201B !important;
    }

    .stTabs [data-baseweb="tab"] p {
        margin: 0 !important;
        text-align: center !important;
        line-height: 1.1 !important;
    }

    .stTabs [data-baseweb="tab"] .tab-icon-badge,
    .stTabs [data-baseweb="tab"] .tab-icon-badge svg {
        color: #6A201B !important;
        fill: none !important;
        -webkit-text-fill-color: #6A201B !important;
    }

    .stTabs [data-baseweb="tab"]:hover {
        background: linear-gradient(180deg, #FFFFFF 0%, #F8F1F1 100%) !important;
        border-color: rgba(121, 14, 9, 0.28) !important;
        box-shadow:
            0 0.86rem 1.45rem rgba(121, 14, 9, 0.14),
            inset 0 1px 0 rgba(255, 255, 255, 0.98) !important;
        transform: translateY(-2px) !important;
    }

    .stTabs [data-baseweb="tab"]:hover .tab-icon-badge {
        background: linear-gradient(180deg, #FFFFFF 0%, #F8F1F1 100%) !important;
        border-color: rgba(121, 14, 9, 0.30) !important;
        box-shadow:
            0 0.42rem 0.9rem rgba(121, 14, 9, 0.14),
            inset 0 1px 0 rgba(255, 255, 255, 0.98) !important;
        transform: translateY(-1px) !important;
    }

    .stTabs [data-baseweb="tab"]:hover::after {
        opacity: 1 !important;
        transform: scaleX(1) !important;
        background: linear-gradient(
            90deg,
            rgba(255, 40, 0, 0.00) 0%,
            rgba(121, 14, 9, 0.78) 50%,
            rgba(255, 40, 0, 0.00) 100%
        ) !important;
    }

    .stTabs [data-baseweb="tab"]:focus-visible {
        outline: none !important;
        box-shadow:
            0 0 0 0.18rem rgba(121, 14, 9, 0.12),
            0 0.86rem 1.45rem rgba(121, 14, 9, 0.14) !important;
    }

    .stTabs [data-baseweb="tab"][aria-selected="true"] {
        background:
            radial-gradient(circle at top, rgba(255, 255, 255, 0.18), rgba(255, 255, 255, 0.00) 54%),
            linear-gradient(135deg, #790E09 0%, #66110C 48%, #5A0A06 100%) !important;
        border-color: #4A0704 !important;
        box-shadow:
            0 1.05rem 1.95rem rgba(121, 14, 9, 0.26),
            inset 0 1px 0 rgba(255, 255, 255, 0.16),
            inset 0 -1px 0 rgba(74, 7, 4, 0.36) !important;
        transform: translateY(-2px) !important;
    }

    .stTabs [data-baseweb="tab"][aria-selected="true"]::before {
        content: none !important;
        display: none !important;
    }

    .stTabs [data-baseweb="tab"][aria-selected="true"]::after {
        opacity: 1 !important;
        transform: scaleX(1) !important;
        background: linear-gradient(
            90deg,
            rgba(255, 255, 255, 0.00) 0%,
            rgba(255, 255, 255, 0.96) 50%,
            rgba(255, 255, 255, 0.00) 100%
        ) !important;
    }

    .stTabs [data-baseweb="tab"][aria-selected="true"],
    .stTabs [data-baseweb="tab"][aria-selected="true"] *,
    .stTabs [data-baseweb="tab"][aria-selected="true"] div,
    .stTabs [data-baseweb="tab"][aria-selected="true"] p,
    .stTabs [data-baseweb="tab"][aria-selected="true"] span {
        color: #FFFFFF !important;
        fill: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        text-shadow: 0 1px 0 rgba(74, 7, 4, 0.28) !important;
    }

    .stTabs [data-baseweb="tab"][aria-selected="true"] .tab-icon-badge {
        border-color: rgba(255, 255, 255, 0.22) !important;
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.20) 0%, rgba(255, 255, 255, 0.08) 100%) !important;
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.16),
            0 0.36rem 0.8rem rgba(74, 7, 4, 0.18) !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
    }

    div[data-testid="stTabs"] > div[role="tabpanel"] {
        padding-top: 0.1rem !important;
    }

    .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {
        height: 0.34rem !important;
    }

    .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar-track {
        background: transparent !important;
    }

    .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar-thumb {
        border-radius: 999px !important;
        background: linear-gradient(90deg, rgba(162, 59, 54, 0.85), rgba(121, 14, 9, 0.92)) !important;
    }

    @media (max-width: 860px) {
        .stTabs [data-baseweb="tab-list"] {
            gap: 0.28rem !important;
            padding: 0.34rem !important;
        }

        .stTabs [data-baseweb="tab"] {
            min-height: 2.66rem !important;
            padding: 0.5rem 0.52rem !important;
            font-size: 0.62rem !important;
            letter-spacing: 0.03em !important;
            gap: 0.34rem !important;
        }

        .stTabs [data-baseweb="tab"] .tab-icon-badge {
            width: 1.16rem !important;
            height: 1.16rem !important;
            flex-basis: 1.16rem !important;
        }

        .stTabs [data-baseweb="tab"] .tab-icon-badge svg {
            width: 0.62rem !important;
            height: 0.62rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <style>
    :root {
        --claro-red: #FF2800;
        --claro-red-deep: #790E09;
        --claro-red-dark: #5A0A06;
        --claro-shadow-soft: 0 0.75rem 2rem rgba(121, 14, 9, 0.08);
        --claro-shadow-medium: 0 1rem 2.4rem rgba(121, 14, 9, 0.12);
    }

    .kpi-card-dinamico,
    .kpi-block-dinamico,
    [data-testid="stMetric"] {
        border-radius: 1.1rem !important;
        border: 1px solid rgba(121, 14, 9, 0.10) !important;
        background:
            radial-gradient(circle at 0% 0%, rgba(255, 40, 0, 0.06) 0%, rgba(255, 40, 0, 0.00) 32%),
            linear-gradient(180deg, #FFFFFF 0%, #FBFBFC 100%) !important;
        box-shadow: var(--claro-shadow-soft) !important;
    }

    .kpi-card-dinamico::before,
    .kpi-block-dinamico::before {
        background: linear-gradient(90deg, #FF2800 0%, #790E09 100%) !important;
    }

    .kpi-title-dinamico {
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 0.65rem !important;
        font-size: 1rem !important;
    }

    .kpi-title-icon {
        width: 1.84rem !important;
        height: 1.84rem !important;
        border-radius: 0.68rem !important;
        background: linear-gradient(135deg, #FF2800 0%, #790E09 100%) !important;
        color: #FFFFFF !important;
    }

    .kpi-title-icon svg {
        width: 0.96rem !important;
        height: 0.96rem !important;
    }

    .kpi-block-label,
    .kpi-block-label span {
        display: inline-flex !important;
        align-items: center !important;
        gap: 0.38rem !important;
    }

    .kpi-block-icon {
        width: 1rem !important;
        height: 1rem !important;
        color: var(--claro-red-deep) !important;
    }

    .kpi-title-dinamico.is-primary .kpi-title-text {
        font-size: 1.04rem !important;
    }

    .kpi-card-dinamico:has(.kpi-title-dinamico.is-primary) {
        border-color: rgba(255, 40, 0, 0.20) !important;
        box-shadow: var(--claro-shadow-medium) !important;
        transform: translateY(-0.05rem);
    }

    .kpi-card-dinamico:has(.kpi-title-dinamico.is-primary) .kpi-value-wrap {
        border-color: rgba(255, 40, 0, 0.18) !important;
        box-shadow: 0 0.85rem 1.6rem rgba(121, 14, 9, 0.10) !important;
    }

    .kpi-card-dinamico:has(.kpi-title-dinamico.is-primary) .kpi-value-dinamico {
        font-size: 1.95rem !important;
    }

    @media (max-width: 768px) {
        .kpi-card-dinamico,
        .kpi-block-dinamico,
        [data-testid="stMetric"] {
            border-radius: 0.9rem !important;
            box-shadow: 0 0.35rem 0.85rem rgba(121, 14, 9, 0.05) !important;
            background: #FFFFFF !important;
        }

        .kpi-title-dinamico {
            gap: 0.45rem !important;
            font-size: 0.86rem !important;
        }

        .kpi-title-icon {
            width: 1.42rem !important;
            height: 1.42rem !important;
            border-radius: 0.5rem !important;
        }

        .kpi-title-icon svg {
            width: 0.76rem !important;
            height: 0.76rem !important;
        }

        .kpi-value-dinamico {
            font-size: 1.45rem !important;
            text-shadow: none !important;
        }

        .kpi-card-dinamico:has(.kpi-title-dinamico.is-primary) .kpi-value-dinamico {
            font-size: 1.6rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <style>
    .kpi-card-dinamico,
    .kpi-block-dinamico,
    [data-testid="stMetric"] {
        position: relative !important;
        overflow: hidden !important;
        border: 1px solid rgba(90, 10, 6, 0.14) !important;
        background:
            radial-gradient(circle at 12% 8%, rgba(255, 255, 255, 0.98) 0%, rgba(255, 255, 255, 0.00) 30%),
            linear-gradient(180deg, #FFFFFF 0%, #FCFCFD 62%, #F6F7F9 100%) !important;
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.98),
            inset 0 0 0 1px rgba(255, 255, 255, 0.48) !important;
        transition:
            transform 0.24s ease,
            box-shadow 0.24s ease,
            border-color 0.24s ease !important;
        backdrop-filter: none !important;
        outline: none !important;
    }

    .kpi-card-dinamico::before,
    .kpi-block-dinamico::before,
    [data-testid="stMetric"]::before {
        content: "" !important;
        position: absolute !important;
        top: 0 !important;
        left: 0.58rem !important;
        right: 0.58rem !important;
        height: 3px !important;
        border-radius: 999px !important;
        background: linear-gradient(
            90deg,
            rgba(90, 10, 6, 0.94) 0%,
            rgba(121, 14, 9, 0.98) 26%,
            rgba(255, 40, 0, 1) 52%,
            rgba(121, 14, 9, 0.98) 78%,
            rgba(90, 10, 6, 0.94) 100%
        ) !important;
        opacity: 1 !important;
        box-shadow:
            0 1px 0 rgba(255, 255, 255, 0.70),
            0 2px 6px rgba(121, 14, 9, 0.16) !important;
        pointer-events: none !important;
        z-index: 0 !important;
    }

    .kpi-card-dinamico::after,
    .kpi-block-dinamico::after,
    [data-testid="stMetric"]::after {
        content: none !important;
        display: none !important;
        background: none !important;
        pointer-events: none !important;
    }

    .kpi-card-dinamico:hover,
    .kpi-block-dinamico:hover,
    [data-testid="stMetric"]:hover {
        transform: translateY(-2px) !important;
        border-color: rgba(121, 14, 9, 0.22) !important;
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.98),
            inset 0 0 0 1px rgba(255, 255, 255, 0.48) !important;
    }

    .kpi-title-dinamico {
        position: relative !important;
        gap: 0.72rem !important;
        margin-top: 0.08rem !important;
        margin-bottom: 0.46rem !important;
        padding-top: 0.14rem !important;
        padding-bottom: 0.5rem !important;
        min-height: 2rem !important;
    }

    .kpi-title-text {
        letter-spacing: 0.085em !important;
        text-shadow: none !important;
    }

    .kpi-title-dinamico::after {
        bottom: 0.02rem !important;
        width: 34px !important;
        height: 2px !important;
        border-radius: 999px !important;
        background: linear-gradient(
            90deg,
            rgba(90, 10, 6, 0.08),
            rgba(121, 14, 9, 0.82) 26%,
            rgba(255, 40, 0, 0.96) 50%,
            rgba(121, 14, 9, 0.82) 74%,
            rgba(90, 10, 6, 0.08)
        ) !important;
        box-shadow: 0 1px 3px rgba(121, 14, 9, 0.16) !important;
    }

    .kpi-card-dinamico:has(.kpi-title-dinamico.is-primary) {
        border-color: rgba(121, 14, 9, 0.18) !important;
        background:
            radial-gradient(circle at 12% 8%, rgba(255, 255, 255, 0.99) 0%, rgba(255, 255, 255, 0.00) 32%),
            linear-gradient(180deg, #FFFFFF 0%, #FCFCFD 60%, #F5F6F8 100%) !important;
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.98),
            inset 0 0 0 1px rgba(255, 255, 255, 0.48) !important;
        transform: none !important;
    }

    .kpi-title-icon {
        position: relative !important;
        top: 0.01rem !important;
        flex-shrink: 0 !important;
        border: 1px solid rgba(255, 255, 255, 0.18) !important;
        background:
            radial-gradient(circle at 30% 24%, rgba(255, 255, 255, 0.18), rgba(255, 255, 255, 0.00) 42%),
            linear-gradient(135deg, #790E09 0%, #5A0A06 100%) !important;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.18) !important;
    }

    .kpi-block-label {
        min-height: 1.4rem !important;
        padding: 0.2rem 0.6rem !important;
        border-radius: 999px !important;
        border: 1px solid rgba(121, 14, 9, 0.14) !important;
        background: linear-gradient(180deg, #FFFFFF 0%, #FAFBFC 100%) !important;
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.96),
            0 1px 0 rgba(121, 14, 9, 0.04) !important;
    }

    .kpi-block-icon {
        width: 1.05rem !important;
        height: 1.05rem !important;
        background:
            radial-gradient(circle at 30% 30%, rgba(255, 255, 255, 0.30), rgba(255, 255, 255, 0.00) 46%),
            linear-gradient(135deg, rgba(121, 14, 9, 0.12), rgba(90, 10, 6, 0.22)) !important;
        color: #790E09 !important;
        border: 1px solid rgba(121, 14, 9, 0.12) !important;
    }

    .kpi-value-wrap {
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 0.24rem !important;
        padding: 0.16rem 0.72rem !important;
        border-radius: 0.82rem !important;
        border: 1px solid rgba(121, 14, 9, 0.15) !important;
        background:
            radial-gradient(circle at top center, rgba(255, 255, 255, 0.92) 0%, rgba(255, 255, 255, 0.00) 58%),
            linear-gradient(180deg, #FFFFFF 0%, #F9FAFC 100%) !important;
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.96),
            inset 0 -1px 0 rgba(47, 55, 71, 0.03),
            0 1px 0 rgba(255, 255, 255, 0.68) !important;
    }

    .kpi-card-dinamico:has(.kpi-title-dinamico.is-primary) .kpi-value-wrap {
        border-color: rgba(121, 14, 9, 0.18) !important;
        background:
            radial-gradient(circle at top center, rgba(255, 255, 255, 0.94) 0%, rgba(255, 255, 255, 0.00) 60%),
            linear-gradient(180deg, #FFFFFF 0%, #FAFBFC 100%) !important;
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.96),
            inset 0 -1px 0 rgba(47, 55, 71, 0.035),
            0 1px 0 rgba(255, 255, 255, 0.68) !important;
    }

    .kpi-value-wrap::after {
        left: 16% !important;
        right: 16% !important;
        height: 1px !important;
        background: linear-gradient(
            90deg,
            transparent,
            rgba(121, 14, 9, 0.26),
            transparent
        ) !important;
    }

    .kpi-value-dinamico {
        letter-spacing: -0.045em !important;
        background: linear-gradient(135deg, #243041 0%, #1F2937 58%, #790E09 100%) !important;
        -webkit-background-clip: text !important;
        background-clip: text !important;
        -webkit-text-fill-color: transparent !important;
        text-shadow: none !important;
    }

    .kpi-meta-line {
        gap: 0.36rem !important;
        margin: 0.36rem 0 0.24rem 0 !important;
    }

    .kpi-meta-chip,
    .kpi-variacao-item,
    .kpi-parcial-note {
        border-radius: 999px !important;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.92) !important;
    }

    .kpi-meta-chip {
        min-height: 1.5rem !important;
        padding: 0.18rem 0.6rem !important;
        border-color: rgba(121, 14, 9, 0.10) !important;
    }

    .kpi-meta-chip-orc {
        border-color: rgba(121, 14, 9, 0.18) !important;
        background: linear-gradient(180deg, #FFFFFF 0%, #FBFCFD 100%) !important;
    }

    .kpi-meta-chip-silentes {
        border-color: rgba(121, 14, 9, 0.16) !important;
        background: linear-gradient(180deg, #FFFFFF 0%, #FBFCFD 100%) !important;
    }

    .kpi-variacao-item {
        min-height: 1.45rem !important;
        padding: 0.18rem 0.62rem !important;
    }

    .kpi-parcial-note {
        min-height: 1.15rem !important;
        padding: 0.12rem 0.6rem !important;
        border-color: rgba(121, 14, 9, 0.14) !important;
        background: linear-gradient(180deg, #FFFFFF 0%, #FBFCFD 100%) !important;
    }

    [data-testid="stMetric"] {
        padding: 1rem 1.08rem 0.95rem 1.08rem !important;
        min-height: 6.4rem !important;
    }

    [data-testid="stMetric"] > div {
        position: relative;
        z-index: 1;
    }

    [data-testid="stMetricLabel"] {
        margin-bottom: 0.28rem !important;
    }

    [data-testid="stMetricLabel"] p {
        font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif !important;
        font-size: 0.72rem !important;
        font-weight: 800 !important;
        letter-spacing: 0.09em !important;
        text-transform: uppercase !important;
        color: #790E09 !important;
    }

    [data-testid="stMetricValue"] {
        font-family: 'Sora', 'Manrope', 'Segoe UI', sans-serif !important;
        font-weight: 900 !important;
        letter-spacing: -0.04em !important;
        line-height: 1.04 !important;
        color: #243041 !important;
        background: linear-gradient(135deg, #243041 0%, #5A0A06 100%) !important;
        -webkit-background-clip: text !important;
        background-clip: text !important;
        -webkit-text-fill-color: transparent !important;
    }

    [data-testid="stMetricDelta"] {
        font-family: 'Manrope', 'Segoe UI', sans-serif !important;
        font-weight: 800 !important;
        letter-spacing: 0.01em !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

@st.cache_data(show_spinner=False, max_entries=CACHE_MAX_ENTRIES_LARGE, ttl=1800)
def load_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    """
    Carrega e pré-trata a base principal.
    O file_mtime é usado apenas para invalidar cache quando o arquivo muda.
    """
    _ = file_mtime  # parâmetro sentinela para invalidação de cache
    path_obj = Path(path)
    try:
        if path_obj.suffix.lower() == ".parquet":
            df = load_tabular_cached(str(path_obj), file_mtime)
            header_df = pd.DataFrame(columns=df.columns)
            colunas_disponiveis = list(getattr(df, "columns", []))
            colunas_leitura = list(colunas_disponiveis)
        else:
            header_df = load_excel_cached(path, file_mtime, nrows=0)
            colunas_disponiveis = list(getattr(header_df, "columns", []))
            colunas_leitura = [col for col in PRIMARY_BASE_USECOLS if col in colunas_disponiveis]
            df = load_excel_cached(path, file_mtime, usecols=colunas_leitura or None)
    except FileNotFoundError:
        st.error("Arquivo de dados não encontrado!")
        st.stop()
    except Exception as e:
        st.error(f"Erro ao carregar dados: {str(e)}")
        st.stop()

    rename_map = {}
    if 'REGIONAL' not in df.columns and 'DSC_REGIONAL_CMV' in df.columns:
        rename_map['DSC_REGIONAL_CMV'] = 'REGIONAL'
    if 'CANAL_PLAN' not in df.columns and 'DSC_CANAL' in df.columns:
        rename_map['DSC_CANAL'] = 'CANAL_PLAN'
    if 'DAT_MOVIMENTO2' not in df.columns:
        for coluna_data_alt in ['DAT_MOVIMENTO', 'DAT_MOVIMENTO_2', 'PERIODO']:
            if coluna_data_alt in df.columns:
                rename_map[coluna_data_alt] = 'DAT_MOVIMENTO2'
                break
    if 'ID_AFILIADOS' not in df.columns and 'ID_AFILIADO' in df.columns:
        rename_map['ID_AFILIADO'] = 'ID_AFILIADOS'
    if 'ORIGEM_AFILIADOS' not in df.columns and 'ORIGEM_AFILIADO' in df.columns:
        rename_map['ORIGEM_AFILIADO'] = 'ORIGEM_AFILIADOS'
    if rename_map:
        df = df.rename(columns=rename_map)

    colunas_descartar = [
        col for col in ['DSC_REGIONAL_CMV', 'DSC_CANAL', 'DAT_MOVIMENTO', 'DAT_MOVIMENTO_2', 'PERIODO', 'ID_AFILIADO', 'ORIGEM_AFILIADO']
        if col in df.columns
    ]
    if colunas_descartar:
        df = df.drop(columns=colunas_descartar, errors='ignore')

    if 'dat_tratada' not in df.columns and 'mes_ano' in df.columns:
        df['dat_tratada'] = df['mes_ano']
    if 'mes_ano' not in df.columns and 'dat_tratada' in df.columns:
        df['mes_ano'] = df['dat_tratada']

    colunas_texto = ['REGIONAL', 'CANAL_PLAN', 'dat_tratada', 'mes_ano', 'DSC_INDICADOR', 'DSC_MOTIVO_STS', 'COD_PLATAFORMA', 'ID_AFILIADOS', 'ORIGEM_AFILIADOS']
    for col in colunas_texto:
        if col in df.columns:
            if pd.api.types.is_string_dtype(df[col]):
                df[col] = df[col].str.strip()
            else:
                df[col] = df[col].astype('string').str.strip()
    if 'CANAL_PLAN' in df.columns:
        df['CANAL_PLAN'] = df['CANAL_PLAN'].map(normalizar_canal_plan).astype('string')

    if 'DAT_MOVIMENTO2' in df.columns and not pd.api.types.is_datetime64_any_dtype(df['DAT_MOVIMENTO2']):
        df['DAT_MOVIMENTO2'] = pd.to_datetime(df['DAT_MOVIMENTO2'], errors='coerce')

    if 'mes_ano' not in df.columns and 'DAT_MOVIMENTO2' in df.columns:
        df['mes_ano'] = df['DAT_MOVIMENTO2'].apply(lambda dt: _formatar_mes_ano_backlog(dt) if pd.notna(dt) else None)
    if 'dat_tratada' not in df.columns and 'mes_ano' in df.columns:
        df['dat_tratada'] = df['mes_ano']

    if 'ANO' not in df.columns and 'DAT_MOVIMENTO2' in df.columns:
        df['ANO'] = pd.to_datetime(df['DAT_MOVIMENTO2'], errors='coerce').dt.year
    if 'DAT_MÊS' not in df.columns and 'DAT_MOVIMENTO2' in df.columns:
        df['DAT_MÊS'] = pd.to_datetime(df['DAT_MOVIMENTO2'], errors='coerce').dt.month

    if 'QTDE' in df.columns:
        df['QTDE'] = normalizar_numerico_serie(df['QTDE']).fillna(0)
    if 'DESAFIO_QTD' in df.columns:
        df['DESAFIO_QTD'] = normalizar_numerico_serie(df['DESAFIO_QTD']).fillna(0)
    else:
        df['DESAFIO_QTD'] = 0.0

    if 'TEND_QTD' in df.columns:
        df['TEND_QTD'] = normalizar_numerico_serie(df['TEND_QTD']).fillna(0)
    else:
        df['TEND_QTD'] = df.get('QTDE', 0)

    compactar_colunas_categoricas(df, colunas_texto)
    del header_df, colunas_disponiveis, colunas_leitura, rename_map, colunas_descartar
    gc.collect()
    return df


@st.cache_data(show_spinner=False, max_entries=CACHE_MAX_ENTRIES_LARGE, ttl=1800)
def load_excel_cached(
    path: str,
    file_mtime: float | None = None,
    usecols=None,
    nrows: int | None = None
) -> pd.DataFrame:
    """Leitura de Excel com cache invalidado por data de modificação."""
    _ = file_mtime
    return pd.read_excel(path, usecols=usecols, nrows=nrows)

DASHBOARD_DATA_DIR = DASHBOARD_APP_DIR
RAW_PRIMARY_BASE_FILE_PATH = resolver_arquivo_dashboard("base_final_trt_new3.xlsx")
RAW_LIGACOES_FILE_PATH = resolver_arquivo_dashboard("televendas_ligacoes2.xlsx")
RAW_COTACOES_FILE_PATH = resolver_arquivo_dashboard("RelatorioFluxoVidaCotacao.xlsx")
RAW_BACKLOG_CONSOLIDADO_FILE_PATH = resolver_arquivo_dashboard("backlog_consolidado.csv")
CHURN_FILE_PATH = resolver_arquivo_dashboard("base_final_churn.xlsx")
RAW_MIGRACOES_FILE_PATH = resolver_arquivo_dashboard("ANALITICO_MIGRACOES_fev26.xlsx")
RAW_FUNIL_FIXA_FILE_PATH = resolver_arquivo_dashboard(
    "base_funil_ecomm_fixa.xlsx",
    DASHBOARD_LEGACY_MOBILITY_DIR / "base_funil_ecomm_fixa.xlsx"
)
RAW_TEND_FUNIL_FIXA_FILE_PATH = resolver_arquivo_dashboard(
    "tend_funil_ecom.xlsx",
    DASHBOARD_LEGACY_MOBILITY_DIR / "tend_funil_ecom.xlsx"
)
RAW_CONVERGENCIA_FILE_PATH = resolver_arquivo_dashboard(
    "base_convergencia.xlsx",
    Path(r"C:\Users\F270665\OneDrive - Claro SA\Documentos\Extração_VDI\FÍSICOS_MOBILIDADE\base_convergencia.xlsx"),
    Path(r"C:\Users\thiag\OneDrive - Claro SA\Documentos\Extração_VDI\FÍSICOS_MOBILIDADE\base_convergencia.xlsx"),
)


def _resolver_primeiro_arquivo_existente(*candidatos: str | Path) -> Path:
    return resolver_arquivo_dashboard(candidatos[0], *candidatos[1:]) if candidatos else DASHBOARD_APP_DIR


@st.cache_data(show_spinner=False, max_entries=CACHE_MAX_ENTRIES_LARGE, ttl=1800)
def load_tabular_cached(
    path: str,
    file_mtime: float | None = None,
    usecols=None,
    nrows: int | None = None
) -> pd.DataFrame:
    """Leitura cacheada para parquet/csv/excel com invalidação por data de modificação."""
    _ = file_mtime
    path_obj = Path(path)
    suffixes = [s.lower() for s in path_obj.suffixes]

    if path_obj.suffix.lower() == ".parquet":
        df = pd.read_parquet(path_obj, columns=list(usecols) if usecols is not None else None)
        return df.head(nrows) if nrows is not None else df

    if ".csv" in suffixes or path_obj.suffix.lower() in {".csv", ".gz"}:
        kwargs = {"low_memory": False}
        if usecols is not None:
            kwargs["usecols"] = usecols
        if nrows is not None:
            kwargs["nrows"] = nrows
        try:
            return pd.read_csv(path_obj, **kwargs)
        except UnicodeDecodeError:
            return pd.read_csv(path_obj, encoding="latin-1", **kwargs)

    return pd.read_excel(path_obj, usecols=usecols, nrows=nrows)


PRIMARY_BASE_FILE_PATH = resolver_arquivo_preprocessado("base_principal.parquet", RAW_PRIMARY_BASE_FILE_PATH)
ATIVADOS_FILE_PATH = resolver_arquivo_preprocessado(
    "ativados_base.parquet",
    PRIMARY_BASE_FILE_PATH,
    RAW_PRIMARY_BASE_FILE_PATH
)
BASE_PERFORMANCE_FILE_PATH = resolver_arquivo_preprocessado("base_performance_mensal.parquet")
ANALITICA_DIARIA_FILE_PATH = resolver_arquivo_preprocessado("analitica_diaria.parquet")
HOME_ANALITICA_DIARIA_FILE_PATH = resolver_arquivo_preprocessado("home_analitica_diaria.parquet", ANALITICA_DIARIA_FILE_PATH)
HOME_ANALITICA_MENSAL_FILE_PATH = resolver_arquivo_preprocessado("home_analitica_mensal.parquet", HOME_ANALITICA_DIARIA_FILE_PATH, ANALITICA_DIARIA_FILE_PATH)
PEDIDOS_FILE_PATH = resolver_arquivo_preprocessado("pedidos_ecommerce.parquet", RAW_PRIMARY_BASE_FILE_PATH)
LIGACOES_FILE_PATH = resolver_arquivo_preprocessado("ligacoes_receptivo.parquet", RAW_LIGACOES_FILE_PATH)
LIGACOES_MENSAL_AGREGADO_FILE_PATH = resolver_arquivo_preprocessado("ligacoes_mensal_agregado.parquet")
LIGACOES_PERFORMANCE_FILE_PATH = resolver_arquivo_preprocessado("ligacoes_performance_mensal.parquet")
EVOLUCAO_MENSAL_FILE_PATH = resolver_arquivo_preprocessado("evolucao_mensal.parquet", "evolucao_mensal_agregado.parquet")
COTACOES_FILE_PATH = resolver_arquivo_preprocessado("cotacoes_agregado.parquet", RAW_COTACOES_FILE_PATH)
BACKLOG_CONSOLIDADO_FILE_PATH = resolver_arquivo_preprocessado(
    "backlog_consolidado_limpo.parquet",
    RAW_BACKLOG_CONSOLIDADO_FILE_PATH
)
ANALITICO_MIGRACOES_FILE_PATH = resolver_arquivo_preprocessado("migracoes_pme.parquet", RAW_MIGRACOES_FILE_PATH)
DESATIVADOS_FILE_PATH = resolver_arquivo_preprocessado("desativados_base.parquet", CHURN_FILE_PATH)
CONVERGENCIA_FILE_PATH = resolver_arquivo_preprocessado(
    "convergencia_mensal.parquet",
    "convergencia_base.parquet",
    RAW_CONVERGENCIA_FILE_PATH
)


def _carregar_dataframe_preprocessado(
    path: str,
    file_mtime: float | None = None,
    *,
    required_cols: set[str] | None = None,
    text_cols: list[str] | tuple[str, ...] | None = None,
    numeric_cols: list[str] | tuple[str, ...] | None = None,
    date_cols: list[str] | tuple[str, ...] | None = None,
    category_cols: list[str] | tuple[str, ...] | None = None,
    default_values: dict[str, object] | None = None
) -> pd.DataFrame:
    path_obj = Path(path)
    if not path_obj.exists():
        return pd.DataFrame()

    try:
        df = load_tabular_cached(str(path_obj), file_mtime)
    except Exception:
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    for coluna, valor_default in (default_values or {}).items():
        if coluna not in df.columns:
            df[coluna] = valor_default

    if required_cols and not set(required_cols).issubset(set(df.columns)):
        return pd.DataFrame()

    for coluna in (date_cols or []):
        if coluna in df.columns:
            df[coluna] = pd.to_datetime(df[coluna], errors='coerce')

    for coluna in (text_cols or []):
        if coluna in df.columns:
            df[coluna] = df[coluna].astype('string').str.strip()

    for coluna_canal in ('CANAL_PLAN', 'Canal', 'NM_CANAL_VENDA_SUBGRUPO', 'DSC_CANAL_AJUSTADO'):
        if coluna_canal in df.columns:
            df[coluna_canal] = df[coluna_canal].map(normalizar_canal_plan).astype('string')
    if 'CANAL_PLAN' in df.columns and 'CANAL_NORM' in df.columns:
        df['CANAL_NORM'] = df['CANAL_PLAN'].map(normalizar_texto_chave).astype('string')

    for coluna in (numeric_cols or []):
        if coluna in df.columns:
            df[coluna] = normalizar_numerico_serie(df[coluna]).fillna(0.0)

    compactar_colunas_categoricas(df, list(category_cols or text_cols or []))
    return df


CONVERGENCIA_COL_ALIASES = {
    'DAT_MOVIMENTO': ('DAT_MOVIMENTO', 'DATA_MOVIMENTO', 'DATA', 'PERIODO'),
    'DSC_REGIONAL': ('DSC_REGIONAL', 'REGIONAL', 'DSC_REGIONAL_CMV'),
    'DSC_CANAL_VENDA': ('DSC_CANAL_VENDA', 'CANAL_PLAN', 'DSC_CANAL', 'CANAL'),
    'DSC_TIPO_ORIGEM': ('DSC_TIPO_ORIGEM', 'COD_PLATAFORMA', 'PRODUTO', 'PLATAFORMA'),
    'QTDE': ('QTDE', 'QTD'),
    'QTDE_CNPJ8': ('QTDE_CNPJ8', 'QTD_CNPJ8', 'QTDE_CLIENTES', 'CLIENTES'),
    'FLG_INCREMENTO': ('FLG_INCREMENTO', 'FLAG_INCREMENTO'),
    'FLG_PORTABILIDADE': ('FLG_PORTABILIDADE', 'FLAG_PORTABILIDADE'),
    'FLG_MIGRACAO': ('FLG_MIGRACAO', 'FLAG_MIGRACAO'),
    'FLG_RENOVACAO': ('FLG_RENOVACAO', 'FLAG_RENOVACAO'),
    'FLG_TROCA_TITULARIDADE': ('FLG_TROCA_TITULARIDADE', 'FLAG_TROCA_TITULARIDADE'),
    'FLG_VENDA_CONVERGENTE': ('FLG_VENDA_CONVERGENTE', 'FLAG_VENDA_CONVERGENTE', 'VENDA_CONVERGENTE'),
    'FLG_NOVO': ('FLG_NOVO', 'FLAG_NOVO'),
    'FLG_NOVO_NOVO': ('FLG_NOVO_NOVO', 'FLAG_NOVO_NOVO', 'FLG_NOVO-NOVO'),
}


def _normalizar_produto_convergencia(valor) -> str:
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


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def load_convergencia_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    """Carrega a base de convergência, mantendo apenas campos usados nos KPIs/tabela."""
    _ = file_mtime
    path_obj = Path(path)
    if not path_obj.exists():
        return pd.DataFrame()

    if path_obj.suffix.lower() == ".parquet":
        try:
            df_raw_opt = load_tabular_cached(str(path_obj), file_mtime)
        except Exception:
            df_raw_opt = pd.DataFrame()

        colunas_agregadas = {'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA', 'mes_ano', 'TOTAL', 'LINHAS', 'NOVO', 'NOVO_NOVO', 'CONV'}
        if df_raw_opt is not None and not df_raw_opt.empty and colunas_agregadas.issubset(set(df_raw_opt.columns)):
            df_agregado = df_raw_opt.copy()
            for coluna in ['mes_ano', 'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA']:
                df_agregado[coluna] = df_agregado[coluna].astype('string').str.strip()
            df_agregado['CANAL_PLAN'] = df_agregado['CANAL_PLAN'].map(normalizar_canal_plan).astype('string')
            for coluna in ['TOTAL', 'LINHAS', 'NOVO', 'NOVO_NOVO', 'CONV', 'QTDE_CNPJ8', 'QTDE']:
                if coluna in df_agregado.columns:
                    df_agregado[coluna] = normalizar_numerico_serie(df_agregado[coluna]).fillna(0.0)
            if 'QTDE_CNPJ8' not in df_agregado.columns:
                df_agregado['QTDE_CNPJ8'] = df_agregado['TOTAL']
            if 'QTDE' not in df_agregado.columns:
                df_agregado['QTDE'] = df_agregado['LINHAS']
            if 'DATA_DIA' in df_agregado.columns:
                df_agregado['DATA_DIA'] = pd.to_datetime(df_agregado['DATA_DIA'], errors='coerce')
            compactar_colunas_categoricas(df_agregado, ['mes_ano', 'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA'])
            return df_agregado

        df_opt = _carregar_dataframe_preprocessado(
            str(path_obj),
            file_mtime,
            required_cols={
                'DATA_DIA', 'mes_ano', 'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA',
                'QTDE', 'QTDE_CNPJ8', 'FLAG_CONV', 'FLAG_NOVO', 'FLAG_NOVO_NOVO'
            },
            text_cols=['mes_ano', 'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA'],
            numeric_cols=['QTDE', 'QTDE_CNPJ8'],
            date_cols=['DATA_DIA'],
            category_cols=['mes_ano', 'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA']
        )
        if not df_opt.empty:
            for coluna_flag in ['FLAG_CONV', 'FLAG_NOVO', 'FLAG_NOVO_NOVO']:
                df_opt[coluna_flag] = df_opt[coluna_flag].astype(bool)
            return df_opt

    try:
        header_df = load_excel_cached(str(path_obj), file_mtime, nrows=0)
        colunas_origem = list(getattr(header_df, "columns", []))
    except Exception:
        return pd.DataFrame()

    rename_map = {}
    usecols = []
    for destino, aliases in CONVERGENCIA_COL_ALIASES.items():
        coluna_real = encontrar_coluna_por_alias(colunas_origem, *aliases)
        if coluna_real:
            rename_map[coluna_real] = destino
            usecols.append(coluna_real)

    obrigatorias = {'DAT_MOVIMENTO', 'DSC_REGIONAL', 'DSC_CANAL_VENDA', 'DSC_TIPO_ORIGEM', 'QTDE', 'QTDE_CNPJ8'}
    if not obrigatorias.issubset(set(rename_map.values())):
        return pd.DataFrame()

    try:
        df = load_excel_cached(str(path_obj), file_mtime, usecols=usecols)
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns=rename_map)
    for coluna in CONVERGENCIA_COL_ALIASES:
        if coluna not in df.columns:
            df[coluna] = ""

    df['DATA_DIA'] = pd.to_datetime(
        df['DAT_MOVIMENTO'],
        errors='coerce',
        dayfirst=True,
        format='mixed'
    ).dt.normalize()
    df = df[df['DATA_DIA'].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    df['mes_ano'] = df['DATA_DIA'].apply(_formatar_mes_ano_backlog)
    df['REGIONAL'] = df['DSC_REGIONAL'].astype(str).str.strip().str[:3].str.upper()
    df['CANAL_PLAN'] = df['DSC_CANAL_VENDA'].map(normalizar_canal_plan).astype('string').str.strip()
    df['COD_PLATAFORMA'] = df['DSC_TIPO_ORIGEM'].apply(_normalizar_produto_convergencia)
    df = df[df['COD_PLATAFORMA'].isin(['FIXA', 'CONTA'])].copy()
    if df.empty:
        return pd.DataFrame()

    df['QTDE'] = normalizar_numerico_serie(df['QTDE']).fillna(0.0)
    df['QTDE_CNPJ8'] = normalizar_numerico_serie(df['QTDE_CNPJ8']).fillna(0.0)

    df['FLAG_CONV'] = df['FLG_VENDA_CONVERGENTE'].apply(normalizar_texto_chave).eq('CONV')
    df['FLAG_NOVO'] = df['FLG_NOVO'].apply(normalizar_texto_chave).eq('NOVO')
    df['FLAG_NOVO_NOVO'] = df['FLG_NOVO_NOVO'].apply(normalizar_texto_chave).eq('NOVO NOVO')

    colunas_saida = [
        'DATA_DIA', 'mes_ano', 'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA',
        'QTDE', 'QTDE_CNPJ8', 'FLAG_CONV', 'FLAG_NOVO', 'FLAG_NOVO_NOVO'
    ]
    df_saida = df[colunas_saida].copy()
    compactar_colunas_categoricas(df_saida, ['mes_ano', 'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA'])
    return df_saida


def ordenar_meses_convergencia(meses_ref) -> list[str]:
    return sorted(
        [str(m).strip().lower() for m in list(meses_ref or []) if str(m).strip()],
        key=mes_ano_para_data
    )


def pct_convergencia(parte: float, total: float) -> float:
    return (float(parte) / float(total) * 100.0) if float(total or 0) > 0 else 0.0


def fmt_pct_convergencia(valor: float) -> str:
    return f"{float(valor or 0):.0f}%".replace(".", ",")


def agregar_convergencia_metricas(df_ref: pd.DataFrame, chaves: list[str]) -> pd.DataFrame:
    colunas_saida = chaves + ['TOTAL', 'LINHAS', 'NOVO', 'NOVO_NOVO', 'CONV']
    if df_ref is None or df_ref.empty:
        return pd.DataFrame(columns=colunas_saida)

    colunas_agregadas = ['TOTAL', 'LINHAS', 'NOVO', 'NOVO_NOVO', 'CONV']
    if set(colunas_agregadas).issubset(set(df_ref.columns)):
        df_agregado = df_ref.copy()
        for coluna in colunas_agregadas:
            df_agregado[coluna] = pd.to_numeric(df_agregado[coluna], errors='coerce').fillna(0.0)
        return (
            df_agregado.groupby(chaves, as_index=False, observed=True)[colunas_agregadas]
            .sum()
            .reindex(columns=colunas_saida)
            .copy()
        )

    df_tmp = df_ref.copy()
    df_tmp['QTDE_CNPJ8'] = pd.to_numeric(df_tmp.get('QTDE_CNPJ8', 0), errors='coerce').fillna(0.0)
    df_tmp['QTDE'] = pd.to_numeric(df_tmp.get('QTDE', 0), errors='coerce').fillna(0.0)
    base = (
        df_tmp.groupby(chaves, as_index=False, observed=True)[['QTDE_CNPJ8', 'QTDE']]
        .sum()
        .rename(columns={'QTDE_CNPJ8': 'TOTAL', 'QTDE': 'LINHAS'})
    )
    for flag_col, destino in [
        ('FLAG_NOVO', 'NOVO'),
        ('FLAG_NOVO_NOVO', 'NOVO_NOVO'),
        ('FLAG_CONV', 'CONV'),
    ]:
        df_flag = df_tmp[df_tmp[flag_col].astype(bool)].copy()
        if df_flag.empty:
            base[destino] = 0.0
            continue
        agg_flag = (
            df_flag.groupby(chaves, as_index=False, observed=True)['QTDE_CNPJ8']
            .sum()
            .rename(columns={'QTDE_CNPJ8': destino})
        )
        base = base.merge(agg_flag, on=chaves, how='left')
        base[destino] = pd.to_numeric(base[destino], errors='coerce').fillna(0.0)

    return base[colunas_saida].copy()


def linha_chip_convergencia(rotulo: str, valor: float, total: float) -> str:
    return (
        '<span class="conv-kpi-chip">'
        f'<span class="conv-kpi-chip-label">{escape(str(rotulo))}:</span>'
        f'<span class="conv-kpi-chip-value">{formatar_numero_brasileiro(valor, 0)}</span>'
        '<span class="conv-kpi-chip-sep">-</span>'
        f'<span class="conv-kpi-chip-pct">{fmt_pct_convergencia(pct_convergencia(valor, total))}</span>'
        '</span>'
    )


def montar_card_convergencia_html(canal_ref: str, produto_ref: str, metricas_ref: dict) -> str:
    total = float(metricas_ref.get('TOTAL', 0) or 0)
    novo = float(metricas_ref.get('NOVO', 0) or 0)
    novo_novo = float(metricas_ref.get('NOVO_NOVO', 0) or 0)
    conv = float(metricas_ref.get('CONV', 0) or 0)
    return (
        f'<div class="kpi-block-dinamico conv-kpi-block" title="Convergência {escape(str(produto_ref))} no canal {escape(str(canal_ref))}">'
        f'{build_kpi_block_label_html(str(produto_ref), str(produto_ref))}'
        f'<div class="kpi-value-wrap"><div class="kpi-value-dinamico">{formatar_numero_brasileiro(total, 0)}</div></div>'
        '<div class="conv-kpi-main-label">Clientes</div>'
        '<div class="conv-kpi-line conv-kpi-line-compact">'
        f'{linha_chip_convergencia("Novo", novo, total)}'
        f'{linha_chip_convergencia("NV-NV", novo_novo, total)}'
        f'{linha_chip_convergencia("Conv", conv, total)}'
        '</div>'
        '</div>'
    )


def montar_tabela_convergencia_dimensao_html(
    df_ref: pd.DataFrame,
    meses_ref: list[str],
    coluna_dimensao: str = 'REGIONAL',
    titulo_coluna: str = 'REGIONAL',
    ordem_linhas: list[str] | None = None
) -> str:
    if df_ref is None or df_ref.empty or not meses_ref or coluna_dimensao not in df_ref.columns:
        return ""
    df_tab = df_ref[df_ref['mes_ano'].astype(str).str.strip().str.lower().isin(meses_ref)].copy()
    if df_tab.empty:
        return ""

    df_agg = agregar_convergencia_metricas(df_tab, [coluna_dimensao, 'mes_ano'])
    if df_agg.empty:
        return ""

    mapa_metricas = {
        (str(row[coluna_dimensao]).strip(), str(row['mes_ano']).strip().lower()): row
        for _, row in df_agg.iterrows()
    }
    linhas_dimensao_base = (
        df_agg[df_agg['mes_ano'].astype(str).str.lower().eq(str(meses_ref[-1]).lower())]
        .sort_values('TOTAL', ascending=False)[coluna_dimensao]
        .astype(str)
        .tolist()
    )
    todas_linhas = df_agg[coluna_dimensao].dropna().astype(str).unique().tolist()
    if ordem_linhas:
        linhas_dimensao = [item for item in ordem_linhas if item in set(todas_linhas)]
        linhas_dimensao += [item for item in linhas_dimensao_base if item not in linhas_dimensao]
    else:
        linhas_dimensao = list(linhas_dimensao_base)
    linhas_dimensao += [
        item for item in sorted(todas_linhas)
        if item not in linhas_dimensao
    ]

    def _metricas_linha(reg_ref: str, mes_ref: str) -> dict[str, float]:
        row_ref = mapa_metricas.get((reg_ref, str(mes_ref).strip().lower()))
        if row_ref is None:
            return {'TOTAL': 0.0, 'NOVO': 0.0, 'NOVO_NOVO': 0.0, 'CONV': 0.0}
        return {
            'TOTAL': float(row_ref.get('TOTAL', 0) or 0),
            'NOVO': float(row_ref.get('NOVO', 0) or 0),
            'NOVO_NOVO': float(row_ref.get('NOVO_NOVO', 0) or 0),
            'CONV': float(row_ref.get('CONV', 0) or 0),
        }

    def _cells_metricas(reg_ref: str, mes_ref: str) -> str:
        m = _metricas_linha(reg_ref, mes_ref)
        total = m['TOTAL']
        return (
            f'<td class="col-total">{formatar_numero_brasileiro(total, 0)}</td>'
            f'<td>{formatar_numero_brasileiro(m["NOVO"], 0)}</td>'
            f'<td>{formatar_numero_brasileiro(m["NOVO_NOVO"], 0)}</td>'
            f'<td>{formatar_numero_brasileiro(m["CONV"], 0)}</td>'
            f'<td class="col-pct">{fmt_pct_convergencia(pct_convergencia(m["NOVO"], total))}</td>'
            f'<td class="col-pct">{fmt_pct_convergencia(pct_convergencia(m["NOVO_NOVO"], total))}</td>'
            f'<td class="col-pct">{fmt_pct_convergencia(pct_convergencia(m["CONV"], total))}</td>'
        )

    total_por_mes = {}
    for mes_ref in meses_ref:
        df_mes = df_agg[df_agg['mes_ano'].astype(str).str.strip().str.lower().eq(str(mes_ref).lower())]
        total_por_mes[mes_ref] = {
            'TOTAL': float(df_mes['TOTAL'].sum()),
            'NOVO': float(df_mes['NOVO'].sum()),
            'NOVO_NOVO': float(df_mes['NOVO_NOVO'].sum()),
            'CONV': float(df_mes['CONV'].sum()),
        }

    def _cells_total(mes_ref: str) -> str:
        m = total_por_mes.get(mes_ref, {'TOTAL': 0.0, 'NOVO': 0.0, 'NOVO_NOVO': 0.0, 'CONV': 0.0})
        total = float(m['TOTAL'] or 0)
        return (
            f'<td class="col-total">{formatar_numero_brasileiro(total, 0)}</td>'
            f'<td>{formatar_numero_brasileiro(m["NOVO"], 0)}</td>'
            f'<td>{formatar_numero_brasileiro(m["NOVO_NOVO"], 0)}</td>'
            f'<td>{formatar_numero_brasileiro(m["CONV"], 0)}</td>'
            f'<td class="col-pct">{fmt_pct_convergencia(pct_convergencia(m["NOVO"], total))}</td>'
            f'<td class="col-pct">{fmt_pct_convergencia(pct_convergencia(m["NOVO_NOVO"], total))}</td>'
            f'<td class="col-pct">{fmt_pct_convergencia(pct_convergencia(m["CONV"], total))}</td>'
        )

    th_mes = ''.join(
        f'<th colspan="7" class="th-mes">{escape(str(mes).replace("/", "-").upper())}</th>'
        for mes in meses_ref
    )
    th_sub = ''.join(
        '<th class="th-total">TOTAL</th><th class="th-novo">NOVO</th><th class="th-nvnv">NV-NV</th><th class="th-conv">CONV</th>'
        '<th class="th-pct th-pct-first">%NOVO</th><th class="th-pct">%NV-NV</th><th class="th-pct">%CONV</th>'
        for _ in meses_ref
    )
    colgroup = '<colgroup><col style="width:82px;">' + ''.join(
        '<col style="width:48px;"><col style="width:44px;"><col style="width:44px;"><col style="width:44px;">'
        '<col style="width:46px;"><col style="width:46px;"><col style="width:46px;">'
        for _ in meses_ref
    ) + '</colgroup>'

    linhas = (
        '<tr class="linha-total"><td class="col-regional">TOTAL</td>' +
        ''.join(_cells_total(mes_ref) for mes_ref in meses_ref) +
        '</tr>'
    )
    for reg_ref in linhas_dimensao:
        linhas += (
            '<tr class="linha-regional">'
            f'<td class="col-regional">{escape(str(reg_ref))}</td>'
            f'{"".join(_cells_metricas(str(reg_ref), mes_ref) for mes_ref in meses_ref)}'
            '</tr>'
        )

    return f"""
    <style>
    .conv-table-container {{
        width: 100%;
        overflow-x: auto;
        overflow-y: auto;
        max-height: 560px;
        border: 2px solid #790E09;
        border-radius: 10px;
        box-shadow: 0 4px 20px rgba(121, 14, 9, 0.15);
        background: #FFFFFF;
        margin: 12px 0 18px 0;
        font-family: 'Manrope', 'Segoe UI', sans-serif;
    }}
    table.conv-table {{
        width: max-content;
        min-width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
        font-variant-numeric: tabular-nums;
        font-size: 9px;
        line-height: 1.04;
    }}
    .conv-table th {{
        background: linear-gradient(135deg, #790E09 0%, #5A0A06 100%);
        color: #FFFFFF;
        padding: 5px 4px;
        text-align: center;
        border-right: 1px solid #FFFFFF;
        border-bottom: 3px solid #5A0A06;
        font-weight: 700;
        white-space: normal;
        overflow-wrap: anywhere;
        text-transform: uppercase;
    }}
    .conv-table th.th-mes {{
        background: linear-gradient(135deg, #6C0C08 0%, #4A0704 100%);
        font-size: 9.4px;
    }}
    .conv-table th.th-total {{
        background: linear-gradient(135deg, #5F0B07 0%, #4B0805 100%);
    }}
    .conv-table th.th-novo {{
        background: linear-gradient(135deg, #72130E 0%, #5B0D08 100%);
    }}
    .conv-table th.th-nvnv {{
        background: linear-gradient(135deg, #7A2D22 0%, #5E1710 100%);
    }}
    .conv-table th.th-conv {{
        background: linear-gradient(135deg, #67413A 0%, #4E241D 100%);
    }}
    .conv-table th.th-pct {{
        background: linear-gradient(135deg, #5F0B07 0%, #4B0805 100%) !important;
        color: #FFFFFF !important;
        border-bottom-color: #5A0A06;
    }}
    .conv-table th.th-pct-first {{
        border-left: 2px solid rgba(255, 255, 255, 0.78);
    }}
    .conv-table td {{
        padding: 3.8px 4px;
        text-align: right;
        border-bottom: 1px solid #FFFFFF;
        border-right: 1px solid #FFFFFF;
        color: #2F3747;
        font-weight: 400;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .conv-table td.col-regional {{
        text-align: left;
        font-weight: 600;
        color: #333333;
    }}
    .conv-table tr.linha-regional:nth-child(even) td {{
        background: linear-gradient(135deg, #FCFCFD 0%, #F7F8FA 100%);
    }}
    .conv-table tr.linha-regional:nth-child(odd) td {{
        background: linear-gradient(135deg, #FFFFFF 0%, #FAFBFC 100%);
    }}
    .conv-table tr.linha-regional:hover td {{
        background: linear-gradient(135deg, #FFF6F3 0%, #FAF0ED 100%);
        box-shadow: inset 0 0 0 1px rgba(162, 59, 54, 0.12);
    }}
    .conv-table td.col-total {{
        background: linear-gradient(180deg, rgba(47, 55, 71, 0.06) 0%, rgba(47, 55, 71, 0.025) 100%);
        color: #1F2937;
        font-weight: 600;
    }}
    .conv-table td.col-pct {{
        font-weight: 600;
    }}
    .conv-table tr.linha-total td {{
        background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
        color: #FFFFFF !important;
        font-weight: 700;
    }}
    </style>
    <div class="conv-table-container">
      <table class="conv-table">
        {colgroup}
        <thead>
          <tr><th rowspan="2">{escape(str(titulo_coluna).upper())}</th>{th_mes}</tr>
          <tr>{th_sub}</tr>
        </thead>
        <tbody>{linhas}</tbody>
      </table>
    </div>
    """


def montar_tabela_convergencia_regional_html(df_ref: pd.DataFrame, meses_ref: list[str]) -> str:
    return montar_tabela_convergencia_dimensao_html(df_ref, meses_ref, 'REGIONAL', 'REGIONAL')


def montar_tabela_convergencia_canal_html(
    df_ref: pd.DataFrame,
    meses_ref: list[str],
    ordem_canais_ref: list[str] | None = None
) -> str:
    return montar_tabela_convergencia_dimensao_html(
        df_ref,
        meses_ref,
        'CANAL_PLAN',
        'CANAL',
        ordem_canais_ref
    )


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def load_ativados_dashboard_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    df = _carregar_dataframe_preprocessado(
        path,
        file_mtime,
        required_cols={'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'dat_tratada', 'QTDE', 'DESAFIO_QTD', 'TEND_QTD'},
        text_cols=['REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'dat_tratada', 'DSC_MOTIVO_STS'],
        numeric_cols=['QTDE', 'DESAFIO_QTD', 'TEND_QTD', 'ANO', 'DAT_MÊS'],
        date_cols=['DAT_MOVIMENTO2'],
        category_cols=['REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'dat_tratada', 'DSC_MOTIVO_STS']
    )
    if df.empty:
        return df
    df['REGIONAL'] = df['REGIONAL'].astype(str).str.strip().str[:3].str.upper()
    return df


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def load_base_performance_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    return _carregar_dataframe_preprocessado(
        path,
        file_mtime,
        required_cols={
            'REGIONAL', 'CANAL_PLAN', 'CANAL_NORM', 'COD_PLATAFORMA', 'PLATAFORMA_NORM',
            'DSC_INDICADOR', 'INDICADOR_NORM', 'INDICADOR_CANONICO', 'dat_tratada', 'ANO_REF',
            'QTDE', 'DESAFIO_QTD', 'TEND_QTD'
        },
        text_cols=[
            'REGIONAL', 'CANAL_PLAN', 'CANAL_NORM', 'COD_PLATAFORMA', 'PLATAFORMA_NORM',
            'DSC_INDICADOR', 'INDICADOR_NORM', 'INDICADOR_CANONICO', 'dat_tratada', 'ANO_REF'
        ],
        numeric_cols=['QTDE', 'DESAFIO_QTD', 'TEND_QTD'],
        category_cols=[
            'REGIONAL', 'CANAL_PLAN', 'CANAL_NORM', 'COD_PLATAFORMA', 'PLATAFORMA_NORM',
            'DSC_INDICADOR', 'INDICADOR_NORM', 'INDICADOR_CANONICO', 'dat_tratada', 'ANO_REF'
        ]
    )


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def load_analitica_diaria_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    return _carregar_dataframe_preprocessado(
        path,
        file_mtime,
        required_cols={
            'CANAL_PLAN', 'COD_PLATAFORMA', 'REGIONAL', 'dat_tratada', 'MES_NORM', 'DATA_DIA',
            'QTDE', 'DESAFIO_QTD', 'TEND_QTD', 'DSC_INDICADOR', 'DSC_IND_NORM', 'IND_NORM'
        },
        text_cols=[
            'CANAL_PLAN', 'COD_PLATAFORMA', 'REGIONAL', 'dat_tratada', 'MES_NORM',
            'DSC_INDICADOR', 'DSC_IND_NORM', 'IND_NORM'
        ],
        numeric_cols=['QTDE', 'DESAFIO_QTD', 'TEND_QTD'],
        date_cols=['DAT_MOVIMENTO2', 'DATA_DIA'],
        category_cols=[
            'CANAL_PLAN', 'COD_PLATAFORMA', 'REGIONAL', 'dat_tratada', 'MES_NORM',
            'DSC_INDICADOR', 'DSC_IND_NORM', 'IND_NORM'
        ]
    )


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def load_home_analitica_mensal_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    return _carregar_dataframe_preprocessado(
        path,
        file_mtime,
        required_cols={
            'CANAL_PLAN', 'COD_PLATAFORMA', 'REGIONAL', 'dat_tratada', 'MES_NORM',
            'QTDE', 'DESAFIO_QTD', 'TEND_QTD', 'DSC_INDICADOR', 'DSC_IND_NORM', 'IND_NORM'
        },
        text_cols=[
            'CANAL_PLAN', 'COD_PLATAFORMA', 'REGIONAL', 'dat_tratada', 'MES_NORM',
            'DSC_INDICADOR', 'DSC_IND_NORM', 'IND_NORM'
        ],
        numeric_cols=['QTDE', 'DESAFIO_QTD', 'TEND_QTD'],
        category_cols=[
            'CANAL_PLAN', 'COD_PLATAFORMA', 'REGIONAL', 'dat_tratada', 'MES_NORM',
            'DSC_INDICADOR', 'DSC_IND_NORM', 'IND_NORM'
        ]
    )


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def load_home_analitica_diaria_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    return load_analitica_diaria_data(path, file_mtime)


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def load_ligacoes_mensal_agregado_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    return _carregar_dataframe_preprocessado(
        path,
        file_mtime,
        required_cols={'REGIONAL', 'mes_ano', 'ANO', 'MES_NUM', 'TOTAL_QTD', 'FIXA_QTD', 'CONTA_QTD', 'CTC_QTD'},
        text_cols=['REGIONAL', 'mes_ano'],
        numeric_cols=['ANO', 'MES_NUM', 'TOTAL_QTD', 'FIXA_QTD', 'CONTA_QTD', 'CTC_QTD'],
        category_cols=['REGIONAL', 'mes_ano']
    )


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def load_ligacoes_performance_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    return load_base_performance_data(path, file_mtime)


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def load_evolucao_mensal_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    df_evolucao = _carregar_dataframe_preprocessado(
        path,
        file_mtime,
        required_cols={'Ano', 'Mês', 'Mês_Num', 'Valor', 'Tipo', 'Produto', 'Regional', 'Canal', 'Indicador'},
        text_cols=['Mês', 'Tipo', 'Produto', 'Regional', 'Canal', 'Indicador', 'Tipo_Chamada', 'Periodo'],
        numeric_cols=['Ano', 'Mês_Num', 'Valor'],
        category_cols=['Mês', 'Tipo', 'Produto', 'Regional', 'Canal', 'Indicador', 'Tipo_Chamada', 'Periodo']
    )
    if df_evolucao.empty:
        return df_evolucao

    df_evolucao['Produto'] = df_evolucao['Produto'].astype(str).str.strip().apply(normalizar_rotulo_produto)
    df_evolucao['Regional'] = df_evolucao['Regional'].astype(str).str.strip().str[:3].str.upper()
    df_evolucao['Canal'] = df_evolucao['Canal'].astype(str).str.strip()
    df_evolucao['Indicador'] = df_evolucao['Indicador'].astype(str).str.strip()
    df_evolucao['Indicador_Chave'] = df_evolucao['Indicador'].map(normalizar_texto_chave).astype('string')
    df_evolucao['Tipo'] = df_evolucao['Tipo'].astype(str).str.strip()
    if 'Periodo' in df_evolucao.columns:
        df_evolucao['Periodo'] = df_evolucao['Periodo'].astype(str).str.strip().str.lower()
    else:
        df_evolucao['Periodo'] = (
            df_evolucao['Mês'].astype(str).str.strip().str.lower() + "/" +
            df_evolucao['Ano'].astype(str).str[-2:]
        )
    if 'Tipo_Chamada' in df_evolucao.columns:
        df_evolucao['Tipo_Chamada'] = df_evolucao['Tipo_Chamada'].astype(str).str.strip()

    compactar_colunas_categoricas(
        df_evolucao,
        ['Mês', 'Tipo', 'Produto', 'Regional', 'Canal', 'Indicador', 'Indicador_Chave', 'Tipo_Chamada', 'Periodo']
    )
    return df_evolucao


@st.cache_data(show_spinner=False, max_entries=6, ttl=1800)
def load_evolucao_mensal(
    path: str,
    file_mtime: float | None,
    produto: str = "Todas",
    regional: str = "Todas",
    canal: str = "Todos",
    indicadores: tuple[str, ...] = tuple(),
    periodos: tuple[str, ...] = tuple(),
    tipo_chamada: str = "Todos"
) -> pd.DataFrame:
    df = load_evolucao_mensal_data(path, file_mtime)
    if df.empty:
        return df

    mask = np.ones(len(df), dtype=bool)

    if produto != "Todas":
        mask &= df['Produto'].eq(normalizar_rotulo_produto(produto)).to_numpy()
    if regional != "Todas":
        mask &= df['Regional'].eq(str(regional).strip().upper()[:3]).to_numpy()
    if canal != "Todos":
        mask &= df['Canal'].eq(str(canal).strip()).to_numpy()
    if indicadores:
        indicadores_validos = {
            normalizar_texto_chave(item) for item in indicadores if str(item).strip()
        }
        if indicadores_validos:
            coluna_indicador_chave = 'Indicador_Chave' if 'Indicador_Chave' in df.columns else 'Indicador'
            if coluna_indicador_chave == 'Indicador_Chave':
                mask &= df[coluna_indicador_chave].astype(str).isin(indicadores_validos).to_numpy()
            else:
                mask &= df[coluna_indicador_chave].map(normalizar_texto_chave).isin(indicadores_validos).to_numpy()
    if periodos:
        periodos_validos = {str(item).strip().lower() for item in periodos if str(item).strip()}
        if periodos_validos:
            mask &= df['Periodo'].isin(periodos_validos).to_numpy()
    if tipo_chamada != "Todos" and 'Tipo_Chamada' in df.columns:
        tipo_ref = str(tipo_chamada).strip()
        mask &= ((df['Tipo'] != 'Real') | df['Tipo_Chamada'].eq(tipo_ref)).to_numpy()

    return df.loc[mask].copy(deep=False)


@st.cache_data(show_spinner=False, max_entries=2, ttl=1800)
def load_desativados_base_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    df = _carregar_dataframe_preprocessado(
        path,
        file_mtime,
        required_cols={'COD_PLATAFORMA', 'REGIONAL', 'CANAL_PLAN', 'INADIMPLENTE', 'mes_ano', 'QTDE', 'QTDE_SILENTE'},
        text_cols=['COD_PLATAFORMA', 'REGIONAL', 'CANAL_PLAN', 'INADIMPLENTE', 'mes_ano'],
        numeric_cols=['QTDE', 'QTDE_SILENTE', 'FLG_SILENTE'],
        date_cols=['DAT_MOVIMENTO2'],
        category_cols=['COD_PLATAFORMA', 'REGIONAL', 'CANAL_PLAN', 'INADIMPLENTE', 'mes_ano']
    )
    if df.empty:
        return df
    df['REGIONAL'] = df['REGIONAL'].astype(str).str.strip().str[:3].str.upper()
    return df

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
MIGRACOES_PME_CANAIS = ["E-Commerce", "Consultivo Remoto", "Televendas"]
COTACOES_ORDEM_CANAIS = [
    "Televendas Ativo",
    "Televendas Receptivo",
    "S2S+DAC",
    "E-Commerce",
    "Consultivo Remoto",
    "Hospitality"
]

def _read_excel_with_copy_fallback(
    path: str | Path,
    *,
    usecols=None,
    nrows: int | None = None
) -> pd.DataFrame:
    """Le Excel com fallback para copia temporaria quando o arquivo estiver bloqueado."""
    path_obj = Path(path)
    try:
        return pd.read_excel(path_obj, usecols=usecols, nrows=nrows)
    except PermissionError:
        temp_path = Path(tempfile.gettempdir()) / f"{path_obj.stem}_cache{path_obj.suffix}"
        shutil.copy2(path_obj, temp_path)
        return pd.read_excel(temp_path, usecols=usecols, nrows=nrows)

def _filtrar_regra_cotacoes_novas_linhas(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica a regra base de COTAÇÕES por canal de venda e palavras-chave."""
    if df is None or df.empty:
        return pd.DataFrame(columns=getattr(df, "columns", None))

    atividades_norm = df["LISTA_ATIVIDADES"].astype(str).map(normalizar_chave_visual)
    canal_venda_norm = df["CANAL_DE_VENDA"].astype(str).map(normalizar_chave_visual)
    mask_regra = (
        df["QTD_NOVAS_LINHAS_ATIVAR"].ne(0) &
        df["QTD_LINHAS_VOZ"].ne(0) &
        canal_venda_norm.isin(ALLOWED_CANAIS_VENDA_COTACOES) &
        (
            atividades_norm.str.contains(r"\bnovo\b", regex=True, na=False) |
            atividades_norm.str.contains(r"\bincremento de linhas\b", regex=True, na=False)
        )
    )
    return df.loc[mask_regra]

@st.cache_data(ttl=3600, show_spinner=False, max_entries=2)
def load_cotacoes_data(
    path: str,
    file_mtime: float | None = None,
    cache_version: str = COTACOES_CACHE_VERSION
) -> pd.DataFrame:
    """Carrega e trata a base de fluxo de vida da cotacao aplicando a regra de novas linhas."""
    _ = (file_mtime, cache_version)
    path_obj = Path(path)
    if not path_obj.exists():
        return pd.DataFrame()

    suffixes = [s.lower() for s in path_obj.suffixes]
    if path_obj.suffix.lower() == ".parquet" or ".csv" in suffixes:
        try:
            df_cot_opt = load_tabular_cached(str(path_obj), file_mtime)
        except Exception:
            return pd.DataFrame()
        if df_cot_opt is None or df_cot_opt.empty:
            return pd.DataFrame()
        colunas_agregadas = {"mes_ano", "CANAL_PLAN", "REGIONAL", "STATUS_ATUAL", "VALOR_NOVAS_LINHAS"}
        if colunas_agregadas.issubset(set(df_cot_opt.columns)):
            if "dat_tratada" not in df_cot_opt.columns:
                df_cot_opt["dat_tratada"] = df_cot_opt["mes_ano"]
            if "DATA_CRIACAO_COTACAO" in df_cot_opt.columns:
                df_cot_opt["DATA_CRIACAO_COTACAO"] = pd.to_datetime(df_cot_opt["DATA_CRIACAO_COTACAO"], errors="coerce")
            else:
                df_cot_opt["DATA_CRIACAO_COTACAO"] = pd.NaT
            if "QTD_COTACOES_UNICAS" not in df_cot_opt.columns:
                df_cot_opt["QTD_COTACOES_UNICAS"] = 0.0
            df_cot_opt["mes_ano"] = df_cot_opt["mes_ano"].astype(str).str.strip()
            df_cot_opt["dat_tratada"] = df_cot_opt["dat_tratada"].astype(str).str.strip()
            df_cot_opt["CANAL_PLAN"] = df_cot_opt["CANAL_PLAN"].astype(str).str.strip()
            df_cot_opt["REGIONAL"] = df_cot_opt["REGIONAL"].astype(str).str.strip().str[:3].str.upper()
            df_cot_opt["STATUS_ATUAL"] = (
                df_cot_opt["STATUS_ATUAL"].astype(str).str.strip().replace({"": "Status nao informado", "nan": "Status nao informado"})
            )
            df_cot_opt["VALOR_NOVAS_LINHAS"] = normalizar_numerico_serie(df_cot_opt["VALOR_NOVAS_LINHAS"]).fillna(0.0)
            df_cot_opt["QTD_COTACOES_UNICAS"] = normalizar_numerico_serie(df_cot_opt["QTD_COTACOES_UNICAS"]).fillna(0.0)
            compactar_colunas_categoricas(
                df_cot_opt,
                ["CANAL_PLAN", "REGIONAL", "STATUS_ATUAL", "mes_ano", "dat_tratada"]
            )
            return df_cot_opt[
                [col for col in [
                    "DATA_CRIACAO_COTACAO", "mes_ano", "dat_tratada", "CANAL_PLAN",
                    "REGIONAL", "STATUS_ATUAL", "VALOR_NOVAS_LINHAS", "QTD_COTACOES_UNICAS"
                ] if col in df_cot_opt.columns]
            ]

    try:
        header_df = _read_excel_with_copy_fallback(path_obj, nrows=0)
    except Exception:
        return pd.DataFrame()

    coluna_cotacao = encontrar_coluna_por_alias(header_df.columns, "FÚNIL FIXA", "COTACAO")
    coluna_data = encontrar_coluna_por_alias(
        header_df.columns,
        "DATA CRIAÇÃO COTAÇÃO",
        "DATA CRIACAO COTACAO"
    )
    coluna_canal_venda = encontrar_coluna_por_alias(header_df.columns, "CANAL DE VENDA")
    coluna_canal = encontrar_coluna_por_alias(
        header_df.columns,
        "CANAL_PLAN",
        "CANAL PLAN",
        "CANAL_TERRITORIO"
    )
    coluna_regional = encontrar_coluna_por_alias(
        header_df.columns,
        "REGIONAL",
        "REGIONAL CRIADOR COTAÇÃO",
        "REGIONAL CRIADOR COTACAO",
        "REGIONAL CLIENTE",
        "REGIONAL_TERRITORIO"
    )
    coluna_status = encontrar_coluna_por_alias(header_df.columns, "STATUS ATUAL")
    coluna_novas_linhas = encontrar_coluna_por_alias(
        header_df.columns,
        "QUANTIDADE NOVAS LINHAS A SEREM ATIVADAS"
    )
    coluna_linhas_voz = encontrar_coluna_por_alias(header_df.columns, "QTD LINHAS VOZ")
    coluna_lista_atividades = encontrar_coluna_por_alias(header_df.columns, "LISTA ATIVIDADES")

    colunas_obrigatorias = [
        coluna_data, coluna_canal_venda, coluna_canal, coluna_regional,
        coluna_novas_linhas, coluna_linhas_voz, coluna_lista_atividades
    ]
    if any(col is None for col in colunas_obrigatorias):
        return pd.DataFrame()

    colunas_leitura = [
        col for col in [
            coluna_cotacao, coluna_data, coluna_canal_venda, coluna_canal, coluna_regional, coluna_status,
            coluna_novas_linhas, coluna_linhas_voz, coluna_lista_atividades
        ] if col
    ]

    try:
        df_cot = _read_excel_with_copy_fallback(path_obj, usecols=colunas_leitura)
    except Exception:
        return pd.DataFrame()

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

    df_cot = df_cot.rename(columns=rename_map)
    if "COTACAO_ID" not in df_cot.columns:
        df_cot["COTACAO_ID"] = ""
    if "STATUS_ATUAL" not in df_cot.columns:
        df_cot["STATUS_ATUAL"] = ""

    df_cot["COTACAO_ID"] = (
        df_cot["COTACAO_ID"]
        .astype(str)
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "NULL": pd.NA})
    )
    df_cot["DATA_CRIACAO_COTACAO"] = pd.to_datetime(
        df_cot["DATA_CRIACAO_COTACAO"],
        errors="coerce"
    )
    df_cot["CANAL_DE_VENDA"] = (
        df_cot["CANAL_DE_VENDA"]
        .astype(str)
        .str.strip()
        .replace({"": "Canal de venda nao informado", "nan": "Canal de venda nao informado"})
    )
    df_cot["CANAL_PLAN"] = (
        df_cot["CANAL_PLAN"]
        .astype(str)
        .str.strip()
        .replace({"": "Canal nao informado", "nan": "Canal nao informado"})
    )
    df_cot["REGIONAL"] = (
        df_cot["REGIONAL"]
        .astype(str)
        .str.strip()
        .str.upper()
        .str[:3]
        .replace({"": "N/I", "NAN": "N/I"})
    )
    df_cot["STATUS_ATUAL"] = df_cot["STATUS_ATUAL"].astype(str).str.strip()
    df_cot["QTD_NOVAS_LINHAS_ATIVAR"] = normalizar_numerico_serie(df_cot["QTD_NOVAS_LINHAS_ATIVAR"]).fillna(0)
    df_cot["QTD_LINHAS_VOZ"] = normalizar_numerico_serie(df_cot["QTD_LINHAS_VOZ"]).fillna(0)
    df_cot["LISTA_ATIVIDADES"] = df_cot["LISTA_ATIVIDADES"].astype(str).str.strip()

    df_cot = _filtrar_regra_cotacoes_novas_linhas(df_cot)
    if df_cot.empty:
        return pd.DataFrame()

    df_cot = df_cot.loc[df_cot["DATA_CRIACAO_COTACAO"].notna()]
    if df_cot.empty:
        return pd.DataFrame()

    meses_pt = {
        1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
        7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez"
    }
    df_cot["mes_ano"] = df_cot["DATA_CRIACAO_COTACAO"].apply(
        lambda dt: f"{meses_pt.get(dt.month, 'jan')}/{dt.strftime('%y')}" if pd.notna(dt) else None
    )
    df_cot["dat_tratada"] = df_cot["mes_ano"]
    compactar_colunas_categoricas(df_cot, ["CANAL_DE_VENDA", "CANAL_PLAN", "REGIONAL", "STATUS_ATUAL", "mes_ano", "dat_tratada"])
    return df_cot

def _agregar_cotacoes_dataframe(df_cot: pd.DataFrame) -> pd.DataFrame:
    """Agrupa a base filtrada de COTAÇÕES para alimentar cards e gráficos."""
    colunas_saida = ["mes_ano", "CANAL_PLAN", "REGIONAL", "STATUS_ATUAL", "VALOR_NOVAS_LINHAS"]
    if df_cot is None or df_cot.empty:
        return pd.DataFrame(columns=colunas_saida)

    if "VALOR_NOVAS_LINHAS" in df_cot.columns and "QTD_NOVAS_LINHAS_ATIVAR" not in df_cot.columns:
        df_agg = df_cot.copy()
        df_agg["STATUS_ATUAL"] = (
            df_agg["STATUS_ATUAL"]
            .astype(str)
            .str.strip()
            .replace({"": "Status nao informado", "nan": "Status nao informado"})
        )
        df_agg["VALOR_NOVAS_LINHAS"] = normalizar_numerico_serie(df_agg["VALOR_NOVAS_LINHAS"]).fillna(0.0)
        df_agg = (
            df_agg.groupby(["mes_ano", "CANAL_PLAN", "REGIONAL", "STATUS_ATUAL"], as_index=False, observed=True)["VALOR_NOVAS_LINHAS"]
            .sum()
        )
        compactar_colunas_categoricas(df_agg, ["mes_ano", "CANAL_PLAN", "REGIONAL", "STATUS_ATUAL"])
        return df_agg[colunas_saida]

    df_agg = (
        df_cot.assign(
            STATUS_ATUAL=(
                df_cot["STATUS_ATUAL"]
                .astype(str)
                .str.strip()
                .replace({"": "Status nao informado", "nan": "Status nao informado"})
            )
        )
        .groupby(["mes_ano", "CANAL_PLAN", "REGIONAL", "STATUS_ATUAL"], as_index=False, observed=True)
        ["QTD_NOVAS_LINHAS_ATIVAR"]
        .sum()
        .rename(columns={"QTD_NOVAS_LINHAS_ATIVAR": "VALOR_NOVAS_LINHAS"})
    )
    compactar_colunas_categoricas(df_agg, ["mes_ano", "CANAL_PLAN", "REGIONAL", "STATUS_ATUAL"])
    return df_agg[colunas_saida]

@st.cache_data(ttl=1800, show_spinner=False, max_entries=2)
def preparar_agregados_cotacoes(
    path: str,
    file_mtime: float | None = None,
    cache_version: str = COTACOES_CACHE_VERSION
) -> pd.DataFrame:
    """Pré-agrega novas linhas de cotações por mês/canal/regional/status para reduzir groupbys."""
    df_cot = load_cotacoes_data(path, file_mtime, cache_version)
    df_agg = _agregar_cotacoes_dataframe(df_cot)
    del df_cot
    gc.collect()
    return df_agg

def diagnosticar_cotacoes_loader(path: str | Path) -> dict:
    """Retorna diagnóstico detalhado da carga de COTAÇÕES para depuração no Cloud."""
    path_obj = Path(path)
    diag = {
        "path": str(path_obj),
        "exists": path_obj.exists(),
        "matched_columns": {},
        "rows_header": 0,
        "rows_raw": 0,
        "rows_regra": 0,
        "rows_validas": 0,
        "rows_final": 0,
        "sum_final": 0.0,
        "error": ""
    }
    if not path_obj.exists():
        diag["error"] = "Arquivo não encontrado."
        return diag

    suffixes = [s.lower() for s in path_obj.suffixes]
    if path_obj.suffix.lower() == ".parquet" or ".csv" in suffixes:
        try:
            df_opt = load_tabular_cached(str(path_obj), path_obj.stat().st_mtime if path_obj.exists() else None)
        except Exception as exc:
            diag["error"] = f"Falha ao ler arquivo otimizado: {exc}"
            return diag
        diag["rows_header"] = int(len(df_opt.columns)) if df_opt is not None else 0
        diag["rows_raw"] = int(len(df_opt)) if df_opt is not None else 0
        diag["rows_regra"] = diag["rows_raw"]
        diag["rows_validas"] = diag["rows_raw"]
        diag["rows_final"] = diag["rows_raw"]
        if df_opt is not None and "VALOR_NOVAS_LINHAS" in df_opt.columns:
            diag["sum_final"] = float(pd.to_numeric(df_opt["VALOR_NOVAS_LINHAS"], errors="coerce").fillna(0).sum())
        diag["matched_columns"] = {col: col for col in getattr(df_opt, "columns", [])}
        return diag

    try:
        header_df = _read_excel_with_copy_fallback(path_obj, nrows=0)
        diag["rows_header"] = int(len(header_df.columns))
    except Exception as exc:
        diag["error"] = f"Falha ao ler cabeçalho: {exc}"
        return diag

    aliases = {
        "COTACAO": ("COTAÇÃO", "COTACAO"),
        "DATA": ("DATA CRIAÇÃO COTAÇÃO", "DATA CRIACAO COTACAO"),
        "CANAL_VENDA": ("CANAL DE VENDA",),
        "CANAL": ("CANAL_PLAN", "CANAL PLAN", "CANAL_TERRITORIO"),
        "REGIONAL": ("REGIONAL", "REGIONAL CRIADOR COTAÇÃO", "REGIONAL CRIADOR COTACAO", "REGIONAL CLIENTE", "REGIONAL_TERRITORIO"),
        "STATUS": ("STATUS ATUAL",),
        "NOVAS_LINHAS": ("QUANTIDADE NOVAS LINHAS A SEREM ATIVADAS",),
        "LINHAS_VOZ": ("QTD LINHAS VOZ",),
        "ATIVIDADES": ("LISTA ATIVIDADES",)
    }
    matched = {chave: encontrar_coluna_por_alias(header_df.columns, *alts) for chave, alts in aliases.items()}
    diag["matched_columns"] = matched
    if any(v is None for k, v in matched.items() if k not in {"STATUS", "COTACAO"}):
        faltantes = [k for k, v in matched.items() if v is None and k not in {"STATUS", "COTACAO"}]
        diag["error"] = f"Colunas obrigatórias não encontradas: {', '.join(faltantes)}"
        return diag

    usecols = [v for v in matched.values() if v]
    try:
        df = _read_excel_with_copy_fallback(path_obj, usecols=usecols)
    except Exception as exc:
        diag["error"] = f"Falha ao ler dados: {exc}"
        return diag

    diag["rows_raw"] = int(len(df))
    rename_map = {
        matched["DATA"]: "DATA_CRIACAO_COTACAO",
        matched["CANAL_VENDA"]: "CANAL_DE_VENDA",
        matched["CANAL"]: "CANAL_PLAN",
        matched["REGIONAL"]: "REGIONAL",
        matched["NOVAS_LINHAS"]: "QTD_NOVAS_LINHAS_ATIVAR",
        matched["LINHAS_VOZ"]: "QTD_LINHAS_VOZ",
        matched["ATIVIDADES"]: "LISTA_ATIVIDADES",
    }
    if matched["COTACAO"]:
        rename_map[matched["COTACAO"]] = "COTACAO_ID"
    if matched["STATUS"]:
        rename_map[matched["STATUS"]] = "STATUS_ATUAL"
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
    df["QTD_NOVAS_LINHAS_ATIVAR"] = normalizar_numerico_serie(df["QTD_NOVAS_LINHAS_ATIVAR"]).fillna(0)
    df["QTD_LINHAS_VOZ"] = normalizar_numerico_serie(df["QTD_LINHAS_VOZ"]).fillna(0)
    df["LISTA_ATIVIDADES"] = df["LISTA_ATIVIDADES"].astype(str).str.strip()

    df_regra = _filtrar_regra_cotacoes_novas_linhas(df)
    diag["rows_regra"] = int(len(df_regra))
    if df_regra.empty:
        return diag

    df_valid = df_regra.loc[df_regra["DATA_CRIACAO_COTACAO"].notna()]
    diag["rows_validas"] = int(len(df_valid))
    if df_valid.empty:
        return diag

    df_final = df_valid.copy()
    diag["rows_final"] = int(len(df_final))
    diag["sum_final"] = float(pd.to_numeric(df_final["QTD_NOVAS_LINHAS_ATIVAR"], errors="coerce").fillna(0).sum())
    return diag

def _formatar_mes_ano_backlog(data_valor) -> str | None:
    """Formata datas do backlog como mmm/aa em PT-BR."""
    if pd.isna(data_valor):
        return None
    data_ts = pd.Timestamp(data_valor)
    meses_pt = {
        1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
        7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez"
    }
    return f"{meses_pt.get(data_ts.month, 'jan')}/{data_ts.strftime('%y')}"

@st.cache_data(ttl=3600, show_spinner=False, max_entries=1)
def load_backlog_consolidado_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    """Carrega o backlog consolidado com os mesmos filtros do notebook de preparo."""
    _ = file_mtime
    path_obj = Path(path)
    if not path_obj.exists():
        return pd.DataFrame()

    suffixes = [s.lower() for s in path_obj.suffixes]
    if path_obj.suffix.lower() == ".parquet" or ".csv" in suffixes:
        try:
            header_opt = load_tabular_cached(str(path_obj), file_mtime, nrows=0)
        except Exception:
            header_opt = pd.DataFrame()
        if {"NM_CANAL_VENDA_SUBGRUPO", "MES_ANO", "QTD_CONTRATOS"}.issubset(set(getattr(header_opt, "columns", []))):
            try:
                df_opt = load_tabular_cached(str(path_obj), file_mtime)
            except Exception:
                df_opt = pd.DataFrame()
        else:
            df_opt = pd.DataFrame()
        if not df_opt.empty and {"NM_CANAL_VENDA_SUBGRUPO", "MES_ANO", "QTD_CONTRATOS"}.issubset(set(df_opt.columns)):
            if "NM_REGIONAL" not in df_opt.columns:
                df_opt["NM_REGIONAL"] = "Não Informado"
            if "NOME_OS_TIPO_STATUS_AGENDA" not in df_opt.columns:
                df_opt["NOME_OS_TIPO_STATUS_AGENDA"] = "Não Informado"
            df_opt["NM_REGIONAL"] = (
                df_opt["NM_REGIONAL"].astype("string").str.strip().replace({"": "Não Informado", "nan": "Não Informado"})
            )
            df_opt["NM_CANAL_VENDA_SUBGRUPO"] = df_opt["NM_CANAL_VENDA_SUBGRUPO"].astype("string").str.strip()
            df_opt["MES_ANO"] = df_opt["MES_ANO"].astype("string").str.strip()
            df_opt["NOME_OS_TIPO_STATUS_AGENDA"] = (
                df_opt["NOME_OS_TIPO_STATUS_AGENDA"]
                .astype("string")
                .str.strip()
                .replace({"": "Não Informado", "nan": "Não Informado"})
            )
            df_opt["QTD_CONTRATOS"] = normalizar_numerico_serie(df_opt["QTD_CONTRATOS"]).fillna(0.0)
            df_opt = df_opt[["NM_REGIONAL", "NM_CANAL_VENDA_SUBGRUPO", "MES_ANO", "QTD_CONTRATOS", "NOME_OS_TIPO_STATUS_AGENDA"]]
            compactar_colunas_categoricas(df_opt, ["NM_REGIONAL", "NM_CANAL_VENDA_SUBGRUPO", "MES_ANO", "NOME_OS_TIPO_STATUS_AGENDA"])
            return df_opt

    usecols = [
        "SK_DATA",
        "NR_CONTRATO",
        "NM_VISAO_ANALISE",
        "NM_REGIONAL",
        "NM_CANAL_VENDA_SUBGRUPO",
        "NOME_OS_TIPO_STATUS_AGENDA",
        "DT_AGENDA_ORDEM_SERVICO",
    ]
    try:
        header_raw = pd.read_csv(path_obj, nrows=0)
    except UnicodeDecodeError:
        header_raw = pd.read_csv(path_obj, encoding="latin-1", nrows=0)
    except Exception:
        header_raw = pd.DataFrame()
    colunas_disponiveis_backlog = set(getattr(header_raw, "columns", []))
    usecols = [col for col in usecols if col in colunas_disponiveis_backlog]
    colunas_obrigatorias_backlog = {"NR_CONTRATO", "NM_VISAO_ANALISE", "NM_REGIONAL", "NM_CANAL_VENDA_SUBGRUPO"}
    if not colunas_obrigatorias_backlog.issubset(set(usecols)) or not ({"SK_DATA", "DT_AGENDA_ORDEM_SERVICO"} & set(usecols)):
        return pd.DataFrame()

    read_kwargs = {
        "usecols": usecols,
        "low_memory": False,
        "dtype": {
            "SK_DATA": "string",
            "NR_CONTRATO": "string",
            "NM_VISAO_ANALISE": "string",
            "NM_REGIONAL": "string",
            "NM_CANAL_VENDA_SUBGRUPO": "string",
            "NOME_OS_TIPO_STATUS_AGENDA": "string",
            "DT_AGENDA_ORDEM_SERVICO": "string",
        }
    }
    try:
        df = pd.read_csv(path_obj, **read_kwargs)
    except UnicodeDecodeError:
        df = pd.read_csv(path_obj, encoding="latin-1", **read_kwargs)
    except Exception:
        return pd.DataFrame()

    for coluna in [
        "SK_DATA",
        "NR_CONTRATO",
        "NM_VISAO_ANALISE",
        "NM_REGIONAL",
        "NM_CANAL_VENDA_SUBGRUPO",
        "NOME_OS_TIPO_STATUS_AGENDA",
        "DT_AGENDA_ORDEM_SERVICO",
    ]:
        if coluna in df.columns:
            df[coluna] = df[coluna].astype("string").str.strip()

    canais_permitidos_norm = {normalizar_chave_visual(v) for v in BACKLOG_CANAIS_PERMITIDOS}
    canais_backlog_norm = df["NM_CANAL_VENDA_SUBGRUPO"].map(normalizar_chave_visual)
    filtro = (
        df["NM_VISAO_ANALISE"].map(normalizar_chave_visual).eq(normalizar_chave_visual("Novos Domicilios")) &
        canais_backlog_norm.isin(canais_permitidos_norm)
    )
    df = df.loc[filtro]
    if df.empty:
        return pd.DataFrame()

    df["NM_CANAL_VENDA_SUBGRUPO"] = (
        canais_backlog_norm.loc[df.index]
        .map(BACKLOG_MAPEAMENTO_CANAIS_NORM)
        .fillna(df["NM_CANAL_VENDA_SUBGRUPO"].astype("string").str.strip())
        .astype("string")
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA})
    )
    df["NR_CONTRATO"] = (
        df["NR_CONTRATO"]
        .astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "NULL": pd.NA})
    )
    df["NM_REGIONAL"] = (
        df["NM_REGIONAL"]
        .astype("string")
        .str.strip()
        .replace({"": "Não Informado", "nan": "Não Informado"})
    )
    if "NOME_OS_TIPO_STATUS_AGENDA" not in df.columns:
        df["NOME_OS_TIPO_STATUS_AGENDA"] = "Não Informado"
    df["NOME_OS_TIPO_STATUS_AGENDA"] = (
        df["NOME_OS_TIPO_STATUS_AGENDA"]
        .astype("string")
        .str.strip()
        .replace({"": "Não Informado", "nan": "Não Informado", "None": "Não Informado", "NULL": "Não Informado"})
    )
    data_sk = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    if "SK_DATA" in df.columns:
        sk_data_norm = (
            df["SK_DATA"]
            .astype("string")
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
            .str.zfill(8)
        )
        data_sk = pd.to_datetime(sk_data_norm, format="%Y%m%d", errors="coerce")

    data_agenda = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    if "DT_AGENDA_ORDEM_SERVICO" in df.columns:
        data_agenda = pd.to_datetime(df["DT_AGENDA_ORDEM_SERVICO"], errors="coerce")

    df["DATA_BACKLOG_REF"] = data_sk.combine_first(data_agenda)
    df = df.loc[
        df["NM_CANAL_VENDA_SUBGRUPO"].notna() &
        df["NR_CONTRATO"].notna() &
        df["DATA_BACKLOG_REF"].notna()
    ]
    if df.empty:
        return pd.DataFrame()

    df["MES_ANO"] = df["DATA_BACKLOG_REF"].map(_formatar_mes_ano_backlog)
    df = df.loc[
        df["MES_ANO"].notna(),
        ["NM_REGIONAL", "NM_CANAL_VENDA_SUBGRUPO", "MES_ANO", "NR_CONTRATO", "NOME_OS_TIPO_STATUS_AGENDA"]
    ]
    compactar_colunas_categoricas(df, ["NM_REGIONAL", "NM_CANAL_VENDA_SUBGRUPO", "MES_ANO", "NOME_OS_TIPO_STATUS_AGENDA"])
    return df


@st.cache_data(ttl=3600, show_spinner=False, max_entries=2)
def load_pedidos_dashboard_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    """Carrega a base otimizada de pedidos E-Commerce já consolidada por mês/regional/canal."""
    _ = file_mtime
    path_obj = Path(path)
    if not path_obj.exists():
        return pd.DataFrame()

    try:
        df_ped = load_tabular_cached(str(path_obj), file_mtime)
    except Exception:
        return pd.DataFrame()

    colunas_minimas = {
        "DAT_MOVIMENTO2",
        "dat_tratada",
        "REGIONAL",
        "CANAL_PLAN",
        "COD_PLATAFORMA",
        "DSC_INDICADOR",
        "QTDE",
        "DESAFIO_QTD",
        "TEND_QTD",
    }
    if not colunas_minimas.issubset(set(df_ped.columns)):
        return pd.DataFrame()

    df_ped["DAT_MOVIMENTO2"] = pd.to_datetime(df_ped["DAT_MOVIMENTO2"], errors="coerce")
    df_ped = df_ped[df_ped["DAT_MOVIMENTO2"].notna()].copy()
    if df_ped.empty:
        return pd.DataFrame()

    if "ID_AFILIADOS" not in df_ped.columns:
        df_ped["ID_AFILIADOS"] = ""
    if "ORIGEM_AFILIADOS" not in df_ped.columns:
        df_ped["ORIGEM_AFILIADOS"] = "N/D"

    df_ped["dat_tratada"] = df_ped["dat_tratada"].astype(str).str.strip()
    df_ped["REGIONAL"] = df_ped["REGIONAL"].astype(str).str.strip().str[:3].str.upper()
    df_ped["CANAL_PLAN"] = df_ped["CANAL_PLAN"].astype(str).str.strip()
    df_ped["COD_PLATAFORMA"] = df_ped["COD_PLATAFORMA"].astype(str).str.strip()
    df_ped["DSC_INDICADOR"] = df_ped["DSC_INDICADOR"].astype(str).str.strip()
    df_ped["ID_AFILIADOS"] = df_ped["ID_AFILIADOS"].astype(str).str.strip()
    df_ped["ORIGEM_AFILIADOS"] = (
        df_ped["ORIGEM_AFILIADOS"]
        .astype(str)
        .str.strip()
        .replace({"": "N/D", "nan": "N/D", "None": "N/D", "NONE": "N/D"})
    )
    for coluna_num in ["QTDE", "DESAFIO_QTD", "TEND_QTD"]:
        df_ped[coluna_num] = normalizar_numerico_serie(df_ped[coluna_num]).fillna(0.0)

    compactar_colunas_categoricas(
        df_ped,
        ["dat_tratada", "REGIONAL", "CANAL_PLAN", "COD_PLATAFORMA", "DSC_INDICADOR", "ID_AFILIADOS", "ORIGEM_AFILIADOS"]
    )
    return df_ped

def montar_tabela_backlog_canais(
    df_backlog: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Monta a tabela canal x mês do backlog com linha total no topo."""
    colunas_vazias = ["CANAL"]
    if df_backlog is None or df_backlog.empty:
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    meses_disponiveis = sorted(
        df_backlog["MES_ANO"].dropna().astype(str).unique().tolist(),
        key=mes_ano_para_data
    )
    if not meses_disponiveis:
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)
    mes_corrente_backlog = get_mes_atual_formatado().strip().lower()
    if mes_corrente_backlog not in [str(m).strip().lower() for m in meses_disponiveis]:
        try:
            if pd.Timestamp(mes_ano_para_data(mes_corrente_backlog)) >= pd.Timestamp(mes_ano_para_data(str(meses_disponiveis[-1]))):
                meses_disponiveis.append(mes_corrente_backlog)
        except Exception:
            pass
    mes_foco_backlog = (
        mes_corrente_backlog
        if mes_corrente_backlog in [str(m).strip().lower() for m in meses_disponiveis]
        else str(meses_disponiveis[-1]).strip().lower()
    )
    meses_ordem = obter_janela_meses_disponiveis(mes_foco_backlog, meses_disponiveis, qtd_meses=13)
    if not meses_ordem:
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    if "QTD_CONTRATOS" in df_backlog.columns:
        mapa_canal_mes_completo = (
            df_backlog.groupby(["NM_CANAL_VENDA_SUBGRUPO", "MES_ANO"], observed=True)["QTD_CONTRATOS"]
            .sum()
            .astype(float)
            .to_dict()
        )
        totais_mes_completo = (
            df_backlog.groupby("MES_ANO", observed=True)["QTD_CONTRATOS"]
            .sum()
            .astype(float)
            .to_dict()
        )
        tabela = pd.pivot_table(
            df_backlog,
            index="NM_CANAL_VENDA_SUBGRUPO",
            columns="MES_ANO",
            values="QTD_CONTRATOS",
            aggfunc="sum",
            fill_value=0
        ).reindex(columns=meses_ordem, fill_value=0)
        totais_mes = (
            df_backlog.groupby("MES_ANO", observed=True)["QTD_CONTRATOS"]
            .sum()
            .reindex(meses_ordem, fill_value=0)
        )
    else:
        mapa_canal_mes_completo = (
            df_backlog.groupby(["NM_CANAL_VENDA_SUBGRUPO", "MES_ANO"], observed=True)["NR_CONTRATO"]
            .nunique()
            .astype(float)
            .to_dict()
        )
        totais_mes_completo = (
            df_backlog.groupby("MES_ANO", observed=True)["NR_CONTRATO"]
            .nunique()
            .astype(float)
            .to_dict()
        )
        tabela = pd.pivot_table(
            df_backlog,
            index="NM_CANAL_VENDA_SUBGRUPO",
            columns="MES_ANO",
            values="NR_CONTRATO",
            aggfunc=pd.Series.nunique,
            fill_value=0
        ).reindex(columns=meses_ordem, fill_value=0)
        totais_mes = (
            df_backlog.groupby("MES_ANO", observed=True)["NR_CONTRATO"]
            .nunique()
            .reindex(meses_ordem, fill_value=0)
        )

    if tabela.empty:
        return pd.DataFrame(columns=["CANAL", *meses_ordem]), pd.DataFrame(columns=["CANAL", *meses_ordem])

    tabela = tabela.assign(_ordem=tabela.sum(axis=1)).sort_values("_ordem", ascending=False).drop(columns="_ordem")

    df_num = tabela.reset_index().rename(columns={"NM_CANAL_VENDA_SUBGRUPO": "CANAL"})
    linha_total = {"CANAL": "TOTAL"}
    for mes in meses_ordem:
        linha_total[mes] = float(totais_mes.get(mes, 0))
    df_num = pd.concat([pd.DataFrame([linha_total]), df_num], ignore_index=True)

    mes_m1_backlog = get_mes_anterior(mes_foco_backlog)
    for idx_linha, row in df_num.iterrows():
        canal = str(row.get("CANAL", "")).strip()
        if canal.upper() == "TOTAL":
            lookup_real = {
                str(mes_key).strip().lower(): float(valor or 0.0)
                for mes_key, valor in totais_mes_completo.items()
            }
        else:
            lookup_real = {
                str(mes_key).strip().lower(): float(valor or 0.0)
                for (canal_key, mes_key), valor in mapa_canal_mes_completo.items()
                if str(canal_key).strip() == canal
            }
        valor_foco = float(lookup_real.get(mes_foco_backlog, 0.0))
        valor_m1 = float(lookup_real.get(mes_m1_backlog, 0.0))
        metricas_backlog = calcular_yoy_ytd_mensal_lookup(
            lookup_real,
            {},
            mes_foco_backlog
        )
        df_num.loc[idx_linha, "MoM"] = calcular_variacao_percentual(valor_foco, valor_m1)
        df_num.loc[idx_linha, "YoY"] = metricas_backlog["YOY"]
        df_num.loc[idx_linha, "YTD25"] = metricas_backlog["YTD25"]
        df_num.loc[idx_linha, "YTD26"] = metricas_backlog["YTD26"]
        df_num.loc[idx_linha, "YTD_ORÇ"] = metricas_backlog["YTD_ORÇ"]
        df_num.loc[idx_linha, "YTD26 vs YTD25"] = metricas_backlog["YTD26 vs YTD25"]
        df_num.loc[idx_linha, "YTD26 vs YTD_ORÇ"] = metricas_backlog["YTD26 vs YTD_ORÇ"]

    df_fmt = df_num.copy().astype(object)
    for col in df_fmt.columns:
        if col == "CANAL":
            continue
        if col in {"MoM", "YoY", "YTD26 vs YTD25", "YTD26 vs YTD_ORÇ"}:
            df_fmt[col] = pd.to_numeric(df_fmt[col], errors="coerce").fillna(0).apply(
                lambda valor: f"{float(valor):+.1f}%".replace(".", ",")
            )
        else:
            df_fmt[col] = pd.to_numeric(df_fmt[col], errors="coerce").fillna(0).apply(
                lambda valor: formatar_numero_brasileiro(valor, 0)
            )
    return df_fmt, df_num

def _interpolar_cores_hex(cor_inicio: str, cor_fim: str, qtd: int) -> list[str]:
    """Gera escala linear entre duas cores hexadecimais."""
    if qtd <= 0:
        return []
    if qtd == 1:
        return [cor_fim]

    def _hex_para_rgb(cor: str) -> tuple[int, int, int]:
        cor_limpa = str(cor).strip().lstrip("#")
        return tuple(int(cor_limpa[i:i + 2], 16) for i in (0, 2, 4))

    rgb_inicio = _hex_para_rgb(cor_inicio)
    rgb_fim = _hex_para_rgb(cor_fim)
    cores = []
    for idx in range(qtd):
        peso = idx / max(qtd - 1, 1)
        rgb = tuple(
            int(round(rgb_inicio[pos] + (rgb_fim[pos] - rgb_inicio[pos]) * peso))
            for pos in range(3)
        )
        cores.append("#" + "".join(f"{valor:02X}" for valor in rgb))
    return cores

def criar_grafico_cascata_backlog_status(
    df_backlog: pd.DataFrame,
    mes_ref: str,
    canal_ref: str = "Todos",
    regional_ref: str = "Todos"
) -> go.Figure:
    """Cria cascata de distribuição do backlog por status de agenda."""
    fig_vazia = go.Figure()
    col_status = "NOME_OS_TIPO_STATUS_AGENDA"
    col_canal = "NM_CANAL_VENDA_SUBGRUPO"
    col_regional = "NM_REGIONAL"
    col_mes = "MES_ANO"
    col_contrato = "NR_CONTRATO"
    col_qtd = "QTD_CONTRATOS"

    if df_backlog is None or df_backlog.empty or col_status not in df_backlog.columns:
        return fig_vazia

    base = df_backlog.copy()
    base[col_mes] = base[col_mes].astype(str).str.strip().str.lower()
    base[col_status] = (
        base[col_status]
        .astype("string")
        .str.strip()
        .replace({"": "Não Informado", "nan": "Não Informado", "None": "Não Informado", "NULL": "Não Informado"})
    )
    base = base[base[col_mes].eq(str(mes_ref).strip().lower())]
    if canal_ref and str(canal_ref).strip().lower() != "todos" and col_canal in base.columns:
        base = base[base[col_canal].astype(str).str.strip().eq(str(canal_ref).strip())]
    if regional_ref and str(regional_ref).strip().lower() != "todos" and col_regional in base.columns:
        base = base[base[col_regional].astype(str).str.strip().eq(str(regional_ref).strip())]
    if base.empty:
        return fig_vazia

    if col_qtd in base.columns and col_contrato not in base.columns:
        serie_status = (
            base.groupby(col_status, observed=True)[col_qtd]
            .sum()
            .sort_values(ascending=False)
        )
    else:
        serie_status = (
            base.groupby(col_status, observed=True)[col_contrato]
            .nunique()
            .sort_values(ascending=False)
        )

    serie_status = serie_status[serie_status.gt(0)]
    if serie_status.empty:
        return fig_vazia

    labels = [str(label) for label in serie_status.index.tolist()]
    valores = [float(valor) for valor in serie_status.tolist()]
    total = float(sum(valores))
    labels_plot = [*labels, "TOTAL"]
    valores_plot = [*valores, total]
    bases_plot = [0.0, *np.cumsum(valores).astype(float).tolist()[:-1], 0.0]
    textos = [formatar_numero_brasileiro(valor, 0) for valor in valores]
    textos.append(formatar_numero_brasileiro(total, 0))
    cores = [*_interpolar_cores_hex("#F9D6D3", "#8D1A12", len(labels)), "#4B5563"]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=labels_plot,
            y=valores_plot,
            base=bases_plot,
            text=textos,
            textposition="outside",
            marker=dict(
                color=cores,
                line=dict(color="rgba(255,255,255,0.92)", width=1.1),
            ),
            customdata=textos,
            cliponaxis=False,
            hovertemplate=(
                "<b>Status:</b> %{x}<br>"
                "<b>Contratos:</b> %{customdata}<extra></extra>"
            ),
            showlegend=False,
        )
    )
    acumulado_status = np.cumsum(valores).astype(float).tolist()
    if len(labels) > 1:
        fig.add_trace(
            go.Scatter(
                x=labels,
                y=acumulado_status,
                mode="lines",
                line=dict(color="rgba(121,14,9,0.30)", width=1.2, dash="dot"),
                hoverinfo="skip",
                showlegend=False,
            )
        )
    fig.add_shape(
        type="line",
        xref="paper",
        yref="y",
        x0=0,
        x1=1,
        y0=total,
        y1=total,
        line=dict(color="rgba(75,85,99,0.18)", width=1, dash="dot"),
    )
    fig.update_layout(
        height=460,
        showlegend=False,
        plot_bgcolor="white",
        paper_bgcolor="#FCFCFD",
        font=dict(family="Manrope, Segoe UI, sans-serif", size=12, color="#2F3747"),
        margin=dict(l=18, r=18, t=32, b=74),
        yaxis=dict(
            title="",
            showgrid=True,
            gridcolor="rgba(230,236,244,0.88)",
            zeroline=False,
            tickfont=dict(size=11, color="#5B6578"),
        ),
        xaxis=dict(
            title="",
            tickangle=-18,
            tickfont=dict(size=11, color="#374151"),
            showgrid=False,
        ),
        bargap=0.28,
    )
    fig.update_traces(textfont=dict(size=12, color="#1F2937", family="Manrope, Segoe UI, sans-serif"))
    return fig

def montar_tabela_cotacoes_canais_mensal(
    df_cotacoes: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Monta a tabela canal x mês de COTAÇÕES somando novas linhas por canal."""
    colunas_vazias = ["CANAL"]
    colunas_necessarias = {"CANAL_PLAN", "mes_ano", "VALOR_NOVAS_LINHAS"}
    if (
        df_cotacoes is None or
        df_cotacoes.empty or
        not colunas_necessarias.issubset(set(df_cotacoes.columns))
    ):
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    df_base = df_cotacoes.loc[
        df_cotacoes["CANAL_PLAN"].notna() &
        df_cotacoes["mes_ano"].notna()
    ].copy()
    if df_base.empty:
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    df_base["CANAL_PLAN"] = df_base["CANAL_PLAN"].astype("string").str.strip()
    df_base["mes_ano"] = df_base["mes_ano"].astype("string").str.strip()
    df_base["VALOR_NOVAS_LINHAS"] = normalizar_numerico_serie(df_base["VALOR_NOVAS_LINHAS"]).fillna(0.0)

    meses_ordem = sorted(
        df_base["mes_ano"].dropna().astype(str).unique().tolist(),
        key=mes_ano_para_data
    )
    if not meses_ordem:
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)
    mes_foco_cot = (
        get_mes_atual_formatado().strip().lower()
        if get_mes_atual_formatado().strip().lower() in [str(m).strip().lower() for m in meses_ordem]
        else str(meses_ordem[-1]).strip().lower()
    )
    meses_ordem = obter_janela_meses_disponiveis(mes_foco_cot, meses_ordem, qtd_meses=13)
    if not meses_ordem:
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    tabela = pd.pivot_table(
        df_base,
        index="CANAL_PLAN",
        columns="mes_ano",
        values="VALOR_NOVAS_LINHAS",
        aggfunc="sum",
        fill_value=0
    ).reindex(columns=meses_ordem, fill_value=0)

    if tabela.empty:
        return pd.DataFrame(columns=["CANAL", *meses_ordem]), pd.DataFrame(columns=["CANAL", *meses_ordem])

    canais_ordenados = [canal for canal in COTACOES_ORDEM_CANAIS if canal in tabela.index]
    canais_restantes = [canal for canal in tabela.index.tolist() if canal not in canais_ordenados]
    tabela = tabela.reindex([*canais_ordenados, *sorted(canais_restantes)], fill_value=0)

    totais_mes = (
        df_base.groupby("mes_ano", observed=True)["VALOR_NOVAS_LINHAS"]
        .sum()
        .reindex(meses_ordem, fill_value=0)
    )

    df_num = tabela.reset_index().rename(columns={"CANAL_PLAN": "CANAL"})
    linha_total = {"CANAL": "TOTAL"}
    for mes in meses_ordem:
        linha_total[mes] = float(totais_mes.get(mes, 0))
    df_num = pd.concat([pd.DataFrame([linha_total]), df_num], ignore_index=True)

    df_fmt = df_num.copy().astype(object)
    for col in df_fmt.columns:
        if col == "CANAL":
            continue
        df_fmt[col] = pd.to_numeric(df_fmt[col], errors="coerce").fillna(0).apply(
            lambda valor: formatar_numero_brasileiro(valor, 0)
        )
    return df_fmt, df_num

def _normalizar_texto_funil_cotacoes(valor) -> str:
    """Normaliza texto para comparacoes robustas na tabela de funil de COTACOES."""
    if pd.isna(valor):
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = texto.encode("ASCII", "ignore").decode("ASCII")
    texto = texto.strip().upper()
    texto = re.sub(r"[^A-Z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()

def _mapear_canal_funil_cotacoes(valor_canal) -> str:
    """Converte canais da base principal/cotacoes para a taxonomia exibida na aba de COTACOES."""
    texto = _normalizar_texto_funil_cotacoes(valor_canal)
    if not texto:
        return ""
    if "TELEVENDAS ATIVO" in texto:
        return "Televendas Ativo"
    if "TELEVENDAS RECEPTIVO" in texto or texto == "RECEPTIVO":
        return "Televendas Receptivo"
    if "S2S" in texto or "DAC" in texto:
        return "S2S+DAC"
    if "E COMMERCE" in texto:
        return "E-Commerce"
    if texto in CANAL_CONSULTIVO_REMOTO_ALIASES:
        return "Consultivo Remoto"
    if "HOSPITALITY" in texto:
        return "Hospitality"
    return ""

def _ordenar_canais_funil_cotacoes(canais: list[str]) -> list[str]:
    """Ordena canais no padrão executivo da aba de COTACOES."""
    canais_validos = [str(canal).strip() for canal in canais if str(canal).strip()]
    ordenados = [canal for canal in COTACOES_ORDEM_CANAIS if canal in canais_validos]
    for canal in sorted(canais_validos):
        if canal not in ordenados:
            ordenados.append(canal)
    return ordenados

def _projetar_cotacoes_mes_util(df_cotacoes_mes: pd.DataFrame, mes_ref: str) -> float:
    """Projeta o fechamento do mes de cotacoes pelo ritmo de dias uteis transcorridos."""
    if df_cotacoes_mes is None or df_cotacoes_mes.empty:
        return 0.0

    realizado = float(pd.to_numeric(df_cotacoes_mes.get("VALOR_COTACAO", 0), errors="coerce").fillna(0).sum())
    if realizado <= 0:
        return 0.0

    datas_validas = pd.to_datetime(df_cotacoes_mes.get("DATA_CRIACAO_COTACAO"), errors="coerce").dropna()
    if datas_validas.empty:
        return realizado

    try:
        inicio_mes = pd.Timestamp(mes_ano_para_data(str(mes_ref))).normalize()
    except Exception:
        return realizado

    fim_mes = (inicio_mes + pd.offsets.MonthEnd(0)).normalize()
    data_corte = min(pd.Timestamp(datas_validas.max()).normalize(), fim_mes)

    dias_uteis_decorridos = len(pd.bdate_range(start=inicio_mes, end=data_corte))
    dias_uteis_totais = len(pd.bdate_range(start=inicio_mes, end=fim_mes))
    if dias_uteis_decorridos <= 0 or dias_uteis_totais <= 0:
        return realizado

    fator = float(dias_uteis_totais) / float(dias_uteis_decorridos)
    return float(max(realizado * fator, realizado))

def montar_tabela_funil_cotacoes(
    df_base_principal: pd.DataFrame,
    df_cotacoes_base: pd.DataFrame,
    canal_ref: str = "Todos",
    mes_ref: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Monta tabela de funil na aba de COTACOES com linhas:
    PEDIDOS, COTACAO, ATIVAÇÃO e taxas de conversao.
    """
    mes_coluna_vazia = str((mes_ref or get_mes_atual_formatado())).strip().upper()
    colunas_vazias = ["ETAPA", mes_coluna_vazia, "MoM", "YoY", "YTD25", "YTD26", "YTD_ORÇ", "YTD26 vs YTD25", "YTD26 vs YTD_ORÇ"]
    if (
        (df_base_principal is None or df_base_principal.empty) and
        (df_cotacoes_base is None or df_cotacoes_base.empty)
    ):
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    meses_union: list[str] = []
    if df_base_principal is not None and not df_base_principal.empty and "dat_tratada" in df_base_principal.columns:
        meses_union.extend(
            df_base_principal["dat_tratada"].dropna().astype(str).str.strip().str.lower().tolist()
        )
    if df_cotacoes_base is not None and not df_cotacoes_base.empty:
        col_mes_cot = "mes_ano" if "mes_ano" in df_cotacoes_base.columns else "dat_tratada"
        if col_mes_cot in df_cotacoes_base.columns:
            meses_union.extend(
                df_cotacoes_base[col_mes_cot].dropna().astype(str).str.strip().str.lower().tolist()
            )

    meses_validos = sorted(
        [mes for mes in set(meses_union) if re.match(r"^[a-z]{3}/\d{2}$", str(mes).strip(), flags=re.IGNORECASE)],
        key=mes_ano_para_data
    )
    mes_corrente = get_mes_atual_formatado().strip().lower()
    mes_foco = str(mes_ref or "").strip().lower()
    if not mes_foco:
        mes_foco = mes_corrente if mes_corrente in meses_validos else (meses_validos[-1] if meses_validos else mes_corrente)
    elif mes_foco not in meses_validos and meses_validos:
        mes_foco = meses_validos[-1]

    def _gerar_intervalo_meses(mes_final: str, qtd_meses: int = 13) -> list[str]:
        try:
            data_final = pd.Timestamp(mes_ano_para_data(str(mes_final))).normalize()
        except Exception:
            return [str(mes_final).strip().lower()]
        intervalo = []
        for deslocamento in range(max(int(qtd_meses), 1) - 1, -1, -1):
            data_ref = data_final - pd.DateOffset(months=deslocamento)
            intervalo.append(_formatar_mes_ano_backlog(data_ref).lower())
        return intervalo

    mes_m1 = get_mes_anterior(mes_foco)
    usar_tend_mes_foco = mes_foco == mes_corrente
    meses_serie = _gerar_intervalo_meses(mes_foco, qtd_meses=13)
    colunas_meses: list[str] = []
    mapa_coluna_mes: dict[str, str] = {}
    for mes_item in meses_serie:
        label_mes = str(mes_item).strip().upper()
        if usar_tend_mes_foco and str(mes_item).strip().lower() == str(mes_foco).strip().lower():
            label_mes = f"{label_mes} (TEND.)"
        colunas_meses.append(label_mes)
        mapa_coluna_mes[str(mes_item).strip().lower()] = label_mes
    colunas_saida = ["ETAPA", *colunas_meses, "MoM", "YoY", "YTD25", "YTD26", "YTD_ORÇ", "YTD26 vs YTD25", "YTD26 vs YTD_ORÇ"]

    canal_escolhido = str(canal_ref or "Todos").strip()
    canais_alvo = None if canal_escolhido.lower() == "todos" else {canal_escolhido}
    canal_funil_escolhido = _mapear_canal_funil_cotacoes(canal_escolhido)
    exibir_linha_entrada = canal_escolhido.lower() == "todos" or canal_funil_escolhido in {"E-Commerce", "Televendas Receptivo"}
    usar_ligacoes_como_entrada = canal_funil_escolhido == "Televendas Receptivo"
    label_entrada = "LIGAÇÕES" if usar_ligacoes_como_entrada else "PEDIDOS"
    label_conv_1 = "COT. VS LIG" if usar_ligacoes_como_entrada else "PED. VS COT"
    label_saida = "ATIVAÇÃO"
    label_conv_2 = "ATIV. VS COTAÇÃO"

    def _normalizar_plataforma_funil(valor_plataforma) -> str:
        texto = _normalizar_texto_funil_cotacoes(valor_plataforma)
        if "FIXA" in texto:
            return "FIXA"
        if "CONTA" in texto or "MOVEL" in texto or "MOBILE" in texto:
            return "CONTA"
        return texto

    df_principal = pd.DataFrame()
    if df_base_principal is not None and not df_base_principal.empty:
        colunas_minimas = {"CANAL_PLAN", "COD_PLATAFORMA", "DSC_INDICADOR", "dat_tratada", "QTDE", "DESAFIO_QTD", "TEND_QTD"}
        if colunas_minimas.issubset(set(df_base_principal.columns)):
            df_principal = df_base_principal[
                ["CANAL_PLAN", "COD_PLATAFORMA", "DSC_INDICADOR", "dat_tratada", "QTDE", "DESAFIO_QTD", "TEND_QTD"]
            ].copy()
            df_principal["MES_NORM"] = df_principal["dat_tratada"].astype(str).str.strip().str.lower()
            df_principal["CANAL_FUNIL"] = df_principal["CANAL_PLAN"].apply(_mapear_canal_funil_cotacoes)
            df_principal["PLATAFORMA_NORM"] = df_principal["COD_PLATAFORMA"].apply(_normalizar_plataforma_funil)
            df_principal["IND_NORM"] = df_principal["DSC_INDICADOR"].apply(_normalizar_texto_funil_cotacoes)
            for coluna_num in ["QTDE", "DESAFIO_QTD", "TEND_QTD"]:
                df_principal[coluna_num] = normalizar_numerico_serie(df_principal[coluna_num]).fillna(0.0)
            df_principal = df_principal[
                df_principal["CANAL_FUNIL"].isin(COTACOES_ORDEM_CANAIS) &
                df_principal["MES_NORM"].str.match(r"^[a-z]{3}/\d{2}$", na=False)
            ].copy()
            if canais_alvo:
                df_principal = df_principal[df_principal["CANAL_FUNIL"].isin(canais_alvo)].copy()

    df_cot = pd.DataFrame()
    if df_cotacoes_base is not None and not df_cotacoes_base.empty:
        col_mes_cot = "mes_ano" if "mes_ano" in df_cotacoes_base.columns else "dat_tratada"
        col_valor_cot = "QTD_NOVAS_LINHAS_ATIVAR" if "QTD_NOVAS_LINHAS_ATIVAR" in df_cotacoes_base.columns else (
            "VALOR_NOVAS_LINHAS" if "VALOR_NOVAS_LINHAS" in df_cotacoes_base.columns else None
        )
        if col_mes_cot in df_cotacoes_base.columns and col_valor_cot in df_cotacoes_base.columns:
            colunas_cot = ["CANAL_PLAN", col_mes_cot, col_valor_cot]
            if "DATA_CRIACAO_COTACAO" in df_cotacoes_base.columns:
                colunas_cot.append("DATA_CRIACAO_COTACAO")
            df_cot = df_cotacoes_base[colunas_cot].copy()
            df_cot["MES_NORM"] = df_cot[col_mes_cot].astype(str).str.strip().str.lower()
            df_cot["CANAL_FUNIL"] = df_cot["CANAL_PLAN"].apply(_mapear_canal_funil_cotacoes)
            df_cot["VALOR_COTACAO"] = normalizar_numerico_serie(df_cot[col_valor_cot]).fillna(0.0)
            if "DATA_CRIACAO_COTACAO" in df_cot.columns:
                df_cot["DATA_CRIACAO_COTACAO"] = pd.to_datetime(df_cot["DATA_CRIACAO_COTACAO"], errors="coerce")
            else:
                df_cot["DATA_CRIACAO_COTACAO"] = pd.NaT
            df_cot = df_cot[
                df_cot["CANAL_FUNIL"].isin(COTACOES_ORDEM_CANAIS) &
                df_cot["MES_NORM"].str.match(r"^[a-z]{3}/\d{2}$", na=False)
            ].copy()
            if canais_alvo:
                df_cot = df_cot[df_cot["CANAL_FUNIL"].isin(canais_alvo)].copy()

    def _pct_delta(valor_atual: float, valor_base: float) -> float:
        base = float(valor_base or 0)
        if base <= 0:
            return np.nan
        return ((float(valor_atual or 0) / base) - 1.0) * 100.0

    def _ratio_pct(valor_numerador: float, valor_denominador: float) -> float:
        denominador = float(valor_denominador or 0)
        if denominador <= 0:
            return np.nan
        return (float(valor_numerador or 0) / denominador) * 100.0

    def _pp_delta(valor_atual: float, valor_base: float) -> float:
        if pd.isna(valor_atual) or pd.isna(valor_base):
            return np.nan
        return float(valor_atual) - float(valor_base)

    def _fmt_num(valor: float) -> str:
        return formatar_numero_brasileiro(float(pd.to_numeric(pd.Series([valor]), errors="coerce").fillna(0.0).iloc[0]), 0)

    def _fmt_pct(valor: float, *, mostrar_sinal: bool = True, sufixo: str = "%") -> str:
        if pd.isna(valor):
            return ""
        formato = f"{float(valor):+.1f}" if mostrar_sinal else f"{float(valor):.1f}"
        return f"{formato}{sufixo}".replace(".", ",")

    def _fmt_pp(valor: float) -> str:
        if pd.isna(valor):
            return ""
        return f"{float(valor):+.1f} p.p.".replace(".", ",")

    def _somar_entrada(mes_alvo: str, usar_tendencia: bool = False, meta: bool = False) -> float:
        if df_principal.empty:
            return 0.0
        mask = (
            df_principal["MES_NORM"].eq(str(mes_alvo).strip().lower()) &
            df_principal["PLATAFORMA_NORM"].eq("CONTA") &
            (
                df_principal["IND_NORM"].str.contains("LIGAC", na=False)
                if usar_ligacoes_como_entrada
                else df_principal["IND_NORM"].str.contains("PEDID", na=False)
            )
        )
        coluna_valor = "DESAFIO_QTD" if meta else ("TEND_QTD" if usar_tendencia else "QTDE")
        return float(pd.to_numeric(df_principal.loc[mask, coluna_valor], errors="coerce").fillna(0.0).sum())

    def _somar_instalados(mes_alvo: str, usar_tendencia: bool = False, meta: bool = False) -> float:
        if df_principal.empty:
            return 0.0
        mes_norm = str(mes_alvo).strip().lower()
        if meta:
            mask_meta_gl = (
                df_principal["MES_NORM"].eq(mes_norm) &
                df_principal["PLATAFORMA_NORM"].eq("CONTA") &
                df_principal["IND_NORM"].str.contains("GROSS LIQ", na=False)
            )
            valor_meta = float(pd.to_numeric(df_principal.loc[mask_meta_gl, "DESAFIO_QTD"], errors="coerce").fillna(0.0).sum())
            if valor_meta > 0:
                return valor_meta

        mask_real = (
            df_principal["MES_NORM"].eq(mes_norm) &
            df_principal["PLATAFORMA_NORM"].eq("CONTA") &
            df_principal["IND_NORM"].str.contains("GROSS LIQ", na=False)
        )
        coluna_valor = "DESAFIO_QTD" if meta else ("TEND_QTD" if usar_tendencia else "QTDE")
        return float(pd.to_numeric(df_principal.loc[mask_real, coluna_valor], errors="coerce").fillna(0.0).sum())

    def _somar_cotacoes(mes_alvo: str, usar_tendencia: bool = False) -> float:
        if df_cot.empty:
            return 0.0
        mask = df_cot["MES_NORM"].eq(str(mes_alvo).strip().lower())
        df_mes = df_cot.loc[mask].copy()
        if df_mes.empty:
            return 0.0
        if usar_tendencia:
            return _projetar_cotacoes_mes_util(df_mes, mes_alvo)
        return float(pd.to_numeric(df_mes.get("VALOR_COTACAO", 0), errors="coerce").fillna(0.0).sum())

    entradas_por_mes = {
        mes_item: _somar_entrada(
            mes_item,
            usar_tendencia=(usar_tend_mes_foco and str(mes_item).strip().lower() == str(mes_foco).strip().lower())
        )
        for mes_item in meses_serie
    }
    cotacoes_por_mes = {
        mes_item: _somar_cotacoes(
            mes_item,
            usar_tendencia=(usar_tend_mes_foco and str(mes_item).strip().lower() == str(mes_foco).strip().lower())
        )
        for mes_item in meses_serie
    }

    instalados_por_mes = {
        mes_item: _somar_instalados(
            mes_item,
            usar_tendencia=(usar_tend_mes_foco and str(mes_item).strip().lower() == str(mes_foco).strip().lower())
        )
        for mes_item in meses_serie
    }
    meses_metricas = set(meses_serie)
    meses_metricas.update(obter_meses_ytd_ano(mes_foco, "25"))
    meses_metricas.update(obter_meses_ytd_ano(mes_foco, "26"))
    meses_metricas.update([get_mes_ano_anterior(mes_foco), mes_m1, mes_foco])
    for mes_item in sorted(meses_metricas, key=mes_ano_para_data):
        mes_norm = str(mes_item).strip().lower()
        usar_tend_extra = usar_tend_mes_foco and mes_norm == str(mes_foco).strip().lower()
        if mes_norm not in entradas_por_mes:
            entradas_por_mes[mes_norm] = _somar_entrada(mes_norm, usar_tendencia=usar_tend_extra)
        if mes_norm not in cotacoes_por_mes:
            cotacoes_por_mes[mes_norm] = _somar_cotacoes(mes_norm, usar_tendencia=usar_tend_extra)
        if mes_norm not in instalados_por_mes:
            instalados_por_mes[mes_norm] = _somar_instalados(mes_norm, usar_tendencia=usar_tend_extra)
    entradas_orc_por_mes = {mes_item: _somar_entrada(mes_item, meta=True) for mes_item in meses_metricas}
    cotacoes_orc_por_mes = {mes_item: 0.0 for mes_item in meses_metricas}
    instalados_orc_por_mes = {mes_item: _somar_instalados(mes_item, meta=True) for mes_item in meses_metricas}
    ped_vs_cot_por_mes = {
        mes_item: _ratio_pct(cotacoes_por_mes.get(mes_item, 0.0), entradas_por_mes.get(mes_item, 0.0))
        for mes_item in meses_metricas
    }

    inst_vs_cot_por_mes = {
        mes_item: _ratio_pct(instalados_por_mes.get(mes_item, 0.0), cotacoes_por_mes.get(mes_item, 0.0))
        for mes_item in meses_metricas
    }

    valor_atual_entrada = float(entradas_por_mes.get(mes_foco, 0.0))
    valor_anterior_entrada = float(entradas_por_mes.get(mes_m1, 0.0))
    valor_atual_cot = float(cotacoes_por_mes.get(mes_foco, 0.0))
    valor_anterior_cot = float(cotacoes_por_mes.get(mes_m1, 0.0))
    valor_atual_inst = float(instalados_por_mes.get(mes_foco, 0.0))
    valor_anterior_inst = float(instalados_por_mes.get(mes_m1, 0.0))
    valor_atual_conv_1 = ped_vs_cot_por_mes.get(mes_foco, np.nan)
    valor_anterior_conv_1 = ped_vs_cot_por_mes.get(mes_m1, np.nan)
    valor_atual_inst_vs = inst_vs_cot_por_mes.get(mes_foco, np.nan)
    valor_anterior_inst_vs = inst_vs_cot_por_mes.get(mes_m1, np.nan)

    def _metricas_valores_funil(lookup_ref: dict[str, float], lookup_orc_ref: dict[str, float] | None = None) -> dict[str, float]:
        return calcular_yoy_ytd_mensal_lookup(lookup_ref, {}, mes_foco, mes_corrente, lookup_orc=lookup_orc_ref)

    def _metricas_conversao_funil(
        lookup_num: dict[str, float],
        lookup_den: dict[str, float],
        lookup_num_orc: dict[str, float] | None = None,
        lookup_den_orc: dict[str, float] | None = None
    ) -> dict[str, float]:
        mes_yoy = get_mes_ano_anterior(mes_foco)
        atual = _ratio_pct(lookup_num.get(mes_foco, 0.0), lookup_den.get(mes_foco, 0.0))
        base_yoy = _ratio_pct(lookup_num.get(mes_yoy, 0.0), lookup_den.get(mes_yoy, 0.0))
        meses_ytd25 = obter_meses_ytd_ano(mes_foco, "25")
        meses_ytd26 = obter_meses_ytd_ano(mes_foco, "26")
        ytd25 = _ratio_pct(
            sum(float(lookup_num.get(m, 0.0)) for m in meses_ytd25),
            sum(float(lookup_den.get(m, 0.0)) for m in meses_ytd25)
        )
        ytd26 = _ratio_pct(
            sum(float(lookup_num.get(m, 0.0)) for m in meses_ytd26),
            sum(float(lookup_den.get(m, 0.0)) for m in meses_ytd26)
        )
        lookup_num_orc = lookup_num_orc or {}
        lookup_den_orc = lookup_den_orc or {}
        ytd_orc = _ratio_pct(
            sum(float(lookup_num_orc.get(m, 0.0)) for m in meses_ytd26),
            sum(float(lookup_den_orc.get(m, 0.0)) for m in meses_ytd26)
        )
        return {
            "YOY": _pp_delta(atual, base_yoy),
            "YTD25": ytd25,
            "YTD26": ytd26,
            "YTD_ORÇ": ytd_orc,
            "YTD26 vs YTD25": _pp_delta(ytd26, ytd25),
            "YTD26 vs YTD_ORÇ": _pp_delta(ytd26, ytd_orc),
        }

    linhas_numericas = []
    if exibir_linha_entrada:
        metricas_entrada = _metricas_valores_funil(entradas_por_mes, entradas_orc_por_mes)
        linhas_numericas.append({
            "ETAPA": label_entrada,
            **{mapa_coluna_mes[mes_item]: float(entradas_por_mes.get(mes_item, 0.0)) for mes_item in meses_serie},
            "MoM": _pct_delta(valor_atual_entrada, valor_anterior_entrada),
            "YoY": metricas_entrada["YOY"],
            "YTD25": metricas_entrada["YTD25"],
            "YTD26": metricas_entrada["YTD26"],
            "YTD_ORÇ": metricas_entrada["YTD_ORÇ"],
            "YTD26 vs YTD25": metricas_entrada["YTD26 vs YTD25"],
            "YTD26 vs YTD_ORÇ": metricas_entrada["YTD26 vs YTD_ORÇ"],
        })

    metricas_cot = _metricas_valores_funil(cotacoes_por_mes, cotacoes_orc_por_mes)
    metricas_inst = _metricas_valores_funil(instalados_por_mes, instalados_orc_por_mes)
    metricas_inst_vs = _metricas_conversao_funil(instalados_por_mes, cotacoes_por_mes, instalados_orc_por_mes, cotacoes_orc_por_mes)
    linhas_numericas.extend([
        {
            "ETAPA": "COTAÇÃO",
            **{mapa_coluna_mes[mes_item]: float(cotacoes_por_mes.get(mes_item, 0.0)) for mes_item in meses_serie},
            "MoM": _pct_delta(valor_atual_cot, valor_anterior_cot),
            "YoY": metricas_cot["YOY"],
            "YTD25": metricas_cot["YTD25"],
            "YTD26": metricas_cot["YTD26"],
            "YTD_ORÇ": metricas_cot["YTD_ORÇ"],
            "YTD26 vs YTD25": metricas_cot["YTD26 vs YTD25"],
            "YTD26 vs YTD_ORÇ": metricas_cot["YTD26 vs YTD_ORÇ"],
        },
        {
            "ETAPA": label_saida,
            **{mapa_coluna_mes[mes_item]: float(instalados_por_mes.get(mes_item, 0.0)) for mes_item in meses_serie},
            "MoM": _pct_delta(valor_atual_inst, valor_anterior_inst),
            "YoY": metricas_inst["YOY"],
            "YTD25": metricas_inst["YTD25"],
            "YTD26": metricas_inst["YTD26"],
            "YTD_ORÇ": metricas_inst["YTD_ORÇ"],
            "YTD26 vs YTD25": metricas_inst["YTD26 vs YTD25"],
            "YTD26 vs YTD_ORÇ": metricas_inst["YTD26 vs YTD_ORÇ"],
        },
        {
            "ETAPA": label_conv_2,
            **{mapa_coluna_mes[mes_item]: inst_vs_cot_por_mes.get(mes_item, np.nan) for mes_item in meses_serie},
            "MoM": _pp_delta(valor_atual_inst_vs, valor_anterior_inst_vs),
            "YoY": metricas_inst_vs["YOY"],
            "YTD25": metricas_inst_vs["YTD25"],
            "YTD26": metricas_inst_vs["YTD26"],
            "YTD_ORÇ": metricas_inst_vs["YTD_ORÇ"],
            "YTD26 vs YTD25": metricas_inst_vs["YTD26 vs YTD25"],
            "YTD26 vs YTD_ORÇ": metricas_inst_vs["YTD26 vs YTD_ORÇ"],
        },
    ])
    if exibir_linha_entrada:
        metricas_conv_1 = _metricas_conversao_funil(cotacoes_por_mes, entradas_por_mes, cotacoes_orc_por_mes, entradas_orc_por_mes)
        linhas_numericas.insert(
            3,
            {
                "ETAPA": label_conv_1,
                **{mapa_coluna_mes[mes_item]: ped_vs_cot_por_mes.get(mes_item, np.nan) for mes_item in meses_serie},
                "MoM": _pp_delta(valor_atual_conv_1, valor_anterior_conv_1),
                "YoY": metricas_conv_1["YOY"],
                "YTD25": metricas_conv_1["YTD25"],
                "YTD26": metricas_conv_1["YTD26"],
                "YTD_ORÇ": metricas_conv_1["YTD_ORÇ"],
                "YTD26 vs YTD25": metricas_conv_1["YTD26 vs YTD25"],
                "YTD26 vs YTD_ORÇ": metricas_conv_1["YTD26 vs YTD_ORÇ"],
            }
        )

    df_num = pd.DataFrame(linhas_numericas, columns=colunas_saida)
    df_fmt = df_num.copy().astype(object)
    linhas_percentuais = {label_conv_1, label_conv_2}

    for idx, row in df_fmt.iterrows():
        etapa = str(row["ETAPA"]).strip().upper()
        for coluna in colunas_saida:
            if coluna == "ETAPA":
                continue
            valor = df_num.loc[idx, coluna]
            if etapa in linhas_percentuais:
                if coluna in {"MoM", "YoY", "YTD26 vs YTD25", "YTD26 vs YTD_ORÇ"}:
                    df_fmt.loc[idx, coluna] = _fmt_pp(valor)
                else:
                    df_fmt.loc[idx, coluna] = _fmt_pct(valor, mostrar_sinal=False)
            else:
                if coluna in {"MoM", "YoY", "YTD26 vs YTD25", "YTD26 vs YTD_ORÇ"}:
                    df_fmt.loc[idx, coluna] = _fmt_pct(valor)
                else:
                    df_fmt.loc[idx, coluna] = _fmt_num(valor)

    return df_fmt, df_num

def montar_tabela_funil_fixa(
    df_base_principal: pd.DataFrame,
    canal_ref: str = "Todos",
    mes_ref: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Monta tabela do funil FIXA com linhas:
    PEDIDOS, VENDA BRUTA, INSTALADOS e taxas de conversao.

    Ajustes de robustez aplicados:
    - aceita bases sem DESAFIO_QTD / TEND_QTD;
    - normaliza mês mesmo quando a data vem como datetime/texto;
    - normaliza canal antes dos filtros;
    - evita retorno vazio indevido por inconsistência em dat_tratada.
    """
    mes_coluna_vazia = str((mes_ref or get_mes_atual_formatado())).strip().upper()
    colunas_vazias = ["ETAPA", mes_coluna_vazia, "MoM"]
    if df_base_principal is None or df_base_principal.empty:
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    def _normalizar_mes_funil_fixa_local(valor) -> str | None:
        if pd.isna(valor):
            return None
        meses_pt = {
            1: "jan", 2: "fev", 3: "mar", 4: "abr",
            5: "mai", 6: "jun", 7: "jul", 8: "ago",
            9: "set", 10: "out", 11: "nov", 12: "dez"
        }
        texto = str(valor).strip().lower()
        if not texto:
            return None
        if re.match(r"^[a-z]{3}/\d{2}$", texto, flags=re.IGNORECASE):
            return texto
        dt = pd.to_datetime(valor, errors="coerce", dayfirst=True)
        if pd.isna(dt):
            dt = pd.to_datetime(texto, errors="coerce")
        if pd.notna(dt):
            return f"{meses_pt.get(int(dt.month), '')}/{str(int(dt.year))[-2:]}" if int(dt.month) in meses_pt else None
        return None

    def _resolver_coluna_existente_local(df_base: pd.DataFrame, candidatas: list[str]) -> str | None:
        for col in candidatas:
            if col in df_base.columns:
                return col
        return None

    def _normalizar_plataforma_funil(valor_plataforma) -> str:
        texto = _normalizar_texto_funil_cotacoes(valor_plataforma)
        if "FIXA" in texto:
            return "FIXA"
        if "CONTA" in texto or "MOVEL" in texto or "MOBILE" in texto:
            return "CONTA"
        return texto

    df_principal = df_base_principal.copy()

    col_data_base = _resolver_coluna_existente_local(
        df_principal,
        [
            "dat_tratada", "mes_ano", "MES_ANO", "DATA", "DT_PEDIDO", "DATA_PEDIDO",
            "DT_VENDA", "DATA_VENDA", "DT_INSTALACAO", "DATA_INSTALACAO",
            "DAT_MOVIMENTO2", "DAT_MOVIMENTO", "DAT_MOVIMENTO_2", "PERIODO"
        ]
    )

    colunas_essenciais = {"CANAL_PLAN", "COD_PLATAFORMA", "DSC_INDICADOR", "QTDE"}
    if col_data_base is None or not colunas_essenciais.issubset(set(df_principal.columns)):
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    if "DESAFIO_QTD" not in df_principal.columns:
        df_principal["DESAFIO_QTD"] = 0
    if "TEND_QTD" not in df_principal.columns:
        df_principal["TEND_QTD"] = 0

    df_principal = df_principal[
        ["CANAL_PLAN", "COD_PLATAFORMA", "DSC_INDICADOR", col_data_base, "QTDE", "DESAFIO_QTD", "TEND_QTD"]
    ].copy()
    df_principal = df_principal.rename(columns={col_data_base: "_DATA_BASE_FUNIL_FIXA"})
    df_principal["MES_NORM"] = df_principal["_DATA_BASE_FUNIL_FIXA"].apply(_normalizar_mes_funil_fixa_local)
    df_principal["CANAL_FUNIL"] = df_principal["CANAL_PLAN"].apply(_mapear_canal_funil_cotacoes)
    df_principal["PLATAFORMA_NORM"] = df_principal["COD_PLATAFORMA"].apply(_normalizar_plataforma_funil)
    df_principal["INDICADOR_NORM"] = df_principal["DSC_INDICADOR"].apply(_normalizar_texto_funil_cotacoes)
    df_principal["QTDE"] = pd.to_numeric(df_principal["QTDE"], errors="coerce").fillna(0.0)
    df_principal["DESAFIO_QTD"] = pd.to_numeric(df_principal["DESAFIO_QTD"], errors="coerce").fillna(0.0)
    df_principal["TEND_QTD"] = pd.to_numeric(df_principal["TEND_QTD"], errors="coerce").fillna(0.0)
    df_principal = df_principal.loc[df_principal["MES_NORM"].notna()].copy()

    if df_principal.empty:
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    meses_validos = sorted(
        df_principal["MES_NORM"].dropna().astype(str).str.strip().str.lower().unique().tolist(),
        key=mes_ano_para_data
    )
    mes_corrente = get_mes_atual_formatado().strip().lower()
    mes_foco = str(mes_ref or "").strip().lower()
    if not mes_foco:
        mes_foco = mes_corrente if mes_corrente in meses_validos else (meses_validos[-1] if meses_validos else mes_corrente)
    elif mes_foco not in meses_validos and meses_validos:
        mes_foco = meses_validos[-1]

    def _gerar_intervalo_meses(mes_final: str, qtd_meses: int = 13) -> list[str]:
        try:
            data_final = pd.Timestamp(mes_ano_para_data(str(mes_final))).normalize()
        except Exception:
            return [str(mes_final).strip().lower()]
        intervalo = []
        for deslocamento in range(max(int(qtd_meses), 1) - 1, -1, -1):
            data_ref = data_final - pd.DateOffset(months=deslocamento)
            intervalo.append(_formatar_mes_ano_backlog(data_ref).lower())
        return intervalo

    mes_m1 = get_mes_anterior(mes_foco)
    usar_tend_mes_foco = mes_foco == mes_corrente
    meses_serie = _gerar_intervalo_meses(mes_foco, qtd_meses=13)
    colunas_meses: list[str] = []
    mapa_coluna_mes: dict[str, str] = {}
    for mes_item in meses_serie:
        label_mes = str(mes_item).strip().upper()
        if usar_tend_mes_foco and str(mes_item).strip().lower() == str(mes_foco).strip().lower():
            label_mes = f"{label_mes} (TEND.)"
        colunas_meses.append(label_mes)
        mapa_coluna_mes[str(mes_item).strip().lower()] = label_mes
    colunas_saida = ["ETAPA", *colunas_meses, "MoM"]

    canal_escolhido = str(canal_ref or "Todos").strip()
    canal_funil_escolhido = _mapear_canal_funil_cotacoes(canal_escolhido)
    exibir_linha_entrada = canal_escolhido.lower() == "todos" or canal_funil_escolhido in {"E-Commerce", "Televendas Receptivo"}
    usar_ligacoes_como_entrada = canal_funil_escolhido == "Televendas Receptivo"
    label_entrada = "LIGAÇÕES" if usar_ligacoes_como_entrada else "PEDIDOS"
    label_conv_1 = "V.B VS LIG" if usar_ligacoes_como_entrada else "V.B VS PED"

    if canal_escolhido.lower() != "todos":
        df_principal = df_principal.loc[
            df_principal["CANAL_FUNIL"].astype(str).str.strip().eq(canal_funil_escolhido)
        ].copy()
        if df_principal.empty:
            return pd.DataFrame(columns=colunas_saida), pd.DataFrame(columns=colunas_saida)

    df_principal = df_principal.loc[
        df_principal["PLATAFORMA_NORM"].astype(str).str.strip().eq("FIXA")
    ].copy()
    if df_principal.empty:
        return pd.DataFrame(columns=colunas_saida), pd.DataFrame(columns=colunas_saida)

    def _somar_indicador_por_mes(df_base: pd.DataFrame, nomes_alvo: set[str], usar_tendencia: bool = False) -> dict[str, float]:
        if df_base.empty:
            return {}
        base_ind = df_base.loc[df_base["INDICADOR_NORM"].isin(nomes_alvo)].copy()
        if base_ind.empty:
            return {}
        coluna_valor = "QTDE"
        if usar_tendencia and "TEND_QTD" in base_ind.columns:
            base_ind["VALOR_EXIBICAO"] = np.where(
                base_ind["MES_NORM"].astype(str).str.strip().str.lower().eq(mes_corrente) & base_ind["TEND_QTD"].gt(0),
                base_ind["TEND_QTD"],
                base_ind["QTDE"]
            )
            coluna_valor = "VALOR_EXIBICAO"
        return (
            base_ind.groupby("MES_NORM", observed=True)[coluna_valor]
            .sum().astype(float).to_dict()
        )

    if usar_ligacoes_como_entrada:
        nomes_entrada = {"LIGACOES", "LIGACAO"}
    else:
        nomes_entrada = {"PEDIDOS", "PEDIDO"}

    indicadores_vb = {"VENDA BRUTA", "VENDA_BRUTA", "VB", "GROSS"}
    indicadores_inst = {"INSTALADOS", "INSTALADO", "ATIVADOS", "ATIVADO", "INSTALACAO", "INSTALACAO LIQUIDA"}

    serie_entrada = _somar_indicador_por_mes(df_principal, nomes_entrada, usar_tendencia=True)
    serie_vb = _somar_indicador_por_mes(df_principal, indicadores_vb, usar_tendencia=True)
    serie_inst = _somar_indicador_por_mes(df_principal, indicadores_inst, usar_tendencia=True)

    if not serie_entrada or not serie_vb or not serie_inst:
        base_idx = df_principal.copy()
        col = base_idx["INDICADOR_NORM"].astype(str)
        if not serie_entrada:
            mask = col.str.contains("LIGACAO|LIGACOES", regex=True) if usar_ligacoes_como_entrada else col.str.contains("PEDIDO|PEDIDOS", regex=True)
            serie_entrada = (
                base_idx.loc[mask]
                .assign(VALOR_EXIBICAO=lambda x: np.where(
                    x["MES_NORM"].astype(str).str.strip().str.lower().eq(mes_corrente) & x["TEND_QTD"].gt(0),
                    x["TEND_QTD"], x["QTDE"]
                ))
                .groupby("MES_NORM", observed=True)["VALOR_EXIBICAO"].sum().astype(float).to_dict()
            )
        if not serie_vb:
            mask = col.str.contains("VENDA BRUTA|VENDA_BRUTA|\bVB\b|GROSS", regex=True)
            serie_vb = (
                base_idx.loc[mask]
                .assign(VALOR_EXIBICAO=lambda x: np.where(
                    x["MES_NORM"].astype(str).str.strip().str.lower().eq(mes_corrente) & x["TEND_QTD"].gt(0),
                    x["TEND_QTD"], x["QTDE"]
                ))
                .groupby("MES_NORM", observed=True)["VALOR_EXIBICAO"].sum().astype(float).to_dict()
            )
        if not serie_inst:
            mask = col.str.contains("INSTAL|ATIVAD", regex=True)
            serie_inst = (
                base_idx.loc[mask]
                .assign(VALOR_EXIBICAO=lambda x: np.where(
                    x["MES_NORM"].astype(str).str.strip().str.lower().eq(mes_corrente) & x["TEND_QTD"].gt(0),
                    x["TEND_QTD"], x["QTDE"]
                ))
                .groupby("MES_NORM", observed=True)["VALOR_EXIBICAO"].sum().astype(float).to_dict()
            )

    if (not serie_vb and not serie_inst) or (exibir_linha_entrada and not serie_entrada and not serie_vb and not serie_inst):
        return pd.DataFrame(columns=colunas_saida), pd.DataFrame(columns=colunas_saida)

    def _valor_mes(lookup: dict[str, float], mes_item: str) -> float:
        return float(pd.to_numeric(pd.Series([lookup.get(str(mes_item).strip().lower(), 0.0)]), errors="coerce").fillna(0.0).iloc[0])

    def _linha_valores(nome_linha: str, lookup: dict[str, float]) -> tuple[dict[str, object], dict[str, object]]:
        linha_fmt = {"ETAPA": nome_linha}
        linha_num = {"ETAPA": nome_linha}
        for mes_item in meses_serie:
            col_mes = mapa_coluna_mes.get(str(mes_item).strip().lower(), str(mes_item).strip().upper())
            valor = _valor_mes(lookup, mes_item)
            linha_num[col_mes] = valor
            linha_fmt[col_mes] = formatar_numero_brasileiro(valor, 0)
        valor_atual = _valor_mes(lookup, mes_foco)
        valor_m1 = _valor_mes(lookup, mes_m1)
        mom = _calcular_mom_funil_fixa(valor_atual, valor_m1)
        linha_num["MoM"] = mom
        linha_fmt["MoM"] = _render_mom_badge_funil_fixa(mom)
        return linha_fmt, linha_num

    def _linha_conversao(nome_linha: str, lookup_num: dict[str, float], lookup_den: dict[str, float]) -> tuple[dict[str, object], dict[str, object]]:
        linha_fmt = {"ETAPA": nome_linha}
        linha_num = {"ETAPA": nome_linha}
        for mes_item in meses_serie:
            col_mes = mapa_coluna_mes.get(str(mes_item).strip().lower(), str(mes_item).strip().upper())
            num = _valor_mes(lookup_num, mes_item)
            den = _valor_mes(lookup_den, mes_item)
            perc = ((num / den) * 100.0) if den else np.nan
            linha_num[col_mes] = perc
            linha_fmt[col_mes] = (f"{perc:.1f}%".replace('.', ',') if pd.notna(perc) else "n/d")
        num_atual = _valor_mes(lookup_num, mes_foco)
        den_atual = _valor_mes(lookup_den, mes_foco)
        num_m1 = _valor_mes(lookup_num, mes_m1)
        den_m1 = _valor_mes(lookup_den, mes_m1)
        perc_atual = ((num_atual / den_atual) * 100.0) if den_atual else np.nan
        perc_m1 = ((num_m1 / den_m1) * 100.0) if den_m1 else np.nan
        mom = _calcular_mom_funil_fixa(perc_atual, perc_m1) if pd.notna(perc_atual) and pd.notna(perc_m1) else np.nan
        linha_num["MoM"] = mom
        linha_fmt["MoM"] = _render_mom_badge_funil_fixa(mom)
        return linha_fmt, linha_num

    linhas_fmt: list[dict[str, object]] = []
    linhas_num: list[dict[str, object]] = []

    if exibir_linha_entrada:
        lf, ln = _linha_valores(label_entrada, serie_entrada)
        linhas_fmt.append(lf)
        linhas_num.append(ln)

    lf, ln = _linha_valores("VENDA BRUTA", serie_vb)
    linhas_fmt.append(lf)
    linhas_num.append(ln)

    lf, ln = _linha_valores("INSTALADOS", serie_inst)
    linhas_fmt.append(lf)
    linhas_num.append(ln)

    if exibir_linha_entrada:
        lf, ln = _linha_conversao(label_conv_1, serie_vb, serie_entrada)
        linhas_fmt.append(lf)
        linhas_num.append(ln)

    lf, ln = _linha_conversao("INST VS V.B", serie_inst, serie_vb)
    linhas_fmt.append(lf)
    linhas_num.append(ln)

    df_fmt = pd.DataFrame(linhas_fmt, columns=colunas_saida)
    df_num = pd.DataFrame(linhas_num, columns=colunas_saida)
    return df_fmt, df_num


def criar_tabela_html_funil_cotacoes(
    df_formatado: pd.DataFrame,
    df_numerico: pd.DataFrame,
    table_id: str
) -> str:
    """Renderiza a tabela do funil de COTACOES com suporte a serie mensal longa."""
    if df_formatado is None or df_formatado.empty:
        return ""

    colunas = list(df_formatado.columns)
    col_etapa = colunas[0] if colunas else "ETAPA"
    colunas_resumo = [col for col in ["MoM", "YoY", "YTD25", "YTD26", "YTD_ORÇ", "YTD26 vs YTD25", "YTD26 vs YTD_ORÇ"] if col in colunas]
    colunas_variacao = {"MoM", "YoY", "YTD26 vs YTD25", "YTD26 vs YTD_ORÇ"}
    colunas_ytd = {"YTD25", "YTD26", "YTD_ORÇ"}
    colunas_meses = [col for col in colunas if col not in {col_etapa, *colunas_resumo}]
    col_mes_foco = colunas_meses[-1] if colunas_meses else ""

    qtd_meses = max(len(colunas_meses), 1)
    largura_etapa_pct = 14.5 if qtd_meses >= 10 else 20.0
    largura_resumo_pct = 6.2
    largura_resumos_total_pct = largura_resumo_pct * max(len(colunas_resumo), 1)
    largura_meses_pct = max((100.0 - largura_etapa_pct - largura_resumos_total_pct) / qtd_meses, 3.6)
    larguras = [largura_etapa_pct] + [largura_meses_pct] * len(colunas_meses) + [largura_resumo_pct] * len(colunas_resumo)
    soma_larguras = float(sum(larguras)) if larguras else 100.0
    larguras = [(largura / soma_larguras) * 100.0 for largura in larguras]
    colgroup_html = "<colgroup>" + "".join(
        [f'<col style="width:{largura:.4f}%;">' for largura in larguras]
    ) + "</colgroup>"

    def _classe_pct(valor_raw) -> str:
        try:
            valor = float(valor_raw)
        except Exception:
            return "status-neutro"
        if pd.isna(valor):
            return "status-neutro"
        if valor > 0:
            return "status-positivo"
        if valor < 0:
            return "status-negativo"
        return "status-neutro"

    html = f"""
    <style>
        #{table_id}.tabela-container-funil-cotacoes {{
            width: 100%;
            overflow-x: auto;
            border: 1px solid rgba(121,14,9,0.74);
            border-radius: 4px;
            box-shadow:
                0 18px 38px rgba(90,10,6,0.14),
                0 4px 12px rgba(15,23,42,0.07),
                inset 0 0 0 1px rgba(255,255,255,0.92),
                inset 0 0 0 4px rgba(121,14,9,0.025);
            margin: 10px 0 18px 0;
            background: linear-gradient(180deg, #FFFFFF 0%, #FFF8F7 100%);
            font-family: 'Manrope', 'Segoe UI', sans-serif;
        }}
        #{table_id}.tabela-container-funil-cotacoes::before {{
            content: none;
            display: none;
        }}
        #{table_id} .tabela-funil-cotacoes {{
            border-collapse: separate;
            border-spacing: 0;
            width: 100%;
            min-width: 1200px;
            table-layout: fixed;
            font-size: clamp(10.2px, 0.72vw, 11.3px);
            line-height: 1.15;
            font-family: 'Manrope', 'Segoe UI', sans-serif;
            font-variant-numeric: tabular-nums;
            background: #FFFFFF;
        }}
        #{table_id} .tabela-funil-cotacoes tbody {{
            counter-reset: none;
        }}
        #{table_id} .tabela-funil-cotacoes thead th {{
            position: sticky;
            top: 0;
            z-index: 40;
            background: linear-gradient(180deg, #790E09 0%, #4E0805 100%);
            color: #FFFFFF;
            padding: 8px 5px;
            text-align: center;
            font-weight: 800;
            letter-spacing: 0.30px;
            white-space: nowrap;
            font-size: clamp(9.4px, 0.64vw, 10.4px);
            text-transform: uppercase;
            border-right: 1px solid rgba(255,255,255,0.20);
            border-bottom: 1px solid rgba(61,7,4,0.90);
            text-shadow: 0 1px 0 rgba(0,0,0,0.18);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.14);
        }}
        #{table_id} .tabela-funil-cotacoes thead th:first-child {{
            border-top-left-radius: 3px;
        }}
        #{table_id} .tabela-funil-cotacoes thead th:last-child {{
            border-top-right-radius: 3px;
        }}
        #{table_id} .tabela-funil-cotacoes thead th.col-etapa {{
            position: sticky;
            left: 0;
            z-index: 50;
            background: linear-gradient(180deg, #6C0C08 0%, #3D0704 100%);
            text-align: left;
            padding-left: 10px;
        }}
        #{table_id} .tabela-funil-cotacoes thead th.col-var {{
            background: linear-gradient(180deg, #4F5861 0%, #343B43 100%);
            box-shadow: inset 0 -3px 0 rgba(255,255,255,0.10);
        }}
        #{table_id} .tabela-funil-cotacoes thead th.col-mes-foco {{
            background: linear-gradient(180deg, #A23B36 0%, #790E09 100%);
            color: #FFFFFF;
            box-shadow: inset 3px 0 0 rgba(255,255,255,0.20), inset -3px 0 0 rgba(255,255,255,0.10);
        }}
        #{table_id} .tabela-funil-cotacoes thead th.col-tend {{
            background: linear-gradient(180deg, #6B7280 0%, #4B5563 100%);
            color: #FFFFFF;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,0.18),
                inset 0 -3px 0 rgba(255,255,255,0.18),
                inset 1px 0 0 rgba(255,255,255,0.14),
                inset -1px 0 0 rgba(255,255,255,0.14);
        }}
        #{table_id} .tabela-funil-cotacoes tbody td {{
            padding: 7px 6px;
            text-align: right;
            border-bottom: 1px solid rgba(121,14,9,0.07);
            border-right: 1px solid rgba(121,14,9,0.06);
            color: #2F3747;
            font-size: clamp(10.4px, 0.72vw, 11.6px);
            font-weight: 700;
            white-space: nowrap;
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr:nth-child(odd) td {{
            background: #FFFFFF;
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr:nth-child(even) td {{
            background: #FFF7F6;
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr:hover td {{
            background: linear-gradient(90deg, #FFF3F0 0%, #FFF8F7 100%) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.70);
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr {{
            counter-increment: none;
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr.linha-conversao-funil td {{
            background: linear-gradient(180deg, #FCF4F2 0%, #F9ECE9 100%) !important;
            color: #5F2B27;
            font-weight: 800;
            border-top: 1px solid rgba(121,14,9,0.11);
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-etapa {{
            position: sticky;
            left: 0;
            z-index: 20;
            text-align: left;
            padding-left: 13px;
            font-weight: 900;
            white-space: normal;
            overflow-wrap: anywhere;
            word-break: break-word;
            letter-spacing: -0.01em;
            box-shadow: inset 3px 0 0 #FF2800, 5px 0 12px rgba(90,10,6,0.035);
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-etapa::before {{
            content: none;
            display: none;
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr.etapa-cotacao td.col-etapa::before {{
            border-left-color: #A23B36;
            color: #6B1F1A;
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr.etapa-saida td.col-etapa::before {{
            border-left-color: #790E09;
            background: linear-gradient(90deg, rgba(121,14,9,0.13) 0%, rgba(255,255,255,0.88) 100%);
            color: #5A0A06;
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr.etapa-conversao td.col-etapa::before {{
            border-left-color: #5A6268;
            background: linear-gradient(90deg, rgba(90,98,104,0.15) 0%, rgba(255,255,255,0.88) 100%);
            color: #3E444A;
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr.etapa-cotacao td.col-etapa {{
            box-shadow: inset 3px 0 0 #A23B36, 5px 0 12px rgba(90,10,6,0.035);
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr.etapa-saida td.col-etapa {{
            box-shadow: inset 3px 0 0 #790E09, 5px 0 12px rgba(90,10,6,0.035);
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr.etapa-conversao td.col-etapa {{
            box-shadow: inset 3px 0 0 #5A6268, 5px 0 12px rgba(90,10,6,0.035);
        }}
        #{table_id} .valor-funil {{
            display: inline-flex;
            align-items: center;
            justify-content: flex-end;
            min-width: 44px;
            letter-spacing: -0.02em;
        }}
        #{table_id} .linha-conversao-funil .valor-funil {{
            color: #5F2B27;
            font-weight: 800;
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-mes-foco {{
            background: linear-gradient(180deg, #FFF7F5 0%, #FBEAE7 100%) !important;
            color: #6B1F1A;
            font-weight: 900;
            box-shadow: inset 2px 0 0 rgba(255,40,0,0.14), inset -1px 0 0 rgba(121,14,9,0.06);
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr.linha-conversao-funil td.col-mes-foco {{
            background: linear-gradient(180deg, #FBEEEB 0%, #F8E2DE 100%) !important;
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-var {{
            position: relative;
            text-align: center;
            background: linear-gradient(180deg, #F5F7F9 0%, #EEF2F5 100%) !important;
            border-left: 1px solid rgba(90, 98, 104, 0.08) !important;
            border-right: 1px solid rgba(90, 98, 104, 0.08) !important;
            font-weight: 900;
        }}
        #{table_id} .mom-chip-funil {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 62px;
            padding: 2px 5px;
            border-radius: 2px;
            border: 1px solid rgba(100,116,139,0.16);
            background: rgba(255,255,255,0.62);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.86);
            letter-spacing: -0.02em;
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-var.status-positivo {{
            background: linear-gradient(180deg, #F2FAF4 0%, #EAF6EE 100%) !important;
            box-shadow: inset 3px 0 0 rgba(46,125,50,0.74);
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-var.status-negativo {{
            background: linear-gradient(180deg, #FFF3F1 0%, #FBE4E0 100%) !important;
            box-shadow: inset 3px 0 0 rgba(198,40,40,0.74);
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-var.status-neutro {{
            background: linear-gradient(180deg, #F7F8FA 0%, #EEF1F4 100%) !important;
            box-shadow: inset 3px 0 0 rgba(100,116,139,0.42);
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-var.status-positivo .mom-chip-funil {{
            border-color: rgba(46,125,50,0.18);
            background: rgba(255,255,255,0.78);
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-var.status-negativo .mom-chip-funil {{
            border-color: rgba(198,40,40,0.18);
            background: rgba(255,255,255,0.78);
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-var.status-neutro .mom-chip-funil {{
            border-color: rgba(100,116,139,0.18);
            background: rgba(255,255,255,0.66);
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-var.status-positivo .mom-chip-funil::before {{
            content: "▲";
            margin-right: 3px;
            font-size: 8px;
            color: #2E7D32;
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-var.status-negativo .mom-chip-funil::before {{
            content: "▼";
            margin-right: 3px;
            font-size: 8px;
            color: #C62828;
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-tend {{
            background: linear-gradient(180deg, #F7F8FA 0%, #EEF1F4 100%) !important;
            border-left: 1px solid rgba(100,116,139,0.20) !important;
            border-right: 1px solid rgba(100,116,139,0.16) !important;
            color: #2F3747;
            font-weight: 950;
            box-shadow:
                inset 3px 0 0 rgba(100,116,139,0.34),
                inset -1px 0 0 rgba(100,116,139,0.10);
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.col-tend .valor-funil {{
            padding: 2px 5px;
            border-left: 2px solid rgba(100,116,139,0.42);
            border-bottom: 1px solid rgba(100,116,139,0.12);
            background: rgba(255,255,255,0.58);
            min-width: 50px;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.76);
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr.linha-conversao-funil td.col-tend {{
            background: linear-gradient(180deg, #F1F3F5 0%, #E8ECEF 100%) !important;
            color: #3E444A;
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.status-positivo {{
            color: #1B5E20 !important;
            font-weight: 900;
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.status-negativo {{
            color: #B71C1C !important;
            font-weight: 900;
        }}
        #{table_id} .tabela-funil-cotacoes tbody td.status-neutro {{
            color: #666666 !important;
            font-weight: 800;
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr:last-child td:first-child {{
            border-bottom-left-radius: 3px;
        }}
        #{table_id} .tabela-funil-cotacoes tbody tr:last-child td:last-child {{
            border-bottom-right-radius: 3px;
        }}
        @media (max-width: 768px) {{
            #{table_id} .tabela-funil-cotacoes {{
                min-width: 1120px;
                font-size: 9.2px;
            }}
            #{table_id} .tabela-funil-cotacoes thead th,
            #{table_id} .tabela-funil-cotacoes tbody td {{
                padding: 6px 4px;
                font-size: 9px;
            }}
        }}
    </style>
    <div id="{table_id}" class="tabela-container-funil-cotacoes">
    <table class="tabela-funil-cotacoes">
    {colgroup_html}
    <thead><tr>
    """

    for col in colunas:
        classes = []
        if col == col_etapa:
            classes.append("col-etapa")
        elif col in colunas_variacao:
            classes.append("col-var")
        elif col in colunas_ytd:
            classes.append("col-tend")
        elif col in colunas_meses:
            classes.append("col-mes")
            if col == col_mes_foco:
                classes.append("col-mes-foco")
        if "(TEND.)" in str(col).upper():
            classes.append("col-tend")
        html += f'<th class="{" ".join(classes)}">{escape(str(col))}</th>'
    html += "</tr></thead><tbody>"

    for idx, row in df_formatado.iterrows():
        etapa_ref = str(row.get(col_etapa, "")).strip().upper()
        if " VS " in etapa_ref:
            classe_linha = "linha-conversao-funil etapa-conversao"
        elif "COT" in etapa_ref:
            classe_linha = "linha-etapa-funil etapa-cotacao"
        elif any(chave in etapa_ref for chave in ["ATIVA", "INSTAL", "VENDA BRUTA"]):
            classe_linha = "linha-etapa-funil etapa-saida"
        else:
            classe_linha = "linha-etapa-funil etapa-entrada"
        html += f'<tr class="{classe_linha}">'
        for col_idx, col in enumerate(colunas):
            valor_raw = row[col]
            valor_fmt = escape(str(valor_raw))
            classes = []
            valor_html = f'<span class="valor-funil">{valor_fmt}</span>'
            if col == col_etapa:
                classes.append("col-etapa")
                valor_html = f'<span class="etapa-texto-funil">{valor_fmt}</span>'
            elif col in colunas_variacao:
                classes.extend(["col-var", _classe_pct(df_numerico.iloc[idx, col_idx])])
                valor_mom_html = str(valor_raw or "")
                if "<span" in valor_mom_html.lower():
                    valor_html = valor_mom_html
                else:
                    valor_html = f'<span class="mom-chip-funil">{valor_fmt}</span>'
            elif col in colunas_ytd:
                classes.append("col-tend")
            elif col in colunas_meses:
                classes.append("col-mes")
                if col == col_mes_foco:
                    classes.append("col-mes-foco")
            if "(TEND.)" in str(col).upper():
                classes.append("col-tend")
            classe_attr = " ".join(classes)
            html += f'<td class="{classe_attr}">{valor_html}</td>'
        html += "</tr>"

    html += "</tbody></table></div>"
    return html

def criar_tabela_html_backlog_canais(
    df_formatado: pd.DataFrame,
    df_numerico: pd.DataFrame,
    table_id: str
) -> str:
    """Cria tabela HTML do backlog no padrão premium das tabelas analíticas."""
    if df_formatado is None or df_formatado.empty:
        return ""

    colunas = list(df_formatado.columns)
    colunas_resumo_var = {"MoM", "YoY", "YTD26 vs YTD25", "YTD26 vs YTD_ORÇ"}
    colunas_resumo_valor = {"YTD25", "YTD26", "YTD_ORÇ"}
    qtd_meses = max(len(colunas) - 1, 1)
    largura_canal_pct = 16.2 if qtd_meses >= 10 else 20.0
    largura_mes_pct = (100.0 - largura_canal_pct) / qtd_meses
    larguras = [largura_canal_pct] + [largura_mes_pct] * qtd_meses
    colgroup_html = "<colgroup>" + "".join(
        [f'<col style="width:{largura:.4f}%;">' for largura in larguras]
    ) + "</colgroup>"

    mask_linhas_valor = (
        df_numerico.iloc[:, 0].astype(str).str.strip().str.upper().ne("TOTAL")
        if df_numerico is not None and not df_numerico.empty and df_numerico.shape[1] > 0
        else pd.Series(dtype=bool)
    )
    maximos_coluna: dict[int, float] = {}
    for idx_col in range(1, len(colunas)):
        if colunas[idx_col] in colunas_resumo_var:
            maximos_coluna[idx_col] = 0.0
            continue
        if df_numerico is None or df_numerico.empty or idx_col >= df_numerico.shape[1]:
            maximos_coluna[idx_col] = 0.0
            continue
        serie_valores = pd.to_numeric(df_numerico.iloc[:, idx_col], errors="coerce").fillna(0.0)
        serie_base = (
            serie_valores[mask_linhas_valor]
            if len(mask_linhas_valor) == len(serie_valores) and bool(mask_linhas_valor.any())
            else serie_valores
        )
        maximos_coluna[idx_col] = float(serie_base.max()) if not serie_base.empty else 0.0

    def _largura_data_bar(idx_linha: int, idx_col: int) -> float:
        try:
            valor = float(pd.to_numeric(pd.Series([df_numerico.iloc[idx_linha, idx_col]]), errors="coerce").fillna(0.0).iloc[0])
        except Exception:
            valor = 0.0
        max_coluna = float(maximos_coluna.get(idx_col, 0.0) or 0.0)
        if valor <= 0 or max_coluna <= 0:
            return 0.0
        return max(5.0, min(100.0, (valor / max_coluna) * 100.0))

    html = f"""
    <style>
        .{table_id}-container {{
            width: 100%;
            overflow-x: auto;
            border: 1px solid rgba(121,14,9,0.78);
            border-radius: 5px;
            box-shadow:
                0 18px 38px rgba(90,10,6,0.14),
                0 4px 12px rgba(15,23,42,0.06),
                inset 0 0 0 1px rgba(255,255,255,0.90);
            margin: 8px 0 16px 0;
            background:
                linear-gradient(180deg, #FFFFFF 0%, #FFF8F7 100%);
            font-family: 'Manrope', 'Segoe UI', sans-serif;
        }}
        table.{table_id} {{
            border-collapse: separate;
            border-spacing: 0;
            width: 100%;
            min-width: 1040px;
            table-layout: fixed;
            font-size: clamp(10.1px, 0.72vw, 11.1px);
            line-height: 1.14;
            font-family: 'Manrope', 'Segoe UI', sans-serif;
            font-variant-numeric: tabular-nums;
            background: #FFFFFF;
        }}
        .{table_id} thead th {{
            position: sticky;
            top: 0;
            z-index: 40;
            background: linear-gradient(180deg, #790E09 0%, #4E0805 100%);
            color: #fff;
            padding: 7px 5px;
            text-align: center;
            font-weight: 800;
            letter-spacing: 0.26px;
            white-space: nowrap;
            font-size: clamp(9.3px, 0.64vw, 10.2px);
            border-right: 1px solid rgba(255,255,255,0.20);
            border-bottom: 1px solid rgba(61,7,4,0.92);
            text-transform: uppercase;
            text-shadow: 0 1px 0 rgba(0,0,0,0.18);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.14);
        }}
        .{table_id} thead th.col-total-mes {{
            background: linear-gradient(180deg, #6B7280 0%, #4B5563 100%);
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,0.18),
                inset 0 -3px 0 rgba(255,255,255,0.14);
        }}
        .{table_id} thead th.col-mes-atual {{
            background: linear-gradient(180deg, #B7443B 0%, #8F241D 100%) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,0.20),
                inset 0 -3px 0 rgba(255,255,255,0.16);
        }}
        .{table_id} thead th.col-canal {{
            position: sticky;
            left: 0;
            z-index: 50;
            background: linear-gradient(180deg, #6C0C08 0%, #3D0704 100%);
            text-align: left;
            padding-left: 10px;
        }}
        .{table_id} tbody td {{
            position: relative;
            padding: 7px 6px;
            text-align: right;
            border-bottom: 1px solid rgba(121,14,9,0.07);
            border-right: 1px solid rgba(121,14,9,0.055);
            color: #2F3747;
            font-size: clamp(10.3px, 0.72vw, 11.2px);
            font-weight: 700;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }}
        .{table_id} tbody tr:nth-child(odd) td {{
            background: #FFFFFF;
        }}
        .{table_id} tbody tr:nth-child(even) td {{
            background: #FFF7F6;
        }}
        .{table_id} tbody tr:hover td {{
            background: linear-gradient(90deg, #FFF3F0 0%, #FFF8F7 100%) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.70);
        }}
        .{table_id} tbody td.col-canal {{
            position: sticky;
            left: 0;
            z-index: 20;
            text-align: left;
            padding-left: 10px;
            color: #5A0A06;
            font-weight: 800;
            white-space: normal;
            overflow-wrap: anywhere;
            word-break: break-word;
            box-shadow: inset 3px 0 0 #FF2800, 5px 0 12px rgba(90,10,6,0.035);
        }}
        .{table_id} tbody td.col-valor {{
            overflow: hidden;
        }}
        .{table_id} tbody td.col-valor::before {{
            content: "";
            position: absolute;
            left: 5px;
            top: 5px;
            bottom: 5px;
            width: var(--bar, 0%);
            max-width: calc(100% - 10px);
            border-radius: 2px;
            background: linear-gradient(90deg, rgba(121,14,9,0.115) 0%, rgba(255,40,0,0.035) 100%);
            pointer-events: none;
        }}
        .{table_id} .valor-tabela {{
            position: relative;
            z-index: 2;
            letter-spacing: -0.015em;
        }}
        .{table_id} tbody td.col-total-mes {{
            background: linear-gradient(180deg, #F7F8FA 0%, #EEF1F4 100%) !important;
            color: #2F3747;
            font-weight: 900;
            box-shadow: inset 3px 0 0 rgba(100,116,139,0.34);
        }}
        .{table_id} tbody td.col-total-mes::before {{
            background: linear-gradient(90deg, rgba(100,116,139,0.13) 0%, rgba(100,116,139,0.035) 100%);
        }}
        .{table_id} tbody tr.linha-total td {{
            background: linear-gradient(180deg, #5A0A06 0%, #3D0704 100%) !important;
            color: #FFFFFF !important;
            font-weight: 900;
            border-bottom: 1px solid #A23B36;
            border-right: 1px solid rgba(255,255,255,0.13);
            text-shadow: 0 1px 0 rgba(0,0,0,0.22);
        }}
        .{table_id} tbody tr.linha-total td.col-valor::before {{
            display: none;
        }}
        .{table_id} tbody tr.linha-total td.col-canal {{
            z-index: 30;
            box-shadow: inset 3px 0 0 #FF2800;
        }}
        .{table_id}-container::-webkit-scrollbar {{
            width: 8px;
            height: 8px;
        }}
        .{table_id}-container::-webkit-scrollbar-track {{
            background: #F5F5F5;
            border-radius: 10px;
        }}
        .{table_id}-container::-webkit-scrollbar-thumb {{
            background: linear-gradient(135deg, #A23B36 0%, #790E09 100%);
            border-radius: 10px;
        }}
    </style>
    <div class="{table_id}-container">
      <table class="{table_id}">
        {colgroup_html}
        <thead>
          <tr>
    """
    mes_atual_backlog_label = get_mes_atual_formatado().strip().lower()
    for idx_col, coluna in enumerate(colunas):
        classes = []
        coluna_norm = str(coluna).replace("TEND.", "").strip().lower()
        if idx_col == 0:
            classes.append("col-canal")
        elif str(coluna).strip().upper().startswith("TEND.") or str(coluna).strip() in (colunas_resumo_var | colunas_resumo_valor):
            classes.append("col-total-mes")
        if idx_col > 0 and coluna_norm == mes_atual_backlog_label:
            classes.append("col-mes-atual")
        html += f'<th class="{" ".join(classes)}">{escape(str(coluna))}</th>'
    html += "</tr></thead><tbody>"

    for idx_linha, row in df_formatado.iterrows():
        canal_ref = str(df_numerico.iloc[idx_linha, 0]) if idx_linha < len(df_numerico) else str(row.iloc[0])
        is_total = canal_ref.strip().upper() == "TOTAL"
        classe_linha = ' class="linha-total"' if is_total else ""
        html += f"<tr{classe_linha}>"
        for idx_col, coluna in enumerate(colunas):
            valor = escape(str(row[coluna]))
            classes = []
            style_attr = ""
            if idx_col == 0:
                classes.append("col-canal")
                valor_html = valor
            else:
                classes.append("col-valor")
                if str(coluna).strip().upper().startswith("TEND.") or str(coluna).strip() in (colunas_resumo_var | colunas_resumo_valor):
                    classes.append("col-total-mes")
                if not is_total and str(coluna).strip() not in colunas_resumo_var:
                    style_attr = f' style="--bar:{_largura_data_bar(idx_linha, idx_col):.2f}%;"'
                valor_html = f'<span class="valor-tabela">{valor}</span>'
            html += f'<td class="{" ".join(classes)}"{style_attr}>{valor_html}</td>'
        html += "</tr>"

    html += "</tbody></table></div>"
    return html

def criar_tabela_html_resumo_mensal_canal(
    df_formatado: pd.DataFrame,
    df_numerico: pd.DataFrame,
    table_id: str
) -> str:
    """Tabela limpa para o resumo mensal por canal, sem data bars e com cabecalhos quebrados."""
    if df_formatado is None or df_formatado.empty:
        return ""

    colunas = list(df_formatado.columns)
    col_canal = colunas[0] if colunas else "CANAL"
    colunas_variacao = {"MoM", "YoY", "YTD26 vs YTD25", "YTD26 vs YTD_ORÇ"}
    colunas_ytd = {"YTD25", "YTD26"}
    colunas_meta = {"YTD_ORÇ"}
    qtd_colunas_num = max(len(colunas) - 1, 1)
    largura_canal_pct = 12.0
    largura_demais_pct = (100.0 - largura_canal_pct) / qtd_colunas_num
    colgroup_html = "<colgroup>" + "".join(
        [f'<col style="width:{largura_canal_pct:.4f}%;">'] +
        [f'<col style="width:{largura_demais_pct:.4f}%;">' for _ in range(qtd_colunas_num)]
    ) + "</colgroup>"

    def _classe_variacao(valor) -> str:
        try:
            valor_float = float(valor)
        except Exception:
            valor_float = 0.0
        if valor_float > 0:
            return "status-positivo"
        if valor_float < 0:
            return "status-negativo"
        return "status-neutro"

    def _cabecalho(coluna: str) -> str:
        coluna_str = str(coluna)
        if coluna_str == "YTD26 vs YTD25":
            return "YTD26<br>vs<br>YTD25"
        if coluna_str == "YTD26 vs YTD_ORÇ":
            return "YTD26<br>vs<br>ORÇ"
        return escape(coluna_str)

    mes_atual_label = get_mes_atual_formatado().strip().lower()
    html = f"""
    <style>
        .{table_id}-container {{
            width: 100%;
            overflow-x: auto;
            border: 2px solid #790E09;
            border-radius: 12px;
            box-shadow: 0 6px 18px rgba(121,14,9,0.12);
            margin: 8px 0 14px 0;
            background: linear-gradient(180deg, #FFFFFF 0%, #FFF7F6 100%);
            font-family: 'Manrope', 'Segoe UI', sans-serif;
        }}
        table.{table_id} {{
            border-collapse: collapse;
            width: 100%;
            min-width: 100%;
            table-layout: fixed;
            font-size: clamp(7.6px, 0.58vw, 9.1px);
            font-family: 'Manrope', 'Segoe UI', sans-serif;
            font-variant-numeric: tabular-nums;
        }}
        .{table_id} thead th {{
            position: sticky;
            top: 0;
            z-index: 40;
            background: linear-gradient(135deg, #790E09 0%, #5A0A06 100%);
            color: #FFFFFF;
            padding: 4px 2px;
            text-align: center;
            font-weight: 800;
            letter-spacing: 0.05px;
            border-right: 1px solid rgba(255,255,255,0.90);
            white-space: normal;
            overflow-wrap: anywhere;
            line-height: 1.0;
            font-size: clamp(6.8px, 0.52vw, 8.5px);
            text-transform: uppercase;
        }}
        .{table_id} thead th.col-canal {{
            position: sticky;
            left: 0;
            z-index: 50;
            text-align: left;
            padding-left: 7px;
            background: linear-gradient(135deg, #6C0C08 0%, #4A0704 100%) !important;
        }}
        .{table_id} thead th.col-variacao {{
            background: linear-gradient(135deg, #5A6268 0%, #3E444A 100%) !important;
        }}
        .{table_id} thead th.col-ytd {{
            background: linear-gradient(135deg, #D45D44 0%, #A23B36 100%) !important;
        }}
        .{table_id} thead th.col-meta {{
            background: linear-gradient(135deg, #A23B36 0%, #790E09 100%) !important;
        }}
        .{table_id} thead th.col-mes-atual {{
            background: linear-gradient(135deg, #B7443B 0%, #8F241D 100%) !important;
        }}
        .{table_id} tbody td {{
            padding: 3px 2px;
            text-align: right;
            border-bottom: 1px solid #FFFFFF;
            border-right: 1px solid #FFFFFF;
            font-weight: 400;
            color: #2F3747;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            line-height: 1.08;
            letter-spacing: -0.02em;
        }}
        .{table_id} tbody tr:nth-child(odd) td {{ background: #FFF9F8; }}
        .{table_id} tbody tr:nth-child(even) td {{ background: #FDF3F2; }}
        .{table_id} tbody td.col-canal {{
            position: sticky;
            left: 0;
            z-index: 20;
            text-align: left;
            padding-left: 6px;
            font-weight: 600;
            color: #333333;
            white-space: nowrap;
        }}
        .{table_id} tbody tr:nth-child(odd) td.col-canal {{ background: #FFF9F8 !important; }}
        .{table_id} tbody tr:nth-child(even) td.col-canal {{ background: #FDF3F2 !important; }}
        .{table_id} tbody td.col-ytd {{
            background: linear-gradient(180deg, rgba(47,55,71,0.06) 0%, rgba(47,55,71,0.025) 100%) !important;
            color: #1F2937 !important;
            font-weight: 600;
        }}
        .{table_id} tbody td.col-meta {{
            background: linear-gradient(180deg, rgba(121,14,9,0.06) 0%, rgba(121,14,9,0.022) 100%) !important;
            color: #6B1F1A !important;
            font-weight: 600;
        }}
        .{table_id} tbody td.col-variacao {{
            position: relative;
            background: linear-gradient(180deg, rgba(90,98,104,0.08) 0%, rgba(90,98,104,0.03) 100%) !important;
            font-weight: 600;
            padding-left: 14px !important;
        }}
        .{table_id} tbody td.col-variacao.status-positivo {{
            color: #1B5E20 !important;
        }}
        .{table_id} tbody td.col-variacao.status-negativo {{
            color: #B71C1C !important;
        }}
        .{table_id} tbody td.col-variacao.status-neutro {{
            color: #475569 !important;
        }}
        .{table_id} tbody tr:not(.linha-total) td.col-variacao.status-positivo::before {{
            content: "▲";
            position: absolute;
            left: 4px;
            top: 50%;
            transform: translateY(-50%);
            font-size: 8px;
            color: #2E7D32;
        }}
        .{table_id} tbody tr:not(.linha-total) td.col-variacao.status-negativo::before {{
            content: "▼";
            position: absolute;
            left: 4px;
            top: 50%;
            transform: translateY(-50%);
            font-size: 8px;
            color: #C62828;
        }}
        .{table_id} tbody tr.linha-total td {{
            background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
            color: #FFFFFF !important;
            font-weight: 700;
            border-bottom: 2px solid #A23B36;
        }}
        .{table_id} tbody tr.linha-total td.col-canal,
        .{table_id} tbody tr.linha-total td.col-ytd,
        .{table_id} tbody tr.linha-total td.col-meta,
        .{table_id} tbody tr.linha-total td.col-variacao,
        .{table_id} tbody tr.linha-total td.col-variacao.status-positivo,
        .{table_id} tbody tr.linha-total td.col-variacao.status-negativo,
        .{table_id} tbody tr.linha-total td.col-variacao.status-neutro {{
            background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
            color: #FFFFFF !important;
            font-weight: 700 !important;
        }}
        .{table_id} tbody tr.linha-total td::before {{
            content: "" !important;
        }}
    </style>
    <div class="{table_id}-container">
      <table class="{table_id}">
        {colgroup_html}
        <thead>
          <tr>
    """

    for idx_col, coluna in enumerate(colunas):
        classes = []
        coluna_norm = str(coluna).replace("TEND.", "").strip().lower()
        if idx_col == 0:
            classes.append("col-canal")
        elif str(coluna) in colunas_variacao:
            classes.append("col-variacao")
        elif str(coluna) in colunas_ytd:
            classes.append("col-ytd")
        elif str(coluna) in colunas_meta:
            classes.append("col-meta")
        if idx_col > 0 and coluna_norm == mes_atual_label:
            classes.append("col-mes-atual")
        html += f'<th class="{" ".join(classes)}">{_cabecalho(str(coluna))}</th>'
    html += "</tr></thead><tbody>"

    for idx_linha, row in df_formatado.iterrows():
        canal_ref = str(df_numerico.iloc[idx_linha, 0]) if df_numerico is not None and idx_linha < len(df_numerico) else str(row.iloc[0])
        is_total = canal_ref.strip().upper() == "TOTAL"
        html += '<tr class="linha-total">' if is_total else "<tr>"
        for idx_col, coluna in enumerate(colunas):
            classes = []
            if idx_col == 0:
                classes.append("col-canal")
            elif str(coluna) in colunas_variacao:
                classes.extend(["col-variacao", _classe_variacao(df_numerico.iloc[idx_linha, idx_col])])
            elif str(coluna) in colunas_ytd:
                classes.append("col-ytd")
            elif str(coluna) in colunas_meta:
                classes.append("col-meta")
            valor = escape(str(row[coluna]))
            html += f'<td class="{" ".join(classes)}">{valor}</td>'
        html += "</tr>"

    html += "</tbody></table></div>"
    return html

def criar_tabela_html_migracoes_regionais(
    df_formatado: pd.DataFrame,
    df_numerico: pd.DataFrame,
    table_id: str
) -> str:
    """Cria tabela regional x mes para migracoes, com destaque de tendencia e MoM."""
    if df_formatado is None or df_formatado.empty:
        return ""

    colunas = list(df_formatado.columns)
    col_regional = colunas[0] if colunas else "REGIONAL"
    col_mom = "MoM" if "MoM" in colunas else ""
    colunas_valor = [col for col in colunas if col not in {col_regional, col_mom}]
    largura_regional_pct = 10.2
    largura_mom_pct = 8.3 if col_mom else 0.0
    largura_mes_pct = (100.0 - largura_regional_pct - largura_mom_pct) / max(len(colunas_valor), 1)
    larguras: list[float] = []
    for coluna in colunas:
        if coluna == col_regional:
            larguras.append(largura_regional_pct)
        elif coluna == col_mom:
            larguras.append(largura_mom_pct)
        else:
            larguras.append(largura_mes_pct)
    colgroup_html = "<colgroup>" + "".join(
        [f'<col style="width:{largura:.4f}%;">' for largura in larguras]
    ) + "</colgroup>"

    mask_linhas_valor = (
        df_numerico.iloc[:, 0].astype(str).str.strip().str.upper().ne("TOTAL")
        if df_numerico is not None and not df_numerico.empty and df_numerico.shape[1] > 0
        else pd.Series(dtype=bool)
    )
    maximos_coluna: dict[int, float] = {}
    for idx_col, coluna in enumerate(colunas):
        if coluna in {col_regional, col_mom} or df_numerico is None or df_numerico.empty or idx_col >= df_numerico.shape[1]:
            continue
        serie_valores = pd.to_numeric(df_numerico.iloc[:, idx_col], errors="coerce").fillna(0.0)
        serie_base = (
            serie_valores[mask_linhas_valor]
            if len(mask_linhas_valor) == len(serie_valores) and bool(mask_linhas_valor.any())
            else serie_valores
        )
        maximos_coluna[idx_col] = float(serie_base.max()) if not serie_base.empty else 0.0

    def _largura_data_bar(idx_linha: int, idx_col: int) -> float:
        try:
            valor = float(pd.to_numeric(pd.Series([df_numerico.iloc[idx_linha, idx_col]]), errors="coerce").fillna(0.0).iloc[0])
        except Exception:
            valor = 0.0
        max_coluna = float(maximos_coluna.get(idx_col, 0.0) or 0.0)
        if valor <= 0 or max_coluna <= 0:
            return 0.0
        return max(6.0, min(100.0, (valor / max_coluna) * 100.0))

    def _classe_mom(valor) -> str:
        try:
            valor_float = float(valor)
        except Exception:
            valor_float = 0.0
        if valor_float > 0.05:
            return "mom-pos"
        if valor_float < -0.05:
            return "mom-neg"
        return "mom-neut"

    html = f"""
    <style>
        .{table_id}-container {{
            width: 100%;
            overflow-x: auto;
            border: 1px solid rgba(121,14,9,0.78);
            border-radius: 5px;
            box-shadow:
                0 18px 38px rgba(90,10,6,0.13),
                0 4px 12px rgba(15,23,42,0.06),
                inset 0 0 0 1px rgba(255,255,255,0.90);
            margin: 8px 0 14px 0;
            background: linear-gradient(180deg, #FFFFFF 0%, #FFF8F7 100%);
            font-family: 'Manrope', 'Segoe UI', sans-serif;
        }}
        table.{table_id} {{
            border-collapse: separate;
            border-spacing: 0;
            width: 100%;
            min-width: 820px;
            table-layout: fixed;
            font-size: clamp(11.2px, 0.80vw, 12.4px);
            line-height: 1.14;
            font-family: 'Manrope', 'Segoe UI', sans-serif;
            font-variant-numeric: tabular-nums;
            background: #FFFFFF;
        }}
        .{table_id} thead th {{
            position: sticky;
            top: 0;
            z-index: 40;
            background: linear-gradient(180deg, #790E09 0%, #4E0805 100%);
            color: #fff;
            padding: 7px 6px;
            text-align: center;
            font-weight: 800;
            letter-spacing: 0.26px;
            white-space: nowrap;
            font-size: clamp(10.0px, 0.70vw, 11.0px);
            border-right: 1px solid rgba(255,255,255,0.20);
            border-bottom: 1px solid rgba(61,7,4,0.92);
            text-transform: uppercase;
            text-shadow: 0 1px 0 rgba(0,0,0,0.18);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.14);
        }}
        .{table_id} thead th.col-regional {{
            position: sticky;
            left: 0;
            z-index: 50;
            background: linear-gradient(180deg, #6C0C08 0%, #3D0704 100%);
            text-align: left;
            padding-left: 10px;
        }}
        .{table_id} thead th.col-tend {{
            background: linear-gradient(180deg, #6B7280 0%, #4B5563 100%);
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,0.18),
                inset 0 -3px 0 rgba(255,255,255,0.14);
        }}
        .{table_id} thead th.col-mom {{
            background: linear-gradient(180deg, #4F5861 0%, #343B43 100%);
        }}
        .{table_id} tbody td {{
            position: relative;
            padding: 8px 6px;
            text-align: right;
            border-bottom: 1px solid rgba(121,14,9,0.07);
            border-right: 1px solid rgba(121,14,9,0.055);
            color: #2F3747;
            font-size: clamp(11.3px, 0.80vw, 12.5px);
            font-weight: 700;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }}
        .{table_id} tbody tr:nth-child(odd) td {{
            background: #FFFFFF;
        }}
        .{table_id} tbody tr:nth-child(even) td {{
            background: #FFF7F6;
        }}
        .{table_id} tbody tr:hover td {{
            background: linear-gradient(90deg, #FFF3F0 0%, #FFF8F7 100%) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.70);
        }}
        .{table_id} tbody td.col-regional {{
            position: sticky;
            left: 0;
            z-index: 20;
            text-align: left;
            padding-left: 10px;
            color: #5A0A06;
            font-weight: 800;
            box-shadow: inset 3px 0 0 #FF2800, 5px 0 12px rgba(90,10,6,0.035);
        }}
        .{table_id} tbody td.col-valor {{
            overflow: hidden;
        }}
        .{table_id} tbody td.col-valor::before {{
            content: "";
            position: absolute;
            left: 5px;
            top: 5px;
            bottom: 5px;
            width: var(--bar, 0%);
            max-width: calc(100% - 10px);
            border-radius: 2px;
            background: linear-gradient(90deg, rgba(121,14,9,0.115) 0%, rgba(255,40,0,0.035) 100%);
            pointer-events: none;
        }}
        .{table_id} .valor-migracoes {{
            position: relative;
            z-index: 2;
            letter-spacing: -0.015em;
        }}
        .{table_id} tbody td.col-tend {{
            background: linear-gradient(180deg, #F7F8FA 0%, #EEF1F4 100%) !important;
            color: #2F3747;
            font-weight: 900;
            box-shadow: inset 3px 0 0 rgba(100,116,139,0.34);
        }}
        .{table_id} tbody td.col-tend::before {{
            background: linear-gradient(90deg, rgba(100,116,139,0.13) 0%, rgba(100,116,139,0.035) 100%);
        }}
        .{table_id} tbody td.col-mom {{
            background: linear-gradient(180deg, #F5F7F9 0%, #EEF2F5 100%) !important;
            font-weight: 700;
            text-align: center;
            border-left: 1px solid rgba(90,98,104,0.08) !important;
        }}
        .{table_id} .mom-chip-migracoes {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 66px;
            padding: 2px 6px;
            border-radius: 2px;
            border: 1px solid rgba(100,116,139,0.16);
            background: rgba(255,255,255,0.70);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.86);
            letter-spacing: -0.02em;
        }}
        .{table_id} tbody td.mom-pos {{
            color: #0F8A4B;
            box-shadow: inset 3px 0 0 rgba(15,138,75,0.62);
        }}
        .{table_id} tbody td.mom-neg {{
            color: #B42318;
            box-shadow: inset 3px 0 0 rgba(180,35,24,0.62);
        }}
        .{table_id} tbody td.mom-neut {{
            color: #6B7280;
            box-shadow: inset 3px 0 0 rgba(100,116,139,0.38);
        }}
        .{table_id} tbody tr.linha-total td {{
            background: linear-gradient(180deg, #5A0A06 0%, #3D0704 100%) !important;
            color: #FFFFFF !important;
            font-weight: 800;
            border-bottom: 1px solid #A23B36;
            border-right: 1px solid rgba(255,255,255,0.13);
            text-shadow: 0 1px 0 rgba(0,0,0,0.22);
        }}
        .{table_id} tbody tr.linha-total td.col-valor::before {{
            display: none;
        }}
        .{table_id} tbody tr.linha-total td.col-regional {{
            z-index: 30;
            box-shadow: inset 3px 0 0 #FF2800;
        }}
        .{table_id} tbody tr.linha-total .mom-chip-migracoes {{
            background: rgba(255,255,255,0.12);
            border-color: rgba(255,255,255,0.22);
        }}
        .{table_id}-container::-webkit-scrollbar {{
            width: 8px;
            height: 8px;
        }}
        .{table_id}-container::-webkit-scrollbar-track {{
            background: #F5F5F5;
            border-radius: 10px;
        }}
        .{table_id}-container::-webkit-scrollbar-thumb {{
            background: linear-gradient(135deg, #A23B36 0%, #790E09 100%);
            border-radius: 10px;
        }}
    </style>
    <div class="{table_id}-container">
      <table class="{table_id}">
        {colgroup_html}
        <thead>
          <tr>
    """
    for idx_col, coluna in enumerate(colunas):
        classes = []
        coluna_txt = str(coluna).strip()
        if idx_col == 0:
            classes.append("col-regional")
        elif coluna_txt.upper().startswith("TEND."):
            classes.append("col-tend")
        elif coluna == col_mom:
            classes.append("col-mom")
        html += f'<th class="{" ".join(classes)}">{escape(coluna_txt)}</th>'
    html += "</tr></thead><tbody>"

    for idx_linha, row in df_formatado.iterrows():
        regional_ref = str(df_numerico.iloc[idx_linha, 0]) if idx_linha < len(df_numerico) else str(row.iloc[0])
        is_total = regional_ref.strip().upper() == "TOTAL"
        classe_linha = ' class="linha-total"' if is_total else ""
        html += f"<tr{classe_linha}>"
        for idx_col, coluna in enumerate(colunas):
            valor = escape(str(row[coluna]))
            classes = []
            style_attr = ""
            valor_html = valor
            coluna_txt = str(coluna).strip()
            if idx_col == 0:
                classes.append("col-regional")
            elif coluna_txt.upper().startswith("TEND."):
                classes.extend(["col-valor", "col-tend"])
                if not is_total:
                    style_attr = f' style="--bar:{_largura_data_bar(idx_linha, idx_col):.2f}%;"'
                valor_html = f'<span class="valor-migracoes">{valor}</span>'
            elif coluna == col_mom:
                classes.extend(["col-mom", _classe_mom(df_numerico.iloc[idx_linha][coluna])])
                valor_html = f'<span class="mom-chip-migracoes">{valor}</span>'
            else:
                classes.append("col-valor")
                if not is_total:
                    style_attr = f' style="--bar:{_largura_data_bar(idx_linha, idx_col):.2f}%;"'
                valor_html = f'<span class="valor-migracoes">{valor}</span>'
            html += f'<td class="{" ".join(classes)}"{style_attr}>{valor_html}</td>'
        html += "</tr>"

    html += "</tbody></table></div>"
    return html

@st.cache_data(ttl=1800, show_spinner=False, max_entries=CACHE_MAX_ENTRIES_MEDIUM)
def preparar_base_gross_motivo_status(df_base: pd.DataFrame) -> pd.DataFrame:
    """Prepara a base de Gross Liquido Conta por motivo para os graficos do Analitico."""
    colunas_saida = ["dat_tratada", "CANAL_PLAN", "REGIONAL", "MOTIVO_STS", "QTDE"]
    colunas_minimas = {
        "dat_tratada", "CANAL_PLAN", "REGIONAL",
        "COD_PLATAFORMA", "DSC_INDICADOR", "DSC_MOTIVO_STS", "QTDE"
    }
    if df_base is None or df_base.empty or not colunas_minimas.issubset(set(df_base.columns)):
        return pd.DataFrame(columns=colunas_saida)

    df_work = df_base[list(colunas_minimas)].copy()
    for coluna in ["dat_tratada", "CANAL_PLAN", "REGIONAL", "COD_PLATAFORMA", "DSC_INDICADOR", "DSC_MOTIVO_STS"]:
        df_work[coluna] = df_work[coluna].astype(str).str.strip()

    df_work["dat_tratada"] = df_work["dat_tratada"].astype(str).str.strip().str.lower()
    df_work = df_work[df_work["dat_tratada"].str.match(r"^[a-z]{3}/\d{2}$", na=False)].copy()
    if df_work.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_work["COD_PLATAFORMA"] = df_work["COD_PLATAFORMA"].apply(normalizar_rotulo_produto)
    indicador_norm = df_work["DSC_INDICADOR"].map(normalizar_chave_visual)
    mask_gross_conta = (
        df_work["COD_PLATAFORMA"].eq("CONTA") &
        indicador_norm.str.contains("gross", na=False) &
        indicador_norm.str.contains("liq", na=False)
    )
    df_work = df_work.loc[mask_gross_conta].copy()
    if df_work.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_work["QTDE"] = normalizar_numerico_serie(df_work["QTDE"]).fillna(0.0)
    df_work["CANAL_PLAN"] = (
        df_work["CANAL_PLAN"]
        .replace({"": "Canal nao informado", "nan": "Canal nao informado"})
    )
    df_work["REGIONAL"] = (
        df_work["REGIONAL"]
        .replace({"": "N/I", "nan": "N/I"})
    )
    df_work["MOTIVO_STS"] = (
        df_work["DSC_MOTIVO_STS"]
        .replace({"": "Nao informado", "nan": "Nao informado", "None": "Nao informado", "NULL": "Nao informado"})
        .astype(str)
        .str.strip()
    )
    df_work = df_work.loc[df_work["QTDE"].ne(0)].copy()
    if df_work.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_saida = df_work[["dat_tratada", "CANAL_PLAN", "REGIONAL", "MOTIVO_STS", "QTDE"]].copy()
    compactar_colunas_categoricas(df_saida, ["dat_tratada", "CANAL_PLAN", "REGIONAL", "MOTIVO_STS"])
    return df_saida

def _classificar_motivo_gross_sts(motivo: str) -> tuple[int, str]:
    """Define ordem visual estável para os motivos de gross líquido móvel."""
    motivo_txt = str(motivo or "").strip()
    motivo_norm = normalizar_chave_visual(motivo_txt)
    if not motivo_norm:
        return (98, "nao informado")
    if "novo" in motivo_norm:
        return (1, motivo_norm)
    if "renov" in motivo_norm:
        return (2, motivo_norm)
    if "migra" in motivo_norm:
        return (3, motivo_norm)
    if "upgrade" in motivo_norm or "up grade" in motivo_norm:
        return (4, motivo_norm)
    if "portab" in motivo_norm:
        return (5, motivo_norm)
    if "outro" in motivo_norm:
        return (90, motivo_norm)
    if "nao informado" in motivo_norm:
        return (99, motivo_norm)
    return (50, motivo_norm)

def _cor_motivo_gross_sts(motivo: str, idx_fallback: int = 0) -> str:
    """Retorna cor estável dos motivos dentro da paleta do dashboard."""
    motivo_norm = normalizar_chave_visual(motivo)
    mapa = {
        "novo": "#790E09",
        "renov": "#A61C14",
        "migra": "#C53A2B",
        "upgrade": "#D95F4A",
        "up grade": "#D95F4A",
        "portab": "#E87F69",
        "outro": "#6B7280",
        "nao informado": "#94A3B8",
    }
    for chave, cor in mapa.items():
        if chave in motivo_norm:
            return cor
    paleta_fallback = ["#5A0A06", "#8D1A12", "#B23A2F", "#C86E61", "#7C3F3A", "#A95C54"]
    return paleta_fallback[idx_fallback % len(paleta_fallback)]

@st.cache_data(ttl=3600, show_spinner=False, max_entries=2)
def load_migracoes_pme_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    """Carrega a base de migracoes PME e padroniza os campos usados na tabela analitica."""
    _ = file_mtime
    path_obj = Path(path)
    colunas_saida = ["REGIONAL", "MES_ANO", "QTDE"]
    if not path_obj.exists():
        return pd.DataFrame(columns=colunas_saida)

    if path_obj.suffix.lower() == ".parquet":
        df_opt = _carregar_dataframe_preprocessado(
            str(path_obj),
            file_mtime,
            required_cols=set(colunas_saida),
            text_cols=["REGIONAL", "MES_ANO"],
            numeric_cols=["QTDE"],
            category_cols=["REGIONAL", "MES_ANO"]
        )
        if df_opt.empty:
            return pd.DataFrame(columns=colunas_saida)
        df_opt["REGIONAL"] = df_opt["REGIONAL"].astype(str).str.strip().str.upper().str[:3]
        return df_opt[colunas_saida]

    try:
        header_df = _read_excel_with_copy_fallback(path_obj, nrows=0)
    except Exception:
        return pd.DataFrame(columns=colunas_saida)

    coluna_data = encontrar_coluna_por_alias(
        header_df.columns,
        "DAT_REFERENCIA",
        "DATA_REFERENCIA",
        "DAT REFERENCIA"
    )
    coluna_regional = encontrar_coluna_por_alias(
        header_df.columns,
        "DSC_REGIONAL_CMV",
        "DSC REGIONAL CMV",
        "REGIONAL_CMV",
        "REGIONAL CMV",
        "DSC_REGIONAL",
        "REGIONAL"
    )
    coluna_qtde = encontrar_coluna_por_alias(
        header_df.columns,
        "QTDE_FINAL",
        "QTDE FINAL",
        "QTDE",
        "QTD",
        "QUANTIDADE"
    )

    if not coluna_data or not coluna_regional or not coluna_qtde:
        return pd.DataFrame(columns=colunas_saida)

    try:
        df = _read_excel_with_copy_fallback(
            path_obj,
            usecols=[coluna_data, coluna_regional, coluna_qtde]
        )
    except Exception:
        return pd.DataFrame(columns=colunas_saida)

    if df is None or df.empty:
        return pd.DataFrame(columns=colunas_saida)

    df = df.rename(
        columns={
            coluna_data: "DAT_REFERENCIA",
            coluna_regional: "REGIONAL",
            coluna_qtde: "QTDE",
        }
    ).copy()

    df["DAT_REFERENCIA"] = pd.to_datetime(
        df["DAT_REFERENCIA"],
        format="mixed",
        errors="coerce",
        dayfirst=True
    )
    df["REGIONAL"] = (
        df["REGIONAL"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str[:3]
        .replace({"": pd.NA, "NAN": pd.NA, "NON": pd.NA, "NUL": pd.NA})
    )
    df["QTDE"] = normalizar_numerico_serie(df["QTDE"]).fillna(0.0)

    df = df[
        df["DAT_REFERENCIA"].notna() &
        df["REGIONAL"].notna()
    ].copy()

    if df.empty:
        return pd.DataFrame(columns=colunas_saida)

    df["MES_ANO"] = df["DAT_REFERENCIA"].map(_formatar_mes_ano_backlog)
    df = df[df["MES_ANO"].notna()].copy()

    compactar_colunas_categoricas(df, ["REGIONAL", "MES_ANO"])
    return df[colunas_saida]

def _prever_tendencia_mensal_migracoes(valores_hist: pd.Series | np.ndarray | list[float]) -> float:
    """
    Projeta o próximo mês pela média móvel dos 2 últimos meses realizados
    com crescimento adicional de 10%.
    """
    serie = pd.Series(valores_hist, dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    if serie.empty:
        return 0.0

    y = serie.to_numpy(dtype=float)
    janela = y[-min(2, len(y)):]
    return float(max(np.mean(janela) * 1.10, 0.0))

def _resolver_coluna_mes_migracoes(colunas_valor: list[str], mes_ref: str | None) -> str:
    """Resolve mes selecionado para coluna real ou tendencia da tabela de migracoes."""
    if not colunas_valor:
        return ""
    mes_ref_norm = str(mes_ref or "").replace("TEND.", "").strip().lower()
    for coluna in colunas_valor:
        coluna_norm = str(coluna).replace("TEND.", "").strip().lower()
        if coluna_norm == mes_ref_norm:
            return str(coluna)
    return str(colunas_valor[-1])

def _formatar_mom_migracoes(valor: float | int | None) -> str:
    try:
        valor_float = float(valor)
    except Exception:
        valor_float = 0.0
    if valor_float > 0.05:
        seta = "▲"
    elif valor_float < -0.05:
        seta = "▼"
    else:
        seta = "•"
    return f"{seta} {valor_float:+.1f}%".replace(".", ",")

def obter_meses_mom_migracoes(df_migracoes: pd.DataFrame) -> tuple[list[str], str]:
    """Retorna meses reais e marca o ultimo mes carregado como tendencia."""
    if df_migracoes is None or df_migracoes.empty or "MES_ANO" not in df_migracoes.columns:
        return [], ""

    meses_reais = sorted(
        df_migracoes["MES_ANO"].dropna().astype(str).unique().tolist(),
        key=mes_ano_para_data
    )
    if not meses_reais:
        return [], ""

    mes_tendencia = str(meses_reais[-1]).strip().lower()
    return meses_reais, mes_tendencia

def montar_tabela_migracoes_pme_regionais(
    df_migracoes: pd.DataFrame,
    mes_mom_ref: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Monta a tabela regional x mes para migracoes PME somando QTDE."""
    colunas_vazias = ["REGIONAL"]
    if df_migracoes is None or df_migracoes.empty:
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    meses_ordem = sorted(
        df_migracoes["MES_ANO"].dropna().astype(str).unique().tolist(),
        key=mes_ano_para_data
    )
    if not meses_ordem:
        return pd.DataFrame(columns=colunas_vazias), pd.DataFrame(columns=colunas_vazias)

    tabela = pd.pivot_table(
        df_migracoes,
        index="REGIONAL",
        columns="MES_ANO",
        values="QTDE",
        aggfunc="sum",
        fill_value=0,
        observed=False
    ).reindex(columns=meses_ordem, fill_value=0)

    if tabela.empty:
        return pd.DataFrame(columns=["REGIONAL", *meses_ordem]), pd.DataFrame(columns=["REGIONAL", *meses_ordem])

    try:
        mes_tendencia_base = str(meses_ordem[-1]).strip().lower()
        data_ref_tend = pd.Timestamp(mes_ano_para_data(mes_tendencia_base)).normalize()
        coluna_tendencia = f"TEND. {_formatar_mes_ano_backlog(data_ref_tend)}"
    except Exception:
        mes_tendencia_base = str(meses_ordem[-1]).strip().lower()
        data_ref_tend = pd.Timestamp(mes_ano_para_data(meses_ordem[-1])).normalize()
        coluna_tendencia = f"TEND. {str(meses_ordem[-1]).strip()}"

    meses_historico_tendencia = [
        mes for mes in meses_ordem
        if pd.Timestamp(mes_ano_para_data(str(mes))).normalize() < data_ref_tend
    ]
    if not meses_historico_tendencia:
        meses_historico_tendencia = meses_ordem.copy()

    tabela[coluna_tendencia] = [
        _prever_tendencia_mensal_migracoes(
            pd.to_numeric(tabela.loc[regional, meses_historico_tendencia], errors="coerce").fillna(0.0)
        )
        for regional in tabela.index
    ]

    meses_valor_reais = [
        mes for mes in meses_ordem
        if pd.Timestamp(mes_ano_para_data(str(mes))).normalize() < data_ref_tend
    ]
    colunas_valor = [*meses_valor_reais, coluna_tendencia]
    coluna_mom_ref = _resolver_coluna_mes_migracoes(colunas_valor, mes_mom_ref)
    mes_mom_base = str(coluna_mom_ref).replace("TEND.", "").strip()
    mes_mom_anterior = get_mes_anterior(mes_mom_base)
    coluna_mom_anterior = _resolver_coluna_mes_migracoes(colunas_valor, mes_mom_anterior)
    if str(coluna_mom_anterior).replace("TEND.", "").strip().lower() != str(mes_mom_anterior).strip().lower():
        coluna_mom_anterior = ""
    try:
        data_mom_ref = pd.Timestamp(mes_ano_para_data(mes_mom_base)).normalize()
    except Exception:
        data_mom_ref = pd.Timestamp(mes_ano_para_data(meses_ordem[-1])).normalize()
    eh_coluna_tendencia = str(coluna_mom_ref).strip().upper().startswith("TEND.")
    if eh_coluna_tendencia:
        meses_exibicao = [
            mes for mes in meses_ordem
            if pd.Timestamp(mes_ano_para_data(str(mes))).normalize() < data_mom_ref
        ]
    else:
        meses_exibicao = [
            mes for mes in meses_ordem
            if pd.Timestamp(mes_ano_para_data(str(mes))).normalize() <= data_mom_ref
        ]
    if not meses_exibicao:
        meses_exibicao = [meses_ordem[0]]
    colunas_exibicao_valor = meses_exibicao.copy()
    if eh_coluna_tendencia and coluna_tendencia not in colunas_exibicao_valor:
        colunas_exibicao_valor.append(coluna_tendencia)

    def _calc_mom(row: pd.Series) -> float:
        valor_atual = float(pd.to_numeric(pd.Series([row.get(coluna_mom_ref, 0.0)]), errors="coerce").fillna(0.0).iloc[0])
        valor_anterior = (
            float(pd.to_numeric(pd.Series([row.get(coluna_mom_anterior, 0.0)]), errors="coerce").fillna(0.0).iloc[0])
            if coluna_mom_anterior else 0.0
        )
        return ((valor_atual / valor_anterior) - 1.0) * 100.0 if valor_anterior > 0 else 0.0

    tabela["MoM"] = tabela.apply(_calc_mom, axis=1)
    if coluna_mom_ref in tabela.columns:
        tabela = tabela.sort_values(by=[coluna_mom_ref, "MoM"], ascending=[False, False])
    else:
        tabela = tabela.sort_index()

    totais_mes = (
        df_migracoes.groupby("MES_ANO", observed=True)["QTDE"]
        .sum()
        .reindex(meses_ordem, fill_value=0)
    )

    colunas_saida_numericas = [*colunas_exibicao_valor, "MoM"]
    df_num = tabela[colunas_saida_numericas].reset_index()
    linha_total = {"REGIONAL": "TOTAL"}
    for mes in meses_exibicao:
        linha_total[mes] = float(totais_mes.get(mes, 0))
    if coluna_tendencia in colunas_exibicao_valor:
        linha_total[coluna_tendencia] = float(pd.to_numeric(tabela[coluna_tendencia], errors="coerce").fillna(0.0).sum())
    valor_total_atual = float(linha_total.get(coluna_mom_ref, 0.0))
    valor_total_anterior = float(linha_total.get(coluna_mom_anterior, 0.0)) if coluna_mom_anterior else 0.0
    linha_total["MoM"] = ((valor_total_atual / valor_total_anterior) - 1.0) * 100.0 if valor_total_anterior > 0 else 0.0
    df_num = pd.concat([pd.DataFrame([linha_total]), df_num], ignore_index=True)

    df_fmt = df_num.copy().astype(object)
    for col in df_fmt.columns:
        if col == "REGIONAL":
            continue
        serie_col = pd.to_numeric(df_fmt[col], errors="coerce").fillna(0.0)
        if col == "MoM":
            df_fmt[col] = serie_col.apply(_formatar_mom_migracoes)
        else:
            df_fmt[col] = serie_col.apply(lambda valor: formatar_numero_brasileiro(valor, 0))
    return df_fmt, df_num

def montar_serie_grafico_migracoes_pme(
    df_tabela_numerica: pd.DataFrame,
    regional_ref: str = "Todos"
) -> pd.DataFrame:
    """Converte a tabela numerica de migracoes PME em serie mensal para o grafico."""
    if df_tabela_numerica is None or df_tabela_numerica.empty or "REGIONAL" not in df_tabela_numerica.columns:
        return pd.DataFrame(columns=["MES_ANO", "MES_LABEL", "QTDE", "TIPO"])

    regional_busca = "TOTAL" if str(regional_ref).strip().lower() in {"", "todos"} else str(regional_ref).strip()
    linha = df_tabela_numerica[df_tabela_numerica["REGIONAL"].astype(str).str.strip().eq(regional_busca)].copy()
    if linha.empty:
        return pd.DataFrame(columns=["MES_ANO", "MES_LABEL", "QTDE", "TIPO"])

    row = linha.iloc[0]
    registros: list[dict[str, object]] = []
    for coluna in df_tabela_numerica.columns:
        if coluna in {"REGIONAL", "MoM"}:
            continue
        coluna_str = str(coluna).strip()
        eh_tendencia = coluna_str.upper().startswith("TEND.")
        mes_ref = coluna_str.replace("TEND.", "").strip() if eh_tendencia else coluna_str
        valor = float(pd.to_numeric(pd.Series([row[coluna]]), errors="coerce").fillna(0.0).iloc[0])
        registros.append(
            {
                "MES_ANO": mes_ref,
                "MES_LABEL": mes_ref.upper(),
                "QTDE": valor,
                "TIPO": "TENDENCIA" if eh_tendencia else "REALIZADO",
            }
        )

    if not registros:
        return pd.DataFrame(columns=["MES_ANO", "MES_LABEL", "QTDE", "TIPO"])

    df_serie = pd.DataFrame(registros)
    df_serie = df_serie.sort_values(
        by="MES_ANO",
        key=lambda serie: serie.map(lambda valor: pd.Timestamp(mes_ano_para_data(str(valor))))
    ).reset_index(drop=True)
    return df_serie

def criar_grafico_migracoes_pme_mensal(
    df_serie: pd.DataFrame,
    regional_ref: str = "Todos",
    altura: int = 340
) -> go.Figure:
    """Cria gráfico mensal de colunas para Migracoes PME com destaque para a tendencia."""
    if df_serie is None or df_serie.empty:
        return go.Figure()

    categorias = df_serie["MES_LABEL"].astype(str).tolist()
    valores = pd.to_numeric(df_serie["QTDE"], errors="coerce").fillna(0.0).astype(float).tolist()
    tipos = df_serie["TIPO"].astype(str).tolist()

    palette_real = [
        "#5A0A06", "#63100B", "#6D1511", "#761A16", "#7F1F1B",
        "#882421", "#922927", "#9B2E2C", "#A43332", "#AE3937"
    ]
    cores: list[str] = []
    idx_real = 0
    for tipo in tipos:
        if str(tipo).upper() == "TENDENCIA":
            cores.append("#D96A5F")
        else:
            cores.append(palette_real[min(idx_real, len(palette_real) - 1)])
            idx_real += 1

    comparacoes: list[dict[str, int]] = []
    idx_tend = next((idx for idx, tipo in enumerate(tipos) if str(tipo).upper() == "TENDENCIA"), None)
    if idx_tend is not None and idx_tend > 0:
        comparacoes.append({"origem": idx_tend - 1, "destino": idx_tend})
    elif len(categorias) >= 2:
        comparacoes.append({"origem": len(categorias) - 2, "destino": len(categorias) - 1})

    fig = _criar_grafico_barras_resumo_comparativo(
        categorias=categorias,
        valores=valores,
        cores=cores,
        altura=altura,
        comparacoes=comparacoes
    )

    customdata = [
        [categorias[idx], "Tendência" if str(tipos[idx]).upper() == "TENDENCIA" else "Realizado"]
        for idx in range(len(categorias))
    ]
    fig.update_traces(
        customdata=customdata,
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "<b>Tipo:</b> %{customdata[1]}<br>"
            "<b>QTDE:</b> %{y:,.0f}<extra></extra>"
        )
    )
    fig.update_layout(
        paper_bgcolor="#FCFCFD",
        plot_bgcolor="#FFFFFF",
        margin=dict(l=16, r=16, t=60, b=44),
        hoverlabel=dict(
            bgcolor="white",
            font_size=12,
            font_family="Segoe UI",
            bordercolor="#E2E8F0",
            font_color="#2F3747"
        )
    )

    if idx_tend is not None:
        fig.add_annotation(
            x=0.995,
            y=1.11,
            xref="paper",
            yref="paper",
            xanchor="right",
            yanchor="top",
            text="<b>Barra clara = Tendência</b>",
            showarrow=False,
            font=dict(size=11, color="#A23B36"),
            bgcolor="rgba(255,255,255,0.96)",
            bordercolor="rgba(162, 59, 54, 0.34)",
            borderwidth=1.0,
            borderpad=5
        )

    return fig

@st.cache_data(ttl=3600, show_spinner=False, max_entries=2)
def load_ligacoes_raw_tratada(path: str = LIGACOES_FILE_PATH, file_mtime: float | None = None) -> pd.DataFrame:
    """Carrega e normaliza a base bruta de ligações uma única vez para reaproveitamento no app."""
    path_obj = Path(path)
    _ = file_mtime
    if not path_obj.exists():
        return pd.DataFrame()

    ligacoes_mtime = file_mtime if file_mtime is not None else path_obj.stat().st_mtime
    suffixes = [s.lower() for s in path_obj.suffixes]

    if path_obj.suffix.lower() == ".parquet" or ".csv" in suffixes:
        try:
            df_opt = load_tabular_cached(str(path_obj), ligacoes_mtime)
        except Exception:
            return pd.DataFrame()
        if df_opt is None or df_opt.empty:
            return pd.DataFrame()

        if "DATA_DIA" not in df_opt.columns and "DAT_MOVIMENTO2" in df_opt.columns:
            df_opt["DATA_DIA"] = pd.to_datetime(df_opt["DAT_MOVIMENTO2"], errors="coerce").dt.normalize()
        elif "DATA_DIA" in df_opt.columns:
            df_opt["DATA_DIA"] = pd.to_datetime(df_opt["DATA_DIA"], errors="coerce").dt.normalize()

        if "DAT_MOVIMENTO2" not in df_opt.columns and "DATA_DIA" in df_opt.columns:
            df_opt["DAT_MOVIMENTO2"] = pd.to_datetime(df_opt["DATA_DIA"], errors="coerce").dt.normalize()
        elif "DAT_MOVIMENTO2" in df_opt.columns:
            df_opt["DAT_MOVIMENTO2"] = pd.to_datetime(df_opt["DAT_MOVIMENTO2"], errors="coerce").dt.normalize()

        if "mes_ano" not in df_opt.columns and "DATA_DIA" in df_opt.columns:
            df_opt["mes_ano"] = df_opt["DATA_DIA"].apply(
                lambda dt: _formatar_mes_ano_backlog(dt) if pd.notna(dt) else None
            )
        if "dat_tratada" not in df_opt.columns and "mes_ano" in df_opt.columns:
            df_opt["dat_tratada"] = df_opt["mes_ano"]
        if "REGIONAL" not in df_opt.columns:
            df_opt["REGIONAL"] = ""
        if "CANAL_PLAN" not in df_opt.columns:
            df_opt["CANAL_PLAN"] = "Televendas Receptivo"
        if "COD_PLATAFORMA" not in df_opt.columns:
            df_opt["COD_PLATAFORMA"] = np.where(
                pd.Series(df_opt.get("FLAG_FIXA", False)).astype(bool),
                "FIXA",
                "CONTA"
            )
        if "DSC_INDICADOR" not in df_opt.columns:
            df_opt["DSC_INDICADOR"] = "LIGACOES"
        if "CABEADO" not in df_opt.columns:
            df_opt["CABEADO"] = np.where(
                pd.Series(df_opt.get("FLAG_FIXA", False)).astype(bool),
                "SIM",
                "NAO"
            )
        if "TIPO_CHAMADA" not in df_opt.columns:
            df_opt["TIPO_CHAMADA"] = "DEMAIS"
        if "FLAG_FIXA" not in df_opt.columns:
            df_opt["FLAG_FIXA"] = df_opt["CABEADO"].astype(str).str.strip().str.upper().isin({'SIM', 'S', 'TRUE', '1', 'FIXA'})
        if "DESAFIO_QTD" not in df_opt.columns:
            df_opt["DESAFIO_QTD"] = 0.0
        if "TELEFONE" not in df_opt.columns:
            df_opt["TELEFONE"] = ""

        df_opt["REGIONAL"] = df_opt["REGIONAL"].astype(str).str.strip().str[:3].str.upper()
        df_opt["mes_ano"] = df_opt["mes_ano"].astype(str).str.strip()
        df_opt["dat_tratada"] = df_opt["dat_tratada"].astype(str).str.strip()
        df_opt["QTDE"] = normalizar_numerico_serie(df_opt.get("QTDE", 0)).fillna(0.0)
        df_opt["DESAFIO_QTD"] = normalizar_numerico_serie(df_opt.get("DESAFIO_QTD", 0)).fillna(0.0)
        df_opt["FLAG_FIXA"] = pd.Series(df_opt["FLAG_FIXA"]).astype(bool)

        colunas_saida = [
            'DAT_MOVIMENTO2', 'DATA_DIA', 'mes_ano', 'dat_tratada', 'REGIONAL',
            'CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'QTDE', 'DESAFIO_QTD',
            'CABEADO', 'TIPO_CHAMADA', 'TELEFONE', 'FLAG_FIXA'
        ]
        df_saida = df_opt[[c for c in colunas_saida if c in df_opt.columns]].copy()
        compactar_colunas_categoricas(
            df_saida,
            ['mes_ano', 'dat_tratada', 'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'CABEADO', 'TIPO_CHAMADA']
        )
        return df_saida

    header_df = load_excel_cached(str(path_obj), ligacoes_mtime, nrows=0)
    if header_df is None:
        return pd.DataFrame()

    colunas_disponiveis = set(header_df.columns)
    req_cols = {'QTD', 'CABEADO'}
    if not req_cols.issubset(colunas_disponiveis):
        return pd.DataFrame()

    coluna_data = None
    for col_ref in ['DATA_MOVIMENTO', 'DAT_MOVIMENTO', 'DAT_MOVIMENTO2', 'PERIODO']:
        if col_ref in colunas_disponiveis:
            coluna_data = col_ref
            break
    if coluna_data is None:
        return pd.DataFrame()

    colunas_leitura = ['QTD', 'CABEADO', coluna_data]
    for col_opcional in ['DSC_REGIONAL_CMV', 'REGIONAL', 'TELEFONE']:
        if col_opcional in colunas_disponiveis and col_opcional not in colunas_leitura:
            colunas_leitura.append(col_opcional)

    df_lig = load_excel_cached(str(path_obj), ligacoes_mtime, usecols=colunas_leitura)
    if df_lig is None or df_lig.empty:
        return pd.DataFrame()

    serie_data_ref = pd.to_datetime(df_lig[coluna_data], errors='coerce')
    mask_data_valida = serie_data_ref.notna()
    df_work = df_lig.loc[mask_data_valida].copy()
    df_work['DAT_MOVIMENTO2'] = serie_data_ref.loc[df_work.index]
    if df_work.empty:
        return pd.DataFrame()

    meses_pt = {
        1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
        7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
    }
    df_work['mes_ano'] = df_work['DAT_MOVIMENTO2'].apply(
        lambda dt: f"{meses_pt.get(dt.month, 'jan')}/{dt.strftime('%y')}"
    )
    df_work['dat_tratada'] = df_work['mes_ano']

    col_reg = next((c for c in ['DSC_REGIONAL_CMV', 'REGIONAL'] if c in df_work.columns), None)
    if col_reg is not None:
        df_work['REGIONAL'] = df_work[col_reg].astype(str).str.strip().str[:3].str.upper()
    else:
        df_work['REGIONAL'] = ''

    df_work['QTDE'] = pd.to_numeric(df_work['QTD'], errors='coerce').fillna(0.0)
    telefone_ref = df_work['TELEFONE'].astype(str) if 'TELEFONE' in df_work.columns else pd.Series('', index=df_work.index)
    df_work['TIPO_CHAMADA'] = np.where(
        telefone_ref.str.contains('0960|8449', regex=True, na=False),
        'Click to Call',
        'DEMAIS'
    )

    cabeado_norm = df_work['CABEADO'].astype(str).str.strip().str.upper()
    mask_fixa = cabeado_norm.isin({'SIM', 'S', 'TRUE', '1', 'FIXA'})
    df_work['FLAG_FIXA'] = mask_fixa
    df_work['COD_PLATAFORMA'] = np.where(mask_fixa, 'FIXA', 'CONTA')
    df_work['CANAL_PLAN'] = 'Televendas Receptivo'
    df_work['DSC_INDICADOR'] = 'LIGACOES'
    df_work['DESAFIO_QTD'] = 0.0
    if 'TELEFONE' not in df_work.columns:
        df_work['TELEFONE'] = ''
    df_work['DATA_DIA'] = pd.to_datetime(df_work['DAT_MOVIMENTO2'], errors='coerce').dt.normalize()

    colunas_saida = [
        'DAT_MOVIMENTO2', 'DATA_DIA', 'mes_ano', 'dat_tratada', 'REGIONAL',
        'CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'QTDE', 'DESAFIO_QTD',
        'CABEADO', 'TIPO_CHAMADA', 'TELEFONE', 'FLAG_FIXA'
    ]
    df_saida = df_work[[c for c in colunas_saida if c in df_work.columns]]
    compactar_colunas_categoricas(
        df_saida,
        ['mes_ano', 'dat_tratada', 'REGIONAL', 'CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'CABEADO', 'TIPO_CHAMADA']
    )
    del df_lig, df_work, serie_data_ref
    gc.collect()
    return df_saida

def mes_ano_para_data(mes_ano_str: str) -> datetime:
    """Converte string 'mes/ano' para objeto datetime"""
    try:
        meses_pt = {
            'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
            'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12
        }
        mes_str, ano_str = mes_ano_str.lower().split('/')
        mes_num = meses_pt.get(mes_str, 1)
        ano_num = int(f"20{ano_str}")
        return datetime(ano_num, mes_num, 1)
    except:
        return datetime(1900, 1, 1)

def get_mes_atual_formatado() -> str:
    """Retorna o mês atual no formato 'mmm/aa'"""
    try:
        hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    except Exception:
        hoje = date.today()
    meses_abreviados = {
        1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
        7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
    }
    mes_abrev = meses_abreviados.get(hoje.month, 'jan')
    ano_abrev = str(hoje.year)[-2:]
    return f"{mes_abrev}/{ano_abrev}"

def get_mes_anterior(mes_atual: str) -> str:
    """Retorna o mês anterior baseado no mês atual no formato 'mmm/aa'"""
    try:
        meses_pt = {
            'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
            'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12
        }
        meses_reverso = {v: k for k, v in meses_pt.items()}
        
        mes_str, ano_str = mes_atual.lower().split('/')
        mes_num = meses_pt.get(mes_str, 1)
        ano_num = int(f"20{ano_str}")
        
        if mes_num == 1:  # Janeiro
            mes_anterior_num = 12
            ano_anterior_num = ano_num - 1
        else:
            mes_anterior_num = mes_num - 1
            ano_anterior_num = ano_num
        
        mes_anterior_str = meses_reverso.get(mes_anterior_num, 'jan')
        ano_anterior_str = str(ano_anterior_num)[-2:]
        
        return f"{mes_anterior_str}/{ano_anterior_str}"
    except:
        return mes_atual

def normalizar_mes_dashboard(mes_ref: str | None) -> str:
    """Normaliza mês do dashboard para comparar regras de realizado/tendência."""
    return str(mes_ref or "").strip().lower()

def eh_mes_atual_dashboard(mes_ref: str | None, mes_corrente: str | None = None) -> bool:
    """Indica se o mês selecionado é o mês corrente usado para tendência."""
    mes_corrente_norm = normalizar_mes_dashboard(mes_corrente or get_mes_atual_formatado())
    return bool(normalizar_mes_dashboard(mes_ref) == mes_corrente_norm)

def deve_usar_tendencia_dashboard(
    mes_ref: str | None,
    valor_tendencia: float | int | None,
    mes_corrente: str | None = None
) -> bool:
    """Regra única: usar tendência somente no mês corrente e quando houver valor positivo."""
    try:
        tendencia = float(valor_tendencia or 0.0)
    except Exception:
        tendencia = 0.0
    return eh_mes_atual_dashboard(mes_ref, mes_corrente) and tendencia > 0

def escolher_valor_realizado_ou_tendencia(
    valor_realizado: float | int | None,
    valor_tendencia: float | int | None,
    mes_ref: str | None,
    mes_corrente: str | None = None
) -> tuple[float, bool]:
    """Retorna o valor exibido e se ele veio da tendência."""
    try:
        real = float(valor_realizado or 0.0)
    except Exception:
        real = 0.0
    try:
        tendencia = float(valor_tendencia or 0.0)
    except Exception:
        tendencia = 0.0
    usar_tendencia = deve_usar_tendencia_dashboard(mes_ref, tendencia, mes_corrente)
    return (tendencia if usar_tendencia else real), usar_tendencia

def obter_mes_referencia_grafico(mes_ref: str | None = None) -> tuple[int, str]:
    """Extrai o mês de referência selecionado no dashboard com fallback para o mês atual."""
    mes_ref_txt = str(mes_ref or "").strip().lower()
    if mes_ref_txt:
        try:
            data_ref = pd.Timestamp(mes_ano_para_data(mes_ref_txt)).normalize()
            if int(data_ref.year) >= 2000:
                return int(data_ref.month), str(mes_ref_txt).upper()
        except Exception:
            pass

    mes_atual = get_mes_atual_formatado()
    data_atual = pd.Timestamp(mes_ano_para_data(mes_atual)).normalize()
    return int(data_atual.month), str(mes_atual).upper()

def gerar_intervalo_meses_retroativos(mes_final: str, qtd_meses: int = 13) -> list[str]:
    """Retorna os últimos N meses até o mês de referência no formato mmm/aa."""
    try:
        data_final = pd.Timestamp(mes_ano_para_data(str(mes_final))).normalize()
    except Exception:
        return [str(mes_final).strip().lower()]

    intervalo = []
    for deslocamento in range(max(int(qtd_meses), 1) - 1, -1, -1):
        data_ref = data_final - pd.DateOffset(months=deslocamento)
        intervalo.append(_formatar_mes_ano_backlog(data_ref).lower())
    return intervalo

def obter_janela_meses_disponiveis(
    mes_ref: str,
    meses_disponiveis: list[str] | pd.Series,
    qtd_meses: int = 13
) -> list[str]:
    """Retorna a janela cronológica dos últimos N meses existentes até o mês de referência."""
    meses_validos = sorted(
        list({
            str(mes).strip().lower()
            for mes in list(meses_disponiveis or [])
            if re.match(r"^[a-z]{3}/\d{2}$", str(mes).strip(), flags=re.IGNORECASE)
        }),
        key=mes_ano_para_data
    )
    if not meses_validos:
        return []

    mes_ref_norm = str(mes_ref).strip().lower()
    if mes_ref_norm not in meses_validos:
        mes_ref_norm = meses_validos[-1]

    janela = gerar_intervalo_meses_retroativos(mes_ref_norm, qtd_meses=qtd_meses)
    meses_set = set(meses_validos)
    return [mes for mes in janela if mes in meses_set]

def _obter_cores_serie_mensal_produto(produto_ref: str) -> tuple[str, str, str, str]:
    """Define a paleta do gráfico mensal conforme o produto selecionado."""
    produto_norm = normalizar_rotulo_produto(produto_ref)
    if produto_norm == 'FIXA':
        return '#475569', '#64748B', '#334155', '#5A6268'
    if produto_norm == 'CONTA':
        return '#790E09', '#FF2800', '#790E09', '#5A6268'
    return '#8D1A12', '#FF2800', '#790E09', '#5A6268'

def obter_valor_serie_mensal_mes(
    mes_item: str,
    mes_foco: str,
    mes_corrente: str,
    lookup_real: dict[str, float] | None = None,
    lookup_tend: dict[str, float] | None = None
) -> float:
    """Retorna o valor do mês usando tendência apenas no mês atual selecionado."""
    mes_item_norm = normalizar_mes_dashboard(mes_item)
    mes_foco_norm = normalizar_mes_dashboard(mes_foco)
    mes_corrente_norm = normalizar_mes_dashboard(mes_corrente)
    lookup_real = {str(k).strip().lower(): float(v or 0.0) for k, v in (lookup_real or {}).items()}
    lookup_tend = {str(k).strip().lower(): float(v or 0.0) for k, v in (lookup_tend or {}).items()}

    valor_real = float(lookup_real.get(mes_item_norm, 0.0))
    valor_tend = float(lookup_tend.get(mes_item_norm, 0.0))
    valor_exibido, _ = escolher_valor_realizado_ou_tendencia(
        valor_realizado=valor_real,
        valor_tendencia=valor_tend,
        mes_ref=mes_item_norm if mes_item_norm == mes_foco_norm else "",
        mes_corrente=mes_corrente_norm
    )
    return valor_exibido

def get_mes_ano_anterior(mes_ref: str) -> str:
    """Retorna o mesmo mês do ano anterior no formato mmm/aa."""
    try:
        data_ref = pd.Timestamp(mes_ano_para_data(str(mes_ref).strip().lower())).normalize()
        return str(_formatar_mes_ano_backlog(data_ref - pd.DateOffset(years=1))).lower()
    except Exception:
        return str(mes_ref or "").strip().lower()

def obter_meses_ytd_ano(mes_ref: str, ano_curto: str | int) -> list[str]:
    """Retorna jan..mês_ref para o ano indicado, usado nos comparativos YTD."""
    try:
        data_ref = pd.Timestamp(mes_ano_para_data(str(mes_ref).strip().lower())).normalize()
        meses_ordem_local = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
        ano_txt = str(ano_curto).strip()[-2:].zfill(2)
        return [f"{meses_ordem_local[idx]}/{ano_txt}" for idx in range(int(data_ref.month))]
    except Exception:
        return []

def calcular_variacao_percentual(valor_atual: float | int | None, valor_base: float | int | None) -> float:
    """Calcula variação percentual padrão do dashboard, retornando 0 quando a base é nula."""
    try:
        atual = float(valor_atual or 0.0)
        base = float(valor_base or 0.0)
    except Exception:
        return 0.0
    if base <= 0:
        return 0.0
    return ((atual / base) - 1.0) * 100.0

def _normalizar_lookup_mensal(lookup: dict | None) -> dict[str, float]:
    """Normaliza chaves de mês e valores numéricos para cálculos rápidos de tabela."""
    saida: dict[str, float] = {}
    for chave, valor in (lookup or {}).items():
        chave_norm = str(chave).strip().lower()
        if not chave_norm:
            continue
        try:
            saida[chave_norm] = float(valor or 0.0)
        except Exception:
            saida[chave_norm] = 0.0
    return saida

def calcular_yoy_ytd_mensal_lookup(
    lookup_real: dict[str, float] | None,
    lookup_tend: dict[str, float] | None,
    mes_ref: str,
    mes_corrente: str | None = None,
    lookup_orc: dict[str, float] | None = None
) -> dict[str, float]:
    """
    Calcula YoY/YTD no padrão executivo:
    - valor do mês foco usa tendência apenas quando o mês foco é o mês corrente;
    - YTD26 soma jan/26..mês foco, usando tendência no mês corrente;
    - YTD25 soma jan/25..mesmo mês de 2025.
    """
    mes_ref_norm = str(mes_ref or "").strip().lower()
    mes_corrente_norm = str(mes_corrente or get_mes_atual_formatado()).strip().lower()
    lookup_real_norm = _normalizar_lookup_mensal(lookup_real)
    lookup_tend_norm = _normalizar_lookup_mensal(lookup_tend)
    lookup_orc_norm = _normalizar_lookup_mensal(lookup_orc)

    valor_atual = obter_valor_serie_mensal_mes(
        mes_ref_norm,
        mes_ref_norm,
        mes_corrente_norm,
        lookup_real_norm,
        lookup_tend_norm
    )
    mes_yoy = get_mes_ano_anterior(mes_ref_norm)
    valor_yoy_base = float(lookup_real_norm.get(mes_yoy, 0.0))

    meses_ytd_26 = obter_meses_ytd_ano(mes_ref_norm, "26")
    meses_ytd_25 = obter_meses_ytd_ano(mes_ref_norm, "25")
    ytd26 = float(sum(
        obter_valor_serie_mensal_mes(
            mes_item,
            mes_ref_norm,
            mes_corrente_norm,
            lookup_real_norm,
            lookup_tend_norm
        )
        for mes_item in meses_ytd_26
    ))
    ytd25 = float(sum(float(lookup_real_norm.get(mes_item, 0.0)) for mes_item in meses_ytd_25))
    ytd_orc = float(sum(float(lookup_orc_norm.get(mes_item, 0.0)) for mes_item in meses_ytd_26))
    ytd26_vs_ytd25 = calcular_variacao_percentual(ytd26, ytd25)
    ytd26_vs_orc = calcular_variacao_percentual(ytd26, ytd_orc)

    return {
        "YOY": calcular_variacao_percentual(valor_atual, valor_yoy_base),
        "YTD25": ytd25,
        "YTD26": ytd26,
        "YTD_ORÇ": ytd_orc,
        "% YTD": ytd26_vs_ytd25,
        "YTD26 vs YTD25": ytd26_vs_ytd25,
        "YTD26 vs YTD_ORÇ": ytd26_vs_orc,
    }

def criar_grafico_serie_mensal_comparativos(
    mes_foco: str,
    lookup_real: dict[str, float],
    lookup_tend: dict[str, float] | None = None,
    lookup_meta: dict[str, float] | None = None,
    produto_ref: str = 'CONTA',
    titulo: str = 'SÉRIE MENSAL E COMPARATIVOS',
    subtitulo: str = '',
    altura: int = 250,
    linha_valores: list[float] | None = None,
    linha_nome: str = 'Demanda',
    linha_cor: str = '#7A7F87'
) -> go.Figure:
    """Cria gráfico mensal com 13 meses + M-1, mês foco e orçamento."""
    mes_foco_norm = str(mes_foco).strip().lower()
    meses_janela = gerar_intervalo_meses_retroativos(mes_foco_norm, qtd_meses=13)
    mes_m1 = get_mes_anterior(mes_foco_norm)
    mes_corrente = get_mes_atual_formatado().strip().lower()

    lookup_real = {str(k).strip().lower(): float(v or 0.0) for k, v in (lookup_real or {}).items()}
    lookup_tend = {str(k).strip().lower(): float(v or 0.0) for k, v in (lookup_tend or {}).items()}
    lookup_meta = {str(k).strip().lower(): float(v or 0.0) for k, v in (lookup_meta or {}).items()}

    valores_janela: list[float] = []
    for mes_item in meses_janela:
        valores_janela.append(
            obter_valor_serie_mensal_mes(
                mes_item=mes_item,
                mes_foco=mes_foco_norm,
                mes_corrente=mes_corrente,
                lookup_real=lookup_real,
                lookup_tend=lookup_tend
            )
        )

    valor_foco = float(valores_janela[-1]) if valores_janela else 0.0
    valor_m1 = float(lookup_real.get(mes_m1, 0.0))
    valor_orc = float(lookup_meta.get(mes_foco_norm, 0.0))
    linha_valores_norm = (
        [float(v or 0.0) for v in list(linha_valores or [])[:len(meses_janela)]]
        if linha_valores is not None else None
    )
    if linha_valores_norm is not None and len(linha_valores_norm) < len(meses_janela):
        linha_valores_norm.extend([0.0] * (len(meses_janela) - len(linha_valores_norm)))

    cor_base, cor_foco, cor_m1, cor_orc = _obter_cores_serie_mensal_produto(produto_ref)
    rotulo_mes_foco = str(mes_foco_norm).strip().upper()

    categorias_plot = [str(mes).strip().upper() for mes in meses_janela] + ['M-1', rotulo_mes_foco, 'ORÇAMENTO']
    valores_plot = valores_janela + [valor_m1, valor_foco, valor_orc]
    cores_plot = ([cor_base] * len(meses_janela)) + [cor_m1, cor_foco, cor_orc]
    posicoes_plot = list(range(len(meses_janela))) + [
        len(meses_janela) + 0.8,
        len(meses_janela) + 1.95,
        len(meses_janela) + 3.1
    ]
    larguras_plot = ([0.50] * len(meses_janela)) + [0.56, 0.56, 0.56]

    fig = _criar_grafico_barras_resumo_comparativo(
        categorias=categorias_plot,
        valores=valores_plot,
        cores=cores_plot,
        altura=altura,
        comparacoes=[
            {'origem': len(meses_janela), 'destino': len(meses_janela) + 1},
            {'origem': len(meses_janela) + 2, 'destino': len(meses_janela) + 1}
        ],
        posicoes_x=posicoes_plot,
        larguras=larguras_plot,
        rotulos_externos=True,
        offset_rotulo_externo=max(max(valores_plot) * 0.085, 3.0) if valores_plot else 3.0,
        folga_seta_rotulo=max(max(valores_plot) * 0.032, 1.0) if valores_plot else 1.0
    )

    if linha_valores_norm is not None and any(float(v) > 0 for v in linha_valores_norm):
        textos_linha = [
            formatar_numero_brasileiro(valor_linha, 0) if float(valor_linha) > 0 else ''
            for valor_linha in linha_valores_norm
        ]
        x_linha = posicoes_plot[:len(meses_janela)]

        fig.add_trace(go.Scatter(
            x=x_linha,
            y=linha_valores_norm,
            mode='lines',
            line=dict(
                color='rgba(122, 127, 135, 0.16)',
                width=9.5,
                shape='spline',
                smoothing=1.15
            ),
            hoverinfo='skip',
            showlegend=False,
            cliponaxis=False
        ))
        fig.add_trace(go.Scatter(
            x=x_linha,
            y=linha_valores_norm,
            mode='lines+markers+text',
            name=linha_nome,
            line=dict(color=linha_cor, width=3.2, shape='spline', smoothing=1.15),
            marker=dict(
                size=8.5,
                color=linha_cor,
                line=dict(color='#FFFFFF', width=1.5),
                symbol='circle'
            ),
            text=textos_linha,
            textposition='top center',
            textfont=dict(
                family='Segoe UI Semibold',
                size=10,
                color='#6B7280'
            ),
            customdata=[str(mes).strip().upper() for mes in meses_janela],
            hovertemplate=(
                "<b>%{customdata}</b><br>"
                f"<b>{linha_nome}:</b> " + "%{y:,.0f}<extra></extra>"
            ),
            cliponaxis=False,
            showlegend=True
        ))

        y_top_atual = 0.0
        if fig.layout.yaxis and fig.layout.yaxis.range and len(fig.layout.yaxis.range) > 1:
            try:
                y_top_atual = float(fig.layout.yaxis.range[1] or 0.0)
            except Exception:
                y_top_atual = 0.0
        max_linha = max(linha_valores_norm, default=0.0)
        y_top_final = max(y_top_atual, max_linha + max(max_linha * 0.12, 6.0))
        if y_top_final > 0:
            fig.update_yaxes(range=[0, y_top_final])

    fig.update_layout(
        plot_bgcolor='#FFFFFF',
        paper_bgcolor='#FFFFFF',
        font=dict(family='Segoe UI', size=13, color='#2F3747'),
        margin=dict(l=16, r=16, t=28, b=24),
        height=altura,
        title=dict(text=''),
        hoverlabel=dict(
            bgcolor='white',
            bordercolor='#E9ECEF',
            font_size=12,
            font_family='Segoe UI',
            font_color='#2F3747'
        ),
        showlegend=bool(linha_valores_norm is not None and any(float(v) > 0 for v in linha_valores_norm)),
        legend=dict(
            orientation='h',
            yanchor='bottom',
            y=1.01,
            xanchor='right',
            x=0.995,
            bgcolor='rgba(255,255,255,0.92)',
            bordercolor='rgba(148, 163, 184, 0.24)',
            borderwidth=1,
            font=dict(size=11, color='#5B6578')
        )
    )
    fig.update_xaxes(
        tickfont=dict(size=10, color='#5B6578'),
        showline=False,
        tickangle=0
    )
    fig.add_shape(
        type='line',
        xref='x',
        yref='paper',
        x0=12.35,
        x1=12.35,
        y0=0.06,
        y1=0.95,
        line=dict(color='rgba(148, 163, 184, 0.45)', width=1.0, dash='dot'),
        layer='above'
    )
    apply_standard_title_style(fig)
    return fig

def montar_lookups_demanda_ativados(
    df_base_principal: pd.DataFrame | None,
    produto_ref: str = 'Todos',
    regionais_ref: list[str] | None = None
) -> dict[str, dict[str, float]]:
    """Prepara lookups de demanda (Pedidos e Ligações) para a série mensal de Ativados."""
    produto_norm = normalizar_rotulo_produto(produto_ref)
    produto_filtro = produto_norm if produto_norm in {'CONTA', 'FIXA'} else 'TODOS'
    regionais_norm = {
        str(reg).strip()[:3].upper()
        for reg in (regionais_ref or [])
        if str(reg).strip()
    }
    resultado = {
        'pedidos_real': {},
        'pedidos_tend': {},
        'ligacoes_real': {},
        'ligacoes_tend': {}
    }

    def _filtrar_regionais(df_local: pd.DataFrame) -> pd.DataFrame:
        if df_local is None or df_local.empty or not regionais_norm or 'REGIONAL' not in df_local.columns:
            return df_local
        serie_reg = df_local['REGIONAL'].astype(str).str.strip().str[:3].str.upper()
        return df_local.loc[serie_reg.isin(regionais_norm)]

    if df_base_principal is not None and not df_base_principal.empty:
        colunas_pedidos = [
            col for col in ['CANAL_PLAN', 'DSC_INDICADOR', 'dat_tratada', 'REGIONAL', 'COD_PLATAFORMA', 'QTDE', 'TEND_QTD']
            if col in df_base_principal.columns
        ]
        if {'CANAL_PLAN', 'DSC_INDICADOR', 'dat_tratada', 'COD_PLATAFORMA', 'QTDE'}.issubset(colunas_pedidos):
            df_pedidos = df_base_principal.loc[:, colunas_pedidos].copy()
            mask_pedidos = (
                df_pedidos['CANAL_PLAN'].astype(str).str.strip().eq('E-Commerce') &
                df_pedidos['DSC_INDICADOR'].astype(str).str.strip().str.upper().eq('PEDIDOS')
            )
            df_pedidos = _filtrar_regionais(df_pedidos.loc[mask_pedidos])
            if df_pedidos is not None and not df_pedidos.empty:
                df_pedidos['dat_tratada'] = df_pedidos['dat_tratada'].astype(str).str.strip().str.lower()
                df_pedidos['COD_PLATAFORMA'] = df_pedidos['COD_PLATAFORMA'].apply(normalizar_rotulo_produto)
                if produto_filtro in {'CONTA', 'FIXA'}:
                    df_pedidos = df_pedidos.loc[df_pedidos['COD_PLATAFORMA'].eq(produto_filtro)]
                if 'TEND_QTD' not in df_pedidos.columns:
                    df_pedidos['TEND_QTD'] = 0.0
                if not df_pedidos.empty:
                    agg_pedidos = (
                        df_pedidos.groupby('dat_tratada', as_index=False, observed=True)[['QTDE', 'TEND_QTD']]
                        .sum()
                    )
                    resultado['pedidos_real'] = {
                        str(row['dat_tratada']).strip().lower(): float(row['QTDE'] or 0.0)
                        for _, row in agg_pedidos.iterrows()
                    }
                    resultado['pedidos_tend'] = {
                        str(row['dat_tratada']).strip().lower(): float(row['TEND_QTD'] or 0.0)
                        for _, row in agg_pedidos.iterrows()
                    }

    ligacoes_path = Path(LIGACOES_FILE_PATH)
    if ligacoes_path.exists():
        ligacoes_mtime = ligacoes_path.stat().st_mtime if ligacoes_path.exists() else None
        df_ligacoes = load_ligacoes_raw_tratada(LIGACOES_FILE_PATH, ligacoes_mtime)
        if df_ligacoes is not None and not df_ligacoes.empty:
            colunas_ligacoes = [
                col for col in ['dat_tratada', 'mes_ano', 'REGIONAL', 'QTDE', 'CABEADO', 'TIPO_CHAMADA', 'FLAG_FIXA']
                if col in df_ligacoes.columns
            ]
            df_ligacoes = _filtrar_regionais(df_ligacoes.loc[:, colunas_ligacoes])
            if df_ligacoes is not None and not df_ligacoes.empty:
                df_ligacoes = df_ligacoes.copy()
                if 'dat_tratada' in df_ligacoes.columns:
                    df_ligacoes['MES_REF'] = df_ligacoes['dat_tratada'].astype(str).str.strip().str.lower()
                else:
                    df_ligacoes['MES_REF'] = df_ligacoes['mes_ano'].astype(str).str.strip().str.lower()

                fixa_mask = pd.Series(False, index=df_ligacoes.index, dtype='boolean')
                if 'FLAG_FIXA' in df_ligacoes.columns:
                    fixa_mask = fixa_mask | pd.Series(df_ligacoes['FLAG_FIXA'], index=df_ligacoes.index).fillna(False).astype(bool)
                if 'CABEADO' in df_ligacoes.columns:
                    fixa_mask = fixa_mask | df_ligacoes['CABEADO'].astype(str).str.strip().str.upper().isin({'SIM', 'S', 'TRUE', '1', 'FIXA'})
                conta_mask = (
                    df_ligacoes['TIPO_CHAMADA'].astype(str).str.strip().str.upper().eq('DEMAIS')
                    if 'TIPO_CHAMADA' in df_ligacoes.columns
                    else pd.Series(False, index=df_ligacoes.index)
                )

                if produto_filtro == 'FIXA':
                    mask_ligacoes = fixa_mask
                elif produto_filtro == 'CONTA':
                    mask_ligacoes = conta_mask
                else:
                    mask_ligacoes = fixa_mask | conta_mask

                df_ligacoes = df_ligacoes.loc[mask_ligacoes].copy()
                if not df_ligacoes.empty:
                    agg_ligacoes = (
                        df_ligacoes.groupby('MES_REF', as_index=False, observed=True)[['QTDE']]
                        .sum()
                    )
                    resultado['ligacoes_real'] = {
                        str(row['MES_REF']).strip().lower(): float(row['QTDE'] or 0.0)
                        for _, row in agg_ligacoes.iterrows()
                    }

    if df_base_principal is not None and not df_base_principal.empty:
        colunas_base = [col for col in ['dat_tratada', 'REGIONAL', 'CANAL_PLAN', 'DSC_INDICADOR', 'COD_PLATAFORMA', 'TEND_QTD'] if col in df_base_principal.columns]
        if {'dat_tratada', 'CANAL_PLAN', 'DSC_INDICADOR', 'COD_PLATAFORMA', 'TEND_QTD'}.issubset(set(colunas_base)):
            mask_lig_meta = (
                df_base_principal['CANAL_PLAN'].astype(str).str.strip().eq('Televendas Receptivo') &
                df_base_principal['DSC_INDICADOR'].astype(str).str.strip().eq('LIGACOES')
            )
            df_lig_meta = _filtrar_regionais(df_base_principal.loc[mask_lig_meta, colunas_base])
            if df_lig_meta is not None and not df_lig_meta.empty:
                df_lig_meta = df_lig_meta.copy()
                df_lig_meta['dat_tratada'] = df_lig_meta['dat_tratada'].astype(str).str.strip().str.lower()
                df_lig_meta['COD_PLATAFORMA'] = df_lig_meta['COD_PLATAFORMA'].apply(normalizar_rotulo_produto)
                if produto_filtro in {'CONTA', 'FIXA'}:
                    df_lig_meta = df_lig_meta.loc[df_lig_meta['COD_PLATAFORMA'].eq(produto_filtro)]
                else:
                    df_lig_meta = df_lig_meta.loc[df_lig_meta['COD_PLATAFORMA'].isin(['CONTA', 'FIXA'])]
                if not df_lig_meta.empty:
                    agg_lig_meta = (
                        df_lig_meta.groupby('dat_tratada', as_index=False, observed=True)[['TEND_QTD']]
                        .sum()
                    )
                    resultado['ligacoes_tend'] = {
                        str(row['dat_tratada']).strip().lower(): float(row['TEND_QTD'] or 0.0)
                        for _, row in agg_lig_meta.iterrows()
                    }

    return resultado

def get_mes_ano_anterior(mes_atual: str) -> str:
    """Retorna o mesmo mes do ano anterior no formato 'mmm/aa'."""
    try:
        data_ref = pd.Timestamp(mes_ano_para_data(mes_atual)).normalize()
        data_yoy = data_ref - pd.DateOffset(years=1)
        meses_pt = {
            1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
            7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
        }
        return f"{meses_pt.get(int(data_yoy.month), 'jan')}/{str(int(data_yoy.year))[-2:]}"
    except Exception:
        return mes_atual

def render_filter_label(texto: str):
    """Renderiza rótulo padrão para filtros com label colapsado."""
    st.markdown(f'<div class="filter-label-standard">{texto}</div>', unsafe_allow_html=True)

def limpar_texto_visual(valor, default: str = "") -> str:
    """Remove valores indefinidos/legados de títulos e subtítulos visuais."""
    if valor is None:
        return default
    try:
        if pd.isna(valor):
            return default
    except Exception:
        pass

    texto = re.sub(r"\s+", " ", str(valor)).strip()
    if not texto:
        return default

    texto_sem_html = re.sub(r"<[^>]+>", " ", texto)
    chave = normalizar_chave_visual(texto_sem_html)
    if chave in {"", "none", "nan", "undefined", "null", "n a", "na", "sem titulo", "sem subtitulo"}:
        return default
    return texto

def montar_titulo_plotly_html(titulo, subtitulo=None) -> str:
    """Monta título Plotly com subtítulo apenas quando houver texto válido."""
    titulo_txt = limpar_texto_visual(titulo)
    subtitulo_txt = limpar_texto_visual(subtitulo)

    if titulo_txt and subtitulo_txt:
        return (
            f"<b>{escape(titulo_txt)}</b><br>"
            f"<span style='font-size:12px;color:#7A8495;'>{escape(subtitulo_txt)}</span>"
        )
    if titulo_txt:
        return f"<b>{escape(titulo_txt)}</b>"
    if subtitulo_txt:
        return f"<b>{escape(subtitulo_txt)}</b>"
    return ""

def build_visual_title_html(
    title: str,
    icon_hint: str | None = None,
    title_class: str = "section-title",
    subtitle: str | None = None,
    extra_style: str | None = None
) -> str:
    """Monta títulos visuais no mesmo HTML estrutural usado pelo app10."""
    title_limpo = limpar_texto_visual(title, default=limpar_texto_visual(icon_hint, default="VISÃO"))
    subtitle_limpo = limpar_texto_visual(subtitle)
    title_txt = escape(title_limpo)
    icon_svg = get_kpi_icon_svg(icon_hint or title_limpo)
    style_attr = f' style="{escape(str(extra_style), quote=True)}"' if extra_style else ""
    subtitle_html = f'<div class="title-subtitle">{escape(subtitle_limpo)}</div>' if subtitle_limpo else ""
    subtitle_class = " has-subtitle" if subtitle_limpo else ""
    return (
        f'<div class="{title_class}{subtitle_class}"{style_attr}>'
        f'<span class="section-icon" aria-hidden="true">{icon_svg}</span>'
        f'<div class="title-copy">'
        f'<div class="title-main">{title_txt}</div>'
        f'{subtitle_html}'
        f'</div>'
        '</div>'
    )

def build_kpi_title_html(title: str, icon_hint: str | None = None) -> str:
    """Monta o título dos cards KPI com ícone inline e rótulo acessível."""
    title_txt = escape(str(title))
    icon_svg = get_kpi_icon_svg(icon_hint or title)
    title_key = normalizar_chave_visual(title)
    prioridade = " is-primary" if "total" in title_key else ""
    return (
        f'<div class="kpi-title-dinamico{prioridade}" aria-label="{title_txt}">'
        f'<span class="kpi-title-icon" aria-hidden="true">{icon_svg}</span>'
        f'<span class="kpi-title-text">{title_txt}</span>'
        '</div>'
    )

def build_kpi_block_label_html(label: str, icon_hint: str | None = None) -> str:
    """Cria label compacta do bloco interno do card com um pequeno ícone."""
    label_txt = escape(str(label))
    icon_svg = get_kpi_icon_svg(icon_hint or label)
    return (
        f'<div class="kpi-block-label" aria-label="{label_txt}">'
        f'<span class="kpi-block-icon" aria-hidden="true">{icon_svg}</span>'
        f'<span>{label_txt}</span>'
        '</div>'
    )

def build_tendencia_icon_html(usa_tendencia: bool) -> str:
    """Retorna ícone de tendência no valor KPI quando aplicável."""
    if not usa_tendencia:
        return ""
    icon_svg = get_kpi_icon_svg("trend")
    return (
        '<span class="kpi-tooltip kpi-tooltip-inline" '
        'style="color:#FFFFFF !important;'
        '-webkit-text-fill-color:#FFFFFF !important;border:1px solid rgba(255,255,255,0.9);" '
        'title="Tendência = projeção de fechamento do mês com base no ritmo atual." '
        'aria-label="Tendência aplicada ao valor">'
        f'{icon_svg}'
        '<span>Tend.</span>'
        '</span>'
    )

def build_kpi_meta_line(
    anterior_valor: str,
    meta_valor: str | None = None,
    mes_anterior_ref: str | None = None,
    mes_meta_ref: str | None = None,
    break_line: bool = False
) -> str:
    """Gera linha de meta dos cards com tooltip de referência de mês."""
    mes_anterior_txt = str(mes_anterior_ref).strip() if mes_anterior_ref else ""
    mes_meta_txt = str(mes_meta_ref).strip() if mes_meta_ref else ""
    anterior_txt = escape(str(anterior_valor))

    hint_anterior = (
        f"M-1: referente ao mês {mes_anterior_txt}"
        if mes_anterior_txt
        else "M-1: valor de referência"
    )
    chip_anterior = (
        f'<span class="kpi-meta-chip kpi-meta-chip-anterior" title="{escape(hint_anterior)}" '
        f'aria-label="{escape(hint_anterior)}">'
        f'<span class="kpi-meta-label">M-1</span>'
        f'<span class="kpi-meta-value">{anterior_txt}</span>'
        f'</span>'
    )

    if meta_valor is None:
        return f'<span class="kpi-meta-items">{chip_anterior}</span>'

    meta_txt = escape(str(meta_valor))
    hint_meta = (
        f"Orç: referente ao mês {mes_meta_txt}"
        if mes_meta_txt
        else "Orç: valor de meta do mês selecionado"
    )
    chip_meta = (
        f'<span class="kpi-meta-chip kpi-meta-chip-orc" title="{escape(hint_meta)}" '
        f'aria-label="{escape(hint_meta)}">'
        f'<span class="kpi-meta-label">Orç</span>'
        f'<span class="kpi-meta-value">{meta_txt}</span>'
        f'</span>'
    )
    classes = "kpi-meta-items kpi-meta-items-break" if break_line else "kpi-meta-items"
    return f'<span class="{classes}">{chip_anterior}{chip_meta}</span>'

def build_painel_contexto_evolucao_mensal(
    resumo_label: str,
    resumo_valor: str,
    resumo_aux: str | None = None,
    legenda_2025: str = '2025 (Real)',
    legenda_2026: str = '2026 (Real/Tend Mês)',
    legenda_orc: str = '2026 (Orç)',
    titulo_2025: str = 'Série Histórica',
    titulo_2026: str = 'Série Principal',
    titulo_orc: str = 'Referência',
    nota_2025: str = 'Base de comparação do ano anterior.',
    nota_2026: str = 'Real até a data e tendência no mês atual.',
    nota_orc: str = 'Linha de orçamento mensal.',
    cor_2025: str = '#790E09',
    cor_2026: str = '#FF2800',
    cor_orc: str = '#5A6268',
    comparativo_m1: dict | None = None,
    comparativo_orc: dict | None = None,
    contexto_filtros: list[tuple[str, str]] | None = None
) -> str:
    """Renderiza apenas a faixa de legendas executivas para evolução mensal."""
    chips_contexto = ''
    contexto_limpo = []
    for item in list(contexto_filtros or []):
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        chave = str(item[0]).strip()
        valor = str(item[1]).strip()
        if not chave or not valor:
            continue
        contexto_limpo.append((chave, valor))

    if contexto_limpo:
        chips_contexto = (
            '<div class="evo-monthly-context">' +
            ''.join(
                f'<div class="evo-monthly-context-chip" title="{escape(chave)}: {escape(valor)}">'
                f'<span class="evo-monthly-context-label">{escape(chave)}</span>'
                f'<span class="evo-monthly-context-value">{escape(valor)}</span>'
                f'</div>'
                for chave, valor in contexto_limpo[:3]
            ) +
            '</div>'
        )

    return (
        '<div class="evo-monthly-panel">'
        f'{chips_contexto}'
        '<div class="evo-monthly-legends">'
        '<div class="evo-monthly-legend-card">'
        '<div class="evo-monthly-legend-head">'
        f'<div class="evo-monthly-legend-dot" style="background:{escape(str(cor_2025), quote=True)};"></div>'
        f'<div class="evo-monthly-legend-title">{escape(str(titulo_2025))}</div>'
        '</div>'
        '<div class="evo-monthly-legend-body">'
        f'<div class="evo-monthly-legend-text">{escape(str(legenda_2025))}</div>'
        '<div class="evo-monthly-legend-divider">•</div>'
        f'<div class="evo-monthly-legend-note">{escape(str(nota_2025))}</div>'
        '</div>'
        '</div>'
        '<div class="evo-monthly-legend-card">'
        '<div class="evo-monthly-legend-head">'
        f'<div class="evo-monthly-legend-dot" style="background:{escape(str(cor_2026), quote=True)};"></div>'
        f'<div class="evo-monthly-legend-title">{escape(str(titulo_2026))}</div>'
        '</div>'
        '<div class="evo-monthly-legend-body">'
        f'<div class="evo-monthly-legend-text">{escape(str(legenda_2026))}</div>'
        '<div class="evo-monthly-legend-divider">•</div>'
        f'<div class="evo-monthly-legend-note">{escape(str(nota_2026))}</div>'
        '</div>'
        '</div>'
        '<div class="evo-monthly-legend-card">'
        '<div class="evo-monthly-legend-head">'
        f'<div class="evo-monthly-legend-dot is-orc" style="background:{escape(str(cor_orc), quote=True)};"></div>'
        f'<div class="evo-monthly-legend-title">{escape(str(titulo_orc))}</div>'
        '</div>'
        '<div class="evo-monthly-legend-body">'
        f'<div class="evo-monthly-legend-text">{escape(str(legenda_orc))}</div>'
        '<div class="evo-monthly-legend-divider">•</div>'
        f'<div class="evo-monthly-legend-note">{escape(str(nota_orc))}</div>'
        '</div>'
        '</div>'
        '</div>'
        '</div>'
    )

def montar_comparativo_resumo(
    valor_atual: float,
    valor_referencia: float,
    titulo: str,
    nota: str
) -> dict[str, str]:
    """Monta dicionário de comparação executiva para o painel de contexto."""
    valor_atual_num = float(valor_atual or 0.0)
    valor_ref_num = float(valor_referencia or 0.0)
    delta = valor_atual_num - valor_ref_num
    if delta > 0:
        seta = '↑'
        status = 'Crescimento'
        classe = 'is-up'
    elif delta < 0:
        seta = '↓'
        status = 'Queda'
        classe = 'is-down'
    else:
        seta = '→'
        status = 'Estável'
        classe = 'is-flat'

    if valor_ref_num > 0:
        pct = (delta / valor_ref_num) * 100.0
        pct_txt = f"{pct:+.1f}%".replace('.', ',')
    else:
        pct_txt = 'Base sem referência'

    delta_txt = f"{delta:+,.0f}".replace(',', '.')
    return {
        'titulo': titulo,
        'status': status,
        'seta': seta,
        'classe': classe,
        'delta': delta_txt,
        'percentual': pct_txt,
        'nota': nota
    }

def obter_resumo_comparativo_evolucao(
    df_linhas_base: pd.DataFrame,
    serie_real_tend: str = '2026 Real/Tend',
    serie_orc: str = '2026',
    serie_ano_anterior: str = '2025',
    mes_ref_num: int | None = None,
    rotulo_mes_ref: str | None = None
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Extrai comparativos do mês de referência para os painéis de evolução."""
    base = df_linhas_base.copy() if df_linhas_base is not None else pd.DataFrame()
    if base.empty:
        return (
            str(rotulo_mes_ref or 'N/D'),
            montar_comparativo_resumo(0.0, 0.0, 'vs M-1', 'Sem base disponível para comparação mensal.'),
            montar_comparativo_resumo(0.0, 0.0, 'vs Orçamento', 'Sem base disponível para comparação com orçamento.')
        )

    base['Ano'] = base.get('Ano', '').astype(str)
    base['Mês_Num'] = pd.to_numeric(base.get('Mês_Num', 0), errors='coerce').fillna(0).astype(int)
    base['Valor'] = pd.to_numeric(base.get('Valor', 0), errors='coerce').fillna(0.0)

    if mes_ref_num is None:
        mes_ref_num = int(pd.Timestamp.today().month)
    mes_ref_num = int(mes_ref_num)
    mapa_meses = {
        1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
        7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
    }
    rotulo_mes = str(rotulo_mes_ref or mapa_meses.get(mes_ref_num, 'jan')).upper()

    def _valor_serie(nome_serie: str, mes_num: int) -> float:
        valores = base.loc[
            base['Ano'].eq(str(nome_serie)) & base['Mês_Num'].eq(int(mes_num)),
            'Valor'
        ]
        return float(valores.iloc[0] or 0.0) if not valores.empty else 0.0

    valor_atual = _valor_serie(serie_real_tend, mes_ref_num)
    if mes_ref_num == 1:
        valor_m1 = _valor_serie(serie_ano_anterior, 12)
    else:
        valor_m1 = _valor_serie(serie_real_tend, mes_ref_num - 1)
    valor_orc = _valor_serie(serie_orc, mes_ref_num)

    comparativo_m1 = montar_comparativo_resumo(
        valor_atual,
        valor_m1,
        'vs M-1',
        f"{rotulo_mes} x {mapa_meses.get(12 if mes_ref_num == 1 else mes_ref_num - 1, 'jan').upper()}"
    )
    comparativo_orc = montar_comparativo_resumo(
        valor_atual,
        valor_orc,
        'vs Orçamento',
        f"{rotulo_mes} x Orçamento"
    )
    return rotulo_mes, comparativo_m1, comparativo_orc

def get_data_realizado_max_formatada(df_base: pd.DataFrame) -> str:
    """Retorna a data máxima de realizado no formato dd/mm/aaaa."""
    if df_base is None or df_base.empty:
        return "N/D"

    base_ref = df_base
    if 'QTDE' in df_base.columns:
        qtde_num = pd.to_numeric(df_base['QTDE'], errors='coerce').fillna(0)
        base_ref = df_base.loc[qtde_num > 0]
        if base_ref.empty:
            return "N/D"

    colunas_data = ['DAT_MOVIMENTO', 'DAT_MOVIMENTO2', 'PERIODO', 'DAT_MOVIMENTO_2']
    for col in colunas_data:
        if col in base_ref.columns:
            serie_data = pd.to_datetime(base_ref[col], errors='coerce')
            data_max = serie_data.max()
            if pd.notna(data_max):
                return data_max.strftime('%d/%m/%Y')
    return "N/D"


def construir_linhas_evolucao_mensal_preagregada(
    df_grafico: pd.DataFrame,
    usar_tendencia: bool = True
) -> pd.DataFrame:
    """Converte base mensal preagregada em séries prontas para o gráfico comparativo anual."""
    meses_abreviados = {
        1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
        7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
    }
    colunas_saida = ['Ano', 'Mês', 'Mês_Num', 'Valor', 'Tipo', 'Mês_Ord', 'Valor_Formatado']
    if df_grafico is None or df_grafico.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_work = df_grafico.copy()
    df_work['Ano'] = pd.to_numeric(df_work.get('Ano', 0), errors='coerce').fillna(0).astype(int)
    df_work['Mês_Num'] = pd.to_numeric(df_work.get('Mês_Num', 0), errors='coerce').fillna(0).astype(int)
    df_work['Valor'] = pd.to_numeric(df_work.get('Valor', 0), errors='coerce').fillna(0.0)
    df_work['Tipo'] = df_work.get('Tipo', '').astype(str).str.strip().str.title()
    df_work = df_work[df_work['Mês_Num'].between(1, 12)].copy()

    if df_work.empty:
        return pd.DataFrame(columns=colunas_saida)

    serie_valores = (
        df_work.groupby(['Ano', 'Mês_Num', 'Tipo'], observed=True, dropna=False)['Valor']
        .sum()
        .to_dict()
    )
    mes_corrente = int(pd.Timestamp.today().month)
    ano_corrente = int(pd.Timestamp.today().year)
    dados_grafico: list[dict[str, object]] = []

    for mes_num in range(1, 13):
        dados_grafico.append({
            'Ano': '2025',
            'Mês': meses_abreviados[mes_num],
            'Mês_Num': mes_num,
            'Valor': float(serie_valores.get((2025, mes_num, 'Real'), 0.0)),
            'Tipo': 'Real'
        })

    for mes_num in range(1, 13):
        valor_real = float(serie_valores.get((2026, mes_num, 'Real'), 0.0))
        valor_tend = float(serie_valores.get((2026, mes_num, 'Tend'), 0.0))
        usar_tend_mes = usar_tendencia and (ano_corrente == 2026) and (mes_num == mes_corrente) and (valor_tend > 0)
        dados_grafico.append({
            'Ano': '2026 Real/Tend',
            'Mês': meses_abreviados[mes_num],
            'Mês_Num': mes_num,
            'Valor': valor_tend if usar_tend_mes else valor_real,
            'Tipo': 'Real/Tend'
        })

    for mes_num in range(1, 13):
        dados_grafico.append({
            'Ano': '2026',
            'Mês': meses_abreviados[mes_num],
            'Mês_Num': mes_num,
            'Valor': float(serie_valores.get((2026, mes_num, 'Orç'), 0.0)),
            'Tipo': 'Orç'
        })

    df_linhas = pd.DataFrame(dados_grafico)
    ordem_anos = ['2025', '2026 Real/Tend', '2026']
    df_linhas['Ano'] = pd.Categorical(df_linhas['Ano'], categories=ordem_anos, ordered=True)
    df_linhas['Mês_Ord'] = df_linhas['Mês_Num']
    df_linhas = df_linhas.sort_values(['Ano', 'Mês_Ord'])
    df_linhas['Valor_Formatado'] = df_linhas['Valor'].apply(lambda x: formatar_numero_brasileiro(x, 0))
    return df_linhas


def create_line_chart_data(df_grafico):
    """Cria dados para gráfico de linhas temporal"""
    colunas_preagregadas = {'Ano', 'Mês', 'Mês_Num', 'Valor', 'Tipo'}
    if colunas_preagregadas.issubset(set(df_grafico.columns)):
        return construir_linhas_evolucao_mensal_preagregada(df_grafico, usar_tendencia=True)

    if 'ANO' not in df_grafico.columns or 'DAT_MÊS' not in df_grafico.columns:
        if not pd.api.types.is_datetime64_any_dtype(df_grafico['DAT_MOVIMENTO2']):
            df_grafico['DAT_MOVIMENTO2'] = pd.to_datetime(df_grafico['DAT_MOVIMENTO2'], errors='coerce')
        df_grafico['ANO'] = df_grafico['DAT_MOVIMENTO2'].dt.year
        df_grafico['DAT_MÊS'] = df_grafico['DAT_MOVIMENTO2'].dt.month
    
    meses_abreviados = {
        1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
        7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
    }
    
    mes_corrente = int(pd.Timestamp.today().month)
    ano_corrente = int(pd.Timestamp.today().year)
    dados_grafico = []

    df_2025 = df_grafico[df_grafico['ANO'] == 2025]
    for mes_num in range(1, 13):
        df_mes = df_2025[df_2025['DAT_MÊS'] == mes_num]
        valor = float(pd.to_numeric(df_mes.get('QTDE', 0), errors='coerce').fillna(0).sum())
        dados_grafico.append({
            'Ano': '2025',
            'Mês': meses_abreviados[mes_num],
            'Mês_Num': mes_num,
            'Valor': valor,
            'Tipo': 'Real'
        })

    df_2026 = df_grafico[df_grafico['ANO'] == 2026]
    for mes_num in range(1, 13):
        df_mes = df_2026[df_2026['DAT_MÊS'] == mes_num]
        valor_real = float(pd.to_numeric(df_mes.get('QTDE', 0), errors='coerce').fillna(0).sum())
        valor_tend = float(pd.to_numeric(df_mes.get('TEND_QTD', 0), errors='coerce').fillna(0).sum())
        usar_tend_mes = (ano_corrente == 2026) and (mes_num == mes_corrente) and (valor_tend > 0)
        dados_grafico.append({
            'Ano': '2026 Real/Tend',
            'Mês': meses_abreviados[mes_num],
            'Mês_Num': mes_num,
            'Valor': valor_tend if usar_tend_mes else valor_real,
            'Tipo': 'Real/Tend'
        })

    for mes_num in range(1, 13):
        df_mes = df_2026[df_2026['DAT_MÊS'] == mes_num]
        valor_orc = float(pd.to_numeric(df_mes.get('DESAFIO_QTD', 0), errors='coerce').fillna(0).sum())
        dados_grafico.append({
            'Ano': '2026',
            'Mês': meses_abreviados[mes_num],
            'Mês_Num': mes_num,
            'Valor': valor_orc,
            'Tipo': 'Orç'
        })
    
    df_linhas = pd.DataFrame(dados_grafico)
    ordem_anos = ['2025', '2026 Real/Tend', '2026']
    df_linhas['Ano'] = pd.Categorical(df_linhas['Ano'], categories=ordem_anos, ordered=True)
    df_linhas['Mês_Ord'] = df_linhas['Mês_Num']
    df_linhas = df_linhas.sort_values(['Ano', 'Mês_Ord'])
    df_linhas['Valor_Formatado'] = df_linhas['Valor'].apply(lambda x: formatar_numero_brasileiro(x, 0))
    
    return df_linhas

def aplicar_regra_sem_zeros_e_fallback_orc(
    df_linhas: pd.DataFrame,
    serie_real_tend: str = '2026 Real/Tend',
    serie_orc: str = '2026',
    mes_ref_num: int | None = None
) -> pd.DataFrame:
    """
    Ajusta séries de linha para:
    1) manter Real/Tend na cor padrão (vermelho);
    2) respeitar o mês de corte selecionado no dashboard;
    3) quando o mês de corte for anterior ao mês atual, usar o orçamento do mês seguinte
       como ponto-ponte da série principal para conectar visualmente as linhas;
    4) ocultar meses posteriores ao corte/ponto-ponte na série Real/Tend;
    5) ocultar meses zerados no gráfico (Valor = NaN).
    """
    if df_linhas is None or df_linhas.empty:
        return df_linhas

    df_out = df_linhas.copy()
    df_out['Valor'] = pd.to_numeric(df_out.get('Valor', 0), errors='coerce')
    df_out['Mês'] = df_out['Mês'].astype(str)
    df_out['Ano'] = df_out['Ano'].astype(str)

    mapa_orc = (
        df_out[df_out['Ano'].eq(str(serie_orc))]
        .groupby('Mês', observed=True)['Valor']
        .sum()
        .to_dict()
    )

    mask_real_tend = df_out['Ano'].eq(str(serie_real_tend))
    meses_pt_ordem = {
        1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
        7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
    }
    meses_ordem_rev = {v: k for k, v in meses_pt_ordem.items()}
    if mes_ref_num is None:
        mes_ref_num = int(pd.Timestamp.today().month)
    mes_ref_num = int(mes_ref_num)

    mes_atual_num = int(pd.Timestamp.today().month)
    usar_ponto_ponte = mes_ref_num <= mes_atual_num
    mes_ponte_num = mes_ref_num + 1 if usar_ponto_ponte and mes_ref_num < 12 else None

    for idx, row in df_out.loc[mask_real_tend, ['Mês', 'Valor']].iterrows():
        mes_ref = str(row['Mês']).strip().lower()
        mes_item_num = int(meses_ordem_rev.get(mes_ref, 0))
        if mes_ponte_num is not None and mes_item_num == mes_ponte_num:
            valor_orc_mes = float(mapa_orc.get(mes_ref, 0.0) or 0.0)
            df_out.at[idx, 'Valor'] = valor_orc_mes if valor_orc_mes > 0 else np.nan
        elif mes_item_num > mes_ref_num:
            df_out.at[idx, 'Valor'] = np.nan
            if mes_ponte_num is not None and mes_item_num <= mes_ponte_num:
                continue

    if mes_ponte_num is not None:
        mask_pos_ponte = mask_real_tend & df_out['Mês'].astype(str).str.lower().map(meses_ordem_rev).fillna(0).astype(int).gt(mes_ponte_num)
        df_out.loc[mask_pos_ponte, 'Valor'] = np.nan

    mask_zero = pd.to_numeric(df_out['Valor'], errors='coerce').fillna(0).le(0)
    df_out.loc[mask_zero, 'Valor'] = np.nan

    if 'Valor_Formatado' in df_out.columns:
        df_out['Valor_Formatado'] = df_out['Valor'].apply(
            lambda x: formatar_numero_brasileiro(x, 0) if pd.notna(x) else ''
        )

    ordem_base = ['2025', str(serie_real_tend), str(serie_orc)]
    anos_presentes = [str(a) for a in df_out['Ano'].dropna().astype(str).unique().tolist()]
    ordem_final = [a for a in ordem_base if a in anos_presentes] + [a for a in anos_presentes if a not in ordem_base]
    df_out['Ano'] = pd.Categorical(df_out['Ano'].astype(str), categories=ordem_final, ordered=True)
    if 'Mês_Ord' in df_out.columns:
        df_out = df_out.sort_values(['Ano', 'Mês_Ord'])
    return df_out

def ocultar_rotulo_orc_sobreposto(
    fig: go.Figure,
    series_real_tend: tuple[str, ...] = ('2026 Real/Tend',),
    serie_orc: str = '2026'
) -> None:
    """Oculta rótulos da série Orç quando coincidem com Real/Tend no mesmo mês."""
    if fig is None or not hasattr(fig, 'data'):
        return

    nomes_real_tend = set(series_real_tend or ())
    traces_real = []
    trace_orc = None
    for trace in fig.data:
        nome = str(getattr(trace, 'name', ''))
        if nome in nomes_real_tend:
            traces_real.append(trace)
        elif nome == str(serie_orc):
            trace_orc = trace

    if not traces_real or trace_orc is None:
        return

    mapa_real = {}
    for trace_real in traces_real:
        for x_val, y_val in zip(list(trace_real.x), list(trace_real.y)):
            if pd.notna(y_val):
                mapa_real[str(x_val)] = float(y_val)

    textos_orc = list(trace_orc.text) if getattr(trace_orc, 'text', None) is not None else [''] * len(list(trace_orc.y))
    novos_textos = []
    for i, (x_val, y_val) in enumerate(zip(list(trace_orc.x), list(trace_orc.y))):
        texto_atual = textos_orc[i] if i < len(textos_orc) else ''
        y_real = mapa_real.get(str(x_val))
        if pd.notna(y_val) and (y_real is not None) and np.isclose(float(y_val), float(y_real), atol=1e-9):
            novos_textos.append('')
        else:
            novos_textos.append(texto_atual)

    trace_orc.update(text=novos_textos)

MESES_TICKVALS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
ALTURA_EVOLUCAO_MENSAL_PADRAO = 384
ALTURA_EVOLUCAO_MENSAL_LIGACOES = 448
ALTURA_EVOLUCAO_MENSAL_DESATIVADOS = 345
ALTURA_SERIE_MENSAL_COMPARATIVA = 288

def apply_standard_line_layout(fig, y_axis_title: str, height: int = 520):
    """Aplica padrão visual moderno e consistente para gráficos de linha."""
    fig.update_layout(
        plot_bgcolor='#FFFFFF',
        paper_bgcolor='#FFFFFF',
        font=dict(family='Manrope, Segoe UI, Arial, sans-serif', size=13, color='#2F3747'),
        margin=dict(l=30, r=18, t=18, b=26),
        xaxis=dict(
            title='',
            tickmode='array',
            tickvals=MESES_TICKVALS,
            tickfont=dict(size=12, color='#5B6578'),
            showgrid=False,
            gridcolor='rgba(230, 236, 244, 0.85)',
            gridwidth=1,
            linecolor='#E6ECF4',
            linewidth=1.5,
            mirror=True,
            tickangle=0,
            showline=True,
            zeroline=False
        ),
        yaxis=dict(
            title='',
            title_font=dict(size=13, color='#2F3747'),
            tickfont=dict(size=12, color='#5B6578'),
            showgrid=False,
            gridcolor='rgba(230, 236, 244, 0.85)',
            gridwidth=1,
            linecolor='#E6ECF4',
            linewidth=1.5,
            mirror=True,
            showline=True,
            zeroline=False,
            rangemode='tozero'
        ),
        legend=dict(
            title=dict(text='<b>ANO</b>', font=dict(size=13, color='#2F3747')),
            orientation='h',
            yanchor='bottom',
            y=-0.13,
            xanchor='center',
            x=0.5,
            bgcolor='rgba(255, 255, 255, 0.92)',
            bordercolor='#E6ECF4',
            borderwidth=1.5,
            font=dict(size=12, color='#2F3747'),
            itemwidth=52,
            traceorder='normal'
        ),
        title=dict(
            x=0.5,
            xanchor='center',
            yanchor='top',
            font=dict(size=16, color='#2F3747'),
            y=0.95
        ),
        hovermode='x unified',
        hoverlabel=dict(
            bgcolor='white',
            font_size=13,
            font_family='Segoe UI',
            bordercolor='#E6ECF4',
            font_color='#2F3747'
        ),
        height=height,
        showlegend=False
    )

def compactar_resumo_evolucao_mensal(fig: go.Figure, altura: int) -> go.Figure:
    """Compacta o gráfico lateral de comparativo mensal sem alterar sua largura."""
    if fig is None or not hasattr(fig, 'update_layout'):
        return fig

    fig.update_layout(
        height=int(altura),
        margin=dict(l=8, r=6, t=18, b=24)
    )
    return fig

def apply_standard_line_traces(fig, color_map: dict, valor_label: str = 'Valor', meta_year: str = '2026'):
    """Aplica padrão de linha/marcador/rótulo para séries anuais."""
    for trace in fig.data:
        ano = str(trace.name)
        cor = color_map.get(ano, '#6B7280')
        is_meta = (ano == str(meta_year))
        hover_nome = 'Orç' if is_meta else valor_label
        posicoes_texto = {
            '2026 Real/Tend': 'top left',
            '2025': 'top center',
            '2026': 'top right'
        }
        texto_trace = [
            formatar_numero_brasileiro(v, 0) if pd.notna(v) and float(v) != 0 else ''
            for v in list(trace.y)
        ]
        trace.update(
            mode='lines+markers+text',
            connectgaps=False,
            marker=dict(
                size=10,
                line=dict(width=1.8, color='white'),
                symbol='diamond' if is_meta else 'circle',
                opacity=0.92
            ),
            line=dict(
                width=3.4 if is_meta else 3.2,
                dash='dash' if is_meta else 'solid',
                smoothing=1.2,
                color=cor
            ),
            hovertemplate=(
                f"<b>%{{x}}/{ano}</b><br>"
                + f"<b>{hover_nome}:</b> %{{y:,.0f}}<br>"
                + "<extra></extra>"
            ),
            text=texto_trace,
            textposition=posicoes_texto.get(ano, 'top center'),
            textfont=dict(size=10, color=cor),
            cliponaxis=False
        )

def apply_premium_plotly_theme(fig, title_size: int = 16) -> None:
    """Aplica um acabamento visual premium e consistente sem alterar a lógica do gráfico."""
    if fig is None or not hasattr(fig, "layout"):
        return

    margin_atual = fig.layout.margin if fig.layout.margin is not None else None
    margem_l = max(int(getattr(margin_atual, 'l', 0) or 0), 14)
    margem_r = max(int(getattr(margin_atual, 'r', 0) or 0), 14)
    margem_t = max(int(getattr(margin_atual, 't', 0) or 0), 26)
    margem_b = max(int(getattr(margin_atual, 'b', 0) or 0), 28)

    fonte_atual = fig.layout.font if fig.layout.font is not None else None
    familia_fonte = getattr(fonte_atual, 'family', None) or 'Manrope, Segoe UI, Arial, sans-serif'
    tamanho_fonte = int(getattr(fonte_atual, 'size', 0) or 12)
    cor_fonte = getattr(fonte_atual, 'color', None) or '#2F3747'

    legenda_atual = fig.layout.legend if fig.layout.legend is not None else None
    orientacao_legenda = getattr(legenda_atual, 'orientation', None) or 'h'
    legenda_x = getattr(legenda_atual, 'x', None)
    legenda_y = getattr(legenda_atual, 'y', None)
    legenda_xanchor = getattr(legenda_atual, 'xanchor', None)
    legenda_yanchor = getattr(legenda_atual, 'yanchor', None)

    fig.update_layout(
        paper_bgcolor='#FFFFFF',
        plot_bgcolor='#FFFFFF',
        font=dict(family=familia_fonte, size=tamanho_fonte, color=cor_fonte),
        margin=dict(l=margem_l, r=margem_r, t=margem_t, b=margem_b),
        hoverlabel=dict(
            bgcolor='rgba(255,255,255,0.98)',
            bordercolor='rgba(148, 163, 184, 0.36)',
            font_size=max(tamanho_fonte - 1, 11),
            font_family='Manrope, Segoe UI, sans-serif',
            font_color='#243041',
            align='left'
        ),
        uniformtext=dict(minsize=max(min(title_size - 5, 11), 9), mode='hide'),
        separators=',.',
        transition=dict(duration=380, easing='cubic-in-out'),
        dragmode=False,
        legend=dict(
            orientation=orientacao_legenda,
            x=legenda_x if legenda_x is not None else (0.995 if orientacao_legenda == 'h' else 1.01),
            y=legenda_y if legenda_y is not None else (1.015 if orientacao_legenda == 'h' else 0.98),
            xanchor=legenda_xanchor if legenda_xanchor is not None else ('right' if orientacao_legenda == 'h' else 'left'),
            yanchor=legenda_yanchor if legenda_yanchor is not None else ('bottom' if orientacao_legenda == 'h' else 'top'),
            bgcolor='rgba(255,255,255,0.98)',
            bordercolor='rgba(226, 232, 240, 0.96)',
            borderwidth=1,
            font=dict(size=max(tamanho_fonte - 2, 11), color='#465468', family='Manrope, Segoe UI, sans-serif'),
            tracegroupgap=6
        )
    )
    fig.update_xaxes(
        automargin=True,
        zeroline=False,
        showspikes=False,
        showgrid=False,
        ticks='outside',
        ticklen=5,
        tickfont=dict(size=max(tamanho_fonte - 1, 11), color='#5A6678', family='Manrope, Segoe UI, Arial, sans-serif'),
        title_font=dict(size=max(tamanho_fonte - 1, 12), color='#475569', family='Manrope, Segoe UI, Arial, sans-serif'),
        linecolor='rgba(148, 163, 184, 0.28)',
        linewidth=1.1
    )
    fig.update_yaxes(
        automargin=True,
        zeroline=False,
        showspikes=False,
        separatethousands=True,
        ticks='outside',
        ticklen=4,
        tickfont=dict(size=max(tamanho_fonte - 1, 11), color='#5A6678', family='Manrope, Segoe UI, Arial, sans-serif'),
        title_font=dict(size=max(tamanho_fonte - 1, 12), color='#475569', family='Manrope, Segoe UI, Arial, sans-serif'),
        gridcolor='rgba(148, 163, 184, 0.14)',
        gridwidth=1,
        linecolor='rgba(148, 163, 184, 0.20)',
        linewidth=1.0
    )

    for trace in getattr(fig, 'data', []):
        try:
            tipo = str(getattr(trace, 'type', '') or '').lower()
            modo = str(getattr(trace, 'mode', '') or '').lower()
            if tipo == 'scatter' and 'markers' in modo:
                marker_atual = getattr(trace, 'marker', None)
                tamanho_atual = getattr(marker_atual, 'size', None) if marker_atual is not None else None
                if isinstance(tamanho_atual, (int, float)):
                    tamanho_final = max(float(tamanho_atual), 9.0)
                else:
                    tamanho_final = 9.0
                trace.update(marker=dict(size=tamanho_final))
        except Exception:
            continue

    titulo_atual = fig.layout.title.text if fig.layout.title is not None else None
    titulo_atual_limpo = limpar_texto_visual(titulo_atual)
    if titulo_atual_limpo:
        x_val = fig.layout.title.x if fig.layout.title.x is not None else 0.02
        y_val = fig.layout.title.y if fig.layout.title.y is not None else 0.96
        try:
            y_val = min(float(y_val), 0.96)
        except Exception:
            y_val = 0.96
        x_anchor = fig.layout.title.xanchor if fig.layout.title.xanchor is not None else 'left'
        y_anchor = fig.layout.title.yanchor if fig.layout.title.yanchor is not None else 'top'
        fig.update_layout(
            title=dict(
                text=titulo_atual,
                x=x_val,
                y=min(float(y_val), 0.97),
                xanchor=x_anchor,
                yanchor=y_anchor,
                font=dict(size=title_size, color='#2F3747', family='Manrope, Segoe UI, Arial, sans-serif')
            )
        )
    else:
        fig.update_layout(title=dict(text=""))


def apply_standard_title_style(fig, size: int = 16):
    """Padroniza visual do título dos gráficos sem alterar posicionamento já definido."""
    apply_premium_plotly_theme(fig, title_size=size)

def aplicar_estilo_visual_evolucao_mensal(fig: go.Figure, altura: int = 400, mes_referencia: str | None = None) -> None:
    """Refina apenas o visual dos gráficos mensais de linha fora da aba Analítico."""
    if fig is None or not hasattr(fig, 'data'):
        return

    meses_pt = {
        1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
        7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
    }
    mes_ref_num, rotulo_mes_foco = obter_mes_referencia_grafico(mes_referencia)
    ticktext_meses = [m.upper() if m == meses_pt.get(int(mes_ref_num), '') else m for m in MESES_TICKVALS]
    valores_validos: list[float] = []
    for trace in getattr(fig, 'data', []):
        try:
            ys = list(getattr(trace, 'y', []) or [])
        except Exception:
            ys = []
        for valor in ys:
            try:
                valor_num = float(valor)
            except Exception:
                continue
            if np.isfinite(valor_num) and valor_num > 0:
                valores_validos.append(valor_num)

    yaxis_kwargs: dict[str, object] = dict(rangemode='tozero')
    if valores_validos:
        max_val = float(max(valores_validos))
        min_val = float(min(valores_validos))
        if max_val <= 3000:
            dtick = 500.0
        elif max_val <= 10000:
            dtick = 1000.0
        elif max_val <= 25000:
            dtick = 2500.0
        else:
            dtick = 5000.0

        amplitude = max(max_val - min_val, dtick)
        folga_inferior = max(dtick * 0.18, amplitude * 0.08)
        folga_superior = max(dtick * 0.18, amplitude * 0.06)

        if min_val > dtick * 0.35:
            y_min = max(0.0, float(np.floor(max(min_val - folga_inferior, 0.0) / dtick) * dtick))
        else:
            y_min = 0.0
        y_max = float(np.ceil((max_val + folga_superior) / dtick) * dtick)
        if y_max <= y_min:
            y_max = y_min + dtick

        yaxis_kwargs.update(
            dtick=dtick,
            tick0=y_min,
            range=[y_min, y_max],
            rangemode='normal'
        )

    fig.update_layout(
        plot_bgcolor='#FFFFFF',
        paper_bgcolor='#FFFFFF',
        font=dict(family='Manrope, Segoe UI, Arial, sans-serif', size=13, color='#2F3747'),
        margin=dict(l=26, r=10, t=14, b=20),
        xaxis=dict(
            title='',
            tickmode='array',
            tickvals=MESES_TICKVALS,
            ticktext=ticktext_meses,
            tickfont=dict(size=12, color='#5B6578'),
            showgrid=False,
            linecolor='#E2E8F0',
            linewidth=1.4,
            mirror=False,
            tickangle=0,
            showline=True,
            ticks='outside',
            ticklen=6,
            zeroline=False
        ),
        yaxis=dict(
            title='',
            title_font=dict(size=13, color='#2F3747'),
            tickfont=dict(size=12, color='#5B6578'),
            showgrid=True,
            gridcolor='rgba(226, 232, 240, 0.78)',
            gridwidth=1,
            linecolor='#E2E8F0',
            linewidth=1.4,
            mirror=False,
            showline=True,
            ticks='outside',
            ticklen=5,
            zeroline=False,
            **yaxis_kwargs
        ),
        hovermode='x unified',
        hoverlabel=dict(
            bgcolor='white',
            font_size=12,
            font_family='Segoe UI',
            bordercolor='#E2E8F0',
            font_color='#2F3747'
        ),
        height=altura,
        showlegend=False
    )

    fig.add_shape(
        type='line',
        x0=meses_pt.get(int(mes_ref_num), ''),
        x1=meses_pt.get(int(mes_ref_num), ''),
        y0=0,
        y1=1,
        xref='x',
        yref='paper',
        line=dict(color='rgba(162, 59, 54, 0.18)', width=1.6, dash='dot'),
        layer='below'
    )

    glow_traces: list[go.Scatter] = []
    for trace in fig.data:
        nome = str(getattr(trace, 'name', ''))
        if nome == '2026 Real/Tend':
            trace.update(
                line=dict(width=4.0, color='#FF2800', shape='spline', smoothing=1.25),
                marker=dict(size=9, color='#FF2800', line=dict(width=1.8, color='white'), symbol='circle'),
                textfont=dict(size=11, color='#FF2800'),
                fill=None
            )
            glow_traces.append(
                go.Scatter(
                    x=list(getattr(trace, 'x', [])),
                    y=list(getattr(trace, 'y', [])),
                    mode='lines',
                    line=dict(width=8.8, color='rgba(255, 40, 0, 0.08)', shape='spline', smoothing=1.25),
                    hoverinfo='skip',
                    showlegend=False,
                    cliponaxis=False
                )
            )
        elif nome == '2025':
            trace.update(
                line=dict(width=3.0, color='#790E09', shape='spline', smoothing=1.15),
                marker=dict(size=8, color='#790E09', line=dict(width=1.7, color='white'), symbol='circle-open'),
                textfont=dict(size=10, color='#790E09')
            )
        elif nome == '2026':
            trace.update(
                line=dict(width=3.2, color='#5A6268', dash='dash', shape='spline', smoothing=1.1),
                marker=dict(size=8, color='#5A6268', line=dict(width=1.7, color='white'), symbol='diamond-open'),
                textfont=dict(size=10, color='#5A6268')
            )

    if glow_traces:
        qtd_glow = len(glow_traces)
        fig.add_traces(glow_traces)
        traces_atuais = list(fig.data)
        fig.data = tuple(traces_atuais[-qtd_glow:] + traces_atuais[:-qtd_glow])

    apply_premium_plotly_theme(fig, title_size=16)

def aplicar_estilo_visual_evolucao_semanal(
    fig: go.Figure,
    rotulo_mes_foco: str,
    nome_serie_real: str,
    altura: int = 460
) -> None:
    """Aplica ao grafico semanal o mesmo acabamento visual dos graficos mensais refinados."""
    if fig is None or not hasattr(fig, 'data'):
        return

    fig.update_layout(
        plot_bgcolor='#FFFFFF',
        paper_bgcolor='#FFFFFF',
        font=dict(family='Segoe UI', size=14, color='#2F3747'),
        margin=dict(l=38, r=24, t=56, b=54),
        xaxis=dict(
            title='',
            type='multicategory',
            tickfont=dict(size=11, color='#5B6578'),
            showgrid=False,
            linecolor='#E2E8F0',
            linewidth=1.4,
            mirror=False,
            showline=True,
            ticks='outside',
            ticklen=6,
            zeroline=False
        ),
        yaxis=dict(
            title='',
            tickfont=dict(size=12, color='#5B6578'),
            showgrid=True,
            gridcolor='rgba(226, 232, 240, 0.78)',
            gridwidth=1,
            linecolor='#E2E8F0',
            linewidth=1.4,
            mirror=False,
            showline=True,
            ticks='outside',
            ticklen=5,
            zeroline=False,
            rangemode='tozero'
        ),
        hovermode='x unified',
        hoverlabel=dict(
            bgcolor='white',
            font_size=12,
            font_family='Segoe UI',
            bordercolor='#E2E8F0',
            font_color='#2F3747'
        ),
        legend=dict(
            orientation='h',
            yanchor='bottom',
            y=1.015,
            xanchor='right',
            x=0.995,
            bgcolor='rgba(255,255,255,0.92)',
            bordercolor='#E2E8F0',
            borderwidth=1.2,
            font=dict(size=10, color='#2F3747'),
            title=dict(text='')
        ),
        height=altura,
        showlegend=True
    )

    glow_traces: list[go.Scatter] = []
    for trace in fig.data:
        nome = str(getattr(trace, 'name', ''))
        if nome == str(nome_serie_real):
            trace.update(
                line=dict(width=4.0, color='#FF2800', shape='spline', smoothing=1.25),
                marker=dict(size=9, color='#FF2800', line=dict(width=1.8, color='white'), symbol='circle'),
                textfont=dict(size=11, color='#FF2800'),
                fill='tozeroy',
                fillcolor='rgba(255, 40, 0, 0.07)'
            )
            glow_traces.append(
                go.Scatter(
                    x=list(getattr(trace, 'x', [])),
                    y=list(getattr(trace, 'y', [])),
                    mode='lines',
                    line=dict(width=10.5, color='rgba(255, 40, 0, 0.10)', shape='spline', smoothing=1.25),
                    hoverinfo='skip',
                    showlegend=False,
                    cliponaxis=False
                )
            )
        elif nome == 'M-1':
            trace.update(
                line=dict(width=3.0, color='#790E09', shape='spline', smoothing=1.15),
                marker=dict(size=8, color='#790E09', line=dict(width=1.7, color='white'), symbol='circle-open'),
                textfont=dict(size=10, color='#790E09')
            )
        elif nome == 'Orçamento':
            trace.update(
                line=dict(width=3.2, color='#5A6268', dash='dash', shape='spline', smoothing=1.1),
                marker=dict(size=8, color='#5A6268', line=dict(width=1.7, color='white'), symbol='diamond-open'),
                textfont=dict(size=10, color='#5A6268')
            )

    if glow_traces:
        qtd_glow = len(glow_traces)
        fig.add_traces(glow_traces)
        traces_atuais = list(fig.data)
        fig.data = tuple(traces_atuais[-qtd_glow:] + traces_atuais[:-qtd_glow])

def _hex_to_rgba_resumo_barras(cor_hex: str, alpha: float) -> str:
    """Converte cores hex em rgba para os efeitos visuais dos resumos laterais."""
    cor_limpa = str(cor_hex).strip().lstrip('#')
    if len(cor_limpa) != 6:
        return f'rgba(90, 98, 104, {alpha})'
    r = int(cor_limpa[0:2], 16)
    g = int(cor_limpa[2:4], 16)
    b = int(cor_limpa[4:6], 16)
    return f'rgba({r}, {g}, {b}, {alpha})'

def _formatar_variacao_resumo_barras(valor_origem: float, valor_destino: float) -> tuple[str, str]:
    """Calcula a variacao percentual exibida nos conectores entre barras."""
    if not np.isfinite(valor_origem) or float(valor_origem) == 0.0:
        return ("N/A", '#6B7280')
    variacao = ((float(valor_destino) - float(valor_origem)) / float(valor_origem)) * 100.0
    if variacao > 0:
        cor_var = '#2E7D32'
    elif variacao < 0:
        cor_var = '#C62828'
    else:
        cor_var = '#475569'
    return (f"{variacao:+.1f}%".replace('.', ','), cor_var)

def _criar_grafico_barras_resumo_comparativo(
    categorias: list[str],
    valores: list[float],
    cores: list[str],
    altura: int = 460,
    comparacoes: list[dict[str, int]] | None = None,
    posicoes_x: list[float] | None = None,
    larguras: list[float] | None = None,
    rotulos_externos: bool = False,
    offset_rotulo_externo: float | None = None,
    folga_seta_rotulo: float | None = None
) -> go.Figure:
    """Cria o grafico lateral premium com barras mais densas e conectores estilo ponte."""
    fig = go.Figure()
    if not categorias or not valores or not cores:
        return fig

    total_itens = min(len(categorias), len(valores), len(cores))
    categorias = [str(cat) for cat in categorias[:total_itens]]
    cores = [str(cor) for cor in cores[:total_itens]]

    def _coagir_float(valor) -> float:
        try:
            if pd.isna(valor):
                return 0.0
            return float(valor)
        except Exception:
            return 0.0

    valores = [_coagir_float(v) for v in valores[:total_itens]]
    textos = [formatar_numero_brasileiro(v, 0) for v in valores]
    if posicoes_x is None or len(posicoes_x) < total_itens:
        posicoes_x = list(range(total_itens))
    else:
        posicoes_x = [float(v) for v in posicoes_x[:total_itens]]
    max_val = float(max(valores)) if valores else 0.0
    largura_barra = 0.54 if total_itens <= 3 else 0.46
    if larguras is None or len(larguras) < total_itens:
        larguras = [largura_barra] * total_itens
    else:
        larguras = [float(v) for v in larguras[:total_itens]]
    offset_conector = max((max_val * 0.145), 3.0)
    offset_rotulo_calc = (
        float(offset_rotulo_externo)
        if offset_rotulo_externo is not None
        else max((max_val * 0.075), 2.2)
    )
    folga_seta_calc = (
        float(folga_seta_rotulo)
        if folga_seta_rotulo is not None
        else max((max_val * 0.028), 0.9)
    )
    cor_conector = 'rgba(90, 98, 104, 0.82)'

    if comparacoes is None and total_itens >= 3:
        comparacoes = [
            {'origem': 1, 'destino': 0},
            {'origem': 2, 'destino': 0},
        ]
    comparacoes = comparacoes or []

    fig.add_trace(go.Bar(
        x=posicoes_x,
        y=valores,
        width=larguras,
        marker=dict(
            color=cores,
            line=dict(color='rgba(0,0,0,0)', width=0)
        ),
        text=['' if rotulos_externos else (texto if float(valor) > 0 else '') for texto, valor in zip(textos, valores)],
        texttemplate='<b>%{text}</b>',
        textposition='outside' if rotulos_externos else 'inside',
        insidetextanchor='middle',
        insidetextfont=dict(
            family='Segoe UI Semibold',
            size=12,
            color='#FFF8F7'
        ),
        textangle=0,
        customdata=categorias,
        hovertemplate="<b>%{customdata}</b><br><b>Total:</b> %{y:,.0f}<extra></extra>",
        cliponaxis=False
    ))

    topos_rotulos = []
    for pos_x, valor_barra, texto_barra, cor_barra in zip(posicoes_x, valores, textos, cores):
        topo_rotulo = float(valor_barra)
        if rotulos_externos and float(valor_barra) > 0:
            topo_rotulo = float(valor_barra) + offset_rotulo_calc
            fig.add_annotation(
                x=pos_x,
                y=topo_rotulo,
                xref='x',
                yref='y',
                text=f"<b>{texto_barra}</b>",
                showarrow=False,
                font=dict(size=10, color='#5B6578')
            )
        topos_rotulos.append(topo_rotulo)

    for idx_seta, cfg in enumerate(comparacoes):
        idx_origem = int(cfg.get('origem', -1))
        idx_destino = int(cfg.get('destino', -1))
        if (
            idx_origem < 0 or idx_destino < 0 or
            idx_origem >= total_itens or idx_destino >= total_itens
        ):
            continue

        valor_origem = float(valores[idx_origem])
        valor_destino = float(valores[idx_destino])
        x0 = float(posicoes_x[idx_origem])
        x1 = float(posicoes_x[idx_destino])
        y_anchor_origem = float(topos_rotulos[idx_origem]) + (folga_seta_calc if rotulos_externos and valor_origem > 0 else 0.0)
        y_anchor_destino = float(topos_rotulos[idx_destino]) + (folga_seta_calc if rotulos_externos and valor_destino > 0 else 0.0)
        y_conector = max(y_anchor_origem, y_anchor_destino) + (offset_conector * (1.18 + (idx_seta * 1.08)))

        fig.add_shape(
            type='line',
            xref='x',
            yref='y',
            x0=min(x0, x1),
            x1=max(x0, x1),
            y0=y_conector,
            y1=y_conector,
            line=dict(color=cor_conector, width=1.65),
            layer='above'
        )

        for x_barra, y_barra in ((x0, y_anchor_origem), (x1, y_anchor_destino)):
            fig.add_annotation(
                x=x_barra,
                y=y_barra,
                ax=x_barra,
                ay=y_conector,
                xref='x',
                yref='y',
                axref='x',
                ayref='y',
                text='',
                showarrow=True,
                arrowhead=2,
                arrowsize=1.0,
                arrowwidth=1.55,
                arrowcolor=cor_conector,
                standoff=0
            )

        texto_var, cor_texto = _formatar_variacao_resumo_barras(valor_origem, valor_destino)
        x_label = (x0 + x1) / 2.0
        y_label = y_conector + (offset_conector * 0.26)
        raio_x = 0.44
        raio_y = max(offset_conector * 0.24, 6.2)
        fig.add_shape(
            type='circle',
            xref='x',
            yref='y',
            x0=x_label - (raio_x * 1.08),
            x1=x_label + (raio_x * 1.08),
            y0=y_label - (raio_y * 1.08),
            y1=y_label + (raio_y * 1.08),
            line=dict(color='rgba(121,14,9,0.00)', width=0),
            fillcolor='rgba(121,14,9,0.08)',
            layer='above'
        )
        fig.add_shape(
            type='circle',
            xref='x',
            yref='y',
            x0=x_label - raio_x,
            x1=x_label + raio_x,
            y0=y_label - raio_y,
            y1=y_label + raio_y,
            line=dict(color='rgba(255,255,255,1.0)', width=2.0),
            fillcolor='rgba(255,255,255,0.98)',
            layer='above'
        )
        fig.add_annotation(
            x=x_label,
            y=y_label,
            xref='x',
            yref='y',
            text=f"<b>{texto_var}</b>",
            showarrow=False,
            font=dict(size=11, color=cor_texto)
        )

    y_top_base = max([float(v) for v in topos_rotulos], default=max_val)
    y_top = (
        y_top_base + (offset_conector * (len(comparacoes) + 2.45)) + (folga_seta_calc if rotulos_externos else 0.0)
        if y_top_base > 0 else 5.0
    )

    fig.add_shape(
        type='line',
        xref='x',
        yref='y',
        x0=min(posicoes_x) - 0.42,
        x1=max(posicoes_x) + 0.42,
        y0=0,
        y1=0,
        line=dict(color='rgba(226, 232, 240, 0.95)', width=1.25),
        layer='below'
    )

    fig.update_layout(
        plot_bgcolor='#FFFFFF',
        paper_bgcolor='#FFFFFF',
        font=dict(family='Segoe UI', size=13, color='#2F3747'),
        margin=dict(l=14, r=10, t=36, b=42),
        xaxis=dict(
            title='',
            tickmode='array',
            tickvals=posicoes_x,
            ticktext=categorias,
            tickfont=dict(size=11, color='#5B6578'),
            showgrid=False,
            linecolor='#E2E8F0',
            linewidth=1.25,
            showline=False,
            ticks='outside',
            ticklen=0,
            zeroline=False
        ),
        yaxis=dict(
            title='',
            tickfont=dict(size=11, color='#5B6578'),
            showticklabels=False,
            ticks='',
            showgrid=True,
            gridcolor='rgba(226, 232, 240, 0.66)',
            gridwidth=1,
            linecolor='#E2E8F0',
            linewidth=1.2,
            showline=False,
            zeroline=False,
            rangemode='tozero',
            range=[0, y_top]
        ),
        bargap=0.32,
        bargroupgap=0.0,
        height=altura,
        hovermode='x',
        uniformtext=dict(minsize=11, mode='hide'),
        showlegend=False
    )

    apply_premium_plotly_theme(fig, title_size=15)
    return fig

def criar_grafico_barras_resumo_evolucao_semanal(
    categorias_totais: list[str],
    valores_totais: list[float],
    cores_totais: list[str],
    altura: int = 460,
    comparacoes: list[dict[str, int]] | None = None
) -> go.Figure:
    """Cria o grafico lateral semanal no mesmo padrao visual dos resumos mensais."""
    return _criar_grafico_barras_resumo_comparativo(
        categorias=categorias_totais,
        valores=valores_totais,
        cores=cores_totais,
        altura=altura,
        comparacoes=comparacoes
    )

def montar_ctx_plotly_evolucao_semanal(
    df_sem_base: pd.DataFrame,
    mes_sem_sel: str,
    canal_sem_sel: str,
    produto_sem_sel: str,
    regional_sem_sel: str
) -> dict[str, str]:
    if df_sem_base is None or df_sem_base.empty:
        return {}
    dt_mes_sem = pd.Timestamp(mes_ano_para_data(mes_sem_sel)).normalize()
    if dt_mes_sem.year != 2026:
        return {}

    mes_sem_m1 = get_mes_anterior(mes_sem_sel)
    dt_mes_sem_m1 = pd.Timestamp(mes_ano_para_data(mes_sem_m1)).normalize()
    regional_sem_norm3 = str(regional_sem_sel).strip().upper()[:3]
    usar_tendencia_grafico = str(mes_sem_sel).strip().lower() == get_mes_atual_formatado().strip().lower()

    df_sem = df_sem_base[df_sem_base['COD_PLATAFORMA'] == normalizar_rotulo_produto(produto_sem_sel)].copy()
    if canal_sem_sel != "Todos":
        df_sem = df_sem[df_sem['CANAL_PLAN'] == canal_sem_sel].copy()
    if regional_sem_sel != "Todas":
        df_sem = df_sem[df_sem['REGIONAL'].astype(str).str.strip().str.upper().str[:3].eq(regional_sem_norm3)].copy()
    if df_sem.empty:
        return {}

    aliases_real = {'GROSS LIQUIDO'} if produto_sem_sel == 'CONTA' else {'INSTALACAO', 'INSTALADOS', 'INSTAL'}
    aliases_real_norm = {normalizar_texto_chave(a) for a in aliases_real}
    aliases_meta_norm = {normalizar_texto_chave('GROSS LIQUIDO')}
    df_real_hist = df_sem[df_sem['DSC_IND_NORM'].isin(aliases_real_norm)].copy()
    df_mes_atual = df_sem[df_sem['dat_tratada'] == mes_sem_sel].copy()
    df_mes_anterior = df_sem[df_sem['dat_tratada'] == mes_sem_m1].copy()
    df_real_atual = df_mes_atual[df_mes_atual['DSC_IND_NORM'].isin(aliases_real_norm)].copy()
    df_real_m1 = df_mes_anterior[df_mes_anterior['DSC_IND_NORM'].isin(aliases_real_norm)].copy()
    df_meta_atual = df_mes_atual[df_mes_atual['DSC_IND_NORM'].isin(aliases_meta_norm)].copy()
    meta_mes_total = float(pd.to_numeric(df_meta_atual.get('DESAFIO_QTD', 0), errors='coerce').fillna(0).sum())

    def montar_calendario_mes(inicio_mes: pd.Timestamp) -> pd.DataFrame:
        fim_mes = (pd.Timestamp(inicio_mes) + pd.offsets.MonthEnd(0)).normalize()
        datas = pd.date_range(start=pd.Timestamp(inicio_mes).normalize(), end=fim_mes, freq='D')
        dia_abrev = {0: 'seg', 1: 'ter', 2: 'qua', 3: 'qui', 4: 'sex', 5: 'sab', 6: 'dom'}
        dia_inicio_mes = int(pd.Timestamp(inicio_mes).weekday())
        df_cal = pd.DataFrame({'DATA': datas})
        df_cal['DIA_SEMANA'] = df_cal['DATA'].dt.weekday.astype(int)
        df_cal['DIA_ABREV'] = df_cal['DIA_SEMANA'].map(dia_abrev)
        df_cal['DIA_ORDEM_REF'] = ((df_cal['DIA_SEMANA'] - dia_inicio_mes) % 7).astype(int)
        df_cal['SEMANA_IDX'] = ((df_cal['DATA'].dt.day - 1) // 7) + 1
        df_cal['SEMANA_LABEL'] = df_cal['SEMANA_IDX'].apply(lambda n: f"S{int(n)}")
        return df_cal.sort_values(['SEMANA_IDX', 'DIA_ORDEM_REF']).reset_index(drop=True)

    def agregar_valor_diario(df_in: pd.DataFrame, cal_ref: pd.DataFrame, coluna_valor: str = 'QTDE') -> pd.DataFrame:
        df_out = cal_ref.copy()
        if df_in is None or df_in.empty:
            df_out['VALOR_DIA'] = 0.0
            return df_out
        df_tmp = df_in.copy()
        df_tmp['DATA'] = pd.to_datetime(df_tmp['DAT_MOVIMENTO2'], errors='coerce').dt.normalize()
        df_tmp = df_tmp[df_tmp['DATA'].notna()].copy()
        if df_tmp.empty:
            df_out['VALOR_DIA'] = 0.0
            return df_out
        dt_min = pd.to_datetime(df_out['DATA']).min()
        dt_max = pd.to_datetime(df_out['DATA']).max()
        df_tmp = df_tmp[(df_tmp['DATA'] >= dt_min) & (df_tmp['DATA'] <= dt_max)].copy()
        if df_tmp.empty:
            df_out['VALOR_DIA'] = 0.0
            return df_out
        agg = df_tmp.groupby('DATA', as_index=False, observed=True)[coluna_valor].sum().rename(columns={coluna_valor: 'VALOR_DIA'})
        df_out = df_out.merge(agg, on='DATA', how='left')
        df_out['VALOR_DIA'] = pd.to_numeric(df_out['VALOR_DIA'], errors='coerce').fillna(0.0)
        return df_out

    cal_atual = montar_calendario_mes(dt_mes_sem)
    cal_m1 = montar_calendario_mes(dt_mes_sem_m1)
    serie_atual = agregar_valor_diario(df_real_atual, cal_atual, 'QTDE')
    serie_m1_base = agregar_valor_diario(df_real_m1, cal_m1, 'QTDE')
    prev_lookup = serie_m1_base.set_index(['SEMANA_IDX', 'DIA_SEMANA'])['VALOR_DIA'] if not serie_m1_base.empty else pd.Series(dtype='float64')
    prev_wd_media = serie_m1_base.groupby('DIA_SEMANA', observed=True)['VALOR_DIA'].mean() if not serie_m1_base.empty else pd.Series(dtype='float64')
    ctx_peso_proj = _montar_contexto_pesos_projecao_semana_dia(df_real_hist, mes_sem_sel, valor_col='QTDE')
    serie_atual['VALOR_FINAL'] = pd.to_numeric(serie_atual.get('VALOR_DIA', 0), errors='coerce').fillna(0.0)

    if usar_tendencia_grafico and not serie_atual.empty:
        tend_mes_total = float(pd.to_numeric(df_real_atual.get('TEND_QTD', 0), errors='coerce').fillna(0).sum())
        if tend_mes_total > 0:
            df_datas_real = df_real_atual.copy()
            df_datas_real['DATA'] = pd.to_datetime(df_datas_real.get('DAT_MOVIMENTO2'), errors='coerce').dt.normalize()
            df_datas_real['QTDE'] = pd.to_numeric(df_datas_real.get('QTDE', 0), errors='coerce').fillna(0.0)
            datas_validas = pd.to_datetime(df_datas_real.loc[df_datas_real['QTDE'] > 0, 'DATA'], errors='coerce').dropna()
            if datas_validas.empty:
                datas_validas = pd.to_datetime(df_datas_real.loc[df_datas_real['DATA'].notna(), 'DATA'], errors='coerce').dropna()
            if datas_validas.empty:
                data_corte = pd.Timestamp(dt_mes_sem).normalize() - pd.Timedelta(days=1)
            else:
                data_corte = pd.Timestamp(datas_validas.max()).normalize()
                limite_real = pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
                if data_corte > limite_real:
                    data_corte = limite_real
            mask_realizado = pd.to_datetime(serie_atual['DATA'], errors='coerce') <= data_corte
            real_total = float(pd.to_numeric(serie_atual.loc[mask_realizado, 'VALOR_FINAL'], errors='coerce').fillna(0).sum())
            gap_tend = float(tend_mes_total) - real_total
            if gap_tend > 0:
                mask_restante = pd.to_datetime(serie_atual['DATA'], errors='coerce') > data_corte
                if bool(mask_restante.any()):
                    idx_restantes = list(serie_atual.index[mask_restante])
                    pesos_tend = []
                    for idx_row in idx_restantes:
                        peso = _obter_peso_projecao_semana_dia(ctx_peso_proj, int(serie_atual.at[idx_row, 'SEMANA_IDX']), int(serie_atual.at[idx_row, 'DIA_SEMANA']))
                        pesos_tend.append(max(float(peso), 0.0))
                    soma_pesos_tend = float(np.sum(pesos_tend))
                    if soma_pesos_tend <= 0:
                        pesos_tend = [1.0] * len(idx_restantes)
                        soma_pesos_tend = float(len(idx_restantes)) if idx_restantes else 1.0
                    addicoes = [gap_tend * (p / soma_pesos_tend) for p in pesos_tend]
                    if addicoes:
                        addicoes[-1] = addicoes[-1] + (gap_tend - float(np.sum(addicoes)))
                        for idx_row, add_val in zip(idx_restantes, addicoes):
                            serie_atual.at[idx_row, 'VALOR_FINAL'] = float(serie_atual.at[idx_row, 'VALOR_FINAL']) + float(add_val)

    serie_atual['REAL_M1_DIA'] = [
        float((prev_wd_media.get(int(row.DIA_SEMANA), 0.0) if pd.isna(prev_lookup.get((int(row.SEMANA_IDX), int(row.DIA_SEMANA)), np.nan)) else prev_lookup.get((int(row.SEMANA_IDX), int(row.DIA_SEMANA)), 0.0)) or 0.0)
        for row in serie_atual.itertuples(index=False)
    ]
    pesos_meta = [max(float(_obter_peso_projecao_semana_dia(ctx_peso_proj, int(row.SEMANA_IDX), int(row.DIA_SEMANA))), 0.0) for row in serie_atual.itertuples(index=False)]
    soma_pesos = float(np.sum(pesos_meta))
    if soma_pesos <= 0:
        pesos_meta = [1.0] * len(serie_atual)
        soma_pesos = float(len(serie_atual)) if len(serie_atual) > 0 else 1.0
    meta_diaria = [meta_mes_total * (peso / soma_pesos) for peso in pesos_meta]
    if meta_diaria:
        meta_diaria[-1] = meta_diaria[-1] + (meta_mes_total - float(np.sum(meta_diaria)))
    serie_atual['META_DIA'] = meta_diaria if meta_diaria else 0.0
    if serie_atual.empty:
        return {}

    cores = {'REAL_ATUAL': '#FF2800', 'REAL_M1': '#790E09', 'META': '#5A6268'}
    def _rotulos_trace(serie_valor: pd.Series) -> list[str]:
        return [formatar_numero_brasileiro(v, 0) if pd.notna(v) and float(v) != 0 else '' for v in list(serie_valor)]

    nome_trace_real = 'Atual/Tend.' if usar_tendencia_grafico else 'Atual'
    hover_real_atual = "Realizado/Tendencia (Dia)" if usar_tendencia_grafico else "Realizado Atual (Dia)"
    x_multicat = [serie_atual['SEMANA_LABEL'].tolist(), serie_atual['DIA_ABREV'].tolist()]
    datas_hover = serie_atual['DATA'].dt.strftime('%d/%m/%Y').tolist()
    fig_semanal = go.Figure()
    fig_semanal.add_trace(
        go.Scatter(
            x=x_multicat,
            y=serie_atual['VALOR_FINAL'],
            mode='lines+markers+text',
            name=nome_trace_real,
            marker=dict(
                size=9,
                symbol='circle',
                color=cores['REAL_ATUAL'],
                line=dict(width=1.4, color='white')
            ),
            line=dict(width=3.0, color=cores['REAL_ATUAL'], shape='spline', smoothing=1.1),
            text=_rotulos_trace(serie_atual['VALOR_FINAL']),
            textposition='top left',
            textfont=dict(size=11, color=cores['REAL_ATUAL']),
            customdata=datas_hover,
            hovertemplate=f"<b>%{{customdata}}</b><br><b>{hover_real_atual}:</b> %{{y:,.0f}}<extra></extra>",
            cliponaxis=False
        )
    )
    fig_semanal.add_trace(
        go.Scatter(
            x=x_multicat,
            y=serie_atual['REAL_M1_DIA'],
            mode='lines+markers+text',
            name='M-1',
            marker=dict(
                size=9,
                symbol='circle',
                color=cores['REAL_M1'],
                line=dict(width=1.4, color='white')
            ),
            line=dict(width=3.0, color=cores['REAL_M1'], shape='spline', smoothing=1.1),
            text=_rotulos_trace(serie_atual['REAL_M1_DIA']),
            textposition='top center',
            textfont=dict(size=11, color=cores['REAL_M1']),
            customdata=datas_hover,
            hovertemplate="<b>%{customdata}</b><br><b>Realizado M-1 (Dia):</b> %{y:,.0f}<extra></extra>",
            cliponaxis=False
        )
    )
    fig_semanal.add_trace(
        go.Scatter(
            x=x_multicat,
            y=serie_atual['META_DIA'],
            mode='lines+markers+text',
            name='Orçamento',
            marker=dict(
                size=10,
                symbol='diamond',
                color=cores['META'],
                line=dict(width=1.4, color='white')
            ),
            line=dict(width=3.2, color=cores['META'], dash='dash', shape='spline', smoothing=1.0),
            text=_rotulos_trace(serie_atual['META_DIA']),
            textposition='top right',
            textfont=dict(size=11, color=cores['META']),
            customdata=datas_hover,
            hovertemplate="<b>%{customdata}</b><br><b>Orç Diaria:</b> %{y:,.0f}<extra></extra>",
            cliponaxis=False
        )
    )
    fig_semanal.update_layout(title=dict(text=""), height=460, showlegend=True)
    aplicar_estilo_visual_evolucao_semanal(fig_semanal, rotulo_mes_foco=str(mes_sem_sel).upper(), nome_serie_real=nome_trace_real, altura=460)
    fig_totais_sem = criar_grafico_barras_resumo_evolucao_semanal(
        categorias_totais=['M-1', 'M-0', 'ORÇ'],
        valores_totais=[
            float(pd.to_numeric(serie_atual['REAL_M1_DIA'], errors='coerce').fillna(0).sum()),
            float(pd.to_numeric(serie_atual['VALOR_FINAL'], errors='coerce').fillna(0).sum()),
            float(pd.to_numeric(serie_atual['META_DIA'], errors='coerce').fillna(0).sum())
        ],
        cores_totais=[cores['REAL_M1'], cores['REAL_ATUAL'], cores['META']],
        altura=460,
        comparacoes=[{'origem': 0, 'destino': 1}, {'origem': 2, 'destino': 1}]
    )
    payload = {
        "fig_principal_json": fig_semanal.to_json(),
        "fig_resumo_json": fig_totais_sem.to_json(),
        "mes": str(mes_sem_sel).upper()
    }
    del fig_semanal, fig_totais_sem
    gc.collect()
    return payload

def criar_grafico_barras_resumo_evolucao_mensal(
    df_linhas_base: pd.DataFrame,
    altura: int = 400,
    serie_real_tend: str = '2026 Real/Tend',
    serie_orc: str = '2026',
    serie_ano_anterior: str = '2025',
    mes_ref_num: int | None = None,
    rotulo_mes_ref: str | None = None
) -> go.Figure:
    """Cria gráfico lateral com M-1, mês de referência e Orç a partir da mesma base da linha."""
    meses_pt = {
        1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
        7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
    }

    base = df_linhas_base.copy() if df_linhas_base is not None else pd.DataFrame()
    if base.empty:
        return go.Figure()

    base['Ano'] = base.get('Ano', '').astype(str)
    base['Mês_Num'] = pd.to_numeric(base.get('Mês_Num', 0), errors='coerce').fillna(0).astype(int)
    base['Valor'] = pd.to_numeric(base.get('Valor', 0), errors='coerce').fillna(0.0)

    def _valor_mes(nome_serie: str, mes_num: int) -> float:
        mask = base['Ano'].eq(str(nome_serie)) & base['Mês_Num'].eq(int(mes_num))
        valores = base.loc[mask, 'Valor']
        if valores.empty:
            return 0.0
        return float(valores.iloc[0] or 0.0)

    if mes_ref_num is None:
        mes_ref_num = int(pd.Timestamp.today().month)
    mes_ref_num = int(mes_ref_num)
    mes_anterior = 12 if mes_ref_num == 1 else (mes_ref_num - 1)

    valor_m0 = _valor_mes(serie_real_tend, mes_ref_num)
    if mes_ref_num == 1:
        valor_m1 = _valor_mes(serie_ano_anterior, mes_anterior)
    else:
        valor_m1 = _valor_mes(serie_real_tend, mes_anterior)
    valor_orc = _valor_mes(serie_orc, mes_ref_num)

    rotulo_mes_atual = str(rotulo_mes_ref or meses_pt.get(mes_ref_num, 'mes')).upper()
    categorias = ['M-1', rotulo_mes_atual, 'ORÇ']
    valores = [valor_m1, valor_m0, valor_orc]
    posicoes_x = list(range(len(categorias)))
    cores = ['#790E09', '#FF2800', '#5A6268']
    fig = _criar_grafico_barras_resumo_comparativo(
        categorias=categorias,
        valores=valores,
        cores=cores,
        altura=altura,
        comparacoes=[
            {'origem': 0, 'destino': 1},
            {'origem': 2, 'destino': 1}
        ]
    )
    fig.update_layout(
        hoverlabel=dict(
            bgcolor='white',
            font_size=12,
            font_family='Segoe UI',
            bordercolor='#E2E8F0',
            font_color='#2F3747'
        )
    )
    return fig

    textos = [formatar_numero_brasileiro(v, 0) for v in valores]

    def _hex_to_rgba(cor_hex: str, alpha: float) -> str:
        cor_limpa = str(cor_hex).strip().lstrip('#')
        if len(cor_limpa) != 6:
            return f'rgba(90, 98, 104, {alpha})'
        r = int(cor_limpa[0:2], 16)
        g = int(cor_limpa[2:4], 16)
        b = int(cor_limpa[4:6], 16)
        return f'rgba({r}, {g}, {b}, {alpha})'

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=posicoes_x,
        y=valores,
        width=[0.42, 0.42, 0.42],
        marker=dict(color=cores, line=dict(color='white', width=1.4)),
        text=['', '', ''],
        textposition='outside',
        textfont=dict(size=12, color='#2F3747'),
        customdata=categorias,
        hovertemplate="<b>%{customdata}</b><br><b>Total:</b> %{y:,.0f}<extra></extra>",
        cliponaxis=False
    ))

    max_val = float(max(valores)) if valores else 0.0
    offset_rotulo = max((max_val * 0.055), 1.0)
    offset_seta = max((max_val * 0.19), 1.0)
    cor_seta = '#6B7280'

    base_destaque = max((max_val * 0.012), 1.0)
    for pos_x, valor_barra, cor_barra in zip(posicoes_x, valores, cores):
        fig.add_shape(
            type='rect',
            xref='x',
            yref='y',
            x0=float(pos_x) - 0.31,
            x1=float(pos_x) + 0.31,
            y0=0,
            y1=max(float(valor_barra) + base_destaque, base_destaque),
            line=dict(color=_hex_to_rgba(cor_barra, 0.18), width=1.0),
            fillcolor=_hex_to_rgba(cor_barra, 0.07),
            layer='below'
        )

    for pos_x, valor_barra, texto_barra, cor_barra in zip(posicoes_x, valores, textos, cores):
        fig.add_annotation(
            x=pos_x,
            y=float(valor_barra) + offset_rotulo,
            xref='x',
            yref='y',
            text=f"<b>{texto_barra}</b>",
            showarrow=False,
            font=dict(size=11, color=cor_barra),
            bgcolor='rgba(255,255,255,0.96)',
            bordercolor=cor_barra,
            borderwidth=1.1,
            borderpad=5
        )

    def _formatar_variacao(valor_origem: float, valor_destino: float) -> tuple[str, str]:
        if not np.isfinite(valor_origem) or float(valor_origem) == 0.0:
            return ("N/A", '#6B7280')
        variacao = ((float(valor_destino) - float(valor_origem)) / float(valor_origem)) * 100.0
        if variacao > 0:
            cor_var = '#2E7D32'
        elif variacao < 0:
            cor_var = '#C62828'
        else:
            cor_var = '#475569'
        return (f"{variacao:+.1f}%".replace('.', ','), cor_var)

    comparacoes = [
        {'origem': 1, 'destino': 0},  # M-0 vs M-1
        {'origem': 2, 'destino': 0},  # M-0 vs Orç
    ]

    for idx_seta, cfg in enumerate(comparacoes):
        idx_origem = int(cfg['origem'])
        idx_destino = int(cfg['destino'])
        valor_origem = float(valores[idx_origem])
        valor_destino = float(valores[idx_destino])
        y_linha = max(valor_origem, valor_destino) + (offset_seta * (1.0 + (idx_seta * 1.0)))
        x0 = float(posicoes_x[idx_origem])
        x1 = float(posicoes_x[idx_destino])

        fig.add_annotation(
            x=x1, y=y_linha, ax=x0, ay=y_linha,
            xref='x', yref='y', axref='x', ayref='y',
            text='',
            showarrow=True,
            arrowhead=2,
            arrowsize=1.1,
            arrowwidth=1.8,
            arrowcolor=cor_seta
        )
        fig.add_annotation(
            x=x0, y=y_linha, ax=x1, ay=y_linha,
            xref='x', yref='y', axref='x', ayref='y',
            text='',
            showarrow=True,
            arrowhead=2,
            arrowsize=1.1,
            arrowwidth=1.8,
            arrowcolor=cor_seta
        )

        texto_var, cor_texto = _formatar_variacao(valor_origem, valor_destino)
        fig.add_annotation(
            x=(x0 + x1) / 2.0,
            y=y_linha + (offset_seta * 0.16),
            xref='x',
            yref='y',
            text=f"<b>{texto_var}</b>",
            showarrow=False,
            font=dict(size=11, color=cor_texto)
        )

    y_top = (max_val + offset_rotulo + (offset_seta * 4.4)) if max_val > 0 else 5.0
    fig.update_layout(
        plot_bgcolor='#FFFFFF',
        paper_bgcolor='#F4F7FB',
        font=dict(family='Segoe UI', size=13, color='#2F3747'),
        margin=dict(l=20, r=16, t=64, b=52),
        xaxis=dict(
            title='',
            tickmode='array',
            tickvals=posicoes_x,
            ticktext=categorias,
            tickfont=dict(size=11, color='#5B6578'),
            showgrid=False,
            linecolor='#E2E8F0',
            linewidth=1.3,
            showline=True,
            ticks='outside',
            ticklen=5,
            zeroline=False
        ),
        yaxis=dict(
            title='',
            tickfont=dict(size=11, color='#5B6578'),
            showticklabels=False,
            ticks='',
            showgrid=True,
            gridcolor='rgba(226, 232, 240, 0.70)',
            gridwidth=1,
            linecolor='#E2E8F0',
            linewidth=1.3,
            showline=True,
            zeroline=False,
            rangemode='tozero',
            range=[0, y_top]
        ),
        bargap=0.38,
        bargroupgap=0.0,
        height=altura,
        showlegend=False
    )

    return fig

def normalizar_rotulo_produto(valor) -> str:
    """Normaliza rótulo de produto para uso em legenda e barras."""
    if pd.isna(valor):
        return "N/D"

    texto = str(valor).strip()
    if not texto:
        return "N/D"

    base = unicodedata.normalize("NFKD", texto)
    base = base.encode("ASCII", "ignore").decode("ASCII")
    base = base.upper().strip()
    base = re.sub(r"\s+", " ", base)

    if base in {"NAN", "NONE", "NULL", "N/D"}:
        return "N/D"
    if "FIXA" in base:
        return "FIXA"
    if ("MOVEL" in base) or ("MOBILE" in base):
        return "CONTA"
    if "CONTA" in base:
        return "CONTA"
    if "CLICK" in base and "CALL" in base:
        return "CLICK TO CALL"
    if base in {"CTC"}:
        return "CLICK TO CALL"
    if re.fullmatch(r"\d+", base):
        return f"PRODUTO {base}"
    return base


@st.cache_data(show_spinner=False, max_entries=4, ttl=1800)
def cached_fig_linhas_json(
    df_json: str,
    altura: int,
    mes_referencia: str,
    titulo_eixo: str,
    valor_label: str
) -> str:
    df = desserializar_dataframe_cache(df_json)
    if df.empty:
        return go.Figure().to_json()

    cores_personalizadas = {
        '2025': '#790E09',
        '2026 Real/Tend': '#FF2800',
        '2026': '#5A6268'
    }
    fig = px.line(
        df,
        x='Mês',
        y='Valor',
        color='Ano',
        title='',
        labels={'Valor': titulo_eixo, 'Mês': ''},
        markers=True,
        line_shape='spline',
        color_discrete_map=cores_personalizadas
    )
    apply_standard_line_layout(fig, titulo_eixo, height=altura)
    apply_standard_line_traces(fig, cores_personalizadas, valor_label=valor_label, meta_year='2026')
    ocultar_rotulo_orc_sobreposto(fig)
    aplicar_estilo_visual_evolucao_mensal(
        fig,
        altura=altura,
        mes_referencia=mes_referencia
    )
    return fig.to_json()


@st.cache_data(show_spinner=False, max_entries=4, ttl=1800)
def cached_fig_bar_resumo_json(
    df_json: str,
    altura: int,
    mes_ref_num: int,
    rotulo_mes_ref: str
) -> str:
    df = desserializar_dataframe_cache(df_json)
    fig = criar_grafico_barras_resumo_evolucao_mensal(
        df,
        altura=altura,
        mes_ref_num=mes_ref_num,
        rotulo_mes_ref=rotulo_mes_ref
    )
    compactar_resumo_evolucao_mensal(fig, altura)
    return fig.to_json()

def create_bar_chart_data(df_mes_selecionado):
    """Cria dados para gráfico de barras horizontais"""
    df_plot = df_mes_selecionado.copy()
    df_plot['CANAL_PLAN'] = df_plot['CANAL_PLAN'].astype(str).str.strip()
    df_plot['COD_PLATAFORMA'] = df_plot['COD_PLATAFORMA'].apply(normalizar_rotulo_produto)
    df_plot['QTDE'] = pd.to_numeric(df_plot['QTDE'], errors='coerce').fillna(0)
    bar_data = df_plot.groupby(['CANAL_PLAN', 'COD_PLATAFORMA'], observed=True)['QTDE'].sum().reset_index()
    canal_totals = bar_data.groupby('CANAL_PLAN', observed=True)['QTDE'].sum().sort_values(ascending=False)
    canal_order = canal_totals.index
    
    bar_data['CANAL_PLAN'] = pd.Categorical(bar_data['CANAL_PLAN'], categories=canal_order, ordered=True)
    bar_data = bar_data.sort_values('CANAL_PLAN', ascending=False)
    bar_data['QTDE_Formatado'] = bar_data['QTDE'].apply(lambda x: formatar_numero_brasileiro(x, 0))
    
    return bar_data, canal_totals

def contar_dias_restantes_semana(mes_ref: str, data_corte: date | None = None) -> dict[int, int]:
    """
    Conta quantas ocorrências de SEG..DOM restam até o fim do mês de referência.
    Retorna dict com chaves 0..6 (segunda..domingo).
    """
    try:
        inicio_mes = pd.Timestamp(mes_ano_para_data(mes_ref)).normalize().date()
    except Exception:
        return {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}

    fim_mes = (pd.Timestamp(inicio_mes) + pd.offsets.MonthEnd(0)).date()
    try:
        hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    except Exception:
        hoje = date.today()

    if data_corte is None:
        corte_base = inicio_mes - timedelta(days=1)
    else:
        corte_base = pd.Timestamp(data_corte).date()

    if str(mes_ref).strip().lower() == get_mes_atual_formatado().strip().lower():
        corte_base = max(corte_base, hoje - timedelta(days=1))

    inicio_planejamento = max(inicio_mes, corte_base + timedelta(days=1))
    if inicio_planejamento > fim_mes:
        return {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}

    intervalo = pd.date_range(start=inicio_planejamento, end=fim_mes, freq='D')
    return {wd: int((intervalo.weekday == wd).sum()) for wd in range(7)}

def contar_dias_uteis_restantes(mes_ref: str, data_corte: date | None = None) -> dict[int, int]:
    """
    Compatibilidade retroativa: mantém assinatura antiga retornando SEG..DOM.
    """
    return contar_dias_restantes_semana(mes_ref, data_corte)

def construir_tabela_analitica_produto(
    df_base: pd.DataFrame,
    mes_ref: str,
    indicador_real_ref: str,
    regional_ref: str,
    produto_ref: str,
    indicador_meta_ref: str | None = None
) -> tuple[pd.DataFrame, dict]:
    """
    Monta tabela analítica por canal com meta diária SEG..DOM para atingir a meta do mês.
    """
    colunas_saida = [
        'CANAL',
        'MES_ANTERIOR',
        'MES_ATUAL_REAL',
        'META_MES',
        'VAR_META_X_REAL',
        'SEG',
        'TER',
        'QUA',
        'QUI',
        'SEX',
        'SAB',
        'DOM'
    ]

    if df_base is None or df_base.empty:
        return pd.DataFrame(columns=colunas_saida), {
            'mes_anterior': get_mes_anterior(mes_ref),
            'dias_restantes': {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0},
            'data_corte': None
        }

    df_work = df_base.copy()
    for col in ['CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'REGIONAL', 'dat_tratada']:
        if col in df_work.columns:
            df_work[col] = df_work[col].astype(str).str.strip()

    if 'COD_PLATAFORMA' not in df_work.columns:
        return pd.DataFrame(columns=colunas_saida), {
            'mes_anterior': get_mes_anterior(mes_ref),
            'dias_restantes': {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0},
            'data_corte': None
        }

    df_work['COD_PLATAFORMA'] = df_work['COD_PLATAFORMA'].apply(normalizar_rotulo_produto)
    df_work['QTDE'] = normalizar_numerico_serie(df_work.get('QTDE', 0)).fillna(0)
    df_work['DESAFIO_QTD'] = normalizar_numerico_serie(df_work.get('DESAFIO_QTD', 0)).fillna(0)
    df_work['DAT_MOVIMENTO2'] = pd.to_datetime(df_work.get('DAT_MOVIMENTO2'), errors='coerce')

    produto_norm = normalizar_rotulo_produto(produto_ref)
    df_work = df_work[df_work['COD_PLATAFORMA'] == produto_norm].copy()

    if regional_ref and regional_ref != "Todas":
        df_work = df_work[df_work['REGIONAL'] == regional_ref].copy()

    indicador_meta_ref = indicador_real_ref if indicador_meta_ref is None else indicador_meta_ref

    def _filtrar_indicador(df_in: pd.DataFrame, indicador: str) -> pd.DataFrame:
        if indicador and indicador != "Todos":
            indicador_cmp = str(indicador).strip().upper()
            return df_in[df_in['DSC_INDICADOR'].str.upper() == indicador_cmp].copy()
        return df_in.copy()

    df_real = _filtrar_indicador(df_work, indicador_real_ref)
    df_meta = _filtrar_indicador(df_work, indicador_meta_ref)

    mes_anterior_ref = get_mes_anterior(mes_ref)
    df_mes_atual_real = df_real[df_real['dat_tratada'] == mes_ref].copy()
    df_mes_anterior_real = df_real[df_real['dat_tratada'] == mes_anterior_ref].copy()
    df_mes_atual_meta = df_meta[df_meta['dat_tratada'] == mes_ref].copy()

    datas_validas = df_mes_atual_real['DAT_MOVIMENTO2'].dropna()
    data_corte = datas_validas.max().date() if not datas_validas.empty else None
    dias_restantes = contar_dias_restantes_semana(mes_ref, data_corte)

    df_hist = df_real[df_real['DAT_MOVIMENTO2'].notna()].copy()
    df_hist['DIA_SEMANA'] = df_hist['DAT_MOVIMENTO2'].dt.weekday
    df_hist['DIA_SEMANA'] = pd.to_numeric(df_hist['DIA_SEMANA'], errors='coerce')
    df_hist = df_hist[df_hist['DIA_SEMANA'].notna()].copy()
    df_hist['DIA_SEMANA'] = df_hist['DIA_SEMANA'].astype(int)
    df_hist['DATA_DIA'] = pd.to_datetime(df_hist['DAT_MOVIMENTO2'], errors='coerce').dt.normalize()

    def _mes_ref_para_ts(valor_mes: str) -> pd.Timestamp:
        try:
            return pd.Timestamp(mes_ano_para_data(str(valor_mes))).normalize()
        except Exception:
            return pd.NaT

    def _media_ponderada_robusta(valores: np.ndarray, decay: float = 0.88) -> float:
        arr = np.asarray(valores, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return 0.0
        if arr.size >= 5:
            q_low, q_high = np.quantile(arr, [0.10, 0.90])
            arr = np.clip(arr, q_low, q_high)
        n = arr.size
        pesos = np.array([decay ** (n - 1 - i) for i in range(n)], dtype=float)
        soma_pesos = float(pesos.sum())
        if soma_pesos <= 0:
            return float(np.mean(arr))
        return float(np.dot(arr, pesos) / soma_pesos)

    df_hist['MES_REF_TS'] = df_hist['dat_tratada'].apply(_mes_ref_para_ts)
    mes_ref_ts = _mes_ref_para_ts(mes_ref)
    df_hist_treino = df_hist.copy()
    if pd.notna(mes_ref_ts):
        df_hist_treino = df_hist_treino[df_hist_treino['MES_REF_TS'] < mes_ref_ts].copy()
        if df_hist_treino.empty:
            df_hist_treino = df_hist.copy()

    meses_hist = pd.to_datetime(df_hist_treino['MES_REF_TS'].dropna().unique(), errors='coerce')
    meses_hist = sorted([m for m in meses_hist if pd.notna(m)])
    if len(meses_hist) > 3:
        mes_limite = meses_hist[-3]
        df_hist_treino = df_hist_treino[df_hist_treino['MES_REF_TS'] >= mes_limite].copy()

    base_mes_dia = (
        df_hist_treino
        .groupby(['CANAL_PLAN', 'MES_REF_TS', 'DIA_SEMANA'], observed=True)['QTDE']
        .sum()
        .reset_index(name='qtd')
    )

    cache_contagem_mes: dict[str, dict[int, int]] = {}
    def _contar_weekday_mes(mes_ts: pd.Timestamp) -> dict[int, int]:
        chave = pd.Timestamp(mes_ts).strftime('%Y-%m')
        if chave in cache_contagem_mes:
            return cache_contagem_mes[chave]
        inicio = pd.Timestamp(mes_ts).normalize()
        fim = (inicio + pd.offsets.MonthEnd(0)).normalize()
        intervalo = pd.date_range(start=inicio, end=fim, freq='D')
        counts = np.bincount(intervalo.weekday, minlength=7)
        out = {wd: int(counts[wd]) for wd in range(7)}
        cache_contagem_mes[chave] = out
        return out

    if base_mes_dia.empty:
        agg_mes_dia = pd.DataFrame(columns=['CANAL_PLAN', 'MES_REF_TS', 'DIA_SEMANA', 'qtd', 'dias', 'taxa'])
    else:
        base_mes_dia['MES_REF_TS'] = pd.to_datetime(base_mes_dia['MES_REF_TS'], errors='coerce').dt.normalize()
        combos = base_mes_dia[['CANAL_PLAN', 'MES_REF_TS']].dropna().drop_duplicates()
        if combos.empty:
            agg_mes_dia = pd.DataFrame(columns=['CANAL_PLAN', 'MES_REF_TS', 'DIA_SEMANA', 'qtd', 'dias', 'taxa'])
        else:
            wd_ref = pd.DataFrame({'DIA_SEMANA': list(range(7))})
            combos = combos.assign(_k=1)
            wd_ref = wd_ref.assign(_k=1)
            grade = combos.merge(wd_ref, on='_k', how='inner').drop(columns=['_k'])

            base_mes_dia['DIA_SEMANA'] = pd.to_numeric(base_mes_dia['DIA_SEMANA'], errors='coerce').astype('Int64')
            base_mes_dia = base_mes_dia[base_mes_dia['DIA_SEMANA'].between(0, 6, inclusive='both')].copy()
            base_mes_dia['DIA_SEMANA'] = base_mes_dia['DIA_SEMANA'].astype(int)

            agg_mes_dia = grade.merge(
                base_mes_dia[['CANAL_PLAN', 'MES_REF_TS', 'DIA_SEMANA', 'qtd']],
                on=['CANAL_PLAN', 'MES_REF_TS', 'DIA_SEMANA'],
                how='left'
            )
            agg_mes_dia['qtd'] = pd.to_numeric(agg_mes_dia['qtd'], errors='coerce').fillna(0.0)

            meses_unicos = pd.to_datetime(combos['MES_REF_TS'].dropna().unique(), errors='coerce')
            linhas_cal = []
            for mes_calc in meses_unicos:
                if pd.isna(mes_calc):
                    continue
                contagem = _contar_weekday_mes(pd.Timestamp(mes_calc))
                for wd, qtd_wd in contagem.items():
                    linhas_cal.append({
                        'MES_REF_TS': pd.Timestamp(mes_calc).normalize(),
                        'DIA_SEMANA': int(wd),
                        'dias': float(qtd_wd)
                    })
            df_cal = pd.DataFrame(linhas_cal)
            agg_mes_dia = agg_mes_dia.merge(df_cal, on=['MES_REF_TS', 'DIA_SEMANA'], how='left')
            agg_mes_dia['dias'] = pd.to_numeric(agg_mes_dia['dias'], errors='coerce').fillna(0.0)
            agg_mes_dia['taxa'] = np.where(agg_mes_dia['dias'] > 0, agg_mes_dia['qtd'] / agg_mes_dia['dias'], 0.0)

    def _taxa_global(df_train_agg: pd.DataFrame) -> tuple[dict[int, float], float]:
        if df_train_agg.empty:
            return {wd: 1.0 for wd in range(7)}, 1.0
        stats = (
            df_train_agg
            .groupby('DIA_SEMANA', observed=True)
            .agg(qtd=('qtd', 'sum'), dias=('dias', 'sum'))
        )
        qtd_total = float(df_train_agg['qtd'].sum())
        dias_total = float(df_train_agg['dias'].sum())
        media_global = (qtd_total / dias_total) if dias_total > 0 else 1.0
        if media_global <= 0:
            media_global = 1.0
        taxas = {}
        for wd in range(7):
            qtd_wd = float(stats['qtd'].get(wd, 0.0)) if 'qtd' in stats else 0.0
            dias_wd = float(stats['dias'].get(wd, 0.0)) if 'dias' in stats else 0.0
            taxa_wd = (qtd_wd / dias_wd) if dias_wd > 0 else 0.0
            taxas[wd] = float(taxa_wd if taxa_wd > 0 else media_global)
        return taxas, media_global

    def _score_semana_from_taxa(taxas_ref: dict[int, float], contagem_semana: dict[int, int]) -> np.ndarray:
        """Converte taxa por dia em score mensal considerando quantidade de ocorrências no mês."""
        return np.array(
            [
                max(float(taxas_ref.get(wd, 0.0)), 0.0) * float(contagem_semana.get(wd, 0))
                for wd in range(7)
            ],
            dtype=float
        )

    def _combinar_score_canal_global(
        score_canal: np.ndarray,
        score_global: np.ndarray,
        volume_hist: float,
        blend_alpha: float,
        vol_k: float
    ) -> np.ndarray:
        """Combina score do canal com score global usando confiança baseada em volume histórico."""
        vol_ref = max(float(volume_hist), 0.0)
        vol_k_ref = max(float(vol_k), 1.0)
        conf_volume = vol_ref / (vol_ref + vol_k_ref)
        alpha_eff = float(np.clip(float(blend_alpha) * conf_volume, 0.0, 1.0))
        return (alpha_eff * score_canal) + ((1.0 - alpha_eff) * score_global)

    def _taxa_fallback_robusta(
        hist_canal_agg: pd.DataFrame,
        agg_hist_total: pd.DataFrame,
        taxas_global_ref: dict[int, float],
        media_global_ref: float,
        meses_recencia_global: int = 4,
        meses_recencia_canal: int = 2
    ) -> dict[int, float]:
        """Fallback estável: perfil global recente + ajuste leve do canal."""
        taxas_out = taxas_global_ref.copy()
        if agg_hist_total is None or agg_hist_total.empty:
            return taxas_out

        def _calc_rate_with_support(df_tmp: pd.DataFrame) -> tuple[dict[int, float], dict[int, float], dict[int, float]]:
            if df_tmp is None or df_tmp.empty:
                vazio_rate = {wd: 0.0 for wd in range(7)}
                vazio_sup = {wd: 0.0 for wd in range(7)}
                return vazio_rate, vazio_sup, vazio_sup
            st = (
                df_tmp
                .groupby('DIA_SEMANA', observed=True)
                .agg(q=('qtd', 'sum'), d=('dias', 'sum'))
            )
            out_rate: dict[int, float] = {}
            out_dias: dict[int, float] = {}
            out_qtd: dict[int, float] = {}
            for wd in range(7):
                q = float(st['q'].get(wd, 0.0)) if 'q' in st else 0.0
                d = float(st['d'].get(wd, 0.0)) if 'd' in st else 0.0
                out_rate[wd] = float((q / d) if d > 0 else 0.0)
                out_dias[wd] = float(max(d, 0.0))
                out_qtd[wd] = float(max(q, 0.0))
            return out_rate, out_dias, out_qtd

        meses_ord = sorted(pd.to_datetime(agg_hist_total['MES_REF_TS'].dropna().unique(), errors='coerce'))
        meses_ord = [m for m in meses_ord if pd.notna(m)]
        if not meses_ord:
            return taxas_out

        meses_global = set(meses_ord[-max(1, int(meses_recencia_global)):])
        df_global_rec = agg_hist_total[agg_hist_total['MES_REF_TS'].isin(meses_global)]
        taxa_global_rec, dias_global_rec, _ = _calc_rate_with_support(df_global_rec)

        hist_canal = hist_canal_agg.copy() if hist_canal_agg is not None else pd.DataFrame()
        if not hist_canal.empty:
            meses_canal = sorted(pd.to_datetime(hist_canal['MES_REF_TS'].dropna().unique(), errors='coerce'))
            meses_canal = [m for m in meses_canal if pd.notna(m)]
            meses_canal_rec = set(meses_canal[-max(1, int(meses_recencia_canal)):]) if meses_canal else set()
            hist_canal_rec = hist_canal[hist_canal['MES_REF_TS'].isin(meses_canal_rec)] if meses_canal_rec else hist_canal
        else:
            hist_canal_rec = hist_canal
        taxa_canal_rec, dias_canal_rec, _ = _calc_rate_with_support(hist_canal_rec)

        vol_canal_rec = float(hist_canal_rec['qtd'].sum()) if (hist_canal_rec is not None and not hist_canal_rec.empty) else 0.0
        if hist_canal_rec is not None and not hist_canal_rec.empty:
            meses_canal_rec_n = int(pd.to_datetime(hist_canal_rec['MES_REF_TS'], errors='coerce').dropna().nunique())
        else:
            meses_canal_rec_n = 0
        alpha_vol = (vol_canal_rec / (vol_canal_rec + 2200.0)) if vol_canal_rec > 0 else 0.0
        alpha_meses = min(1.0, meses_canal_rec_n / 6.0)

        ratios_validos: list[float] = []
        for wd in range(7):
            tc = float(taxa_canal_rec.get(wd, 0.0))
            tg = float(taxa_global_rec.get(wd, taxas_global_ref.get(wd, media_global_ref)))
            sup_wd = float(dias_canal_rec.get(wd, 0.0))
            if tc > 0 and tg > 0 and sup_wd >= 2.0:
                ratios_validos.append(tc / tg)
        if ratios_validos:
            arr_ratio = np.array(ratios_validos, dtype=float)
            media_ratio = float(np.mean(arr_ratio))
            cv_ratio = float(np.std(arr_ratio) / media_ratio) if media_ratio > 0 else 1.0
            estabilidade = float(np.clip(1.10 - (0.55 * cv_ratio), 0.35, 1.00))
        else:
            estabilidade = 0.55

        alpha_base = 0.10 + (0.60 * ((0.65 * alpha_vol) + (0.35 * alpha_meses)))
        alpha_canal = float(np.clip(alpha_base * estabilidade, 0.08, 0.62))

        for wd in range(7):
            tg = float(taxa_global_rec.get(wd, 0.0))
            if tg <= 0:
                tg = float(taxas_global_ref.get(wd, media_global_ref))
            tc = float(taxa_canal_rec.get(wd, 0.0))
            suporte_wd = float(dias_canal_rec.get(wd, 0.0))
            conf_wd = suporte_wd / (suporte_wd + 6.0) if suporte_wd > 0 else 0.0
            alpha_wd = float(np.clip(alpha_canal * conf_wd, 0.0, alpha_canal))
            base = tc if tc > 0 else tg
            taxas_out[wd] = float((alpha_wd * base) + ((1.0 - alpha_wd) * tg))

        weekday_vals = np.array([max(float(taxas_out.get(wd, 0.0)), 0.0) for wd in [0, 1, 2, 3, 4]], dtype=float)
        weekday_pos = weekday_vals[weekday_vals > 0]
        if weekday_pos.size > 0:
            media_weekday = float(np.mean(weekday_pos))
            for wd in [0, 1, 2, 3, 4]:
                taxa_wd = max(float(taxas_out.get(wd, media_weekday)), 0.0)
                taxas_out[wd] = float((0.70 * taxa_wd) + (0.30 * media_weekday))

            glob_wd = np.mean([
                max(float(taxa_global_rec.get(wd, taxas_global_ref.get(wd, media_global_ref))), 0.0)
                for wd in [0, 1, 2, 3, 4]
            ])
            glob_we = np.mean([
                max(float(taxa_global_rec.get(wd, taxas_global_ref.get(wd, media_global_ref))), 0.0)
                for wd in [5, 6]
            ])
            ratio_we = (glob_we / glob_wd) if glob_wd > 0 else 0.45
            ratio_cap = float(np.clip(ratio_we * 1.15, 0.30, 0.90))
            cap_we = media_weekday * ratio_cap
            for wd in [5, 6]:
                taxas_out[wd] = float(min(max(float(taxas_out.get(wd, cap_we)), 0.0), cap_we))

            media_miolo = float(np.mean([taxas_out.get(2, media_weekday), taxas_out.get(3, media_weekday), taxas_out.get(4, media_weekday)]))
            if media_miolo > 0:
                taxas_out[1] = float(min(float(taxas_out.get(1, media_miolo)), media_miolo * 1.08))

        arr = np.array([max(float(taxas_out.get(wd, 0.0)), 0.0) for wd in range(7)], dtype=float)
        pos = arr[arr > 0]
        if pos.size > 0:
            media_ref = float(np.mean(pos))
            piso = 0.40 * media_ref
            teto = 1.90 * media_ref
            for wd in range(7):
                taxas_out[wd] = float(np.clip(float(taxas_out.get(wd, media_ref)), piso, teto))

        if hist_canal_agg is not None and not hist_canal_agg.empty:
            total_q = float(pd.to_numeric(hist_canal_agg['qtd'], errors='coerce').fillna(0.0).sum())
            if total_q > 0:
                q_we = float(
                    pd.to_numeric(
                        hist_canal_agg.loc[hist_canal_agg['DIA_SEMANA'].isin([5, 6]), 'qtd'],
                        errors='coerce'
                    ).fillna(0.0).sum()
                )
                share_we = q_we / total_q
                if total_q >= 300.0 and share_we <= 0.01:
                    weekday_vals = [float(taxas_out.get(wd, 0.0)) for wd in [0, 1, 2, 3, 4] if float(taxas_out.get(wd, 0.0)) > 0]
                    if weekday_vals:
                        cap_we = float(np.mean(weekday_vals)) * 0.08
                        taxas_out[5] = min(float(taxas_out.get(5, cap_we)), cap_we)
                        taxas_out[6] = min(float(taxas_out.get(6, cap_we)), cap_we)

        return taxas_out

    def _taxa_canal_posterior(
        hist_canal_agg: pd.DataFrame,
        taxas_global_ref: dict[int, float],
        media_global_ref: float,
        decay: float,
        tau_prior: float,
        clip_low_mult: float,
        clip_high_mult: float
    ) -> dict[int, float]:
        taxas = taxas_global_ref.copy()
        if hist_canal_agg.empty:
            return taxas

        for wd in range(7):
            wd_stats = hist_canal_agg[hist_canal_agg['DIA_SEMANA'] == wd].sort_values('MES_REF_TS')
            if wd_stats.empty:
                continue
            taxas_wd = wd_stats['taxa'].to_numpy(dtype=float)
            taxa_recente = _media_ponderada_robusta(taxas_wd, decay=decay)
            ocorrencias = float(wd_stats['dias'].sum())
            taxa_prior = float(taxas_global_ref.get(wd, media_global_ref))
            tau_efetivo = float(tau_prior) * (1.6 if wd in {5, 6} else 1.0)
            denominador = ocorrencias + tau_efetivo
            taxa_post = (
                ((ocorrencias * taxa_recente) + (tau_efetivo * taxa_prior)) / denominador
                if denominador > 0 else taxa_prior
            )
            taxas[wd] = max(float(taxa_post), 0.0)

        arr = np.array([float(taxas.get(wd, 0.0)) for wd in range(7)], dtype=float)
        positivos = arr[arr > 0]
        if positivos.size > 0:
            media_ref = float(np.mean(positivos))
            piso = max(0.05, clip_low_mult * media_ref)
            teto = max(piso, clip_high_mult * media_ref)
            for wd in range(7):
                taxas[wd] = float(np.clip(taxas.get(wd, media_ref), piso, teto))

        taxa_weekday = [float(taxas.get(wd, 0.0)) for wd in [0, 1, 2, 3, 4] if float(taxas.get(wd, 0.0)) > 0]
        taxa_weekend_global = [float(taxas_global_ref.get(wd, media_global_ref)) for wd in [5, 6] if float(taxas_global_ref.get(wd, media_global_ref)) > 0]
        taxa_weekday_global = [float(taxas_global_ref.get(wd, media_global_ref)) for wd in [0, 1, 2, 3, 4] if float(taxas_global_ref.get(wd, media_global_ref)) > 0]
        if taxa_weekday:
            media_weekday = float(np.mean(taxa_weekday))
            if media_weekday > 0:
                if taxa_weekend_global and taxa_weekday_global and float(np.mean(taxa_weekday_global)) > 0:
                    ratio_global = float(np.mean(taxa_weekend_global)) / float(np.mean(taxa_weekday_global))
                else:
                    ratio_global = 0.45
                ratio_cap = float(np.clip(ratio_global * 1.35, 0.35, 1.05))
                cap_weekend = media_weekday * ratio_cap
                for wd in [5, 6]:
                    taxas[wd] = float(min(taxas.get(wd, cap_weekend), cap_weekend))

        taxa_weekday = [float(taxas.get(wd, 0.0)) for wd in [0, 1, 2, 3, 4] if float(taxas.get(wd, 0.0)) > 0]
        taxa_weekday_global = [float(taxas_global_ref.get(wd, media_global_ref)) for wd in [0, 1, 2, 3, 4] if float(taxas_global_ref.get(wd, media_global_ref)) > 0]
        if taxa_weekday and taxa_weekday_global:
            media_weekday = float(np.mean(taxa_weekday))
            media_weekday_global = float(np.mean(taxa_weekday_global))
            if media_weekday > 0 and media_weekday_global > 0:
                boost_midweek = {2: 1.08, 3: 1.12, 4: 1.06}
                piso_ratio_min = {2: 0.92, 3: 0.96, 4: 0.90}
                for wd in [2, 3, 4]:
                    ratio_global_dia = float(taxas_global_ref.get(wd, media_weekday_global)) / media_weekday_global
                    ratio_ref = max(ratio_global_dia * boost_midweek[wd], piso_ratio_min[wd])
                    piso_dia = media_weekday * ratio_ref * 0.90
                    taxas[wd] = float(max(taxas.get(wd, piso_dia), piso_dia))

                taxas_miolo = [float(taxas.get(wd, 0.0)) for wd in [2, 3, 4] if float(taxas.get(wd, 0.0)) > 0]
                if taxas_miolo and float(taxas.get(1, 0.0)) > 0:
                    media_miolo = float(np.mean(taxas_miolo))
                    ratio_global_ter = float(taxas_global_ref.get(1, media_weekday_global)) / media_weekday_global
                    fator_cap_ter = float(np.clip(ratio_global_ter * 1.08, 0.95, 1.18))
                    cap_ter = media_miolo * fator_cap_ter
                    taxas[1] = float(min(taxas.get(1, cap_ter), cap_ter))

        if hist_canal_agg is not None and not hist_canal_agg.empty:
            total_q = float(pd.to_numeric(hist_canal_agg['qtd'], errors='coerce').fillna(0.0).sum())
            if total_q > 0:
                q_we = float(
                    pd.to_numeric(
                        hist_canal_agg.loc[hist_canal_agg['DIA_SEMANA'].isin([5, 6]), 'qtd'],
                        errors='coerce'
                    ).fillna(0.0).sum()
                )
                share_we = q_we / total_q
                if total_q >= 300.0 and share_we <= 0.01:
                    weekday_vals = [float(taxas.get(wd, 0.0)) for wd in [0, 1, 2, 3, 4] if float(taxas.get(wd, 0.0)) > 0]
                    if weekday_vals:
                        cap_we = float(np.mean(weekday_vals)) * 0.10
                        taxas[5] = min(float(taxas.get(5, cap_we)), cap_we)
                        taxas[6] = min(float(taxas.get(6, cap_we)), cap_we)
        return taxas

    def _calibrar_parametros_modelo(df_agg_hist: pd.DataFrame) -> dict[str, float]:
        padrao = {
            'decay': 0.88,
            'tau_prior': 20.0,
            'clip_low_mult': 0.25,
            'clip_high_mult': 2.20,
            'blend_alpha': 0.55,
            'vol_k': 900.0,
            'wape_cv': np.nan,
            'wape_global': np.nan,
            'model_mode': 'padrao'
        }
        if df_agg_hist is None or df_agg_hist.empty:
            return padrao

        meses = sorted(pd.to_datetime(df_agg_hist['MES_REF_TS'].dropna().unique(), errors='coerce'))
        meses = [m for m in meses if pd.notna(m)]
        if len(meses) < 6:
            return padrao

        min_treino = 4
        meses_valid = meses[min_treino:]
        if len(meses_valid) > 6:
            meses_valid = meses_valid[-6:]
        if not meses_valid:
            return padrao

        def _avaliar_parametros(params_eval: dict[str, float]) -> float:
            wapes_mes: list[float] = []
            pesos_mes: list[float] = []

            for mes_val in meses_valid:
                erro_mes_abs = 0.0
                base_mes_abs = 0.0
                train = df_agg_hist[df_agg_hist['MES_REF_TS'] < mes_val]
                valid = df_agg_hist[df_agg_hist['MES_REF_TS'] == mes_val]
                if train.empty or valid.empty:
                    continue

                taxa_global_cv, media_global_cv = _taxa_global(train)
                contagem_mes = _contar_weekday_mes(mes_val)
                score_global_cv = _score_semana_from_taxa(taxa_global_cv, contagem_mes)
                if float(score_global_cv.sum()) <= 0:
                    continue

                totais_valid = (
                    valid.groupby('CANAL_PLAN', observed=True)['qtd']
                    .sum()
                    .astype(float)
                )
                if totais_valid.empty:
                    continue

                totais_valid_ord = totais_valid.sort_values(ascending=False)
                total_mes_valid = float(totais_valid_ord.sum())
                if total_mes_valid <= 0:
                    continue

                share_acum = totais_valid_ord.cumsum() / total_mes_valid
                canais_relevantes = set(share_acum[share_acum <= 0.94].index.tolist())
                if len(canais_relevantes) < 3:
                    canais_relevantes = set(
                        totais_valid_ord.head(min(8, len(totais_valid_ord))).index.tolist()
                    )

                n_canais_mes = int(len(totais_valid_ord))
                if n_canais_mes >= 10:
                    perc_corte = 35
                    piso_corte = 90.0
                elif n_canais_mes >= 6:
                    perc_corte = 30
                    piso_corte = 70.0
                else:
                    perc_corte = 20
                    piso_corte = 45.0
                try:
                    min_real_cv = float(np.nanpercentile(totais_valid_ord.to_numpy(dtype=float), perc_corte))
                except Exception:
                    min_real_cv = piso_corte
                min_real_cv = max(piso_corte, min_real_cv)

                for canal, valid_canal in valid.groupby('CANAL_PLAN', observed=True):
                    if canal not in canais_relevantes:
                        continue

                    total_real = float(valid_canal['qtd'].sum())
                    if total_real <= 0 or total_real < min_real_cv:
                        continue

                    hist_canal_train = train[train['CANAL_PLAN'] == canal]
                    model_mode_eval = str(params_eval.get('model_mode', 'hibrido_calibrado'))
                    if model_mode_eval == 'fallback_robusto':
                        taxa_canal_cv = _taxa_fallback_robusta(
                            hist_canal_train,
                            train,
                            taxa_global_cv,
                            media_global_cv,
                            meses_recencia_global=4,
                            meses_recencia_canal=2
                        )
                        score_mix = _score_semana_from_taxa(taxa_canal_cv, contagem_mes)
                    else:
                        taxa_canal_cv = _taxa_canal_posterior(
                            hist_canal_train,
                            taxa_global_cv,
                            media_global_cv,
                            params_eval['decay'],
                            params_eval['tau_prior'],
                            params_eval['clip_low_mult'],
                            params_eval['clip_high_mult']
                        )
                        score_canal_cv = _score_semana_from_taxa(taxa_canal_cv, contagem_mes)
                        volume_hist = float(hist_canal_train['qtd'].sum())
                        score_mix = _combinar_score_canal_global(
                            score_canal_cv,
                            score_global_cv,
                            volume_hist,
                            params_eval['blend_alpha'],
                            params_eval['vol_k']
                        )

                    soma_score = float(score_mix.sum())
                    if soma_score <= 0:
                        continue

                    real_por_dia = np.zeros(7, dtype=float)
                    for _, row_val in valid_canal.iterrows():
                        wd_val = int(row_val['DIA_SEMANA'])
                        if 0 <= wd_val <= 6:
                            real_por_dia[wd_val] += float(row_val['qtd'])

                    previsto = (score_mix / soma_score) * total_real
                    erro_canal = float(np.abs(previsto - real_por_dia).sum())
                    erro_mes_abs += float(min(erro_canal, total_real * 1.25))
                    base_mes_abs += float(real_por_dia.sum())

                if base_mes_abs > 0:
                    wape_mes = float(erro_mes_abs / base_mes_abs)
                    peso_mes = float(np.sqrt(base_mes_abs))
                    wapes_mes.append(wape_mes)
                    pesos_mes.append(max(peso_mes, 1.0))

            if not wapes_mes:
                return np.inf
            return float(np.average(np.array(wapes_mes, dtype=float), weights=np.array(pesos_mes, dtype=float)))

        grid = [
            {'decay': 0.84, 'tau_prior': 20.0, 'clip_low_mult': 0.24, 'clip_high_mult': 2.40, 'blend_alpha': 0.20, 'vol_k': 1600.0},
            {'decay': 0.90, 'tau_prior': 24.0, 'clip_low_mult': 0.24, 'clip_high_mult': 2.30, 'blend_alpha': 0.20, 'vol_k': 1800.0},
            {'decay': 0.94, 'tau_prior': 28.0, 'clip_low_mult': 0.26, 'clip_high_mult': 2.20, 'blend_alpha': 0.20, 'vol_k': 2000.0},
            {'decay': 0.84, 'tau_prior': 20.0, 'clip_low_mult': 0.23, 'clip_high_mult': 2.50, 'blend_alpha': 0.30, 'vol_k': 1400.0},
            {'decay': 0.90, 'tau_prior': 28.0, 'clip_low_mult': 0.25, 'clip_high_mult': 2.35, 'blend_alpha': 0.30, 'vol_k': 1600.0},
            {'decay': 0.94, 'tau_prior': 32.0, 'clip_low_mult': 0.28, 'clip_high_mult': 2.25, 'blend_alpha': 0.30, 'vol_k': 1800.0},
            {'decay': 0.84, 'tau_prior': 16.0, 'clip_low_mult': 0.22, 'clip_high_mult': 2.60, 'blend_alpha': 0.45, 'vol_k': 800.0},
            {'decay': 0.84, 'tau_prior': 24.0, 'clip_low_mult': 0.24, 'clip_high_mult': 2.40, 'blend_alpha': 0.45, 'vol_k': 1000.0},
            {'decay': 0.90, 'tau_prior': 20.0, 'clip_low_mult': 0.22, 'clip_high_mult': 2.60, 'blend_alpha': 0.45, 'vol_k': 900.0},
            {'decay': 0.90, 'tau_prior': 28.0, 'clip_low_mult': 0.25, 'clip_high_mult': 2.40, 'blend_alpha': 0.45, 'vol_k': 1200.0},
            {'decay': 0.94, 'tau_prior': 24.0, 'clip_low_mult': 0.24, 'clip_high_mult': 2.40, 'blend_alpha': 0.45, 'vol_k': 1200.0},
            {'decay': 0.94, 'tau_prior': 36.0, 'clip_low_mult': 0.28, 'clip_high_mult': 2.20, 'blend_alpha': 0.45, 'vol_k': 1500.0},
            {'decay': 0.84, 'tau_prior': 20.0, 'clip_low_mult': 0.22, 'clip_high_mult': 2.60, 'blend_alpha': 0.60, 'vol_k': 900.0},
            {'decay': 0.84, 'tau_prior': 28.0, 'clip_low_mult': 0.25, 'clip_high_mult': 2.40, 'blend_alpha': 0.60, 'vol_k': 1200.0},
            {'decay': 0.90, 'tau_prior': 24.0, 'clip_low_mult': 0.24, 'clip_high_mult': 2.50, 'blend_alpha': 0.60, 'vol_k': 1000.0},
            {'decay': 0.90, 'tau_prior': 32.0, 'clip_low_mult': 0.27, 'clip_high_mult': 2.30, 'blend_alpha': 0.60, 'vol_k': 1400.0},
            {'decay': 0.94, 'tau_prior': 28.0, 'clip_low_mult': 0.26, 'clip_high_mult': 2.30, 'blend_alpha': 0.60, 'vol_k': 1400.0},
            {'decay': 0.94, 'tau_prior': 36.0, 'clip_low_mult': 0.30, 'clip_high_mult': 2.20, 'blend_alpha': 0.60, 'vol_k': 1800.0},
        ]

        melhor = padrao.copy()
        melhor_wape = np.inf
        for params in grid:
            wape = _avaliar_parametros(params)
            if np.isfinite(wape) and wape < melhor_wape:
                melhor_wape = wape
                melhor = {
                    'decay': float(params['decay']),
                    'tau_prior': float(params['tau_prior']),
                    'clip_low_mult': float(params['clip_low_mult']),
                    'clip_high_mult': float(params['clip_high_mult']),
                    'blend_alpha': float(params['blend_alpha']),
                    'vol_k': float(params['vol_k']),
                    'wape_cv': float(wape),
                    'wape_global': np.nan,
                    'model_mode': 'hibrido_calibrado'
                }

        params_global = {
            'decay': padrao['decay'],
            'tau_prior': padrao['tau_prior'],
            'clip_low_mult': padrao['clip_low_mult'],
            'clip_high_mult': padrao['clip_high_mult'],
            'blend_alpha': 0.0,
            'vol_k': 1200.0,
            'model_mode': 'fallback_global'
        }
        params_fallback_robusto = {
            'decay': 0.90,
            'tau_prior': 28.0,
            'clip_low_mult': 0.25,
            'clip_high_mult': 2.30,
            'blend_alpha': 0.0,
            'vol_k': 1800.0,
            'model_mode': 'fallback_robusto'
        }
        wape_global = _avaliar_parametros(params_global)
        wape_fallback_robusto = _avaliar_parametros(params_fallback_robusto)

        if np.isfinite(wape_global):
            if (not np.isfinite(melhor_wape)) or (melhor_wape > (wape_global * 1.05)):
                melhor = {
                    'decay': params_global['decay'],
                    'tau_prior': params_global['tau_prior'],
                    'clip_low_mult': params_global['clip_low_mult'],
                    'clip_high_mult': params_global['clip_high_mult'],
                    'blend_alpha': params_global['blend_alpha'],
                    'vol_k': params_global['vol_k'],
                    'wape_cv': float(wape_global),
                    'wape_global': float(wape_global),
                    'model_mode': 'fallback_global'
                }
            else:
                melhor['wape_global'] = float(wape_global)

        if np.isfinite(wape_fallback_robusto):
            wape_atual = float(melhor.get('wape_cv', np.inf))
            if wape_fallback_robusto < (wape_atual * 0.98):
                melhor = {
                    'decay': params_fallback_robusto['decay'],
                    'tau_prior': params_fallback_robusto['tau_prior'],
                    'clip_low_mult': params_fallback_robusto['clip_low_mult'],
                    'clip_high_mult': params_fallback_robusto['clip_high_mult'],
                    'blend_alpha': params_fallback_robusto['blend_alpha'],
                    'vol_k': params_fallback_robusto['vol_k'],
                    'wape_cv': float(wape_fallback_robusto),
                    'wape_global': float(wape_global) if np.isfinite(wape_global) else np.nan,
                    'model_mode': 'fallback_robusto'
                }

        if np.isfinite(float(melhor.get('wape_cv', np.nan))) and float(melhor.get('wape_cv', np.nan)) > 1.20:
            if str(melhor.get('model_mode', '')) not in {'fallback_robusto', 'fallback_global'}:
                melhor['blend_alpha'] = min(float(melhor.get('blend_alpha', 0.55)), 0.35)
                melhor['vol_k'] = max(float(melhor.get('vol_k', 900.0)), 1400.0)
                melhor['model_mode'] = 'calibrado_conservador'

        for chave, valor_padrao in padrao.items():
            if chave not in melhor:
                melhor[chave] = valor_padrao

        return melhor

    params_modelo = _calibrar_parametros_modelo(agg_mes_dia)
    decay_modelo = float(params_modelo.get('decay', 0.88))
    tau_prior_modelo = float(params_modelo.get('tau_prior', 20.0))
    clip_low_modelo = float(params_modelo.get('clip_low_mult', 0.25))
    clip_high_modelo = float(params_modelo.get('clip_high_mult', 2.20))
    blend_alpha_modelo = float(params_modelo.get('blend_alpha', 0.55))
    vol_k_modelo = float(params_modelo.get('vol_k', 900.0))
    wape_modelo = float(params_modelo.get('wape_cv', np.nan)) if pd.notna(params_modelo.get('wape_cv', np.nan)) else np.nan
    mode_modelo = str(params_modelo.get('model_mode', 'hibrido_calibrado'))
    usar_fallback_robusto = (mode_modelo == 'fallback_robusto')
    if usar_fallback_robusto:
        params_modelo['model_mode'] = 'fallback_robusto'
        params_modelo['blend_alpha'] = 0.0
        params_modelo['vol_k'] = max(vol_k_modelo, 2000.0)
    elif pd.notna(wape_modelo) and (wape_modelo > 1.20):
        params_modelo['model_mode'] = 'calibrado_super_conservador'
        params_modelo['blend_alpha'] = min(blend_alpha_modelo, 0.20)
        params_modelo['vol_k'] = max(vol_k_modelo, 1800.0)

    blend_alpha_modelo = float(params_modelo.get('blend_alpha', blend_alpha_modelo))
    vol_k_modelo = float(params_modelo.get('vol_k', vol_k_modelo))

    taxa_global, taxa_media_global = _taxa_global(agg_mes_dia)

    canais_real = set(df_real['CANAL_PLAN'].dropna().unique().tolist())
    canais_meta = set(df_meta['CANAL_PLAN'].dropna().unique().tolist())
    canais = sorted(canais_real | canais_meta)
    rows = []
    for canal in canais:
        real_anterior = float(df_mes_anterior_real.loc[df_mes_anterior_real['CANAL_PLAN'] == canal, 'QTDE'].sum())
        real_atual = float(df_mes_atual_real.loc[df_mes_atual_real['CANAL_PLAN'] == canal, 'QTDE'].sum())
        meta_mes = float(df_mes_atual_meta.loc[df_mes_atual_meta['CANAL_PLAN'] == canal, 'DESAFIO_QTD'].sum())

        var_meta_real = meta_mes - real_atual
        faltante = max(var_meta_real, 0.0)

        dias_validos = [wd for wd in range(7) if dias_restantes.get(wd, 0) > 0]
        hist_canal = agg_mes_dia[agg_mes_dia['CANAL_PLAN'] == canal]
        if usar_fallback_robusto:
            taxa_canal = _taxa_fallback_robusta(
                hist_canal,
                agg_mes_dia,
                taxa_global,
                taxa_media_global,
                meses_recencia_global=4,
                meses_recencia_canal=2
            )
        else:
            taxa_canal = _taxa_canal_posterior(
                hist_canal,
                taxa_global,
                taxa_media_global,
                decay_modelo,
                tau_prior_modelo,
                clip_low_modelo,
                clip_high_modelo
            )

        participacao: dict[int, float] = {wd: 0.0 for wd in range(7)}
        if dias_validos:
            score_canal = _score_semana_from_taxa(taxa_canal, dias_restantes)
            if usar_fallback_robusto:
                score_mix = score_canal
            else:
                score_global = _score_semana_from_taxa(taxa_global, dias_restantes)
                volume_hist_canal = float(hist_canal['qtd'].sum()) if not hist_canal.empty else 0.0
                score_mix = _combinar_score_canal_global(
                    score_canal,
                    score_global,
                    volume_hist_canal,
                    blend_alpha_modelo,
                    vol_k_modelo
                )
            peso_distrib = {
                wd: float(score_mix[wd]) if 0 <= wd < len(score_mix) else 0.0
                for wd in dias_validos
            }
            soma_distrib = float(sum(peso_distrib.values()))
            if soma_distrib > 0:
                for wd in dias_validos:
                    participacao[wd] = float(peso_distrib.get(wd, 0.0)) / soma_distrib
            else:
                total_dias_rest = float(sum(dias_restantes.get(wd, 0) for wd in dias_validos))
                if total_dias_rest > 0:
                    for wd in dias_validos:
                        participacao[wd] = float(dias_restantes.get(wd, 0)) / total_dias_rest

        metas_dia = {wd: 0.0 for wd in range(7)}
        faltante_int = int(np.round(faltante))
        if faltante_int > 0 and dias_validos:
            aloc_float = {wd: faltante_int * participacao.get(wd, 0.0) for wd in dias_validos}
            aloc_base = {wd: int(np.floor(v)) for wd, v in aloc_float.items()}
            resto = faltante_int - int(sum(aloc_base.values()))

            if resto > 0:
                ordem_up = sorted(
                    dias_validos,
                    key=lambda wd: ((aloc_float[wd] - aloc_base[wd]), dias_restantes.get(wd, 0)),
                    reverse=True
                )
                for wd in ordem_up[:resto]:
                    aloc_base[wd] += 1
            elif resto < 0:
                ordem_down = sorted(
                    dias_validos,
                    key=lambda wd: (aloc_float[wd] - aloc_base[wd])
                )
                for wd in ordem_down:
                    if resto == 0:
                        break
                    if aloc_base[wd] > 0:
                        aloc_base[wd] -= 1
                        resto += 1

            for wd in dias_validos:
                qtd_dias = int(dias_restantes.get(wd, 0))
                total_dia_semana = float(aloc_base.get(wd, 0))
                metas_dia[wd] = (total_dia_semana / qtd_dias) if qtd_dias > 0 else 0.0

        rows.append({
            'CANAL': canal,
            'MES_ANTERIOR': real_anterior,
            'MES_ATUAL_REAL': real_atual,
            'META_MES': meta_mes,
            'VAR_META_X_REAL': var_meta_real,
            'SEG': metas_dia[0],
            'TER': metas_dia[1],
            'QUA': metas_dia[2],
            'QUI': metas_dia[3],
            'SEX': metas_dia[4],
            'SAB': metas_dia[5],
            'DOM': metas_dia[6]
        })

    df_out = pd.DataFrame(rows, columns=colunas_saida)
    if not df_out.empty:
        df_out = df_out.sort_values(['VAR_META_X_REAL', 'CANAL'], ascending=[False, True]).reset_index(drop=True)

    return df_out, {
        'mes_anterior': mes_anterior_ref,
        'dias_restantes': dias_restantes,
        'data_corte': data_corte,
        'calibracao_modelo': params_modelo
    }

def _normalizar_texto_chave_analitico(valor) -> str:
    if pd.isna(valor):
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = texto.encode("ASCII", "ignore").decode("ASCII")
    texto = texto.strip().upper()
    texto = re.sub(r"[^A-Z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()

def _montar_calendario_semana_mes_analitico(mes_ref: str) -> pd.DataFrame:
    cols = ['DATA_DIA', 'SEMANA_IDX', 'SEMANA_STD', 'DIA_SEMANA', 'DIA_ROTULO', 'DIA_ORDEM_REF']
    try:
        dt_inicio = pd.Timestamp(mes_ano_para_data(mes_ref)).normalize()
    except Exception:
        return pd.DataFrame(columns=cols)

    dt_fim = (dt_inicio + pd.offsets.MonthEnd(0)).normalize()
    datas = pd.date_range(start=dt_inicio, end=dt_fim, freq='D')
    mapa_weekday = {0: 'seg', 1: 'ter', 2: 'qua', 3: 'qui', 4: 'sex', 5: 'sab', 6: 'dom'}
    dia_inicio_mes = int(dt_inicio.weekday())
    df_cal = pd.DataFrame({'DATA_DIA': datas})
    df_cal['DIA_SEMANA'] = df_cal['DATA_DIA'].dt.weekday.astype(int)
    df_cal['DIA_ROTULO'] = df_cal['DIA_SEMANA'].map(mapa_weekday)
    df_cal['DIA_ORDEM_REF'] = ((df_cal['DIA_SEMANA'] - dia_inicio_mes) % 7).astype(int)
    df_cal['SEMANA_IDX'] = ((df_cal['DATA_DIA'].dt.day - 1) // 7) + 1
    df_cal['SEMANA_STD'] = pd.to_numeric(df_cal['SEMANA_IDX'], errors='coerce').fillna(0).astype(int)
    df_cal = df_cal.sort_values(['SEMANA_IDX', 'DIA_ORDEM_REF']).reset_index(drop=True)
    return df_cal[cols]

def _alocar_inteiros_por_peso(total_valor: float, pesos: list[float]) -> list[int]:
    if not pesos:
        return []
    total_int = int(np.round(max(float(total_valor or 0.0), 0.0)))
    if total_int <= 0:
        return [0] * len(pesos)

    arr_pesos = np.asarray(pesos, dtype=float)
    arr_pesos[~np.isfinite(arr_pesos)] = 0.0
    arr_pesos = np.clip(arr_pesos, 0.0, None)
    soma_pesos = float(arr_pesos.sum())
    if soma_pesos <= 0:
        arr_pesos = np.ones(len(pesos), dtype=float)
        soma_pesos = float(arr_pesos.sum())

    aloc_float = (arr_pesos / soma_pesos) * float(total_int)
    aloc_base = np.floor(aloc_float).astype(int)
    resto = int(total_int - int(aloc_base.sum()))
    if resto > 0:
        frac = (aloc_float - aloc_base).tolist()
        ordem_up = sorted(range(len(frac)), key=lambda i: frac[i], reverse=True)
        for idx in ordem_up[:resto]:
            aloc_base[idx] += 1
    elif resto < 0:
        frac = (aloc_float - aloc_base).tolist()
        ordem_down = sorted(range(len(frac)), key=lambda i: frac[i])
        for idx in ordem_down:
            if resto == 0:
                break
            if aloc_base[idx] > 0:
                aloc_base[idx] -= 1
                resto += 1
    return [int(v) for v in aloc_base.tolist()]

def _formatar_mes_ano_pt_br(data_ref) -> str:
    try:
        ts = pd.Timestamp(data_ref)
        if pd.isna(ts):
            return ""
        meses_pt = {
            1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
            7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
        }
        return f"{meses_pt.get(int(ts.month), 'jan')}/{ts.strftime('%y')}"
    except Exception:
        return ""

def _montar_contexto_pesos_projecao_semana_dia(
    df_hist: pd.DataFrame,
    mes_ref: str,
    valor_col: str = 'QTDE'
) -> dict:
    serie_vazia = pd.Series(dtype='float64')
    taxas_default = {wd: 1.0 for wd in range(7)}
    ctx_default = {
        'mes_ref_5s': '',
        'mes_ref_m1': '',
        'lookup_5s': serie_vazia,
        'lookup_m1': serie_vazia,
        'wd_media_5s': serie_vazia,
        'wd_media_m1': serie_vazia,
        'taxas_bayes': taxas_default
    }
    if df_hist is None or df_hist.empty:
        return ctx_default

    try:
        dt_mes_ref = pd.Timestamp(mes_ano_para_data(str(mes_ref))).normalize()
    except Exception:
        return ctx_default

    df_tmp = df_hist.copy()
    col_data = next((c for c in ['DATA_DIA', 'DATA', 'DAT_MOVIMENTO2'] if c in df_tmp.columns), None)
    if col_data is None:
        return ctx_default

    df_tmp['DATA_DIA_REF'] = pd.to_datetime(df_tmp[col_data], errors='coerce').dt.normalize()
    df_tmp = df_tmp[df_tmp['DATA_DIA_REF'].notna()].copy()
    if df_tmp.empty:
        return ctx_default

    if valor_col in df_tmp.columns:
        df_tmp['VALOR_REF'] = pd.to_numeric(df_tmp[valor_col], errors='coerce').fillna(0.0)
    else:
        df_tmp['VALOR_REF'] = pd.to_numeric(df_tmp.get('QTDE', 0), errors='coerce').fillna(0.0)

    col_mes = next((c for c in ['MES_NORM', 'dat_tratada', 'mes_ano'] if c in df_tmp.columns), None)
    if col_mes is not None:
        df_tmp['MES_REF_NORM'] = df_tmp[col_mes].astype(str).str.strip().str.lower()
    else:
        df_tmp['MES_REF_NORM'] = df_tmp['DATA_DIA_REF'].apply(_formatar_mes_ano_pt_br).astype(str).str.strip().str.lower()

    soma_mes_hist = (
        df_tmp.groupby('MES_REF_NORM', observed=True)['VALOR_REF'].sum()
        if not df_tmp.empty else pd.Series(dtype='float64')
    )
    meses_hist = []
    for mes_hist in df_tmp['MES_REF_NORM'].dropna().astype(str).str.strip().unique().tolist():
        if not mes_hist:
            continue
        try:
            dt_hist = pd.Timestamp(mes_ano_para_data(mes_hist)).normalize()
        except Exception:
            continue
        if dt_hist >= dt_mes_ref:
            continue
        if float(soma_mes_hist.get(mes_hist, 0.0) or 0.0) <= 0:
            continue
        meses_hist.append((dt_hist, mes_hist))

    if not meses_hist:
        return ctx_default

    meses_hist = [mes for _, mes in sorted(meses_hist, key=lambda x: x[0])]
    mes_ref_m1 = meses_hist[-1]
    mes_ref_5s = ''
    for mes_hist in reversed(meses_hist):
        cal_hist = _montar_calendario_semana_mes_analitico(mes_hist)
        if not cal_hist.empty and int(cal_hist['SEMANA_IDX'].max()) >= 5:
            mes_ref_5s = mes_hist
            break
    if not mes_ref_5s:
        mes_ref_5s = mes_ref_m1

    def _montar_lookup_mes(mes_lookup: str) -> tuple[pd.Series, pd.Series]:
        if not mes_lookup:
            return serie_vazia, serie_vazia
        cal_lookup = _montar_calendario_semana_mes_analitico(mes_lookup)
        if cal_lookup.empty:
            return serie_vazia, serie_vazia
        df_mes_lookup = df_tmp[df_tmp['MES_REF_NORM'].eq(str(mes_lookup).strip().lower())].copy()
        agg_lookup = (
            df_mes_lookup.groupby('DATA_DIA_REF', observed=True)['VALOR_REF'].sum()
            if not df_mes_lookup.empty else pd.Series(dtype='float64')
        )
        df_agg_lookup = agg_lookup.rename('VALOR_DIA').reset_index()
        if "DATA_DIA_REF" not in df_agg_lookup.columns and not df_agg_lookup.empty:
            primeira_coluna_lookup = df_agg_lookup.columns[0]
            df_agg_lookup = df_agg_lookup.rename(columns={primeira_coluna_lookup: 'DATA_DIA_REF'})
        elif df_agg_lookup.empty:
            df_agg_lookup = pd.DataFrame(columns=['DATA_DIA_REF', 'VALOR_DIA'])
        serie_lookup = cal_lookup[['DATA_DIA', 'SEMANA_IDX', 'DIA_SEMANA']].copy()
        serie_lookup = serie_lookup.merge(
            df_agg_lookup.rename(columns={'DATA_DIA_REF': 'DATA_DIA'}),
            on='DATA_DIA',
            how='left'
        )
        serie_lookup['VALOR_DIA'] = pd.to_numeric(
            serie_lookup.get('VALOR_DIA', 0),
            errors='coerce'
        ).fillna(0.0)
        lookup = serie_lookup.set_index(['SEMANA_IDX', 'DIA_SEMANA'])['VALOR_DIA']
        wd_media = serie_lookup.groupby('DIA_SEMANA', observed=True)['VALOR_DIA'].mean()
        return lookup, wd_media

    lookup_5s, wd_media_5s = _montar_lookup_mes(mes_ref_5s)
    lookup_m1, wd_media_m1 = _montar_lookup_mes(mes_ref_m1)

    df_bayes = df_tmp[['DATA_DIA_REF', 'VALOR_REF']].copy()
    df_bayes['DAT_MOVIMENTO2'] = df_bayes['DATA_DIA_REF']
    df_bayes['QTDE'] = df_bayes['VALOR_REF']
    taxas_bayes = _calcular_taxas_bayes_ultimos_3_meses(df_bayes[['DAT_MOVIMENTO2', 'QTDE']], mes_ref)

    return {
        'mes_ref_5s': mes_ref_5s,
        'mes_ref_m1': mes_ref_m1,
        'lookup_5s': lookup_5s,
        'lookup_m1': lookup_m1,
        'wd_media_5s': wd_media_5s,
        'wd_media_m1': wd_media_m1,
        'taxas_bayes': taxas_bayes
    }

def _obter_peso_projecao_semana_dia(ctx_peso: dict, semana_idx: int, dia_semana: int) -> float:
    semana_idx = int(semana_idx)
    dia_semana = int(dia_semana)
    for chave_lookup in ['lookup_5s', 'lookup_m1']:
        lookup = ctx_peso.get(chave_lookup, pd.Series(dtype='float64'))
        peso = lookup.get((semana_idx, dia_semana), np.nan)
        if pd.notna(peso) and float(peso) > 0:
            return float(peso)
    for chave_media in ['wd_media_5s', 'wd_media_m1']:
        wd_media = ctx_peso.get(chave_media, pd.Series(dtype='float64'))
        peso = wd_media.get(dia_semana, np.nan)
        if pd.notna(peso) and float(peso) > 0:
            return float(peso)
    taxas_bayes = ctx_peso.get('taxas_bayes', {})
    peso_bayes = float(taxas_bayes.get(dia_semana, 0.0) or 0.0)
    if peso_bayes > 0:
        return peso_bayes
    return 1.0

def _calcular_taxas_bayes_ultimos_3_meses(df_hist: pd.DataFrame, mes_ref: str) -> dict[int, float]:
    taxas_default = {wd: 1.0 for wd in range(7)}
    if df_hist is None or df_hist.empty:
        return taxas_default
    if 'DAT_MOVIMENTO2' not in df_hist.columns:
        return taxas_default

    df_tmp = df_hist.copy()
    df_tmp['DATA_DIA'] = pd.to_datetime(df_tmp.get('DAT_MOVIMENTO2'), errors='coerce').dt.normalize()
    df_tmp['QTDE'] = pd.to_numeric(df_tmp.get('QTDE', 0), errors='coerce').fillna(0.0)
    df_tmp = df_tmp[df_tmp['DATA_DIA'].notna()].copy()
    if df_tmp.empty:
        return taxas_default

    try:
        mes_ref_ts = pd.Timestamp(mes_ano_para_data(str(mes_ref))).normalize()
    except Exception:
        return taxas_default

    df_tmp['MES_REF_TS'] = df_tmp['DATA_DIA'].dt.to_period('M').dt.to_timestamp()
    df_tmp = df_tmp[df_tmp['MES_REF_TS'] < mes_ref_ts].copy()
    if df_tmp.empty:
        return taxas_default

    meses_hist = sorted(pd.to_datetime(df_tmp['MES_REF_TS'].dropna().unique(), errors='coerce'))
    meses_hist = [pd.Timestamp(m).normalize() for m in meses_hist if pd.notna(m)]
    if not meses_hist:
        return taxas_default
    if len(meses_hist) > 3:
        meses_hist = meses_hist[-3:]
    meses_set = {pd.Timestamp(m).normalize() for m in meses_hist}
    df_tmp = df_tmp[df_tmp['MES_REF_TS'].isin(meses_set)].copy()
    if df_tmp.empty:
        return taxas_default

    agg_diario = (
        df_tmp.groupby('DATA_DIA', observed=True)['QTDE']
        .sum()
        .astype(float)
    )
    linhas = []
    for mes_hist in meses_hist:
        inicio = pd.Timestamp(mes_hist).normalize()
        fim = (inicio + pd.offsets.MonthEnd(0)).normalize()
        for dt_ref in pd.date_range(start=inicio, end=fim, freq='D'):
            linhas.append({
                'DIA_SEMANA': int(dt_ref.weekday()),
                'QTDE': float(agg_diario.get(pd.Timestamp(dt_ref).normalize(), 0.0))
            })

    df_full = pd.DataFrame(linhas)
    if df_full.empty:
        return taxas_default

    stats = (
        df_full.groupby('DIA_SEMANA', observed=True)
        .agg(qtd=('QTDE', 'sum'), dias=('QTDE', 'count'))
    )
    qtd_total = float(pd.to_numeric(df_full['QTDE'], errors='coerce').fillna(0.0).sum())
    dias_total = float(len(df_full))
    media_global = (qtd_total / dias_total) if dias_total > 0 else 1.0
    if media_global <= 0:
        media_global = 1.0

    taxas_brutas: dict[int, float] = {}
    taxas_suavizadas: dict[int, float] = {}
    for wd in range(7):
        qtd_wd = float(stats['qtd'].get(wd, 0.0)) if 'qtd' in stats else 0.0
        dias_wd = float(stats['dias'].get(wd, 0.0)) if 'dias' in stats else 0.0
        taxa_bruta = (qtd_wd / dias_wd) if dias_wd > 0 else 0.0
        tau_prior = 5.0 if wd in {5, 6} else 3.0
        taxa_post = ((qtd_wd + (tau_prior * media_global)) / (dias_wd + tau_prior)) if (dias_wd + tau_prior) > 0 else media_global
        taxas_brutas[wd] = float(max(taxa_bruta, 0.0))
        taxas_suavizadas[wd] = float(max(taxa_post, 0.0))

    taxa_uteis = [float(taxas_suavizadas.get(wd, 0.0)) for wd in [0, 1, 2, 3, 4] if float(taxas_suavizadas.get(wd, 0.0)) > 0]
    taxa_uteis_bruta = [float(taxas_brutas.get(wd, 0.0)) for wd in [0, 1, 2, 3, 4] if float(taxas_brutas.get(wd, 0.0)) > 0]
    taxa_fds_bruta = [float(taxas_brutas.get(wd, 0.0)) for wd in [5, 6] if float(taxas_brutas.get(wd, 0.0)) > 0]
    if taxa_uteis and taxa_uteis_bruta:
        media_uteis = float(np.mean(taxa_uteis))
        media_uteis_bruta = float(np.mean(taxa_uteis_bruta))
        media_fds_bruta = float(np.mean(taxa_fds_bruta)) if taxa_fds_bruta else 0.0
        ratio_fds = (media_fds_bruta / media_uteis_bruta) if media_uteis_bruta > 0 else 0.45
        ratio_cap = float(np.clip(ratio_fds * 1.08, 0.10, 0.80))
        cap_fds = media_uteis * ratio_cap
        for wd in [5, 6]:
            taxas_suavizadas[wd] = float(min(float(taxas_suavizadas.get(wd, cap_fds)), cap_fds))

    arr = np.array([max(float(taxas_suavizadas.get(wd, 0.0)), 0.0) for wd in range(7)], dtype=float)
    if float(arr.sum()) <= 0:
        return taxas_default
    return {wd: float(arr[wd]) for wd in range(7)}

def criar_tabela_html_necessidade_diaria_produto(
    df_base: pd.DataFrame,
    mes_ref: str,
    regional_ref: str,
    canal_ref: str,
    produto_ref: str,
    table_id: str,
    base_preparada: bool = False,
    incluir_ctx: bool = False
) -> tuple[str, dict]:
    ctx_default = {
        'mes_anterior': get_mes_anterior(mes_ref),
        'dias_restantes': {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0},
        'data_corte': None
    }
    if df_base is None or df_base.empty:
        return "", (ctx_default if incluir_ctx else {})

    if base_preparada:
        df_work = df_base
        if 'COD_PLATAFORMA' not in df_work.columns or 'IND_NORM' not in df_work.columns:
            return "", (ctx_default if incluir_ctx else {})
    else:
        colunas_necessarias = [
            col for col in [
                'CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'REGIONAL', 'dat_tratada',
                'QTDE', 'DESAFIO_QTD', 'TEND_QTD', 'DAT_MOVIMENTO2'
            ]
            if col in df_base.columns
        ]
        df_work = df_base[colunas_necessarias].copy()
        for col in ['CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'REGIONAL', 'dat_tratada']:
            if col in df_work.columns:
                df_work[col] = df_work[col].astype(str).str.strip()
        if 'COD_PLATAFORMA' not in df_work.columns or 'DSC_INDICADOR' not in df_work.columns:
            return "", (ctx_default if incluir_ctx else {})

        if 'DESAFIO_QTD' not in df_work.columns:
            df_work['DESAFIO_QTD'] = 0
        if 'TEND_QTD' not in df_work.columns:
            df_work['TEND_QTD'] = df_work.get('QTDE', 0)
        df_work['QTDE'] = normalizar_numerico_serie(df_work.get('QTDE', 0)).fillna(0.0)
        df_work['DESAFIO_QTD'] = normalizar_numerico_serie(df_work.get('DESAFIO_QTD', 0)).fillna(0.0)
        df_work['TEND_QTD'] = normalizar_numerico_serie(df_work.get('TEND_QTD', 0)).fillna(0.0)
        df_work['DAT_MOVIMENTO2'] = pd.to_datetime(df_work.get('DAT_MOVIMENTO2'), errors='coerce')
        df_work['DATA_DIA'] = pd.to_datetime(df_work['DAT_MOVIMENTO2'], errors='coerce').dt.normalize()
        df_work['COD_PLATAFORMA'] = df_work['COD_PLATAFORMA'].apply(normalizar_rotulo_produto)
        df_work['IND_NORM'] = df_work['DSC_INDICADOR'].apply(_normalizar_texto_chave_analitico)
        df_work['MES_NORM'] = df_work['dat_tratada'].astype(str).str.strip().str.lower()

    produto_norm = normalizar_rotulo_produto(produto_ref)
    df_work = df_work[df_work['COD_PLATAFORMA'] == produto_norm].copy()
    if regional_ref and str(regional_ref).strip() != "Todas":
        df_work = df_work[df_work['REGIONAL'] == regional_ref].copy()
    if canal_ref and str(canal_ref).strip() != "Todos":
        df_work = df_work[df_work['CANAL_PLAN'] == canal_ref].copy()
    if df_work.empty:
        return "", (ctx_default if incluir_ctx else {})

    cal = _montar_calendario_semana_mes_analitico(mes_ref)
    if cal.empty:
        return "", (ctx_default if incluir_ctx else {})

    mes_ref_norm = str(mes_ref).strip().lower()
    mes_m1 = get_mes_anterior(mes_ref)
    mes_m1_norm = str(mes_m1).strip().lower()
    eh_mes_atual = mes_ref_norm == get_mes_atual_formatado().strip().lower()
    try:
        hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    except Exception:
        hoje = date.today()

    alias_vb = {
        _normalizar_texto_chave_analitico('VENDA BRUTA'),
        _normalizar_texto_chave_analitico('VENDAS BRUTAS'),
        _normalizar_texto_chave_analitico('GROSS BRUTO')
    }
    alias_ativ = (
        {_normalizar_texto_chave_analitico('GROSS LIQUIDO')}
        if produto_norm == 'CONTA'
        else {
            _normalizar_texto_chave_analitico('INSTALACAO'),
            _normalizar_texto_chave_analitico('INSTALADOS'),
            _normalizar_texto_chave_analitico('INSTAL')
        }
    )
    alias_meta_ativ = {_normalizar_texto_chave_analitico('GROSS LIQUIDO')}

    metricas_cfg = [
        {
            'nome': 'Venda Bruta',
            'aliases_real': alias_vb,
            'aliases_meta': alias_vb,
            'fallback_meta_vb': True
        },
        {
            'nome': 'Ativados',
            'aliases_real': alias_ativ,
            'aliases_meta': alias_meta_ativ,
            'fallback_meta_vb': False
        }
    ]

    def _serie_diaria_para_merge(serie: pd.Series, valor_col: str = "VALOR_DIA") -> pd.DataFrame:
        """Garante DataFrame diário com chave DATA_DIA mesmo quando a série está vazia."""
        if serie is None or len(serie) == 0:
            return pd.DataFrame(columns=["DATA_DIA", valor_col])
        df_serie = serie.rename(valor_col).reset_index()
        if "DATA_DIA" not in df_serie.columns:
            primeira_coluna = df_serie.columns[0]
            df_serie = df_serie.rename(columns={primeira_coluna: "DATA_DIA"})
        df_serie["DATA_DIA"] = pd.to_datetime(df_serie["DATA_DIA"], errors="coerce").dt.normalize()
        df_serie = df_serie[df_serie["DATA_DIA"].notna()].copy()
        return df_serie[["DATA_DIA", valor_col]]

    aliases_union = set().union(*[m['aliases_real'] for m in metricas_cfg])
    df_mes_union = df_work[
        df_work['MES_NORM'].eq(mes_ref_norm) &
        df_work['IND_NORM'].isin(aliases_union)
    ].copy()
    df_mes_union = df_mes_union[df_mes_union['DATA_DIA'].notna()].copy()
    if not df_mes_union.empty:
        qtd_mes_union = pd.to_numeric(df_mes_union.get('QTDE', 0), errors='coerce').fillna(0.0)
        datas_validas = pd.to_datetime(
            df_mes_union.loc[qtd_mes_union > 0, 'DATA_DIA'],
            errors='coerce'
        ).dropna()
        if datas_validas.empty:
            datas_validas = pd.to_datetime(df_mes_union['DATA_DIA'], errors='coerce').dropna()
    else:
        datas_validas = pd.Series(dtype='datetime64[ns]')

    inicio_mes = pd.to_datetime(cal['DATA_DIA'], errors='coerce').min().date()
    fim_mes = pd.to_datetime(cal['DATA_DIA'], errors='coerce').max().date()
    if datas_validas.empty:
        data_corte = (inicio_mes - timedelta(days=1)) if eh_mes_atual else fim_mes
    else:
        data_corte = pd.Timestamp(datas_validas.max()).date()
    if eh_mes_atual and data_corte > hoje:
        data_corte = hoje
    if data_corte > fim_mes:
        data_corte = fim_mes

    dias_restantes = contar_dias_restantes_semana(mes_ref, data_corte)
    dias_tend_header: set[tuple[int, str]] = set()
    if eh_mes_atual:
        cal_fut = cal[pd.to_datetime(cal['DATA_DIA'], errors='coerce').dt.date > data_corte].copy()
        dias_tend_header = {
            (int(r.SEMANA_IDX), str(r.DIA_ROTULO))
            for r in cal_fut.itertuples(index=False)
        }

    def soma_mes_alias(aliases: set[str], coluna: str, mes_norm_ref: str, canal_ref_local: str | None = None) -> float:
        if not aliases:
            return 0.0
        filtro = df_work['MES_NORM'].eq(str(mes_norm_ref).strip().lower()) & df_work['IND_NORM'].isin(aliases)
        if canal_ref_local is not None and str(canal_ref_local).strip():
            filtro = filtro & df_work['CANAL_PLAN'].eq(str(canal_ref_local).strip())
        return float(pd.to_numeric(df_work.loc[filtro, coluna], errors='coerce').fillna(0.0).sum())

    semanas = sorted(cal['SEMANA_IDX'].dropna().astype(int).unique().tolist())
    dias_semana_map: dict[int, list[dict]] = {}
    for sem in semanas:
        cal_sem = cal[cal['SEMANA_IDX'].eq(int(sem))].copy()
        dias_semana_map[int(sem)] = [
            {
                'data': pd.Timestamp(r.DATA_DIA).normalize(),
                'dia_rotulo': str(r.DIA_ROTULO),
                'dia_semana': int(r.DIA_SEMANA)
            }
            for r in cal_sem.itertuples(index=False)
        ]

    metricas_out = []
    tem_dados = False
    for cfg in metricas_cfg:
        aliases_real = set(cfg['aliases_real'])
        aliases_meta = set(cfg['aliases_meta'])
        nome_metrica = str(cfg['nome'])

        df_real_all = df_work[df_work['IND_NORM'].isin(aliases_real)].copy()
        df_real_mes = df_real_all[df_real_all['MES_NORM'].eq(mes_ref_norm)].copy()
        df_real_mes = df_real_mes[df_real_mes['DATA_DIA'].notna()].copy()
        df_real_m1 = df_real_all[df_real_all['MES_NORM'].eq(mes_m1_norm)].copy()
        df_real_m1 = df_real_m1[df_real_m1['DATA_DIA'].notna()].copy()
        agg_real = (
            df_real_mes.groupby('DATA_DIA', observed=True)['QTDE'].sum()
            if not df_real_mes.empty else pd.Series(dtype='float64')
        )
        agg_real_m1 = (
            df_real_m1.groupby('DATA_DIA', observed=True)['QTDE'].sum()
            if not df_real_m1.empty else pd.Series(dtype='float64')
        )

        serie_atual = cal[['DATA_DIA', 'SEMANA_IDX', 'DIA_SEMANA']].copy()
        serie_atual['DATA_DIA'] = pd.to_datetime(serie_atual['DATA_DIA'], errors='coerce').dt.normalize()
        serie_atual = serie_atual[serie_atual['DATA_DIA'].notna()].copy()
        serie_atual = serie_atual.merge(
            _serie_diaria_para_merge(agg_real, 'VALOR_DIA'),
            on='DATA_DIA',
            how='left'
        )
        serie_atual['VALOR_DIA'] = pd.to_numeric(serie_atual.get('VALOR_DIA', 0), errors='coerce').fillna(0.0)
        serie_atual['VALOR_FINAL'] = pd.to_numeric(serie_atual['VALOR_DIA'], errors='coerce').fillna(0.0)

        ctx_peso_proj = _montar_contexto_pesos_projecao_semana_dia(
            df_real_all,
            mes_ref,
            valor_col='QTDE'
        )

        tend_mes_total = soma_mes_alias(aliases_real, 'TEND_QTD', mes_ref_norm)
        if eh_mes_atual and float(tend_mes_total or 0.0) > 0 and not serie_atual.empty:
            df_datas_real = df_real_mes.copy()
            if not df_datas_real.empty:
                df_datas_real['DATA_DIA'] = pd.to_datetime(
                    df_datas_real.get('DATA_DIA'),
                    errors='coerce'
                ).dt.normalize()
                df_datas_real['QTDE'] = pd.to_numeric(
                    df_datas_real.get('QTDE', 0),
                    errors='coerce'
                ).fillna(0.0)
                datas_validas = pd.to_datetime(
                    df_datas_real.loc[df_datas_real['QTDE'] > 0, 'DATA_DIA'],
                    errors='coerce'
                ).dropna()
                if datas_validas.empty:
                    datas_validas = pd.to_datetime(
                        df_datas_real.loc[df_datas_real['DATA_DIA'].notna(), 'DATA_DIA'],
                        errors='coerce'
                    ).dropna()
            else:
                datas_validas = pd.Series(dtype='datetime64[ns]')

            if datas_validas.empty:
                data_corte_metrica = pd.Timestamp(inicio_mes).normalize() - pd.Timedelta(days=1)
            else:
                data_corte_metrica = pd.Timestamp(datas_validas.max()).normalize()
                limite_real_metrica = pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
                if data_corte_metrica > limite_real_metrica:
                    data_corte_metrica = limite_real_metrica

            mask_realizado = pd.to_datetime(serie_atual['DATA_DIA'], errors='coerce') <= data_corte_metrica
            real_total = float(pd.to_numeric(
                serie_atual.loc[mask_realizado, 'VALOR_FINAL'],
                errors='coerce'
            ).fillna(0.0).sum())
            gap_tend = float(tend_mes_total) - real_total

            if gap_tend > 0:
                mask_restante = pd.to_datetime(serie_atual['DATA_DIA'], errors='coerce') > data_corte_metrica
                if bool(mask_restante.any()):
                    idx_restantes = list(serie_atual.index[mask_restante])
                    pesos_tend = []
                    for idx_row in idx_restantes:
                        semana_idx = int(serie_atual.at[idx_row, 'SEMANA_IDX'])
                        dia_semana = int(serie_atual.at[idx_row, 'DIA_SEMANA'])
                        peso = _obter_peso_projecao_semana_dia(
                            ctx_peso_proj,
                            semana_idx,
                            dia_semana
                        )
                        pesos_tend.append(max(float(peso), 0.0))

                    soma_pesos_tend = float(np.sum(pesos_tend))
                    if soma_pesos_tend <= 0:
                        pesos_tend = [1.0] * len(idx_restantes)
                        soma_pesos_tend = float(len(idx_restantes)) if idx_restantes else 1.0

                    if idx_restantes and soma_pesos_tend > 0:
                        addicoes = [gap_tend * (p / soma_pesos_tend) for p in pesos_tend]
                        ajuste_final = gap_tend - float(np.sum(addicoes))
                        addicoes[-1] = addicoes[-1] + ajuste_final
                        for idx_row, add_val in zip(idx_restantes, addicoes):
                            serie_atual.at[idx_row, 'VALOR_FINAL'] = (
                                float(serie_atual.at[idx_row, 'VALOR_FINAL']) + float(add_val)
                            )

        real_dia_exib = {
            pd.Timestamp(r.DATA_DIA).normalize(): float(r.VALOR_FINAL or 0.0)
            for r in serie_atual.itertuples(index=False)
        }

        orc_mes = soma_mes_alias(aliases_meta, 'DESAFIO_QTD', mes_ref_norm)
        if bool(cfg.get('fallback_meta_vb')):
            canais_calculo = sorted(
                df_work.loc[df_work['MES_NORM'].eq(mes_ref_norm), 'CANAL_PLAN']
                .dropna()
                .astype(str)
                .str.strip()
                .unique()
                .tolist()
            )
            if not canais_calculo:
                canais_calculo = sorted(
                    df_work['CANAL_PLAN']
                    .dropna()
                    .astype(str)
                    .str.strip()
                    .unique()
                    .tolist()
                )

            orc_mes_canal_total = 0.0
            for canal_calc in canais_calculo:
                meta_vb_canal = soma_mes_alias(aliases_meta, 'DESAFIO_QTD', mes_ref_norm, canal_calc)
                if float(meta_vb_canal or 0.0) > 0:
                    orc_mes_canal_total += float(meta_vb_canal)
                    continue

                meta_ativ_canal = soma_mes_alias(alias_meta_ativ, 'DESAFIO_QTD', mes_ref_norm, canal_calc)
                real_ativ_canal = soma_mes_alias(alias_ativ, 'QTDE', mes_ref_norm, canal_calc)
                real_vb_canal = soma_mes_alias(alias_vb, 'QTDE', mes_ref_norm, canal_calc)
                ratio_ativ_vb_canal = (float(real_ativ_canal) / float(real_vb_canal)) if float(real_vb_canal) > 0 else np.nan

                if (not pd.notna(ratio_ativ_vb_canal)) or float(ratio_ativ_vb_canal) <= 0:
                    real_ativ_canal_m1 = soma_mes_alias(alias_ativ, 'QTDE', mes_m1_norm, canal_calc)
                    real_vb_canal_m1 = soma_mes_alias(alias_vb, 'QTDE', mes_m1_norm, canal_calc)
                    ratio_ativ_vb_canal = (float(real_ativ_canal_m1) / float(real_vb_canal_m1)) if float(real_vb_canal_m1) > 0 else np.nan

                if pd.notna(ratio_ativ_vb_canal) and float(ratio_ativ_vb_canal) > 0 and float(meta_ativ_canal) > 0:
                    meta_vb_canal = float(meta_ativ_canal) / float(ratio_ativ_vb_canal)
                else:
                    meta_vb_canal = 0.0
                orc_mes_canal_total += float(meta_vb_canal)

            if float(orc_mes_canal_total) > 0:
                orc_mes = float(orc_mes_canal_total)

        proj_dia: dict[pd.Timestamp, float] = {}
        if not serie_atual.empty:
            pesos_meta = []
            datas_meta = []
            for row in serie_atual.itertuples(index=False):
                semana_idx = int(row.SEMANA_IDX)
                dia_semana = int(row.DIA_SEMANA)
                peso = _obter_peso_projecao_semana_dia(
                    ctx_peso_proj,
                    semana_idx,
                    dia_semana
                )
                datas_meta.append(pd.Timestamp(row.DATA_DIA).normalize())
                pesos_meta.append(max(float(peso), 0.0))

            soma_pesos_meta = float(np.sum(pesos_meta))
            if soma_pesos_meta <= 0:
                pesos_meta = [1.0] * len(datas_meta)
                soma_pesos_meta = float(len(datas_meta)) if datas_meta else 1.0

            meta_diaria = [
                float(orc_mes or 0.0) * (peso / soma_pesos_meta)
                for peso in pesos_meta
            ] if datas_meta else []
            if meta_diaria:
                ajuste_meta = float(orc_mes or 0.0) - float(np.sum(meta_diaria))
                meta_diaria[-1] = float(meta_diaria[-1]) + float(ajuste_meta)

            for dt_ref, v_ref in zip(datas_meta, meta_diaria):
                proj_dia[dt_ref] = float(v_ref)

        for dt_ref in pd.to_datetime(cal['DATA_DIA'], errors='coerce').dropna():
            dt_norm = pd.Timestamp(dt_ref).normalize()
            if dt_norm not in proj_dia:
                proj_dia[dt_norm] = 0.0

        total_sem_proj: dict[int, float] = {}
        total_sem_real: dict[int, float] = {}
        ating_sem: dict[int, float] = {}
        for sem in semanas:
            dias_sem = dias_semana_map.get(int(sem), [])
            soma_proj = float(sum(proj_dia.get(pd.Timestamp(d['data']).normalize(), 0.0) for d in dias_sem))
            soma_real = float(sum(real_dia_exib.get(pd.Timestamp(d['data']).normalize(), 0.0) for d in dias_sem))
            total_sem_proj[int(sem)] = soma_proj
            total_sem_real[int(sem)] = soma_real
            ating_sem[int(sem)] = (((soma_real / soma_proj) - 1) * 100.0) if soma_proj > 0 else np.nan

        total_mes_proj = float(sum(total_sem_proj.values()))
        total_mes_real = float(sum(total_sem_real.values()))
        ating_mes = (((total_mes_real / total_mes_proj) - 1) * 100.0) if total_mes_proj > 0 else np.nan
        if (
            total_mes_proj > 0 or
            total_mes_real > 0 or
            float(orc_mes or 0.0) > 0
        ):
            tem_dados = True

        metricas_out.append({
            'nome': nome_metrica,
            'proj_dia': proj_dia,
            'real_dia': real_dia_exib,
            'total_sem_proj': total_sem_proj,
            'total_sem_real': total_sem_real,
            'ating_sem': ating_sem,
            'total_mes_proj': total_mes_proj,
            'total_mes_real': total_mes_real,
            'ating_mes': ating_mes
        })

    if not tem_dados:
        return "", ({
            'mes_anterior': mes_m1,
            'dias_restantes': dias_restantes,
            'data_corte': data_corte
        } if incluir_ctx else {})

    def fmt_num(v: float) -> str:
        return formatar_numero_brasileiro(v, 0)

    def fmt_pct(v: float) -> str:
        if pd.isna(v):
            return "-"
        return f"{float(v):.0f}%".replace(".", ",")

    def classe_pct(v: float) -> str:
        if pd.isna(v):
            return "pct-neutro"
        if float(v) > 0:
            return "pct-positivo"
        if float(v) < 0:
            return "pct-negativo"
        return "pct-neutro"

    total_cols_dinamicas = sum(len(dias_semana_map.get(int(s), [])) + 1 for s in semanas) + 1
    largura_label = 7.5
    largura_dinamica = (100.0 - largura_label) / float(max(total_cols_dinamicas, 1))
    colgroup = "<colgroup>"
    colgroup += f'<col style="width:{largura_label:.4f}%;">'
    for _ in range(total_cols_dinamicas):
        colgroup += f'<col style="width:{largura_dinamica:.4f}%;">'
    colgroup += "</colgroup>"

    th_sem1 = "".join(
        [
            f'<th colspan="{len(dias_semana_map.get(int(s), [])) + 1}" class="th-semana w{s}">SEMANA {int(s)}</th>'
            for s in semanas
        ]
    )
    th_sem2 = ""
    for s in semanas:
        dias_sem = dias_semana_map.get(int(s), [])
        for idx, d in enumerate(dias_sem):
            dia_ref = str(d['dia_rotulo'])
            cls = f"th-dia w{int(s)}"
            if idx == 0:
                cls += " week-start"
            if (int(s), dia_ref) in dias_tend_header:
                cls += " th-dia-tend"
            else:
                cls += " th-dia-real"
            th_sem2 += f'<th class="{cls}">{escape(dia_ref)}</th>'
        th_sem2 += f'<th class="th-dia-tot w{int(s)}">tot.</th>'

    def linha_valor(
        rotulo: str,
        valores_dia: dict[pd.Timestamp, float],
        total_sem: dict[int, float],
        total_mes: float,
        classe_linha: str,
        ocultar_futuro: bool = False
    ) -> str:
        html_row = f'<tr class="{classe_linha}"><td class="col-linha">{escape(rotulo)}</td>'
        for s in semanas:
            dias_sem = dias_semana_map.get(int(s), [])
            for idx, d in enumerate(dias_sem):
                dt_key = pd.Timestamp(d['data']).normalize()
                dia_sem = int(d['dia_semana'])
                eh_futuro = bool(eh_mes_atual and (dt_key.date() > data_corte))
                cls = f'col-dia w{int(s)}'
                if idx == 0:
                    cls += " week-start"
                if dia_sem in {5, 6}:
                    cls += " dia-fds"
                else:
                    cls += " dia-util"
                if eh_futuro:
                    cls += " dia-futuro"
                valor = float(valores_dia.get(dt_key, 0.0))
                txt = "" if (ocultar_futuro and eh_futuro) else fmt_num(valor)
                html_row += f'<td class="{cls}">{txt}</td>'
            html_row += f'<td class="col-total-sem w{int(s)}">{fmt_num(total_sem.get(int(s), 0.0))}</td>'
        html_row += f'<td class="col-total-mes">{fmt_num(total_mes)}</td></tr>'
        return html_row

    def linha_ating(
        rotulo: str,
        proj_dia_ref: dict[pd.Timestamp, float],
        real_dia_ref: dict[pd.Timestamp, float],
        ating_sem_ref: dict[int, float],
        ating_mes_ref: float
    ) -> str:
        html_row = f'<tr class="linha-ating"><td class="col-linha">{escape(rotulo)}</td>'
        for s in semanas:
            dias_sem = dias_semana_map.get(int(s), [])
            for idx, d in enumerate(dias_sem):
                dt_key = pd.Timestamp(d['data']).normalize()
                dia_sem = int(d['dia_semana'])
                cls = f'col-dia w{int(s)}'
                if idx == 0:
                    cls += " week-start"
                if dia_sem in {5, 6}:
                    cls += " dia-fds"
                else:
                    cls += " dia-util"
                proj_dia = float(proj_dia_ref.get(dt_key, 0.0))
                real_dia = float(real_dia_ref.get(dt_key, 0.0))
                ating_dia = (((real_dia / proj_dia) - 1) * 100.0) if proj_dia > 0 else np.nan
                cls += f" col-pct {classe_pct(ating_dia)}"
                html_row += f'<td class="{cls}">{fmt_pct(ating_dia)}</td>'
            ating_sem_val = ating_sem_ref.get(int(s), np.nan)
            html_row += f'<td class="col-total-sem w{int(s)} col-pct {classe_pct(ating_sem_val)}">{fmt_pct(ating_sem_val)}</td>'
        html_row += f'<td class="col-total-mes col-pct {classe_pct(ating_mes_ref)}">{fmt_pct(ating_mes_ref)}</td></tr>'
        return html_row

    total_cols = 1 + sum(len(dias_semana_map.get(int(s), [])) + 1 for s in semanas) + 1
    corpo_html = ""
    for met in metricas_out:
        corpo_html += f'<tr class="linha-grupo"><td colspan="{int(total_cols)}">{escape(str(met["nome"]).upper())}</td></tr>'
        corpo_html += linha_valor(
            "Orçamento Diário",
            met['proj_dia'],
            met['total_sem_proj'],
            met['total_mes_proj'],
            "linha-projecao",
            ocultar_futuro=False
        )
        corpo_html += linha_valor(
            "Realizado",
            met['real_dia'],
            met['total_sem_real'],
            met['total_mes_real'],
            "linha-realizado",
            ocultar_futuro=False
        )
        corpo_html += linha_ating(
            "Atingimento (%)",
            met['proj_dia'],
            met['real_dia'],
            met['ating_sem'],
            met['ating_mes']
        )

    css = f"""
    <style>
    .{table_id}-container {{
        width: 100%;
        overflow-x: auto;
        border: 2px solid #790E09;
        border-radius: 12px;
        box-shadow: 0 4px 20px rgba(121, 14, 9, 0.15);
        margin: 10px 0 6px 0;
        background: #FFFFFF;
    }}
    table.{table_id} {{
        border-collapse: collapse;
        width: 100%;
        min-width: 100%;
        max-width: 100%;
        table-layout: fixed;
        font-size: clamp(8.8px, 0.70vw, 10.4px);
        line-height: 1.04;
    }}
    .{table_id} thead th {{
        background: linear-gradient(135deg, #790E09 0%, #5A0A06 100%);
        color: #fff;
        padding: 5px 3px;
        text-align: center;
        font-weight: 800;
        letter-spacing: 0.15px;
        border-right: 1px solid rgba(255,255,255,0.88);
        white-space: normal;
        line-height: 1.0;
        font-size: clamp(8.2px, 0.66vw, 10.2px);
    }}
    .{table_id} thead th.th-semana {{
        background: linear-gradient(135deg, #6C0C08 0%, #4A0704 100%) !important;
        border-bottom: 2px solid rgba(255,255,255,0.22);
        font-size: clamp(8.6px, 0.70vw, 10.8px);
    }}
    .{table_id} thead th.th-dia-tot {{
        background: linear-gradient(135deg, #B23A31 0%, #8F1B14 100%) !important;
    }}
    .{table_id} thead th.th-total-mes {{
        background: linear-gradient(135deg, #A4342D 0%, #7A130E 100%) !important;
    }}
    .{table_id} thead th.th-dia.th-dia-real {{
        background: linear-gradient(135deg, #6C0C08 0%, #4A0704 100%) !important;
    }}
    .{table_id} thead th.th-dia.th-dia-tend {{
        background: linear-gradient(135deg, #B7443B 0%, #8F241D 100%) !important;
    }}
    .{table_id} thead tr:nth-child(2) th {{
        font-size: clamp(7.8px, 0.60vw, 9.6px);
    }}
    .{table_id} tbody td {{
        padding: 4px 3px;
        text-align: center;
        border-bottom: 1px solid #FFFFFF;
        border-right: 1px solid #FFFFFF;
        color: #2F3747;
        font-weight: 400;
        font-size: clamp(8.5px, 0.66vw, 10px);
        vertical-align: bottom;
        white-space: nowrap;
    }}
    .{table_id} tbody td.col-linha {{
        text-align: left;
        padding-left: 5px;
        font-weight: 600;
        line-height: 1.05;
        position: sticky;
        left: 0;
        z-index: 5;
        background: transparent !important;
        white-space: nowrap;
    }}
    .{table_id} tbody td.col-dia {{
        background: transparent !important;
    }}
    .{table_id} tbody td.col-dia.week-start {{
        border-left: 1px solid #FFFFFF;
    }}
    .{table_id} tbody td.col-dia.dia-fds {{
        background: transparent !important;
    }}
    .{table_id} tbody td.col-dia.dia-util {{
        background: transparent !important;
    }}
    .{table_id} tbody td.col-dia.dia-futuro {{
        background: transparent !important;
    }}
    .{table_id} tbody td.col-total-sem {{
        background: linear-gradient(180deg, rgba(47, 55, 71, 0.06) 0%, rgba(47, 55, 71, 0.025) 100%) !important;
        color: #1F2937;
        font-weight: 600;
    }}
    .{table_id} tbody td.col-total-mes {{
        background: linear-gradient(180deg, rgba(47, 55, 71, 0.075) 0%, rgba(47, 55, 71, 0.03) 100%) !important;
        color: #1F2937;
        font-weight: 700;
    }}
    .{table_id} tbody td.col-pct {{
        color: #374151;
        font-weight: 600;
        background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
    }}
    .{table_id} tbody td.col-pct.pct-positivo {{
        color: #1B5E20 !important;
    }}
    .{table_id} tbody td.col-pct.pct-negativo {{
        color: #B71C1C !important;
    }}
    .{table_id} tbody td.col-pct.pct-neutro {{
        color: #475569 !important;
    }}
    .{table_id} tbody tr.linha-grupo td {{
        background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
        color: #FFFFFF !important;
        text-align: left !important;
        font-weight: 800;
        letter-spacing: 0.3px;
        padding: 6px 8px !important;
        border-top: 1px solid #FFFFFF !important;
        border-bottom: 1px solid #FFFFFF !important;
    }}
    .{table_id} tbody tr.linha-projecao td {{
        background: linear-gradient(135deg, #FCFCFD 0%, #F7F8FA 100%) !important;
    }}
    .{table_id} tbody tr.linha-realizado td {{
        background: linear-gradient(135deg, #FFFFFF 0%, #FAFBFC 100%) !important;
    }}
    .{table_id} tbody tr.linha-ating td {{
        background: linear-gradient(135deg, #FCFCFD 0%, #F7F8FA 100%) !important;
    }}
    .{table_id} tbody tr.linha-projecao td.col-linha,
    .{table_id} tbody tr.linha-projecao td.col-dia,
    .{table_id} tbody tr.linha-projecao td.col-dia.dia-fds,
    .{table_id} tbody tr.linha-projecao td.col-dia.dia-util,
    .{table_id} tbody tr.linha-projecao td.col-dia.dia-futuro,
    .{table_id} tbody tr.linha-realizado td.col-linha,
    .{table_id} tbody tr.linha-realizado td.col-dia,
    .{table_id} tbody tr.linha-realizado td.col-dia.dia-fds,
    .{table_id} tbody tr.linha-realizado td.col-dia.dia-util,
    .{table_id} tbody tr.linha-realizado td.col-dia.dia-futuro,
    .{table_id} tbody tr.linha-ating td.col-linha {{
        background: linear-gradient(135deg, #FCFCFD 0%, #F7F8FA 100%) !important;
    }}
    .{table_id} tbody tr.linha-realizado td.col-linha,
    .{table_id} tbody tr.linha-realizado td.col-dia,
    .{table_id} tbody tr.linha-realizado td.col-dia.dia-fds,
    .{table_id} tbody tr.linha-realizado td.col-dia.dia-util,
    .{table_id} tbody tr.linha-realizado td.col-dia.dia-futuro {{
        background: linear-gradient(135deg, #FFFFFF 0%, #FAFBFC 100%) !important;
    }}
    .{table_id} tbody tr.linha-ating td.col-linha,
    .{table_id} tbody tr.linha-ating td.col-dia,
    .{table_id} tbody tr.linha-ating td.col-dia.dia-fds,
    .{table_id} tbody tr.linha-ating td.col-dia.dia-util,
    .{table_id} tbody tr.linha-ating td.col-dia.dia-futuro {{
        background: linear-gradient(135deg, #FCFCFD 0%, #F7F8FA 100%) !important;
    }}
    @media (max-width: 1600px) {{
        table.{table_id} {{ min-width: 100%; }}
    }}
    @media (max-width: 1366px) {{
        table.{table_id} {{ min-width: 100%; }}
    }}
    </style>
    """

    html_out = f"""
    {css}
    <div class="{table_id}-container">
          <table class="{table_id}">
        {colgroup}
        <thead>
          <tr>
            <th rowspan="2">{escape(str(produto_norm).upper())}</th>
            {th_sem1}
            <th rowspan="2" class="th-total-mes">TOTAL<br>MÊS</th>
          </tr>
          <tr>
            {th_sem2}
          </tr>
        </thead>
        <tbody>
          {corpo_html}
        </tbody>
      </table>
    </div>
    """

    return html_out, ({
        'mes_anterior': mes_m1,
        'dias_restantes': dias_restantes,
        'data_corte': data_corte
    } if incluir_ctx else {})

def _colunas_tabela_analitica(mes_ref: str, mes_anterior_ref: str) -> list[str]:
    ano_meta = "26"
    try:
        ano_meta = str(mes_ref).split('/')[-1]
    except Exception:
        pass
    mes_anterior_lbl = str(mes_anterior_ref).strip().upper()
    mes_atual_lbl = str(mes_ref).strip().upper()
    return [
        'CANAL',
        mes_anterior_lbl,
        mes_atual_lbl,
        f'ORÇ {ano_meta}',
        'ORÇ X REAL',
        'SEG',
        'TER',
        'QUA',
        'QUI',
        'SEX',
        'SAB',
        'DOM'
    ]

def montar_tabela_analitica_exibicao_numerica(
    df_tabela: pd.DataFrame,
    mes_ref: str,
    mes_anterior_ref: str,
    incluir_total: bool = False
) -> pd.DataFrame:
    """Monta tabela analítica com colunas de exibição e valores numéricos."""
    colunas_saida = _colunas_tabela_analitica(mes_ref, mes_anterior_ref)
    if df_tabela is None or df_tabela.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_num = pd.DataFrame({
        'CANAL': df_tabela['CANAL'].astype(str),
        colunas_saida[1]: normalizar_numerico_serie(df_tabela['MES_ANTERIOR']).fillna(0.0),
        colunas_saida[2]: normalizar_numerico_serie(df_tabela['MES_ATUAL_REAL']).fillna(0.0),
        colunas_saida[3]: normalizar_numerico_serie(df_tabela['META_MES']).fillna(0.0),
        colunas_saida[4]: normalizar_numerico_serie(df_tabela['VAR_META_X_REAL']).fillna(0.0),
        'SEG': normalizar_numerico_serie(df_tabela['SEG']).fillna(0.0),
        'TER': normalizar_numerico_serie(df_tabela['TER']).fillna(0.0),
        'QUA': normalizar_numerico_serie(df_tabela['QUA']).fillna(0.0),
        'QUI': normalizar_numerico_serie(df_tabela['QUI']).fillna(0.0),
        'SEX': normalizar_numerico_serie(df_tabela['SEX']).fillna(0.0),
        'SAB': normalizar_numerico_serie(df_tabela['SAB']).fillna(0.0),
        'DOM': normalizar_numerico_serie(df_tabela['DOM']).fillna(0.0)
    })

    if incluir_total and not df_num.empty:
        total_atual = float(pd.to_numeric(df_num[colunas_saida[2]], errors='coerce').fillna(0.0).sum())
        total_meta = float(pd.to_numeric(df_num[colunas_saida[3]], errors='coerce').fillna(0.0).sum())
        pct_total = (100.0 * total_atual / total_meta) if total_meta > 0 else np.nan
        if pd.notna(pct_total):
            pct_total_txt = f"{pct_total:,.1f}%".replace(",", "X").replace(".", ",").replace("X", ".")
            rotulo_total = f"TOTAL ({pct_total_txt})"
        else:
            rotulo_total = "TOTAL"

        linha_total = {'CANAL': rotulo_total}
        for col in df_num.columns:
            if col != 'CANAL':
                linha_total[col] = float(pd.to_numeric(df_num[col], errors='coerce').fillna(0.0).sum())
        df_num = pd.concat([pd.DataFrame([linha_total]), df_num], ignore_index=True)

    return df_num

def formatar_tabela_analitica(
    df_tabela: pd.DataFrame,
    mes_ref: str,
    mes_anterior_ref: str,
    incluir_total: bool = False
) -> pd.DataFrame:
    """Formata tabela analítica para exibição em padrão BR."""
    df_num = montar_tabela_analitica_exibicao_numerica(
        df_tabela=df_tabela,
        mes_ref=mes_ref,
        mes_anterior_ref=mes_anterior_ref,
        incluir_total=incluir_total
    )
    if df_num.empty:
        return df_num

    df_fmt = df_num.copy().astype(object)
    for col in df_fmt.columns:
        if col == 'CANAL':
            continue
        df_fmt[col] = pd.to_numeric(df_fmt[col], errors='coerce').fillna(0.0).apply(
            lambda x: formatar_numero_brasileiro(x, 0)
        )
    return df_fmt

def criar_tabela_html_analitica(df_formatado: pd.DataFrame, df_numerico: pd.DataFrame, table_id: str) -> str:
    """Cria tabela HTML estilizada para aba Analítico no padrão visual das tabelas do dashboard."""
    if df_formatado is None or df_formatado.empty:
        return ""

    colunas = list(df_formatado.columns)
    total_colunas = max(len(colunas), 1)
    largura_col_pct = 100.0 / total_colunas
    colgroup_html = "<colgroup>" + "".join(
        [f'<col style="width:{largura_col_pct:.4f}%;">' for _ in range(total_colunas)]
    ) + "</colgroup>"
    dias_semana_cols = {'SEG', 'TER', 'QUA', 'QUI', 'SEX', 'SAB', 'DOM'}
    col_var = colunas[4] if len(colunas) > 4 else "ORÇ X REAL"
    col_mes_anterior = colunas[1] if len(colunas) > 1 else ""
    col_mes_atual = colunas[2] if len(colunas) > 2 else ""
    col_meta = colunas[3] if len(colunas) > 3 else ""

    html = f"""
    <style>
        #{table_id}.tabela-container-analitico {{
            width: 100%;
            max-height: 520px;
            overflow-y: auto;
            overflow-x: auto;
            border: 2px solid #790E09;
            border-radius: 10px;
            box-shadow: 0 4px 20px rgba(121, 14, 9, 0.15);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            margin: 12px 0 18px 0;
            background: #FFFFFF;
        }}
        #{table_id} .tabela-analitico {{
            width: 100%;
            border-collapse: collapse;
            border-spacing: 0;
            font-size: 10px;
            line-height: 1.14;
            table-layout: fixed;
            font-family: 'Manrope', 'Segoe UI', sans-serif;
            min-width: 980px;
        }}
        #{table_id} .tabela-analitico thead {{
            position: sticky;
            top: 0;
            z-index: 100;
        }}
        #{table_id} .tabela-analitico th {{
            background: linear-gradient(135deg, #790E09 0%, #5A0A06 100%) !important;
            color: white !important;
            font-weight: 700;
            padding: 5px 5px;
            text-align: center;
            vertical-align: middle;
            border-bottom: 3px solid #5A0A06;
            border-right: 1px solid #FFFFFF;
            white-space: normal;
            overflow-wrap: break-word;
            word-break: normal;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            line-height: 1.2;
            font-size: 9px;
            font-family: 'Manrope', 'Segoe UI', sans-serif;
        }}
        #{table_id} .tabela-analitico th.col-var {{
            background: linear-gradient(135deg, #5A6268 0%, #3E444A 100%) !important;
        }}
        #{table_id} .tabela-analitico th.col-meta {{
            background: linear-gradient(135deg, #A23B36 0%, #790E09 100%) !important;
        }}
        #{table_id} .tabela-analitico th.col-dia {{
            background: linear-gradient(135deg, #6A7075 0%, #4B5258 100%) !important;
        }}
        #{table_id} .tabela-analitico td {{
            padding: 6px 5px 4px 5px;
            text-align: right;
            vertical-align: bottom !important;
            border-bottom: 1px solid #FFFFFF;
            border-right: 1px solid #FFFFFF;
            font-weight: 400;
            font-variant-numeric: tabular-nums;
            color: #1F2937;
            font-family: 'Manrope', 'Segoe UI', sans-serif;
            font-size: 10px;
            line-height: 1.16;
            white-space: normal;
            overflow-wrap: break-word;
            word-break: normal;
        }}
        #{table_id} .tabela-analitico tbody td,
        #{table_id} .tabela-analitico tbody td * {{
            font-weight: 400 !important;
        }}
        #{table_id} .tabela-analitico td.col-canal {{
            text-align: left;
            font-weight: 600;
            color: #2F3747;
            background: transparent !important;
            padding-left: 6px;
        }}
        #{table_id} .linha-canal-analitico:nth-child(even) {{
            background: linear-gradient(135deg, #FCFCFD 0%, #F7F8FA 100%) !important;
        }}
        #{table_id} .linha-canal-analitico:nth-child(odd) {{
            background: linear-gradient(135deg, #FFFFFF 0%, #FAFBFC 100%) !important;
        }}
        #{table_id} .linha-canal-analitico:hover {{
            background: linear-gradient(135deg, #FFF6F3 0%, #FAF0ED 100%) !important;
            box-shadow: inset 0 0 0 1px rgba(162, 59, 54, 0.12);
        }}
        #{table_id} .tabela-analitico td.col-anterior {{
            background: transparent !important;
            color: #2F3747;
            border-left: 1px solid rgba(47, 55, 71, 0.04);
            border-right: 1px solid rgba(47, 55, 71, 0.04);
        }}
        #{table_id} .tabela-analitico td.col-atual {{
            background: linear-gradient(180deg, rgba(47, 55, 71, 0.06) 0%, rgba(47, 55, 71, 0.025) 100%) !important;
            color: #1F2937;
            font-weight: 600;
            border-left: 1px solid rgba(47, 55, 71, 0.08);
            border-right: 1px solid rgba(47, 55, 71, 0.08);
        }}
        #{table_id} .tabela-analitico td.col-meta {{
            background: linear-gradient(180deg, rgba(121, 14, 9, 0.06) 0%, rgba(121, 14, 9, 0.022) 100%) !important;
            color: #6B1F1A;
            font-weight: 600;
            border-left: 1px solid rgba(121, 14, 9, 0.08);
            border-right: 1px solid rgba(121, 14, 9, 0.08);
        }}
        #{table_id} .tabela-analitico td.col-dia {{
            background: transparent !important;
            color: #2F3747;
            font-weight: 400;
        }}
        #{table_id} .tabela-analitico td.col-var.status-gap {{
            color: #B71C1C !important;
            font-weight: 700;
            position: relative;
            padding-left: 13px !important;
            background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
            border-left: 1px solid rgba(90, 98, 104, 0.08) !important;
            border-right: 1px solid rgba(90, 98, 104, 0.08) !important;
        }}
        #{table_id} .tabela-analitico td.col-var.status-gap::before {{
            content: "▼";
            position: absolute;
            left: 4px;
            top: 50%;
            transform: translateY(-50%);
            color: #C62828;
            font-size: 8px;
        }}
        #{table_id} .tabela-analitico td.col-var.status-superavit {{
            color: #1B5E20 !important;
            font-weight: 700;
            position: relative;
            padding-left: 13px !important;
            background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
            border-left: 1px solid rgba(90, 98, 104, 0.08) !important;
            border-right: 1px solid rgba(90, 98, 104, 0.08) !important;
        }}
        #{table_id} .tabela-analitico td.col-var.status-superavit::before {{
            content: "▲";
            position: absolute;
            left: 4px;
            top: 50%;
            transform: translateY(-50%);
            color: #2E7D32;
            font-size: 8px;
        }}
        #{table_id} .tabela-analitico td.col-var.status-neutro {{
            color: #666666 !important;
            font-weight: 500;
            background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
            border-left: 1px solid rgba(90, 98, 104, 0.08) !important;
            border-right: 1px solid rgba(90, 98, 104, 0.08) !important;
        }}
        #{table_id} .linha-total-analitico {{
            position: sticky;
            top: 35px;
            z-index: 95;
            border-bottom: 2px solid #790E09;
        }}
        #{table_id} .linha-total-analitico td {{
            background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
            color: white !important;
            font-weight: 700;
            font-size: 10px;
            padding: 6px 5px 4px 5px;
            vertical-align: bottom !important;
            border-right: 1px solid rgba(255, 255, 255, 0.12) !important;
        }}
        #{table_id} .linha-total-analitico td.col-canal {{
            background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
            z-index: 80;
        }}
        #{table_id} .linha-total-analitico td.col-var.status-gap,
        #{table_id} .linha-total-analitico td.col-var.status-superavit,
        #{table_id} .linha-total-analitico td.col-var.status-neutro {{
            background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
            color: #FFFFFF !important;
            padding-left: 4px !important;
        }}
        #{table_id} .linha-total-analitico td.col-var::before {{
            content: "" !important;
        }}
        #{table_id} .linha-total-analitico td.col-var.status-gap::before,
        #{table_id} .linha-total-analitico td.col-var.status-superavit::before,
        #{table_id} .linha-total-analitico td.col-var.status-neutro::before {{
            content: "" !important;
        }}
        #{table_id}.tabela-container-analitico::-webkit-scrollbar {{
            width: 8px;
            height: 8px;
        }}
        #{table_id}.tabela-container-analitico::-webkit-scrollbar-track {{
            background: #F5F5F5;
            border-radius: 10px;
        }}
        #{table_id}.tabela-container-analitico::-webkit-scrollbar-thumb {{
            background: linear-gradient(135deg, #A23B36 0%, #790E09 100%);
            border-radius: 10px;
        }}
    </style>
    <div id="{table_id}" class="tabela-container-analitico">
    <table class="tabela-analitico">
    {colgroup_html}
    <thead><tr>
    """

    for col in colunas:
        classe = ""
        col_upper = str(col).upper()
        if col == col_meta:
            classe = "col-meta"
        elif col == col_var:
            classe = "col-var"
        elif col_upper in dias_semana_cols:
            classe = "col-dia"
        html += f'<th class="{classe}">{escape(str(col))}</th>'
    html += "</tr></thead><tbody>"

    for idx, row in df_formatado.iterrows():
        canal_ref = str(df_numerico.iloc[idx, 0]) if idx < len(df_numerico) else str(row.iloc[0])
        is_total = canal_ref.strip().upper().startswith("TOTAL")
        classe_linha = "linha-total-analitico" if is_total else "linha-canal-analitico"
        html += f'<tr class="{classe_linha}">'

        for col_idx, col in enumerate(colunas):
            valor_fmt = escape(str(row[col]))
            classes = []
            col_upper = str(col).upper()

            if col_idx == 0:
                classes.append("col-canal")
            elif col == col_mes_anterior:
                classes.append("col-anterior")
            elif col == col_mes_atual:
                classes.append("col-atual")
            elif col == col_meta:
                classes.append("col-meta")
            elif col_upper in dias_semana_cols:
                classes.append("col-dia")
            elif col == col_var:
                classes.append("col-var")
                try:
                    valor_raw = float(df_numerico.iloc[idx, col_idx])
                    if valor_raw > 0:
                        classes.append("status-gap")
                    elif valor_raw < 0:
                        classes.append("status-superavit")
                    else:
                        classes.append("status-neutro")
                except Exception:
                    classes.append("status-neutro")

            classe_celula = " ".join(classes)
            html += f'<td class="{classe_celula}">{valor_fmt}</td>'

        html += "</tr>"

    html += "</tbody></table></div>"
    return html

def construir_tabela_resultado_canais(
    df_base: pd.DataFrame,
    mes_ref: str,
    produto_ref: str
) -> pd.DataFrame:
    """Monta tabela de resultado por canal (3 meses, MoM, YoY, YTD, Orç e Var Orç)."""
    colunas_saida = [
        'CANAL_PLAN', 'MES_M2', 'MES_M1', 'MES_ATUAL_TEND',
        'MOM', 'YOY', 'YTD25', 'YTD26', 'YTD_ORC', 'VAR_YTD', 'VAR_YTD_ORC', 'META', 'VAR_META'
    ]
    colunas_retorno = [*colunas_saida, 'MES_YOY_BASE']
    canais_ordem = [
        'Televendas Ativo',
        'Televendas Receptivo',
        'S2S+DAC',
        'E-Commerce',
        'Hospitality',
        'Consultivo Remoto'
    ]

    def _norm_texto(valor) -> str:
        if pd.isna(valor):
            return ""
        texto = unicodedata.normalize("NFKD", str(valor))
        texto = texto.encode("ASCII", "ignore").decode("ASCII")
        texto = texto.strip().upper()
        texto = re.sub(r"[^A-Z0-9]+", " ", texto)
        return re.sub(r"\s+", " ", texto).strip()

    mes_ref_norm = str(mes_ref).strip().lower()
    mes_m1 = get_mes_anterior(mes_ref_norm)
    mes_m2 = get_mes_anterior(mes_m1)
    produto_norm = str(produto_ref).strip().upper()

    indicador_real_norm = 'GROSS LIQUIDO' if produto_norm == 'CONTA' else 'INSTALACAO'
    indicador_meta_norm = 'GROSS LIQUIDO'

    if df_base is None or df_base.empty:
        return pd.DataFrame(
            [
                {
                    'CANAL_PLAN': canal,
                    'MES_M2': 0.0,
                    'MES_M1': 0.0,
                    'MES_ATUAL_TEND': 0.0,
                    'MOM': 0.0,
                    'YOY': 0.0,
                    'YTD25': 0.0,
                    'YTD26': 0.0,
                    'YTD_ORC': 0.0,
                    'VAR_YTD': 0.0,
                    'VAR_YTD_ORC': 0.0,
                    'MES_YOY_BASE': 0.0,
                    'META': 0.0,
                    'VAR_META': 0.0
                }
                for canal in canais_ordem
            ],
            columns=colunas_retorno
        )

    df_work = df_base.copy()
    for coluna in ['CANAL_PLAN', 'COD_PLATAFORMA', 'DSC_INDICADOR', 'dat_tratada']:
        if coluna in df_work.columns:
            df_work[coluna] = df_work[coluna].astype(str).str.strip()

    if 'TEND_QTD' not in df_work.columns:
        df_work['TEND_QTD'] = df_work.get('QTDE', 0)
    if 'DESAFIO_QTD' not in df_work.columns:
        df_work['DESAFIO_QTD'] = 0

    df_work['QTDE'] = normalizar_numerico_serie(df_work.get('QTDE', 0)).fillna(0.0)
    df_work['TEND_QTD'] = normalizar_numerico_serie(df_work.get('TEND_QTD', 0)).fillna(0.0)
    df_work['DESAFIO_QTD'] = normalizar_numerico_serie(df_work.get('DESAFIO_QTD', 0)).fillna(0.0)
    df_work['MES_NORM'] = df_work['dat_tratada'].astype(str).str.strip().str.lower()
    df_work['PLATAFORMA_NORM'] = df_work['COD_PLATAFORMA'].astype(str).str.strip().str.upper()
    df_work['INDICADOR_NORM'] = df_work['DSC_INDICADOR'].apply(_norm_texto)

    def _canal_canonico(valor_canal: str) -> str:
        texto = _norm_texto(valor_canal)
        if 'TELEVENDAS ATIVO' in texto:
            return 'Televendas Ativo'
        if 'TELEVENDAS RECEPTIVO' in texto:
            return 'Televendas Receptivo'
        if 'S2S' in texto and 'DAC' in texto:
            return 'S2S+DAC'
        if 'E COMMERCE' in texto:
            return 'E-Commerce'
        if 'HOSPITALITY' in texto:
            return 'Hospitality'
        if texto in CANAL_CONSULTIVO_REMOTO_ALIASES:
            return 'Consultivo Remoto'
        return ""

    df_work['CANAL_CANONICO'] = df_work['CANAL_PLAN'].apply(_canal_canonico)
    df_work = df_work[
        (df_work['CANAL_CANONICO'] != "") &
        (df_work['PLATAFORMA_NORM'] == produto_norm)
    ].copy()

    def _agg_por_canal(coluna_valor: str, mes_alvo: str, indicador_alvo_norm: str) -> dict[str, float]:
        df_f = df_work[
            (df_work['MES_NORM'] == str(mes_alvo).strip().lower()) &
            (df_work['INDICADOR_NORM'] == indicador_alvo_norm)
        ]
        if df_f.empty:
            return {}
        return (
            df_f.groupby('CANAL_CANONICO', observed=True)[coluna_valor]
            .sum()
            .astype(float)
            .to_dict()
        )

    agg_m2 = _agg_por_canal('QTDE', mes_m2, indicador_real_norm)
    agg_m1 = _agg_por_canal('QTDE', mes_m1, indicador_real_norm)
    agg_m0_real = _agg_por_canal('QTDE', mes_ref_norm, indicador_real_norm)
    agg_m0_tend = _agg_por_canal('TEND_QTD', mes_ref_norm, indicador_real_norm)
    agg_meta = _agg_por_canal('DESAFIO_QTD', mes_ref_norm, indicador_meta_norm)
    usar_tendencia_mes = mes_ref_norm == get_mes_atual_formatado().strip().lower()

    def _lookup_mensal_por_canal(coluna_valor: str, indicador_alvo_norm: str) -> dict[tuple[str, str], float]:
        df_f = df_work[df_work['INDICADOR_NORM'] == indicador_alvo_norm]
        if df_f.empty:
            return {}
        return (
            df_f.groupby(['CANAL_CANONICO', 'MES_NORM'], observed=True)[coluna_valor]
            .sum()
            .astype(float)
            .to_dict()
        )

    lookup_real_canal_mes = _lookup_mensal_por_canal('QTDE', indicador_real_norm)
    lookup_tend_canal_mes = _lookup_mensal_por_canal('TEND_QTD', indicador_real_norm)
    lookup_meta_canal_mes = _lookup_mensal_por_canal('DESAFIO_QTD', indicador_meta_norm)

    def _lookup_canal(mapa_ref: dict[tuple[str, str], float], canal_ref: str) -> dict[str, float]:
        return {
            str(mes_ref_key).strip().lower(): float(valor or 0.0)
            for (canal_key, mes_ref_key), valor in mapa_ref.items()
            if str(canal_key).strip() == str(canal_ref).strip()
        }

    rows: list[dict[str, float | str]] = []
    for canal in canais_ordem:
        val_m2 = float(agg_m2.get(canal, 0.0))
        val_m1 = float(agg_m1.get(canal, 0.0))
        val_m0_real = float(agg_m0_real.get(canal, 0.0))
        val_m0_tend = float(agg_m0_tend.get(canal, 0.0))
        val_m0 = val_m0_tend if (usar_tendencia_mes and val_m0_tend > 0) else val_m0_real
        val_meta = float(agg_meta.get(canal, 0.0))
        mom = (((val_m0 / val_m1) - 1.0) * 100.0) if val_m1 > 0 else 0.0
        var_meta = (((val_m0 / val_meta) - 1.0) * 100.0) if val_meta > 0 else 0.0
        lookup_real_canal = _lookup_canal(lookup_real_canal_mes, canal)
        lookup_tend_canal = _lookup_canal(lookup_tend_canal_mes, canal)
        lookup_meta_canal = _lookup_canal(lookup_meta_canal_mes, canal)
        yoy_ytd = calcular_yoy_ytd_mensal_lookup(
            lookup_real_canal,
            lookup_tend_canal,
            mes_ref_norm,
            lookup_orc=lookup_meta_canal
        )
        val_yoy_base = float(lookup_real_canal.get(get_mes_ano_anterior(mes_ref_norm), 0.0))

        rows.append({
            'CANAL_PLAN': canal,
            'MES_M2': val_m2,
            'MES_M1': val_m1,
            'MES_ATUAL_TEND': val_m0,
            'MOM': mom,
            'YOY': yoy_ytd['YOY'],
            'YTD25': yoy_ytd['YTD25'],
            'YTD26': yoy_ytd['YTD26'],
            'YTD_ORC': yoy_ytd['YTD_ORÇ'],
            'VAR_YTD': yoy_ytd['YTD26 vs YTD25'],
            'VAR_YTD_ORC': yoy_ytd['YTD26 vs YTD_ORÇ'],
            'MES_YOY_BASE': val_yoy_base,
            'META': val_meta,
            'VAR_META': var_meta
        })

    return pd.DataFrame(rows, columns=colunas_retorno)

def _colunas_tabela_resultado_canais(
    mes_ref: str,
    mes_m1: str,
    mes_m2: str,
    produto_ref: str | None = None
) -> list[str]:
    produto_label = str(produto_ref or "").strip().upper()
    if produto_label not in {"CONTA", "FIXA"}:
        produto_label = "CANAL_PLAN"
    mes_yoy_label = str(get_mes_ano_anterior(mes_ref)).strip().upper()
    return [
        produto_label,
        mes_yoy_label,
        str(mes_m2).strip().upper(),
        str(mes_m1).strip().upper(),
        str(mes_ref).strip().upper(),
        'MoM',
        'YoY',
        'YTD25',
        'YTD26',
        'YTD_ORÇ',
        'YTD26 vs YTD25',
        'YTD26 vs YTD_ORÇ',
        'Orç',
        'Var Orç'
    ]

def montar_tabela_resultado_canais_exibicao_numerica(
    df_tabela: pd.DataFrame,
    mes_ref: str,
    mes_m1: str,
    mes_m2: str,
    produto_ref: str | None = None,
    incluir_total: bool = True
) -> pd.DataFrame:
    colunas_saida = _colunas_tabela_resultado_canais(mes_ref, mes_m1, mes_m2, produto_ref)
    if df_tabela is None or df_tabela.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_num = pd.DataFrame({
        colunas_saida[0]: df_tabela['CANAL_PLAN'].astype(str),
        colunas_saida[1]: normalizar_numerico_serie(df_tabela.get('MES_YOY_BASE', 0)).fillna(0.0),
        colunas_saida[2]: normalizar_numerico_serie(df_tabela['MES_M2']).fillna(0.0),
        colunas_saida[3]: normalizar_numerico_serie(df_tabela['MES_M1']).fillna(0.0),
        colunas_saida[4]: normalizar_numerico_serie(df_tabela['MES_ATUAL_TEND']).fillna(0.0),
        colunas_saida[5]: normalizar_numerico_serie(df_tabela['MOM']).fillna(0.0),
        colunas_saida[6]: normalizar_numerico_serie(df_tabela['YOY']).fillna(0.0),
        colunas_saida[7]: normalizar_numerico_serie(df_tabela['YTD25']).fillna(0.0),
        colunas_saida[8]: normalizar_numerico_serie(df_tabela['YTD26']).fillna(0.0),
        colunas_saida[9]: normalizar_numerico_serie(df_tabela['YTD_ORC']).fillna(0.0),
        colunas_saida[10]: normalizar_numerico_serie(df_tabela['VAR_YTD']).fillna(0.0),
        colunas_saida[11]: normalizar_numerico_serie(df_tabela['VAR_YTD_ORC']).fillna(0.0),
        colunas_saida[12]: normalizar_numerico_serie(df_tabela['META']).fillna(0.0),
        colunas_saida[13]: normalizar_numerico_serie(df_tabela['VAR_META']).fillna(0.0)
    })

    if incluir_total and not df_num.empty:
        total_m12 = float(pd.to_numeric(df_num[colunas_saida[1]], errors='coerce').fillna(0.0).sum())
        total_m2 = float(pd.to_numeric(df_num[colunas_saida[2]], errors='coerce').fillna(0.0).sum())
        total_m1 = float(pd.to_numeric(df_num[colunas_saida[3]], errors='coerce').fillna(0.0).sum())
        total_m0 = float(pd.to_numeric(df_num[colunas_saida[4]], errors='coerce').fillna(0.0).sum())
        total_ytd25 = float(pd.to_numeric(df_num[colunas_saida[7]], errors='coerce').fillna(0.0).sum())
        total_ytd26 = float(pd.to_numeric(df_num[colunas_saida[8]], errors='coerce').fillna(0.0).sum())
        total_ytd_orc = float(pd.to_numeric(df_num[colunas_saida[9]], errors='coerce').fillna(0.0).sum())
        total_meta = float(pd.to_numeric(df_num[colunas_saida[12]], errors='coerce').fillna(0.0).sum())
        mom_total = (((total_m0 / total_m1) - 1.0) * 100.0) if total_m1 > 0 else 0.0
        total_yoy_base = float(pd.to_numeric(df_tabela.get('MES_YOY_BASE', 0), errors='coerce').fillna(0.0).sum())
        yoy_total = calcular_variacao_percentual(total_m0, total_yoy_base)
        var_ytd_total = calcular_variacao_percentual(total_ytd26, total_ytd25)
        var_ytd_orc_total = calcular_variacao_percentual(total_ytd26, total_ytd_orc)
        var_meta_total = (((total_m0 / total_meta) - 1.0) * 100.0) if total_meta > 0 else 0.0

        linha_total = {
            colunas_saida[0]: 'NACIONAIS',
            colunas_saida[1]: total_m12,
            colunas_saida[2]: total_m2,
            colunas_saida[3]: total_m1,
            colunas_saida[4]: total_m0,
            colunas_saida[5]: mom_total,
            colunas_saida[6]: yoy_total,
            colunas_saida[7]: total_ytd25,
            colunas_saida[8]: total_ytd26,
            colunas_saida[9]: total_ytd_orc,
            colunas_saida[10]: var_ytd_total,
            colunas_saida[11]: var_ytd_orc_total,
            colunas_saida[12]: total_meta,
            colunas_saida[13]: var_meta_total
        }
        df_num = pd.concat([pd.DataFrame([linha_total]), df_num], ignore_index=True)

    return df_num

def formatar_tabela_resultado_canais(
    df_tabela: pd.DataFrame,
    mes_ref: str,
    mes_m1: str,
    mes_m2: str,
    produto_ref: str | None = None,
    incluir_total: bool = True
) -> pd.DataFrame:
    df_num = montar_tabela_resultado_canais_exibicao_numerica(
        df_tabela=df_tabela,
        mes_ref=mes_ref,
        mes_m1=mes_m1,
        mes_m2=mes_m2,
        produto_ref=produto_ref,
        incluir_total=incluir_total
    )
    if df_num.empty:
        return df_num

    colunas = list(df_num.columns)
    colunas_percentuais = {'MoM', 'YoY', 'YTD26 vs YTD25', 'YTD26 vs YTD_ORÇ', 'Var Orç'}
    df_fmt = df_num.copy().astype(object)

    def _fmt_pct(valor: float) -> str:
        try:
            return f"{float(valor):+.1f}%".replace('.', ',')
        except Exception:
            return "0,0%"

    for col in colunas:
        if col == colunas[0]:
            continue
        if col in colunas_percentuais:
            df_fmt[col] = pd.to_numeric(df_fmt[col], errors='coerce').fillna(0.0).apply(_fmt_pct)
        else:
            df_fmt[col] = pd.to_numeric(df_fmt[col], errors='coerce').fillna(0.0).apply(
                lambda x: formatar_numero_brasileiro(x, 0)
            )

    return df_fmt

def criar_tabela_html_resultado_canais(df_formatado: pd.DataFrame, df_numerico: pd.DataFrame, table_id: str) -> str:
    """Cria tabela HTML no padrão visual do dashboard para resultado por canal."""
    if df_formatado is None or df_formatado.empty:
        return ""

    colunas = list(df_formatado.columns)
    total_colunas = max(len(colunas), 1)
    if total_colunas == 1:
        larguras_colunas = [100.0]
    else:
        largura_canal_pct = 13.0
        largura_num_pct = (100.0 - largura_canal_pct) / float(total_colunas - 1)
        larguras_colunas = [largura_canal_pct] + [largura_num_pct] * (total_colunas - 1)
    colgroup_html = "<colgroup>" + "".join(
        [f'<col style="width:{w:.4f}%;">' for w in larguras_colunas]
    ) + "</colgroup>"
    col_canal = colunas[0] if colunas else 'CANAL_PLAN'
    col_meta = 'Orç'
    colunas_var = {'MoM', 'YoY', 'YTD26 vs YTD25', 'YTD26 vs YTD_ORÇ', 'Var Orç'}

    def _serie_fonte_coluna(coluna_nome: str) -> pd.Series:
        if df_numerico is not None and not df_numerico.empty and coluna_nome in df_numerico.columns:
            return df_numerico[coluna_nome]
        if coluna_nome in df_formatado.columns:
            return df_formatado[coluna_nome]
        return pd.Series([None] * len(df_formatado), index=df_formatado.index)

    def _fmt_int_dashboard(valor) -> str:
        try:
            valor_num = float(pd.to_numeric(pd.Series([valor]), errors='coerce').fillna(0.0).iloc[0])
        except Exception:
            valor_num = 0.0
        return formatar_numero_brasileiro(valor_num, 0)

    def _fmt_pct_dashboard(valor) -> str:
        try:
            valor_num = float(pd.to_numeric(pd.Series([valor]), errors='coerce').fillna(0.0).iloc[0])
        except Exception:
            valor_num = 0.0
        return f"{valor_num:+.1f}%".replace('.', ',')

    df_display = pd.DataFrame(index=df_formatado.index)
    for coluna_nome in colunas:
        serie_fonte = _serie_fonte_coluna(coluna_nome)
        if coluna_nome == col_canal:
            df_display[coluna_nome] = serie_fonte.astype(str)
        elif coluna_nome in colunas_var:
            df_display[coluna_nome] = serie_fonte.apply(_fmt_pct_dashboard)
        else:
            df_display[coluna_nome] = serie_fonte.apply(_fmt_int_dashboard)

    html = f"""
    <style>
        #{table_id}.tabela-container-resultado-canais {{
            width: 100%;
            max-height: 500px;
            overflow-y: auto;
            overflow-x: auto;
            border: 2px solid #790E09;
            border-radius: 0;
            box-shadow: 0 4px 20px rgba(121, 14, 9, 0.14);
            font-family: 'Manrope', 'Segoe UI', sans-serif;
            margin: 10px 0 18px 0;
            background: #FFFFFF;
        }}
        #{table_id} .tabela-resultado-canais {{
            width: 100%;
            min-width: 1180px;
            border-collapse: collapse;
            border-spacing: 0;
            font-size: 11.5px;
            line-height: 1.16;
            table-layout: fixed;
            font-family: 'Manrope', 'Segoe UI', sans-serif;
        }}
        #{table_id} .tabela-resultado-canais thead {{
            position: sticky;
            top: 0;
            z-index: 100;
        }}
        #{table_id} .tabela-resultado-canais th {{
            background: linear-gradient(135deg, #790E09 0%, #5A0A06 100%) !important;
            color: #FFFFFF !important;
            font-weight: 700;
            padding: 6px 4px;
            text-align: center;
            vertical-align: middle !important;
            border-bottom: 0 !important;
            border-right: 1px solid #FFFFFF;
            white-space: normal;
            overflow: visible;
            text-overflow: clip;
            overflow-wrap: break-word;
            word-break: normal;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            line-height: 1.2;
            font-size: 10px;
        }}
        #{table_id} .tabela-resultado-canais th.col-var {{
            background: linear-gradient(135deg, #5A6268 0%, #3E444A 100%) !important;
        }}
        #{table_id} .tabela-resultado-canais th.col-meta {{
            background: linear-gradient(135deg, #A23B36 0%, #790E09 100%) !important;
        }}
        #{table_id} .tabela-resultado-canais td {{
            padding: 6px 4px 4px 4px;
            text-align: right;
            vertical-align: bottom !important;
            border-bottom: 1px solid #FFFFFF;
            border-right: 1px solid #FFFFFF;
            font-weight: 400;
            font-variant-numeric: tabular-nums;
            color: #1F2937;
            font-size: 11px;
            line-height: 1.16;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        #{table_id} .tabela-resultado-canais tbody td,
        #{table_id} .tabela-resultado-canais tbody td * {{
            font-weight: 400 !important;
        }}
        #{table_id} .tabela-resultado-canais tbody tr td {{
            vertical-align: bottom !important;
        }}
        #{table_id} .tabela-resultado-canais td.col-canal {{
            text-align: left;
            color: #2F3747;
            background: transparent !important;
            padding-left: 5px;
            white-space: normal;
            overflow-wrap: anywhere;
            word-break: break-word;
            overflow: visible;
            text-overflow: clip;
        }}
        #{table_id} .linha-canal-resultado.linha-zebra-par td {{
            background: linear-gradient(135deg, #FCFCFD 0%, #F7F8FA 100%) !important;
        }}
        #{table_id} .linha-canal-resultado.linha-zebra-impar td {{
            background: linear-gradient(135deg, #FFFFFF 0%, #FAFBFC 100%) !important;
        }}
        #{table_id} .linha-canal-resultado.linha-zebra-par:hover,
        #{table_id} .linha-canal-resultado.linha-zebra-impar:hover {{
            background: linear-gradient(135deg, #FFF6F3 0%, #FAF0ED 100%) !important;
            box-shadow: inset 0 0 0 1px rgba(162, 59, 54, 0.12);
        }}
        #{table_id} .linha-canal-resultado.linha-zebra-par:hover td,
        #{table_id} .linha-canal-resultado.linha-zebra-impar:hover td {{
            background: linear-gradient(135deg, #FFF6F3 0%, #FAF0ED 100%) !important;
        }}
        #{table_id} .tabela-resultado-canais td.col-meta {{
            background: linear-gradient(180deg, rgba(121, 14, 9, 0.06) 0%, rgba(121, 14, 9, 0.022) 100%) !important;
            color: #6B1F1A;
            font-weight: 600;
            border-left: 1px solid rgba(121, 14, 9, 0.08) !important;
            border-right: 1px solid rgba(121, 14, 9, 0.08) !important;
        }}
        #{table_id} .tabela-resultado-canais td.col-var {{
            position: relative;
            padding-left: 13px !important;
            background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
            border-left: 1px solid rgba(90, 98, 104, 0.08) !important;
            border-right: 1px solid rgba(90, 98, 104, 0.08) !important;
        }}
        #{table_id} .tabela-resultado-canais td.col-var.status-positivo {{
            color: #1B5E20 !important;
            background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
            font-weight: 700;
        }}
        #{table_id} .tabela-resultado-canais td.col-var.status-positivo::before {{
            content: "▲";
            position: absolute;
            left: 4px;
            top: 50%;
            transform: translateY(-50%);
            color: #2E7D32;
            font-size: 8px;
        }}
        #{table_id} .tabela-resultado-canais td.col-var.status-negativo {{
            color: #B71C1C !important;
            background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
            font-weight: 700;
        }}
        #{table_id} .tabela-resultado-canais td.col-var.status-negativo::before {{
            content: "▼";
            position: absolute;
            left: 4px;
            top: 50%;
            transform: translateY(-50%);
            color: #C62828;
            font-size: 8px;
        }}
        #{table_id} .tabela-resultado-canais td.col-var.status-neutro {{
            color: #666666 !important;
            background: linear-gradient(180deg, rgba(90, 98, 104, 0.08) 0%, rgba(90, 98, 104, 0.03) 100%) !important;
            font-weight: 500;
        }}
        #{table_id} .linha-total-resultado {{
            position: sticky;
            top: 34px;
            z-index: 95;
            border-top: 0 !important;
            border-bottom: 2px solid #790E09;
        }}
        #{table_id} .tabela-resultado-canais tbody tr.linha-canal-resultado td {{
            min-height: 24px;
            padding-top: 7px !important;
            padding-bottom: 3px !important;
        }}
        #{table_id} .linha-total-resultado td {{
            background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
            color: #FFFFFF !important;
            font-weight: 700;
            border-top: 0 !important;
            border-right: 1px solid rgba(255, 255, 255, 0.12) !important;
            padding: 6px 4px 4px 4px !important;
            font-size: 11px;
            vertical-align: bottom !important;
        }}
        #{table_id} .linha-total-resultado td.col-canal {{
            background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
            z-index: 80;
        }}
        #{table_id} .linha-total-resultado td.col-meta,
        #{table_id} .linha-total-resultado td.col-var.status-positivo,
        #{table_id} .linha-total-resultado td.col-var.status-negativo,
        #{table_id} .linha-total-resultado td.col-var.status-neutro {{
            background: linear-gradient(135deg, #5A0A06 0%, #3D0704 100%) !important;
            color: #FFFFFF !important;
        }}
        #{table_id} .linha-total-resultado td.col-var::before {{
            content: "" !important;
        }}
        #{table_id} .linha-total-resultado td.col-var.status-positivo::before,
        #{table_id} .linha-total-resultado td.col-var.status-negativo::before,
        #{table_id} .linha-total-resultado td.col-var.status-neutro::before {{
            content: "" !important;
        }}
    </style>
    <div id="{table_id}" class="tabela-container-resultado-canais">
    <table class="tabela-resultado-canais">
    {colgroup_html}
    <thead><tr>
    """

    for col in colunas:
        classes = []
        if col == col_meta:
            classes.append("col-meta")
        elif col in colunas_var:
            classes.append("col-var")
        html += f'<th class="{" ".join(classes)}">{escape(str(col))}</th>'
    html += "</tr></thead><tbody>"

    idx_linha_canal = 0
    for idx, row in df_display.iterrows():
        canal_ref = str(df_numerico.iloc[idx, 0]) if idx < len(df_numerico) else str(row.iloc[0])
        canal_ref_norm = canal_ref.strip().upper()
        is_total = canal_ref_norm.startswith("TOTAL") or canal_ref_norm.startswith("NACIONAIS")
        if is_total:
            classe_linha = "linha-total-resultado"
        else:
            classe_zebra = "linha-zebra-par" if (idx_linha_canal % 2 == 0) else "linha-zebra-impar"
            classe_linha = f"linha-canal-resultado {classe_zebra}"
            idx_linha_canal += 1
        html += f'<tr class="{classe_linha}">'

        for col_idx, col in enumerate(colunas):
            valor_fmt = escape(str(row[col]))
            classes = []

            if col == col_canal:
                classes.append("col-canal")
            elif col == col_meta:
                classes.append("col-meta")
            elif col in colunas_var:
                classes.append("col-var")
                try:
                    valor_raw = float(df_numerico.iloc[idx, col_idx])
                    if valor_raw > 0:
                        classes.append("status-positivo")
                    elif valor_raw < 0:
                        classes.append("status-negativo")
                    else:
                        classes.append("status-neutro")
                except Exception:
                    classes.append("status-neutro")

            classe_celula = " ".join(classes)
            html += f'<td class="{classe_celula}">{valor_fmt}</td>'

        html += "</tr>"

    html += "</tbody></table></div>"
    return html


@st.cache_data(show_spinner=False, max_entries=3, ttl=1800)
def cached_tabela_html_funil_cotacoes(df_fmt_json: str, df_num_json: str, table_id: str) -> str:
    return criar_tabela_html_funil_cotacoes(
        desserializar_dataframe_cache(df_fmt_json),
        desserializar_dataframe_cache(df_num_json),
        table_id
    )


@st.cache_data(show_spinner=False, max_entries=3, ttl=1800)
def cached_tabela_html_analitica(df_fmt_json: str, df_num_json: str, table_id: str) -> str:
    return criar_tabela_html_analitica(
        desserializar_dataframe_cache(df_fmt_json),
        desserializar_dataframe_cache(df_num_json),
        table_id
    )


@st.cache_data(show_spinner=False, max_entries=3, ttl=1800)
def cached_tabela_html_resultado_canais(df_fmt_json: str, df_num_json: str, table_id: str) -> str:
    return criar_tabela_html_resultado_canais(
        desserializar_dataframe_cache(df_fmt_json),
        desserializar_dataframe_cache(df_num_json),
        table_id
    )

def validate_data(df):
    """Valida se as colunas necessárias existem no dataset"""
    required_columns = ['REGIONAL', 'CANAL_PLAN', 'dat_tratada', 'DSC_INDICADOR', 'QTDE', 'COD_PLATAFORMA', 'DAT_MOVIMENTO2', 'DESAFIO_QTD']
    missing_columns = [col for col in required_columns if col not in df.columns]
    
    if missing_columns:
        st.error(f"Colunas faltando no dataset: {missing_columns}")
        st.stop()
    
    critical_columns = ['REGIONAL', 'CANAL_PLAN', 'dat_tratada', 'QTDE']
    null_counts = df[critical_columns].isnull().sum()
    
    if null_counts.sum() > 0:
        st.warning(f"Valores nulos encontrados:\n{null_counts[null_counts > 0]}")
    
    return True



# ==============================
# BLOCO ADICIONAL - FUNIL FIXA E-COMMERCE
# ==============================
FUNIL_FIXA_FILE_PATH = resolver_arquivo_dashboard(
    resolver_arquivo_preprocessado("funil_fixa_ecommerce.parquet"),
    RAW_FUNIL_FIXA_FILE_PATH
)

TEND_FUNIL_FIXA_FILE_PATH = resolver_arquivo_dashboard(
    resolver_arquivo_preprocessado("tend_funil_fixa.parquet"),
    RAW_TEND_FUNIL_FIXA_FILE_PATH
)

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
FUNIL_FIXA_INDICADOR_LABELS = {
    chave: label for chave, label, _ in FUNIL_FIXA_INDICADORES_CONFIG
}
FUNIL_FIXA_INDICADOR_ORDENS = {
    chave: ordem for chave, _, ordem in FUNIL_FIXA_INDICADORES_CONFIG
}
FUNIL_FIXA_INDICADORES_PERCENTUAIS = [
    ("SESSOES", "SESSÕES"),
    ("PORTEIRA_CEP", "PORTEIRA CEP"),
    ("DADOS_PESSOAIS", "DADOS PESSOAIS"),
    ("ENDERECO", "ENDEREÇO"),
    ("PAGAMENTO", "PAGAMENTO"),
    ("PEDIDOS_TOTAL", "PEDIDOS_TOTAL"),
    ("REJEITADO", "REJEITADO"),
    ("VENDA_BRUTA", "VENDA BRUTA"),
    ("DESISTENCIA", "DESISTÊNCIA"),
    ("INSTALACAO", "INSTALAÇÃO"),
]
FUNIL_FIXA_BASE_PERCENTUAL_ESPECIAL = {
    "REJEITADO": "PEDIDOS_TOTAL",
    "VENDA_BRUTA": "PEDIDOS_TOTAL",
    "DESISTENCIA": "VENDA_BRUTA",
    "INSTALACAO": "VENDA_BRUTA",
}


def _normalizar_chave_funil_fixa(valor) -> str:
    if pd.isna(valor):
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = texto.encode("ASCII", "ignore").decode("ASCII")
    texto = texto.strip().upper()
    texto = re.sub(r"[^A-Z0-9]+", "_", texto)
    return re.sub(r"_+", "_", texto).strip("_")


def _encontrar_coluna_funil_fixa(colunas, *aliases: str) -> str | None:
    mapa = {}
    # Normalizar entrada: garantir que podemos iterar sobre `colunas`.
    # Evitar avaliar a truthiness de objetos como pandas.Index (causa ValueError).
    if colunas is None:
        iterable_colunas = []
    else:
        try:
            iterable_colunas = list(colunas)
        except Exception:
            # Tentativa de recuperar via tolist (Index/array-like)
            try:
                iterable_colunas = list(colunas.tolist())
            except Exception:
                iterable_colunas = []

    for coluna in iterable_colunas:
        try:
            chave = _normalizar_chave_funil_fixa(coluna)
        except Exception:
            chave = ""
        if chave and chave not in mapa:
            mapa[chave] = coluna
    for alias in aliases:
        chave_alias = _normalizar_chave_funil_fixa(alias)
        if chave_alias in mapa:
            return mapa[chave_alias]
    return None


def _formatar_mes_ano_funil_fixa(data_ref) -> str:
    try:
        ts = pd.Timestamp(data_ref)
        if pd.isna(ts):
            return ""
        meses_pt = {
            1: 'jan', 2: 'fev', 3: 'mar', 4: 'abr', 5: 'mai', 6: 'jun',
            7: 'jul', 8: 'ago', 9: 'set', 10: 'out', 11: 'nov', 12: 'dez'
        }
        return f"{meses_pt.get(int(ts.month), 'jan')}/{str(int(ts.year))[-2:]}"
    except Exception:
        return ""


def _normalizar_segmento_funil_fixa(valor) -> str:
    chave = _normalizar_chave_funil_fixa(valor)
    if chave == "RESIDENCIAL_CABO":
        return "PF"
    if chave == "PF":
        return "PF"
    if chave == "PME":
        return "PME"
    return str(valor).strip()


def _normalizar_indicadores_funil_fixa_df(df_funil: pd.DataFrame) -> pd.DataFrame:
    if df_funil is None or df_funil.empty or 'INDICADOR' not in df_funil.columns:
        return df_funil

    df = df_funil.copy()
    if 'INDICADOR_CHAVE' in df.columns:
        chave_base = df['INDICADOR_CHAVE'].astype('object')
        mask_chave_vazia = chave_base.isna() | chave_base.astype(str).str.strip().eq('')
        chave_base.loc[mask_chave_vazia] = df.loc[mask_chave_vazia, 'INDICADOR']
    else:
        chave_base = df['INDICADOR']

    df['INDICADOR_CHAVE'] = chave_base.map(_normalizar_chave_funil_fixa)
    df = df[df['INDICADOR_CHAVE'].isin(FUNIL_FIXA_INDICADOR_LABELS.keys())].copy()
    if df.empty:
        return df

    df['INDICADOR'] = df['INDICADOR_CHAVE'].map(FUNIL_FIXA_INDICADOR_LABELS)
    if 'INDICADOR_ORDEM' not in df.columns:
        df['INDICADOR_ORDEM'] = 999.0
    df['INDICADOR_ORDEM'] = normalizar_numerico_serie(df['INDICADOR_ORDEM']).fillna(999.0)
    df['INDICADOR_ORDEM'] = (
        df['INDICADOR_CHAVE']
        .map(FUNIL_FIXA_INDICADOR_ORDENS)
        .fillna(df['INDICADOR_ORDEM'])
        .astype(float)
    )
    return df


@st.cache_data(ttl=3600, show_spinner=False, max_entries=1)
def load_tend_funil_fixa_data(path: str, file_mtime: float | None = None) -> pd.DataFrame:
    _ = file_mtime
    path_obj = Path(path)
    if not path_obj.exists():
        return pd.DataFrame()

    if path_obj.suffix.lower() == ".parquet":
        df_opt = _carregar_dataframe_preprocessado(
            str(path_obj),
            file_mtime,
            required_cols={'SEGMENTO', 'INDICADOR', 'PERIODO_MES', 'QTDE', 'INDICADOR_CHAVE', 'INDICADOR_ORDEM', 'MES_ANO', 'MES_ANO_ORDEM'},
            text_cols=['SEGMENTO', 'INDICADOR', 'INDICADOR_CHAVE', 'MES_ANO'],
            numeric_cols=['QTDE', 'INDICADOR_ORDEM', 'MES_ANO_ORDEM'],
            date_cols=['PERIODO_MES'],
            category_cols=['SEGMENTO', 'INDICADOR', 'INDICADOR_CHAVE', 'MES_ANO']
        )
        if not df_opt.empty:
            df_opt = _normalizar_indicadores_funil_fixa_df(df_opt)
            df_opt['MES_ANO_ORDEM'] = normalizar_numerico_serie(df_opt['MES_ANO_ORDEM']).fillna(0).astype(int)
            return df_opt

    df_raw = pd.read_excel(path_obj, engine='openpyxl')
    if df_raw is None or df_raw.empty:
        return pd.DataFrame()

    col_segmento = _encontrar_coluna_funil_fixa(df_raw.columns, 'SEGMENTO')
    col_indicador = _encontrar_coluna_funil_fixa(df_raw.columns, 'INDICADOR')
    col_periodo = _encontrar_coluna_funil_fixa(df_raw.columns, 'PERIODO', 'PERIODO_MES', 'PERIODO MES')
    col_qtde = _encontrar_coluna_funil_fixa(df_raw.columns, 'QTDE')
    obrigatorias = [col_segmento, col_indicador, col_periodo, col_qtde]
    if any(col is None for col in obrigatorias):
        return pd.DataFrame()

    df = df_raw.rename(columns={
        col_segmento: 'SEGMENTO',
        col_indicador: 'INDICADOR',
        col_periodo: 'PERIODO_MES',
        col_qtde: 'QTDE',
    })[['SEGMENTO', 'INDICADOR', 'PERIODO_MES', 'QTDE']].copy()

    df['SEGMENTO'] = df['SEGMENTO'].map(_normalizar_segmento_funil_fixa)
    df['INDICADOR_CHAVE'] = df['INDICADOR'].map(_normalizar_chave_funil_fixa)
    df = df[df['SEGMENTO'].isin(['PF', 'PME'])].copy()
    df = _normalizar_indicadores_funil_fixa_df(df)
    if df.empty:
        return pd.DataFrame()

    df['PERIODO_MES'] = pd.to_datetime(
        df['PERIODO_MES'],
        format='mixed',
        errors='coerce',
        dayfirst=True
    )
    df['QTDE'] = normalizar_numerico_serie(df['QTDE']).fillna(0.0)
    df = df[df['PERIODO_MES'].notna()].copy()
    df['MES_ANO'] = df['PERIODO_MES'].apply(_formatar_mes_ano_funil_fixa).astype(str).str.strip().str.lower()
    df['MES_ANO_ORDEM'] = (df['PERIODO_MES'].dt.year * 100 + df['PERIODO_MES'].dt.month).astype(int)
    return df


def _calcular_pesos_origem_tend_funil_fixa(
    df_base: pd.DataFrame,
    segmento_ref: str,
    indicador_ref: str,
    mes_ref_ordem: int,
    qtd_meses_hist: int = 3
) -> pd.Series:
    if df_base is None or df_base.empty:
        return pd.Series(dtype=float)

    base_ref = df_base[
        df_base['SEGMENTO'].astype(str).eq(str(segmento_ref)) &
        df_base['INDICADOR'].astype(str).eq(str(indicador_ref))
    ].copy()
    if base_ref.empty:
        return pd.Series(dtype=float)

    colunas_peso = ['ORIGEM_AGG']
    if 'CANAL_ENTRADA' in base_ref.columns:
        colunas_peso.append('CANAL_ENTRADA')

    meses_hist = sorted(
        base_ref.loc[
            pd.to_numeric(base_ref['MES_ANO_ORDEM'], errors='coerce').fillna(0).astype(int).lt(int(mes_ref_ordem)),
            'MES_ANO_ORDEM'
        ].dropna().astype(int).unique().tolist()
    )[-max(int(qtd_meses_hist), 1):]

    base_hist = base_ref[base_ref['MES_ANO_ORDEM'].isin(meses_hist)].copy()
    if not base_hist.empty:
        tabela_hist = base_hist.pivot_table(
            index=colunas_peso,
            columns='MES_ANO_ORDEM',
            values='QTDE',
            aggfunc='sum',
            fill_value=0.0
        )
        pesos = pd.to_numeric(tabela_hist.mean(axis=1), errors='coerce').fillna(0.0)
        pesos = pesos[pesos.gt(0)]
        if not pesos.empty and float(pesos.sum()) > 0:
            return pesos / float(pesos.sum())

    base_prev = base_ref[
        pd.to_numeric(base_ref['MES_ANO_ORDEM'], errors='coerce').fillna(0).astype(int).lt(int(mes_ref_ordem))
    ].copy()
    if not base_prev.empty:
        pesos = pd.to_numeric(
            base_prev.groupby(colunas_peso, observed=True)['QTDE'].sum(),
            errors='coerce'
        ).fillna(0.0)
        pesos = pesos[pesos.gt(0)]
        if not pesos.empty and float(pesos.sum()) > 0:
            return pesos / float(pesos.sum())

    base_mes = base_ref[
        pd.to_numeric(base_ref['MES_ANO_ORDEM'], errors='coerce').fillna(0).astype(int).eq(int(mes_ref_ordem))
    ].copy()
    if not base_mes.empty:
        pesos = pd.to_numeric(
            base_mes.groupby(colunas_peso, observed=True)['QTDE'].sum(),
            errors='coerce'
        ).fillna(0.0)
        pesos = pesos[pesos.gt(0)]
        if not pesos.empty and float(pesos.sum()) > 0:
            return pesos / float(pesos.sum())

    combos_hist = (
        base_ref[colunas_peso]
        .dropna()
        .astype(str)
        .apply(lambda col: col.str.strip())
        .drop_duplicates()
    )
    if not combos_hist.empty:
        peso_uniforme = 1.0 / float(len(combos_hist))
        if len(colunas_peso) == 1:
            return pd.Series(
                {str(linha['ORIGEM_AGG']).strip(): peso_uniforme for _, linha in combos_hist.iterrows()},
                dtype=float
            )
        return pd.Series(
            {
                (str(linha['ORIGEM_AGG']).strip(), str(linha['CANAL_ENTRADA']).strip()): peso_uniforme
                for _, linha in combos_hist.iterrows()
            },
            dtype=float
        )

    return pd.Series({'DEMAIS': 1.0}, dtype=float)


def _aplicar_tend_funil_fixa(df_funil: pd.DataFrame, df_tend: pd.DataFrame) -> pd.DataFrame:
    if df_funil is None or df_funil.empty:
        return pd.DataFrame()

    base = df_funil.copy()
    base['EH_TEND'] = pd.to_numeric(base.get('EH_TEND', 0), errors='coerce').fillna(0).astype(int)
    base = _normalizar_indicadores_funil_fixa_df(base)
    if df_tend is None or df_tend.empty:
        return base

    df_tend = _normalizar_indicadores_funil_fixa_df(df_tend)
    if df_tend is None or df_tend.empty:
        return base

    base_sem_tend = base.copy()
    chaves_tend = (
        df_tend[['SEGMENTO', 'INDICADOR', 'MES_ANO_ORDEM']]
        .drop_duplicates()
        .to_dict('records')
    )
    for chave in chaves_tend:
        segmento_ref = str(chave['SEGMENTO']).strip()
        indicador_ref = str(chave['INDICADOR']).strip()
        mes_ref_ordem = int(chave['MES_ANO_ORDEM'])
        base_sem_tend = base_sem_tend[
            ~(
                base_sem_tend['SEGMENTO'].astype(str).eq(segmento_ref) &
                base_sem_tend['INDICADOR'].astype(str).eq(indicador_ref) &
                pd.to_numeric(base_sem_tend['MES_ANO_ORDEM'], errors='coerce').fillna(0).astype(int).eq(mes_ref_ordem)
            )
        ].copy()

    linhas_tend = []
    for _, linha_tend in df_tend.iterrows():
        segmento_ref = str(linha_tend.get('SEGMENTO', '')).strip()
        indicador_ref = str(linha_tend.get('INDICADOR', '')).strip()
        indicador_chave = str(linha_tend.get('INDICADOR_CHAVE', '')).strip()
        indicador_ordem = float(pd.to_numeric(pd.Series([linha_tend.get('INDICADOR_ORDEM', 999.0)]), errors='coerce').fillna(999.0).iloc[0])
        mes_ref_ordem = int(pd.to_numeric(pd.Series([linha_tend.get('MES_ANO_ORDEM', 0)]), errors='coerce').fillna(0).iloc[0])
        qtde_tend = float(pd.to_numeric(pd.Series([linha_tend.get('QTDE', 0.0)]), errors='coerce').fillna(0.0).iloc[0])
        if qtde_tend <= 0 or mes_ref_ordem <= 0:
            continue

        pesos_origem = _calcular_pesos_origem_tend_funil_fixa(
            df_base=base,
            segmento_ref=segmento_ref,
            indicador_ref=indicador_ref,
            mes_ref_ordem=mes_ref_ordem,
            qtd_meses_hist=3
        )
        pesos_origem = pd.to_numeric(pesos_origem, errors='coerce').fillna(0.0)
        pesos_origem = pesos_origem[pesos_origem.gt(0)]
        if pesos_origem.empty:
            continue
        pesos_origem = pesos_origem / float(pesos_origem.sum())

        chaves_peso = list(pesos_origem.index)
        valores_alloc = [qtde_tend * float(pesos_origem.loc[chave_peso]) for chave_peso in chaves_peso]
        if valores_alloc:
            ajuste_final = qtde_tend - float(np.sum(valores_alloc))
            idx_maior = int(np.argmax(valores_alloc))
            valores_alloc[idx_maior] += ajuste_final

        periodo_ref = pd.Timestamp(linha_tend.get('PERIODO_MES'))
        mes_rotulo = str(linha_tend.get('MES_ANO', '')).strip().lower() or _formatar_mes_ano_funil_fixa(periodo_ref)
        for chave_peso, qtde_alloc in zip(chaves_peso, valores_alloc):
            if isinstance(chave_peso, tuple):
                origem_ref = chave_peso[0] if len(chave_peso) > 0 else 'DEMAIS'
                canal_entrada_ref = chave_peso[1] if len(chave_peso) > 1 else 'Não Informado'
            else:
                origem_ref = chave_peso
                canal_entrada_ref = 'Não Informado'
            linhas_tend.append({
                'SEGMENTO': segmento_ref,
                'ORIGEM_AGG': str(origem_ref).strip(),
                'CANAL_ENTRADA': str(canal_entrada_ref).strip(),
                'INDICADOR': indicador_ref,
                'PERIODO_MES': periodo_ref,
                'QTDE': float(qtde_alloc),
                'INDICADOR_ORDEM': indicador_ordem,
                'MES_ANO': mes_rotulo,
                'MES_ANO_ORDEM': mes_ref_ordem,
                'INDICADOR_CHAVE': indicador_chave,
                'EH_TEND': 1,
            })

    if not linhas_tend:
        return base

    df_tend_alloc = pd.DataFrame(linhas_tend)
    for coluna in base_sem_tend.columns:
        if coluna not in df_tend_alloc.columns:
            df_tend_alloc[coluna] = np.nan
    df_tend_alloc = df_tend_alloc[base_sem_tend.columns.tolist()]

    df_out = pd.concat([base_sem_tend, df_tend_alloc], ignore_index=True, sort=False)
    df_out['QTDE'] = normalizar_numerico_serie(df_out['QTDE']).fillna(0.0)
    df_out['MES_ANO_ORDEM'] = normalizar_numerico_serie(df_out['MES_ANO_ORDEM']).fillna(0).astype(int)
    df_out['INDICADOR_ORDEM'] = normalizar_numerico_serie(df_out['INDICADOR_ORDEM']).fillna(999.0)
    df_out['EH_TEND'] = pd.to_numeric(df_out.get('EH_TEND', 0), errors='coerce').fillna(0).astype(int)
    df_out = _normalizar_indicadores_funil_fixa_df(df_out)
    return df_out


@st.cache_data(ttl=3600, show_spinner=False, max_entries=1)
def load_funil_fixa_ecommerce_data(
    path: str,
    file_mtime: float | None = None,
    tend_path: str | None = None,
    tend_file_mtime: float | None = None
) -> pd.DataFrame:
    _ = file_mtime
    _ = tend_file_mtime
    path_obj = Path(path)
    if not path_obj.exists():
        return pd.DataFrame()

    if path_obj.suffix.lower() == ".parquet":
        df_opt = _carregar_dataframe_preprocessado(
            str(path_obj),
            file_mtime,
            required_cols={'SEGMENTO', 'ORIGEM_AGG', 'CANAL_ENTRADA', 'INDICADOR', 'PERIODO_MES', 'QTDE', 'INDICADOR_ORDEM', 'MES_ANO', 'MES_ANO_ORDEM'},
            text_cols=['SEGMENTO', 'ORIGEM_AGG', 'CANAL_ENTRADA', 'INDICADOR', 'MES_ANO', 'INDICADOR_CHAVE'],
            numeric_cols=['QTDE', 'INDICADOR_ORDEM', 'MES_ANO_ORDEM', 'EH_TEND'],
            date_cols=['PERIODO_MES'],
            category_cols=['SEGMENTO', 'ORIGEM_AGG', 'CANAL_ENTRADA', 'INDICADOR', 'MES_ANO', 'INDICADOR_CHAVE'],
            default_values={'EH_TEND': 0, 'CANAL_ENTRADA': 'Não Informado'}
        )
        if not df_opt.empty:
            df_opt = _normalizar_indicadores_funil_fixa_df(df_opt)
            df_opt['MES_ANO_ORDEM'] = normalizar_numerico_serie(df_opt['MES_ANO_ORDEM']).fillna(0).astype(int)
            df_opt['EH_TEND'] = pd.to_numeric(df_opt.get('EH_TEND', 0), errors='coerce').fillna(0).astype(int)

            tend_path_obj = resolver_arquivo_dashboard(
                Path(tend_path) if tend_path else TEND_FUNIL_FIXA_FILE_PATH,
                TEND_FUNIL_FIXA_FILE_PATH,
                path_obj.with_name('tend_funil_ecom.parquet'),
                path_obj.with_name('tend_funil_ecom.xlsx'),
                'tend_funil_ecom.parquet',
                'tend_funil_ecom.xlsx'
            )
            tend_path_obj = tend_path_obj if Path(tend_path_obj).exists() else None
            if tend_path_obj is not None:
                df_tend = load_tend_funil_fixa_data(str(tend_path_obj), tend_file_mtime)
                if not df_tend.empty:
                    df_opt = _aplicar_tend_funil_fixa(df_opt, df_tend)
            return df_opt

    df_raw = pd.read_excel(path_obj, engine='openpyxl')
    if df_raw is None or df_raw.empty:
        return pd.DataFrame()

    col_segmento = _encontrar_coluna_funil_fixa(df_raw.columns, 'SEGMENTO')
    col_origem = _encontrar_coluna_funil_fixa(df_raw.columns, 'ORIGEM_AGG', 'ORIGEM AGG')
    col_canal_entrada = _encontrar_coluna_funil_fixa(df_raw.columns, 'CANAL_ENTRADA', 'CANAL ENTRADA', 'CANAL DE ENTRADA')
    col_indicador = _encontrar_coluna_funil_fixa(df_raw.columns, 'INDICADOR')
    col_indicador_ordem = _encontrar_coluna_funil_fixa(df_raw.columns, 'INDICADOR_ORDEM', 'INDICADOR ORDEM')
    col_periodo = _encontrar_coluna_funil_fixa(df_raw.columns, 'PERIODO_MES', 'PERIODO MES')
    col_mes_ano = _encontrar_coluna_funil_fixa(df_raw.columns, 'MES_ANO', 'MÊS_ANO')
    col_mes_ordem = _encontrar_coluna_funil_fixa(df_raw.columns, 'MES_ANO_ORDEM', 'MES ANO ORDEM')
    col_qtde = _encontrar_coluna_funil_fixa(df_raw.columns, 'QTDE')

    obrigatorias = [col_segmento, col_origem, col_indicador, col_periodo, col_qtde]
    if any(col is None for col in obrigatorias):
        return pd.DataFrame()

    rename_map = {
        col_segmento: 'SEGMENTO',
        col_origem: 'ORIGEM_AGG',
        col_indicador: 'INDICADOR',
        col_periodo: 'PERIODO_MES',
        col_qtde: 'QTDE',
    }
    if col_indicador_ordem:
        rename_map[col_indicador_ordem] = 'INDICADOR_ORDEM'
    if col_canal_entrada:
        rename_map[col_canal_entrada] = 'CANAL_ENTRADA'
    if col_mes_ano:
        rename_map[col_mes_ano] = 'MES_ANO'
    if col_mes_ordem:
        rename_map[col_mes_ordem] = 'MES_ANO_ORDEM'

    df = df_raw.rename(columns=rename_map)[list(rename_map.values())].copy()

    if 'CANAL_ENTRADA' not in df.columns:
        df['CANAL_ENTRADA'] = 'Não Informado'

    for coluna_txt in ['SEGMENTO', 'ORIGEM_AGG', 'CANAL_ENTRADA', 'INDICADOR']:
        df[coluna_txt] = df[coluna_txt].astype(str).str.strip()
        df = df[df[coluna_txt].ne('')]

    df['SEGMENTO'] = df['SEGMENTO'].map(_normalizar_segmento_funil_fixa)
    df['INDICADOR_CHAVE'] = df['INDICADOR'].map(_normalizar_chave_funil_fixa)
    df = df[df['SEGMENTO'].isin(['PF', 'PME'])].copy()
    df = _normalizar_indicadores_funil_fixa_df(df)

    df['QTDE'] = normalizar_numerico_serie(df['QTDE']).fillna(0.0)

    df['PERIODO_MES'] = pd.to_datetime(
        df['PERIODO_MES'],
        format='mixed',
        errors='coerce',
        dayfirst=True
    )
    if 'MES_ANO_ORDEM' in df.columns:
        df['MES_ANO_ORDEM'] = normalizar_numerico_serie(df['MES_ANO_ORDEM'])
    else:
        df['MES_ANO_ORDEM'] = np.nan

    if 'MES_ANO' not in df.columns:
        df['MES_ANO'] = ''
    df['MES_ANO'] = df['MES_ANO'].fillna('').astype(str).str.strip().str.lower()

    mask_periodo = df['PERIODO_MES'].notna()
    df.loc[mask_periodo, 'MES_ANO'] = df.loc[mask_periodo, 'PERIODO_MES'].apply(_formatar_mes_ano_funil_fixa)
    df.loc[mask_periodo, 'MES_ANO_ORDEM'] = df.loc[mask_periodo, 'PERIODO_MES'].dt.year * 100 + df.loc[mask_periodo, 'PERIODO_MES'].dt.month

    df['MES_ANO'] = df['MES_ANO'].astype(str).str.strip().str.lower()
    df['MES_ANO_ORDEM'] = normalizar_numerico_serie(df['MES_ANO_ORDEM']).fillna(0).astype(int)
    df = df[df['MES_ANO_ORDEM'] > 0].copy()
    df['EH_TEND'] = 0

    tend_path_obj = resolver_arquivo_dashboard(
        Path(tend_path) if tend_path else TEND_FUNIL_FIXA_FILE_PATH,
        TEND_FUNIL_FIXA_FILE_PATH,
        path_obj.with_name('tend_funil_ecom.xlsx'),
        'tend_funil_ecom.xlsx'
    )
    tend_path_obj = tend_path_obj if Path(tend_path_obj).exists() else None
    if tend_path_obj is not None:
        df_tend = load_tend_funil_fixa_data(str(tend_path_obj), tend_file_mtime)
        if not df_tend.empty:
            df = _aplicar_tend_funil_fixa(df, df_tend)

    return df


def _calcular_janela_meses_funil_fixa(
    df_funil: pd.DataFrame,
    qtd_meses: int = 13,
    mes_foco_ordem: int | None = None
) -> list[int]:
    if df_funil is None or df_funil.empty or 'MES_ANO_ORDEM' not in df_funil.columns:
        return []
    meses = sorted(df_funil['MES_ANO_ORDEM'].dropna().astype(int).unique().tolist())
    if mes_foco_ordem is not None:
        meses = [mes for mes in meses if int(mes) <= int(mes_foco_ordem)]
    if not meses:
        return []
    return meses[-max(int(qtd_meses), 1):]


def _obter_mes_tend_funil_fixa(df_funil: pd.DataFrame) -> int | None:
    if df_funil is None or df_funil.empty or 'EH_TEND' not in df_funil.columns:
        return None
    meses_tend = sorted(
        df_funil.loc[
            pd.to_numeric(df_funil.get('EH_TEND', 0), errors='coerce').fillna(0).astype(int).gt(0),
            'MES_ANO_ORDEM'
        ].dropna().astype(int).unique().tolist()
    )
    return int(meses_tend[-1]) if meses_tend else None


def _formatar_valor_funil_fixa(valor: float) -> str:
    return formatar_numero_brasileiro(float(pd.to_numeric(pd.Series([valor]), errors='coerce').fillna(0.0).iloc[0]), 0)


def _calcular_mom_funil_fixa(valor_atual: float, valor_anterior: float) -> float:
    atual = float(valor_atual or 0.0)
    anterior = float(valor_anterior or 0.0)
    if anterior == 0:
        return np.nan
    return ((atual / anterior) - 1.0) * 100.0


def _render_mom_badge_funil_fixa(valor_mom: float) -> str:
    if pd.isna(valor_mom):
        return '<span class="mom-pill mom-flat">• n/d</span>'
    if valor_mom > 0:
        classe = 'mom-up'
        seta = '▲'
    elif valor_mom < 0:
        classe = 'mom-down'
        seta = '▼'
    else:
        classe = 'mom-flat'
        seta = '•'
    texto = f"{seta} {valor_mom:+.1f}%".replace('.', ',')
    return f'<span class="mom-pill {classe}">{texto}</span>'


def _formatar_percentual_funil_fixa(valor: float) -> str:
    valor_num = pd.to_numeric(pd.Series([valor]), errors='coerce').iloc[0]
    if pd.isna(valor_num):
        return 'n/d'
    return f"{formatar_numero_brasileiro(float(valor_num), 1)}%"


def _calcular_delta_pp_funil_fixa(valor_atual: float, valor_base: float) -> float:
    atual = pd.to_numeric(pd.Series([valor_atual]), errors='coerce').iloc[0]
    base = pd.to_numeric(pd.Series([valor_base]), errors='coerce').iloc[0]
    if pd.isna(atual) or pd.isna(base):
        return np.nan
    return float(atual) - float(base)


def _render_pp_badge_funil_fixa(valor_pp: float) -> str:
    if pd.isna(valor_pp):
        return '<span class="mom-pill mom-flat">• n/d</span>'
    if valor_pp > 0:
        classe = 'mom-up'
        seta = '▲'
    elif valor_pp < 0:
        classe = 'mom-down'
        seta = '▼'
    else:
        classe = 'mom-flat'
        seta = '•'
    texto = f"{seta} {valor_pp:+.1f} p.p.".replace('.', ',')
    return f'<span class="mom-pill {classe}">{texto}</span>'


def _obter_base_percentual_funil_fixa(indicador_chave: str) -> str | None:
    chave = str(indicador_chave or '').strip().upper()
    if chave == "SESSOES":
        return None
    if chave in FUNIL_FIXA_BASE_PERCENTUAL_ESPECIAL:
        return FUNIL_FIXA_BASE_PERCENTUAL_ESPECIAL[chave]
    chaves_percentuais = [chave_item for chave_item, _ in FUNIL_FIXA_INDICADORES_PERCENTUAIS]
    try:
        idx = chaves_percentuais.index(chave)
    except ValueError:
        return None
    return chaves_percentuais[idx - 1] if idx > 0 else None


def _serie_percentual_etapa_funil_fixa(
    obter_serie_qtde,
    indicador_chave: str,
    meses_ordem: list[int]
) -> pd.Series:
    colunas = [int(mes) for mes in meses_ordem]
    serie_num = obter_serie_qtde(indicador_chave).reindex(colunas, fill_value=0.0).astype(float)
    serie_pct = pd.Series(np.nan, index=colunas, dtype=float)

    if str(indicador_chave).strip().upper() == "SESSOES":
        serie_pct.loc[serie_num.gt(0)] = 100.0
        return serie_pct

    base_chave = _obter_base_percentual_funil_fixa(indicador_chave)
    if not base_chave:
        return serie_pct

    serie_base = obter_serie_qtde(base_chave).reindex(colunas, fill_value=0.0).astype(float)
    mask_base = serie_base.gt(0)
    serie_pct.loc[mask_base] = (serie_num.loc[mask_base] / serie_base.loc[mask_base]) * 100.0
    return serie_pct


def montar_estrutura_funil_fixa_ecommerce(
    df_funil: pd.DataFrame,
    segmentos_sel: list[str] | None = None,
    origens_sel: list[str] | None = None,
    canais_entrada_sel: list[str] | None = None,
    indicadores_sel: list[str] | None = None,
    mes_ref_ordem: int | None = None,
    qtd_meses: int = 13
) -> dict:
    estrutura_vazia = {
        'meses_ordem': [],
        'meses_rotulos': [],
        'mes_atual_rotulo': '',
        'meses_base_mm3': [],
        'mes_tend_ordem': None,
        'rows': [],
        'observacao_mm3': ''
    }
    if df_funil is None or df_funil.empty:
        return estrutura_vazia

    base = _normalizar_indicadores_funil_fixa_df(df_funil)
    if base is None or base.empty:
        return estrutura_vazia
    if segmentos_sel:
        base = base[base['SEGMENTO'].isin(segmentos_sel)].copy()
    if origens_sel:
        base = base[base['ORIGEM_AGG'].isin(origens_sel)].copy()
    if canais_entrada_sel and 'CANAL_ENTRADA' in base.columns:
        base = base[base['CANAL_ENTRADA'].isin(canais_entrada_sel)].copy()
    if indicadores_sel:
        base = base[base['INDICADOR'].isin(indicadores_sel)].copy()
    if base.empty:
        return estrutura_vazia

    mapa_meses = (
        base[['MES_ANO_ORDEM', 'MES_ANO']]
        .drop_duplicates()
        .sort_values(['MES_ANO_ORDEM', 'MES_ANO'])
        .drop_duplicates(subset=['MES_ANO_ORDEM'], keep='last')
    )
    mapa_ordem_rotulo = {int(linha['MES_ANO_ORDEM']): str(linha['MES_ANO']).strip().upper() for _, linha in mapa_meses.iterrows()}
    mes_tend_ordem = _obter_mes_tend_funil_fixa(base)
    meses_ordem = _calcular_janela_meses_funil_fixa(
        base,
        qtd_meses=qtd_meses,
        mes_foco_ordem=mes_ref_ordem
    )
    if not meses_ordem:
        return estrutura_vazia
    meses_rotulos = [mapa_ordem_rotulo.get(ordem, str(ordem)) for ordem in meses_ordem]

    agg = (
        base.groupby(['INDICADOR', 'ORIGEM_AGG', 'INDICADOR_ORDEM', 'MES_ANO_ORDEM'], observed=True)['QTDE']
        .sum()
        .reset_index()
    )
    if agg.empty:
        return estrutura_vazia

    tabela_child = agg.pivot_table(
        index=['INDICADOR', 'ORIGEM_AGG', 'INDICADOR_ORDEM'],
        columns='MES_ANO_ORDEM',
        values='QTDE',
        aggfunc='sum',
        fill_value=0.0
    )
    tabela_child = tabela_child.reindex(columns=meses_ordem, fill_value=0.0)

    mes_atual_ordem = meses_ordem[-1]
    mes_yoy_ordem = int(mes_atual_ordem) - 100
    tem_mes_yoy = mes_yoy_ordem in tabela_child.columns
    mes_mais_recente_disponivel = max(mapa_ordem_rotulo.keys()) if mapa_ordem_rotulo else None
    meses_mm3 = meses_ordem[-4:-1] if len(meses_ordem) >= 4 else meses_ordem[:-1]
    aplicar_mm3_mes_atual = bool(
        meses_mm3 and
        mes_mais_recente_disponivel is not None and
        int(mes_atual_ordem) == int(mes_mais_recente_disponivel) and
        (mes_tend_ordem is None or int(mes_atual_ordem) != int(mes_tend_ordem))
    )
    if aplicar_mm3_mes_atual:
        tabela_child[mes_atual_ordem] = tabela_child[meses_mm3].mean(axis=1)

    tabela_parent = tabela_child.groupby(level=['INDICADOR', 'INDICADOR_ORDEM']).sum()
    tabela_child_reset = tabela_child.reset_index()

    rows = []
    indicadores_ordenados = sorted(
        [(idx[0], float(idx[1] if not pd.isna(idx[1]) else 999.0)) for idx in tabela_parent.index],
        key=lambda item: (item[1], str(item[0]).upper())
    )

    for idx_pai, (indicador, indicador_ordem) in enumerate(indicadores_ordenados, start=1):
        serie_pai = tabela_parent.loc[(indicador, indicador_ordem)]
        atual_pai = float(serie_pai.get(mes_atual_ordem, 0.0))
        anterior_pai = float(serie_pai.get(meses_ordem[-2], 0.0)) if len(meses_ordem) >= 2 else 0.0
        yoy_pai = float(serie_pai.get(mes_yoy_ordem, 0.0)) if tem_mes_yoy else 0.0
        row_id = f"funil-pai-{idx_pai}"
        filhos = []

        filhos_df = tabela_child_reset[
            tabela_child_reset['INDICADOR'].astype(str).eq(str(indicador)) &
            pd.to_numeric(tabela_child_reset['INDICADOR_ORDEM'], errors='coerce').fillna(999.0).eq(float(indicador_ordem))
        ].copy()
        filhos_df['_ordem_total'] = filhos_df[meses_ordem].sum(axis=1)
        filhos_df['_ordem_mes_ref'] = pd.to_numeric(filhos_df[mes_atual_ordem], errors='coerce').fillna(0.0)
        filhos_df = filhos_df.sort_values(
            by=['_ordem_total', '_ordem_mes_ref', 'ORIGEM_AGG'],
            ascending=[False, False, True],
            na_position='last'
        )

        for idx_filho, (_, linha_filho) in enumerate(filhos_df.iterrows(), start=1):
            origem = str(linha_filho.get('ORIGEM_AGG', '')).strip()
            atual_filho = float(linha_filho.get(mes_atual_ordem, 0.0))
            anterior_filho = float(linha_filho.get(meses_ordem[-2], 0.0)) if len(meses_ordem) >= 2 else 0.0
            yoy_filho = float(linha_filho.get(mes_yoy_ordem, 0.0)) if tem_mes_yoy else 0.0
            raw_values_filho = [float(linha_filho.get(mes, 0.0)) for mes in meses_ordem]
            filhos.append({
                'id': f'{row_id}-filho-{idx_filho}',
                'parent_id': row_id,
                'label': str(origem),
                'level': 1,
                'values': [_formatar_valor_funil_fixa(valor) for valor in raw_values_filho],
                'raw_values': raw_values_filho,
                'sort_mes_ref': float(raw_values_filho[-1]) if raw_values_filho else 0.0,
                'sort_total': float(sum(raw_values_filho)) if raw_values_filho else 0.0,
                'sort_vector': [float(v) for v in raw_values_filho[::-1]],
                'mom_html': _render_mom_badge_funil_fixa(_calcular_mom_funil_fixa(atual_filho, anterior_filho)),
                'yoy_html': _render_mom_badge_funil_fixa(_calcular_mom_funil_fixa(atual_filho, yoy_filho)),
            })

        filhos = sorted(
            filhos,
            key=lambda item: (
                *tuple(-float(v) for v in item.get('sort_vector', [])),
                -float(item.get('sort_total', 0.0)),
                str(item.get('label', '')).upper(),
            )
        )

        raw_values_pai = [float(serie_pai.get(mes, 0.0)) for mes in meses_ordem]
        rows.append({
            'id': row_id,
            'label': str(indicador),
            'level': 0,
            'values': [_formatar_valor_funil_fixa(valor) for valor in raw_values_pai],
            'raw_values': raw_values_pai,
            'mom_html': _render_mom_badge_funil_fixa(_calcular_mom_funil_fixa(atual_pai, anterior_pai)),
            'yoy_html': _render_mom_badge_funil_fixa(_calcular_mom_funil_fixa(atual_pai, yoy_pai)),
            'children': filhos,
        })

    observacao_mm3 = ''
    if mes_tend_ordem is not None and int(mes_atual_ordem) == int(mes_tend_ordem):
        meses_ref_tend = meses_ordem[-4:-1] if len(meses_ordem) >= 4 else meses_ordem[:-1]
        rotulos_ref_tend = ', '.join(mapa_ordem_rotulo.get(m, str(m)) for m in meses_ref_tend) if meses_ref_tend else ''
        complemento = f" pela proporcao media dos ultimos 3 meses ({rotulos_ref_tend})." if rotulos_ref_tend else '.'
        observacao_mm3 = (
            f"O mes {mapa_ordem_rotulo.get(mes_atual_ordem, str(mes_atual_ordem))} foi carregado pelo arquivo de tend"
            f"{complemento}"
        )
    elif aplicar_mm3_mes_atual and meses_mm3:
        observacao_mm3 = (
            f"O mês {mapa_ordem_rotulo.get(mes_atual_ordem, str(mes_atual_ordem))} foi preenchido com média móvel "
            f"dos últimos {len(meses_mm3)} meses ({', '.join(mapa_ordem_rotulo.get(m, str(m)) for m in meses_mm3)})."
        )

    return {
        'meses_ordem': meses_ordem,
        'meses_rotulos': meses_rotulos,
        'mes_atual_rotulo': mapa_ordem_rotulo.get(mes_atual_ordem, str(mes_atual_ordem)),
        'meses_base_mm3': [mapa_ordem_rotulo.get(m, str(m)) for m in meses_mm3] if aplicar_mm3_mes_atual else [],
        'mes_tend_ordem': mes_tend_ordem,
        'rows': rows,
        'observacao_mm3': observacao_mm3,
    }


def montar_estrutura_funil_fixa_ecommerce_percentual(
    df_funil: pd.DataFrame,
    segmentos_sel: list[str] | None = None,
    origens_sel: list[str] | None = None,
    canais_entrada_sel: list[str] | None = None,
    indicadores_sel: list[str] | None = None,
    mes_ref_ordem: int | None = None,
    qtd_meses: int = 13
) -> dict:
    estrutura_vazia = {
        'meses_ordem': [],
        'meses_rotulos': [],
        'mes_atual_rotulo': '',
        'meses_base_mm3': [],
        'mes_tend_ordem': None,
        'rows': [],
        'observacao_mm3': ''
    }
    if df_funil is None or df_funil.empty:
        return estrutura_vazia

    base = _normalizar_indicadores_funil_fixa_df(df_funil)
    if base is None or base.empty:
        return estrutura_vazia
    if segmentos_sel:
        base = base[base['SEGMENTO'].isin(segmentos_sel)].copy()
    if origens_sel:
        base = base[base['ORIGEM_AGG'].isin(origens_sel)].copy()
    if canais_entrada_sel and 'CANAL_ENTRADA' in base.columns:
        base = base[base['CANAL_ENTRADA'].isin(canais_entrada_sel)].copy()
    if base.empty:
        return estrutura_vazia

    indicadores_exibir = [
        (chave, label)
        for chave, label in FUNIL_FIXA_INDICADORES_PERCENTUAIS
        if (not indicadores_sel) or (label in indicadores_sel)
    ]
    if not indicadores_exibir:
        return estrutura_vazia

    mapa_meses = (
        base[['MES_ANO_ORDEM', 'MES_ANO']]
        .drop_duplicates()
        .sort_values(['MES_ANO_ORDEM', 'MES_ANO'])
        .drop_duplicates(subset=['MES_ANO_ORDEM'], keep='last')
    )
    mapa_ordem_rotulo = {int(linha['MES_ANO_ORDEM']): str(linha['MES_ANO']).strip().upper() for _, linha in mapa_meses.iterrows()}
    mes_tend_ordem = _obter_mes_tend_funil_fixa(base)
    meses_ordem = _calcular_janela_meses_funil_fixa(
        base,
        qtd_meses=qtd_meses,
        mes_foco_ordem=mes_ref_ordem
    )
    if not meses_ordem:
        return estrutura_vazia
    meses_rotulos = [mapa_ordem_rotulo.get(ordem, str(ordem)) for ordem in meses_ordem]

    agg = (
        base.groupby(['INDICADOR_CHAVE', 'INDICADOR', 'ORIGEM_AGG', 'INDICADOR_ORDEM', 'MES_ANO_ORDEM'], observed=True)['QTDE']
        .sum()
        .reset_index()
    )
    if agg.empty:
        return estrutura_vazia

    tabela_child = agg.pivot_table(
        index=['INDICADOR_CHAVE', 'INDICADOR', 'ORIGEM_AGG', 'INDICADOR_ORDEM'],
        columns='MES_ANO_ORDEM',
        values='QTDE',
        aggfunc='sum',
        fill_value=0.0
    )
    tabela_child = tabela_child.reindex(columns=meses_ordem, fill_value=0.0)

    mes_atual_ordem = meses_ordem[-1]
    mes_yoy_ordem = int(mes_atual_ordem) - 100
    tem_mes_yoy = mes_yoy_ordem in tabela_child.columns
    mes_mais_recente_disponivel = max(mapa_ordem_rotulo.keys()) if mapa_ordem_rotulo else None
    meses_mm3 = meses_ordem[-4:-1] if len(meses_ordem) >= 4 else meses_ordem[:-1]
    aplicar_mm3_mes_atual = bool(
        meses_mm3 and
        mes_mais_recente_disponivel is not None and
        int(mes_atual_ordem) == int(mes_mais_recente_disponivel) and
        (mes_tend_ordem is None or int(mes_atual_ordem) != int(mes_tend_ordem))
    )
    if aplicar_mm3_mes_atual:
        tabela_child[mes_atual_ordem] = tabela_child[meses_mm3].mean(axis=1)

    tabela_parent = tabela_child.groupby(level=['INDICADOR_CHAVE', 'INDICADOR', 'INDICADOR_ORDEM']).sum()

    def _serie_vazia_qtde() -> pd.Series:
        return pd.Series(0.0, index=meses_ordem, dtype=float)

    def _obter_serie_parent(indicador_chave: str) -> pd.Series:
        try:
            fatia = tabela_parent.xs(str(indicador_chave).strip().upper(), level='INDICADOR_CHAVE')
        except (KeyError, ValueError):
            return _serie_vazia_qtde()
        if isinstance(fatia, pd.Series):
            serie = fatia
        else:
            serie = fatia.reindex(columns=meses_ordem, fill_value=0.0).sum(axis=0)
        return pd.to_numeric(serie.reindex(meses_ordem, fill_value=0.0), errors='coerce').fillna(0.0).astype(float)

    def _obter_serie_child(indicador_chave: str, origem: str) -> pd.Series:
        try:
            fatia = tabela_child.xs(
                (str(indicador_chave).strip().upper(), str(origem)),
                level=('INDICADOR_CHAVE', 'ORIGEM_AGG')
            )
        except (KeyError, ValueError):
            return _serie_vazia_qtde()
        if isinstance(fatia, pd.Series):
            serie = fatia
        else:
            serie = fatia.reindex(columns=meses_ordem, fill_value=0.0).sum(axis=0)
        return pd.to_numeric(serie.reindex(meses_ordem, fill_value=0.0), errors='coerce').fillna(0.0).astype(float)

    def _tem_referencia_percentual(indicador_chave: str, obter_serie) -> bool:
        serie_num = obter_serie(indicador_chave)
        base_chave = _obter_base_percentual_funil_fixa(indicador_chave)
        serie_base = obter_serie(base_chave) if base_chave else serie_num
        return bool(float(serie_num.sum()) > 0 or float(serie_base.sum()) > 0)

    def _valores_percentuais(serie_pct: pd.Series) -> list[float]:
        return [float(serie_pct.get(mes, np.nan)) if not pd.isna(serie_pct.get(mes, np.nan)) else np.nan for mes in meses_ordem]

    rows = []
    tabela_child_reset = tabela_child.reset_index()
    for idx_pai, (indicador_chave, indicador_label) in enumerate(indicadores_exibir, start=1):
        if not _tem_referencia_percentual(indicador_chave, _obter_serie_parent):
            continue

        serie_pct_pai = _serie_percentual_etapa_funil_fixa(
            _obter_serie_parent,
            indicador_chave,
            meses_ordem
        )
        raw_values_pai = _valores_percentuais(serie_pct_pai)
        atual_pai = raw_values_pai[-1] if raw_values_pai else np.nan
        anterior_pai = raw_values_pai[-2] if len(raw_values_pai) >= 2 else np.nan
        yoy_pai = float(serie_pct_pai.get(mes_yoy_ordem, np.nan)) if tem_mes_yoy else np.nan
        row_id = f"funil-percentual-pai-{idx_pai}"

        filhos = []
        filhos_df = tabela_child_reset[
            tabela_child_reset['INDICADOR_CHAVE'].astype(str).str.upper().eq(str(indicador_chave).upper())
        ].copy()
        origens_indicador = sorted(filhos_df['ORIGEM_AGG'].dropna().astype(str).str.strip().unique().tolist())
        for idx_filho, origem in enumerate(origens_indicador, start=1):
            if not _tem_referencia_percentual(indicador_chave, lambda chave, origem_ref=origem: _obter_serie_child(chave, origem_ref)):
                continue
            serie_pct_filho = _serie_percentual_etapa_funil_fixa(
                lambda chave, origem_ref=origem: _obter_serie_child(chave, origem_ref),
                indicador_chave,
                meses_ordem
            )
            raw_values_filho = _valores_percentuais(serie_pct_filho)
            atual_filho = raw_values_filho[-1] if raw_values_filho else np.nan
            anterior_filho = raw_values_filho[-2] if len(raw_values_filho) >= 2 else np.nan
            yoy_filho = float(serie_pct_filho.get(mes_yoy_ordem, np.nan)) if tem_mes_yoy else np.nan
            valores_sort = [0.0 if pd.isna(valor) else float(valor) for valor in raw_values_filho]
            filhos.append({
                'id': f'{row_id}-filho-{idx_filho}',
                'parent_id': row_id,
                'label': str(origem),
                'level': 1,
                'values': [_formatar_percentual_funil_fixa(valor) for valor in raw_values_filho],
                'raw_values': raw_values_filho,
                'sort_mes_ref': float(valores_sort[-1]) if valores_sort else 0.0,
                'sort_total': float(sum(valores_sort)) if valores_sort else 0.0,
                'sort_vector': [float(v) for v in valores_sort[::-1]],
                'mom_html': _render_pp_badge_funil_fixa(_calcular_delta_pp_funil_fixa(atual_filho, anterior_filho)),
                'yoy_html': _render_pp_badge_funil_fixa(_calcular_delta_pp_funil_fixa(atual_filho, yoy_filho)),
            })

        filhos = sorted(
            filhos,
            key=lambda item: (
                *tuple(-float(v) for v in item.get('sort_vector', [])),
                -float(item.get('sort_total', 0.0)),
                str(item.get('label', '')).upper(),
            )
        )

        rows.append({
            'id': row_id,
            'label': str(indicador_label),
            'level': 0,
            'values': [_formatar_percentual_funil_fixa(valor) for valor in raw_values_pai],
            'raw_values': raw_values_pai,
            'mom_html': _render_pp_badge_funil_fixa(_calcular_delta_pp_funil_fixa(atual_pai, anterior_pai)),
            'yoy_html': _render_pp_badge_funil_fixa(_calcular_delta_pp_funil_fixa(atual_pai, yoy_pai)),
            'children': filhos,
        })

    observacao_mm3 = ''
    if mes_tend_ordem is not None and int(mes_atual_ordem) == int(mes_tend_ordem):
        meses_ref_tend = meses_ordem[-4:-1] if len(meses_ordem) >= 4 else meses_ordem[:-1]
        rotulos_ref_tend = ', '.join(mapa_ordem_rotulo.get(m, str(m)) for m in meses_ref_tend) if meses_ref_tend else ''
        complemento = f" pela proporcao media dos ultimos 3 meses ({rotulos_ref_tend})." if rotulos_ref_tend else '.'
        observacao_mm3 = (
            f"O mes {mapa_ordem_rotulo.get(mes_atual_ordem, str(mes_atual_ordem))} foi carregado pelo arquivo de tend"
            f"{complemento}"
        )
    elif aplicar_mm3_mes_atual and meses_mm3:
        observacao_mm3 = (
            f"O mês {mapa_ordem_rotulo.get(mes_atual_ordem, str(mes_atual_ordem))} foi preenchido com média móvel "
            f"dos últimos {len(meses_mm3)} meses ({', '.join(mapa_ordem_rotulo.get(m, str(m)) for m in meses_mm3)})."
        )

    return {
        'meses_ordem': meses_ordem,
        'meses_rotulos': meses_rotulos,
        'mes_atual_rotulo': mapa_ordem_rotulo.get(mes_atual_ordem, str(mes_atual_ordem)),
        'meses_base_mm3': [mapa_ordem_rotulo.get(m, str(m)) for m in meses_mm3] if aplicar_mm3_mes_atual else [],
        'mes_tend_ordem': mes_tend_ordem,
        'rows': rows,
        'observacao_mm3': observacao_mm3,
    }


def criar_tabela_html_funil_fixa_ecommerce(
    estrutura: dict,
    table_id: str,
    max_body_height: int = 760
) -> str:
    if not estrutura or not estrutura.get('rows'):
        return ''

    qtd_meses = max(len(estrutura.get('meses_rotulos', [])), 1)
    largura_primeira_coluna = 148
    largura_coluna_variacao = 72
    largura_colunas_variacao = largura_coluna_variacao * 2
    mes_tend_ordem = estrutura.get('mes_tend_ordem')
    colgroup_html = (
        '<colgroup>'
        f'<col style="width:{largura_primeira_coluna}px;">'
        + ''.join(
            f'<col style="width:calc((100% - {largura_primeira_coluna + largura_colunas_variacao}px) / {qtd_meses});">'
            for _ in range(qtd_meses)
        )
        + f'<col style="width:{largura_coluna_variacao}px;">'
        + f'<col style="width:{largura_coluna_variacao}px;">'
        + '</colgroup>'
    )

    def _render_databar_cell(valor_bruto: float, valor_formatado: str, max_coluna: float, eh_col_tend: bool = False) -> str:
        valor_num = float(pd.to_numeric(pd.Series([valor_bruto]), errors='coerce').fillna(0.0).iloc[0])
        max_ref = float(pd.to_numeric(pd.Series([max_coluna]), errors='coerce').fillna(0.0).iloc[0])
        if max_ref <= 0 or valor_num <= 0:
            largura = 0.0
        else:
            largura = max(8.0, min(100.0, (valor_num / max_ref) * 100.0))

        return (
            f'<td class="ff-data-cell{" ff-col-tend" if eh_col_tend else ""}">'
            '<div class="ff-data-bar-wrap">'
            f'<div class="ff-data-bar-fill" style="width:{largura:.1f}%;"></div>'
            f'<span class="ff-data-bar-text">{escape(str(valor_formatado))}</span>'
            '</div>'
            '</td>'
        )

    cabecalhos_meses = ''.join(
        f'<th class="ff-col-mes{" ff-col-tend" if estrutura.get("meses_ordem", [])[idx] == mes_tend_ordem else ""}">{escape(str(rotulo))}</th>'
        for idx, rotulo in enumerate(estrutura.get('meses_rotulos', []))
    )

    linhas_html = []
    for row in estrutura.get('rows', []):
        possui_filhos = bool(row.get('children'))
        btn_toggle = (
            f'<button class="ff-toggle" data-target="{escape(str(row.get("id")), quote=True)}" aria-label="Expandir {escape(str(row.get("label")))}">+</button>'
            if possui_filhos else '<span class="ff-toggle ff-toggle-placeholder"></span>'
        )
        valores_html = ''.join(
            f'<td class="{"ff-col-tend" if estrutura.get("meses_ordem", [])[idx_coluna] == mes_tend_ordem else ""}">{escape(str(valor))}</td>'
            for idx_coluna, valor in enumerate(row.get('values', []))
        )
        linhas_html.append(
            f'<tr class="ff-row ff-row-parent" data-row-id="{escape(str(row.get("id")), quote=True)}">'
            f'<td class="ff-sticky ff-row-label">{btn_toggle}<span class="ff-label-text">{escape(str(row.get("label")))}</span></td>'
            f'{valores_html}'
            f'<td class="ff-mom-cell">{row.get("mom_html", "")}</td>'
            f'<td class="ff-mom-cell">{row.get("yoy_html", "")}</td>'
            f'</tr>'
        )
        maximos_filhos = []
        if row.get('children'):
            qtd_colunas = len(row.get('values', []))
            for idx_coluna in range(qtd_colunas):
                max_coluna = max(
                    [
                        float(pd.to_numeric(pd.Series([child.get('raw_values', [0.0] * qtd_colunas)[idx_coluna]]), errors='coerce').fillna(0.0).iloc[0])
                        for child in row.get('children', [])
                    ] or [0.0]
                )
                maximos_filhos.append(max_coluna)
        for child in row.get('children', []):
            child_values_html = ''.join(
                _render_databar_cell(
                    valor_bruto=child.get('raw_values', [0.0] * len(child.get('values', [])))[idx_coluna],
                    valor_formatado=child.get('values', [''])[idx_coluna],
                    max_coluna=maximos_filhos[idx_coluna] if idx_coluna < len(maximos_filhos) else 0.0,
                    eh_col_tend=estrutura.get("meses_ordem", [])[idx_coluna] == mes_tend_ordem
                )
                for idx_coluna in range(len(child.get('values', [])))
            )
            linhas_html.append(
                f'<tr class="ff-row ff-row-child" data-parent-row="{escape(str(row.get("id")), quote=True)}" style="display:none;">'
                f'<td class="ff-sticky ff-row-label ff-row-label-child"><span class="ff-child-indent"></span><span class="ff-label-text">{escape(str(child.get("label")))}</span></td>'
                f'{child_values_html}'
                f'<td class="ff-mom-cell">{child.get("mom_html", "")}</td>'
                f'<td class="ff-mom-cell">{child.get("yoy_html", "")}</td>'
                f'</tr>'
            )

    observacao = ''

    return f'''
    <div id="{escape(table_id, quote=True)}" class="ff-wrapper">
      <style>
        #{table_id}.ff-wrapper {{font-family:'Manrope','Segoe UI',sans-serif; color:#312B2A; margin-top:2px;}}
        #{table_id} .ff-note {{margin:0 0 6px 2px; font-size:11px; color:#6B5C59;}}
        #{table_id} .ff-table-box {{
            position:relative;
            border:1px solid rgba(121,14,9,0.70);
            border-radius:6px;
            overflow-y:auto;
            overflow-x:hidden;
            max-height:{int(max_body_height)}px;
            background:
                linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(255,248,247,0.96) 100%);
            box-shadow:
                0 18px 40px rgba(90,10,6,0.14),
                0 4px 14px rgba(15,23,42,0.07),
                inset 0 0 0 1px rgba(255,255,255,0.92);
        }}
        #{table_id} .ff-table-box::before {{
            content:none;
            display:none;
        }}
        #{table_id} table {{border-collapse:separate; border-spacing:0; width:100%; table-layout:fixed; margin-top:0; font-variant-numeric:tabular-nums; background:#FFFFFF;}}
        #{table_id} thead th {{
            position:sticky;
            top:0;
            z-index:3;
            background:linear-gradient(180deg, #790E09 0%, #4E0805 100%);
            color:#FFFFFF;
            font-size:9.4px;
            letter-spacing:0.24px;
            text-transform:uppercase;
            padding:8px 5px;
            border-right:1px solid rgba(255,255,255,0.18);
            border-bottom:1px solid rgba(61,7,4,0.92);
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
            font-weight:800;
        }}
        #{table_id} thead th:first-child {{left:0; z-index:4; border-top-left-radius:5px; text-align:left; padding-left:12px; background:linear-gradient(180deg, #6C0C08 0%, #3D0704 100%);}}
        #{table_id} thead th:nth-last-child(2),
        #{table_id} thead th:last-child {{background:linear-gradient(180deg, #4F5861 0%, #343B43 100%);}}
        #{table_id} thead th:last-child {{border-top-right-radius:5px;}}
        #{table_id} thead th.ff-col-tend {{background:linear-gradient(135deg, #B7443B 0%, #8F241D 100%); color:#FFFFFF;}}
        #{table_id} tbody td {{
            padding:7px 5px;
            font-size:11.3px;
            border-bottom:1px solid rgba(121,14,9,0.075);
            border-right:1px solid rgba(121,14,9,0.045);
            white-space:nowrap;
            text-align:right;
            color:#312B2A;
            background:#FFFFFF;
            overflow:hidden;
            text-overflow:ellipsis;
            font-weight:520;
        }}
        #{table_id} tbody td.ff-col-tend {{background:linear-gradient(180deg, rgba(183,68,59,0.075) 0%, rgba(183,68,59,0.030) 100%); color:#111827;}}
        #{table_id} tbody tr.ff-row-parent td {{
            background:linear-gradient(180deg, #FFF6F4 0%, #FFFFFF 100%);
            font-weight:650;
            color:#171717;
            border-top:1px solid rgba(121,14,9,0.08);
        }}
        #{table_id} tbody tr.ff-row-parent td:not(.ff-sticky):not(.ff-mom-cell) {{
            background:linear-gradient(180deg, #FFFDFC 0%, #FFFFFF 100%);
            color:#111827;
            letter-spacing:-0.02em;
        }}
        #{table_id} tbody tr.ff-row-parent td.ff-col-tend {{background:linear-gradient(180deg, rgba(183,68,59,0.085) 0%, rgba(183,68,59,0.035) 100%); color:#111827;}}
        #{table_id} tbody tr.ff-row-child td {{background:#FFFDFC; color:#3E454E; font-weight:510;}}
        #{table_id} tbody tr.ff-row-child:nth-child(even) td {{background:#FFF8F7;}}
        #{table_id} tbody tr.ff-row-child td.ff-col-tend {{background:linear-gradient(180deg, rgba(183,68,59,0.070) 0%, rgba(183,68,59,0.028) 100%); color:#111827;}}
        #{table_id} tbody tr:hover td {{background:linear-gradient(90deg, #FFF3F0 0%, #FFF8F7 100%) !important;}}
        #{table_id} tbody tr:hover td.ff-col-tend {{background:linear-gradient(180deg, rgba(183,68,59,0.10) 0%, rgba(183,68,59,0.042) 100%) !important;}}
        #{table_id} .ff-sticky {{
            position:sticky;
            left:0;
            z-index:2;
            text-align:left !important;
            min-width:{largura_primeira_coluna}px;
            max-width:{largura_primeira_coluna}px;
            width:{largura_primeira_coluna}px;
        }}
        #{table_id} .ff-row-parent .ff-sticky {{z-index:3; color:#111827; background:linear-gradient(180deg, #FFF4F1 0%, #FFFDFC 100%);}}
        #{table_id} .ff-row-label {{display:flex; align-items:center; gap:6px;}}
        #{table_id} .ff-label-text {{overflow:hidden; text-overflow:ellipsis; letter-spacing:-0.01em;}}
        #{table_id} .ff-row-parent .ff-label-text {{text-transform:uppercase; font-size:11.2px; font-weight:650;}}
        #{table_id} .ff-row-label-child {{padding-left:8px; color:#4B5563 !important;}}
        #{table_id} .ff-child-indent {{display:inline-block; width:12px; height:1px; background:linear-gradient(90deg, rgba(121,14,9,0.42), rgba(121,14,9,0.08)); margin-right:1px;}}
        #{table_id} .ff-toggle {{width:18px; height:18px; border-radius:3px; border:1px solid rgba(121,14,9,0.26); background:linear-gradient(180deg,#FFFFFF 0%,#FFEAE6 100%); color:#790E09; font-weight:900; line-height:16px; cursor:pointer; display:inline-flex; align-items:center; justify-content:center; font-size:12px; padding:0; flex:0 0 auto; box-shadow:0 1px 0 rgba(255,255,255,0.9), 0 4px 10px rgba(121,14,9,0.08);}}
        #{table_id} .ff-toggle:hover {{background:linear-gradient(180deg,#FFFFFF 0%,#FFDCD5 100%); transform:translateY(-1px);}}
        #{table_id} .ff-toggle-placeholder {{border-color:transparent; background:transparent; cursor:default; box-shadow:none;}}
        #{table_id} .ff-col-mes {{min-width:0;}}
        #{table_id} .ff-mom-cell {{text-align:center !important; width:{largura_coluna_variacao}px; min-width:{largura_coluna_variacao}px;}}
        #{table_id} .ff-data-cell {{padding:4px 3px;}}
        #{table_id} .ff-data-bar-wrap {{position:relative; width:100%; min-width:0; height:20px; border-radius:3px; background:linear-gradient(180deg, rgba(121,14,9,0.035) 0%, rgba(121,14,9,0.075) 100%); overflow:hidden; border:1px solid rgba(121,14,9,0.055); box-shadow:inset 0 1px 0 rgba(255,255,255,0.82);}}
        #{table_id} .ff-data-cell.ff-col-tend .ff-data-bar-wrap {{background:linear-gradient(180deg, rgba(183,68,59,0.045) 0%, rgba(183,68,59,0.085) 100%); border-color:rgba(183,68,59,0.10);}}
        #{table_id} .ff-data-bar-fill {{position:absolute; inset:0 auto 0 0; background:linear-gradient(90deg, rgba(121,14,9,0.13) 0%, rgba(255,40,0,0.15) 100%); border-right:1px solid rgba(121,14,9,0.10);}}
        #{table_id} .ff-data-cell.ff-col-tend .ff-data-bar-fill {{background:linear-gradient(90deg, rgba(183,68,59,0.16) 0%, rgba(183,68,59,0.08) 100%); border-right-color:rgba(183,68,59,0.12);}}
        #{table_id} .ff-data-bar-text {{position:relative; z-index:1; display:flex; align-items:center; justify-content:flex-end; height:100%; padding:0 5px; font-size:10.7px; color:#2F3747; letter-spacing:-0.04em; font-weight:600; text-shadow:0 1px 0 rgba(255,255,255,0.75);}}
        #{table_id} .mom-pill {{display:inline-flex; align-items:center; justify-content:center; min-width:62px; padding:2px 4px; border-radius:3px; font-size:10.7px; font-weight:650; letter-spacing:0.02em; border:1px solid rgba(100,116,139,0.14); background:rgba(255,255,255,0.70); box-shadow:inset 0 1px 0 rgba(255,255,255,0.88);}}
        #{table_id} .mom-up {{color:#14532D; border-color:rgba(20,83,45,0.16); background:linear-gradient(180deg, rgba(240,253,244,0.90) 0%, rgba(255,255,255,0.70) 100%);}}
        #{table_id} .mom-down {{color:#991B1B; border-color:rgba(153,27,27,0.16); background:linear-gradient(180deg, rgba(254,242,242,0.90) 0%, rgba(255,255,255,0.72) 100%);}}
        #{table_id} .mom-flat {{color:#334155; border-color:rgba(100,116,139,0.16); background:linear-gradient(180deg, rgba(248,250,252,0.92) 0%, rgba(255,255,255,0.72) 100%);}}
        #{table_id} ::-webkit-scrollbar {{height:10px; width:10px;}}
        #{table_id} ::-webkit-scrollbar-thumb {{background:linear-gradient(180deg, rgba(121,14,9,0.34), rgba(121,14,9,0.20)); border-radius:999px; border:2px solid rgba(255,248,247,0.92);}}
        #{table_id} ::-webkit-scrollbar-track {{background:rgba(121,14,9,0.04);}}
      </style>
      {observacao}
      <div class="ff-table-box">
        <table>
          {colgroup_html}
          <thead>
            <tr>
              <th class="ff-sticky">Indicador / Origem</th>
              {cabecalhos_meses}
              <th>MoM</th>
              <th>YoY</th>
            </tr>
          </thead>
          <tbody>
            {''.join(linhas_html)}
          </tbody>
        </table>
      </div>
      <script>
        (function() {{
          const root = document.getElementById({table_id!r});
          if (!root) return;
          root.querySelectorAll('.ff-toggle[data-target]').forEach((btn) => {{
            btn.addEventListener('click', () => {{
              const target = btn.getAttribute('data-target');
              const children = root.querySelectorAll(`[data-parent-row="${{target}}"]`);
              const expanded = btn.getAttribute('data-expanded') === 'true';
              children.forEach((row) => {{ row.style.display = expanded ? 'none' : ''; }});
              btn.setAttribute('data-expanded', expanded ? 'false' : 'true');
              btn.textContent = expanded ? '+' : '−';
            }});
          }});
        }})();
      </script>
    </div>
    '''


def _preparar_base_mes_funil_segmentado_fixa(
    df_funil: pd.DataFrame,
    origens_sel: list[str] | None = None,
    canais_entrada_sel: list[str] | None = None,
    indicadores_sel: list[str] | None = None,
    qtd_meses: int | None = None
) -> tuple[pd.DataFrame, dict[int, str], int | None, list[int], int | None]:
    base_vazia = (pd.DataFrame(), {}, None, [], None)
    if df_funil is None or df_funil.empty:
        return base_vazia

    base = _normalizar_indicadores_funil_fixa_df(df_funil)
    if base is None or base.empty:
        return base_vazia
    if origens_sel:
        base = base[base['ORIGEM_AGG'].isin(origens_sel)].copy()
    if canais_entrada_sel and 'CANAL_ENTRADA' in base.columns:
        base = base[base['CANAL_ENTRADA'].isin(canais_entrada_sel)].copy()
    if indicadores_sel:
        base = base[base['INDICADOR'].isin(indicadores_sel)].copy()
    if base.empty:
        return base_vazia

    if qtd_meses is None:
        meses_ordem = sorted(base['MES_ANO_ORDEM'].dropna().astype(int).unique().tolist())
    else:
        meses_ordem = _calcular_janela_meses_funil_fixa(base, qtd_meses=qtd_meses)
    if not meses_ordem:
        return base_vazia

    mapa_meses = (
        base[['MES_ANO_ORDEM', 'MES_ANO']]
        .drop_duplicates()
        .sort_values(['MES_ANO_ORDEM', 'MES_ANO'])
        .drop_duplicates(subset=['MES_ANO_ORDEM'], keep='last')
    )
    mapa_ordem_rotulo = {
        int(linha['MES_ANO_ORDEM']): str(linha['MES_ANO']).strip().upper()
        for _, linha in mapa_meses.iterrows()
    }
    mes_tend_ordem = _obter_mes_tend_funil_fixa(base)

    agg = (
        base.groupby(['SEGMENTO', 'INDICADOR', 'INDICADOR_ORDEM', 'MES_ANO_ORDEM'], as_index=False, observed=True)['QTDE']
        .sum()
    )
    if agg.empty:
        return base_vazia

    tabela = agg.pivot_table(
        index=['SEGMENTO', 'INDICADOR', 'INDICADOR_ORDEM'],
        columns='MES_ANO_ORDEM',
        values='QTDE',
        aggfunc='sum',
        fill_value=0.0
    )
    tabela = tabela.reindex(columns=meses_ordem, fill_value=0.0)

    mes_atual_ordem = meses_ordem[-1]
    meses_mm3 = meses_ordem[-4:-1] if len(meses_ordem) >= 4 else meses_ordem[:-1]
    aplicar_mm3 = bool(meses_mm3 and (mes_tend_ordem is None or int(mes_atual_ordem) != int(mes_tend_ordem)))
    if aplicar_mm3:
        tabela[mes_atual_ordem] = tabela[meses_mm3].mean(axis=1)

    base_mes = (
        tabela.reset_index()
        .melt(
            id_vars=['SEGMENTO', 'INDICADOR', 'INDICADOR_ORDEM'],
            value_vars=meses_ordem,
            var_name='MES_ANO_ORDEM',
            value_name='QTDE'
        )
    )
    base_mes['MES_ANO_ORDEM'] = normalizar_numerico_serie(base_mes['MES_ANO_ORDEM']).fillna(0).astype(int)
    base_mes['QTDE'] = normalizar_numerico_serie(base_mes['QTDE']).fillna(0.0)
    base_mes = (
        base_mes.groupby(['SEGMENTO', 'INDICADOR', 'INDICADOR_ORDEM', 'MES_ANO_ORDEM'], as_index=False, observed=True)['QTDE']
        .sum()
    )
    return base_mes, mapa_ordem_rotulo, mes_atual_ordem, (meses_mm3 if aplicar_mm3 else []), mes_tend_ordem


def _resolver_mes_ordem_funil_segmentado_fixa(
    mapa_ordem_rotulo: dict[int, str],
    mes_ref,
    fallback_ordem: int | None = None
) -> int | None:
    if not mapa_ordem_rotulo:
        return fallback_ordem

    mes_norm = normalizar_chave_visual(str(mes_ref or ""))
    for mes_ordem, rotulo in mapa_ordem_rotulo.items():
        if normalizar_chave_visual(rotulo) == mes_norm:
            return int(mes_ordem)

    try:
        mes_num = int(float(mes_ref))
        if mes_num in mapa_ordem_rotulo:
            return mes_num
    except Exception:
        pass

    return fallback_ordem


def _formatar_valor_real_funil_segmentado(indicador: str, valor: float) -> str:
    valor_num = float(pd.to_numeric(pd.Series([valor]), errors='coerce').fillna(0.0).iloc[0])
    if normalizar_chave_visual(indicador) == 'investimento':
        return f"R$ {formatar_numero_brasileiro(valor_num, 0)}"
    return formatar_numero_brasileiro(valor_num, 0)


def _formatar_percentual_step_funil_segmentado(
    valor_atual: float,
    valor_anterior: float | None,
    valor_base_primeiro_step: float | None = None
) -> str:
    atual = float(pd.to_numeric(pd.Series([valor_atual]), errors='coerce').fillna(0.0).iloc[0])
    if valor_anterior is None:
        if valor_base_primeiro_step is None:
            return ''
        base_primeiro = float(pd.to_numeric(pd.Series([valor_base_primeiro_step]), errors='coerce').fillna(0.0).iloc[0])
        if base_primeiro <= 0:
            return 'n/d'
        percentual_primeiro = (atual / base_primeiro) * 100.0
        return f"{formatar_numero_brasileiro(percentual_primeiro, 1)}%"

    anterior = float(pd.to_numeric(pd.Series([valor_anterior]), errors='coerce').fillna(0.0).iloc[0])
    if anterior <= 0:
        return 'n/d'

    percentual = (atual / anterior) * 100.0
    return f"{formatar_numero_brasileiro(percentual, 1)}%"


def _gerar_larguras_visuais_funil_segmentado(qtd_etapas: int) -> list[float]:
    if qtd_etapas <= 0:
        return []
    if qtd_etapas == 1:
        return [100.0]
    return np.linspace(100.0, 40.0, qtd_etapas).tolist()


def criar_grafico_funil_segmentado_fixa(
    df_funil: pd.DataFrame,
    mes_ref: str,
    origens_sel: list[str] | None = None,
    canais_entrada_sel: list[str] | None = None,
    indicadores_sel: list[str] | None = None
) -> tuple[go.Figure, str]:
    figura_vazia = go.Figure()
    if df_funil is None or df_funil.empty:
        return figura_vazia, ''

    indicadores_plot = [
        label for _, label, _ in FUNIL_FIXA_INDICADORES_CONFIG
        if (not indicadores_sel) or (label in indicadores_sel)
    ]
    if not indicadores_plot:
        return figura_vazia, ''

    base_mes, mapa_ordem_rotulo, mes_atual_ordem, meses_mm3, mes_tend_ordem = _preparar_base_mes_funil_segmentado_fixa(
        df_funil=df_funil,
        origens_sel=origens_sel,
        canais_entrada_sel=canais_entrada_sel,
        indicadores_sel=indicadores_sel,
        qtd_meses=None
    )
    if base_mes.empty or not mapa_ordem_rotulo:
        return figura_vazia, ''

    mes_ref_ordem = _resolver_mes_ordem_funil_segmentado_fixa(
        mapa_ordem_rotulo=mapa_ordem_rotulo,
        mes_ref=mes_ref,
        fallback_ordem=mes_atual_ordem
    )
    if mes_ref_ordem is None:
        return figura_vazia, ''

    base_mes = base_mes[base_mes['MES_ANO_ORDEM'].eq(int(mes_ref_ordem))].copy()
    if base_mes.empty:
        return figura_vazia, ''

    base_mes['SEGMENTO'] = base_mes['SEGMENTO'].astype(str).str.strip()
    base_mes['INDICADOR'] = base_mes['INDICADOR'].astype(str).str.strip()
    base_mes_plot = (
        base_mes.groupby(['SEGMENTO', 'INDICADOR'], as_index=False, observed=True)['QTDE']
        .sum()
    )

    base_skeleton = pd.MultiIndex.from_product(
        [['PME', 'PF'], indicadores_plot],
        names=['SEGMENTO', 'INDICADOR']
    ).to_frame(index=False)
    base_skeleton['INDICADOR_ORDEM'] = base_skeleton['INDICADOR'].map(
        {label: ordem for _, label, ordem in FUNIL_FIXA_INDICADORES_CONFIG}
    ).fillna(999.0)

    base_plot = base_skeleton.merge(
        base_mes_plot,
        on=['SEGMENTO', 'INDICADOR'],
        how='left'
    )
    base_plot['QTDE'] = normalizar_numerico_serie(base_plot['QTDE']).fillna(0.0)
    base_plot['INDICADOR_NORM'] = base_plot['INDICADOR'].apply(normalizar_chave_visual)
    total_investimento_mes = float(
        pd.to_numeric(
            base_plot.loc[base_plot['INDICADOR_NORM'].eq('investimento'), 'QTDE'],
            errors='coerce'
        ).fillna(0.0).sum()
    )

    fig = make_subplots(
        rows=1,
        cols=2,
        specs=[[{'type': 'funnel'}, {'type': 'funnel'}]],
        horizontal_spacing=0.12,
        subplot_titles=('PME', 'PF')
    )

    segmentos_plot = [
        (1, 'PME', '#790E09', 'rgba(121,14,9,0.16)', '#FFFFFF'),
        (2, 'PF', '#AEAFAF', 'rgba(174,175,175,0.28)', '#312B2A'),
    ]

    for coluna_ref, segmento_ref, cor_ref, cor_conector, cor_texto in segmentos_plot:
        df_seg = (
            base_plot[base_plot['SEGMENTO'].eq(segmento_ref)]
            .sort_values(['INDICADOR_ORDEM', 'INDICADOR'])
            .copy()
        )
        valores = []
        textos = []
        customdata = []
        larguras_visuais = _gerar_larguras_visuais_funil_segmentado(len(df_seg))
        valores_reais = df_seg['QTDE'].astype(float).tolist()
        valores_por_indicador_norm = {
            normalizar_chave_visual(str(linha_seg.get('INDICADOR', '')).strip()): float(linha_seg.get('QTDE', 0.0))
            for _, linha_seg in df_seg.iterrows()
        }
        valor_anterior_real = None
        etapa_anterior = ''

        for idx_seg, (_, linha_seg) in enumerate(df_seg.iterrows()):
            indicador_ref = str(linha_seg.get('INDICADOR', '')).strip()
            valor_real = float(linha_seg.get('QTDE', 0.0))
            valor_rotulo = _formatar_valor_real_funil_segmentado(indicador_ref, valor_real)
            indicador_norm = normalizar_chave_visual(indicador_ref)
            eh_primeiro_step_investimento = valor_anterior_real is None and indicador_norm == 'investimento'
            valor_base_step = valor_anterior_real
            etapa_base_step = etapa_anterior
            if indicador_norm in {'rejeitado', 'venda bruta'} and 'pedidos total' in valores_por_indicador_norm:
                valor_base_step = valores_por_indicador_norm.get('pedidos total')
                etapa_base_step = 'PEDIDOS_TOTAL'
            elif indicador_norm in {'instalacao', 'instalado', 'instalados'} and 'venda bruta' in valores_por_indicador_norm:
                valor_base_step = valores_por_indicador_norm.get('venda bruta')
                etapa_base_step = 'VENDA BRUTA'
            percentual_step = _formatar_percentual_step_funil_segmentado(
                valor_real,
                valor_base_step,
                valor_base_primeiro_step=total_investimento_mes if eh_primeiro_step_investimento else None
            )

            largura_plot = float(larguras_visuais[idx_seg]) if valor_real > 0 else 0.0
            valores.append(largura_plot)
            texto_label = valor_rotulo if not percentual_step else f"{valor_rotulo} | {percentual_step}"
            if percentual_step:
                if eh_primeiro_step_investimento:
                    texto_hover_step = f"<br><b>% do investimento total do mês:</b> {percentual_step}"
                elif etapa_base_step:
                    rotulo_base_step = "% vs etapa anterior" if etapa_base_step == etapa_anterior else "% vs etapa base"
                    texto_hover_step = f"<br><b>{rotulo_base_step} ({etapa_base_step}):</b> {percentual_step}"
                else:
                    texto_hover_step = ""
            else:
                texto_hover_step = ""
            textos.append(texto_label)
            customdata.append([
                segmento_ref,
                valor_rotulo,
                percentual_step,
                etapa_base_step,
                texto_hover_step,
            ])

            valor_anterior_real = valor_real
            etapa_anterior = indicador_ref

        fig.add_trace(go.Funnel(
            name=segmento_ref,
            y=df_seg['INDICADOR'].tolist(),
            x=valores,
            text=textos,
            textinfo='text',
            textposition='auto',
            textfont=dict(size=14, color=cor_texto, family='Segoe UI'),
            marker=dict(
                color=cor_ref,
                line=dict(color='rgba(255,255,255,0.96)', width=1.2)
            ),
            connector=dict(
                line=dict(color=cor_conector, width=1.0)
            ),
            opacity=0.98,
            customdata=customdata,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "<b>Etapa:</b> %{y}<br>"
                "<b>Valor:</b> %{customdata[1]}%{customdata[4]}<extra></extra>"
            )
        ), row=1, col=coluna_ref)

        if sum(valores_reais) <= 0:
            x_pos = 0.22 if segmento_ref == 'PME' else 0.78
            fig.add_annotation(
                x=x_pos,
                y=0.5,
                xref='paper',
                yref='paper',
                text='Sem dados',
                showarrow=False,
                font=dict(size=12, color='#6B5C59', family='Segoe UI')
            )

    fig.update_layout(
        paper_bgcolor='#FFFFFF',
        plot_bgcolor='#FFFFFF',
        margin=dict(l=16, r=16, t=58, b=14),
        height=max(540, 56 * len(indicadores_plot)),
        showlegend=False,
        funnelgap=0.08,
        funnelmode='overlay',
        uniformtext=dict(minsize=12, mode='hide'),
        hoverlabel=dict(
            bgcolor='white',
            bordercolor='#E2E8F0',
            font_size=12,
            font_family='Segoe UI',
            font_color='#2F3747'
        )
    )
    fig.update_yaxes(
        tickfont=dict(size=14, color='#000000', family='Segoe UI'),
        showticklabels=True
    )
    fig.update_xaxes(showticklabels=False, showgrid=False, zeroline=False)
    for anotacao in fig.layout.annotations:
        texto_anotacao = normalizar_chave_visual(getattr(anotacao, 'text', ''))
        if texto_anotacao == 'pme':
            anotacao.font = dict(size=19, family='Sora', color='#201717')
            anotacao.bgcolor = 'rgba(121,14,9,0.08)'
            anotacao.bordercolor = 'rgba(121,14,9,0.18)'
            anotacao.borderwidth = 1
            anotacao.borderpad = 5
            anotacao.yshift = 4
        elif texto_anotacao == 'pf':
            anotacao.font = dict(size=19, family='Sora', color='#201717')
            anotacao.bgcolor = 'rgba(148,163,184,0.18)'
            anotacao.bordercolor = 'rgba(107,114,128,0.22)'
            anotacao.borderwidth = 1
            anotacao.borderpad = 5
            anotacao.yshift = 4
        else:
            anotacao.font = dict(size=15, family='Segoe UI', color='#312B2A')

    observacao_mm3 = ''
    if mes_tend_ordem is not None and int(mes_ref_ordem) == int(mes_tend_ordem):
        meses_ref_tend = sorted(
            [m for m in mapa_ordem_rotulo.keys() if int(m) < int(mes_tend_ordem)]
        )[-3:]
        meses_base = ', '.join(mapa_ordem_rotulo.get(m, str(m)) for m in meses_ref_tend) if meses_ref_tend else ''
        complemento = f" pela proporcao media dos ultimos 3 meses ({meses_base})." if meses_base else '.'
        observacao_mm3 = (
            f"O mes {mapa_ordem_rotulo.get(int(mes_ref_ordem), str(mes_ref_ordem))} foi carregado pelo arquivo de tend"
            f"{complemento}"
        )
    elif mes_atual_ordem is not None and int(mes_ref_ordem) == int(mes_atual_ordem) and meses_mm3:
        meses_base = ', '.join(mapa_ordem_rotulo.get(m, str(m)) for m in meses_mm3)
        observacao_mm3 = (
            f"O mes {mapa_ordem_rotulo.get(int(mes_ref_ordem), str(mes_ref_ordem))} foi ajustado pela media "
            f"movel dos ultimos {len(meses_mm3)} meses ({meses_base})."
        )

    return fig, observacao_mm3


@fragmento_dashboard
def render_visual_funil_fixa_ecommerce() -> None:
    funil_path = resolver_arquivo_dashboard(
        FUNIL_FIXA_FILE_PATH,
        'base_funil_ecomm_fixa.xlsx',
        DASHBOARD_LEGACY_MOBILITY_DIR / 'base_funil_ecomm_fixa.xlsx'
    )
    funil_path = funil_path if Path(funil_path).exists() else None
    tend_path = resolver_arquivo_dashboard(
        TEND_FUNIL_FIXA_FILE_PATH,
        'tend_funil_ecom.xlsx',
        DASHBOARD_LEGACY_MOBILITY_DIR / 'tend_funil_ecom.xlsx'
    )
    tend_path = tend_path if Path(tend_path).exists() else None

    st.markdown(
        build_visual_title_html(
            'FUNIL DE VENDAS - E-COMMERCE',
            'cart',
            subtitle='Acompanhamento da jornada do cliente no e-commerce da Fixa.'
        ),
        unsafe_allow_html=True
    )

    if funil_path is None:
        st.warning('Arquivo do FUNIL FIXA não encontrado no caminho configurado.')
        st.code(str(FUNIL_FIXA_FILE_PATH))
        return

    funil_mtime = Path(funil_path).stat().st_mtime if Path(funil_path).exists() else None
    tend_mtime = Path(tend_path).stat().st_mtime if tend_path is not None and Path(tend_path).exists() else None
    df_funil = load_funil_fixa_ecommerce_data(
        str(funil_path),
        funil_mtime,
        str(tend_path) if tend_path is not None else None,
        tend_mtime
    )
    if df_funil.empty:
        st.warning('Não foi possível carregar dados válidos do funil FIXA/E-Commerce.')
        return

    segmentos_disp = [
        segmento for segmento in ['PF', 'PME']
        if segmento in set(df_funil['SEGMENTO'].dropna().astype(str).str.strip().unique().tolist())
    ]
    opcoes_segmento = ['Todos'] + segmentos_disp
    indice_padrao_segmento = opcoes_segmento.index('PME') if 'PME' in opcoes_segmento else 0
    meses_funil_disp = (
        df_funil[['MES_ANO_ORDEM', 'MES_ANO']]
        .drop_duplicates()
        .sort_values(['MES_ANO_ORDEM', 'MES_ANO'])
        .drop_duplicates(subset=['MES_ANO_ORDEM'], keep='last')
    )
    meses_funil_labels = meses_funil_disp['MES_ANO'].astype(str).str.strip().str.lower().tolist()
    mapa_mes_funil_ordem = {
        str(linha['MES_ANO']).strip().lower(): int(linha['MES_ANO_ORDEM'])
        for _, linha in meses_funil_disp.iterrows()
    }
    mes_funil_default = meses_funil_labels[-1] if meses_funil_labels else get_mes_atual_formatado().strip().lower()

    col_f1, col_f2, col_f3, col_f4 = st.columns([0.9, 1.05, 1.2, 0.95], gap="medium")

    with col_f1:
        render_filter_label('Segmento')
        segmento_sel = st.selectbox(
            'Filtro de segmento do funil fixa',
            options=opcoes_segmento,
            index=indice_padrao_segmento,
            key='funil_fixa_segmento_v2',
            label_visibility='collapsed'
        )

    base_origem_ref = (
        df_funil
        if segmento_sel == 'Todos'
        else df_funil.loc[df_funil['SEGMENTO'].astype(str).eq(str(segmento_sel))]
    )
    origens_disp = sorted(base_origem_ref['ORIGEM_AGG'].dropna().astype(str).str.strip().unique().tolist())
    opcoes_origem = ['Todos'] + origens_disp
    with col_f2:
        render_filter_label('Origem')
        origem_sel = st.selectbox(
            'Filtro de origem do funil fixa',
            options=opcoes_origem,
            index=0,
            key='funil_fixa_origem_v2',
            label_visibility='collapsed'
        )

    base_canal_entrada_ref = (
        base_origem_ref
        if origem_sel == 'Todos'
        else base_origem_ref.loc[base_origem_ref['ORIGEM_AGG'].astype(str).eq(str(origem_sel))]
    )
    canais_entrada_disp = (
        sorted(base_canal_entrada_ref['CANAL_ENTRADA'].dropna().astype(str).str.strip().unique().tolist())
        if 'CANAL_ENTRADA' in base_canal_entrada_ref.columns
        else []
    )
    opcoes_canal_entrada = ['Todos'] + canais_entrada_disp
    with col_f3:
        render_filter_label('CANAL DE ENTRADA')
        canal_entrada_sel = st.selectbox(
            'Filtro de canal de entrada do funil fixa',
            options=opcoes_canal_entrada,
            index=0,
            key='funil_fixa_canal_entrada_v1',
            label_visibility='collapsed'
        )
    with col_f4:
        render_filter_label('Mês do gráfico')
        mes_grafico_sel = st.selectbox(
            'Filtro de mês do gráfico do funil fixa',
            options=meses_funil_labels,
            index=meses_funil_labels.index(mes_funil_default) if mes_funil_default in meses_funil_labels else 0,
            key='funil_fixa_mes_grafico_v1',
            label_visibility='collapsed'
        )

    segmentos_sel = None if segmento_sel == 'Todos' else [segmento_sel]
    origens_sel = None if origem_sel == 'Todos' else [origem_sel]
    canais_entrada_sel = None if canal_entrada_sel == 'Todos' else [canal_entrada_sel]
    indicadores_sel = None
    mes_ref_ordem_sel = mapa_mes_funil_ordem.get(str(mes_grafico_sel).strip().lower())

    estrutura = montar_estrutura_funil_fixa_ecommerce(
        df_funil=df_funil,
        segmentos_sel=segmentos_sel,
        origens_sel=origens_sel,
        canais_entrada_sel=canais_entrada_sel,
        indicadores_sel=indicadores_sel,
        mes_ref_ordem=mes_ref_ordem_sel,
        qtd_meses=13
    )

    if not estrutura.get('rows'):
        st.info('Sem dados para os filtros selecionados.')
        return

    altura_tabela = max(520, 118 + 36 * len(estrutura.get('rows', [])))
    altura_corpo_tabela = min(max(altura_tabela - 84, 360), 840)
    html_tabela = obter_cache_session_dashboard(
        "html_funil_fixa_ecommerce_valor",
        (
            funil_mtime,
            tend_mtime,
            tuple(segmentos_sel or []),
            tuple(origens_sel or []),
            tuple(canais_entrada_sel or []),
            mes_ref_ordem_sel,
            altura_corpo_tabela,
        ),
        lambda: criar_tabela_html_funil_fixa_ecommerce(
            estrutura,
            table_id='funil-fixa-ecommerce',
            max_body_height=altura_corpo_tabela
        ),
        max_variacoes=2
    )
    components.html(
        html_tabela,
        height=min(altura_corpo_tabela + 52, 900),
        scrolling=False
    )

    estrutura_percentual = montar_estrutura_funil_fixa_ecommerce_percentual(
        df_funil=df_funil,
        segmentos_sel=segmentos_sel,
        origens_sel=origens_sel,
        canais_entrada_sel=canais_entrada_sel,
        indicadores_sel=indicadores_sel,
        mes_ref_ordem=mes_ref_ordem_sel,
        qtd_meses=13
    )
    if estrutura_percentual.get('rows'):
        st.markdown(
            build_visual_title_html(
                'FUNIL E-COMMERCE - CONVERSÃO POR ETAPA',
                'cart',
                'subsection-title',
                subtitle='Etapas de SESSÕES até INSTALAÇÃO • MoM e YoY em p.p.',
                extra_style='margin-top:12px;'
            ),
            unsafe_allow_html=True
        )
        altura_tabela_percentual = max(500, 118 + 36 * len(estrutura_percentual.get('rows', [])))
        altura_corpo_tabela_percentual = min(max(altura_tabela_percentual - 84, 360), 760)
        html_tabela_percentual = obter_cache_session_dashboard(
            "html_funil_fixa_ecommerce_percentual",
            (
                funil_mtime,
                tend_mtime,
                tuple(segmentos_sel or []),
                tuple(origens_sel or []),
                tuple(canais_entrada_sel or []),
                mes_ref_ordem_sel,
                altura_corpo_tabela_percentual,
            ),
            lambda: criar_tabela_html_funil_fixa_ecommerce(
                estrutura_percentual,
                table_id='funil-fixa-ecommerce-percentual',
                max_body_height=altura_corpo_tabela_percentual
            ),
            max_variacoes=2
        )
        components.html(
            html_tabela_percentual,
            height=min(altura_corpo_tabela_percentual + 52, 820),
            scrolling=False
        )

    st.markdown(
        build_visual_title_html(
            'FUNIL POR SEGMENTO - PME X PF',
            'cart',
            'subsection-title',
            subtitle=f"MÊS: {str(mes_grafico_sel).upper()}",
            extra_style='margin-top:-10px;'
        ),
        unsafe_allow_html=True
    )
    fig_funil_segmentado, observacao_funil_segmentado = criar_grafico_funil_segmentado_fixa(
        df_funil=df_funil,
        mes_ref=mes_grafico_sel,
        origens_sel=origens_sel,
        canais_entrada_sel=canais_entrada_sel,
        indicadores_sel=indicadores_sel
    )
    if not fig_funil_segmentado.data:
        st.info('Sem dados disponíveis para montar o gráfico do funil no mês selecionado.')
    else:
        st.plotly_chart(
            fig_funil_segmentado,
            width='stretch',
            config={'displayModeBar': False, 'displaylogo': False}
        )


@fragmento_dashboard
def render_bloco_backlog_fixa_pme() -> None:
    st.markdown(
        build_visual_title_html(
            "BACKLOG FIXA PME - CONTRATOS POR CANAL E MÊS",
            "grid",
            "subsection-title",
            extra_style="margin-top:18px;"
        ),
        unsafe_allow_html=True
    )

    backlog_path = resolver_arquivo_preprocessado("backlog_consolidado_limpo.parquet")
    backlog_path = backlog_path if Path(backlog_path).exists() else None

    if backlog_path is None:
        st.info("Arquivo `dados_preprocessados/backlog_consolidado_limpo.parquet` não encontrado. Rode `preprocess_all.py` para gerar a base otimizada de Backlog.")
        return

    backlog_mtime = Path(backlog_path).stat().st_mtime if Path(backlog_path).exists() else None
    df_backlog_consolidado = load_backlog_consolidado_data(str(backlog_path), backlog_mtime)
    tabela_backlog_fmt, tabela_backlog_num = montar_tabela_backlog_canais(df_backlog_consolidado)

    if tabela_backlog_fmt.empty:
        st.info("Sem dados disponíveis para montar a tabela de backlog.")
    else:
        st.markdown(
            criar_tabela_html_backlog_canais(
                df_formatado=tabela_backlog_fmt,
                df_numerico=tabela_backlog_num,
                table_id="tabela-funil-fixa-backlog-fixa-pme"
            ),
            unsafe_allow_html=True
        )

    if "NOME_OS_TIPO_STATUS_AGENDA" not in df_backlog_consolidado.columns:
        return

    st.markdown(
        build_visual_title_html(
            "BACKLOG - STATUS DE AGENDA",
            "trend",
            "subsection-title",
            extra_style="margin-top:18px;"
        ),
        unsafe_allow_html=True
    )

    meses_backlog_status = sorted(
        df_backlog_consolidado["MES_ANO"].dropna().astype(str).str.strip().str.lower().unique().tolist(),
        key=mes_ano_para_data
    )
    canais_backlog_status = sorted(
        df_backlog_consolidado["NM_CANAL_VENDA_SUBGRUPO"].dropna().astype(str).str.strip().unique().tolist()
    )
    regionais_backlog_status = sorted(
        df_backlog_consolidado["NM_REGIONAL"].dropna().astype(str).str.strip().unique().tolist()
    )
    mes_backlog_status_default = (
        get_mes_atual_formatado().strip().lower()
        if get_mes_atual_formatado().strip().lower() in meses_backlog_status
        else (meses_backlog_status[-1] if meses_backlog_status else get_mes_atual_formatado().strip().lower())
    )

    col_backlog_status_mes, col_backlog_status_canal, col_backlog_status_regional = st.columns([0.85, 1.25, 1.35], gap="medium")
    with col_backlog_status_mes:
        render_filter_label("MÊS")
        mes_backlog_status_sel = st.selectbox(
            "Selecione o mês do backlog por status",
            options=meses_backlog_status or [mes_backlog_status_default],
            index=(meses_backlog_status.index(mes_backlog_status_default) if mes_backlog_status_default in meses_backlog_status else 0),
            key="backlog_status_mes",
            label_visibility="collapsed"
        )
    with col_backlog_status_canal:
        render_filter_label("CANAL")
        canal_backlog_status_sel = st.selectbox(
            "Selecione o canal do backlog por status",
            options=["Todos", *canais_backlog_status],
            index=0,
            key="backlog_status_canal",
            label_visibility="collapsed"
        )
    with col_backlog_status_regional:
        render_filter_label("REGIONAL")
        regional_backlog_status_sel = st.selectbox(
            "Selecione a regional do backlog por status",
            options=["Todos", *regionais_backlog_status],
            index=0,
            key="backlog_status_regional",
            label_visibility="collapsed"
        )

    fig_backlog_status = criar_grafico_cascata_backlog_status(
        df_backlog=df_backlog_consolidado,
        mes_ref=mes_backlog_status_sel,
        canal_ref=canal_backlog_status_sel,
        regional_ref=regional_backlog_status_sel
    )
    if not fig_backlog_status.data:
        st.info("Sem dados disponíveis para montar o gráfico de status de agenda.")
    else:
        st.plotly_chart(
            fig_backlog_status,
            width='stretch',
            config={'displayModeBar': 'hover', 'displaylogo': False}
        )


@fragmento_dashboard
def render_bloco_migracoes_pme() -> None:
    st.markdown(
        build_visual_title_html(
            "MIGRAÇÕES PME - QTDE POR REGIONAL E MÊS",
            "grid",
            "subsection-title",
            extra_style="margin-top:18px;"
        ),
        unsafe_allow_html=True
    )

    migracoes_path = resolver_arquivo_dashboard(
        ANALITICO_MIGRACOES_FILE_PATH,
        "ANALITICO_MIGRACOES_fev26.xlsx"
    )
    migracoes_path = migracoes_path if Path(migracoes_path).exists() else None

    if migracoes_path is None:
        st.info("Arquivo `ANALITICO_MIGRACOES_fev26.xlsx` não encontrado no caminho configurado.")
        return

    migracoes_mtime = Path(migracoes_path).stat().st_mtime if Path(migracoes_path).exists() else None
    df_migracoes_pme = load_migracoes_pme_data(str(migracoes_path), migracoes_mtime)

    if df_migracoes_pme.empty:
        st.info("Sem dados disponíveis para montar a tabela de Migrações PME.")
        return

    opcoes_mes_mom_migracoes, mes_tend_migracoes = obter_meses_mom_migracoes(df_migracoes_pme)
    if not opcoes_mes_mom_migracoes:
        st.info("Sem meses disponíveis para montar a tabela de Migrações PME.")
        tabela_migracoes_fmt, tabela_migracoes_num = pd.DataFrame(), pd.DataFrame()
    else:
        col_mig_mes_mom, col_mig_mes_spacer = st.columns([0.85, 2.65], gap="medium")
        with col_mig_mes_mom:
            render_filter_label("Mês MoM")
            indice_mes_tend_migracoes = (
                opcoes_mes_mom_migracoes.index(mes_tend_migracoes)
                if mes_tend_migracoes in opcoes_mes_mom_migracoes
                else max(len(opcoes_mes_mom_migracoes) - 1, 0)
            )
            mes_migracoes_mom_ref = st.selectbox(
                "Selecione o mês de referência do MoM de Migrações PME",
                options=opcoes_mes_mom_migracoes,
                index=indice_mes_tend_migracoes,
                key="funil_movel_migracoes_pme_mes_mom",
                label_visibility="collapsed",
                format_func=lambda mes: (
                    f"{str(mes).upper()} (TEND.)"
                    if str(mes).strip().lower() == str(mes_tend_migracoes).strip().lower()
                    else str(mes).upper()
                )
            )
        with col_mig_mes_spacer:
            st.empty()

        tabela_migracoes_fmt, tabela_migracoes_num = montar_tabela_migracoes_pme_regionais(
            df_migracoes_pme,
            mes_mom_ref=mes_migracoes_mom_ref
        )

    if tabela_migracoes_fmt.empty:
        st.info("Sem dados disponíveis para montar a tabela de Migrações PME.")
    else:
        st.markdown(
            criar_tabela_html_migracoes_regionais(
                df_formatado=tabela_migracoes_fmt,
                df_numerico=tabela_migracoes_num,
                table_id="tabela-analitico-migracoes-pme"
            ),
            unsafe_allow_html=True
        )

    st.markdown(
        build_visual_title_html(
            "MIGRAÇÕES PME - EVOLUÇÃO MENSAL",
            "trend",
            "subsection-title",
            extra_style="margin-top:14px;"
        ),
        unsafe_allow_html=True
    )

    if tabela_migracoes_num.empty:
        st.info("Sem dados disponíveis para montar o gráfico mensal de Migrações PME.")
        return

    regionais_migracoes_disp = [
        regional for regional in tabela_migracoes_num["REGIONAL"].astype(str).tolist()
        if str(regional).strip().upper() != "TOTAL"
    ]
    opcoes_regional_migracoes = ["Todos"] + regionais_migracoes_disp

    col_mig_filtro, col_mig_spacer = st.columns([0.95, 2.55], gap="medium")
    with col_mig_filtro:
        render_filter_label("Regional")
        regional_migracoes_ref = st.selectbox(
            "Selecione a regional de Migrações PME",
            options=opcoes_regional_migracoes,
            index=0,
            key="funil_movel_migracoes_pme_regional",
            label_visibility="collapsed"
        )
    with col_mig_spacer:
        st.empty()

    serie_grafico_migracoes = montar_serie_grafico_migracoes_pme(
        tabela_migracoes_num,
        regional_ref=regional_migracoes_ref
    )
    if serie_grafico_migracoes.empty:
        st.info("Sem dados disponíveis para montar o gráfico mensal de Migrações PME.")
    else:
        fig_migracoes_mensal = criar_grafico_migracoes_pme_mensal(
            serie_grafico_migracoes,
            regional_ref=regional_migracoes_ref
        )
        st.plotly_chart(
            fig_migracoes_mensal,
            width="stretch",
            config={"displayModeBar": False, "displaylogo": False}
        )


@fragmento_dashboard
def render_bloco_cotacoes_funil_movel() -> None:
    cotacoes_path = resolver_arquivo_dashboard(
        COTACOES_FILE_PATH,
        "RelatorioFluxoVidaCotacao.xlsx"
    )
    cotacoes_path = cotacoes_path if Path(cotacoes_path).exists() else None

    if cotacoes_path is None:
        return

    cotacoes_mtime = Path(cotacoes_path).stat().st_mtime if Path(cotacoes_path).exists() else None
    df_cotacoes_base_movel = load_cotacoes_data(
        str(cotacoes_path),
        cotacoes_mtime,
        COTACOES_CACHE_VERSION
    )
    df_cotacoes_tabela_movel = preparar_agregados_cotacoes(
        str(cotacoes_path),
        cotacoes_mtime,
        COTACOES_CACHE_VERSION
    )

    if df_cotacoes_tabela_movel is not None and not getattr(df_cotacoes_tabela_movel, "empty", True):
        st.markdown(
            build_visual_title_html("COTAÇÕES - VALOR MÊS A MÊS POR CANAL", "grid", "subsection-title", extra_style="margin-top:18px;"),
            unsafe_allow_html=True
        )
        tabela_cotacoes_fmt, tabela_cotacoes_num = montar_tabela_cotacoes_canais_mensal(df_cotacoes_tabela_movel)
        if tabela_cotacoes_fmt.empty:
            st.info("Sem dados disponíveis para montar a tabela mensal de COTAÇÕES.")
        else:
            st.markdown(
                criar_tabela_html_backlog_canais(
                    df_formatado=tabela_cotacoes_fmt,
                    df_numerico=tabela_cotacoes_num,
                    table_id="tabela-funil-movel-cotacoes-valor-mensal"
                ),
                unsafe_allow_html=True
            )
            st.caption("Tabela consolidada por canal e mês, sem aplicar o filtro de regional.")

    base_funil_cotacoes = globals().get("df_perf_base", pd.DataFrame())
    if base_funil_cotacoes is None or getattr(base_funil_cotacoes, "empty", True):
        perf_path_obj = Path(BASE_PERFORMANCE_FILE_PATH)
        perf_mtime = perf_path_obj.stat().st_mtime if perf_path_obj.exists() else None
        base_funil_cotacoes = load_base_performance_data(str(BASE_PERFORMANCE_FILE_PATH), perf_mtime)
    if (base_funil_cotacoes is None or getattr(base_funil_cotacoes, "empty", True)) and "preparar_base_performance" in globals():
        try:
            df_origem_principal = globals().get("df", pd.DataFrame())
            if df_origem_principal is not None and not getattr(df_origem_principal, "empty", True):
                base_funil_cotacoes = preparar_base_performance(df_origem_principal)
        except Exception:
            base_funil_cotacoes = pd.DataFrame()

    if (
        base_funil_cotacoes is None or getattr(base_funil_cotacoes, "empty", True) or
        df_cotacoes_base_movel is None or getattr(df_cotacoes_base_movel, "empty", True)
    ):
        return

    st.markdown(
        build_visual_title_html("FUNIL CONTA - PEDIDOS X COTAÇÃO X ATIVAÇÃO", "target", "subsection-title", extra_style="margin-top:18px;"),
        unsafe_allow_html=True
    )

    canais_funil_base = []
    if "CANAL_PLAN" in base_funil_cotacoes.columns:
        canais_funil_base.extend(
            [
                _mapear_canal_funil_cotacoes(valor)
                for valor in base_funil_cotacoes["CANAL_PLAN"].dropna().tolist()
            ]
        )
    if "CANAL_PLAN" in df_cotacoes_base_movel.columns:
        canais_funil_base.extend(
            [
                _mapear_canal_funil_cotacoes(valor)
                for valor in df_cotacoes_base_movel["CANAL_PLAN"].dropna().tolist()
            ]
        )
    canais_funil_opcoes = ["Todos"] + _ordenar_canais_funil_cotacoes(
        [canal for canal in canais_funil_base if str(canal).strip()]
    )

    meses_funil_union = []
    if "dat_tratada" in base_funil_cotacoes.columns:
        meses_funil_union.extend(
            base_funil_cotacoes["dat_tratada"].dropna().astype(str).str.strip().str.lower().tolist()
        )
    col_mes_cot = "mes_ano" if "mes_ano" in df_cotacoes_base_movel.columns else "dat_tratada"
    if col_mes_cot in df_cotacoes_base_movel.columns:
        meses_funil_union.extend(
            df_cotacoes_base_movel[col_mes_cot].dropna().astype(str).str.strip().str.lower().tolist()
        )
    meses_funil_opcoes = sorted(
        [
            mes for mes in set(meses_funil_union)
            if re.match(r"^[a-z]{3}/\d{2}$", str(mes).strip(), flags=re.IGNORECASE)
        ],
        key=mes_ano_para_data
    )
    if not meses_funil_opcoes:
        st.info("Sem dados disponíveis para montar a tabela do funil CONTA.")
        return

    mes_funil_default = get_mes_atual_formatado().strip().lower()
    if mes_funil_default not in meses_funil_opcoes:
        mes_funil_default = meses_funil_opcoes[-1]

    col_funil_c1, col_funil_c2 = st.columns([1.05, 0.95], gap="medium")
    with col_funil_c1:
        render_filter_label("Canal")
        canal_funil_sel = st.selectbox(
            "Selecione o canal do funil conta",
            options=canais_funil_opcoes,
            index=0,
            key="funil_conta_cotacoes_canal",
            label_visibility="collapsed"
        )
    with col_funil_c2:
        render_filter_label("Mês Atual")
        mes_funil_sel = st.selectbox(
            "Selecione o mês do funil conta",
            options=meses_funil_opcoes,
            index=meses_funil_opcoes.index(mes_funil_default) if mes_funil_default in meses_funil_opcoes else len(meses_funil_opcoes) - 1,
            key="funil_conta_cotacoes_mes",
            format_func=lambda valor: str(valor).upper(),
            label_visibility="collapsed"
        )

    tabela_funil_conta_fmt, tabela_funil_conta_num = montar_tabela_funil_cotacoes(
        df_base_principal=base_funil_cotacoes,
        df_cotacoes_base=df_cotacoes_base_movel,
        canal_ref=canal_funil_sel,
        mes_ref=mes_funil_sel
    )

    if tabela_funil_conta_fmt.empty:
        st.info("Sem dados disponíveis para montar a tabela do funil CONTA.")
    else:
        st.markdown(
            cached_tabela_html_funil_cotacoes(
                serializar_dataframe_cache(tabela_funil_conta_fmt),
                serializar_dataframe_cache(tabela_funil_conta_num),
                "tabela-funil-conta-cotacoes-ativacao"
            ),
            unsafe_allow_html=True
        )

file_path = str(PRIMARY_BASE_FILE_PATH)
file_mtime = Path(file_path).stat().st_mtime if Path(file_path).exists() else None
df = load_data(file_path, file_mtime)

validate_data(df)

def obter_opcoes_filtros_globais_cached(_df_base: pd.DataFrame, file_mtime_ref: float | None) -> dict[str, list]:
    """Memoiza opções dos filtros gerais sem serializar a base."""
    agora = time.time()
    cache = st.session_state.setdefault("_dashboard_opcoes_filtros_cache", OrderedDict())
    if not isinstance(cache, OrderedDict):
        cache = OrderedDict(cache)
    cache_key = file_mtime_ref
    if cache_key in cache:
        opcoes_cache, ts_cache = _desempacotar_item_cache_session(cache.pop(cache_key))
        if ts_cache is None or (agora - ts_cache) <= SESSION_CACHE_TTL_SECONDS:
            cache[cache_key] = (opcoes_cache, agora)
            st.session_state["_dashboard_opcoes_filtros_cache"] = cache
            return opcoes_cache

    opcoes = {
        "regionais": _df_base["REGIONAL"].unique().tolist(),
        "canais": _df_base["CANAL_PLAN"].unique().tolist(),
        "periodos": _df_base["dat_tratada"].unique().tolist(),
        "indicadores": _df_base["DSC_INDICADOR"].unique().tolist(),
    }
    cache.clear()
    cache[cache_key] = (opcoes, agora)
    st.session_state["_dashboard_opcoes_filtros_cache"] = cache
    return opcoes

opcoes_filtros_globais = obter_opcoes_filtros_globais_cached(df, file_mtime)

with st.sidebar:
    with st.expander("⚙️ FILTROS GERAIS", expanded=False):
        st.markdown("**🔍 Filtre os dados abaixo:**")
        
        region_filter = st.multiselect(
            "**Regional:**", 
            options=opcoes_filtros_globais["regionais"], 
            default=[],
            help="Selecione uma ou mais regionais"
        )
        
        canal_filter = st.multiselect(
            "**Canal:**", 
            options=opcoes_filtros_globais["canais"], 
            default=[],
            help="Selecione um ou mais canais"
        )
        
        data_filter = st.multiselect(
            "**Período:**", 
            options=opcoes_filtros_globais["periodos"], 
            default=[],
            help="Selecione um ou mais períodos"
        )
        
        indicador_filter = st.multiselect(
            "**Indicador:**", 
            options=opcoes_filtros_globais["indicadores"], 
            default=["Instalação", "GROSS LIQUIDO"],
            help="Selecione um ou mais indicadores"
        )
        
        st.markdown("---")
        st.markdown("**ℹ️ Informações:**")
        st.info(f"Total de registros: {len(df):,}")

def aplicar_filtros_globais(
    df_base: pd.DataFrame,
    regionais: list[str],
    canais: list[str],
    periodos: list[str],
    indicadores: list[str],
    incluir_periodo: bool = True
) -> pd.DataFrame:
    """Aplica filtros globais com máscara booleana (mais eficiente que query em alto volume)."""
    if df_base.empty:
        return df_base

    mask = np.ones(len(df_base), dtype=bool)
    if regionais:
        mask &= df_base['REGIONAL'].isin(set(regionais)).to_numpy()
    if canais:
        mask &= df_base['CANAL_PLAN'].isin(set(canais)).to_numpy()
    if incluir_periodo and periodos:
        mask &= df_base['dat_tratada'].isin(set(periodos)).to_numpy()
    if indicadores:
        mask &= df_base['DSC_INDICADOR'].isin(set(indicadores)).to_numpy()
    return df_base.loc[mask]

def aplicar_filtros_globais_cached(
    _df_base: pd.DataFrame,
    file_mtime_ref: float | None,
    regionais: tuple[str, ...],
    canais: tuple[str, ...],
    periodos: tuple[str, ...],
    indicadores: tuple[str, ...],
    incluir_periodo: bool = True
) -> pd.DataFrame:
    """Memoiza filtros globais por sessão sem serializar o DataFrame inteiro."""
    agora = time.time()
    cache_key = (
        file_mtime_ref,
        tuple(regionais),
        tuple(canais),
        tuple(periodos),
        tuple(indicadores),
        bool(incluir_periodo),
    )
    cache = st.session_state.setdefault("_dashboard_filtros_globais_cache", OrderedDict())
    if not isinstance(cache, OrderedDict):
        cache = OrderedDict(cache)

    for chave_existente, item_existente in list(cache.items()):
        _, ts_existente = _desempacotar_item_cache_session(item_existente)
        if ts_existente is not None and (agora - ts_existente) > SESSION_CACHE_TTL_SECONDS:
            cache.pop(chave_existente, None)

    if cache_key in cache:
        indice_cache, ts_cache = _desempacotar_item_cache_session(cache.pop(cache_key))
        if ts_cache is None or (agora - ts_cache) <= SESSION_CACHE_TTL_SECONDS:
            cache[cache_key] = (indice_cache, agora)
            st.session_state["_dashboard_filtros_globais_cache"] = cache
            try:
                return _df_base.loc[indice_cache].copy(deep=False)
            except Exception:
                pass

    resultado = aplicar_filtros_globais(
        _df_base,
        list(regionais),
        list(canais),
        list(periodos),
        list(indicadores),
        incluir_periodo=incluir_periodo
    )

    memoria_resultado = 0
    try:
        memoria_resultado = int(resultado.memory_usage(index=True, deep=False).sum())
    except Exception:
        memoria_resultado = 0

    reter_em_cache = (
        resultado.empty or (
            len(resultado) <= 30_000 and
            memoria_resultado <= 5 * 1024 * 1024 and
            (len(_df_base) == 0 or len(resultado) < int(len(_df_base) * 0.35))
        )
    )

    if reter_em_cache:
        cache[cache_key] = (resultado.index.copy(), agora)
        while len(cache) > CACHE_MAX_ENTRIES_FILTERS:
            cache.popitem(last=False)
        st.session_state["_dashboard_filtros_globais_cache"] = cache

    return resultado.copy(deep=False)

region_filter_key = tuple(str(item) for item in region_filter)
canal_filter_key = tuple(str(item) for item in canal_filter)
data_filter_key = tuple(str(item) for item in data_filter)
indicador_filter_key = tuple(str(item) for item in indicador_filter)

st.markdown("""
    <div class="main-title">
        CANAIS ESTRATÉGICOS
    </div>
""", unsafe_allow_html=True)

st.markdown("""
    <div class="dashboard-hero-divider" aria-hidden="true">
        <span class="dashboard-hero-divider-line"></span>
        <span class="dashboard-hero-divider-badge">
            <i></i><i></i><i></i>
        </span>
    </div>
""", unsafe_allow_html=True)

home_inicio_ctx: dict[str, object] = {}
labels_abas_dashboard = [
    "INICIO",
    "ATIVADOS",
    "PEDIDOS",
    "LIGACOES",
    "FUNIL_MOVEL",
    "DESATIVACOES",
]
st.markdown("""
<style>
div.st-key-dashboard_tab_ativa div[role="radiogroup"] {
    display: grid !important;
    grid-template-columns: repeat(6, minmax(0, 1fr)) !important;
    gap: 0.45rem !important;
    margin: 0.25rem 0 1.1rem 0 !important;
}
div.st-key-dashboard_tab_ativa div[role="radiogroup"] label {
    min-height: 3rem !important;
    border: 1px solid rgba(121, 14, 9, 0.14) !important;
    border-radius: 999px !important;
    background: linear-gradient(180deg, #FFFFFF 0%, #FFF7F6 100%) !important;
    box-shadow: 0 0.45rem 1rem rgba(121, 14, 9, 0.08), inset 0 1px 0 rgba(255,255,255,0.92) !important;
    justify-content: center !important;
    padding: 0.35rem 0.55rem !important;
}
div.st-key-dashboard_tab_ativa div[role="radiogroup"] label:has(input:checked) {
    border-color: rgba(121, 14, 9, 0.55) !important;
    background: linear-gradient(135deg, #790E09 0%, #5A0A06 100%) !important;
    color: #FFFFFF !important;
    box-shadow: 0 0.7rem 1.35rem rgba(121, 14, 9, 0.22) !important;
}
div.st-key-dashboard_tab_ativa div[role="radiogroup"] label:has(input:checked) p,
div.st-key-dashboard_tab_ativa div[role="radiogroup"] label:has(input:checked) span {
    color: #FFFFFF !important;
}
div.st-key-dashboard_tab_ativa div[role="radiogroup"] input {
    display: none !important;
}
div.st-key-dashboard_tab_ativa div[role="radiogroup"] p {
    font-weight: 800 !important;
    font-size: clamp(0.72rem, 0.86vw, 0.86rem) !important;
    color: #790E09 !important;
    white-space: nowrap !important;
}
@media (max-width: 900px) {
    div.st-key-dashboard_tab_ativa div[role="radiogroup"] {
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    }
}
</style>
""", unsafe_allow_html=True)

_label_abas_display = {
    "INICIO": "INÍCIO",
    "ATIVADOS": "ATIVADOS",
    "PEDIDOS": "E-COMMERCE",
    "LIGACOES": "TELEVENDAS",
    "FUNIL_MOVEL": "EM CONSTRUÇÃO",
    "DESATIVACOES": "DESATIVAÇÕES",
}

_aba_padrao_dashboard = st.session_state.get("dashboard_tab_ativa", "INICIO")
if _aba_padrao_dashboard not in labels_abas_dashboard:
    _aba_padrao_dashboard = "INICIO"

aba_dashboard_ativa = st.radio(
    "Navegacao principal do dashboard",
    options=labels_abas_dashboard,
    index=labels_abas_dashboard.index(_aba_padrao_dashboard),
    key="dashboard_tab_ativa",
    horizontal=True,
    label_visibility="collapsed",
    format_func=lambda valor: _label_abas_display.get(valor, valor),
)

tab0 = st.container()
tab1 = st.container()
tab3 = st.container()
tab4 = st.container()
tab5 = st.container()
tab2 = st.container()

tab_inicio_ativa = aba_dashboard_ativa == "INICIO"
tab_ativados_ativa = aba_dashboard_ativa == "ATIVADOS"
tab_desativacoes_ativa = aba_dashboard_ativa == "DESATIVACOES"
tab_pedidos_ativa = aba_dashboard_ativa == "PEDIDOS"
tab_ligacoes_ativa = aba_dashboard_ativa == "LIGACOES"
tab_funil_movel_ativa = aba_dashboard_ativa == "FUNIL_MOVEL"

st.markdown(
    """
    <style>
    .stPlotlyChart,
    div[data-testid="stPlotlyChart"] {
        overflow: hidden !important;
        overflow-y: hidden !important;
        max-height: none !important;
    }

    div[data-testid="stPlotlyChart"] > div,
    div[data-testid="stPlotlyChart"] .js-plotly-plot,
    div[data-testid="stPlotlyChart"] .plot-container,
    div[data-testid="stPlotlyChart"] .svg-container {
        overflow: hidden !important;
        max-width: 100% !important;
    }

    div[data-testid="stPlotlyChart"]::-webkit-scrollbar,
    div[data-testid="stPlotlyChart"] *::-webkit-scrollbar {
        width: 0 !important;
        height: 0 !important;
        display: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <style>
    body .tabela-analitico-migracoes-pme-container,
    body .tabela-funil-movel-cotacoes-valor-mensal-container,
    body #tabela-funil-conta-cotacoes-ativacao.tabela-container-funil-cotacoes {
        border: 1px solid rgba(121, 14, 9, 0.18) !important;
        border-radius: 4px !important;
        box-shadow: none !important;
        background: #FFFFFF !important;
    }

    body #tabela-funil-conta-cotacoes-ativacao.tabela-container-funil-cotacoes::before {
        content: none !important;
        display: none !important;
    }

    body table.tabela-analitico-migracoes-pme,
    body table.tabela-funil-movel-cotacoes-valor-mensal,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes {
        background: #FFFFFF !important;
        box-shadow: none !important;
    }

    body table.tabela-analitico-migracoes-pme th,
    body table.tabela-funil-movel-cotacoes-valor-mensal th,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes th {
        background: #790E09 !important;
        text-shadow: none !important;
        box-shadow: none !important;
        border-bottom: 1px solid rgba(61, 7, 4, 0.72) !important;
    }

    body table.tabela-analitico-migracoes-pme th.col-tend,
    body table.tabela-analitico-migracoes-pme th.col-mom,
    body table.tabela-funil-movel-cotacoes-valor-mensal th.col-total-mes,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes th.col-var,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes th.col-tend {
        background: #8E241D !important;
    }

    body table.tabela-analitico-migracoes-pme td,
    body table.tabela-funil-movel-cotacoes-valor-mensal td,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes td {
        border-bottom: 1px solid #E6E6E6 !important;
        border-right: 1px solid #EFEFEF !important;
        background: #FFFFFF !important;
        box-shadow: none !important;
        text-shadow: none !important;
    }

    body table.tabela-analitico-migracoes-pme th,
    body table.tabela-funil-movel-cotacoes-valor-mensal th,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes th,
    body table.tabela-analitico-migracoes-pme td,
    body table.tabela-funil-movel-cotacoes-valor-mensal td,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes td {
        padding: 6px 5px !important;
    }

    body table.tabela-analitico-migracoes-pme td,
    body table.tabela-funil-movel-cotacoes-valor-mensal td,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes td {
        font-size: clamp(10.5px, 0.72vw, 11.2px) !important;
        font-weight: 600 !important;
    }

    body table.tabela-analitico-migracoes-pme tbody tr:nth-child(even) td,
    body table.tabela-funil-movel-cotacoes-valor-mensal tbody tr:nth-child(even) td,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes tbody tr:nth-child(even) td {
        background: #FAFAFA !important;
    }

    body table.tabela-analitico-migracoes-pme tbody tr:hover td,
    body table.tabela-funil-movel-cotacoes-valor-mensal tbody tr:hover td,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes tbody tr:hover td {
        background: #FFF8F7 !important;
    }

    body table.tabela-analitico-migracoes-pme td.col-regional,
    body table.tabela-funil-movel-cotacoes-valor-mensal td.col-canal,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes td.col-etapa {
        box-shadow: none !important;
        color: #2F3747 !important;
        font-weight: 700 !important;
    }

    body table.tabela-analitico-migracoes-pme .mom-chip-migracoes,
    body #tabela-funil-conta-cotacoes-ativacao .mom-chip-funil {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        min-width: auto !important;
        padding: 0 !important;
        border-radius: 0 !important;
    }

    body table.tabela-analitico-migracoes-pme td.col-valor::before,
    body table.tabela-funil-movel-cotacoes-valor-mensal td.col-valor::before {
        display: none !important;
    }

    body table.tabela-analitico-migracoes-pme td.col-tend,
    body table.tabela-funil-movel-cotacoes-valor-mensal td.col-total-mes,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes td.col-tend {
        background: #F5F5F5 !important;
        color: #2F3747 !important;
        font-weight: 700 !important;
    }

    body table.tabela-analitico-migracoes-pme tr.linha-total,
    body table.tabela-funil-movel-cotacoes-valor-mensal tr.linha-total,
    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes tr.linha-conversao-funil {
        border-top: 1px solid #D9D9D9 !important;
    }

    body table.tabela-analitico-migracoes-pme tr.linha-total td,
    body table.tabela-funil-movel-cotacoes-valor-mensal tr.linha-total td {
        background: linear-gradient(180deg, #790E09 0%, #4E0805 100%) !important;
        color: #FFFFFF !important;
        text-shadow: none !important;
        border-top: 1px solid rgba(255,255,255,0.18) !important;
        border-bottom: 1px solid rgba(61,7,4,0.88) !important;
        font-weight: 800 !important;
    }

    body #tabela-funil-conta-cotacoes-ativacao .tabela-funil-cotacoes tbody tr.linha-conversao-funil td {
        background: #FAFAFA !important;
        color: #2F3747 !important;
        font-weight: 700 !important;
        border-top: 1px solid #E6E6E6 !important;
    }

    body .tabela-funil-fixa-backlog-fixa-pme-container,
    body .tabela-funil-movel-cotacoes-valor-mensal-container,
    body #tabela-funil-conta-cotacoes-ativacao.tabela-container-funil-cotacoes {
        border: 1px solid rgba(121, 14, 9, 0.16) !important;
        border-radius: 3px !important;
        box-shadow: none !important;
        background: #FFFFFF !important;
        margin: 8px 0 14px 0 !important;
    }

    body table.tabela-funil-fixa-backlog-fixa-pme,
    body table.tabela-funil-movel-cotacoes-valor-mensal,
    body #tabela-funil-conta-cotacoes-ativacao table.tabela-funil-cotacoes {
        border-collapse: collapse !important;
        border-spacing: 0 !important;
        background: #FFFFFF !important;
        box-shadow: none !important;
        font-family: 'Manrope', 'Segoe UI', sans-serif !important;
    }

    body table.tabela-funil-fixa-backlog-fixa-pme th,
    body table.tabela-funil-movel-cotacoes-valor-mensal th,
    body #tabela-funil-conta-cotacoes-ativacao table.tabela-funil-cotacoes th {
        background: #790E09 !important;
        color: #FFFFFF !important;
        box-shadow: none !important;
        text-shadow: none !important;
        border-right: 1px solid rgba(255,255,255,0.18) !important;
        border-bottom: 1px solid rgba(61,7,4,0.82) !important;
        font-weight: 800 !important;
        letter-spacing: 0.18px !important;
        padding: 6px 5px !important;
        white-space: normal !important;
    }

    body table.tabela-funil-fixa-backlog-fixa-pme th.col-total-mes,
    body table.tabela-funil-fixa-backlog-fixa-pme th.col-mes-atual,
    body table.tabela-funil-movel-cotacoes-valor-mensal th.col-total-mes,
    body table.tabela-funil-movel-cotacoes-valor-mensal th.col-mes-atual,
    body #tabela-funil-conta-cotacoes-ativacao table.tabela-funil-cotacoes th.col-var,
    body #tabela-funil-conta-cotacoes-ativacao table.tabela-funil-cotacoes th.col-tend {
        background: #8E241D !important;
    }

    body table.tabela-funil-fixa-backlog-fixa-pme td,
    body table.tabela-funil-movel-cotacoes-valor-mensal td,
    body #tabela-funil-conta-cotacoes-ativacao table.tabela-funil-cotacoes td {
        background: #FFFFFF !important;
        color: #2F3747 !important;
        box-shadow: none !important;
        text-shadow: none !important;
        border-bottom: 1px solid #E8E8E8 !important;
        border-right: 1px solid #F0F0F0 !important;
        padding: 5px 5px !important;
        font-weight: 600 !important;
    }

    body table.tabela-funil-fixa-backlog-fixa-pme tbody tr:nth-child(even) td,
    body table.tabela-funil-movel-cotacoes-valor-mensal tbody tr:nth-child(even) td,
    body #tabela-funil-conta-cotacoes-ativacao table.tabela-funil-cotacoes tbody tr:nth-child(even) td {
        background: #FAFAFA !important;
    }

    body table.tabela-funil-fixa-backlog-fixa-pme tbody tr:hover td,
    body table.tabela-funil-movel-cotacoes-valor-mensal tbody tr:hover td,
    body #tabela-funil-conta-cotacoes-ativacao table.tabela-funil-cotacoes tbody tr:hover td {
        background: #FFF8F7 !important;
        transform: none !important;
    }

    body table.tabela-funil-fixa-backlog-fixa-pme td.col-valor::before,
    body table.tabela-funil-movel-cotacoes-valor-mensal td.col-valor::before,
    body #tabela-funil-conta-cotacoes-ativacao table.tabela-funil-cotacoes td::before {
        content: none !important;
        display: none !important;
    }

    body table.tabela-funil-fixa-backlog-fixa-pme td.col-canal,
    body table.tabela-funil-movel-cotacoes-valor-mensal td.col-canal,
    body #tabela-funil-conta-cotacoes-ativacao table.tabela-funil-cotacoes td.col-etapa {
        color: #2F3747 !important;
        font-weight: 800 !important;
        box-shadow: none !important;
        text-align: left !important;
    }

    body table.tabela-funil-fixa-backlog-fixa-pme tr.linha-total td,
    body table.tabela-funil-movel-cotacoes-valor-mensal tr.linha-total td {
        background: #5A0A06 !important;
        color: #FFFFFF !important;
        font-weight: 800 !important;
        border-color: rgba(255,255,255,0.18) !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

