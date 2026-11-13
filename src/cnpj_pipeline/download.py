"""Download parametrizado dos arquivos de dados abertos do CNPJ.

Caracteristicas:
- parametrizado por mes de referencia, tabelas e numero de partes;
- retomada de downloads interrompidos (HTTP Range + arquivo .part);
- retry com backoff exponencial para erros de rede e respostas 5xx;
- verificacao de tamanho contra o Content-Length;
- manifesto JSON por mes com tamanho, SHA-256 e URL de cada arquivo.

Exemplos:
    python -m cnpj_pipeline.download --month 2026-11 --dry-run
    python -m cnpj_pipeline.download --month 2026-11 --tables empresas cnaes
    python -m cnpj_pipeline.download --month 2026-11 --url-template "https://host/{month}/{filename}"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests

from cnpj_pipeline import config

log = logging.getLogger("cnpj_pipeline.download")

CHUNK_SIZE = 1024 * 1024  # 1 MiB
USER_AGENT = "cnpj-pipeline/0.1 (projeto de portfolio; engenharia de dados)"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

STATUS_DOWNLOADED = "downloaded"
STATUS_SKIPPED = "skipped"
STATUS_NOT_FOUND = "not_found"
STATUS_FAILED = "failed"


@dataclass
class DownloadResult:
    filename: str
    url: str
    status: str
    bytes: int = 0
    sha256: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_DOWNLOADED, STATUS_SKIPPED)


class NotFoundError(Exception):
    """O servidor respondeu 404 para o arquivo."""


# --------------------------------------------------------------------------- #
# Montagem da lista de arquivos e URLs
# --------------------------------------------------------------------------- #


def normalize_tables(requested: list[str] | None) -> list[str]:
    """Converte nomes de tabelas (sem diferenciar maiusculas) para o nome canonico."""
    if not requested:
        return list(config.ALL_TABLES)
    canonical = {t.lower(): t for t in config.ALL_TABLES}
    result = []
    for name in requested:
        key = name.lower()
        if key not in canonical:
            valid = ", ".join(config.ALL_TABLES)
            raise ValueError(f"Tabela desconhecida: {name!r}. Validas: {valid}")
        if canonical[key] not in result:
            result.append(canonical[key])
    return result


def build_filenames(tables: list[str], parts: int) -> dict[str, list[str]]:
    """Retorna {tabela: [arquivos]} para as tabelas pedidas."""
    files: dict[str, list[str]] = {}
    for table in tables:
        if table in config.SPLIT_TABLES:
            files[table] = [f"{table}{i}.zip" for i in range(parts)]
        else:
            files[table] = [f"{table}.zip"]
    return files


def build_url(template: str, month: str, filename: str) -> str:
    return template.format(month=month, filename=filename)


# --------------------------------------------------------------------------- #
# Download de um arquivo
# --------------------------------------------------------------------------- #


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_size(session: requests.Session, url: str, timeout: float) -> int | None:
    """Tamanho remoto via HEAD. None se o servidor nao informar. Levanta NotFoundError em 404."""
    try:
        resp = session.head(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return None
    if resp.status_code == 404:
        raise NotFoundError(url)
    if resp.status_code >= 400:
        return None
    length = resp.headers.get("Content-Length")
    return int(length) if length and length.isdigit() else None


def _fetch_once(
    session: requests.Session,
    url: str,
    part: Path,
    expected: int | None,
    timeout: float,
) -> int:
    """Uma tentativa de download, retomando de `part` quando possivel. Retorna o tamanho final."""
    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}

    with session.get(url, headers=headers, stream=True, timeout=timeout) as resp:
        if resp.status_code == 404:
            raise NotFoundError(url)
        if resp.status_code == 416:
            # Range invalido: ou o .part ja esta completo, ou esta corrompido.
            if expected is not None and offset == expected:
                return offset
            part.unlink(missing_ok=True)
            raise requests.RequestException("Range nao satisfatorio; reiniciando do zero")
        resp.raise_for_status()

        if resp.status_code == 206:
            mode = "ab"
        else:
            # Servidor ignorou o Range (200): recomeca do zero.
            mode = "wb"
            offset = 0

        content_length = resp.headers.get("Content-Length")
        total = offset + int(content_length) if content_length and content_length.isdigit() else expected

        written = offset
        next_log = 0.1
        with part.open(mode) as fh:
            for chunk in resp.iter_content(CHUNK_SIZE):
                if not chunk:
                    continue
                fh.write(chunk)
                written += len(chunk)
                if total and written / total >= next_log:
                    log.info("%s: %d%% (%.1f MiB)", part.stem, int(written / total * 100), written / 2**20)
                    next_log += 0.1

    if total is not None and written != total:
        raise requests.RequestException(f"Download incompleto: {written} de {total} bytes")
    return written


def download_file(
    url: str,
    dest: Path,
    *,
    retries: int = 5,
    backoff: float = 2.0,
    timeout: float = 60.0,
    checksum: bool = True,
) -> DownloadResult:
    """Baixa `url` para `dest` com retomada, retry e verificacao de tamanho."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    result = DownloadResult(filename=dest.name, url=url, status=STATUS_FAILED)

    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT

        try:
            expected = _remote_size(session, url, timeout)
        except NotFoundError:
            result.status = STATUS_NOT_FOUND
            return result

        if dest.exists() and expected is not None and dest.stat().st_size == expected:
            result.status = STATUS_SKIPPED
            result.bytes = expected
            result.sha256 = _sha256(dest) if checksum else None
            log.info("%s: ja existe com o tamanho esperado, pulando", dest.name)
            return result

        last_error = "erro desconhecido"
        for attempt in range(1, retries + 1):
            try:
                size = _fetch_once(session, url, part, expected, timeout)
                part.replace(dest)
                result.status = STATUS_DOWNLOADED
                result.bytes = size
                result.sha256 = _sha256(dest) if checksum else None
                log.info("%s: concluido (%.1f MiB)", dest.name, size / 2**20)
                return result
            except NotFoundError:
                result.status = STATUS_NOT_FOUND
                return result
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                last_error = f"HTTP {status}"
                if 400 <= status < 500:  # erro do cliente: nao adianta repetir
                    break
            except requests.RequestException as exc:
                last_error = str(exc)

            wait = backoff**attempt
            log.warning(
                "%s: tentativa %d/%d falhou (%s); nova tentativa em %.0fs",
                dest.name,
                attempt,
                retries,
                last_error,
                wait,
            )
            if attempt < retries:
                time.sleep(wait)

        result.error = last_error
        return result


