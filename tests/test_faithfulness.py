from __future__ import annotations

from decimal import Decimal

import pytest

from cnpj_pipeline.agent.ask import MAX_TOOL_ROWS, Answer, QueryRecord
from cnpj_pipeline.agent.faithfulness import check_answer, check_text, extract_numbers


def check(text, rows, *, question="Quantas?", sqls=()):
    return check_text(text, question=question, rows=[rows], sqls=sqls)


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("São 1.234.567 empresas", [Decimal("1234567")]),
        ("A taxa é 12,5%", [Decimal("12.5")]),
        ("About 1,234,567 firms", [Decimal("1234567")]),
        ("Rate of 12.5 percent", [Decimal("12.5")]),
        ("Foram 12.345,67 reais", [Decimal("12345.67")]),
    ],
)
def test_extract_numbers_reads_brazilian_and_english_formats(text, values):
    assert [m.values[0] for m in extract_numbers(text)] == values


def test_extract_numbers_keeps_both_readings_of_an_ambiguous_token():
    (mention,) = extract_numbers("são 1.234 empresas")
    assert set(mention.values) == {Decimal("1234"), Decimal("1.234")}


def test_extract_numbers_ignores_dates_list_markers_and_identifiers():
    text = "1. SP\n2) RJ\nDados de 2026-10, 13/11/2026 e 11/2026 na tabela mart_v2 e no CNAE a1"
    assert extract_numbers(text) == []


def test_extract_numbers_reads_scales_and_percent():
    scaled, percent = extract_numbers("cerca de 1,2 milhão, ou 45% do total")
    assert scaled.scale == 10**6 and not scaled.percent
    assert percent.percent and percent.scale == 1


def test_exact_numbers_from_the_result_are_supported():
    assert check("SP tem 1.234.567 empresas ativas.", [[1234567]]).faithful is True
    assert check("SP tem 1.234.567 empresas ativas.", [[Decimal("1234567")]]).faithful is True


def test_a_wrong_digit_is_unsupported():
    result = check("SP tem 1.234.568 empresas ativas.", [[1234567]])
    assert result.faithful is False and result.unsupported == ["1.234.568"]


def test_a_number_that_is_not_in_the_result_is_unsupported_and_counted():
    result = check("SP tem 100.000 e RJ tem 200.000.", [[100000], [300000]])
    assert result.checked == 2 and result.unsupported == ["200.000"]


def test_rounding_and_truncation_to_the_shown_decimals_are_accepted():
    assert check("A parcela é 12,3%.", [[12.3456]]).faithful is True
    assert check("A parcela é 12,4%.", [[12.3856]]).faithful is True  # arredondado
    assert check("A parcela é 12,3%.", [[12.3856]]).faithful is True  # truncado
    assert check("A parcela é 12,5%.", [[12.3856]]).faithful is False


def test_a_percent_matches_a_ratio_in_the_result():
    assert check("São 12,3% das empresas.", [[0.123]]).faithful is True
    assert check("São 13% das empresas.", [[0.123]]).faithful is False


def test_scaled_numbers_match_the_rounded_value():
    assert check("São cerca de 1,2 milhão de empresas.", [[1234567]]).faithful is True
    assert check("São cerca de 2 milhões de empresas.", [[1234567]]).faithful is False
    assert check("Cerca de 5 mil empresas.", [[5234]]).faithful is True


def test_numbers_in_the_question_the_sql_and_the_row_count_are_supported():
    result = check(
        "Em 2022, nas 3 maiores UFs do top 10, SP lidera com 50.",
        [[50], [40], [30]],
        question="Qual UF teve mais aberturas em 2022?",
        sqls=["SELECT uf, n FROM t WHERE ano = 2022 ORDER BY n DESC LIMIT 10"],
    )
    assert result.faithful is True  # 2022 da pergunta, 10 do LIMIT, 3 linhas, 50 do resultado


def test_the_month_of_the_executed_sql_supports_the_year_and_month_in_the_text():
    result = check(
        "Em outubro de 2026 havia 700 empresas.",
        [[700]],
        sqls=["SELECT SUM(n) FROM t WHERE reference_month = '2026-10'"],
    )
    assert result.faithful is True


def test_numbers_inside_text_cells_and_negative_values_are_supported():
    assert check("A natureza 2135 tem 10 empresas.", [["2135", 10]]).faithful is True
    assert check("A queda foi de 500.", [[-500]]).faithful is True


def test_a_sum_done_in_the_text_is_unsupported():
    assert check("Juntas, SP e RJ somam 300.", [[100], [200]]).unsupported == ["300"]


def test_no_numbers_means_nothing_to_check():
    result = check("Não há dados para essa pergunta.", [[1]])
    assert result.checked == 0 and result.faithful is None


def test_check_answer_uses_only_the_rows_the_model_was_shown():
    rows = [[i] for i in range(MAX_TOOL_ROWS + 10)]
    query = QueryRecord(sql="SELECT n FROM t", ok=True, columns=["n"], rows=rows)

    seen = Answer("answered", "Há 7 casos.", [query])
    unseen = Answer("answered", f"Há {MAX_TOOL_ROWS + 5} casos.", [query])
    assert check_answer("Quantos?", seen).faithful is True
    assert check_answer("Quantos?", unseen).faithful is False


def test_check_answer_ignores_failed_queries():
    good = QueryRecord(sql="SELECT 1", ok=True, columns=["n"], rows=[[10]])
    bad = QueryRecord(sql="SELECT 2", ok=False, error="recusado", rows=[[99]])
    assert check_answer("?", Answer("answered", "São 99.", [good, bad])).faithful is False
    assert check_answer("?", Answer("answered", "São 10.", [good, bad])).faithful is True


def test_numbers_from_the_documentation_the_model_saw_are_supported():
    text = "A camada silver de Estabelecimentos levou 77 segundos."
    seen = ["fonte: README.md\nSilver de Estabelecimentos: 77 s"]

    assert check_text(text, question="quanto tempo?", rows=[], docs=seen).faithful
    unsupported = check_text(text, question="quanto tempo?", rows=[], docs=["fonte: README.md\nSilver: 12 s"])
    assert not unsupported.faithful and unsupported.unsupported == ["77"]
    assert not check_text(text, question="quanto tempo?", rows=[]).faithful


def test_check_answer_uses_the_documentation_text_shown_to_the_model():
    from cnpj_pipeline.agent.ask import DocSearch

    answer = Answer(
        "answered",
        "Foram 77 segundos.",
        docs=[DocSearch(query="silver", sources=["README.md"], shown="[1] fonte: README.md\nSilver: 77 s")],
    )
    assert check_answer("quanto tempo?", answer).faithful
    assert not check_answer("quanto tempo?", Answer("answered", "Foram 77 segundos.")).faithful
