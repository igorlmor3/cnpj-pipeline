"""Armazenamento da API: contadores, razao de gasto e parametros.

Cada um tem uma versao em memoria (usada nos testes) e uma da AWS (DynamoDB e SSM Parameter Store).
O cliente da AWS e recebido pronto, entao nada aqui importa boto3.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

MICRO = 1_000_000


class CounterStore(Protocol):
    def incr(self, key: str, ttl_seconds: int) -> int:
        """Soma 1 ao contador de forma atomica e devolve o valor novo. O contador expira em ttl_seconds."""
        ...


class ParamStore(Protocol):
    def get(self, name: str, *, decrypt: bool = False) -> str: ...


class InMemoryCounters:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._data: dict[str, tuple[int, float]] = {}

    def incr(self, key: str, ttl_seconds: int) -> int:
        now = self._clock()
        count, expires = self._data.get(key, (0, now + ttl_seconds))
        if expires <= now:
            count, expires = 0, now + ttl_seconds
        self._data[key] = (count + 1, expires)
        return count + 1


class InMemoryParams:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.reads: list[str] = []

    def get(self, name: str, *, decrypt: bool = False) -> str:
        self.reads.append(name)
        return self.values[name]


class DynamoCounters:
    """Contadores numa tabela com chave `pk` (texto) e TTL no atributo `expires_at`."""

    def __init__(self, client: Any, table: str, clock: Callable[[], float] = time.time) -> None:
        self.client = client
        self.table = table
        self._clock = clock

    def incr(self, key: str, ttl_seconds: int) -> int:
        response = self.client.update_item(
            TableName=self.table,
            Key={"pk": {"S": key}},
            UpdateExpression="ADD n :one SET expires_at = if_not_exists(expires_at, :exp)",
            ExpressionAttributeValues={
                ":one": {"N": "1"},
                ":exp": {"N": str(int(self._clock()) + ttl_seconds)},
            },
            ReturnValues="UPDATED_NEW",
        )
        return int(response["Attributes"]["n"]["N"])


class DynamoLedger:
    """Razao de gasto (SpendLedger) na mesma tabela, em microdolares inteiros somados de forma atomica."""

    def __init__(self, client: Any, table: str) -> None:
        self.client = client
        self.table = table

    @staticmethod
    def _key(period: str) -> dict[str, Any]:
        return {"pk": {"S": f"spend#{period}"}}

    def spent_usd(self, period: str) -> float:
        response = self.client.get_item(TableName=self.table, Key=self._key(period), ConsistentRead=True)
        item = response.get("Item")
        return int(item["micros"]["N"]) / MICRO if item else 0.0

    def add_usd(self, period: str, amount: float) -> None:
        if amount <= 0:
            return
        micros = max(1, round(amount * MICRO))
        self.client.update_item(
            TableName=self.table,
            Key=self._key(period),
            UpdateExpression="ADD micros :m",
            ExpressionAttributeValues={":m": {"N": str(micros)}},
        )


class SsmParams:
    def __init__(self, client: Any) -> None:
        self.client = client

    def get(self, name: str, *, decrypt: bool = False) -> str:
        response = self.client.get_parameter(Name=name, WithDecryption=decrypt)
        return str(response["Parameter"]["Value"])


class KillSwitch:
    """Interruptor lido de um parametro. So fica ligado se o valor for exatamente `true`; qualquer erro de
    leitura desliga (falha fechada). O valor e guardado por pouco tempo para nao ler a cada pedido."""

    OK_TTL_S = 30.0
    ERROR_TTL_S = 5.0

    def __init__(self, params: ParamStore, name: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.params = params
        self.name = name
        self._clock = clock
        self._value = False
        self._until = float("-inf")

    def enabled(self) -> bool:
        now = self._clock()
        if now < self._until:
            return self._value
        try:
            self._value = self.params.get(self.name).strip().lower() == "true"
            self._until = now + self.OK_TTL_S
        except Exception:
            self._value = False
            self._until = now + self.ERROR_TTL_S
        return self._value
