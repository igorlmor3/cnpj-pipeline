"""Configuracao da API, lida de variaveis de ambiente da funcao (nunca de arquivo, nunca com segredo).

Os segredos (chave do modelo, sal dos hashes) ficam em parametros cifrados; aqui so entram os NOMES
desses parametros.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cnpj_pipeline.agent.llm import DEFAULT_MODEL

PREFIX = "CNPJ_API_"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
ORIGIN_RE = re.compile(r"https://[A-Za-z0-9.-]+(:\d+)?|http://localhost(:\d+)?")
DEFAULT_COLUMNS_FILE = Path(__file__).with_name("agent_columns.json")

# Limites padrao. O gasto mensal da aplicacao fica abaixo do limite do workspace no provedor (US$ 5),
# que e a barreira que vale; este e a segunda.
DEFAULT_PER_MINUTE = 3
DEFAULT_PER_DAY = 15
DEFAULT_GLOBAL_PER_DAY = 150
DEFAULT_MONTHLY_BUDGET_USD = 4.0
DEFAULT_DEADLINE_S = 40.0
DEFAULT_ATHENA_TIMEOUT_S = 25.0
DEFAULT_LLM_TIMEOUT_S = 20.0


class ConfigError(ValueError):
    """A configuracao da funcao esta incompleta ou invalida. A mensagem nao contem valores."""


@dataclass(frozen=True)
class ApiConfig:
    month: str
    database: str
    workgroup: str
    table: str
    allowed_origins: tuple[str, ...]
    param_prefix: str
    per_minute: int = DEFAULT_PER_MINUTE
    per_day: int = DEFAULT_PER_DAY
    global_per_day: int = DEFAULT_GLOBAL_PER_DAY
    monthly_budget_usd: float = DEFAULT_MONTHLY_BUDGET_USD
    deadline_s: float = DEFAULT_DEADLINE_S
    athena_timeout_s: float = DEFAULT_ATHENA_TIMEOUT_S
    llm_timeout_s: float = DEFAULT_LLM_TIMEOUT_S
    model: str = DEFAULT_MODEL
    columns_file: Path = DEFAULT_COLUMNS_FILE

    @property
    def param_enabled(self) -> str:
        return f"{self.param_prefix}/enabled"

    @property
    def param_api_key(self) -> str:
        return f"{self.param_prefix}/anthropic-api-key"

    @property
    def param_salt(self) -> str:
        return f"{self.param_prefix}/salt"

    def __post_init__(self) -> None:
        if not MONTH_RE.fullmatch(self.month):
            raise ConfigError("CNPJ_API_MONTH deve ter o formato AAAA-MM")
        for name in ("database", "workgroup", "table", "param_prefix"):
            if not getattr(self, name):
                raise ConfigError(f"{PREFIX}{name.upper()} e obrigatoria")
        if not self.param_prefix.startswith("/"):
            raise ConfigError("CNPJ_API_PARAM_PREFIX deve comecar com /")
        for name in ("per_minute", "per_day", "global_per_day"):
            if getattr(self, name) < 1:
                raise ConfigError(f"{name} deve ser pelo menos 1")
        for name in ("monthly_budget_usd", "deadline_s", "athena_timeout_s", "llm_timeout_s"):
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name} deve ser maior que zero")
        for origin in self.allowed_origins:
            if not ORIGIN_RE.fullmatch(origin):
                raise ConfigError("origem permitida invalida: use https://... (sem curinga)")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ApiConfig:
        def get(name: str, default: str | None = None) -> str:
            value = env.get(PREFIX + name, default)
            if value is None or value == "":
                raise ConfigError(f"{PREFIX}{name} e obrigatoria")
            return value

        def number(name: str, default: float, kind: type) -> float:
            raw = env.get(PREFIX + name)
            if raw is None or raw == "":
                return default
            try:
                return kind(raw)
            except ValueError as exc:
                raise ConfigError(f"{PREFIX}{name} deve ser numerica") from exc

        origins = tuple(o.strip() for o in env.get(PREFIX + "ALLOWED_ORIGINS", "").split(",") if o.strip())
        return cls(
            month=get("MONTH"),
            database=get("DATABASE", "cnpj"),
            workgroup=get("WORKGROUP"),
            table=get("TABLE"),
            allowed_origins=origins,
            param_prefix=get("PARAM_PREFIX"),
            per_minute=int(number("PER_MINUTE", DEFAULT_PER_MINUTE, int)),
            per_day=int(number("PER_DAY", DEFAULT_PER_DAY, int)),
            global_per_day=int(number("GLOBAL_PER_DAY", DEFAULT_GLOBAL_PER_DAY, int)),
            monthly_budget_usd=float(number("MONTHLY_BUDGET_USD", DEFAULT_MONTHLY_BUDGET_USD, float)),
            deadline_s=float(number("DEADLINE_S", DEFAULT_DEADLINE_S, float)),
            model=env.get(PREFIX + "MODEL") or DEFAULT_MODEL,
            columns_file=Path(env.get(PREFIX + "COLUMNS_FILE") or DEFAULT_COLUMNS_FILE),
        )
