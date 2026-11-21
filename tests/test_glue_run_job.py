"""Testes do disparador do job na Glue, com um cliente simulado: nada chega a AWS."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "glue_spark"))

import run_job  # noqa: E402

from cnpj_pipeline.cloud import common  # noqa: E402

RUNNING = {"JobRun": {"JobRunState": "RUNNING"}}


def done(state="SUCCEEDED", seconds=600, dpu_seconds=3000.0, error=None):
    run = {"JobRunState": state, "ExecutionTime": seconds}
    if dpu_seconds is not None:
        run["DPUSeconds"] = dpu_seconds
    if error:
        run["ErrorMessage"] = error
    return {"JobRun": run}


def network_error(name: str) -> Exception:
    return type(name, (Exception,), {"__module__": "botocore.exceptions"})("falha simulada")


class FakeGlue:
    def __init__(self, script=None, run_id="jr_abc") -> None:
        self.script = list(script or [done()])
        self.run_id = run_id
        self.started: list[dict] = []
        self.polls = 0

    def start_job_run(self, **kwargs):
        self.started.append(kwargs)
        return {"JobRunId": self.run_id} if self.run_id else {}

    def get_job_run(self, JobName, RunId):
        assert RunId == self.run_id
        self.polls += 1
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("o teste tentou criar um cliente da AWS")

    monkeypatch.setattr(common, "make_client", refuse)
    monkeypatch.delenv("CNPJ_S3_BUCKET", raising=False)
    monkeypatch.delenv("CNPJ_GLUE_JOB", raising=False)
    monkeypatch.setattr(run_job.time, "sleep", lambda seconds: None)


def test_the_verification_points_at_the_gold_published_by_duckdb():
    assert run_job.build_arguments("2026-10", verify_bucket="meu-bucket") == {
        "--month": "2026-10",
        "--verify-dir": "s3://meu-bucket/gold",
    }
    assert run_job.build_arguments("2026-10", verify_bucket=None) == {"--month": "2026-10"}


def test_the_run_starts_with_the_workers_and_arguments_asked():
    glue = FakeGlue()
    run_id = run_job.start(
        glue, job_name="j", workers=5, worker_type="G.1X", arguments={"--month": "2026-10"}
    )
    assert run_id == "jr_abc"
    assert glue.started == [
        {"JobName": "j", "Arguments": {"--month": "2026-10"}, "WorkerType": "G.1X", "NumberOfWorkers": 5}
    ]


def test_a_start_without_a_run_id_is_an_error():
    with pytest.raises(run_job.JobError, match="identificador"):
        run_job.start(FakeGlue(run_id=""), job_name="j", workers=2, worker_type="G.1X", arguments={})


def test_waiting_polls_every_thirty_seconds_until_a_final_state():
    clock = Clock()
    glue = FakeGlue([RUNNING, RUNNING, done("FAILED", error="boom")])
    run = run_job.wait_for_run(
        glue, "j", "jr_abc", timeout_minutes=10, sleep=clock.sleep, monotonic=clock.monotonic
    )
    assert run["JobRunState"] == "FAILED"
    assert clock.sleeps == [30, 30]


def test_a_network_failure_while_waiting_is_retried(capsys):
    glue = FakeGlue([RUNNING, network_error("ConnectTimeoutError"), done()])
    clock = Clock()
    run = run_job.wait_for_run(
        glue, "j", "jr_abc", timeout_minutes=10, sleep=clock.sleep, monotonic=clock.monotonic
    )
    assert run["JobRunState"] == "SUCCEEDED"
    assert "tentando de novo" in capsys.readouterr().err


def test_an_error_that_is_not_transient_is_not_hidden():
    glue = FakeGlue([ValueError("EntityNotFound")])
    with pytest.raises(ValueError):
        run_job.wait_for_run(glue, "j", "jr_abc", timeout_minutes=10, sleep=Clock().sleep)


def test_the_wait_gives_up_after_too_many_failures_and_says_the_run_may_still_go_on():
    glue = FakeGlue([network_error("ConnectTimeoutError")])
    with pytest.raises(run_job.JobError, match="pode continuar rodando"):
        run_job.wait_for_run(
            glue, "j", "jr_abc", timeout_minutes=600, sleep=Clock().sleep, monotonic=Clock().monotonic
        )


def test_the_wait_ends_with_an_error_when_the_run_outlives_the_limit():
    glue = FakeGlue([RUNNING])
    with pytest.raises(run_job.JobError, match="depois de 2 minutos"):
        clock = Clock()
        run_job.wait_for_run(
            glue, "j", "jr_abc", timeout_minutes=2, sleep=clock.sleep, monotonic=clock.monotonic
        )


def test_the_cost_comes_from_the_dpu_seconds_the_glue_reports():
    result = run_job.summarize(done(seconds=600, dpu_seconds=3000.0)["JobRun"], workers=5, worker_type="G.1X")
    assert result["dpu_segundos"] == 3000.0 and result["dpu_segundos_estimados"] is False
    assert result["custo_usd"] == round(3000 / 3600 * 0.44, 4)


def test_without_dpu_seconds_the_cost_is_estimated_with_the_one_minute_minimum():
    result = run_job.summarize(done(seconds=20, dpu_seconds=None)["JobRun"], workers=2, worker_type="G.2X")
    assert result["dpu_segundos_estimados"] is True
    assert result["dpu_segundos"] == 60 * 2 * 2


def test_main_runs_waits_and_reports_time_and_cost(capsys):
    glue = FakeGlue([RUNNING, done(seconds=420, dpu_seconds=2100.0)])
    code = run_job.main(["--month", "2026-10", "--workers", "5"], client=glue)
    out = capsys.readouterr().out
    assert code == 0
    assert "Execucao iniciada: jr_abc (5 workers G.1X, mes 2026-10)" in out
    assert "Tempo de execucao: 420 s" in out
    assert "DPU-segundos: 2100 (informado pela Glue)" in out
    assert "Custo a preco de lista: US$ 0.2567" in out


def test_main_passes_the_verification_directory_when_asked():
    glue = FakeGlue()
    assert run_job.main(["--month", "2026-10", "--verify", "--bucket", "b"], client=glue) == 0
    assert glue.started[0]["Arguments"]["--verify-dir"] == "s3://b/gold"


def test_main_returns_one_and_shows_the_error_when_the_job_fails(capsys):
    glue = FakeGlue([done("FAILED", error="OutOfMemoryError")])
    assert run_job.main(["--month", "2026-10"], client=glue) == 1
    assert "OutOfMemoryError" in capsys.readouterr().err


def test_main_with_no_wait_returns_right_after_starting(capsys):
    glue = FakeGlue()
    assert run_job.main(["--month", "2026-10", "--no-wait"], client=glue) == 0
    assert glue.polls == 0


def test_main_ends_with_code_three_and_the_command_to_check_the_run(capsys):
    glue = FakeGlue([network_error("ConnectTimeoutError")])
    assert run_job.main(["--month", "2026-10"], client=glue) == 3
    err = capsys.readouterr().err
    assert "aws glue get-job-run --job-name cnpj-gold-spark --run-id jr_abc" in err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--month", "2026-1"], "AAAA-MM"),
        (["--month", "2026-10", "--workers", "1"], "pelo menos 2"),
        (["--month", "2026-10", "--timeout-minutes", "0"], "maior que zero"),
        (["--month", "2026-10", "--verify"], "bucket"),
    ],
)
def test_invalid_arguments_are_refused_before_anything_starts(argv, message, capsys):
    glue = FakeGlue()
    assert run_job.main(argv, client=glue) == 2
    assert message in capsys.readouterr().err
    assert glue.started == []
