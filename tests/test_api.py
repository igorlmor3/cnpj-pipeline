"""Testes da API publica do agente: configuracao, limites, armazenamento, interruptor e o pedido completo.

O que importa aqui e que tudo falha fechado (sem parametro, contador, razao de gasto ou com o
interruptor desligado, o modelo nao e chamado) e que nada sensivel vai para a resposta nem para o log.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from helpers import MONTH
from test_agent_ask import GOOD_SQL, FakeLLM, turn
from test_gold import build_silver, run_gold

from cnpj_pipeline.agent.ask import Answer, QueryRecord, build_system_prompt
from cnpj_pipeline.agent.budget import InMemoryLedger
from cnpj_pipeline.agent.context import TABLES
from cnpj_pipeline.agent.executor import DuckDBExecutor
from cnpj_pipeline.agent.llm import LLMError
from cnpj_pipeline.api import handler
from cnpj_pipeline.api.columns import ColumnsError, load_columns, write_columns
from cnpj_pipeline.api.config import ApiConfig, ConfigError
from cnpj_pipeline.api.handler import MAX_BODY_BYTES, Api, lambda_handler
from cnpj_pipeline.api.limits import Limiter, visitor_key
from cnpj_pipeline.api.stores import (
    DynamoCounters,
    DynamoLedger,
    InMemoryCounters,
    InMemoryParams,
    KillSwitch,
    SsmParams,
)
from cnpj_pipeline.cloud.athena import gold_columns

PREFIX = "/cnpj-pipeline/api"
ORIGIN = "https://igorlmor3.github.io"
API_KEY = "sk-ant-api03-CHAVE-FALSA-DE-TESTE"
SALT = "sal-secreto-de-teste"
NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC).timestamp()

ENV = {
    "CNPJ_API_MONTH": MONTH,
    "CNPJ_API_WORKGROUP": "cnpj-pipeline",
    "CNPJ_API_TABLE": "cnpj-api-state",
    "CNPJ_API_PARAM_PREFIX": PREFIX,
    "CNPJ_API_ALLOWED_ORIGINS": ORIGIN,
}


# --------------------------------------------------------------------------- #
# Apoio
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def gold_root(tmp_path_factory):
    base = tmp_path_factory.mktemp("api")
    build_silver(base)
    assert run_gold(base) == 0
    return base / "gold"


@pytest.fixture(scope="module")
def system(gold_root):
    return build_system_prompt(gold_columns(gold_root, MONTH, list(TABLES)), month=MONTH)


@pytest.fixture()
def executor(gold_root):
    ex = DuckDBExecutor(gold_root)
    yield ex
    ex.close()


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_config(**overrides: Any) -> ApiConfig:
    values: dict[str, Any] = {
        "month": MONTH,
        "database": "cnpj",
        "workgroup": "cnpj-pipeline",
        "table": "cnpj-api-state",
        "allowed_origins": (ORIGIN,),
        "param_prefix": PREFIX,
    }
    values.update(overrides)
    return ApiConfig(**values)


def make_params(**overrides: str | None) -> InMemoryParams:
    values = {
        f"{PREFIX}/enabled": "true",
        f"{PREFIX}/anthropic-api-key": API_KEY,
        f"{PREFIX}/salt": SALT,
    }
    for name, value in overrides.items():
        key = f"{PREFIX}/{name.replace('_', '-')}"
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    return InMemoryParams(values)


class Rig:
    """Uma API montada com modelo falso, gold real em DuckDB e tudo em memoria."""

    def __init__(self, executor, system, *, script=None, config=None, params=None, counters=None, clock=None):
        self.llm = FakeLLM(
            *(script if script is not None else [turn("", GOOD_SQL), turn("Resposta pronta.")])
        )
        self.config = config or make_config()
        self.params = params or make_params()
        self.counters = counters or InMemoryCounters(clock or Clock())
        self.ledger = InMemoryLedger()
        self.clock = clock or Clock()
        self.mono = Clock(0.0)
        self.keys: list[str] = []
        self.api = Api(
            self.config,
            params=self.params,
            counters=self.counters,
            ledger=self.ledger,
            executor=executor,
            system=system,
            llm_factory=lambda: self.llm,
            set_api_key=self.keys.append,
            clock=self.clock,
            monotonic=self.mono,
        )

    def ask(self, question="Quantas empresas ativas por UF?", **kwargs):
        return self.api.handle(make_event(question, **kwargs))


def make_event(
    question: Any = "Quantas empresas ativas por UF?",
    *,
    method="POST",
    path="/ask",
    origin: str | None = ORIGIN,
    ip="203.0.113.7",
    content_type: str | None = "application/json",
    body: Any = None,
    base64_body=False,
) -> dict[str, Any]:
    raw = body if body is not None else json.dumps({"question": question})
    headers: dict[str, str] = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if origin is not None:
        headers["Origin"] = origin
    event: dict[str, Any] = {
        "headers": headers,
        "requestContext": {"http": {"method": method, "path": path, "sourceIp": ip}},
        "body": base64.b64encode(raw.encode()).decode() if base64_body else raw,
        "isBase64Encoded": base64_body,
    }
    return event


def body_of(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


# --------------------------------------------------------------------------- #
# Configuracao
# --------------------------------------------------------------------------- #


def test_config_from_env_reads_required_values_and_defaults():
    config = ApiConfig.from_env(ENV)
    assert (config.month, config.database, config.workgroup, config.table) == (
        MONTH,
        "cnpj",
        "cnpj-pipeline",
        "cnpj-api-state",
    )
    assert config.allowed_origins == (ORIGIN,)
    assert (config.per_minute, config.per_day, config.global_per_day) == (3, 15, 150)
    assert config.monthly_budget_usd == 4.0
    assert config.param_enabled == f"{PREFIX}/enabled"
    assert config.param_api_key == f"{PREFIX}/anthropic-api-key"
    assert config.param_salt == f"{PREFIX}/salt"


def test_config_overrides_from_env():
    env = {
        **ENV,
        "CNPJ_API_PER_MINUTE": "2",
        "CNPJ_API_PER_DAY": "7",
        "CNPJ_API_GLOBAL_PER_DAY": "50",
        "CNPJ_API_MONTHLY_BUDGET_USD": "2.5",
        "CNPJ_API_DEADLINE_S": "12",
        "CNPJ_API_MODEL": "outro-modelo",
        "CNPJ_API_ALLOWED_ORIGINS": f"{ORIGIN}, http://localhost:5173",
    }
    config = ApiConfig.from_env(env)
    assert (config.per_minute, config.per_day, config.global_per_day) == (2, 7, 50)
    assert (config.monthly_budget_usd, config.deadline_s, config.model) == (2.5, 12.0, "outro-modelo")
    assert config.allowed_origins == (ORIGIN, "http://localhost:5173")


def test_config_reads_the_list_of_months_and_defaults_to_the_one_month():
    assert ApiConfig.from_env(ENV).available_months == (MONTH,)
    env = {**ENV, "CNPJ_API_MONTHS": f"{MONTH}, 2026-08,2026-09"}
    config = ApiConfig.from_env(env)
    assert config.months == ("2026-08", "2026-09", MONTH) and config.available_months == config.months


@pytest.mark.parametrize(
    "months",
    ["2026-08,2026-09", f"{MONTH},{MONTH}", f"{MONTH},2026-13", f"{MONTH},agosto"],
)
def test_config_rejects_a_list_of_months_that_is_wrong(months):
    with pytest.raises(ConfigError, match="MONTHS"):
        ApiConfig.from_env({**ENV, "CNPJ_API_MONTHS": months})


@pytest.mark.parametrize("missing", ["MONTH", "WORKGROUP", "TABLE", "PARAM_PREFIX"])
def test_config_requires_the_essentials(missing):
    env = {k: v for k, v in ENV.items() if k != f"CNPJ_API_{missing}"}
    with pytest.raises(ConfigError, match=missing):
        ApiConfig.from_env(env)


@pytest.mark.parametrize(
    "changes",
    [
        {"CNPJ_API_MONTH": "2026-13"},
        {"CNPJ_API_MONTH": "outubro"},
        {"CNPJ_API_MONTH": "2026-10\n"},
        {"CNPJ_API_PARAM_PREFIX": "sem-barra"},
        {"CNPJ_API_PER_MINUTE": "0"},
        {"CNPJ_API_PER_DAY": "abc"},
        {"CNPJ_API_MONTHLY_BUDGET_USD": "0"},
        {"CNPJ_API_DEADLINE_S": "-1"},
        {"CNPJ_API_ALLOWED_ORIGINS": "*"},
        {"CNPJ_API_ALLOWED_ORIGINS": "http://exemplo.com"},
        {"CNPJ_API_ALLOWED_ORIGINS": "http://localhost.exemplo.com"},
        {"CNPJ_API_ALLOWED_ORIGINS": "https://*.exemplo.com"},
        {"CNPJ_API_ALLOWED_ORIGINS": "https://exemplo.com/"},
        {"CNPJ_API_ALLOWED_ORIGINS": "https://exemplo.com/caminho"},
    ],
)
def test_config_rejects_invalid_values(changes):
    with pytest.raises(ConfigError):
        ApiConfig.from_env({**ENV, **changes})


def test_config_errors_do_not_echo_values():
    with pytest.raises(ConfigError) as info:
        ApiConfig.from_env({**ENV, "CNPJ_API_ALLOWED_ORIGINS": "http://segredo.exemplo"})
    assert "segredo" not in str(info.value)


def test_no_allowed_origins_is_valid_and_blocks_every_browser_origin(executor, system):
    rig = Rig(executor, system, config=make_config(allowed_origins=()))
    assert rig.ask(origin=ORIGIN)["statusCode"] == 403
    assert rig.ask(origin=None)["statusCode"] == 200


# --------------------------------------------------------------------------- #
# Visitante e limites
# --------------------------------------------------------------------------- #


def test_visitor_key_is_stable_salted_and_hides_the_ip():
    key = visitor_key("203.0.113.7", SALT)
    assert key == visitor_key("203.0.113.7", SALT)
    assert key != visitor_key("203.0.113.8", SALT)
    assert key != visitor_key("203.0.113.7", "outro-sal")
    assert len(key) == 16 and "203" not in key


def test_ipv6_is_grouped_by_slash_64_and_ipv4_is_not():
    a = visitor_key("2001:db8:1:2::1", SALT)
    assert a == visitor_key("2001:db8:1:2:ffff:ffff:ffff:ffff", SALT)
    assert a != visitor_key("2001:db8:1:3::1", SALT)
    assert visitor_key("203.0.113.7", SALT) != visitor_key("203.0.113.8", SALT)


@pytest.mark.parametrize("ip", [None, "", "nao-e-ip", "999.1.1.1"])
def test_requests_without_a_valid_ip_share_one_bucket(ip):
    assert visitor_key(ip, SALT) == visitor_key(None, SALT)


def make_limiter(clock, **kwargs):
    values = {"per_minute": 3, "per_day": 5, "global_per_day": 100}
    values.update(kwargs)
    return Limiter(InMemoryCounters(clock), clock=clock, **values)


def test_the_per_minute_limit_blocks_and_reports_when_to_retry():
    clock = Clock(NOW)  # 12:00:00 UTC
    limiter = make_limiter(clock)
    assert [limiter.check("v").allowed for _ in range(3)] == [True, True, True]
    verdict = limiter.check("v")
    assert (verdict.allowed, verdict.reason) == (False, "minute")
    assert 1 <= verdict.retry_after_s <= 60
    clock.now += 61
    assert limiter.check("v").allowed


def test_the_daily_limit_blocks_until_the_next_utc_day():
    clock = Clock(NOW)
    limiter = make_limiter(clock, per_minute=100)
    assert all(limiter.check("v").allowed for _ in range(5))
    verdict = limiter.check("v")
    assert (verdict.allowed, verdict.reason) == (False, "day")
    assert verdict.retry_after_s == 12 * 3600  # de 12:00 ate a meia-noite UTC
    clock.now += 12 * 3600
    assert limiter.check("v").allowed


def test_visitors_are_counted_separately():
    limiter = make_limiter(Clock())
    for _ in range(3):
        limiter.check("a")
    assert not limiter.check("a").allowed
    assert limiter.check("b").allowed


def test_the_global_limit_blocks_everyone_and_is_not_consumed_by_blocked_visitors():
    limiter = make_limiter(Clock(), per_minute=2, per_day=100, global_per_day=3)
    assert limiter.check("a").allowed and limiter.check("a").allowed
    for _ in range(50):  # "a" bate no limite por minuto: nao pode gastar a cota global
        assert limiter.check("a").reason == "minute"
    assert limiter.check("b").allowed  # 3a e ultima da cota global
    verdict = limiter.check("c")
    assert (verdict.allowed, verdict.reason) == (False, "global")


def test_in_memory_counters_expire():
    clock = Clock(1000.0)
    counters = InMemoryCounters(clock)
    assert [counters.incr("k", 10) for _ in range(3)] == [1, 2, 3]
    clock.now += 11
    assert counters.incr("k", 10) == 1


# --------------------------------------------------------------------------- #
# Armazenamento
# --------------------------------------------------------------------------- #


class FakeDynamo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.items: dict[str, dict[str, int]] = {}

    def update_item(self, **kwargs):
        self.calls.append(("update_item", kwargs))
        key = kwargs["Key"]["pk"]["S"]
        item = self.items.setdefault(key, {})
        for attr, value in (("n", ":one"), ("micros", ":m")):
            if value in kwargs["ExpressionAttributeValues"]:
                item[attr] = item.get(attr, 0) + int(kwargs["ExpressionAttributeValues"][value]["N"])
        return {"Attributes": {a: {"N": str(v)} for a, v in item.items()}}

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        item = self.items.get(kwargs["Key"]["pk"]["S"])
        return {"Item": {a: {"N": str(v)} for a, v in item.items()}} if item else {}


def test_dynamo_counters_use_one_atomic_update_with_a_ttl():
    client = FakeDynamo()
    counters = DynamoCounters(client, "tabela", clock=lambda: 1000.0)
    assert counters.incr("v#abc#m#5", 120) == 1
    assert counters.incr("v#abc#m#5", 120) == 2
    name, call = client.calls[0]
    assert name == "update_item" and call["TableName"] == "tabela"
    assert call["Key"] == {"pk": {"S": "v#abc#m#5"}}
    assert call["UpdateExpression"] == "ADD n :one SET expires_at = if_not_exists(expires_at, :exp)"
    assert call["ExpressionAttributeValues"][":exp"] == {"N": "1120"}
    assert call["ReturnValues"] == "UPDATED_NEW"


def test_dynamo_ledger_sums_microdollars_and_reads_consistently():
    client = FakeDynamo()
    ledger = DynamoLedger(client, "tabela")
    assert ledger.spent_usd("2026-11") == 0.0
    ledger.add_usd("2026-11", 0.0012)
    ledger.add_usd("2026-11", 0.0003)
    assert ledger.spent_usd("2026-11") == pytest.approx(0.0015)
    assert client.items["spend#2026-11"]["micros"] == 1500
    assert [c for n, c in client.calls if n == "get_item"][-1]["ConsistentRead"] is True
    assert ledger.spent_usd("2026-10") == 0.0  # outro periodo, outra linha


def test_dynamo_ledger_ignores_zero_and_never_rounds_a_cost_down_to_nothing():
    client = FakeDynamo()
    ledger = DynamoLedger(client, "tabela")
    ledger.add_usd("p", 0.0)
    ledger.add_usd("p", -1.0)
    assert client.calls == []
    ledger.add_usd("p", 1e-9)
    assert client.items["spend#p"]["micros"] == 1


def test_ssm_params_ask_for_decryption_only_when_told_to():
    class Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def get_parameter(self, **kwargs):
            self.calls.append(kwargs)
            return {"Parameter": {"Value": "valor"}}

    client = Client()
    params = SsmParams(client)
    assert params.get("/x") == "valor" and params.get("/y", decrypt=True) == "valor"
    assert [c["WithDecryption"] for c in client.calls] == [False, True]


@pytest.mark.parametrize("value", ["true", "True", " TRUE \n"])
def test_the_switch_is_on_only_for_true(value):
    assert KillSwitch(InMemoryParams({"p": value}), "p", Clock()).enabled() is True


@pytest.mark.parametrize("value", ["false", "", "1", "yes", "on", "truee", "nao"])
def test_any_other_value_turns_the_switch_off(value):
    assert KillSwitch(InMemoryParams({"p": value}), "p", Clock()).enabled() is False


def test_the_switch_fails_closed_and_retries_soon_after_an_error():
    clock = Clock(0.0)
    params = InMemoryParams()  # o parametro nao existe: get levanta KeyError
    switch = KillSwitch(params, "p", clock)
    assert switch.enabled() is False
    params.values["p"] = "true"
    assert switch.enabled() is False  # ainda dentro dos 5 s do erro
    clock.now += 6
    assert switch.enabled() is True


def test_the_switch_value_is_cached_and_then_refreshed():
    clock = Clock(0.0)
    params = InMemoryParams({"p": "true"})
    switch = KillSwitch(params, "p", clock)
    assert switch.enabled() and switch.enabled() and len(params.reads) == 1
    params.values["p"] = "false"
    clock.now += 10
    assert switch.enabled() is True  # dentro dos 30 s
    clock.now += 21
    assert switch.enabled() is False and len(params.reads) == 2


# --------------------------------------------------------------------------- #
# Colunas
# --------------------------------------------------------------------------- #


def test_columns_roundtrip_through_the_package_file(gold_root, tmp_path):
    out = write_columns(gold_root, MONTH, tmp_path / "pkg" / "agent_columns.json")
    loaded = load_columns(out, MONTH)
    expected = gold_columns(gold_root, MONTH, list(TABLES))
    assert {m: [tuple(c) for c in cols] for m, cols in loaded.items()} == {
        m: [tuple(c) for c in cols] for m, cols in expected.items()
    }
    text = out.read_text(encoding="utf-8")
    assert "EMPRESA SECRETA" not in text and "reference_month" in text  # so nomes e tipos de colunas


def test_the_prompt_from_the_file_equals_the_prompt_from_the_gold(gold_root, tmp_path, system):
    out = write_columns(gold_root, MONTH, tmp_path / "c.json")
    assert build_system_prompt(load_columns(out, MONTH), month=MONTH) == system


def test_columns_file_problems_are_reported_without_paths(gold_root, tmp_path):
    out = write_columns(gold_root, MONTH, tmp_path / "c.json")
    with pytest.raises(ColumnsError, match="outro mes"):
        load_columns(out, "2026-09")
    with pytest.raises(ColumnsError, match="ausente"):
        load_columns(tmp_path / "nao-existe.json", MONTH)
    payload = json.loads(out.read_text(encoding="utf-8"))
    payload["colunas"].pop("dim_porte")
    out.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ColumnsError, match="exatamente"):
        load_columns(out, MONTH)
    payload["formato"] = 99
    out.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ColumnsError, match="formato"):
        load_columns(out, MONTH)
    out.write_text("nao e json", encoding="utf-8")
    with pytest.raises(ColumnsError, match="ilegivel"):
        load_columns(out, MONTH)


# --------------------------------------------------------------------------- #
# O pedido completo
# --------------------------------------------------------------------------- #


def test_a_question_is_answered_with_the_last_query_result(executor, system):
    rig = Rig(executor, system)
    response = rig.ask()
    body = body_of(response)

    assert response["statusCode"] == 200
    assert body["status"] == "answered" and body["message"] == "Resposta pronta."
    assert body["month"] == MONTH
    assert body["sql"].startswith("SELECT") and "gold_mart_simples_mei_uf" in body["sql"]
    assert body["columns"] == ["uf", "empresas_ativas"] and body["rows"]
    assert body["truncated"] is False
    assert response["headers"]["content-type"].startswith("application/json")
    assert response["headers"]["cache-control"] == "no-store"


def test_the_api_key_is_read_once_per_instance_and_handed_to_the_client(executor, system):
    rig = Rig(executor, system, script=[turn("", GOOD_SQL), turn("a"), turn("", GOOD_SQL), turn("b")])
    rig.api.limiter.per_minute = 10
    rig.ask()
    rig.ask()
    assert rig.keys == [API_KEY]
    assert rig.params.reads.count(f"{PREFIX}/anthropic-api-key") == 1
    assert rig.params.reads.count(f"{PREFIX}/salt") == 1


def test_nothing_sensitive_is_in_the_response_or_the_log(executor, system, caplog):
    rig = Rig(executor, system)
    caplog.set_level(logging.DEBUG)
    response = rig.ask("Pergunta secreta sobre o cadastro?", ip="198.51.100.23")
    haystack = response["body"] + json.dumps(response["headers"]) + caplog.text
    for secret in (API_KEY, SALT, "198.51.100.23", "Pergunta secreta"):
        assert secret not in haystack
    assert '"event": "ask"' in caplog.text and '"status": "answered"' in caplog.text
    record = json.loads(next(r.message for r in caplog.records if r.message.startswith("{")))
    assert set(record) >= {"visitor", "ms", "llm_calls", "queries", "cost_usd", "http"}
    assert len(record["visitor"]) == 8


def test_a_refusal_has_no_result_in_the_body(executor, system):
    rig = Rig(executor, system, script=[turn("Não posso responder.")])
    response = rig.ask("Qual a razão social do CNPJ 00.000.000/0001-91?")
    body = body_of(response)
    assert response["statusCode"] == 200 and body["status"] == "out_of_scope"
    assert not ({"sql", "columns", "rows"} & set(body))


def test_the_rows_returned_are_capped_and_flagged(executor, system, monkeypatch):
    monkeypatch.setattr(handler, "MAX_ROWS_RETURNED", 1)
    body = body_of(Rig(executor, system).ask())
    assert len(body["rows"]) == 1 and body["truncated"] is True


def test_an_invalid_question_is_a_400_and_never_reaches_the_model(executor, system):
    rig = Rig(executor, system, script=[])
    response = rig.ask("x" * 301)
    assert response["statusCode"] == 400 and body_of(response)["status"] == "invalid_question"
    assert rig.llm.calls == []


def test_model_and_internal_errors_map_to_status_codes_with_fixed_messages(executor, system):
    rig = Rig(executor, system, script=[LLMError("detalhe do provedor: chave sk-ant-XYZ")])
    response = rig.ask()
    assert response["statusCode"] == 502 and body_of(response)["status"] == "llm_error"
    assert "sk-ant" not in response["body"] and "provedor" not in response["body"]

    class Broken:
        dialect = "duckdb"

        def run(self, sql, *, max_rows):
            raise RuntimeError("caminho /secreto")

    rig = Rig(Broken(), system, script=[turn("", GOOD_SQL), turn("ok")])
    response = rig.ask()
    assert body_of(response)["status"] in {"failed", "internal_error"}
    assert "secreto" not in response["body"]


def test_ask_receives_the_month_budget_and_deadline_from_the_config(executor, system, monkeypatch):
    seen: dict[str, Any] = {}

    def fake_ask(question, **kwargs):
        seen.update(kwargs, question=question)
        return Answer("answered", "ok")

    monkeypatch.setattr(handler, "ask", fake_ask)
    rig = Rig(executor, system, config=make_config(deadline_s=17.0, monthly_budget_usd=2.5))
    rig.ask("Pergunta?")
    assert seen["question"] == "Pergunta?" and seen["month"] == MONTH
    assert seen["months"] == (MONTH,)
    assert seen["deadline_s"] == 17.0 and seen["system"] == system
    assert (
        seen["budget"].limit_usd == 2.5 and seen["budget"].period == "2026-11"
    )  # mes do relogio, nao o dos dados
    assert seen["budget"].ledger is rig.ledger


def test_the_agent_reads_every_configured_month_and_the_response_lists_them(executor, system, monkeypatch):
    seen: dict[str, Any] = {}

    def fake_ask(question, **kwargs):
        seen.update(kwargs)
        return Answer("answered", "ok")

    monkeypatch.setattr(handler, "ask", fake_ask)
    months = ("2026-08", MONTH)
    rig = Rig(executor, system, config=make_config(months=months))
    body = body_of(rig.ask("Pergunta?"))
    assert seen["months"] == months
    assert body["month"] == MONTH and body["months"] == list(months)


# -- entrada ----------------------------------------------------------------------------------


def test_health_reports_the_switch_without_touching_counters_or_the_model(executor, system):
    rig = Rig(executor, system, script=[])
    response = rig.api.handle(make_event(method="GET", path="/health", origin=None, body=""))
    assert response["statusCode"] == 200 and body_of(response) == {"status": "ok", "enabled": True}
    rig.params.values[f"{PREFIX}/enabled"] = "false"
    rig.mono.now += 60
    assert body_of(rig.api.handle(make_event(method="GET", path="/health", origin=None)))["enabled"] is False
    assert rig.llm.calls == [] and rig.keys == []


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/ask"), ("PUT", "/ask"), ("DELETE", "/"), ("POST", "/outro"), ("POST", "/health")],
)
def test_other_methods_and_paths_are_404(executor, system, method, path):
    rig = Rig(executor, system, script=[])
    assert rig.api.handle(make_event(method=method, path=path))["statusCode"] == 404


def test_both_ask_paths_work(executor, system):
    for path in ("/", "/ask"):
        assert Rig(executor, system).ask(path=path)["statusCode"] == 200


def test_a_disallowed_origin_is_refused_before_anything_else(executor, system):
    rig = Rig(executor, system, script=[])
    response = rig.ask(origin="https://outro-site.example")
    assert response["statusCode"] == 403 and body_of(response)["status"] == "forbidden"
    assert rig.llm.calls == [] and rig.params.reads == []


def test_a_request_without_origin_is_allowed_but_still_limited(executor, system):
    rig = Rig(executor, system, script=[turn("", GOOD_SQL), turn("a")] * 5)
    codes = [rig.ask(origin=None)["statusCode"] for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_the_body_must_be_json_with_the_right_content_type(executor, system):
    rig = Rig(executor, system, script=[])
    assert rig.ask(content_type="text/plain")["statusCode"] == 415
    assert rig.ask(content_type=None)["statusCode"] == 415
    assert rig.ask(content_type="Application/JSON; charset=utf-8", body="{")["statusCode"] == 400
    assert rig.llm.calls == []


@pytest.mark.parametrize(
    "body", ["", "nao e json", "[]", "null", '"texto"', "{}", '{"question": 5}', '{"question": null}']
)
def test_malformed_bodies_are_400(executor, system, body):
    assert Rig(executor, system, script=[]).ask(body=body)["statusCode"] == 400


def test_a_large_body_is_413_and_never_parsed(executor, system):
    rig = Rig(executor, system, script=[])
    big = json.dumps({"question": "a" * MAX_BODY_BYTES})
    assert rig.ask(body=big)["statusCode"] == 413
    assert rig.params.reads == []


def test_base64_bodies_are_decoded_and_bad_ones_rejected(executor, system):
    assert Rig(executor, system).ask(base64_body=True)["statusCode"] == 200
    event = make_event()
    event["body"], event["isBase64Encoded"] = "@@não é base64@@", True
    assert Rig(executor, system, script=[]).api.handle(event)["statusCode"] == 400


# -- falha fechada ----------------------------------------------------------------------------


def test_the_switch_off_stops_everything_and_turning_it_on_resumes(executor, system):
    rig = Rig(executor, system, params=make_params(enabled="false"))
    response = rig.ask()
    assert response["statusCode"] == 503 and body_of(response)["status"] == "disabled"
    assert rig.llm.calls == [] and rig.keys == []  # nem a chave foi lida
    assert f"{PREFIX}/anthropic-api-key" not in rig.params.reads
    rig.params.values[f"{PREFIX}/enabled"] = "true"
    rig.mono.now += 31
    assert rig.ask()["statusCode"] == 200


@pytest.mark.parametrize("missing", ["enabled", "anthropic_api_key", "salt"])
def test_a_missing_parameter_is_unavailable_not_an_open_door(executor, system, missing):
    rig = Rig(executor, system, params=make_params(**{missing: None}), script=[])
    response = rig.ask()
    assert response["statusCode"] == 503
    assert body_of(response)["status"] in {"disabled", "unavailable"} and rig.llm.calls == []


@pytest.mark.parametrize("empty", ["anthropic_api_key", "salt"])
def test_an_empty_secret_is_unavailable(executor, system, empty):
    rig = Rig(executor, system, params=make_params(**{empty: ""}), script=[])
    response = rig.ask()
    assert response["statusCode"] == 503 and body_of(response)["status"] == "unavailable"
    assert rig.keys == []


def test_a_counter_outage_is_unavailable_and_the_model_is_not_called(executor, system):
    class Down:
        def incr(self, key, ttl_seconds):
            raise RuntimeError("DynamoDB fora do ar")

    rig = Rig(executor, system, counters=Down(), script=[])
    response = rig.ask()
    assert response["statusCode"] == 503 and body_of(response)["status"] == "unavailable"
    assert "DynamoDB" not in response["body"] and rig.llm.calls == []


def test_a_ledger_outage_does_not_reach_the_model(executor, system):
    class Down:
        def spent_usd(self, period):
            raise RuntimeError("tabela inacessivel")

        def add_usd(self, period, amount):
            raise RuntimeError("tabela inacessivel")

    rig = Rig(executor, system, script=[])
    rig.api.ledger = Down()
    response = rig.ask()
    assert response["statusCode"] == 500 and body_of(response)["status"] == "internal_error"
    assert rig.llm.calls == [] and "tabela" not in response["body"]


# -- limites e orcamento ----------------------------------------------------------------------


def test_the_per_minute_limit_returns_429_with_retry_after(executor, system):
    rig = Rig(executor, system, script=[turn("", GOOD_SQL), turn("a")] * 3)
    assert [rig.ask()["statusCode"] for _ in range(3)] == [200, 200, 200]
    response = rig.ask()
    assert response["statusCode"] == 429 and body_of(response)["status"] == "minute"
    assert 1 <= int(response["headers"]["retry-after"]) <= 60
    assert "retry_after" not in response["headers"]
    assert len(rig.llm.calls) == 6  # a 4a pergunta nao chamou o modelo


def test_visitors_are_limited_independently(executor, system):
    rig = Rig(executor, system, script=[turn("", GOOD_SQL), turn("a")] * 4)
    for _ in range(3):
        rig.ask(ip="203.0.113.1")
    assert rig.ask(ip="203.0.113.1")["statusCode"] == 429
    assert rig.ask(ip="203.0.113.2")["statusCode"] == 200


def test_the_daily_and_global_limits_apply(executor, system):
    config = make_config(per_minute=50, per_day=2, global_per_day=3)
    rig = Rig(executor, system, config=config, script=[turn("", GOOD_SQL), turn("a")] * 4)
    assert [rig.ask(ip="203.0.113.1")["statusCode"] for _ in range(2)] == [200, 200]
    day = rig.ask(ip="203.0.113.1")
    assert day["statusCode"] == 429 and body_of(day)["status"] == "day"
    assert rig.ask(ip="203.0.113.2")["statusCode"] == 200  # 3a pergunta do dia, a ultima da cota global
    over = rig.ask(ip="203.0.113.3")
    assert over["statusCode"] == 429 and body_of(over)["status"] == "global"


def test_a_spent_monthly_budget_stops_before_the_model(executor, system):
    rig = Rig(executor, system, script=[])
    rig.ledger.add_usd("2026-11", 4.0)
    response = rig.ask()
    assert response["statusCode"] == 503 and body_of(response)["status"] == "budget"
    assert rig.llm.calls == []


def test_last_months_spend_does_not_block_this_month(executor, system):
    rig = Rig(executor, system)
    rig.ledger.add_usd("2026-10", 100.0)
    assert rig.ask()["statusCode"] == 200


def test_the_cost_of_each_question_is_added_to_the_ledger(executor, system):
    rig = Rig(executor, system)
    assert rig.ledger.spent_usd("2026-11") == 0.0
    rig.ask()
    assert rig.ledger.spent_usd("2026-11") > 0.0


# --------------------------------------------------------------------------- #
# Saida JSON
# --------------------------------------------------------------------------- #


def test_the_json_default_handles_database_types():
    assert handler._json_default(Decimal("1.50")) == 1.5
    assert handler._json_default(date(2026, 11, 15)) == "2026-11-15"
    assert handler._json_default(datetime(2026, 11, 15, 1, 2, 3)) == "2026-11-15T01:02:03"
    assert handler._json_default(object()).startswith("<object")


def test_only_an_answered_status_carries_a_query_result():
    api = Api.__new__(Api)
    api.config = make_config()
    done = QueryRecord(sql="SELECT 1", ok=True, columns=["a"], rows=[[1]])
    for status in ("failed", "budget", "out_of_scope", "llm_error"):
        assert "rows" not in api._body(Answer(status, "texto", [done]))
    assert api._body(Answer("answered", "texto", [done]))["rows"] == [[1]]


def test_localhost_with_a_port_is_an_allowed_origin():
    assert make_config(allowed_origins=("http://localhost:5173", "https://a.b.example")).allowed_origins


def test_body_of_an_answer_without_a_successful_query_has_no_result():
    api = Api.__new__(Api)
    api.config = make_config()
    answer = Answer("answered", "texto", [QueryRecord(sql="SELECT 1", ok=False, error="erro")])
    assert "rows" not in api._body(answer)


# --------------------------------------------------------------------------- #
# Montagem na AWS
# --------------------------------------------------------------------------- #


def test_lambda_handler_never_raises_when_the_setup_fails(monkeypatch):
    monkeypatch.setattr(handler, "_app", None)

    def boom(env=None):
        raise RuntimeError("falha de configuracao /caminho/secreto")

    monkeypatch.setattr(handler, "build_default", boom)
    response = lambda_handler(make_event())
    assert response["statusCode"] == 503 and "secreto" not in response["body"]


def test_lambda_handler_builds_once_and_reuses_the_app(monkeypatch):
    monkeypatch.setattr(handler, "_app", None)
    built: list[int] = []

    class Stub:
        def handle(self, event):
            return {"statusCode": 204, "headers": {}, "body": ""}

    def build(env=None):
        built.append(1)
        return Stub()

    monkeypatch.setattr(handler, "build_default", build)
    assert lambda_handler({})["statusCode"] == 204 and lambda_handler({})["statusCode"] == 204
    assert built == [1]


def test_build_default_wires_the_aws_pieces_without_calling_aws(gold_root, tmp_path, monkeypatch):
    columns = write_columns(gold_root, MONTH, tmp_path / "agent_columns.json")
    clients: dict[str, object] = {}

    def client(name):
        clients[name] = SimpleNamespace(name=name)
        return clients[name]

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=client))
    created: dict[str, Any] = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr("cnpj_pipeline.agent.llm.AnthropicClient", FakeAnthropic)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = {**ENV, "CNPJ_API_COLUMNS_FILE": str(columns), "CNPJ_API_MODEL": "modelo-x"}
    api = handler.build_default(env)

    assert set(clients) == {"dynamodb", "athena", "ssm"}
    assert api.executor.database == "cnpj" and api.executor.workgroup == "cnpj-pipeline"
    assert api.executor.timeout_s == 25.0
    assert MONTH in api.system and "gold_mart_simples_mei_uf" in api.system
    assert api.ledger.table == "cnpj-api-state" and api.limiter.counters.table == "cnpj-api-state"
    assert "ANTHROPIC_API_KEY" not in os.environ  # a chave so entra no ambiente depois de lida do parametro
    api.set_api_key("valor")
    assert os.environ["ANTHROPIC_API_KEY"] == "valor"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    api.llm_factory()
    assert created == {"model": "modelo-x", "timeout_s": 20.0, "max_retries": 1}


def test_the_handler_module_needs_neither_duckdb_nor_the_sdks_nor_boto3_to_import():
    src = Path(handler.__file__).resolve().parents[2]
    code = (
        "import sys\n"
        "for name in ('duckdb', 'anthropic', 'boto3', 'sqlglot_c'):\n"
        "    sys.modules[name] = None\n"
        "import cnpj_pipeline.api.handler\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(src)},
        check=False,
    )
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr
