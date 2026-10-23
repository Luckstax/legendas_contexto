"""
settings.py — Configuração do projeto.

Dois lugares, com papéis diferentes:
  .env         → credenciais e dados da sua conta Telegram (nunca versionar)
  config.json  → pastas, modelos e parâmetros (sem segredos)

Caminhos relativos são resolvidos a partir da pasta do projeto.
Nenhum caminho absoluto fica escrito no código.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv, set_key

BASE_DIR    = Path(__file__).resolve().parent
ENV_FILE    = BASE_DIR / ".env"
CONFIG_FILE = BASE_DIR / "config.json"

ENV_KEYS = ["API_ID", "API_HASH", "CHANNEL", "LIMIT", "SESSION"]

DEFAULTS = {
    "series_name":        "",
    "videos_dir":         "dados/videos",
    "pasta_transcricoes": "dados/transcricoes",
    "pasta_analises":     "dados/analises",
    "arquivo_final":      "dados/context.json",
    "arquivo_log":        "dados/relatorio.txt",
    "whisper_model":      "medium",
    "whisper_language":   "pt",
    "whisper_device":     "cpu",
    "whisper_compute":    "int8",
    "lms_model_id":       "qwen2.5-7b-instruct",
    "lms_model_quant":    "",
    "lms_port":           1234,
    "lms_context_length": 8192,
    "chunk_minutes":      8,
    "max_tokens":         2048,
    # Desligamento agendado: guardado para continuar valendo na próxima abertura do programa.
    "desligar_ativo":     False,
    "desligar_hora":      "",
}

_data: dict = dict(DEFAULTS)


# ─── config.json ─────────────────────────────────────────────────

def load():
    """Lê config.json mesclando com os padrões. Cria o arquivo se não existir."""
    global _data
    data = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            data.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    _data = data
    if not CONFIG_FILE.exists():
        save()


def save():
    CONFIG_FILE.write_text(json.dumps(_data, ensure_ascii=False, indent=2), encoding="utf-8")


def cfg(key: str):
    return _data.get(key, DEFAULTS.get(key))


def set_cfg(key: str, value):
    _data[key] = value


def path(key: str) -> Path:
    """Caminho de uma chave de config. Relativo é resolvido a partir do projeto."""
    p = Path(str(cfg(key)))
    return p if p.is_absolute() else BASE_DIR / p


# ─── .env ────────────────────────────────────────────────────────

def env(key: str, default: str = "") -> str:
    """Lê uma variável do .env (recarrega o arquivo a cada chamada)."""
    load_dotenv(ENV_FILE, override=True)
    return (os.getenv(key) or default).strip()


def env_all() -> dict:
    return {k: env(k) for k in ENV_KEYS}


def save_env(values: dict):
    if not ENV_FILE.exists():
        ENV_FILE.touch()
    for key in ENV_KEYS:
        set_key(str(ENV_FILE), key, str(values.get(key, "") or ""))
    load_dotenv(ENV_FILE, override=True)


def telegram_ready():
    """Retorna (ok, lista_do_que_falta) para a etapa de download."""
    missing = [k for k in ("API_ID", "API_HASH", "CHANNEL") if not env(k)]
    return (not missing), missing
