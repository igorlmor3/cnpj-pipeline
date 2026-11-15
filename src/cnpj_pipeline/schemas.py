"""Nomes das colunas das tabelas do CNPJ, na ordem em que aparecem nos CSVs.

Os arquivos da Receita nao tem linha de cabecalho; a ordem e os nomes vem do
dicionario de layout publicado junto com os dados de cada mes.

Os nomes abaixo seguem o layout historico e foram validados contra os dados reais
de 2026-10 (nenhuma linha rejeitada). A Receita alterou a estrutura do arquivo de
socios em 2026. A camada bronze compara a quantidade de colunas do arquivo com a
quantidade definida aqui e interrompe o processamento se forem diferentes, entao um
layout novo nunca passa despercebido.

Na camada bronze todas as colunas sao lidas como texto. Os tipos corretos
(datas, decimais, inteiros) sao aplicados na camada silver.
"""

from __future__ import annotations

_DOMAIN = ("codigo", "descricao")

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "Empresas": (
        "cnpj_basico",
        "razao_social",
        "natureza_juridica",
        "qualificacao_responsavel",
        "capital_social",
        "porte_empresa",
        "ente_federativo_responsavel",
    ),
    "Estabelecimentos": (
        "cnpj_basico",
        "cnpj_ordem",
        "cnpj_dv",
        "identificador_matriz_filial",
        "nome_fantasia",
        "situacao_cadastral",
        "data_situacao_cadastral",
        "motivo_situacao_cadastral",
        "nome_cidade_exterior",
        "pais",
        "data_inicio_atividade",
        "cnae_fiscal_principal",
        "cnae_fiscal_secundaria",
        "tipo_logradouro",
        "logradouro",
        "numero",
        "complemento",
        "bairro",
        "cep",
        "uf",
        "municipio",
        "ddd_1",
        "telefone_1",
        "ddd_2",
        "telefone_2",
        "ddd_fax",
        "fax",
        "correio_eletronico",
        "situacao_especial",
        "data_situacao_especial",
    ),
    "Socios": (
        "cnpj_basico",
        "identificador_socio",
        "nome_socio",
        "cnpj_cpf_socio",
        "qualificacao_socio",
        "data_entrada_sociedade",
        "pais",
        "representante_legal",
        "nome_representante",
        "qualificacao_representante_legal",
        "faixa_etaria",
    ),
    "Simples": (
        "cnpj_basico",
        "opcao_pelo_simples",
        "data_opcao_simples",
        "data_exclusao_simples",
        "opcao_pelo_mei",
        "data_opcao_mei",
        "data_exclusao_mei",
    ),
    "Cnaes": _DOMAIN,
    "Motivos": _DOMAIN,
    "Municipios": _DOMAIN,
    "Naturezas": _DOMAIN,
    "Paises": _DOMAIN,
    "Qualificacoes": _DOMAIN,
}


def columns_for(table: str) -> tuple[str, ...]:
    try:
        return TABLE_COLUMNS[table]
    except KeyError:
        raise KeyError(f"Sem esquema definido para a tabela {table!r}") from None
