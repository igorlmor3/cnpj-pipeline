"""Gerador de dados sinteticos no formato dos arquivos do CNPJ.

Cria, para um mes ficticio, zips com CSVs iguais aos da Receita (sem cabecalho, separador
`;`, todos os campos entre aspas, encoding latin-1) e com defeitos plantados de proposito:
espacos nas bordas, `;` e aspas dentro de textos, decimais com ponto, datas zeradas e
invalidas, chaves duplicadas, chaves orfas e linhas com numero de colunas errado.

Todos os dados sao inventados. Nomes, documentos e enderecos nao correspondem a pessoas
ou empresas reais. O gerador e deterministico: a mesma semente produz os mesmos arquivos.

Exemplo:
    python -m cnpj_pipeline.sample --out-dir data/sample/raw --rows 3000
"""

from __future__ import annotations

import argparse
import csv
import io
import random
import sys
import zipfile
from pathlib import Path

from cnpj_pipeline import config
from cnpj_pipeline.download import MONTH_RE
from cnpj_pipeline.schemas import columns_for

DEFAULT_MONTH = "2000-01"
DEFAULT_OUT_DIR = config.DATA_DIR / "sample" / "raw"

# Nome do CSV dentro de cada zip, no estilo dos arquivos reais.
MEMBER_SUFFIX = {
    "Empresas": "EMPRECSV",
    "Estabelecimentos": "ESTABELE",
    "Socios": "SOCIOCSV",
    "Simples": "SIMPLES.CSV",
    "Cnaes": "CNAECSV",
    "Motivos": "MOTICSV",
    "Municipios": "MUNICCSV",
    "Naturezas": "NATJUCSV",
    "Paises": "PAISCSV",
    "Qualificacoes": "QUALSCSV",
}

PREFIXES = ("COMERCIAL", "INDÚSTRIA", "SERVIÇOS", "TRANSPORTES", "DISTRIBUIDORA", "PADARIA", "OFICINA")
WORDS = ("AÇÚCAR", "CAFÉ", "SÃO JOÃO", "BOA VISTA", "ESTRELA", "HORIZONTE", "ÁGUA CLARA", "UNIÃO", "FÊNIX")
SUFFIXES = ("LTDA", "S/A", "EIRELI", "ME")
FIRST_NAMES = ("ANA", "BRUNO", "CARLA", "DANIEL", "ELISA", "FÁBIO", "GISELE", "HUGO", "ÍRIS", "JOÃO")
LAST_NAMES = ("SILVA", "SOUZA", "OLIVEIRA", "LIMA", "ALMEIDA", "GONÇALVES", "RIBEIRO", "CARVALHO")
STREETS = ("DAS FLORES", "SÃO PAULO", "PRIMEIRO DE MAIO", "DOS ANDRADAS", "DA LIBERDADE")
UFS = ("SP", "RJ", "MG", "ES", "PR", "BA", "RS")

DOMAINS: dict[str, list[tuple[str, str]]] = {
    "Cnaes": [(f"{4700000 + i * 137:07d}", f"ATIVIDADE SINTÉTICA {i:02d}") for i in range(1, 21)],
    "Motivos": [(f"{i:02d}", f"MOTIVO SINTÉTICO {i:02d}") for i in range(0, 10)],
    "Municipios": [(f"{7000 + i * 13:04d}", f"MUNICÍPIO SINTÉTICO {i:02d}") for i in range(1, 21)],
    "Naturezas": [(code, f"NATUREZA {code}") for code in ("2062", "2135", "2305", "2240", "1015", "3999")],
    "Paises": [("105", "BRASIL"), ("249", "ESTADOS UNIDOS"), ("173", "PORTUGAL"), ("023", "ALEMANHA")],
    "Qualificacoes": [(f"{c:02d}", f"QUALIFICAÇÃO {c:02d}") for c in (5, 10, 16, 22, 49, 54)],
}


# Chaves invalidas plantadas para exercitar a quarentena da camada silver.
INVALID_BASICOS = ("ABC", "", "1234567")
INVALID_ESTAB_KEYS = (("1", "91"), ("0001", "9X"))


def _line(fields: list[str]) -> str:
    """Formata uma linha como a Receita: tudo entre aspas, separador `;`, aspas duplicadas."""
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";", quotechar='"', quoting=csv.QUOTE_ALL, lineterminator="").writerow(
        fields
    )
    return buffer.getvalue()


