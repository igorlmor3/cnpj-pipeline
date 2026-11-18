"""Testes do contexto semantico: cobertura das colunas reais e conteudo das regras."""

from __future__ import annotations

import pytest
from helpers import MONTH
from test_gold import build_silver, run_gold

from cnpj_pipeline.agent.context import (
    META_PREFIX,
    PARTITION_COLUMN,
    TABLE_PREFIX,
    TABLES,
    build_context,
    documented_tables,
    visible_columns,
)
from cnpj_pipeline.agent.sql_guard import ALLOWED_TABLES, MAX_ROWS
from cnpj_pipeline.cloud.athena import gold_columns
from cnpj_pipeline.gold_models import CNAE_ORG_POLITICA, CNAE_SECOES


@pytest.fixture(scope="module")
def real_columns(tmp_path_factory):
    base = tmp_path_factory.mktemp("ctx")
    build_silver(base)
    assert run_gold(base) == 0
    return gold_columns(base / "gold", MONTH, list(TABLES))


def test_documented_tables_are_exactly_the_allowed_tables():
    assert documented_tables() == ALLOWED_TABLES


def test_every_real_column_has_a_gloss_and_no_gloss_is_orphaned(real_columns):
    for name, doc in TABLES.items():
        shown = {c for c, _ in visible_columns(real_columns[name])}
        assert shown == set(doc.columns), name


def test_meta_and_partition_columns_are_hidden(real_columns):
    for columns in real_columns.values():
        hidden = {c for c, _ in columns} - {c for c, _ in visible_columns(columns)}
        assert "_gold_processed_at" in hidden  # o manifesto nao traz a particao, so os metadados
        assert all(c == PARTITION_COLUMN or c.startswith(META_PREFIX) for c in hidden)
    # a particao, se aparecesse na lista de colunas, tambem seria escondida
    assert visible_columns([("a", "string"), (PARTITION_COLUMN, "string")]) == [("a", "string")]


def test_context_names_every_table_and_column(real_columns):
    text = build_context(real_columns, month=MONTH)
    for name, doc in TABLES.items():
        assert f"{TABLE_PREFIX}{name}" in text
        for column in doc.columns:
            assert column in text
    assert "_gold_processed_at" not in text
    assert "_reference_month" not in text


def test_context_states_the_rules_and_the_month(real_columns):
    text = build_context(real_columns, month=MONTH)
    assert MONTH in text
    assert str(MAX_ROWS) in text
    assert "run_sql" in text
    assert "SELECT" in text


def test_context_explains_the_political_cnae_caveat(real_columns):
    text = build_context(real_columns, month=MONTH)
    assert CNAE_ORG_POLITICA in text
    assert "eleição" in text
    # a instrucao em si, nao so a presenca do codigo: excluir ou separar, e dizer o que foi feito
    assert "use a coluna is_organizacao_politica" in text
    assert "is_organizacao_politica = false" in text
    assert "mostre os dois grupos separados" in text
    assert "diga qual dos dois você usou" in text


def test_context_asks_for_exactly_what_was_asked(real_columns):
    text = build_context(real_columns, month=MONTH)
    assert "Devolva só o que foi perguntado" in text
    assert "LIMIT 1" in text
    assert "um total, devolva uma linha" in text
    assert "Filtre com WHERE" in text
    # natureza_grupo guarda a descricao do grupo, nao o digito
    assert "natureza_grupo (" in text and "Descrição do grupo da natureza jurídica" in text
    # ranking: o menor e o n-esimo devolvem so a linha pedida
    assert "o segundo maior" in text and "LIMIT 1 OFFSET 1" in text
    assert "Não traga linhas além das pedidas" in text
    # contas ficam no SQL, nao na resposta
    assert "Faça as contas no SQL" in text and "100.0" in text
    assert "Não deixe a aritmética para a resposta" in text
    assert "numa única linha" in text and "devolva os valores comparados" in text
    # excluir o grupo politico e filtrar, nao agrupar e escolher depois
    assert "filtre com WHERE is_organizacao_politica = false" in text
    assert "Não agrupe por essa coluna" in text


def test_context_lists_every_cnae_section(real_columns):
    text = build_context(real_columns, month=MONTH)
    for letter, description, _, _ in CNAE_SECOES:
        assert f"{letter} = {description}" in text


def test_context_has_no_individual_data_or_credentials(real_columns):
    text = build_context(real_columns, month=MONTH).lower()
    for forbidden in ("razao_social", "gold_fato_estabelecimento", "access_key", "secret", "arn:aws"):
        assert forbidden not in text


def test_unknown_table_and_unglossed_column_fail_loudly(real_columns):
    with pytest.raises(KeyError, match="sem documentacao"):
        build_context({**real_columns, "fato_estabelecimento": []}, month=MONTH)
    extra = {**real_columns, "dim_porte": [*real_columns["dim_porte"], ("coluna_nova", "string")]}
    with pytest.raises(KeyError, match="coluna sem glossa"):
        build_context(extra, month=MONTH)


MONTHS = ("2026-07", "2026-08", "2026-09", MONTH)


def test_with_one_month_the_prompt_is_the_single_month_prompt(real_columns):
    single = build_context(real_columns, month=MONTH)
    assert build_context(real_columns, month=MONTH, months=(MONTH,)) == single
    assert "Meses\n" not in single and "reference_month" in single  # so a regra de nao filtrar o mes


def test_with_several_months_the_prompt_explains_how_to_compare_them(real_columns):
    text = build_context(real_columns, month=MONTH, months=MONTHS)
    assert ", ".join(MONTHS) in text
    assert f"O mês padrão é {MONTH}" in text
    assert f"filtre com reference_month = '{MONTH}'" in text  # sem mês na pergunta, o mais recente, explícito
    assert f"que os números são de {MONTH}" in text
    assert "nunca some" in text and "SUM(CASE WHEN reference_month" in text
    assert "Não filtre por reference_month" not in text
    # a coluna do mes aparece nos marts e nao nas dimensoes
    marts = text.count("- reference_month (string)")
    assert marts == len([n for n in TABLES if n.startswith("mart_")])


def test_the_month_list_is_not_trusted_to_be_sorted_or_unique(real_columns):
    shuffled = build_context(real_columns, month=MONTH, months=(MONTH, "2026-07", "2026-07", "2026-09"))
    assert "2026-07, 2026-09, 2026-10" in shuffled
