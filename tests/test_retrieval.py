from __future__ import annotations

import json
from pathlib import Path

import pytest

from cnpj_pipeline.agent import retrieval
from cnpj_pipeline.agent.retrieval import (
    INTERNAL,
    PUBLIC,
    BM25Index,
    Chunk,
    DocRule,
    Hit,
    build_index,
    chunk_markdown,
    discover_sources,
    fold,
    format_hits,
    load_index,
    main,
    save_index,
    stem,
    tokenize,
)

# --------------------------------------------------------------------------- #
# Texto para termos
# --------------------------------------------------------------------------- #


def test_fold_removes_accents_and_case():
    assert fold("Informações Ação ÇÃO") == "informacoes acao cao"


def test_tokenize_folds_drops_stopwords_and_keeps_content_words():
    assert tokenize("Por que o MEI e a UF do Simples?") == ["mei", "uf", "simple"] or tokenize(
        "Por que o MEI e a UF do Simples?"
    ) == ["mei", "uf", "simples"]
    assert "uf" in tokenize("qual a UF?") and "mei" in tokenize("MEI ativo")


def test_tokenize_keeps_an_identifier_whole_and_in_parts():
    terms = tokenize("a tabela mart_simples_mei_uf")
    assert {"mart_simples_mei_uf", "mart", "simple", "mei", "uf"} <= set(terms) or {
        "mart_simples_mei_uf",
        "mart",
        "simples",
        "mei",
        "uf",
    } <= set(terms)


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("regras", "regra"),
        ("informacoes", "informacao"),
        ("coes", "coe"),  # curto demais para o sufixo de -cao: so o plural sai
        ("paises", "paise"),
        ("anais", "anal"),
        ("papeis", "papel"),
        ("mensagens", "mensagem"),
        ("status", "status"),
        ("9492800", "9492800"),
        ("cnae2", "cnae2"),
        ("mart_x_uf", "mart_x_uf"),
    ],
)
def test_stem_only_strips_plurals(word, expected):
    assert stem(word) == expected


def test_the_same_term_in_the_question_and_in_the_text_meets():
    assert set(tokenize("Quais são as regras?")) & set(tokenize("A regra de deduplicação"))


# --------------------------------------------------------------------------- #
# Trechos
# --------------------------------------------------------------------------- #

DOC = """# Titulo

Introducao curta.

## Camadas

Texto da camada.

```bash
# isto nao e titulo
python -m x
```

### Detalhe

Mais texto.
"""


def test_chunks_follow_the_headings_and_ignore_headings_inside_code():
    chunks = chunk_markdown(DOC, path="docs/a.md", audience=PUBLIC)

    assert [c.heading for c in chunks] == [
        "Titulo",
        "Titulo > Camadas",
        "Titulo > Camadas > Detalhe",
    ]
    assert "isto nao e titulo" in chunks[1].text and "python -m x" in chunks[1].text
    assert [c.id for c in chunks] == ["docs/a.md#1", "docs/a.md#2", "docs/a.md#3"]
    assert all(c.audience == PUBLIC and c.path == "docs/a.md" for c in chunks)


def test_chunks_record_the_line_where_they_start():
    chunks = chunk_markdown(DOC, path="a.md", audience=PUBLIC)

    assert chunks[0].line == 3 and chunks[1].line == 7
    assert DOC.splitlines()[chunks[2].line - 1] == "Mais texto."


def test_a_heading_level_pops_the_previous_siblings():
    text = "# A\n\n## B\n\ntexto b\n\n## C\n\ntexto c\n"
    assert [c.heading for c in chunk_markdown(text, path="a.md", audience=PUBLIC)] == ["A > B", "A > C"]


def test_a_long_section_is_split_by_paragraph_within_the_limit():
    paragraphs = [f"paragrafo {i} " + "palavra " * 30 for i in range(10)]
    text = "# T\n\n" + "\n\n".join(paragraphs)
    chunks = chunk_markdown(text, path="a.md", audience=PUBLIC, max_chars=700)

    assert len(chunks) > 1 and all(len(c.text) <= 700 for c in chunks)
    assert "".join(c.text for c in chunks).count("paragrafo") == 10
    assert all(c.heading == "T" for c in chunks)