# --------------------------------------------------------------------------- #
# Orquestracao e manifesto
# --------------------------------------------------------------------------- #


def write_manifest(month_dir: Path, month: str, results: list[DownloadResult]) -> Path:
    """Grava (ou atualiza) month_dir/manifest.json com os arquivos obtidos com sucesso."""
    path = month_dir / "manifest.json"
    entries: dict[str, dict] = {}
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        entries = {e["filename"]: e for e in previous.get("files", [])}

    for r in results:
        if r.ok:
            entries[r.filename] = {
                "filename": r.filename,
                "url": r.url,
                "bytes": r.bytes,
                "sha256": r.sha256,
            }

    manifest = {
        "month": month,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "total_bytes": sum(e["bytes"] for e in entries.values()),
        "files": sorted(entries.values(), key=lambda e: e["filename"]),
    }
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def scan_local(
    month_dir: Path, month: str, plan: dict[str, list[str]], url_template: str, checksum: bool
) -> list[DownloadResult]:
    """Registra no manifesto arquivos que ja estao em month_dir, sem acessar a rede."""
    results = []
    for filenames in plan.values():
        for name in filenames:
            path = month_dir / name
            if not path.is_file():
                continue
            results.append(
                DownloadResult(
                    filename=name,
                    url=build_url(url_template, month, name),
                    status=STATUS_SKIPPED,
                    bytes=path.stat().st_size,
                    sha256=_sha256(path) if checksum else None,
                )
            )
    return results


