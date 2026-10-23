"""
lms_client.py — Encontra/sobe o LM Studio, carrega o modelo configurado
e faz chamadas de chat compatíveis com a API OpenAI.
"""

import os
import shutil
import subprocess
import time
import urllib.request

from openai import OpenAI

import settings
from utils import format_duration
from logger import Logger


def _base_url() -> str:
    return f"http://localhost:{settings.cfg('lms_port')}/v1"


def _short_model_name() -> str:
    return settings.cfg("lms_model_id").split("/")[-1].lower().split("-gguf")[0]


def find_lms() -> str:
    if shutil.which("lms"):
        return "lms"
    for p in [
        os.path.expandvars(r"%USERPROFILE%\.lmstudio\bin\lms.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\LM-Studio\bin\lms.exe"),
    ]:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "CLI do LM Studio não encontrado. Abra o LM Studio pelo menos uma vez."
    )


def is_lms_running() -> bool:
    try:
        urllib.request.urlopen(f"{_base_url()}/models", timeout=3)
        return True
    except Exception:
        return False


def load_model(lms: str, logger: Logger):
    model_key = settings.cfg("lms_model_id")
    ctx_len   = settings.cfg("lms_context_length")

    # Descarrega o que estiver em memória: evita reaproveitar uma carga antiga
    # com --context-length diferente do configurado agora.
    subprocess.run([lms, "unload", "--all"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace")

    logger.log(f"Carregando modelo {model_key} (contexto: {ctx_len} tokens)...")
    r = subprocess.run(
        [lms, "load", model_key, "-y", "--context-length", str(ctx_len)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        logger.log(f"⚠ Falha ao carregar modelo: {r.stderr[:200] or r.stdout[:200]}", "warn")
        raise RuntimeError(
            f"Não foi possível carregar o modelo '{model_key}' no LM Studio. "
            f"Confira se o nome bate com o exibido em 'lms ls'."
        )
    logger.log("Modelo carregado.", "ok")


def ensure_lmstudio(logger: Logger):
    t0  = time.time()
    lms = find_lms()

    if not is_lms_running():
        logger.log("Iniciando LM Studio (daemon)...")
        r = subprocess.run([lms, "daemon", "up"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 and "already" not in r.stderr.lower():
            logger.log(f"⚠ Daemon: {r.stderr[:120]}", "warn")
        time.sleep(2)

        ls = subprocess.run([lms, "ls"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if _short_model_name() not in ls.stdout.lower():
            spec = settings.cfg("lms_model_id")
            if settings.cfg("lms_model_quant"):
                spec = f"{spec}@{settings.cfg('lms_model_quant')}"
            logger.log(f"Baixando modelo {spec}...", "warn")
            subprocess.run([lms, "get", spec])

        kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen([lms, "server", "start", "--port", str(settings.cfg("lms_port"))], **kw)

        logger.log("Aguardando LM Studio iniciar...")
        for _ in range(25):
            time.sleep(1)
            if is_lms_running():
                logger.log(f"Servidor pronto ({format_duration(time.time() - t0)}).", "ok")
                break
        else:
            raise RuntimeError("LM Studio não respondeu após 25 segundos.")
    else:
        logger.log("LM Studio já está rodando.", "ok")

    # Sempre recarrega para garantir que o contexto configurado está em vigor.
    load_model(lms, logger)


def get_client() -> OpenAI:
    return OpenAI(base_url=_base_url(), api_key="lm-studio")


def call_lms(client: OpenAI, prompt: str, max_tokens: int = None) -> str:
    mt = max_tokens or settings.cfg("max_tokens")
    r = client.chat.completions.create(
        model=settings.cfg("lms_model_id"),
        max_tokens=mt,
        messages=[{"role": "user", "content": prompt}],
    )
    return r.choices[0].message.content.strip()
