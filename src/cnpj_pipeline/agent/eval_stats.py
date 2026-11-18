"""Estatistica da avaliacao: intervalos de confianca e comparacao pareada. So biblioteca padrao.

Com 25 a 100 perguntas, a acuracia medida tem uma margem larga, e dizer "88%" sem ela passa mais
certeza do que os dados dao. Este modulo calcula essa margem e testa se a diferenca entre duas
execucoes (dois prompts, dois modelos) e maior do que o acaso explicaria.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from math import comb, sqrt

Z_95 = 1.959964  # quantil da normal para 95% de confianca
BOOTSTRAP_RESAMPLES = 4000
BOOTSTRAP_SEED = 20260101  # fixo: o mesmo resultado sempre sai com o mesmo intervalo


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Intervalo de Wilson para uma proporcao. Melhor que o intervalo normal com poucas perguntas
    e perto de 100%, onde o normal passaria de 1."""
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= successes <= n:
        raise ValueError("successes deve ficar entre 0 e n")
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_interval(
    rates: Sequence[float],
    *,
    level: float = 0.95,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Intervalo por reamostragem das PERGUNTAS (cada uma com a sua taxa de acerto nas repeticoes).

    Repetir a mesma pergunta mede a estabilidade do modelo, nao a cobertura de tipos de pergunta;
    por isso a unidade e a pergunta, e 5 repeticoes de 25 perguntas nao valem 125 observacoes
    independentes."""
    if not rates:
        return (0.0, 1.0)
    rng = random.Random(seed)
    n = len(rates)
    means = sorted(statistics.fmean(rng.choices(rates, k=n)) for _ in range(resamples))
    lo = means[int((1 - level) / 2 * (resamples - 1))]
    hi = means[int((1 + level) / 2 * (resamples - 1))]
    return (lo, hi)


def exact_mcnemar_p(only_a: int, only_b: int) -> float:
    """p-valor bilateral exato do teste de McNemar: so contam as perguntas em que uma execucao
    acertou e a outra errou (os discordantes). Sem discordantes nao ha diferenca a testar (p = 1)."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_difference_interval(
    rates_a: Sequence[float],
    rates_b: Sequence[float],
    *,
    level: float = 0.95,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Intervalo da diferenca media (b - a) das taxas por pergunta, reamostrando perguntas."""
    if len(rates_a) != len(rates_b):
        raise ValueError("as duas execucoes precisam ter as mesmas perguntas")
    diffs = [b - a for a, b in zip(rates_a, rates_b, strict=True)]
    return bootstrap_interval(diffs, level=level, resamples=resamples, seed=seed) if diffs else (0.0, 0.0)
