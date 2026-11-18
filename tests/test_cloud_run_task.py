"""Testes do disparador da tarefa no ECS, com um cliente simulado: nada chega a AWS."""

from __future__ import annotations

import pytest

from cnpj_pipeline.cloud import common, run_task

ARN = "arn:aws:ecs:us-east-1:123456789012:task/cnpj-pipeline/abc123"
ARGS = [
    "--cluster", "cnpj-pipeline",
    "--task-definition", "cnpj-pipeline:3",
    "--subnets", "subnet-a, subnet-b",
    "--security-group", "sg-1",
]  # fmt: skip


class FakeWaiter:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def wait(self, **kwargs) -> None:
        self.kwargs = kwargs


class FakeEcs:
    def __init__(self, *, failures=None, tasks=None, exit_code=0, stopped_reason=None) -> None:
        self.failures = failures or []
        self.tasks = [{"taskArn": ARN}] if tasks is None else tasks
        self.exit_code = exit_code
        self.stopped_reason = stopped_reason
        self.waiter = FakeWaiter()
        self.started: list[dict] = []

    def run_task(self, **kwargs):
        self.started.append(kwargs)
        return {"tasks": self.tasks, "failures": self.failures}

    def get_waiter(self, name):
        assert name == "tasks_stopped"
        return self.waiter

    def describe_tasks(self, cluster, tasks):
        container = {} if self.exit_code is None else {"exitCode": self.exit_code}
        return {"tasks": [{"containers": [container], "stoppedReason": self.stopped_reason}]}


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("o teste tentou criar um cliente da AWS")

    monkeypatch.setattr(common, "make_client", refuse)
    for name in (
        "CNPJ_ECS_CLUSTER",
        "CNPJ_ECS_TASK_DEFINITION",
        "CNPJ_ECS_SUBNETS",
        "CNPJ_ECS_SECURITY_GROUP",
    ):
        monkeypatch.delenv(name, raising=False)


def test_lists_accept_commas_semicolons_and_stray_spaces():
    assert run_task.split_list("a, b;c ,") == ["a", "b", "c"]
    assert run_task.split_list(None) == []
    assert run_task.split_list("") == []


def test_the_overrides_carry_the_months_and_name_the_container_of_the_task():
    overrides = run_task.build_overrides(
        months=["2026-09", "2026-10"], probe=False, force=False, timeout_minutes=None
    )
    (container,) = overrides["containerOverrides"]
    assert container["name"] == run_task.CONTAINER_NAME == "pipeline"
    assert container["environment"] == [{"name": "CNPJ_MONTHS", "value": "2026-09 2026-10"}]
    assert "command" not in container


def test_force_and_the_time_limit_become_environment_variables():
    overrides = run_task.build_overrides(months=["2026-10"], probe=False, force=True, timeout_minutes=120)
    environment = {item["name"]: item["value"] for item in overrides["containerOverrides"][0]["environment"]}
    assert environment == {"CNPJ_MONTHS": "2026-10", "CNPJ_FORCE": "1", "CNPJ_TASK_TIMEOUT_MINUTES": "120"}


def test_the_access_test_replaces_the_command_of_the_container():
    overrides = run_task.build_overrides(
        months=["2026-08", "2026-09"], probe=True, force=False, timeout_minutes=None
    )
    command = overrides["containerOverrides"][0]["command"]
    assert command == ["python", "-m", "cnpj_pipeline.cloud.task", "probe", "--month", "2026-08"]


def test_a_task_starts_on_fargate_with_a_public_ip_and_a_single_copy():
    ecs = FakeEcs()
    arn = run_task.start(
        ecs, cluster="c", task_definition="td", subnets=["s1", "s2"], security_group="sg", overrides={"x": 1}
    )
    assert arn == ARN
    (call,) = ecs.started
    assert call["launchType"] == "FARGATE" and call["count"] == 1 and call["overrides"] == {"x": 1}
    network = call["networkConfiguration"]["awsvpcConfiguration"]
    assert network == {"subnets": ["s1", "s2"], "securityGroups": ["sg"], "assignPublicIp": "ENABLED"}


def test_a_refusal_from_ecs_becomes_a_clear_error():
    ecs = FakeEcs(failures=[{"reason": "RESOURCE:ENI", "detail": "sem enderecos"}], tasks=[])
    with pytest.raises(run_task.LaunchError, match=r"recusou iniciar.*RESOURCE:ENI \(sem enderecos\)"):
        run_task.start(
            ecs, cluster="c", task_definition="td", subnets=["s"], security_group="sg", overrides={}
        )


