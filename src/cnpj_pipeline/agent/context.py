"""Contexto semantico: o texto que explica ao modelo as tabelas, colunas e ressalvas da gold.

O texto e montado a partir das colunas reais (manifesto da gold) e das constantes de gold_models.py,
nao copiado a mao, para nao divergir da gold. Uma coluna sem glossa aqui faz um teste falhar.
Todo o conteudo e fixo e vem do codigo: nada que venha do cadastro entra no texto.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cnpj_pipeline.agent.sql_guard import MAX_JOINS, MAX_ROWS
from cnpj_pipeline.gold_models import (
    CNAE_ORG_POLITICA,
    CNAE_SECOES,
    NATUREZA_GRUPOS,
    PORTES,
    TIPOS_SOCIO,
)

TABLE_PREFIX = "gold_"
META_PREFIX = "_"
PARTITION_COLUMN = "reference_month"


@dataclass(frozen=True)
class TableDoc:
    description: str
    grain: str
    columns: dict[str, str]


_FLAG_POLITICA = (
    f"Verdadeiro para o CNAE {CNAE_ORG_POLITICA} (atividades de organizações políticas), cujos registros "
    "crescem nos anos de eleição municipal e não representam atividade econômica."
)

TABLES: dict[str, TableDoc] = {
    "mart_aberturas_mensal": TableDoc(
        "Estabelecimentos (matrizes e filiais) por mês de início de atividade, UF e seção da CNAE.",
        "uma linha por ano_mes, uf, cnae_secao e is_organizacao_politica",
        {
            "ano_mes": "Mês de início da atividade, texto no formato AAAA-MM.",
            "ano": "Ano de início da atividade.",
            "uf": "Sigla da UF do estabelecimento (EX indica exterior).",
            "cnae_secao": "Letra da seção da CNAE (A a U); nula se a divisão não pertence a nenhuma seção.",
            "is_organizacao_politica": _FLAG_POLITICA,
            "estabelecimentos_abertos": "Estabelecimentos que iniciaram atividade no mês.",
            "matrizes_abertas": "Quantos deles são matrizes.",
            "ainda_ativos": "Quantos deles estão ativos hoje.",
        },
    ),
    "mart_estabelecimentos_ativos": TableDoc(
        "Estabelecimentos ativos por UF, município e divisão da CNAE.",
        "uma linha por uf, município, cnae_divisao e is_organizacao_politica",
        {
            "uf": "Sigla da UF.",
            "municipio": "Código do município no cadastro da Receita (não é o código do IBGE).",
            "municipio_nome": "Nome do município.",
            "cnae_divisao": "Divisão da CNAE (2 primeiros dígitos do CNAE principal).",
            "cnae_secao": "Letra da seção da CNAE.",
            "cnae_secao_descricao": "Descrição da seção da CNAE.",
            "is_organizacao_politica": _FLAG_POLITICA,
            "estabelecimentos_ativos": "Estabelecimentos ativos (matrizes e filiais).",
            "matrizes_ativas": "Quantos são matrizes.",
            "optantes_simples": "Quantos pertencem a empresas optantes pelo Simples Nacional.",
            "optantes_mei": "Quantos pertencem a empresas optantes pelo MEI.",
        },
    ),
    "mart_perfil_porte_natureza": TableDoc(
        "Empresas ativas (só a matriz conta) por porte e natureza jurídica, com o capital social.",
        "uma linha por porte_empresa, natureza_juridica e is_organizacao_politica",
        {
            "porte_empresa": "Código do porte (ver 'Códigos').",
            "porte_descricao": "Descrição do porte.",
            "natureza_juridica": "Código da natureza jurídica.",
            "natureza_descricao": "Descrição da natureza jurídica.",
            "natureza_grupo": (
                "Descrição do grupo da natureza jurídica (por exemplo, Entidades Empresariais); "
                "o dígito do grupo é o primeiro de natureza_juridica."
            ),
            "is_organizacao_politica": _FLAG_POLITICA,
            "empresas_ativas": "Empresas ativas.",
            "capital_social_total": "Soma do capital social, em reais.",
            "capital_social_mediano": "Mediana do capital social, em reais.",
        },
    ),
    "mart_simples_mei_uf": TableDoc(
        "Empresas ativas (uma por empresa) e participação de Simples Nacional e MEI por UF.",
        "uma linha por uf e is_organizacao_politica",
        {
            "uf": "Sigla da UF.",
            "is_organizacao_politica": _FLAG_POLITICA,
            "empresas_ativas": "Empresas ativas.",
            "optantes_simples": "Empresas ativas optantes pelo Simples Nacional.",
            "optantes_mei": "Empresas ativas optantes pelo MEI (indicador separado do Simples).",
            "pct_simples": "Percentual (0 a 100) de optantes pelo Simples entre as empresas ativas.",
            "pct_mei": "Percentual (0 a 100) de optantes pelo MEI entre as empresas ativas.",
        },
    ),
    "mart_socios_qualificacao": TableDoc(
        "Quantidade de sócios por qualificação e tipo (pessoa física, jurídica ou estrangeiro).",
        "uma linha por qualificacao_socio e identificador_socio",
        {
            "qualificacao_socio": "Código da qualificação do sócio.",
            "qualificacao_descricao": "Descrição da qualificação.",
            "identificador_socio": "Código do tipo de sócio (ver 'Códigos').",
            "tipo_socio": "Descrição do tipo de sócio.",
            "socios": "Quantidade de vínculos de sócio.",
        },
    ),
    "dim_cnae": TableDoc(
        "Subclasses da CNAE.",
        "uma linha por codigo",
        {
            "codigo": "Código da subclasse (7 dígitos).",
            "descricao": "Descrição.",
            "divisao": "Divisão (2 primeiros dígitos).",
            "secao": "Letra da seção.",
        },
    ),
    "dim_cnae_secao": TableDoc(
        "As 21 seções da CNAE (A a U).",
        "uma linha por codigo",
        {
            "codigo": "Letra da seção.",
            "descricao": "Descrição da seção.",
            "divisao_inicial": "Primeira divisão da seção.",
            "divisao_final": "Última divisão da seção.",
        },
    ),
    "dim_municipio": TableDoc(
        "Municípios, com o código usado no cadastro da Receita.",
        "uma linha por codigo",
        {"codigo": "Código do município.", "descricao": "Nome do município."},
    ),
    "dim_natureza_juridica": TableDoc(
        "Naturezas jurídicas.",
        "uma linha por codigo",
        {
            "codigo": "Código da natureza jurídica.",
            "descricao": "Descrição.",
            "grupo": "Primeiro dígito do código.",
            "grupo_descricao": "Descrição do grupo.",
        },
    ),
    "dim_porte": TableDoc(
        "Portes de empresa.",
        "uma linha por codigo",
        {"codigo": "Código do porte.", "descricao": "Descrição."},
    ),
    "dim_qualificacao_socio": TableDoc(
        "Qualificações de sócio.",
        "uma linha por codigo",
        {"codigo": "Código da qualificação.", "descricao": "Descrição."},
    ),
    "dim_situacao_cadastral": TableDoc(
        "Situações cadastrais.",
        "uma linha por codigo",
        {"codigo": "Código da situação (02 = Ativa).", "descricao": "Descrição."},
    ),
}


def documented_tables() -> frozenset[str]:
    return frozenset(TABLE_PREFIX + name for name in TABLES)


def visible_columns(columns: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """Colunas que o modelo pode usar: sem as de metadados e sem a particao, que a guarda fixa."""
    return [(n, t) for n, t in columns if not n.startswith(META_PREFIX) and n != PARTITION_COLUMN]


def _codes(pairs: Sequence[tuple[str, str]]) -> str:
    return "; ".join(f"{code} = {label}" for code, label in pairs)


def _table_section(name: str, doc: TableDoc, columns: Sequence[tuple[str, str]]) -> str:
    lines = [f"Tabela {TABLE_PREFIX}{name}: {doc.description}", f"  Granularidade: {doc.grain}."]
    for column, column_type in visible_columns(columns):
        gloss = doc.columns.get(column)
        if gloss is None:
            raise KeyError(f"coluna sem glossa: {TABLE_PREFIX}{name}.{column}")
        lines.append(f"  - {column} ({column_type}): {gloss}")
    return "\n".join(lines)


def build_context(columns: Mapping[str, Sequence[tuple[str, str]]], *, month: str) -> str:
    """Texto de contexto para o modelo. `columns` mapeia o modelo (sem o prefixo gold_) as suas colunas."""
    unknown = set(columns) - set(TABLES)
    if unknown:
        raise KeyError(f"tabelas sem documentacao: {sorted(unknown)}")
    sections = [_table_section(name, TABLES[name], columns[name]) for name in TABLES if name in columns]
    secoes = "; ".join(f"{letter} = {desc}" for letter, desc, _, _ in CNAE_SECOES)
    return f"""Você responde perguntas sobre o cadastro de CNPJ da Receita Federal, no mês {month}, \
