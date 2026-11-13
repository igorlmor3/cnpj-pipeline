from __future__ import annotations

from pathlib import Path

import pytest

from cnpj_pipeline.cloud import publish as pub
from cnpj_pipeline.cloud.common import BUCKET_ENV

MONTH = "2026-10"
BUCKET = "bucket-de-teste"


class FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    """Cliente S3 minimo em memoria: guarda tamanho e metadados por chave."""

    def __init__(self, *, truncate_uploads: bool = False) -> None:
        self.objects: dict[str, dict] = {}
        self.upload_calls: list[str] = []
        self.truncate_uploads = truncate_uploads

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise FakeClientError("404")
        obj = self.objects[Key]
        return {"ContentLength": obj["size"], "Metadata": obj["metadata"]}

    def upload_file(self, filename, Bucket, Key, ExtraArgs=None):  # noqa: N803
        size = Path(filename).stat().st_size
        self.upload_calls.append(Key)
        self.objects[Key] = {
            "size": size - 1 if self.truncate_uploads else size,
            "metadata": (ExtraArgs or {}).get("Metadata", {}),
        }


def make_tree(data: Path) -> None:
    files = {
        f"silver/empresas/reference_month={MONTH}/empresas.parquet": b"empresas-v1",
        f"silver/socios/reference_month={MONTH}/socios.parquet": b"socios-v1",
        "silver/empresas/reference_month=2026-09/empresas.parquet": b"mes-antigo",
        f"silver/_quarantine/empresas/reference_month={MONTH}/empresas.parquet": b"quarentena",
        f"silver/_manifest_{MONTH}.json": b"{}",
        "silver/_manifest_2026-09.json": b"{}",
        f"bronze/empresas/reference_month={MONTH}/Empresas0.parquet": b"bronze",
        f"bronze/_rejects/empresas/reference_month={MONTH}/r.parquet": b"rejeitos",
        f"gold/resumo/reference_month={MONTH}/resumo.parquet": b"gold",
    }
    for rel, content in files.items():
        path = data / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


@pytest.fixture
def data(tmp_path):
    make_tree(tmp_path)
    return tmp_path


def keys(files):
    return [key for _, key in files]


def test_collect_silver_only_month_and_manifest(data):
    files = pub.collect_files(data, "silver", MONTH)
    assert keys(files) == [
        f"silver/_manifest_{MONTH}.json",
        f"silver/empresas/reference_month={MONTH}/empresas.parquet",
        f"silver/socios/reference_month={MONTH}/socios.parquet",
    ]


def test_quarantine_and_rejects_only_when_requested(data):
    default = keys(pub.collect_files(data, "silver", MONTH)) + keys(pub.collect_files(data, "bronze", MONTH))
    assert not any("_quarantine" in k or "_rejects" in k for k in default)

    silver = keys(pub.collect_files(data, "silver", MONTH, include_quarantine=True))
    bronze = keys(pub.collect_files(data, "bronze", MONTH, include_quarantine=True))
    assert f"silver/_quarantine/empresas/reference_month={MONTH}/empresas.parquet" in silver
    assert f"bronze/_rejects/empresas/reference_month={MONTH}/r.parquet" in bronze


def test_table_filter_and_missing_layer(data):
    only = keys(pub.collect_files(data, "silver", MONTH, tables=["Socios"]))
    # o manifesto do mes acompanha qualquer filtro de tabelas
    assert only == [
        f"silver/_manifest_{MONTH}.json",
        f"silver/socios/reference_month={MONTH}/socios.parquet",
    ]
    assert pub.collect_files(data, "inexistente", MONTH) == []


def test_publish_uploads_then_skips_identical_files(data):
    client = FakeS3()
    files = pub.collect_files(data, "silver", MONTH)

    first = pub.publish(client, BUCKET, files)
    assert len(first.uploaded) == 3 and not first.skipped
    assert first.bytes_uploaded == sum(p.stat().st_size for p, _ in files)

    client.upload_calls.clear()
    second = pub.publish(client, BUCKET, files)
    assert not second.uploaded and len(second.skipped) == 3
    assert client.upload_calls == []


def test_changed_content_with_same_size_is_reuploaded(data):
    client = FakeS3()
    files = pub.collect_files(data, "silver", MONTH, tables=["empresas"])
    pub.publish(client, BUCKET, files)

    (data / f"silver/empresas/reference_month={MONTH}/empresas.parquet").write_bytes(
        b"empresas-v2"
    )  # mesmo tamanho
    again = pub.publish(client, BUCKET, files)
    assert again.uploaded == [f"silver/empresas/reference_month={MONTH}/empresas.parquet"]
    assert again.skipped == [f"silver/_manifest_{MONTH}.json"]


def test_dry_run_uploads_nothing(data):
    client = FakeS3()
    result = pub.publish(client, BUCKET, pub.collect_files(data, "silver", MONTH), dry_run=True)
    assert len(result.planned) == 3 and not result.uploaded
    assert client.objects == {}


def test_size_mismatch_after_upload_fails(data):
    client = FakeS3(truncate_uploads=True)
    with pytest.raises(pub.PublishError):
        pub.publish(client, BUCKET, pub.collect_files(data, "silver", MONTH))


def test_unexpected_head_errors_are_not_swallowed(data):
    class Broken(FakeS3):
        def head_object(self, Bucket, Key):  # noqa: N803
            raise FakeClientError("AccessDenied")

    with pytest.raises(FakeClientError):
        pub.publish(Broken(), BUCKET, pub.collect_files(data, "silver", MONTH))


def test_cli_end_to_end_with_fake_client(data):
    client = FakeS3()
    argv = ["--month", MONTH, "--bucket", BUCKET, "--data-dir", str(data), "--layers", "silver", "gold"]
    assert pub.main(argv, client=client) == 0
    assert f"gold/resumo/reference_month={MONTH}/resumo.parquet" in client.objects
    assert f"silver/empresas/reference_month={MONTH}/empresas.parquet" in client.objects


def test_cli_validations(data, monkeypatch):
    monkeypatch.delenv(BUCKET_ENV, raising=False)
    assert pub.main(["--month", "x", "--bucket", BUCKET], client=FakeS3()) == 2
    assert pub.main(["--month", MONTH], client=FakeS3()) == 2  # sem bucket
    argv = ["--month", "2030-01", "--bucket", BUCKET, "--data-dir", str(data)]
    assert pub.main(argv, client=FakeS3()) == 1  # nada para publicar
