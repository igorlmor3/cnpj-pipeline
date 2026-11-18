"""Funcao da API (AWS Lambda com URL de funcao): recebe a pergunta, aplica os limites e chama o agente.

Ordem de cada pedido, e cada passo falha fechado:
  1. metodo, caminho, origem, tipo de conteudo, tamanho e JSON do corpo;
  2. interruptor (parametro `enabled`);
  3. chave do modelo e sal (parametros cifrados, lidos uma vez por instancia);
  4. limites por visitante (minuto e dia) e global (dia);
  5. agente, com teto de gasto mensal na razao e prazo maximo por pergunta.

Nada do texto da pergunta, da chave, do IP ou do SQL vai para o log: so o estado, o numero de chamadas,
o custo e um prefixo do hash do visitante. A resposta so leva o resultado da ultima consulta (agregados,
limitado a MAX_ROWS_RETURNED linhas) e o texto do agente.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from cnpj_pipeline.agent.ask import Answer, ask, build_system_prompt
from cnpj_pipeline.agent.budget import Budget, SpendLedger
from cnpj_pipeline.agent.executor import AthenaExecutor, Executor
from cnpj_pipeline.agent.llm import LLMClient
from cnpj_pipeline.agent.retrieval import PUBLIC, Retriever
from cnpj_pipeline.agent.trace import build_trace
from cnpj_pipeline.api.columns import load_columns
from cnpj_pipeline.api.config import ApiConfig
from cnpj_pipeline.api.limits import Limiter, visitor_key
from cnpj_pipeline.api.stores import (
    CounterStore,
    DynamoCounters,
    DynamoLedger,
    KillSwitch,
    ParamStore,
    SsmParams,
)

log = logging.getLogger("cnpj_pipeline.api")

MAX_BODY_BYTES = 2048
MAX_ROWS_RETURNED = 40
ASK_PATHS = ("/", "/ask")
TRACE_STATUSES = ("answered", "out_of_scope", "failed")  # os demais nao chegam a ter trajeto

MESSAGES = {
    "disabled": "O agente está desligado no momento. As perguntas de exemplo continuam disponíveis.",
    "unavailable": "O agente não está disponível agora. As perguntas de exemplo continuam disponíveis.",
    "minute": "Muitas perguntas em pouco tempo. Tente de novo em instantes.",
    "day": "Você atingiu o limite diário de perguntas. As perguntas de exemplo continuam disponíveis.",
    "global": (
        "O limite diário de perguntas do site foi atingido. As perguntas de exemplo continuam disponíveis."
    ),
}

# Status do agente para o codigo HTTP.
HTTP_BY_STATUS = {
    "answered": 200,
    "out_of_scope": 200,
    "failed": 200,
    "invalid_question": 400,
    "budget": 503,
    "llm_error": 502,
    "internal_error": 500,
}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


class Api:
    def __init__(
        self,
        config: ApiConfig,
        *,
        params: ParamStore,
        counters: CounterStore,
        ledger: SpendLedger,
        executor: Executor,
        system: str,
        llm_factory: Callable[[], LLMClient],
        set_api_key: Callable[[str], None],
        retriever: Retriever | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.params = params
        self.ledger = ledger
        self.executor = executor
        self.system = system
        self.llm_factory = llm_factory
        self.set_api_key = set_api_key
        self.retriever = retriever
        self.clock = clock
        self.switch = KillSwitch(params, config.param_enabled, monotonic)
        self.limiter = Limiter(
            counters,
            per_minute=config.per_minute,
            per_day=config.per_day,
            global_per_day=config.global_per_day,
            clock=clock,
        )
        self._salt: str | None = None
        self._llm: LLMClient | None = None

    # -- respostas ---------------------------------------------------------------------------

    @staticmethod
    def _response(
        code: int, body: dict[str, Any], headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        merged = {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"}
        merged.update(headers or {})
        return {
            "statusCode": code,
            "headers": merged,
            "body": json.dumps(body, ensure_ascii=False, default=_json_default),
        }

    def _refuse(
        self,
        code: int,
        status: str,
        message: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        body = {"status": status, "message": message or MESSAGES.get(status, "")}
        return self._response(code, body, headers)

    # -- preparo (uma vez por instancia) -----------------------------------------------------

    def _prepare(self) -> bool:
        """Le o sal e a chave do modelo. Devolve False se algo faltar (falha fechada)."""
        try:
            if self._salt is None:
                self._salt = self.params.get(self.config.param_salt, decrypt=True)
                if not self._salt:
                    self._salt = None
                    return False
            if self._llm is None:
                key = self.params.get(self.config.param_api_key, decrypt=True)
                if not key:
                    return False
                self.set_api_key(key)
                self._llm = self.llm_factory()
        except Exception:
            log.warning("parametros da API indisponiveis")
            return False
        return True

    # -- entrada -----------------------------------------------------------------------------

    def _parse(self, event: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Devolve (corpo JSON, None) ou (None, resposta de erro)."""
        headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
        if not headers.get("content-type", "").lower().startswith("application/json"):
            return None, self._refuse(415, "bad_request", "Envie o corpo como application/json.")
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            try:
                raw = base64.b64decode(raw, validate=True).decode("utf-8")
            except (binascii.Error, ValueError):
                return None, self._refuse(400, "bad_request", "Corpo inválido.")
        if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
            return None, self._refuse(413, "bad_request", "Pergunta grande demais.")
        try:
            body = json.loads(raw)
        except ValueError:
            return None, self._refuse(400, "bad_request", "O corpo precisa ser um JSON.")
        if not isinstance(body, dict) or not isinstance(body.get("question"), str):
            return None, self._refuse(
                400, "bad_request", 'Envie {"question": "..."} com a pergunta em texto.'
            )
        return body, None

    # -- pedido ------------------------------------------------------------------------------

    def handle(self, event: Mapping[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        http = (event.get("requestContext") or {}).get("http") or {}
        method, path = str(http.get("method", "")).upper(), str(http.get("path", "/"))
        headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}

        origin = headers.get("origin")
        if origin is not None and origin not in self.config.allowed_origins:
            return self._refuse(403, "forbidden", "Origem não permitida.")

        if method == "GET" and path == "/health":
            return self._response(200, {"status": "ok", "enabled": self.switch.enabled()})
        if method != "POST" or path not in ASK_PATHS:
            return self._refuse(404, "not_found", "Não encontrado.")

        body, error = self._parse(event)
        if error is not None or body is None:
            return error or self._refuse(400, "bad_request")

        if not self.switch.enabled():
            return self._refuse(503, "disabled")
        if not self._prepare() or self._llm is None or self._salt is None:
            return self._refuse(503, "unavailable")

        visitor = visitor_key(http.get("sourceIp"), self._salt)
        try:
            verdict = self.limiter.check(visitor)
        except Exception:
            log.warning("contadores indisponiveis")
            return self._refuse(503, "unavailable")
        if not verdict.allowed:
            self._log("limited", 429, visitor, None, started, reason=verdict.reason)
            return self._refuse(
                429, verdict.reason or "minute", headers={"retry-after": str(max(1, verdict.retry_after_s))}
            )

        period = time.strftime("%Y-%m", time.gmtime(self.clock()))
        budget = Budget(limit_usd=self.config.monthly_budget_usd, period=period, ledger=self.ledger)
        answer = ask(
            body["question"],
            month=self.config.month,
            months=self.config.available_months,
            system=self.system,
            llm=self._llm,
            executor=self.executor,
            budget=budget,
            deadline_s=self.config.deadline_s,
            retriever=self.retriever,
            audiences=frozenset({PUBLIC}),  # o visitante so ve a documentacao publica
        )
        code = HTTP_BY_STATUS.get(answer.status, 500)
        self._log(answer.status, code, visitor, answer, started)
        return self._response(code, self._body(answer, body["question"]))

    # -- saida -------------------------------------------------------------------------------

    def _body(self, answer: Answer, question: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {
            "status": answer.status,
            "message": answer.text,
            "month": self.config.month,
            "months": list(self.config.available_months),
        }
        last_ok = next((q for q in reversed(answer.queries) if q.ok), None)
        if answer.status == "answered" and last_ok is not None:
            body["sql"] = last_ok.sql
            body["columns"] = last_ok.columns
            body["rows"] = last_ok.rows[:MAX_ROWS_RETURNED]
            body["truncated"] = last_ok.may_be_truncated or len(last_ok.rows) > MAX_ROWS_RETURNED
        sources = list(dict.fromkeys(s for d in answer.docs for s in d.sources))
        if answer.status == "answered" and sources:
            body["sources"] = sources
        if answer.status in TRACE_STATUSES:
            body["trace"] = build_trace(question, answer)
        return body

    def _log(
        self, status: str, code: int, visitor: str, answer: Answer | None, started: float, **extra: Any
    ) -> None:
        record = {
            "event": "ask",
            "status": status,
            "http": code,
            "visitor": visitor[:8],
            "ms": round((time.monotonic() - started) * 1000),
            "llm_calls": answer.llm_calls if answer else 0,
            "queries": len(answer.queries) if answer else 0,
            "doc_searches": len(answer.docs) if answer else 0,
            "cost_usd": round(answer.cost_usd, 6) if answer else 0.0,
            **extra,
        }
        log.info(json.dumps(record))


# --------------------------------------------------------------------------- #
# Montagem na AWS (so roda dentro da funcao)
# --------------------------------------------------------------------------- #

_app: Api | None = None


def build_default(env: Mapping[str, str] | None = None) -> Api:
    import boto3  # fornecido pelo ambiente da funcao

    from cnpj_pipeline.agent.llm import AnthropicClient, tools_for
    from cnpj_pipeline.agent.retrieval import load_index, public_only

    config = ApiConfig.from_env(env if env is not None else os.environ)
    columns = load_columns(config.columns_file, config.month)
    retriever = public_only(load_index(config.docs_index_file)) if config.docs_index_file.is_file() else None
    system = build_system_prompt(
        columns, month=config.month, months=config.available_months, docs=retriever is not None
    )
    dynamo = boto3.client("dynamodb")
    executor = AthenaExecutor(
        boto3.client("athena"),
        database=config.database,
        workgroup=config.workgroup,
        timeout_s=config.athena_timeout_s,
    )

    def set_api_key(key: str) -> None:
        os.environ["ANTHROPIC_API_KEY"] = (
            key  # so no ambiente desta instancia; nunca vai para log nem resposta
        )

    return Api(
        config,
        params=SsmParams(boto3.client("ssm")),
        counters=DynamoCounters(dynamo, config.table),
        ledger=DynamoLedger(dynamo, config.table),
        executor=executor,
        system=system,
        llm_factory=lambda: AnthropicClient(
            model=config.model,
            timeout_s=config.llm_timeout_s,
            max_retries=1,
            tools=tools_for(docs=retriever is not None),
        ),
        set_api_key=set_api_key,
        retriever=retriever,
    )


def lambda_handler(event: Mapping[str, Any], context: Any = None) -> dict[str, Any]:
    global _app
    if _app is None:
        try:
            _app = build_default()
        except Exception:
            log.exception("a funcao nao conseguiu iniciar")
            return Api._response(503, {"status": "unavailable", "message": MESSAGES["unavailable"]})
    return _app.handle(event)