consultando tabelas agregadas por meio da ferramenta run_sql.

Regras do SQL
- O dialeto é o do Athena (Trino). Escreva uma única instrução SELECT.
- Use somente as tabelas listadas abaixo. Não existe tabela com linhas individuais de empresas.
- Não filtre por reference_month: o mês {month} já está fixado para todas as tabelas.
- Toda junção precisa de condição ON (no máximo {MAX_JOINS} junções). O resultado é cortado em \
{MAX_ROWS} linhas; agregue e ordene em vez de listar.
- Devolva só o que foi perguntado. Para "qual" ou "o maior", ordene e use LIMIT 1 (ou o número \
pedido, como "os três maiores" com LIMIT 3). Para "o menor", ordene do menor para o maior com LIMIT 1; \
para "o segundo maior", use LIMIT 1 OFFSET 1. Não traga linhas além das pedidas. Para um total, \
devolva uma linha com o total. Filtre com WHERE o que a pergunta restringe (UF, ano, tipo, porte) em \
vez de listar todos os grupos.
- Faça as contas no SQL. Diferença, média, proporção e porcentagem entre valores saem como uma coluna \
do resultado, calculada na consulta (use 100.0 e não 100 para a divisão não truncar em inteiro). Não \
deixe a aritmética para a resposta.
- Para excluir as organizações políticas, filtre com WHERE is_organizacao_politica = false. Não agrupe \
por essa coluna para depois escolher um dos grupos.
- Se o SQL for recusado ou der erro, leia a mensagem, corrija e tente de novo.

