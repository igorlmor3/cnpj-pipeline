"""Recuperacao de trechos da documentacao do repositorio, com BM25 e controle de acesso na busca.

O agente responde sobre os DADOS com SQL. Esta busca responde sobre o PROJETO (como os dados foram
tratados, o que cada regra decidiu, limites e ressalvas) com trechos dos documentos e a fonte de
cada um. Python puro, sem servico externo: o indice e um JSON que cabe na funcao da API.

Controle de acesso no momento da recuperacao (o mesmo desenho de um indice com filtro por
permissao, aqui sem servico):
- O que entra no indice e decidido por uma lista de regras (`DEFAULT_RULES`). Arquivo que nenhuma
  regra cobre nao e lido: a negacao e o padrao.
- Cada trecho carrega o publico que pode le-lo (`public` ou `internal`), herdado da regra.
- Toda busca informa os publicos permitidos e o filtro roda ANTES da pontuacao: as estatisticas do
  BM25 (frequencia de termos, tamanho medio) sao calculadas so sobre os trechos visiveis, entao nem
  a posicao nem a pontuacao de um resultado dependem de um trecho que o visitante nao pode ver.

    python -m cnpj_pipeline.agent.retrieval build --root . --out build/docs_index.json
    python -m cnpj_pipeline.agent.retrieval search "por que DuckDB?" --index build/docs_index.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

PUBLIC = "public"
INTERNAL = "internal"
AUDIENCES = frozenset({PUBLIC, INTERNAL})
INDEX_VERSION = 1

MAX_CHUNK_CHARS = 1400
TABLE_CHUNK_CHARS = 650  # tabelas se dividem em grupos de linhas menores, com o cabecalho repetido
K1 = 1.2
B = 0.75
HEADING_WEIGHT = 2  # os termos do titulo da secao contam como se aparecessem duas vezes
MAX_HIT_CHARS = 1200  # por trecho, no texto devolvido ao modelo
MAX_HITS_CHARS = 5000  # no total
IGNORED_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "data", "build", "dist", "__pycache__"})


# --------------------------------------------------------------------------- #
# O que entra no indice
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DocRule:
    pattern: str  # glob relativo a raiz, com "/": "*" nao atravessa pastas
    audience: str
    latest_only: bool = False  # entre os arquivos de uma mesma pasta, so o ultimo em ordem alfabetica


# A primeira regra que casa decide. O que nao casa com nenhuma nao e indexado: dados, relatorios de
# avaliacao, codigo e testes ficam de fora. `internal` e o que o desenvolvedor consulta localmente e
# o agente publico nao devolve: operacao da nuvem e da orquestracao, e o profiling bruto.
DEFAULT_RULES: tuple[DocRule, ...] = (
    DocRule("README.md", PUBLIC),
    DocRule("docs/architecture.md", PUBLIC),
    DocRule("docs/data_quality_rules.md", PUBLIC),
    DocRule("docs/agent_design.md", PUBLIC),
    DocRule("docs/site_data.md", PUBLIC),
    DocRule("docs/analysis/*.md", PUBLIC),
    DocRule("docs/quality/*.md", PUBLIC, latest_only=True),
    DocRule("docs/silver/*.md", PUBLIC, latest_only=True),
    DocRule("docs/gold/*.md", PUBLIC, latest_only=True),
    DocRule("docs/evolution/*.md", PUBLIC, latest_only=True),
    DocRule("site/README.md", PUBLIC),
    DocRule("infra/README.md", INTERNAL),
    DocRule("orchestration/README.md", INTERNAL),
    DocRule("docs/profiling/*/*.md", INTERNAL),
)


def _glob_regex(pattern: str) -> re.Pattern[str]:
    parts = [
        ".*" if piece == "**" else re.escape(piece).replace(r"\*", "[^/]*") for piece in pattern.split("/")
    ]
    return re.compile("^" + "/".join(parts) + "$")


@dataclass(frozen=True)
class Source:
    path: str  # relativo a raiz, com "/"
    audience: str
    sha256: str


def discover_sources(root: Path, rules: Sequence[DocRule] = DEFAULT_RULES) -> list[Source]:
    """Arquivos Markdown que as regras permitem indexar, em ordem de caminho. Links simbolicos e
    arquivos fora da raiz sao ignorados."""
    root = root.resolve()
    candidates: list[str] = []
    for path in root.rglob("*.md"):
        rel = path.relative_to(root)
        if path.is_symlink() or any(part in IGNORED_DIRS for part in rel.parts[:-1]):
            continue
        candidates.append(rel.as_posix())

    chosen: dict[str, DocRule] = {}
    for rel in sorted(candidates):
        for rule in rules:
            if _glob_regex(rule.pattern).match(rel):
                chosen[rel] = rule
                break
    for rule in rules:
        if not rule.latest_only:
            continue
        by_dir: dict[str, list[str]] = defaultdict(list)
        for rel, owner in chosen.items():
            if owner is rule:
                by_dir[rel.rpartition("/")[0]].append(rel)
        for files in by_dir.values():
            for stale in sorted(files)[:-1]:
                del chosen[stale]

    sources = []
    for rel in sorted(chosen):
        data = (root / rel).read_bytes()
        sources.append(Source(rel, chosen[rel].audience, hashlib.sha256(data).hexdigest()))
    return sources


# --------------------------------------------------------------------------- #
# Trechos
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Chunk:
    id: str  # "<caminho>#<n>", estavel entre execucoes
    path: str
    heading: str  # "Titulo > Secao > Subsecao"
    text: str
    audience: str
    line: int  # linha do arquivo onde o trecho comeca

    @property
    def citation(self) -> str:
        return f"{self.path}, seção {self.heading}" if self.heading else self.path


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _split_long(text: str, limit: int) -> list[str]:
    """Corta um bloco maior que o limite em linhas e, se uma linha sozinha passar, em espacos."""
    pieces: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:cut])
            line = line[cut:].lstrip()
        if current and len(current) + 1 + len(line) > limit:
            pieces.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        pieces.append(current)
    return pieces


def _is_table(paragraph: str) -> bool:
    lines = paragraph.split("\n")
    return len(lines) >= 3 and all(line.lstrip().startswith("|") for line in lines)


def _split_table(paragraph: str, start_line: int, limit: int) -> list[tuple[int, str]]:
    """Grupos de linhas de uma tabela; cada grupo repete o cabecalho para que os nomes das colunas
    entrem na busca. Uma linha maior que o limite fica inteira no seu grupo."""
    lines = paragraph.split("\n")
    header, rows = lines[:2], lines[2:]
    groups: list[tuple[int, str]] = []
    current: list[str] = []
    current_start = 0
    for offset, row in enumerate(rows):
        size = sum(len(r) + 1 for r in header + current) + len(row)
        if current and size > limit:
            groups.append((current_start, "\n".join(header + current)))
            current = []
        if not current:
            current_start = start_line + 2 + offset
        current.append(row)
    if current:
        groups.append((current_start, "\n".join(header + current)))
    return groups


def _pack(paragraphs: list[tuple[int, str]], limit: int) -> list[tuple[int, str]]:
    """Junta paragrafos consecutivos ate o limite; devolve (linha inicial, texto). Tabelas ficam
    fora da juncao e viram grupos de linhas."""
    out: list[tuple[int, str]] = []
    current_line, current = 0, ""
    for line_no, paragraph in paragraphs:
        if _is_table(paragraph):
            if current:
                out.append((current_line, current))
                current = ""
            out.extend(_split_table(paragraph, line_no, min(limit, TABLE_CHUNK_CHARS)))
            continue
        for piece in _split_long(paragraph, limit) if len(paragraph) > limit else [paragraph]:
            if current and len(current) + 2 + len(piece) > limit:
                out.append((current_line, current))
                current = ""
            if not current:
                current_line = line_no
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        out.append((current_line, current))
    return out


def chunk_markdown(text: str, *, path: str, audience: str, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """Divide um Markdown por secao (titulos #) e, se a secao for longa, por paragrafo. Titulos dentro
    de blocos de codigo nao contam. Cada trecho guarda o caminho de titulos de onde veio."""
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str, int, list[tuple[int, str]]]] = []  # (titulo, linha, paragrafos)
    heading_path = ""
    paragraphs: list[tuple[int, str]] = []
    buffer: list[str] = []
    buffer_line = 0
    in_fence = False
    section_line = 1

    def flush_paragraph() -> None:
        nonlocal buffer, buffer_line
        if buffer:
            paragraphs.append((buffer_line, "\n".join(buffer)))
        buffer = []

    def flush_section() -> None:
        flush_paragraph()
        if paragraphs:
            sections.append((heading_path, section_line, list(paragraphs)))
        paragraphs.clear()

    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            flush_section()
            level, title = len(match.group(1)), match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            heading_path = " > ".join(t for _, t in stack)
            section_line = number
            continue
        if not line.strip() and not in_fence:
            flush_paragraph()
            continue
        if not buffer:
            buffer_line = number
        buffer.append(line)
    flush_section()

    chunks: list[Chunk] = []
    for heading, _, paras in sections:
        for line_no, body in _pack(paras, max_chars):
            chunks.append(Chunk(f"{path}#{len(chunks) + 1}", path, heading, body, audience, line_no))
    return chunks


def load_corpus(root: Path, rules: Sequence[DocRule] = DEFAULT_RULES) -> tuple[list[Chunk], list[Source]]:
    sources = discover_sources(root, rules)
    chunks: list[Chunk] = []
    for source in sources:
        text = (root / source.path).read_text(encoding="utf-8")
        chunks.extend(chunk_markdown(text, path=source.path, audience=source.audience))
    return chunks, sources


# --------------------------------------------------------------------------- #
# Texto para termos
# --------------------------------------------------------------------------- #

# Sem acento e em minusculas, como os termos. "uf", "mei" e "cnae" nao entram: sao conteudo.
_STOPWORDS = frozenset(
    "a o as os um uma uns umas de do da dos das em no na nos nas por para com sem sobre entre e ou "
    "que se como qual quais quando onde quem ao aos pelo pela pelos pelas ele ela eles elas isso "
    "isto esse essa esses essas este esta estes estas seu sua seus suas ser sao foi era eh ha tem "
    "tinha mais menos muito muita ja nao nem so tambem entao porque pois mas ate sao fazer faz "
    "feito pode podem deve devem vez qual".split()
)
_BASE_TOKEN = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def fold(text: str) -> str:
    """Minusculas e sem acentos: a base de toda comparacao de texto deste modulo."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def stem(token: str) -> str:
    """Remove so o plural: 'regras' e 'regra', 'informacoes' e 'informacao'. Leve de proposito: um
    stemmer agressivo junta palavras diferentes e nao ajuda neste vocabulario tecnico."""
    if len(token) <= 3 or any(c.isdigit() for c in token) or "_" in token:
        return token
    for suffix, replacement in (("coes", "cao"), ("oes", "ao"), ("aes", "ao"), ("ais", "al"), ("eis", "el")):
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            return token[: -len(suffix)] + replacement
    if token.endswith("ns"):
        return token[:-2] + "m"
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Termos de um texto. Um identificador como `mart_simples_mei_uf` gera o termo inteiro e as suas
    partes: a busca por 'simples mei' o encontra, e a busca pelo nome exato o encontra melhor ainda."""
    terms: list[str] = []
    for base in _BASE_TOKEN.findall(fold(text)):
        parts = base.split("_")
        candidates = [base, *parts] if len(parts) > 1 else [base]
        for term in candidates:
            if drop_stopwords and term in _STOPWORDS:
                continue
            terms.append(stem(term))
    return terms


# --------------------------------------------------------------------------- #
# BM25
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    rank: int  # 1 e o melhor


class Retriever(Protocol):
    """Qualquer busca de trechos: o agente depende disto, nao do BM25."""

    def search(self, query: str, *, audiences: Iterable[str], k: int = 4) -> list[Hit]: ...


class _View:
    """Estatisticas do BM25 sobre os trechos de um conjunto de publicos, e so deles."""

    def __init__(self, indices: list[int], tokens: list[Counter[str]], lengths: list[int]) -> None:
        self.indices = indices
        self.lengths = [lengths[i] for i in indices]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)  # termo -> (pos, tf)
        for pos, i in enumerate(indices):
            for term, tf in tokens[i].items():
                self.postings[term].append((pos, tf))


class BM25Index:
    def __init__(self, chunks: Iterable[Chunk]) -> None:
        self.chunks: tuple[Chunk, ...] = tuple(chunks)
        for chunk in self.chunks:
            if chunk.audience not in AUDIENCES:
                raise ValueError(f"publico desconhecido em {chunk.id}: {chunk.audience!r}")
        self._tokens: list[Counter[str]] = []
        self._lengths: list[int] = []
        for chunk in self.chunks:
            terms = tokenize(chunk.heading) * HEADING_WEIGHT + tokenize(chunk.text)
            self._tokens.append(Counter(terms))
            self._lengths.append(len(terms))
        self._views: dict[frozenset[str], _View] = {}

    def _view(self, audiences: frozenset[str]) -> _View:
        view = self._views.get(audiences)
        if view is None:
            indices = [i for i, c in enumerate(self.chunks) if c.audience in audiences]
            view = self._views[audiences] = _View(indices, self._tokens, self._lengths)
        return view

    def search(self, query: str, *, audiences: Iterable[str], k: int = 4) -> list[Hit]:
        allowed = frozenset(audiences) & AUDIENCES
        view = self._view(allowed)
        n = len(view.indices)
        if n == 0 or k <= 0:
            return []
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            postings = view.postings.get(term)
            if not postings:
                continue
            idf = math.log(1 + (n - len(postings) + 0.5) / (len(postings) + 0.5))
            for pos, tf in postings:
                norm = K1 * (1 - B + B * view.lengths[pos] / view.avg_len)
                scores[pos] += idf * tf * (K1 + 1) / (tf + norm)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:k]
        return [
            Hit(self.chunks[view.indices[pos]], score, rank) for rank, (pos, score) in enumerate(ranked, 1)
        ]

    # ---- serializacao ---------------------------------------------------------------------

    def to_dict(self, sources: Sequence[Source] = ()) -> dict[str, object]:
        return {
            "version": INDEX_VERSION,
            "sources": [asdict(s) for s in sources],
            "chunks": [asdict(c) for c in self.chunks],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> BM25Index:
        if payload.get("version") != INDEX_VERSION:
            raise ValueError("versao do indice nao suportada")
        raw = payload.get("chunks")
        if not isinstance(raw, list):
            raise ValueError("indice sem trechos")
        try:
            return cls(Chunk(**item) for item in raw)
        except TypeError as exc:
            raise ValueError("trecho do indice com campos invalidos") from exc


def save_index(index: BM25Index, path: Path, sources: Sequence[Source] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(index.to_dict(sources), ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )


def load_index(path: Path) -> BM25Index:
    return BM25Index.from_dict(json.loads(path.read_text(encoding="utf-8")))


def public_only(index: BM25Index) -> BM25Index:
    """Copia do indice so com os trechos publicos. E o que vai para a funcao da API: texto interno
    nunca chega la, mesmo que a busca fosse chamada sem filtro."""
    return BM25Index([c for c in index.chunks if c.audience == PUBLIC])


def build_index(root: Path, rules: Sequence[DocRule] = DEFAULT_RULES) -> tuple[BM25Index, list[Source]]:
    chunks, sources = load_corpus(root, rules)
    return BM25Index(chunks), sources


# --------------------------------------------------------------------------- #
# Texto devolvido ao modelo
# --------------------------------------------------------------------------- #


def format_hits(hits: Sequence[Hit]) -> str:
    """Trechos numerados, com a fonte de cada um, em um tamanho limitado. O texto e dado de
    referencia: o modelo e instruido a nao obedecer ordens que apareçam dentro dele."""
    if not hits:
        return "nenhum trecho da documentação encontrado para essa busca"
    out: list[str] = []
    used = 0
    for hit in hits:
        body = (
            hit.chunk.text if len(hit.chunk.text) <= MAX_HIT_CHARS else hit.chunk.text[:MAX_HIT_CHARS] + "…"
        )
        block = f"[{hit.rank}] fonte: {hit.chunk.citation} (linha {hit.chunk.line})\n{body}"
        if used + len(block) > MAX_HITS_CHARS and out:
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out)


# --------------------------------------------------------------------------- #
# Linha de comando
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indice BM25 da documentacao do repositorio.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="monta o indice a partir da pasta do repositorio")
    build.add_argument("--root", type=Path, default=Path("."))
    build.add_argument("--out", type=Path, default=Path("build/docs_index.json"))
    search = sub.add_parser("search", help="busca no indice")
    search.add_argument("query")
    search.add_argument("--index", type=Path, default=Path("build/docs_index.json"))
    search.add_argument("-k", type=int, default=4)
    search.add_argument("--all-audiences", action="store_true", help="inclui os trechos internos")
    args = parser.parse_args(argv)

    if args.command == "build":
        index, sources = build_index(args.root)
        save_index(index, args.out, sources)
        public = sum(c.audience == PUBLIC for c in index.chunks)
        print(f"{len(sources)} arquivos, {len(index.chunks)} trechos ({public} públicos) em {args.out}")
        return 0

    index = load_index(args.index)
    audiences = AUDIENCES if args.all_audiences else {PUBLIC}
    print(format_hits(index.search(args.query, audiences=audiences, k=args.k)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
