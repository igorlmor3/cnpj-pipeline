from __future__ import annotations

import hashlib
import json

import pytest

from cnpj_pipeline import config
from cnpj_pipeline.download import (
    STATUS_DOWNLOADED,
    STATUS_FAILED,
    STATUS_NOT_FOUND,
    STATUS_SKIPPED,
    build_filenames,
    build_url,
    download_file,
    main,
    normalize_tables,
    run,
)

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_kwargs(tmp_path, server, **overrides):
    kwargs = dict(
        parts=2,
        out_dir=tmp_path,
        url_template=server.base_url + "/{month}/{filename}",
        workers=2,
        retries=2,
        timeout=5,
        checksum=True,
        dry_run=False,
    )
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------- #
# Montagem de nomes e URLs
# --------------------------------------------------------------------------- #


def test_normalize_tables_default_returns_all():
    assert normalize_tables(None) == list(config.ALL_TABLES)


def test_normalize_tables_is_case_insensitive_and_deduplicates():
    assert normalize_tables(["empresas", "CNAES", "Empresas"]) == ["Empresas", "Cnaes"]


def test_normalize_tables_rejects_unknown():
    with pytest.raises(ValueError, match="desconhecida"):
        normalize_tables(["inexistente"])


def test_build_filenames_split_and_single():
    files = build_filenames(["Empresas", "Cnaes"], parts=3)
    assert files["Empresas"] == ["Empresas0.zip", "Empresas1.zip", "Empresas2.zip"]
    assert files["Cnaes"] == ["Cnaes.zip"]


def test_build_url():
    url = build_url("https://host/{month}/{filename}", "2026-11", "Empresas0.zip")
    assert url == "https://host/2026-11/Empresas0.zip"


def test_cli_rejects_invalid_month(capsys):
    assert main(["--month", "2026-13", "--dry-run"]) == 2
    assert "invalido" in capsys.readouterr().err


def test_cli_dry_run_lists_urls(capsys):
    code = main(
        [
            "--month",
            "2026-11",
            "--tables",
            "cnaes",
            "--dry-run",
            "--url-template",
            "https://h/{month}/{filename}",
        ]
    )
    assert code == 0
    assert "https://h/2026-11/Cnaes.zip" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# download_file
# --------------------------------------------------------------------------- #


def test_download_new_file(tmp_path, server):
    server.files["a.zip"] = PAYLOAD
    dest = tmp_path / "a.zip"

    result = download_file(server.url("a.zip"), dest, backoff=0.0)

    assert result.status == STATUS_DOWNLOADED
    assert dest.read_bytes() == PAYLOAD
    assert result.bytes == len(PAYLOAD)
    assert result.sha256 == _sha(PAYLOAD)
    assert not dest.with_name("a.zip.part").exists()


def test_skip_when_file_is_complete(tmp_path, server):
    server.files["a.zip"] = PAYLOAD
    dest = tmp_path / "a.zip"
    dest.write_bytes(PAYLOAD)

    result = download_file(server.url("a.zip"), dest, backoff=0.0)

    assert result.status == STATUS_SKIPPED
    assert server.range_requests == []


def test_resume_from_partial_file(tmp_path, server):
    server.files["a.zip"] = PAYLOAD
    dest = tmp_path / "a.zip"
    dest.with_name("a.zip.part").write_bytes(PAYLOAD[:1000])

    result = download_file(server.url("a.zip"), dest, backoff=0.0)

    assert result.status == STATUS_DOWNLOADED
    assert dest.read_bytes() == PAYLOAD
    assert server.range_requests == ["bytes=1000-"]


def test_restart_when_server_ignores_range(tmp_path, server):
    server.files["a.zip"] = PAYLOAD
    server.honor_range = False
    dest = tmp_path / "a.zip"
    dest.with_name("a.zip.part").write_bytes(PAYLOAD[:1000])

    result = download_file(server.url("a.zip"), dest, backoff=0.0)

    assert result.status == STATUS_DOWNLOADED
    assert dest.read_bytes() == PAYLOAD


def test_not_found(tmp_path, server):
    result = download_file(server.url("nao-existe.zip"), tmp_path / "x.zip", backoff=0.0)
    assert result.status == STATUS_NOT_FOUND
    assert not (tmp_path / "x.zip").exists()


def test_retries_on_server_error(tmp_path, server):
    server.files["a.zip"] = PAYLOAD
    server.fail_get["a.zip"] = 2

    result = download_file(server.url("a.zip"), tmp_path / "a.zip", retries=3, backoff=0.0)

    assert result.status == STATUS_DOWNLOADED
    assert (tmp_path / "a.zip").read_bytes() == PAYLOAD


def test_fails_after_exhausting_retries(tmp_path, server):
    server.files["a.zip"] = PAYLOAD
    server.fail_get["a.zip"] = 10

    result = download_file(server.url("a.zip"), tmp_path / "a.zip", retries=2, backoff=0.0)

    assert result.status == STATUS_FAILED
    assert "503" in result.error
    assert not (tmp_path / "a.zip").exists()


# --------------------------------------------------------------------------- #
# run (orquestracao e manifesto)
# --------------------------------------------------------------------------- #


def test_run_downloads_and_writes_manifest(tmp_path, server):
    server.files["2026-11/Empresas0.zip"] = PAYLOAD
    server.files["2026-11/Empresas1.zip"] = PAYLOAD[:500]
    server.files["2026-11/Cnaes.zip"] = b"cnaes"

    code = run("2026-11", ["Empresas", "Cnaes"], **_run_kwargs(tmp_path, server))

    assert code == 0
    manifest = json.loads((tmp_path / "2026-11" / "manifest.json").read_text(encoding="utf-8"))
    names = [f["filename"] for f in manifest["files"]]
    assert names == ["Cnaes.zip", "Empresas0.zip", "Empresas1.zip"]
    assert manifest["total_bytes"] == len(PAYLOAD) + 500 + 5


def test_run_ignores_missing_part_but_fails_when_table_is_empty(tmp_path, server):
    # Empresas tem so a parte 0 (parte 1 ausente = 404, ignorada); Cnaes inexistente = erro
    server.files["2026-11/Empresas0.zip"] = b"x" * 10

    code_ok = run("2026-11", ["Empresas"], **_run_kwargs(tmp_path, server))
    code_missing = run("2026-11", ["Empresas", "Cnaes"], **_run_kwargs(tmp_path, server))

    assert code_ok == 0
    assert code_missing == 1


def test_run_fails_when_no_file_is_found(tmp_path, server):
    assert run("2026-11", ["Empresas"], **_run_kwargs(tmp_path, server)) == 1


def test_manifest_only_uses_local_files(tmp_path, server):
    month_dir = tmp_path / "2026-11"
    month_dir.mkdir()
    (month_dir / "Cnaes.zip").write_bytes(b"cnaes")

    code = run("2026-11", ["Cnaes", "Empresas"], **_run_kwargs(tmp_path, server, manifest_only=True))

    assert code == 0
    manifest = json.loads((month_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [f["filename"] for f in manifest["files"]] == ["Cnaes.zip"]
    assert manifest["files"][0]["sha256"] == _sha(b"cnaes")


def test_manifest_only_fails_when_folder_is_empty(tmp_path, server):
    assert run("2026-11", ["Cnaes"], **_run_kwargs(tmp_path, server, manifest_only=True)) == 1