def test_a_single_huge_line_is_cut_at_spaces():
    text = "# T\n\n" + "palavra " * 400
    chunks = chunk_markdown(text, path="a.md", audience=PUBLIC, max_chars=500)

    assert len(chunks) > 1 and all(len(c.text) <= 500 for c in chunks)
    assert all(not c.text.endswith("palav") for c in chunks)  # corta em espaco, nao no meio


def test_a_table_is_split_into_row_groups_that_repeat_the_header():
    rows = "\n".join(f"| linha {i} | " + "x" * 80 + " |" for i in range(20))
    text = "# T\n\n| Col A | Col B |\n| --- | --- |\n" + rows + "\n"
    chunks = chunk_markdown(text, path="a.md", audience=PUBLIC)

    assert len(chunks) > 2
    assert all(c.text.startswith("| Col A | Col B |\n| --- | --- |") for c in chunks)
    assert sum(c.text.count("| linha") for c in chunks) == 20
    assert chunks[1].line > chunks[0].line


def test_a_file_without_headings_still_yields_chunks_with_an_empty_heading():
    (chunk,) = chunk_markdown("so um paragrafo\n", path="a.md", audience=PUBLIC)
    assert chunk.heading == "" and chunk.citation == "a.md"


def test_the_citation_names_the_file_and_the_section():
    chunk = Chunk("a.md#1", "docs/a.md", "T > S", "texto", PUBLIC, 3)
    assert chunk.citation == "docs/a.md, seção T > S"


# --------------------------------------------------------------------------- #
# Busca
# --------------------------------------------------------------------------- #


def corpus() -> list[Chunk]:
    def chunk(cid: str, path: str, heading: str, text: str, audience: str, line: int) -> Chunk:
        return Chunk(cid, path, heading, text, audience, line)

    return [
        chunk("a#1", "docs/a.md", "Motor", "Decidido: DuckDB lê CSV maior que a memória.", PUBLIC, 1),
        chunk("a#2", "docs/a.md", "Orquestração", "Airflow com LocalExecutor e uma DAG manual.", PUBLIC, 5),
        chunk("a#3", "docs/a.md", "Gold", "A gold tem marts agregados e dimensões.", PUBLIC, 9),
        chunk("b#1", "infra/b.md", "Chaves", "As chaves do publicador ficam fora do Terraform.", INTERNAL, 1),
        chunk("b#2", "infra/b.md", "Motor", "O motor DuckDB roda na máquina do publicador.", INTERNAL, 4),
    ]


def test_the_best_chunk_comes_first_and_ranks_start_at_one():
    index = BM25Index(corpus())
    hits = index.search("por que DuckDB lê CSV?", audiences={PUBLIC}, k=3)

    assert hits[0].chunk.id == "a#1" and [h.rank for h in hits] == list(range(1, len(hits) + 1))
    assert all(h.score > 0 for h in hits) and hits == sorted(hits, key=lambda h: -h.score)


def test_search_matches_across_accents_case_and_plurals():
    index = BM25Index(corpus())
    assert index.search("DIMENSAO", audiences={PUBLIC}, k=1)[0].chunk.id == "a#3"
    assert index.search("marts", audiences={PUBLIC}, k=1)[0].chunk.id == "a#3"


def test_a_query_without_known_terms_returns_nothing():
    assert BM25Index(corpus()).search("zzzz qqqq", audiences={PUBLIC}) == []
    assert BM25Index(corpus()).search("o que é", audiences={PUBLIC}) == []  # so palavras vazias


def test_k_limits_the_results_and_zero_returns_nothing():
    index = BM25Index(corpus())
    assert len(index.search("DuckDB motor", audiences={PUBLIC, INTERNAL}, k=1)) == 1
    assert index.search("DuckDB", audiences={PUBLIC}, k=0) == []


def test_ties_are_broken_by_corpus_order_so_results_are_deterministic():
    chunks = [Chunk(f"c#{i}", "c.md", "", "mesma frase repetida", PUBLIC, i) for i in range(1, 5)]
    hits = BM25Index(chunks).search("frase repetida", audiences={PUBLIC}, k=4)
    assert [h.chunk.id for h in hits] == ["c#1", "c#2", "c#3", "c#4"]


