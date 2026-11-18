"""O Terraform de observabilidade combina com o registro que a funcao escreve: cada campo usado
nas consultas salvas e nos filtros de metrica existe no registro, nenhum campo sensivel e usado, e
os alarmes e o painel apontam para metricas que existem."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_agent_ask import turn
from test_api import Rig, executor, gold_root, make_config, system  # noqa: F401  (fixtures)

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "infra" / "modules" / "api"
OBS = MODULE / "observability.tf"

needs_infra = pytest.mark.skipif(not OBS.exists(), reason="infra/ ausente")

# Palavras da linguagem de consulta do Logs Insights e dos filtros de metrica.
KEYWORDS = {
    "filter", "stats", "sort", "by", "as", "bin", "pct", "avg", "sum", "count", "and", "or", "not",
    "desc", "asc", "limit", "display", "fields", "source",
}  # fmt: skip
SENSITIVE = {"question", "pergunta", "sql", "answer", "resposta", "ip", "key", "salt", "message", "text"}


def run_and_capture(executor, system, capsys) -> set[str]:  # noqa: F811
    keys: set[str] = set()
    scenarios = [
        {},
        {"script": [turn("Nao sei.")]},
        {"config": make_config(per_minute=1), "repeat": 2},
    ]
    for scenario in scenarios:
        rig = Rig(executor, system, script=scenario.get("script"), config=scenario.get("config"))
        for _ in range(scenario.get("repeat", 1)):
            rig.ask()
    for line in capsys.readouterr().out.splitlines():
        keys |= set(json.loads(line))
    return keys


def terraform_text() -> str:
    return OBS.read_text(encoding="utf-8")


def insights_queries(text: str) -> list[str]:
    return re.findall(r"<<-EOT\n(.*?)\n\s*EOT", text, re.S)


def dashboard_queries(text: str) -> list[str]:
    raw = re.findall(r'query\s*=\s*"SOURCE \'\$\{local\.log_group_name\}\' \| (.*?)"\n', text)
    return [q.replace('\\"', '"') for q in raw]


def identifiers(query: str) -> tuple[set[str], set[str]]:
    """(campos usados, apelidos criados) de uma consulta do Logs Insights."""
    query = re.sub(r'"[^"]*"', " ", query)  # literais de texto
    aliases = set(re.findall(r"\bas\s+([a-z_0-9]+)", query))
    names = set(re.findall(r"\b[a-z_][a-z_0-9]*\b", query))
    return names - KEYWORDS - aliases, aliases


@needs_infra
def test_the_log_line_has_every_field_the_queries_and_filters_use(executor, system, capsys):  # noqa: F811
    keys = run_and_capture(executor, system, capsys)
    assert {"event", "status", "ms", "cost_usd", "numbers_checked", "unsupported_numbers", "reason"} <= keys

    text = terraform_text()
    queries = insights_queries(text) + dashboard_queries(text)
    assert len(queries) >= 8
    for query in queries:
        used, _ = identifiers(query)
        assert used <= keys, (query, used - keys)
        assert not used & SENSITIVE, (query, used & SENSITIVE)

    patterns = re.findall(r'pattern\s*=\s*"(.*?)"\n', text)
    values = re.findall(r'value\s*=\s*"\$\.([a-z_]+)"', text)
    assert len(patterns) == 5
    for pattern in patterns:
        fields = set(re.findall(r"\$\.([a-z_]+)", pattern))
        assert fields and fields <= keys, (pattern, fields - keys)
    assert set(values) <= keys


@needs_infra
def test_the_log_line_is_pure_json_with_no_sensitive_field(executor, system, capsys):  # noqa: F811
    keys = run_and_capture(executor, system, capsys)
    assert not keys & SENSITIVE, keys & SENSITIVE


@needs_infra
def test_alarms_and_the_dashboard_use_only_metrics_that_the_filters_create():
    text = terraform_text()
    created = set(re.findall(r'metric_transformation \{\s*name\s*=\s*"(\w+)"', text))
    namespaces = re.findall(r"namespace\s*=\s*(\S+)", text)
    assert created == {"Asks", "Errors", "BudgetHits", "CostUsd", "AnswersWithUnsupportedNumbers"}
    assert 'metric_ns      = "CnpjPipeline/Api"' in text
    assert namespaces and set(namespaces) == {"local.metric_ns"}  # todas as metricas no mesmo espaco

    alarmed = set(re.findall(r'metric_name\s*=\s*"(\w+)"', text))
    assert alarmed == {"CostUsd", "Errors", "BudgetHits"} and alarmed <= created
    shown = set(re.findall(r'\[local\.metric_ns, "(\w+)"\]', text))
    assert shown and shown <= created


@needs_infra
def test_the_observability_stays_inside_the_free_tier_counts_of_cloudwatch():
    text = terraform_text()
    assert (
        len(re.findall(r'resource "aws_cloudwatch_log_metric_filter"', text)) <= 10
    )  # metricas personalizadas
    assert len(re.findall(r'resource "aws_cloudwatch_metric_alarm"', text)) <= 10
    assert len(re.findall(r'resource "aws_cloudwatch_dashboard"', text)) <= 3


@needs_infra
def test_the_alarm_email_is_optional_and_never_written_in_the_files():
    variables = (MODULE / "variables.tf").read_text(encoding="utf-8")
    block = variables[variables.index('variable "alarm_email"') :]
    assert 'default     = ""' in block[: block.index("\n}")]
    text = terraform_text()
    assert "count = var.alarm_email" in text and "alarm_actions       = local.alarm_actions" in text
    for path in (REPO / "infra").rglob("*.tf"):
        assert not re.search(r"[\w.]+@[\w.]+\.\w+", path.read_text(encoding="utf-8")), path


@needs_infra
def test_the_function_role_gets_no_new_permission_for_the_observability():
    main = (MODULE / "main.tf").read_text(encoding="utf-8")
    for action in ("cloudwatch:", "sns:", "logs:StartQuery", "logs:GetQueryResults"):
        assert action not in main  # os alarmes e o painel sao do usuario que aplica, nao da funcao


def test_the_field_extractor_catches_an_unknown_or_sensitive_field():
    used, aliases = identifiers(
        'filter event = "ask" | stats count(*) as n, sum(cost_usd) as total by bin(1d) as dia'
    )
    assert used == {"event", "cost_usd"} and aliases == {"n", "total", "dia"}
    used, _ = identifiers("filter event = 'x' | stats count(*) as n by question")
    assert "question" in used and used & SENSITIVE
