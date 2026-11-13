"""Configuracao e sessao compartilhadas pelos modulos de nuvem."""

from __future__ import annotations

import os
from typing import Any

DEFAULT_REGION = "us-east-1"
DEFAULT_PROFILE = "cnpj-publisher"
DEFAULT_DATABASE = "cnpj"
DEFAULT_WORKGROUP = "cnpj-pipeline"

BUCKET_ENV = "CNPJ_S3_BUCKET"
PROFILE_ENV = "CNPJ_AWS_PROFILE"
REGION_ENV = "CNPJ_AWS_REGION"


class CloudConfigError(RuntimeError):
    """Configuracao de nuvem ausente ou invalida."""


def default_bucket() -> str | None:
    return os.environ.get(BUCKET_ENV) or None


def default_profile() -> str:
    return os.environ.get(PROFILE_ENV, DEFAULT_PROFILE)


def default_region() -> str:
    return os.environ.get(REGION_ENV, DEFAULT_REGION)


def make_client(service: str, *, profile: str | None, region: str) -> Any:
    """Cria um cliente boto3. O boto3 e opcional: so e exigido quando a nuvem e usada."""
    try:
        import boto3
    except ImportError as exc:
        raise CloudConfigError('boto3 nao esta instalado. Rode: python -m pip install -e ".[aws]"') from exc
    session = boto3.Session(profile_name=profile, region_name=region)
    return session.client(service)
