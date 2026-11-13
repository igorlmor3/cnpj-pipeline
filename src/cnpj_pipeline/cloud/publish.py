"""Publica o Parquet local (bronze, silver, gold) em um bucket S3.

O layout do bucket espelha o layout local:

    data/silver/empresas/reference_month=2026-10/empresas.parquet
    -> s3://<bucket>/silver/empresas/reference_month=2026-10/empresas.parquet

Regras:
- por padrao publica so a silver; bronze e gold entram com --layers;
- quarentena e rejeitados contem linhas originais (possivelmente dados pessoais) e so sobem com
  --include-quarantine;
- idempotente: um arquivo ja publicado com o mesmo tamanho e o mesmo SHA-256 e pulado;
- cada upload e conferido com um HEAD (tamanho) depois de concluido.

Exemplos:
    python -m cnpj_pipeline.cloud.publish --month 2026-10 --dry-run
    python -m cnpj_pipeline.cloud.publish --month 2026-10 --layers silver gold
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cnpj_pipeline import config
from cnpj_pipeline.cloud import common
from cnpj_pipeline.download import MONTH_RE

log = logging.getLogger("cnpj_pipeline.cloud.publish")

LAYERS = ("bronze", "silver", "gold")
DEFAULT_LAYERS = ("silver",)
EXCLUDED_DIRS = ("_quarantine", "_rejects")
CHUNK = 8 * 1024 * 1024


class PublishError(RuntimeError):
    """Falha na publicacao (por exemplo, tamanho diferente depois do upload)."""


@dataclass
class PublishResult:
    uploaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    bytes_uploaded: int = 0


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def collect_files(
    data_dir: Path,
    layer: str,
    month: str,
    *,
    tables: Iterable[str] | None = None,
    include_quarantine: bool = False,
) -> list[tuple[Path, str]]:
    """Lista (arquivo local, chave no S3) de uma camada e mes, em ordem estavel."""
    layer_dir = data_dir / layer
    if not layer_dir.is_dir():
        return []
    wanted = {t.lower() for t in tables} if tables else None
    partition = f"reference_month={month}"
    found: list[tuple[Path, str]] = []

    for path in sorted(layer_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(layer_dir)
        parts = rel.parts
        is_extra = parts[0] in EXCLUDED_DIRS
        if is_extra and not include_quarantine:
            continue
        if path.suffix == ".parquet":
            if partition not in parts:
                continue
            table = parts[1] if is_extra else parts[0]
            if wanted is not None and table.lower() not in wanted:
                continue
        elif not (len(parts) == 1 and path.name == f"_manifest_{month}.json"):
            continue
        found.append((path, f"{layer}/{rel.as_posix()}"))
    return found


def _is_not_found(exc: Exception) -> bool:
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def remote_state(client: Any, bucket: str, key: str) -> tuple[int, str | None] | None:
    """Tamanho e SHA-256 gravados no objeto, ou None se ele nao existe."""
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _is_not_found(exc):
            return None
        raise
    return int(head["ContentLength"]), (head.get("Metadata") or {}).get("sha256")


def upload_one(client: Any, bucket: str, path: Path, key: str, digest: str) -> int:
    size = path.stat().st_size
    client.upload_file(str(path), bucket, key, ExtraArgs={"Metadata": {"sha256": digest}})
    state = remote_state(client, bucket, key)
    if state is None or state[0] != size:
        raise PublishError(f"{key}: tamanho no S3 ({state}) diferente do local ({size} bytes)")
    return size


def publish(
    client: Any,
    bucket: str,
    files: list[tuple[Path, str]],
    *,
    dry_run: bool = False,
) -> PublishResult:
    result = PublishResult()
    for path, key in files:
        digest = sha256_of(path)
        state = remote_state(client, bucket, key)
        if state is not None and state == (path.stat().st_size, digest):
            result.skipped.append(key)
            log.info("ja publicado, pulando: %s", key)
            continue
        if dry_run:
            result.planned.append(key)
            log.info("[dry-run] publicaria: %s", key)
            continue
        result.bytes_uploaded += upload_one(client, bucket, path, key, digest)
        result.uploaded.append(key)
        log.info("publicado: s3://%s/%s", bucket, key)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cnpj_pipeline.cloud.publish",
        description="Publica o Parquet local no S3, de forma idempotente e verificada.",
    )
    parser.add_argument("--month", required=True, help="Mes de referencia AAAA-MM")
    parser.add_argument("--layers", nargs="+", choices=LAYERS, default=list(DEFAULT_LAYERS))
    parser.add_argument("--tables", nargs="+", metavar="TABELA", help="Subconjunto de tabelas")
    parser.add_argument("--bucket", default=common.default_bucket(), help=f"Bucket (ou {common.BUCKET_ENV})")
    parser.add_argument("--profile", default=common.default_profile(), help="Perfil do AWS CLI")
    parser.add_argument("--region", default=common.default_region())
    parser.add_argument("--data-dir", type=Path, default=config.DATA_DIR)
    parser.add_argument(
        "--include-quarantine",
        action="store_true",
        help="Tambem publica quarentena e rejeitados (contem linhas originais)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Mostra o que seria publicado")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, client: Any | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not MONTH_RE.match(args.month):
        print(f"--month invalido: {args.month!r}. Use AAAA-MM, por exemplo 2026-10.", file=sys.stderr)
        return 2
    if not args.bucket:
        print(f"Informe --bucket ou defina {common.BUCKET_ENV}.", file=sys.stderr)
        return 2

    files: list[tuple[Path, str]] = []
    for layer in args.layers:
        files += collect_files(
            args.data_dir,
            layer,
            args.month,
            tables=args.tables,
            include_quarantine=args.include_quarantine,
        )
    if not files:
        log.error("Nenhum arquivo de %s para %s em %s", "/".join(args.layers), args.month, args.data_dir)
        return 1

    try:
        if client is None:
            client = common.make_client("s3", profile=args.profile, region=args.region)
        result = publish(client, args.bucket, files, dry_run=args.dry_run)
    except (common.CloudConfigError, PublishError) as exc:
        log.error("%s", exc)
        return 1

    log.info(
        "Resumo: %d publicado(s) (%.1f MB), %d ja existente(s), %d planejado(s)",
        len(result.uploaded),
        result.bytes_uploaded / 1_000_000,
        len(result.skipped),
        len(result.planned),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
