"""Testes do pacote da funcao: conteudo do zip, determinismo, colunas e coerencia com o Terraform."""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from helpers import MONTH
from test_gold import build_silver, run_gold

from cnpj_pipeline.api import package
from cnpj_pipeline.api.columns import load_columns
from cnpj_pipeline.api.config import ApiConfig

REPO = Path(__file__).resolve().parents[1]
INFRA = REPO / "infra"


@pytest.fixture(scope="module")
def gold_root(tmp_path_factory):
    base = tmp_path_factory.mktemp("pkg")
    build_silver(base)
    assert run_gold(base) == 0
    return base / "gold"


def fake_install(target: Path) -> None:
    """Imita o que o pip deixa no destino: pacotes, bytecode e scripts da linha de comando."""
    (target / "anthropic").mkdir(parents=True)
    (target / "anthropic" / "__init__.py").write_text("VERSION = 'falsa'\n", encoding="utf-8")
    (target / "anthropic" / "__pycache__").mkdir()
    (target / "anthropic" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
    (target / "bin").mkdir()
    (target / "bin" / "httpx").write_text("#!/usr/bin/python\n", encoding="utf-8")


def names(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return zf.namelist()


def test_pip_installs_linux_wheels_for_the_lambda_runtime_only(tmp_path):
    command = package.pip_command(tmp_path)
    assert command[:3] == [sys.executable, "-m", "pip"]
    assert "--only-binary=:all:" in command  # nunca compila nada na maquina de quem gera o pacote
    assert command[command.index("--python-version") + 1] == "3.12"
    assert command[command.index("--target") + 1] == str(tmp_path)
    platforms = [command[i + 1] for i, c in enumerate(command) if c == "--platform"]
    assert platforms and all("x86_64" in p and p.startswith("manylinux") for p in platforms)
    assert command[-len(package.LAMBDA_REQUIREMENTS) :] == list(package.LAMBDA_REQUIREMENTS)
    assert not any(r.startswith(("boto3", "duckdb")) for r in package.LAMBDA_REQUIREMENTS)


def test_the_zip_has_the_code_the_dependencies_and_the_columns_and_nothing_else(gold_root, tmp_path):
    out = tmp_path / "out" / "api.zip"
    digest = package.build(MONTH, gold_root, out, install=fake_install)
    members = names(out)

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert "cnpj_pipeline/api/handler.py" in members
    assert "cnpj_pipeline/api/agent_columns.json" in members
    assert "cnpj_pipeline/agent/ask.py" in members
    assert "anthropic/__init__.py" in members
    assert members == sorted(members)
    assert not [m for m in members if "__pycache__" in m or m.endswith(".pyc")]
    assert not [m for m in members if m.startswith("bin/")]
    assert not [m for m in members if re.search(r"(^|/)(\.env|data|tests?|docs|infra)(/|$)", m)]
    assert not [m for m in members if m.endswith((".parquet", ".csv", ".zip", ".duckdb"))]
    assert not out.with_suffix(".tmp").exists()


def test_the_same_content_gives_the_same_zip_bytes(gold_root, tmp_path):
    first = package.build(MONTH, gold_root, tmp_path / "a.zip", install=fake_install)
    second = package.build(MONTH, gold_root, tmp_path / "b.zip", install=fake_install)
    assert first == second
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "a.zip") as zf:
        assert {i.date_time for i in zf.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        assert {i.external_attr >> 16 for i in zf.infolist()} == {0o644}


def test_a_changed_file_changes_the_zip(gold_root, tmp_path):
    def install_other(target: Path) -> None:
        fake_install(target)
        (target / "anthropic" / "__init__.py").write_text("VERSION = 'outra'\n", encoding="utf-8")

    a = package.build(MONTH, gold_root, tmp_path / "a.zip", install=fake_install)
    b = package.build(MONTH, gold_root, tmp_path / "b.zip", install=install_other)
    assert a != b


def test_the_columns_file_in_the_zip_is_the_requested_month_and_holds_no_data(gold_root, tmp_path):
    out = tmp_path / "api.zip"
    package.build(MONTH, gold_root, out, install=fake_install)
    with zipfile.ZipFile(out) as zf:
        zf.extract("cnpj_pipeline/api/agent_columns.json", tmp_path / "x")
    columns = load_columns(tmp_path / "x" / "cnpj_pipeline" / "api" / "agent_columns.json", MONTH)
    assert "gold_mart_simples_mei_uf" in columns or "mart_simples_mei_uf" in columns
    text = (tmp_path / "x" / "cnpj_pipeline" / "api" / "agent_columns.json").read_text(encoding="utf-8")
    assert "reference_month" in text


def test_a_columns_file_left_in_the_source_tree_never_goes_into_the_zip(gold_root, tmp_path, monkeypatch):
    fake_pkg = tmp_path / "src" / "cnpj_pipeline"
    (fake_pkg / "api").mkdir(parents=True)
    (fake_pkg / "api" / "agent_columns.json").write_text('{"mes": "1999-01"}', encoding="utf-8")
    (fake_pkg / "api" / "handler.py").write_text("x = 1\n", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    package.stage(stage, install=fake_install, package_dir=fake_pkg)
    assert not (stage / "cnpj_pipeline" / "api" / "agent_columns.json").exists()
    assert (stage / "cnpj_pipeline" / "api" / "handler.py").exists()


def test_dependencies_cannot_shadow_the_project_package(tmp_path):
    def evil(target: Path) -> None:
        (target / "cnpj_pipeline").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="cnpj_pipeline"):
        package.stage(tmp_path, install=evil)


def test_an_oversized_zip_is_refused_and_leaves_no_file(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "a.bin").write_bytes(b"x" * 4096)
    monkeypatch.setattr(package, "MAX_ZIP_BYTES", 10)
    out = tmp_path / "out.zip"
    with pytest.raises(RuntimeError, match="teto"):
        package.write_zip(stage, out)
    assert not out.exists() and not out.with_suffix(".tmp").exists()


def test_the_extracted_zip_imports_the_handler_and_loads_its_columns(gold_root, tmp_path):
    out = tmp_path / "api.zip"
    package.build(MONTH, gold_root, out, install=fake_install)
    root = tmp_path / "unzipped"
    with zipfile.ZipFile(out) as zf:
        zf.extractall(root)
    code = (
        "import sys\n"
        "for name in ('duckdb', 'anthropic', 'boto3'):\n"
        "    sys.modules[name] = None\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "import cnpj_pipeline.api.handler as h\n"
        "from cnpj_pipeline.api.config import DEFAULT_COLUMNS_FILE\n"
        "from cnpj_pipeline.api.columns import load_columns\n"
        f"assert h.__file__.startswith({str(root)!r}), h.__file__\n"
        f"assert load_columns(DEFAULT_COLUMNS_FILE, {MONTH!r})\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr


def test_main_reports_failures_without_a_traceback(tmp_path, capsys):
    code = package.main(
        ["--month", MONTH, "--gold-dir", str(tmp_path / "nao-existe"), "--out", str(tmp_path / "x.zip")]
    )
    assert code == 1
    assert "Falha ao gerar o pacote" in capsys.readouterr().err
    assert not (tmp_path / "x.zip").exists()


# --------------------------------------------------------------------------- #
# Terraform: as variaveis de ambiente e as permissoes combinam com o codigo
# --------------------------------------------------------------------------- #

MODULE = INFRA / "modules" / "api"


def test_every_env_var_set_by_terraform_is_one_the_function_reads():
    declared = set(re.findall(r"\bCNPJ_API_[A-Z_]+\b", (MODULE / "main.tf").read_text(encoding="utf-8")))
    source = (REPO / "src" / "cnpj_pipeline" / "api" / "config.py").read_text(encoding="utf-8")
    read = {"CNPJ_API_" + m for m in re.findall(r'(?:get|number)\("([A-Z_]+)"', source)}
    read |= {"CNPJ_API_" + m for m in re.findall(r'PREFIX \+ "([A-Z_]+)"', source)}
    assert declared and declared <= read, declared - read


def test_the_required_settings_are_all_provided_by_terraform():
    declared = set(re.findall(r"\bCNPJ_API_([A-Z_]+)\b", (MODULE / "main.tf").read_text(encoding="utf-8")))
    env = {f"CNPJ_API_{name}": "valor" for name in declared}
    env.update({"CNPJ_API_MONTH": MONTH, "CNPJ_API_ALLOWED_ORIGINS": "https://exemplo.github.io"})
    env.update({"CNPJ_API_PER_MINUTE": "3", "CNPJ_API_PER_DAY": "15", "CNPJ_API_GLOBAL_PER_DAY": "150"})
    env.update({"CNPJ_API_MONTHLY_BUDGET_USD": "4", "CNPJ_API_PARAM_PREFIX": "/cnpj-pipeline/api"})
    assert ApiConfig.from_env(env).table == "valor"


def test_parameter_names_in_terraform_match_the_function():
    text = (MODULE / "main.tf").read_text(encoding="utf-8")
    config = ApiConfig.from_env(
        {
            "CNPJ_API_MONTH": MONTH,
            "CNPJ_API_WORKGROUP": "w",
            "CNPJ_API_TABLE": "t",
            "CNPJ_API_PARAM_PREFIX": "/p",
        }
    )
    for attr in ("param_enabled", "param_salt"):
        suffix = getattr(config, attr).removeprefix("/p")
        assert f'"${{var.param_prefix}}{suffix}"' in text
    assert "anthropic-api-key" not in text  # a chave do provedor nunca passa pelo Terraform


def code_lines(path: Path) -> str:
    """O texto do arquivo sem os comentarios."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_terraform_never_holds_the_provider_key_or_a_wildcard_origin():
    for path in INFRA.rglob("*.tf"):
        text = code_lines(path)
        assert "sk-ant" not in text and "ANTHROPIC_API_KEY" not in text, path
        assert not re.search(r"allow_origins\s*=\s*\[[^\]]*\"\*\"", text), path
    variables = code_lines(INFRA / "variables.tf")
    block = variables[variables.index('variable "api_allowed_origins"') :]
    assert '"*"' not in block[: block.index("\n}")]


def test_the_function_can_read_the_gold_and_not_the_silver():
    text = code_lines(MODULE / "main.tf")
    assert '"${var.bucket_arn}/${var.gold_prefix}*"' in text
    assert "silver" not in text and '"${var.bucket_arn}/*"' not in text
    assert 'default     = "gold/"' in (MODULE / "variables.tf").read_text(encoding="utf-8")


def test_the_switch_is_created_off_and_terraform_does_not_flip_it_back():
    text = (MODULE / "main.tf").read_text(encoding="utf-8")
    block = text[text.index('resource "aws_ssm_parameter" "enabled"') :]
    block = block[: block.index("\n}\n") + 3]
    assert 'value       = "false"' in block and "ignore_changes = [value]" in block


def test_the_api_is_off_by_default_and_the_zip_is_never_versioned():
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    block = variables[variables.index('variable "api_enabled"') :]
    assert "default     = false" in block[: block.index("}")]
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^build/$", ignored, re.MULTILINE)


def test_the_function_timeout_is_longer_than_the_agent_deadline():
    variables = (MODULE / "variables.tf").read_text(encoding="utf-8")
    timeout = float(re.search(r'variable "timeout_s".*?default\s*=\s*(\d+)', variables, re.S).group(1))
    from cnpj_pipeline.api.config import DEFAULT_ATHENA_TIMEOUT_S, DEFAULT_DEADLINE_S, DEFAULT_LLM_TIMEOUT_S

    # depois do prazo nao comeca chamada nova; a em andamento ainda pode levar o seu proprio limite
    assert timeout > DEFAULT_DEADLINE_S + max(DEFAULT_ATHENA_TIMEOUT_S, DEFAULT_LLM_TIMEOUT_S)


def test_the_aws_provider_is_new_enough_for_public_function_urls():
    # invoked_via_function_url (permissao de InvokeFunction so via Function URL) existe a partir da 6.28
    for path in (INFRA / "versions.tf", MODULE / "versions.tf"):
        text = path.read_text(encoding="utf-8")
        assert 'version = "~> 6.28"' in text, path
    assert "invoked_via_function_url = true" in (MODULE / "main.tf").read_text(encoding="utf-8")