Tabelas
{chr(10).join(sections)}

Convenções do cadastro
- Estabelecimento é uma matriz ou uma filial. Empresa ativa, nos marts de perfil e de Simples e MEI, \
é a matriz com situação cadastral ativa, o que conta cada empresa uma vez.
- Porte: {_codes(PORTES)}. Tipo de sócio: {_codes(TIPOS_SOCIO)}.
- Grupos da natureza jurídica: {_codes(NATUREZA_GRUPOS)}.
- Seções da CNAE: {secoes}.
- Os marts de abertura mostram o que existe no cadastro neste mês; estabelecimentos já removidos não \
aparecem, então a série histórica pode subestimar aberturas antigas.

Ressalva importante: CNAE {CNAE_ORG_POLITICA}
Nos anos de eleição municipal, o CNAE {CNAE_ORG_POLITICA} (organizações políticas) concentra centenas de \
milhares de registros que distorcem as aberturas por ano, mês ou seção. Em perguntas sobre aberturas, \
use a coluna is_organizacao_politica: exclua-os (is_organizacao_politica = false) ou mostre os dois \
grupos separados, e diga qual dos dois você usou. Em porte, natureza e Simples/MEI, exclua-os por \
padrão e diga que o fez.

Como responder
- Responda em português, com os números do resultado, e cite o SQL usado.
- Não invente números que não estejam no resultado. Se a pergunta não puder ser respondida com estas \
tabelas (por exemplo, sobre uma empresa específica, contatos, endereços ou nomes), diga isso.
"""
