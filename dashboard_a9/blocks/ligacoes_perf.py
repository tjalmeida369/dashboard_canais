from __future__ import annotations


def _substituir_entre(code: str, inicio: str, fim: str, novo: str) -> str:
    pos_inicio = code.find(inicio)
    if pos_inicio < 0:
        return code
    pos_fim = code.find(fim, pos_inicio)
    if pos_fim < 0:
        return code
    return code[:pos_inicio] + novo + code[pos_fim:]


def otimizar_calculos_ligacoes(block_code: str) -> str:
    """Troca varreduras repetidas por indices locais sem alterar a saida visual."""
    bloco_meta = '''        _cache_meta_correta_ligacoes = {}

        def calcular_meta_correta(df_metas, mes_ano, regional=None, plataforma=None):
            chave_cache = (
                id(df_metas), str(mes_ano).strip(),
                str(regional or "Todas").strip(), str(plataforma or "Todas").strip().upper()
            )
            if chave_cache in _cache_meta_correta_ligacoes:
                return _cache_meta_correta_ligacoes[chave_cache]
            try:
                if df_metas is None or df_metas.empty:
                    resultado = 0.0
                else:
                    mask = df_metas['mes_ano'].eq(mes_ano)
                    if regional and regional != "Todas":
                        mask &= df_metas['REGIONAL'].eq(regional)
                    if plataforma:
                        mask &= df_metas['COD_PLATAFORMA'].eq(str(plataforma).strip().upper())
                    else:
                        mask &= df_metas['COD_PLATAFORMA'].isin(['FIXA', 'CONTA'])
                    resultado = float(pd.to_numeric(
                        df_metas.loc[mask, 'DESAFIO_QTD'], errors='coerce'
                    ).fillna(0).sum())
            except Exception:
                resultado = 0.0
            _cache_meta_correta_ligacoes[chave_cache] = resultado
            return resultado

'''
    block_code = _substituir_entre(
        block_code,
        "        def calcular_meta_correta(",
        "        with st.spinner(",
        bloco_meta,
    )

    bloco_tendencia = '''        _cache_tendencia_ligacoes = {}

        def calcular_tendencia_ligacoes(df_metas, mes, regional_filtro=None, plataforma=None):
            chave_cache = (
                id(df_metas), str(mes).strip(),
                str(regional_filtro or "Todas").strip(), str(plataforma or "Todas").strip().upper()
            )
            if chave_cache in _cache_tendencia_ligacoes:
                return _cache_tendencia_ligacoes[chave_cache]
            if df_metas is None or df_metas.empty or 'TEND_QTD' not in df_metas.columns:
                resultado = 0.0
            else:
                mask = df_metas['mes_ano'].eq(mes)
                if regional_filtro and regional_filtro != "Todas":
                    mask &= df_metas['REGIONAL'].eq(regional_filtro)
                if plataforma:
                    mask &= df_metas['COD_PLATAFORMA'].eq(str(plataforma).strip().upper())
                else:
                    mask &= df_metas['COD_PLATAFORMA'].isin(['FIXA', 'CONTA'])
                resultado = float(pd.to_numeric(
                    df_metas.loc[mask, 'TEND_QTD'], errors='coerce'
                ).fillna(0).sum())
            _cache_tendencia_ligacoes[chave_cache] = resultado
            return resultado

'''
    block_code = _substituir_entre(
        block_code,
        "        def calcular_tendencia_ligacoes(",
        "        def calcular_meta_fixa(",
        bloco_tendencia,
    )

    bloco_fator = '''        _cache_fator_total_ligacoes = {}
        _cache_series_fator_ligacoes = {}

        def obter_fator_total_ligacoes_por_conta(df_reais, mes_ref, regional_filtro=None):
            regional_norm = str(regional_filtro or "Todas").strip()
            chave_cache = (id(df_reais), str(mes_ref).strip(), regional_norm)
            if chave_cache in _cache_fator_total_ligacoes:
                return _cache_fator_total_ligacoes[chave_cache]
            chave_series = (id(df_reais), regional_norm)
            series_ref = _cache_series_fator_ligacoes.get(chave_series)
            if series_ref is None:
                if df_reais is None or df_reais.empty:
                    series_ref = ({}, {}, [])
                else:
                    df_base = df_reais
                    if regional_norm != "Todas":
                        df_base = df_base[df_base['REGIONAL'].eq(regional_norm)]
                    if df_base.empty:
                        series_ref = ({}, {}, [])
                    else:
                        valores_ref = pd.to_numeric(df_base['QTDE'], errors='coerce').fillna(0)
                        total_mes = valores_ref.groupby(df_base['mes_ano'], observed=True).sum().astype(float).to_dict()
                        mask_conta = df_base['TIPO_CHAMADA'].eq('DEMAIS')
                        conta_mes = valores_ref.loc[mask_conta].groupby(
                            df_base.loc[mask_conta, 'mes_ano'], observed=True
                        ).sum().astype(float).to_dict()
                        meses_ref = sorted(
                            {str(m).strip() for m in total_mes if str(m).strip()}, key=ordenar_meses
                        )
                        series_ref = (total_mes, conta_mes, meses_ref)
                _cache_series_fator_ligacoes[chave_series] = series_ref
            total_mes, conta_mes, meses_validos = series_ref
            if not meses_validos:
                resultado = 1.0
            else:
                mes_norm = str(mes_ref).strip()
                if mes_norm in meses_validos:
                    idx_ref = meses_validos.index(mes_norm)
                    meses_busca = list(reversed(meses_validos[:idx_ref + 1]))
                    meses_busca.extend(reversed(meses_validos[idx_ref + 1:]))
                else:
                    meses_busca = list(reversed(meses_validos))
                resultado = 1.0
                for mes_busca in dict.fromkeys(meses_busca):
                    total_real = float(total_mes.get(mes_busca, 0.0) or 0.0)
                    conta_real = float(conta_mes.get(mes_busca, 0.0) or 0.0)
                    if total_real > 0 and conta_real > 0:
                        resultado = total_real / conta_real
                        break
            _cache_fator_total_ligacoes[chave_cache] = resultado
            return resultado

'''
    block_code = _substituir_entre(
        block_code,
        "        def obter_fator_total_ligacoes_por_conta(",
        "        def calcular_tendencia_total_projetada_ligacoes(",
        bloco_fator,
    )

    bloco_share = '''        _cache_agregado_rateio_telefone = {}

        def _obter_agregado_rateio_telefone(df_base, regional="Todas", plataforma="Todas", tipo_chamada="Todos"):
            chave_cache = (
                id(df_base), str(regional or "Todas").strip(),
                str(plataforma or "Todas").strip().upper(), str(tipo_chamada or "Todos").strip()
            )
            if chave_cache in _cache_agregado_rateio_telefone:
                return _cache_agregado_rateio_telefone[chave_cache]
            df_ref = aplicar_filtros_ligacoes_recorte(
                df_base, regional=regional, plataforma=plataforma,
                tipo_chamada=tipo_chamada, telefone="Todos"
            )
            if df_ref.empty or 'TELEFONE' not in df_ref.columns:
                resultado = ([], {}, {})
            else:
                df_group = df_ref[['mes_ano', 'TELEFONE', 'QTDE']].copy()
                df_group['mes_ano'] = df_group['mes_ano'].astype(str).str.strip()
                df_group['TELEFONE'] = df_group['TELEFONE'].map(normalizar_telefone_ligacoes)
                df_group['QTDE'] = pd.to_numeric(df_group['QTDE'], errors='coerce').fillna(0.0)
                # O denominador original inclui tambem linhas sem telefone informado.
                df_group = df_group[df_group['mes_ano'].ne('')]
                por_telefone = df_group.groupby(
                    ['mes_ano', 'TELEFONE'], observed=True
                )['QTDE'].sum().astype(float).to_dict()
                totais = df_group.groupby('mes_ano', observed=True)['QTDE'].sum().astype(float).to_dict()
                meses = sorted(totais.keys(), key=ordenar_meses)
                resultado = (meses, totais, por_telefone)
            _cache_agregado_rateio_telefone[chave_cache] = resultado
            return resultado

        def obter_meses_ligacoes_ordenados(df_base):
            if df_base is None or df_base.empty or 'mes_ano' not in df_base.columns:
                return []
            return sorted(
                [str(m).strip() for m in df_base['mes_ano'].dropna().unique().tolist() if str(m).strip()],
                key=ordenar_meses
            )

        def obter_mes_base_rateio_telefone_ligacoes(df_base, mes_ref, regional="Todas", plataforma="Todas", tipo_chamada="Todos"):
            meses_validos, _, _ = _obter_agregado_rateio_telefone(
                df_base, regional=regional, plataforma=plataforma, tipo_chamada=tipo_chamada
            )
            if not meses_validos:
                return mes_ref
            mes_ref_norm = str(mes_ref).strip()
            mes_alvo = get_mes_anterior(mes_ref_norm) if mes_ref_norm.lower() == get_mes_atual_formatado().strip().lower() else mes_ref_norm
            chave_alvo = ordenar_meses(mes_alvo)
            candidatos = [m for m in meses_validos if ordenar_meses(m) <= chave_alvo]
            if not candidatos and mes_ref_norm in meses_validos:
                candidatos = [mes_ref_norm]
            return (candidatos or meses_validos)[-1]

        def calcular_share_telefone_ligacoes(
            df_base, telefone, mes_ref, regional="Todas", plataforma="Todas", tipo_chamada="Todos"
        ):
            telefone_norm = normalizar_telefone_ligacoes(telefone)
            if not telefone_norm or telefone_norm in {"Todos", "Todas"}:
                return 1.0
            meses_validos, totais_mes, por_telefone = _obter_agregado_rateio_telefone(
                df_base, regional=regional, plataforma=plataforma, tipo_chamada=tipo_chamada
            )
            if not meses_validos:
                return 0.0
            mes_base = obter_mes_base_rateio_telefone_ligacoes(
                df_base, mes_ref, regional=regional, plataforma=plataforma, tipo_chamada=tipo_chamada
            )
            total_mes = float(totais_mes.get(mes_base, 0.0) or 0.0)
            if total_mes <= 0:
                return 0.0
            total_tel = float(por_telefone.get((mes_base, telefone_norm), 0.0) or 0.0)
            return max(total_tel / total_mes, 0.0)

'''
    return _substituir_entre(
        block_code,
        "        def obter_meses_ligacoes_ordenados(",
        "        def ratear_valor_telefone_ligacoes(",
        bloco_share,
    )
