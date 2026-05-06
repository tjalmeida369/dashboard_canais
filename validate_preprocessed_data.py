from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal

from preprocess_all import (
    OUT_DIR_DEFAULT,
    localizar_diretorio_bruto,
    prepare_analitica,
    prepare_ativados_base,
    prepare_backlog,
    prepare_base_performance,
    prepare_base_principal,
    prepare_base_principal_mensal,
    prepare_cotacoes,
    prepare_desativados,
    prepare_funil_fixa,
    prepare_ligacoes,
    prepare_migracoes,
    prepare_pedidos_ecommerce,
    prepare_tend_funil_fixa,
)


ARQUIVOS_ESPERADOS = [
    "base_principal.parquet",
    "base_principal_mensal.parquet",
    "pedidos_ecommerce.parquet",
    "ativados_base.parquet",
    "evolucao_mensal_agregado.parquet",
    "base_performance_mensal.parquet",
    "analitica_diaria.parquet",
    "ligacoes_receptivo.parquet",
    "ligacoes_mensal_agregado.parquet",
    "ligacoes_performance_mensal.parquet",
    "cotacoes_agregado.parquet",
    "backlog_consolidado_limpo.parquet",
    "migracoes_pme.parquet",
    "desativados_base.parquet",
    "funil_fixa_ecommerce.parquet",
    "tend_funil_fixa.parquet",
]


def _canonicalizar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()

    normalizado = pd.DataFrame(index=df.index)
    for coluna in df.columns:
        serie = df[coluna]
        if pd.api.types.is_datetime64_any_dtype(serie):
            normalizado[coluna] = (
                pd.to_datetime(serie, errors="coerce")
                .dt.strftime("%Y-%m-%d %H:%M:%S")
                .fillna("<NA>")
            )
        elif pd.api.types.is_float_dtype(serie):
            normalizado[coluna] = pd.to_numeric(serie, errors="coerce").round(10)
        elif pd.api.types.is_integer_dtype(serie):
            normalizado[coluna] = pd.to_numeric(serie, errors="coerce").astype("Int64")
        elif pd.api.types.is_bool_dtype(serie):
            normalizado[coluna] = serie.astype("boolean").astype("string").fillna("<NA>")
        else:
            normalizado[coluna] = serie.astype("string").fillna("<NA>")

    if list(normalizado.columns):
        normalizado = normalizado.sort_values(
            by=list(normalizado.columns),
            kind="mergesort",
            na_position="first",
        ).reset_index(drop=True)
    return normalizado


def _comparar_parquet(nome_arquivo: str, referencia_dir: Path, saida_dir: Path) -> tuple[bool, str]:
    ref_path = referencia_dir / nome_arquivo
    out_path = saida_dir / nome_arquivo

    if not ref_path.exists():
        return False, f"[fail] {nome_arquivo}: referencia nao gerada."
    if not out_path.exists():
        return False, f"[fail] {nome_arquivo}: arquivo de saida nao encontrado."

    df_ref = pd.read_parquet(ref_path)
    df_out = pd.read_parquet(out_path)

    if list(df_ref.columns) != list(df_out.columns):
        return (
            False,
            f"[fail] {nome_arquivo}: colunas divergentes. ref={list(df_ref.columns)} out={list(df_out.columns)}",
        )

    try:
        assert_frame_equal(
            _canonicalizar_dataframe(df_ref),
            _canonicalizar_dataframe(df_out),
            check_dtype=False,
            check_like=False,
        )
    except AssertionError as exc:
        return False, f"[fail] {nome_arquivo}: {exc}"

    return True, f"[ok] {nome_arquivo}: {len(df_out):,} linhas validadas"


def _gerar_referencia(raw_dir: Path, referencia_dir: Path) -> None:
    df_base = prepare_base_principal(raw_dir, referencia_dir)
    df_base_mensal = prepare_base_principal_mensal(df_base, referencia_dir)
    prepare_pedidos_ecommerce(df_base, referencia_dir)
    prepare_ativados_base(df_base_mensal, referencia_dir)
    prepare_base_performance(df_base, referencia_dir)
    prepare_analitica(df_base, referencia_dir)
    prepare_ligacoes(raw_dir, referencia_dir)
    prepare_cotacoes(raw_dir, referencia_dir)
    prepare_backlog(raw_dir, referencia_dir)
    prepare_migracoes(raw_dir, referencia_dir)
    prepare_desativados(raw_dir, referencia_dir)
    prepare_funil_fixa(raw_dir, referencia_dir)
    prepare_tend_funil_fixa(raw_dir, referencia_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida os Parquets preprocessados contra uma referencia regenerada dos arquivos brutos.")
    parser.add_argument("--raw-dir", default=None, help="Diretorio dos arquivos brutos.")
    parser.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT), help="Diretorio com os Parquets preprocessados.")
    args = parser.parse_args()

    raw_dir = localizar_diretorio_bruto(args.raw_dir)
    out_dir = Path(args.out_dir).expanduser().resolve()

    print(f"[info] RAW_DIR = {raw_dir}")
    print(f"[info] OUT_DIR = {out_dir}")

    falhas = 0
    with tempfile.TemporaryDirectory(prefix="validacao_preprocess_") as tmp_dir:
        referencia_dir = Path(tmp_dir)
        print(f"[info] REF_DIR = {referencia_dir}")
        _gerar_referencia(raw_dir, referencia_dir)

        for nome_arquivo in ARQUIVOS_ESPERADOS:
            ok, mensagem = _comparar_parquet(nome_arquivo, referencia_dir, out_dir)
            print(mensagem)
            if not ok:
                falhas += 1

    if falhas:
        print(f"[done] Validacao concluida com {falhas} falha(s).")
        return 1

    print("[done] Validacao concluida sem divergencias.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
