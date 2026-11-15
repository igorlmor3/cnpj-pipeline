"""Controle de gasto do agente: preco por token, razao de gastos e o teto mensal.

Este controle e uma segunda barreira. A barreira que vale e o limite de gasto mensal configurado
na propria conta do provedor do modelo; aqui o programa para de chamar o modelo antes de chegar la.
O teto e o preco sao parametros, sem valor fixo no codigo de quem chama.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

TOKENS_PER_UNIT = 1_000_000


@dataclass(frozen=True)
class Pricing:
    """Preco em dolares por milhao de tokens."""

    input_per_mtok: float
    output_per_mtok: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / TOKENS_PER_UNIT


# Tabela de precos publicada para o Claude Haiku 5.5 (prompts de ate 100 mil tokens), em out/2026.
# Confira na pagina de precos do provedor antes de confiar neste numero.
HAIKU_5_5 = Pricing(input_per_mtok=0.10, output_per_mtok=0.50)


class BudgetExceeded(RuntimeError):
    """O teto de gasto do periodo foi atingido. A mensagem e segura para exibir."""


class SpendLedger(Protocol):
    """Onde o gasto acumulado do periodo fica guardado (na nuvem, uma tabela; nos testes, memoria)."""

    def spent_usd(self, period: str) -> float: ...

    def add_usd(self, period: str, amount: float) -> None: ...


class InMemoryLedger:
    def __init__(self) -> None:
        self._spent: dict[str, float] = {}

    def spent_usd(self, period: str) -> float:
        return self._spent.get(period, 0.0)

    def add_usd(self, period: str, amount: float) -> None:
        self._spent[period] = self._spent.get(period, 0.0) + amount


class Budget:
    """Teto de gasto por periodo (por exemplo, o mes AAAA-MM). Soma o custo de cada chamada ao modelo."""

    def __init__(
        self,
        *,
        limit_usd: float,
        period: str,
        pricing: Pricing = HAIKU_5_5,
        ledger: SpendLedger | None = None,
    ) -> None:
        if limit_usd <= 0:
            raise ValueError("o teto de gasto precisa ser maior que zero")
        self.limit_usd = limit_usd
        self.period = period
        self.pricing = pricing
        self.ledger: SpendLedger = ledger if ledger is not None else InMemoryLedger()

    @property
    def spent_usd(self) -> float:
        return self.ledger.spent_usd(self.period)

    def check(self) -> None:
        """Chamada antes de cada pedido ao modelo."""
        if self.spent_usd >= self.limit_usd:
            raise BudgetExceeded("o limite de gasto do agente neste mes foi atingido")

    def record(self, input_tokens: int, output_tokens: int) -> float:
        """Soma o custo de uma chamada ja feita e devolve esse custo."""
        cost = self.pricing.cost(input_tokens, output_tokens)
        self.ledger.add_usd(self.period, cost)
        return cost
