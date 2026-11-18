"""Testes da configuracao compartilhada da nuvem: perfil local ou papel IAM da tarefa."""

from __future__ import annotations

import pytest

from cnpj_pipeline.cloud import common

ECS_URI = "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv(common.PROFILE_ENV, raising=False)
    monkeypatch.delenv(ECS_URI, raising=False)


def test_on_a_local_machine_the_default_profile_is_used():
    assert common.default_profile() == common.DEFAULT_PROFILE == "cnpj-publisher"


def test_the_profile_can_be_chosen_in_the_environment(monkeypatch):
    monkeypatch.setenv(common.PROFILE_ENV, "outro-perfil")
    assert common.default_profile() == "outro-perfil"


@pytest.mark.parametrize("value", ["none", "None", "NONE", "", "  "])
def test_none_or_empty_forces_the_default_credential_chain(monkeypatch, value):
    monkeypatch.setenv(common.PROFILE_ENV, value)
    assert common.default_profile() is None


def test_inside_ecs_no_profile_is_used_so_the_task_role_applies(monkeypatch):
    monkeypatch.setenv(ECS_URI, "/v2/credentials/abc")
    assert common.default_profile() is None


def test_an_explicit_profile_wins_even_inside_ecs(monkeypatch):
    monkeypatch.setenv(ECS_URI, "/v2/credentials/abc")
    monkeypatch.setenv(common.PROFILE_ENV, "perfil-explicito")
    assert common.default_profile() == "perfil-explicito"


def test_the_bucket_and_region_come_from_the_environment(monkeypatch):
    monkeypatch.delenv(common.BUCKET_ENV, raising=False)
    monkeypatch.delenv(common.REGION_ENV, raising=False)
    assert common.default_bucket() is None and common.default_region() == "us-east-1"
    monkeypatch.setenv(common.BUCKET_ENV, "meu-bucket")
    monkeypatch.setenv(common.REGION_ENV, "sa-east-1")
    assert common.default_bucket() == "meu-bucket" and common.default_region() == "sa-east-1"
