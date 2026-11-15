"""Limites por visitante e global, contados em janelas fixas (minuto e dia UTC).

O visitante e identificado pelo IP de origem, guardado so como HMAC com um sal secreto e cortado em 16
caracteres hexadecimais; o IP em si nunca vai para o armazenamento. IPv6 e agrupado por /64, porque cada
casa costuma receber um /64 inteiro e o visitante poderia trocar de endereco dentro dele a cada pedido.
O limite global so conta pedidos que passaram nos limites do visitante, para um visitante barrado nao
gastar a cota de todos.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
from collections.abc import Callable
from dataclasses import dataclass

from cnpj_pipeline.api.stores import CounterStore

DAY_S = 86_400
MINUTE_S = 60
UNKNOWN = "desconhecido"


def visitor_key(ip: str | None, salt: str) -> str:
    try:
        address = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        normalized = UNKNOWN  # todos os pedidos sem IP valido dividem um unico balde, de proposito
    else:
        if address.version == 6:
            normalized = str(ipaddress.ip_network(f"{address}/64", strict=False))
        else:
            normalized = str(address)
    return hmac.new(salt.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str | None = None  # "minute", "day" ou "global"
    retry_after_s: int = 0


class Limiter:
    def __init__(
        self,
        counters: CounterStore,
        *,
        per_minute: int,
        per_day: int,
        global_per_day: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.counters = counters
        self.per_minute = per_minute
        self.per_day = per_day
        self.global_per_day = global_per_day
        self._clock = clock

    def check(self, visitor: str) -> Verdict:
        now = int(self._clock())
        minute, day = now // MINUTE_S, now // DAY_S
        to_minute_end = (minute + 1) * MINUTE_S - now
        to_day_end = (day + 1) * DAY_S - now

        if self.counters.incr(f"v#{visitor}#m#{minute}", 2 * MINUTE_S) > self.per_minute:
            return Verdict(False, "minute", to_minute_end)
        if self.counters.incr(f"v#{visitor}#d#{day}", 2 * DAY_S) > self.per_day:
            return Verdict(False, "day", to_day_end)
        if self.counters.incr(f"g#d#{day}", 2 * DAY_S) > self.global_per_day:
            return Verdict(False, "global", to_day_end)
        return Verdict(True)