def run(
    month: str,
    tables: list[str],
    *,
    parts: int,
    out_dir: Path,
    url_template: str,
    workers: int,
    retries: int,
    timeout: float,
    checksum: bool,
    dry_run: bool,
    manifest_only: bool = False,
) -> int:
    """Executa o download. Retorna o codigo de saida (0 = sucesso)."""
    plan = build_filenames(tables, parts)
    month_dir = out_dir / month

    if dry_run:
        for filenames in plan.values():
            for name in filenames:
                print(f"{build_url(url_template, month, name)} -> {month_dir / name}")
        return 0

    if manifest_only:
        found = scan_local(month_dir, month, plan, url_template, checksum)
        if not found:
            log.error("Nenhum arquivo esperado encontrado em %s", month_dir)
            return 1
        path = write_manifest(month_dir, month, found)
        log.info("Manifesto gravado em %s com %d arquivo(s)", path, len(found))
        return 0

    jobs = [
        (table, name, build_url(url_template, month, name))
        for table, filenames in plan.items()
        for name in filenames
    ]
    log.info("Baixando %d arquivo(s) de %s para %s", len(jobs), month, month_dir)

    def work(job: tuple[str, str, str]) -> tuple[str, DownloadResult]:
        table, name, url = job
        return table, download_file(
            url, month_dir / name, retries=retries, timeout=timeout, checksum=checksum
        )

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        outcomes = list(pool.map(work, jobs))

    results = [r for _, r in outcomes]
    manifest_path = write_manifest(month_dir, month, results)

    exit_code = 0
    for table in plan:
        table_results = [r for t, r in outcomes if t == table]
        if not any(r.ok for r in table_results):
            log.error("Tabela %s: nenhum arquivo obtido", table)
            exit_code = 1
        for r in table_results:
            if r.status == STATUS_FAILED:
                log.error("%s: falhou (%s)", r.filename, r.error)
                exit_code = 1
            elif r.status == STATUS_NOT_FOUND and table in config.SINGLE_TABLES:
                log.error("%s: nao encontrado (404): %s", r.filename, r.url)
            elif r.status == STATUS_NOT_FOUND:
                log.warning("%s: nao encontrado (404), parte ignorada", r.filename)

    ok = sum(r.ok for r in results)
    log.info("Resumo: %d ok, %d com problema. Manifesto: %s", ok, len(results) - ok, manifest_path)
    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.download",
        description="Baixa os arquivos de dados abertos do CNPJ para data/raw/<mes>/.",
    )
    parser.add_argument("--month", required=True, help="Mes de referencia no formato AAAA-MM")
    parser.add_argument(
        "--tables",
        nargs="+",
        metavar="TABELA",
        help=f"Tabelas a baixar (padrao: todas). Validas: {', '.join(config.ALL_TABLES)}",
    )
    parser.add_argument(
        "--parts",
        type=int,
        default=config.DEFAULT_PARTS,
        help="Numero de partes das tabelas divididas (padrao: %(default)s)",
    )
    parser.add_argument("--out-dir", type=Path, default=config.RAW_DIR, help="Pasta de destino")
    parser.add_argument(
        "--url-template",
        default=config.URL_TEMPLATE,
        help="Template da URL com {month} e {filename} (padrao: CNPJ_URL_TEMPLATE ou o valor de config.py)",
    )
    parser.add_argument("--workers", type=int, default=2, help="Downloads em paralelo (padrao: %(default)s)")
    parser.add_argument("--retries", type=int, default=5, help="Tentativas por arquivo (padrao: %(default)s)")
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="Timeout em segundos (padrao: %(default)s)"
    )
    parser.add_argument("--no-checksum", action="store_true", help="Nao calcula SHA-256 dos arquivos")
    parser.add_argument("--dry-run", action="store_true", help="Apenas lista as URLs, sem baixar")
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Nao baixa nada: gera o manifesto a partir dos arquivos ja presentes na pasta do mes",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs detalhados")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not MONTH_RE.match(args.month):
        print(f"--month invalido: {args.month!r}. Use AAAA-MM, por exemplo 2026-11.", file=sys.stderr)
        return 2
    if args.parts < 1:
        print("--parts deve ser >= 1", file=sys.stderr)
        return 2
    try:
        tables = normalize_tables(args.tables)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    return run(
        args.month,
        tables,
        parts=args.parts,
        out_dir=args.out_dir,
        url_template=args.url_template,
        workers=args.workers,
        retries=args.retries,
        timeout=args.timeout,
        checksum=not args.no_checksum,
        dry_run=args.dry_run,
        manifest_only=args.manifest_only,
    )


if __name__ == "__main__":
    sys.exit(main())
