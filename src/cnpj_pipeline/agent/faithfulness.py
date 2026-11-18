"""Fidelidade da resposta: todo numero que o texto cita vem do resultado de uma consulta?

A avaliacao compara o resultado da consulta com a referencia, mas o que o visitante le e o texto. O
modelo pode consultar certo e escrever errado (trocar um digito, arredondar mal, somar de cabeca,
citar um numero que nao existe). Esta verificacao pega isso sem chamar nenhum modelo: extrai os
numeros do texto e procura cada um nas linhas que o proprio modelo viu.

Um numero do texto conta como apoiado se:
- bate com uma celula de uma consulta bem-sucedida (exato, ou arredondado/truncado para as casas
  mostradas; "12,3%" tambem bate com 0,123; "1,2 milhao" com 1.234.567);
- ou veio da pergunta, do SQL executado (ano, UF, LIMIT: o recorte que a resposta deve citar) ou e
  o numero de linhas de um resultado ("os 3 maiores").

Limites: so confere numeros, nao a frase em volta (um rotulo trocado passa se o numero existir) e
nao reconhece conta feita no texto (a soma de duas linhas aparece como sem apoio, que e o certo: o
prompt manda fazer contas no SQL). Em ingles, "1,234" pode ser mil duzentos e trinta e quatro ou
1,234; os dois significados sao aceitos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from cnpj_pipeline.agent.ask import MAX_TOOL_ROWS, Answer

# datas e meses em formato ISO ou brasileiro nao sao numeros de resultado
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}/\d{4}\b")
# marcador de lista no inicio da linha ("1. SP", "2) RJ")
_LIST_MARKER = re.compile(r"(?m)^\s*\d{1,2}[.)]\s")
_TOKEN = re.compile(r"(?<![\w.,])\d[\d.,]*\d(?![\w])|(?<![\w.,])\d(?![\w.,])")
_SCALES = (
    (re.compile(r"\s*(?:bilh[õo]es|bilh[ãa]o|billion)\b", re.I), Decimal(10**9)),
    (re.compile(r"\s*(?:milh[õo]es|milh[ãa]o|million)\b", re.I), Decimal(10**6)),
    (re.compile(r"\s*(?:mil|thousand)\b", re.I), Decimal(10**3)),
)


@dataclass(frozen=True)
class NumberMention:
    text: str  # como aparece na resposta
    values: tuple[Decimal, ...]  # leituras possiveis (ponto e virgula sao ambiguos)
    decimals: tuple[int, ...]  # casas decimais de cada leitura
    scale: Decimal  # 1, mil, milhao ou bilhao
    percent: bool


@dataclass(frozen=True)
class Faithfulness:
    checked: int  # numeros do texto que precisavam de apoio
    unsupported: list[str] = field(default_factory=list)

    @property
    def faithful(self) -> bool | None:
        """None quando nao havia numero a conferir (por exemplo, uma recusa)."""
        return None if self.checked == 0 else not self.unsupported


def _readings(token: str) -> list[tuple[Decimal, int]]:
    """Valores possiveis de um token como '1.234.567', '12,5', '1,234.5' ou '1.234'."""
    has_dot, has_comma = "." in token, "," in token
    out: list[tuple[str, int]] = []
    if has_dot and has_comma:
        decimal_sep = "." if token.rfind(".") > token.rfind(",") else ","
        thousands = "," if decimal_sep == "." else "."
        whole, _, frac = token.rpartition(decimal_sep)
        if re.fullmatch(rf"\d{{1,3}}(?:\{thousands}\d{{3}})+", whole) and frac:
            out.append((whole.replace(thousands, "") + "." + frac, len(frac)))
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = token.split(sep)
        as_thousands = re.fullmatch(rf"\d{{1,3}}(?:\{sep}\d{{3}})+", token) is not None
        if as_thousands:
            out.append(("".join(parts), 0))
        if len(parts) == 2 and parts[1]:
            out.append((parts[0] + "." + parts[1], len(parts[1])))
    else:
        out.append((token, 0))
    readings = []
    for text, decimals in out:
        try:
            readings.append((Decimal(text), decimals))
        except InvalidOperation:
            continue
    return readings


def extract_numbers(text: str) -> list[NumberMention]:
    """Numeros citados no texto, sem datas, meses ISO e marcadores de lista."""
    cleaned = _LIST_MARKER.sub("", _DATE.sub(" ", text))
    mentions: list[NumberMention] = []
    for match in _TOKEN.finditer(cleaned):
        readings = _readings(match.group())
        if not readings:
            continue
        after = cleaned[match.end() :]
        scale = Decimal(1)
        for pattern, factor in _SCALES:
            if pattern.match(after):
                scale = factor
                break
        percent = scale == 1 and after.lstrip().startswith("%")
        mentions.append(
            NumberMention(
                text=match.group().strip(),
                values=tuple(v for v, _ in readings),
                decimals=tuple(d for _, d in readings),
                scale=scale,
                percent=percent,
            )
        )
    return mentions


def _to_decimal(cell: Any) -> Decimal | None:
    if cell is None or isinstance(cell, bool):
        return None
    try:
        number = Decimal(str(cell).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _cell_numbers(cell: Any) -> list[Decimal]:
    """Numeros de uma celula: ela mesma, se numerica, ou os numeros dentro de um texto."""
    number = _to_decimal(cell)
    if number is not None:
        return [abs(number)]
    if isinstance(cell, str):
        return [abs(v) for m in extract_numbers(cell) for v in m.values]
    return []


def _matches(mention: NumberMention, available: Iterable[Decimal]) -> bool:
    pool = list(available)
    for value, decimals in zip(mention.values, mention.decimals, strict=True):
        digits = Decimal(1).scaleb(-decimals)
        for p in pool:
            for candidate in (p, p * 100) if mention.percent else (p,):
                target = candidate / mention.scale if mention.scale != 1 else candidate
                for mode in (ROUND_HALF_UP, ROUND_DOWN):
                    if target.quantize(digits, rounding=mode) == value:
                        return True
    return False


def check_text(
    text: str,
    *,
    question: str,
    rows: Iterable[Iterable[Iterable[Any]]],
    sqls: Iterable[str] = (),
) -> Faithfulness:
    """`rows` traz o resultado de cada consulta bem-sucedida (lista de linhas); `sqls`, os SQL
    executados. Cada numero do texto precisa estar nas linhas, na pergunta, no SQL ou ser a
    contagem de linhas de um resultado."""
    available: set[Decimal] = set()
    for result in rows:
        listed = [list(r) for r in result]
        available.add(Decimal(len(listed)))
        for row in listed:
            for cell in row:
                available.update(_cell_numbers(cell))
    for source in (question, *sqls):
        available.update(abs(v) for m in extract_numbers(source) for v in m.values)
        # um ano ou mes dentro de uma data ISO tambem e o recorte da consulta
        for iso in re.findall(r"\b(\d{4})-(\d{2})\b", source):
            available.update(Decimal(part) for part in iso)

    mentions = extract_numbers(text)
    unsupported = [m.text for m in mentions if not _matches(m, available)]
    return Faithfulness(checked=len(mentions), unsupported=unsupported)


def check_answer(question: str, answer: Answer) -> Faithfulness:
    """Confere o texto de uma resposta com o que o modelo viu: as primeiras linhas de cada consulta."""
    ok = [q for q in answer.queries if q.ok]
    return check_text(
        answer.text,
        question=question,
        rows=[q.rows[:MAX_TOOL_ROWS] for q in ok],
        sqls=[q.sql for q in ok],
    )