@pytest.mark.parametrize("tasks", [[], [{"taskArn": "a"}, {"taskArn": "b"}]])
def test_anything_other_than_one_started_task_is_an_error(tasks):
    with pytest.raises(run_task.LaunchError, match="resposta inesperada"):
        run_task.start(
            FakeEcs(tasks=tasks),
            cluster="c",
            task_definition="td",
            subnets=["s"],
            security_group="sg",
            overrides={},
        )


def test_waiting_returns_the_exit_code_of_the_container():
    ecs = FakeEcs(exit_code=3)
    assert run_task.wait_for_exit(ecs, "c", ARN, timeout_minutes=10) == 3
    wait = ecs.waiter.kwargs
    assert wait["cluster"] == "c" and wait["tasks"] == [ARN]
    assert wait["WaiterConfig"] == {"Delay": 30, "MaxAttempts": 20}


def test_a_task_that_stops_without_an_exit_code_counts_as_a_failure(capsys):
    ecs = FakeEcs(exit_code=None, stopped_reason="Task failed to start")
    assert run_task.wait_for_exit(ecs, "c", ARN, timeout_minutes=1) == 1
    assert "Task failed to start" in capsys.readouterr().err


def test_main_starts_the_task_and_returns_without_waiting(capsys):
    ecs = FakeEcs()
    assert run_task.main([*ARGS, "--month", "2026-10"], client=ecs) == 0
    out = capsys.readouterr().out
    assert "Tarefa iniciada (pipeline de 2026-10): abc123" in out
    assert ecs.waiter.kwargs == {}  # nao esperou
    call = ecs.started[0]
    assert call["cluster"] == "cnpj-pipeline" and call["taskDefinition"] == "cnpj-pipeline:3"
    assert call["networkConfiguration"]["awsvpcConfiguration"]["subnets"] == ["subnet-a", "subnet-b"]


def test_main_with_wait_returns_the_exit_code_of_the_pipeline(capsys):
    ecs = FakeEcs(exit_code=1)
    assert run_task.main([*ARGS, "--month", "2026-10", "--wait"], client=ecs) == 1
    assert "terminou com codigo 1" in capsys.readouterr().out
    assert run_task.main([*ARGS, "--month", "2026-10", "--wait"], client=FakeEcs(exit_code=0)) == 0


def test_main_waits_for_the_time_limit_asked_plus_a_margin():
    ecs = FakeEcs()
    run_task.main([*ARGS, "--month", "2026-10", "--wait", "--timeout-minutes", "50"], client=ecs)
    assert ecs.waiter.kwargs["WaiterConfig"]["MaxAttempts"] == (60 * 60) // 30


def test_main_runs_the_access_test_instead_of_the_pipeline(capsys):
    ecs = FakeEcs()
    assert run_task.main([*ARGS, "--month", "2026-10", "--probe"], client=ecs) == 0
    assert "teste de acesso" in capsys.readouterr().out
    assert "command" in ecs.started[0]["overrides"]["containerOverrides"][0]


def test_the_task_data_can_come_from_environment_variables(monkeypatch):
    monkeypatch.setenv("CNPJ_ECS_CLUSTER", "c-env")
    monkeypatch.setenv("CNPJ_ECS_TASK_DEFINITION", "td-env")
    monkeypatch.setenv("CNPJ_ECS_SUBNETS", "subnet-x,subnet-y")
    monkeypatch.setenv("CNPJ_ECS_SECURITY_GROUP", "sg-env")
    ecs = FakeEcs()
    assert run_task.main(["--month", "2026-10"], client=ecs) == 0
    call = ecs.started[0]
    assert (call["cluster"], call["taskDefinition"]) == ("c-env", "td-env")
    assert call["networkConfiguration"]["awsvpcConfiguration"]["securityGroups"] == ["sg-env"]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--month", "2026-10"], "informe --cluster, --task-definition, --subnets, --security-group"),
        ([*ARGS, "--month", "2026-13"], "mes invalido"),
        ([*ARGS, "--month", "2026-10", "--timeout-minutes", "0"], "maior que zero"),
    ],
)
def test_invalid_arguments_are_refused_before_anything_starts(argv, message, capsys):
    ecs = FakeEcs()
    assert run_task.main(argv, client=ecs) == 2
    assert message in capsys.readouterr().err
    assert ecs.started == []


def test_an_ecs_refusal_ends_with_exit_code_two_and_the_reason(capsys):
    ecs = FakeEcs(failures=[{"reason": "MISSING"}], tasks=[])
    assert run_task.main([*ARGS, "--month", "2026-10"], client=ecs) == 2
    assert "MISSING" in capsys.readouterr().err
