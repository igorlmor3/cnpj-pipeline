"""Testes da tarefa que roda o pipeline no ECS Fargate, sem rede, sem AWS e sem executar o pipeline."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from types import SimpleNamespace

import pytest
import requests

from cnpj_pipeline import config, orchestration
from cnpj_pipeline.cloud import task
from cnpj_pipeline.cloud.common import BUCKET_ENV

TODAY = date(2026, 11, 18)
GIB = 1024**3


def disk(free_gib: float):
    return lambda path: SimpleNamespace(free=int(free_gib * GIB))


class Runner:
    """Substitui o subprocess: guarda o comando e o limite de tempo e devolve um codigo de saida."""

    def __init__(self, code: int = 0, *, expire: bool = False) -> None:
        self.code = code
        self.expire = expire
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, argv, *, timeout, check):
        assert check is False
        self.calls.append((list(argv), timeout))
        if self.expire:
            raise subprocess.TimeoutExpired(argv, timeout)
        return SimpleNamespace(returncode=self.code)


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


def env(**extra: str) -> dict[str, str]:
    return {BUCKET_ENV: "bucket-de-teste", **extra}


# --------------------------------------------------------------------------- #
# Meses e comando
# --------------------------------------------------------------------------- #


def test_without_months_the_task_processes_the_current_month():
    assert task.parse_months(None, TODAY) == ["2026-11"]
    assert task.parse_months("  ", TODAY) == ["2026-11"]


def test_months_can_be_separated_by_comma_or_space_and_keep_their_order():
    assert task.parse_months("2026-08, 2026-07 2026-09", TODAY) == ["2026-08", "2026-07", "2026-09"]


@pytest.mark.parametrize("raw", ["2026-13", "26-10", "2026-10-01", "outubro", "2026-07 2026-7"])
def test_an_invalid_month_is_rejected(raw):
    with pytest.raises(task.TaskConfigError, match="mes invalido"):
        task.parse_months(raw, TODAY)


def test_a_repeated_month_is_rejected():
    with pytest.raises(task.TaskConfigError, match="repetido"):
        task.parse_months("2026-07 2026-07", TODAY)


def test_the_command_runs_the_full_flow_with_publishing_and_without_the_site():
    argv = task.orchestration_argv(["2026-09", "2026-10"], force=False)
    assert argv[:3] == [sys.executable, "-m", "cnpj_pipeline.orchestration"]
    args = orchestration.parse_args(argv[3:])  # o comando precisa ser aceito pela propria orquestracao
    assert (args.mode, args.months) == ("full", ["2026-09", "2026-10"])
    assert args.publish and args.skip_site and not args.force


def test_the_command_forwards_force_only_when_asked():
    args = orchestration.parse_args(task.orchestration_argv(["2026-10"], force=True)[3:])
    assert args.force


# --------------------------------------------------------------------------- #
# Execucao
# --------------------------------------------------------------------------- #


def test_a_run_starts_the_orchestration_with_the_default_time_limit(capsys):
    runner = Runner(0)
    code = task.run_task(env(CNPJ_MONTHS="2026-10"), today=TODAY, runner=runner, disk_usage=disk(100))
    assert code == 0
    ((argv, timeout),) = runner.calls
    assert "--publish" in argv and "2026-10" in argv
    assert timeout == task.DEFAULT_TIMEOUT_MINUTES * 60
    out = capsys.readouterr().out
    assert "meses 2026-10" in out and "s3://bucket-de-teste" in out


def test_the_time_limit_comes_from_the_environment():
    runner = Runner(0)
    task.run_task(env(CNPJ_TASK_TIMEOUT_MINUTES="90"), today=TODAY, runner=runner, disk_usage=disk(100))
    assert runner.calls[0][1] == 90 * 60


def test_the_exit_code_of_the_pipeline_becomes_the_exit_code_of_the_task():
    assert task.run_task(env(), today=TODAY, runner=Runner(1), disk_usage=disk(100)) == 1


def test_a_run_that_passes_the_limit_is_stopped_with_the_timeout_code(capsys):
    runner = Runner(expire=True)
    code = task.run_task(env(CNPJ_TASK_TIMEOUT_MINUTES="5"), today=TODAY, runner=runner, disk_usage=disk(100))
    assert code == task.TIMEOUT_EXIT_CODE == 124
    assert "TEMPO ESGOTADO" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "sim", "yes"])
def test_force_is_read_from_the_environment(value):
    runner = Runner(0)
    task.run_task(env(CNPJ_FORCE=value), today=TODAY, runner=runner, disk_usage=disk(100))
    assert "--force" in runner.calls[0][0]


@pytest.mark.parametrize("value", ["", "0", "false", "nao"])
def test_without_force_the_existing_layers_are_kept(value):
    runner = Runner(0)
    task.run_task(env(CNPJ_FORCE=value), today=TODAY, runner=runner, disk_usage=disk(100))
    assert "--force" not in runner.calls[0][0]


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({}, "CNPJ_S3_BUCKET nao esta definido"),
        ({BUCKET_ENV: "   "}, "CNPJ_S3_BUCKET nao esta definido"),
        (env(CNPJ_MONTHS="2026-13"), "mes invalido"),
        (env(CNPJ_TASK_TIMEOUT_MINUTES="muito"), "precisa ser um numero"),
        (env(CNPJ_TASK_TIMEOUT_MINUTES="0"), "maior que zero"),
        (env(CNPJ_TASK_TIMEOUT_MINUTES="-5"), "maior que zero"),
        (env(CNPJ_MIN_FREE_GIB="abc"), "precisa ser um numero"),
    ],
)
def test_a_bad_configuration_stops_before_running_anything(environment, message, capsys):
    runner = Runner(0)
    code = task.run_task(environment, today=TODAY, runner=runner, disk_usage=disk(100))
    assert code == task.CONFIG_EXIT_CODE == 2
    assert runner.calls == []
    err = capsys.readouterr().err
    assert "CONFIGURACAO INVALIDA" in err and message in err


def test_too_little_free_disk_stops_before_downloading_anything(capsys):
    runner = Runner(0)
    code = task.run_task(env(), today=TODAY, runner=runner, disk_usage=disk(20))
    assert code == 2 and runner.calls == []
    err = capsys.readouterr().err
    assert "espaco livre insuficiente" in err and "20.0 GiB" in err and "minimo 60 GiB" in err


def test_the_minimum_free_disk_can_be_changed():
    runner = Runner(0)
    assert task.run_task(env(CNPJ_MIN_FREE_GIB="10"), today=TODAY, runner=runner, disk_usage=disk(20)) == 0


def test_the_work_folder_is_created_before_the_disk_is_checked(data_dir):
    task.run_task(env(), today=TODAY, runner=Runner(0), disk_usage=disk(100))
    assert data_dir.is_dir()


# --------------------------------------------------------------------------- #
# Teste de acesso a Receita
# --------------------------------------------------------------------------- #


class FakeRaw:
    def __init__(self, head: bytes) -> None:
        self.head = head

    def read(self, size, decode_content=True):
        assert size == 4 and decode_content is False  # so os 4 primeiros bytes, sem descompactar
        return self.head


class FakeResponse:
    def __init__(self, head: bytes, status: int = 206, content_type: str = "application/zip") -> None:
        self.raw = FakeRaw(head)
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_probe_succeeds_when_the_first_bytes_are_a_zip(capsys):
    session = FakeSession(FakeResponse(b"PK\x03\x04"))
    code = task.probe("2026-10", url_template=config.URL_TEMPLATE, session=session)
    assert code == 0
    assert "ACESSO OK" in capsys.readouterr().out
    ((url, kwargs),) = session.requests
    assert url.endswith("/2026-10/Cnaes.zip")
    assert kwargs["headers"] == {"Range": "bytes=0-3"} and kwargs["stream"] is True
    assert session.response.closed


def test_probe_is_refused_when_the_server_answers_200_with_an_html_page(capsys):
    session = FakeSession(FakeResponse(b"<!DO", status=200, content_type="text/html"))
    assert task.probe("2026-10", url_template=config.URL_TEMPLATE, session=session) == 1
    err = capsys.readouterr().err
    assert "ACESSO RECUSADO" in err and "HTTP 200" in err and "text/html" in err


def test_probe_reports_a_network_failure(capsys):
    session = FakeSession(error=requests.ConnectionError("sem rota"))
    assert task.probe("2026-10", url_template=config.URL_TEMPLATE, session=session) == 1
    assert "SEM ACESSO: ConnectionError" in capsys.readouterr().err


def test_probe_sends_the_share_token_only_to_the_official_server():
    official = FakeSession(FakeResponse(b"PK\x03\x04"))
    task.probe("2026-10", url_template=config.URL_TEMPLATE, session=official)
    assert official.requests[0][1]["auth"] == (config.SHARE_TOKEN, "")

    mirror = FakeSession(FakeResponse(b"PK\x03\x04"))
    task.probe("2026-10", url_template="https://espelho.example/{month}/{filename}", session=mirror)
    assert mirror.requests[0][1]["auth"] is None


# --------------------------------------------------------------------------- #
# Linha de comando
# --------------------------------------------------------------------------- #


def test_the_run_command_hands_the_environment_to_the_task(monkeypatch):
    seen = {}
    monkeypatch.setattr(task, "run_task", lambda environment, *, today: seen.update(env=environment) or 0)
    assert task.main(["run"], env={"CNPJ_MONTHS": "2026-10"}) == 0
    assert seen["env"] == {"CNPJ_MONTHS": "2026-10"}


def test_the_probe_command_uses_the_month_given_or_the_first_one_of_the_environment(monkeypatch):
    months = []
    monkeypatch.setattr(task, "probe", lambda month, **kwargs: months.append(month) or 0)
    assert task.main(["probe", "--month", "2026-08"], env={}) == 0
    assert task.main(["probe"], env={"CNPJ_MONTHS": "2026-09 2026-10"}) == 0
    assert months == ["2026-08", "2026-09"]


def test_the_probe_command_rejects_a_bad_month(monkeypatch, capsys):
    monkeypatch.setattr(task, "probe", lambda *a, **k: pytest.fail("nao deveria consultar a rede"))
    assert task.main(["probe", "--month", "2026-13"], env={}) == 2
    assert "CONFIGURACAO INVALIDA" in capsys.readouterr().err
    assert task.main(["probe"], env={"CNPJ_MONTHS": "xx"}) == 2
