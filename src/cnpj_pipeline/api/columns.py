"""Colunas das tabelas da gold, para montar o texto de contexto do modelo na funcao.

Na maquina de quem publica, as colunas vem da gold local (`write_columns`); na funcao nao ha gold, entao
elas viajam num arquivo pequeno dentro do pacote (`load_columns`). O arquivo so tem nomes e tipos de
colunas, nenhum dado.
"""

from __future__ import annotations

import json
from pathlib import Path

from cnpj_pipeline.agent.context import TABLES

FORMAT_VERSION = 1


class ColumnsError(ValueError):
    pass


def write_columns(gold_dir: Path, month: str, out: Path) -> Path:
    from cnpj_pipeline.cloud.athena import gold_columns  # so na maquina que le a gold local

    columns = gold_columns(gold_dir, month, list(TABLES))
    payload = {
        "formato": FORMAT_VERSION,
        "mes": month,
        "colunas": {m: [[name, kind] for name, kind in cols] for m, cols in sorted(columns.items())},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def load_columns(path: Path, month: str) -> dict[str, list[tuple[str, str]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ColumnsError("arquivo de colunas ausente ou ilegivel") from exc
    if payload.get("formato") != FORMAT_VERSION:
        raise ColumnsError("formato do arquivo de colunas nao reconhecido")
    if payload.get("mes") != month:
        raise ColumnsError("o arquivo de colunas e de outro mes")
    raw = payload.get("colunas")
    if not isinstance(raw, dict) or set(raw) != set(TABLES):
        raise ColumnsError("o arquivo de colunas nao cobre exatamente as tabelas documentadas")
    return {model: [(str(c[0]), str(c[1])) for c in cols] for model, cols in raw.items()}
