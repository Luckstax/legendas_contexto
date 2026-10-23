"""
pipeline.py — Orquestra as etapas.

  1. Baixar do Telegram  ─┐  rodam ao mesmo tempo:
  2. Transcrever (thread) ┘  o download envia cada vídeo para a fila da transcrição
  3. Analisar            → só depois que download e transcrição terminaram

A análise lê as transcrições do cache, não os vídeos.

Antes de começar qualquer item novo, cada etapa consulta
ctl.pode_iniciar_novo(tipo). Assim o agendador de desligamento consegue
impedir que coisas novas comecem.
"""

import asyncio
import json
import re
import time
import traceback
from pathlib import Path

import settings
from controle import Controle, Interrompido
from logger import Logger
from telegram_fetch import fetch_channel
from transcricao import Transcriber
from lms_client import ensure_lmstudio, get_client
from analise import analyze_episode, merge_contexts, strip_timestamps
from utils import format_duration

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v"}


def sort_episodes(items, key_fn=lambda x: x):
    """Ordena por SxxEyy ou NxN no nome; o que não tiver padrão vai para o fim."""
    def key(item):
        stem = key_fn(item)
        m = re.search(r"[Ss](\d+)[Ee](\d+)", stem)
        if m:
            return (int(m.group(1)), int(m.group(2)))
        m = re.search(r"(\d+)[xX](\d+)", stem)
        if m:
            return (int(m.group(1)), int(m.group(2)))
        return (999, 999)
    return sorted(items, key=key)


def list_videos(folder: Path):
    if not folder.exists():
        return []
    videos = [p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXTS]
    return sort_episodes(videos, key_fn=lambda p: p.stem)


def run_pipeline(opts, logger: Logger, ui, ctl: Controle):
    """
    opts = {"fetch": bool, "transcribe": bool, "analyze": bool, "keep_videos": bool}
    ui   = objeto com status(texto), bar(chave, feito, total, texto) e ask(...)
    """
    cache_dir  = settings.path("pasta_transcricoes")
    ctx_dir    = settings.path("pasta_analises")
    videos_dir = settings.path("videos_dir")
    for folder in (cache_dir, ctx_dir, videos_dir):
        folder.mkdir(parents=True, exist_ok=True)

    def transcrito(stem: str) -> bool:
        return (cache_dir / (stem + ".txt")).exists()

    try:
        if opts["fetch"]:
            ok, missing = settings.telegram_ready()
            if not ok:
                logger.log(f"✗ Preencha em Configurações: {', '.join(missing)}", "err")
                return

        transcriber = Transcriber(cache_dir, opts["keep_videos"], logger, ui, ctl) if opts["transcribe"] else None
        if transcriber is not None:
            logger.log("── TRANSCRIÇÃO (em paralelo ao download) ──", "section")
            transcriber.iniciar()

        try:
            canal_stems = set()
            if opts["fetch"]:
                logger.log("── BAIXAR DO TELEGRAM ──────────────────────", "section")
                canal_stems = asyncio.run(fetch_channel(
                    videos_dir, transcrito, logger, ui, ctl,
                    on_video=transcriber.enfileirar if transcriber else None,
                    on_listing=transcriber.definir_total if transcriber else None,
                ))

            if transcriber is not None:
                # Vídeos que já estavam na pasta, não vieram do canal e ainda não foram transcritos.
                pendentes = [v for v in list_videos(videos_dir)
                             if not transcrito(v.stem) and v.stem not in canal_stems]
                transcriber.somar_total(len(pendentes))
                for v in pendentes:
                    transcriber.enfileirar(v)
        finally:
            if transcriber is not None:
                transcriber.encerrar_fila()
                transcriber.aguardar()
                logger.log("Fase de transcrição concluída.", "ok")

        if opts["analyze"]:
            if ctl.pode_iniciar_novo("analise"):
                run_analysis(logger, ui, cache_dir, ctx_dir, ctl)
            else:
                logger.log("Análise não iniciada: execução encerrando.", "warn")

    except Exception as e:
        logger.log(f"✗ Erro crítico: {e}", "err")
        logger.log(traceback.format_exc(), "err")


def run_analysis(logger: Logger, ui, cache_dir: Path, ctx_dir: Path, ctl: Controle):
    logger.log("── ANÁLISE ──────────────────────────────────", "section")

    stems = sort_episodes([p.stem for p in cache_dir.glob("*.txt")], key_fn=lambda s: s)
    if not stems:
        logger.log("✗ Nenhuma transcrição encontrada. Rode a transcrição antes.", "err")
        return
    if not settings.cfg("series_name"):
        logger.log("⚠ 'Nome da série' está vazio em Configurações.", "warn")

    ctl.registrar("analise", 0)
    try:
        ensure_lmstudio(logger)
        if not ctl.pode_iniciar_novo("analise"):
            return

        client  = get_client()
        all_eps = []
        logger.stats["f2_total"] = len(stems)

        for i, stem in enumerate(stems, 1):
            if not ctl.pode_iniciar_novo("analise"):
                logger.log("⚠ Análise interrompida: não iniciando novos episódios.", "warn")
                break

            ui.status(f"Análise {i}/{len(stems)}: {stem}")
            transcript = strip_timestamps((cache_dir / (stem + ".txt")).read_text(encoding="utf-8")).strip()
            if not transcript:
                logger.stats["f2_err"] += 1
                continue

            logger.log(f"[{i}/{len(stems)}] Iniciando análise: {stem}", "section")
            t0 = time.time()
            ctl.atualizar("analise", 0)
            try:
                ep, from_cache = analyze_episode(client, stem, transcript, ctx_dir, logger, ui.status, ctl)
                all_eps.append(ep)
                dt = time.time() - t0
                if from_cache:
                    logger.stats["f2_cache"] += 1
                else:
                    logger.stats["f2_ok"] += 1
                    logger.stats["f2_durations"].append(dt)
                    logger.log(
                        f"  ✓ {len(ep.get('characters_seen', []))} personagens | "
                        f"{len(ep.get('plot_events', []))} eventos ({format_duration(dt)})",
                        "ok",
                    )
            except Interrompido:
                logger.log(f"⚠ Análise de {stem} interrompida no meio. Nada salvo para este episódio.", "warn")
                break
            except Exception as e:
                logger.log(f"  ✗ Erro: {e}", "err")
                logger.stats["f2_err"] += 1
                logger.stats["errors"].append(f"{stem}: {e}")

        if not all_eps:
            logger.log("✗ Nenhum episódio analisado com sucesso.", "err")
            return

        final = merge_contexts(client, all_eps, logger)
        out = settings.path("arquivo_final")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.log(f"✓ context.json salvo em {out}", "ok")
        logger.log(f"  Personagens: {len(final.get('characters', []))}", "ok")
        logger.log(f"  Glossário:   {len(final.get('glossary', []))}", "ok")
        logger.log(f"  Locais:      {len(final.get('locations', []))}", "ok")
    finally:
        ctl.liberar("analise")