def _date(rng: random.Random) -> str:
    """Data AAAAMMDD, com defeitos: zero, zeros e formatos invalidos."""
    r = rng.random()
    if r < 0.04:
        return "0"
    if r < 0.07:
        return "00000000"
    if r < 0.08:
        return rng.choice(("20231345", "31122020", "20200230"))
    return f"{rng.randint(1990, 2025)}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"


def _person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)} {rng.choice(LAST_NAMES)}"


def make_domain(table: str) -> list[str]:
    return [_line([code, desc]) for code, desc in DOMAINS[table]]


def make_empresas(rng: random.Random, n: int) -> list[str]:
    natureza_codes = [c for c, _ in DOMAINS["Naturezas"]]
    qualif_codes = [c for c, _ in DOMAINS["Qualificacoes"]]
    rows = []
    for i in range(1, n + 1):
        nome = f"{rng.choice(PREFIXES)} {rng.choice(WORDS)} {rng.choice(SUFFIXES)}"
        r = rng.random()
        if r < 0.03:
            nome = f"  {nome} "  # espacos nas bordas
        elif r < 0.05:
            nome = nome.replace(" ", " ; ", 1)  # separador dentro do texto
        elif r < 0.07:
            nome += ' "FILIAL"'  # aspas dentro do texto
        capital = f"{rng.randint(0, 5_000_000)},{rng.randint(0, 99):02d}"
        if rng.random() < 0.01:
            capital = capital.replace(",", ".")  # decimal fora do padrao
        natureza = "9999" if rng.random() < 0.01 else rng.choice(natureza_codes)  # 9999: orfa
        rows.append(
            [
                f"{i:08d}",
                nome,
                natureza,
                rng.choice(qualif_codes),
                capital,
                rng.choice(("01", "03", "05")),
                "",
            ]
        )
    for dup in rng.sample(rows, k=max(1, n // 200)):  # chaves duplicadas
        rows.append([dup[0], dup[1] + " DUPLICADA", *dup[2:]])
    for bad_key in INVALID_BASICOS:  # chaves invalidas (sem sortear: nao altera o restante da amostra)
        rows.append([bad_key, "EMPRESA CHAVE INVALIDA", "2062", "49", "10,00", "01", ""])
    return [_line(r) for r in rows]


def make_estabelecimentos(rng: random.Random, n_empresas: int) -> list[str]:
    cnaes = [c for c, _ in DOMAINS["Cnaes"]]
    motivos = [c for c, _ in DOMAINS["Motivos"]]
    municipios = [c for c, _ in DOMAINS["Municipios"]]
    paises = [c for c, _ in DOMAINS["Paises"]]
    rows = []
    for i in range(1, n_empresas + 1):
        for ordem in range(1, rng.choice((1, 1, 2, 3)) + 1):
            basico = f"{i:08d}"
            if rng.random() < 0.005:
                basico = f"{99_000_000 + i:08d}"  # empresa inexistente (orfa)
            email = f"contato{i}@exemplo.test" if rng.random() < 0.3 else ""
            if email and rng.random() < 0.1:
                email = f" {email.upper()} "
            rows.append(
                [
                    basico,
                    f"{ordem:04d}",
                    f"{rng.randint(0, 99):02d}",
                    "1" if ordem == 1 else "2",
                    f"FANTASIA {i}" if rng.random() < 0.6 else "",
                    rng.choice(("02", "02", "02", "03", "04", "08")),
                    _date(rng),
                    rng.choice(motivos),
                    "",
                    rng.choice(paises) if rng.random() < 0.05 else "",
                    _date(rng),
                    rng.choice(cnaes),
                    ",".join(rng.sample(cnaes, k=rng.randint(0, 3))),
                    "RUA",
                    rng.choice(STREETS),
                    str(rng.randint(1, 3000)) if rng.random() > 0.05 else "S/N",
                    "",
                    "CENTRO",
                    f"{rng.randint(10_000_000, 99_999_999)}",
                    rng.choice(UFS),
                    rng.choice(municipios),
                    f"{rng.randint(11, 99)}",
                    f"{rng.randint(20_000_000, 99_999_999)}",
                    "",
                    "",
                    "",
                    "",
                    email,
                    "",
                    "",
                ]
            )
    template = list(rows[0])
    for ordem, dv in INVALID_ESTAB_KEYS:  # ordem ou digito verificador invalidos
        rows.append([f"{1:08d}", ordem, dv, *template[3:]])
    return [_line(r) for r in rows]


def make_socios(rng: random.Random, n_empresas: int) -> list[str]:
    qualif = [c for c, _ in DOMAINS["Qualificacoes"]]
    rows = []
    for i in range(1, n_empresas + 1):
        for _ in range(rng.choice((0, 1, 1, 2, 3))):
            tipo = rng.choice(("1", "2", "2", "2"))
            documento = (
                f"{rng.randint(10**13, 10**14 - 1)}" if tipo == "1" else f"***{rng.randint(0, 999_999):06d}**"
            )
            rows.append(
                [
                    f"{i:08d}",
                    tipo,
                    _person(rng),
                    documento,
                    rng.choice(qualif),
                    _date(rng),
                    "",
                    "***000000**",
                    "",
                    "00",
                    str(rng.randint(0, 9)),
                ]
            )
    return [_line(r) for r in rows]


def make_simples(rng: random.Random, n_empresas: int) -> list[str]:
    rows = []
    for i in range(1, n_empresas + 1):
        if rng.random() < 0.6:
            rows.append(
                [
                    f"{i:08d}",
                    rng.choice(("S", "N")),
                    _date(rng),
                    _date(rng),
                    rng.choice(("S", "N")),
                    _date(rng),
                    _date(rng),
                ]
            )
    return [_line(r) for r in rows]


def _write_zip(path: Path, member_name: str, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(lines) + "\n").encode("latin-1")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_name, payload)


def generate(
    month: str = DEFAULT_MONTH,
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    rows: int = 3000,
    parts: int = 2,
    seed: int = 42,
) -> dict[str, list[Path]]:
    """Gera os zips em out_dir/<mes>/ e retorna {tabela: [arquivos]}."""
    rng = random.Random(seed)
    month_dir = out_dir / month
    stamp = month.replace("-", "")
    created: dict[str, list[Path]] = {}

    def member(table: str) -> str:
        return f"K3241.K03200Y0.D{stamp[2:]}.{MEMBER_SUFFIX[table]}"

    big = {
        "Empresas": make_empresas(rng, rows),
        "Estabelecimentos": make_estabelecimentos(rng, rows),
        "Socios": make_socios(rng, rows),
    }
    for table, lines in big.items():
        # Uma linha quebrada na primeira parte (numero de colunas errado)
        width = len(columns_for(table))
        broken = _line([f"{99_999_900:08d}", "LINHA QUEBRADA", *["X"] * max(0, width // 3 - 2)])
        lines = [broken, *lines]
        created[table] = []
        for part in range(parts):
            chunk = lines[part::parts]
            path = month_dir / f"{table}{part}.zip"
            _write_zip(path, member(table), chunk)
            created[table].append(path)

    path = month_dir / "Simples.zip"
    _write_zip(path, member("Simples"), make_simples(rng, rows))
    created["Simples"] = [path]

    for table in DOMAINS:
        path = month_dir / f"{table}.zip"
        _write_zip(path, member(table), make_domain(table))
        created[table] = [path]
    return created


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.sample",
        description="Gera zips sinteticos no formato do CNPJ, com defeitos plantados.",
    )
    parser.add_argument("--month", default=DEFAULT_MONTH, help="Mes ficticio AAAA-MM (padrao: %(default)s)")
    parser.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Pasta de saida (padrao: data/sample/raw)"
    )
    parser.add_argument("--rows", type=int, default=3000, help="Quantidade de empresas (padrao: %(default)s)")
    parser.add_argument(
        "--parts", type=int, default=2, help="Partes das tabelas grandes (padrao: %(default)s)"
    )
    parser.add_argument("--seed", type=int, default=42, help="Semente aleatoria (padrao: %(default)s)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not MONTH_RE.match(args.month):
        print(f"--month invalido: {args.month!r}. Use AAAA-MM.", file=sys.stderr)
        return 2
    if args.rows < 10 or args.parts < 1:
        print("--rows deve ser >= 10 e --parts >= 1", file=sys.stderr)
        return 2
    created = generate(args.month, args.out_dir, rows=args.rows, parts=args.parts, seed=args.seed)
    total = sum(len(v) for v in created.values())
    print(f"{total} arquivo(s) sintetico(s) gerado(s) em {args.out_dir / args.month}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
