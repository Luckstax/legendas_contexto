"""
transcricao.py — Transcreve vídeos com faster-whisper, em thread própria.

A transcrição roda em paralelo ao download: quem baixa coloca o vídeo na
fila (enfileirar), e esta thread transcreve um por vez.

Barras pela interface:
  tr_arq   → progresso do vídeo atual (pelo áudio)
  tr_total → transcrições feitas de quantas são previstas

Depois de transcrever, o vídeo é mantido ou apagado conforme a escolha do usuário.
"""

import queue
import threading
import time
from pathlib import Path

import settings
from utils import format_duration
from audio import load_audio

TIPO = "transcricao"


class Transcriber:
    """Mantém o modelo Whisper carregado e processa a fila em uma thread."""

    def __init__(self, cache_dir: Path, keep_videos: bool, logger, ui, ctl):
        self.cache_dir = cache_dir
        self.keep      = keep_videos
        self.log       = logger
        self.ui        = ui
        self.ctl       = ctl
        self.model     = None
        self.total     = 0
        self.feitos    = 0
        self._fila     = queue.Queue()
        self._thread   = None

    # ── contagem ─────────────────────────────────────────────────

    def definir_total(self, n: int):
        self.total = n
        self._barra_total()

    def somar_total(self, n: int):
        self.total += n
        self._barra_total()

    def _barra_total(self):
        self.log.stats["f1_total"] = self.total
        self.ui.bar("tr_total", self.feitos, self.total,
                    f"Transcrições: {self.feitos}/{self.total}")

    # ── fila e thread ────────────────────────────────────────────

    def iniciar(self):
        self._thread = threading.Thread(target=self._executar, daemon=True)
        self._thread.start()

    def enfileirar(self, video: Path) -> bool:
        """Retorna False se a transcrição não for mais permitida nesta execução."""
        if not self.ctl.pode_iniciar_novo(TIPO):
            return False
        self._fila.put(video)
        return True

    def encerrar_fila(self):
        self._fila.put(None)

    def aguardar(self):
        if self._thread is not None:
            self._thread.join()

    def _executar(self):
        self.ctl.registrar(TIPO, 0)
        try:
            while True:
                item = self._fila.get()
                if item is None:
                    break
                if not self.ctl.pode_iniciar_novo(TIPO):
                    self.log.log(f"Transcrição não iniciada (interrompida): {item.name}", "warn")
                    continue
                self.transcribe_file(item)
        finally:
            self.ctl.liberar(TIPO)
            self.close()

    # ── modelo ───────────────────────────────────────────────────

    def _load_model(self):
        if self.model is not None:
            return self.model
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError("Instale faster-whisper: pip install faster-whisper")

        name = settings.cfg("whisper_model")
        self.log.log(f"Carregando Whisper '{name}'...")
        t0 = time.time()
        self.model = WhisperModel(
            name,
            device=settings.cfg("whisper_device"),
            compute_type=settings.cfg("whisper_compute"),
        )
        self.log.log(f"Modelo Whisper carregado ({format_duration(time.time() - t0)}).", "ok")
        return self.model

    def close(self):
        if self.model is not None:
            self.model = None
            self.log.log("Whisper descarregado da memória.")

    # ── vídeo ────────────────────────────────────────────────────

    def _descartar(self, video: Path):
        if not self.keep and video.exists():
            try:
                video.unlink()
                self.log.log(f"  Vídeo apagado após transcrição: {video.name}", "cache")
            except OSError as e:
                self.log.log(f"  ⚠ Não consegui apagar {video.name}: {e}", "warn")

    def transcribe_file(self, video: Path):
        out = self.cache_dir / (video.stem + ".txt")

        if out.exists():
            self.feitos += 1
            self._barra_total()
            self.log.log(f"→ Já transcrito: {video.name}", "cache")
            self._descartar(video)
            return

        model = self._load_model()
        self._transcrever(video, out, model)

    def _transcrever(self, video: Path, out: Path, model):
        st = self.log.stats
        self.log.log(f"Iniciando transcrição: {video.name}", "section")
        self.ui.bar("tr_arq", 0, 100, f"{video.name}: extraindo áudio...")
        self.ctl.atualizar(TIPO, 0)
        t_video0 = time.time()

        try:
            t0 = time.time()
            audio = load_audio(video)
            dt_extract = time.time() - t0
            self.log.log(f"  ✓ Áudio extraído ({format_duration(dt_extract)})", "ok")

            t0 = time.time()
            total_duration = len(audio) / 16000
            segs, info = model.transcribe(audio, language=settings.cfg("whisper_language"))

            lines, ultimo, interrompido = [], -1, False
            for s in segs:
                if self.ctl.abortar(TIPO):
                    interrompido = True
                    break
                lines.append(f"[{s.start:.1f}s] {s.text.strip()}")
                pct = min(s.end / total_duration * 100, 99) if total_duration > 0 else 0.0
                self.ctl.atualizar(TIPO, pct)
                if int(pct) != ultimo:
                    ultimo = int(pct)
                    self.ui.bar("tr_arq", pct, 100, f"{video.name}: {pct:.0f}%")
            dt_whisper = time.time() - t0

            if interrompido:
                # Não grava parcial: o cache assumiria que o episódio está completo.
                self.log.log(f"⚠ Transcrição de {video.name} interrompida; resultado parcial descartado.", "warn")
                self.ui.bar("tr_arq", 0, 100, f"{video.name}: interrompido")
                return

            self.log.log(f"  Idioma detectado: {info.language} ({info.language_probability:.0%})")
            text = "\n".join(lines)
            if not text.strip():
                self.log.log("  ⚠ Transcrição vazia.", "warn")
                st["f1_err"] += 1
                st["errors"].append(f"Transcrição vazia: {video.name}")
                self.ui.bar("tr_arq", 0, 100, f"{video.name}: vazia")
                return

            out.write_text(text, encoding="utf-8")
            dt_total = time.time() - t_video0
            st["f1_ok"] += 1
            st["f1_durations"].append(dt_total)
            ultimo_ts = lines[-1].split("]")[0].strip("[") if lines else "?"
            self.log.log(
                f"  ✓ Transcrição concluída — total {format_duration(dt_total)} "
                f"(áudio {format_duration(dt_extract)} + whisper {format_duration(dt_whisper)}) "
                f"| último timestamp: {ultimo_ts}",
                "ok",
            )
            self.ui.bar("tr_arq", 100, 100, f"{video.name}: concluído")
            self.feitos += 1
            self._barra_total()
            self._descartar(video)

        except Exception as e:
            st["f1_err"] += 1
            st["errors"].append(f"{video.name}: {e}")
            self.log.log(f"  ✗ Erro: {e}", "err")
            self.ui.bar("tr_arq", 0, 100, f"{video.name}: erro")
