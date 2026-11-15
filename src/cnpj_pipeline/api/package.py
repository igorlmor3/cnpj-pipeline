"""Gera o zip da funcao da API: codigo do pacote, dependencias para Linux e o arquivo de colunas.

    python -m cnpj_pipeline.api.package --month 2026-10

As dependencias (anthropic e sqlglot) sao baixadas como rodas para Linux x86_64 e Python 3.12,
o ambiente da funcao, mesmo que o comando rode no Windows. O boto3 ja vem no ambiente da funcao.
O zip e deterministico (ordem e datas fixas): o mesmo conteudo gera o mesmo arquivo, e o Terraform
so atualiza a funcao quando o conteudo muda. O arquivo de colunas so tem nomes e tipos, nenhum dado.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

from cnpj_pipeline.api.columns import write_columns

LAMBDA_REQUIREMENTS = ("anthropic>=0.40", "sqlglot>=25.0")
LAMBDA_PYTHON = "3.12"
LAMBDA_PLATFORMS = ("manylinux2014_x86_64", "manylinux_2_28_x86_64")
# A AWS recusa um envio direto acima de 50 MB; este teto deixa folga.
MAX_ZIP_BYTES = 45 * 1024 * 1024
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
SKIP_SUFFIXES = {".pyc", ".pyo"}
COLUMNS_NAME = "agent_columns.json"

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # .../cnpj_pipeline

Installer = Callable[[Path], None]


def pip_command(target: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--no-input",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--implementation",
        "cp",
        "--python-version",
        LAMBDA_PYTHON,
        "--target",
        str(target),
    ]
    for platform in LAMBDA_PLATFORMS:
        command += ["--platform", platform]
    return [*command, *LAMBDA_REQUIREMENTS]


def install_dependencies(target: Path) -> None:
    subprocess.run(pip_command(target), check=True)


def _skipped(relative: Path) -> bool:
    """Scripts de linha de comando das dependencias (bin/) e bytecode (.pyc) nao vao no zip."""
    return relative.parts[0] == "bin" or relative.suffix in SKIP_SUFFIXES


def stage(stage_dir: Path, *, install: Installer, package_dir: Path = PACKAGE_DIR) -> None:
    """Monta a arvore da funcao: dependencias na raiz e o pacote cnpj_pipeline ao lado."""
    install(stage_dir)
    destination = stage_dir / "cnpj_pipeline"
    if destination.exists():
        raise RuntimeError("as dependencias nao podem trazer um pacote cnpj_pipeline")
    shutil.copytree(package_dir, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (destination / "api" / COLUMNS_NAME).unlink(missing_ok=True)  # coluna de outro mes nunca vai junto


def write_zip(stage_dir: Path, out: Path) -> str:
    """Compacta a arvore de forma deterministica e devolve o SHA-256 do zip."""
    files = sorted(p for p in stage_dir.rglob("*") if p.is_file() and not _skipped(p.relative_to(stage_dir)))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(stage_dir).as_posix(), date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    if tmp.stat().st_size > MAX_ZIP_BYTES:
        size = tmp.stat().st_size
        tmp.unlink()
        raise RuntimeError(
            f"o zip tem {size / 1024 / 1024:.1f} MB, acima do teto de {MAX_ZIP_BYTES // 1024 // 1024} MB"
        )
    tmp.replace(out)
    return hashlib.sha256(out.read_bytes()).hexdigest()


def build(month: str, gold_dir: Path, out: Path, *, install: Installer = install_dependencies) -> str:
    with tempfile.TemporaryDirectory(prefix="cnpj-api-") as tmp:
        stage_dir = Path(tmp)
        stage(stage_dir, install=install)
        write_columns(gold_dir, month, stage_dir / "cnpj_pipeline" / "api" / COLUMNS_NAME)
        return write_zip(stage_dir, out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gera o zip da funcao da API do agente.")
    parser.add_argument(
        "--month", required=True, help="Mes de referencia (AAAA-MM) cuja gold define as colunas"
    )
    parser.add_argument("--gold-dir", type=Path, default=Path("data") / "gold", help="Pasta da gold local")
    parser.add_argument("--out", type=Path, default=Path("infra") / "build" / "api.zip", help="Zip de saida")
    args = parser.parse_args(argv)

    try:
        digest = build(args.month, args.gold_dir, args.out)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Falha ao gerar o pacote: {exc}", file=sys.stderr)
        return 1
    size = args.out.stat().st_size / 1024 / 1024
    print(f"Pacote: {args.out} ({size:.1f} MB)")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