def test_the_title_counts_for_the_ranking():
    chunks = [
        Chunk("x#1", "x.md", "Idempotência", "Cada arquivo e gravado e renomeado ao final.", PUBLIC, 1),
        Chunk("x#2", "x.md", "Outro", "A idempotência também aparece aqui, de passagem.", PUBLIC, 5),
    ]
    assert BM25Index(chunks).search("idempotência", audiences={PUBLIC}, k=1)[0].chunk.id == "x#1"


# --------------------------------------------------------------------------- #
# Controle de acesso
# --------------------------------------------------------------------------- #


def test_an_internal_chunk_is_never_returned_to_a_public_search_even_on_an_exact_match():
    index = BM25Index(corpus())
    hits = index.search("chaves do publicador ficam fora do Terraform", audiences={PUBLIC}, k=10)

    assert all(h.chunk.audience == PUBLIC for h in hits)
    assert "b#1" not in {h.chunk.id for h in index.search("Terraform console chaves", audiences={PUBLIC})}
    internal = index.search("chaves do publicador", audiences={INTERNAL}, k=1)
    assert internal and internal[0].chunk.id == "b#1"


def test_public_results_do_not_depend_on_what_the_internal_chunks_say():
    """O filtro vem antes da pontuacao: os termos e o tamanho medio dos trechos internos nao mudam
    a posicao nem a pontuacao do que o visitante ve."""
    public_only = BM25Index([c for c in corpus() if c.audience == PUBLIC])
    mixed = BM25Index(corpus())
    for query in ("DuckDB memória", "gold marts", "Airflow DAG", "motor"):
        a = public_only.search(query, audiences={PUBLIC}, k=5)
        b = mixed.search(query, audiences={PUBLIC}, k=5)
        assert [(h.chunk.id, h.score) for h in a] == [(h.chunk.id, h.score) for h in b]


def test_no_audience_or_an_unknown_one_sees_nothing():
    index = BM25Index(corpus())
    assert index.search("DuckDB", audiences=set()) == []
    assert index.search("DuckDB", audiences={"admin"}) == []
    with pytest.raises(ValueError, match="publico desconhecido"):
        BM25Index([Chunk("z#1", "z.md", "", "texto", "admin", 1)])


# --------------------------------------------------------------------------- #
# O que entra no indice
# --------------------------------------------------------------------------- #


def write(root: Path, rel: str, text: str = "# T\n\ntexto\n") -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


RULES = (
    DocRule("README.md", PUBLIC),
    DocRule("docs/*.md", PUBLIC),
    DocRule("docs/quality/*.md", PUBLIC, latest_only=True),
    DocRule("infra/README.md", INTERNAL),
)


def test_only_files_that_a_rule_allows_are_indexed(tmp_path):
    for rel in (
        "README.md", "docs/a.md", "infra/README.md", "NOTES.md", "docs/notes/segredo.md",
        "data/x.md", "node_modules/pkg/README.md", ".git/x.md", "build/y.md",
    ):  # fmt: skip
        write(tmp_path, rel)

    assert {(s.path, s.audience) for s in discover_sources(tmp_path, RULES)} == {
        ("README.md", PUBLIC),
        ("docs/a.md", PUBLIC),
        ("infra/README.md", INTERNAL),
    }


def test_a_glob_star_does_not_cross_folders_and_the_first_rule_wins(tmp_path):
    write(tmp_path, "docs/a.md")
    write(tmp_path, "docs/sub/b.md")
    rules = (DocRule("docs/a.md", INTERNAL), DocRule("docs/*.md", PUBLIC))
    found = {s.path: s.audience for s in discover_sources(tmp_path, rules)}

    assert found == {"docs/a.md": INTERNAL}  # docs/sub/b.md nao casa; a primeira regra decide docs/a.md


def test_latest_only_keeps_the_last_file_of_each_folder(tmp_path):
    for month in ("2026-07", "2026-08", "2026-10"):
        write(tmp_path, f"docs/quality/{month}.md")
    write(tmp_path, "docs/other.md")

    assert [s.path for s in discover_sources(tmp_path, RULES)] == [
        "docs/other.md",
        "docs/quality/2026-10.md",
    ]


def test_symlinks_are_not_followed(tmp_path):
    outside = tmp_path / "fora" / "segredo.md"
    write(tmp_path, "fora/segredo.md", "# S\n\nsegredo\n")
    (tmp_path / "repo" / "docs").mkdir(parents=True)
    link = tmp_path / "repo" / "docs" / "a.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("sem suporte a links simbolicos")

    assert discover_sources(tmp_path / "repo", RULES) == []


def test_sources_carry_a_content_hash_that_changes_with_the_file(tmp_path):
    write(tmp_path, "README.md", "# A\n\num\n")
    first = discover_sources(tmp_path, RULES)[0].sha256
    write(tmp_path, "README.md", "# A\n\ndois\n")
    assert discover_sources(tmp_path, RULES)[0].sha256 != first


# --------------------------------------------------------------------------- #
# Indice em arquivo, texto devolvido ao modelo e linha de comando
# --------------------------------------------------------------------------- #


def test_the_index_round_trips_through_json_with_the_same_results(tmp_path):
    index = BM25Index(corpus())
    path = tmp_path / "sub" / "idx.json"
    save_index(index, path)
    loaded = load_index(path)

    assert loaded.chunks == index.chunks
    query = "DuckDB lê CSV"
    assert [(h.chunk.id, h.score) for h in loaded.search(query, audiences={PUBLIC})] == [
        (h.chunk.id, h.score) for h in index.search(query, audiences={PUBLIC})
    ]
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == retrieval.INDEX_VERSION


def test_a_corrupted_or_foreign_index_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="versao"):
        BM25Index.from_dict({"version": 99, "chunks": []})
    with pytest.raises(ValueError, match="sem trechos"):
        BM25Index.from_dict({"version": retrieval.INDEX_VERSION})
    with pytest.raises(ValueError, match="campos invalidos"):
        BM25Index.from_dict({"version": retrieval.INDEX_VERSION, "chunks": [{"id": "x"}]})


def test_format_hits_shows_numbered_sources_and_truncates_long_text():
    short = Hit(Chunk("a#1", "docs/a.md", "T > S", "texto curto", PUBLIC, 7), 1.0, 1)
    long = Hit(Chunk("a#2", "docs/b.md", "T", "x" * 5000, PUBLIC, 9), 0.5, 2)
    text = format_hits([short, long])

    assert "[1] fonte: docs/a.md, seção T > S (linha 7)\ntexto curto" in text
    assert "[2] fonte: docs/b.md" in text and "x" * 1300 not in text and "…" in text
    assert format_hits([]).startswith("nenhum trecho")


def test_format_hits_stops_before_the_total_limit():
    hits = [Hit(Chunk(f"a#{i}", "a.md", "T", "y" * 1100, PUBLIC, i), 1.0, i) for i in range(1, 9)]
    text = format_hits(hits)

    assert len(text) <= retrieval.MAX_HITS_CHARS + 300 and "[1]" in text and "[8]" not in text


def test_cli_builds_and_searches_without_internal_chunks_by_default(tmp_path, capsys, monkeypatch):
    write(tmp_path, "README.md", "# Projeto\n\n## Motor\n\nDuckDB lê CSV maior que a memória.\n")
    write(tmp_path, "infra/README.md", "# Infra\n\n## Motor\n\nO motor interno DuckDB do publicador.\n")
    monkeypatch.setattr(retrieval, "DEFAULT_RULES", RULES)
    monkeypatch.setattr(retrieval, "build_index", lambda root, rules=RULES: build_index(root, rules))
    out = tmp_path / "build" / "idx.json"

    assert main(["build", "--root", str(tmp_path), "--out", str(out)]) == 0
    assert "2 arquivos, 2 trechos (1 públicos)" in capsys.readouterr().out

    assert main(["search", "motor DuckDB", "--index", str(out)]) == 0
    shown = capsys.readouterr().out
    assert "README.md" in shown and "infra/README.md" not in shown

    assert main(["search", "motor DuckDB", "--index", str(out), "--all-audiences"]) == 0
    assert "infra/README.md" in capsys.readouterr().out
