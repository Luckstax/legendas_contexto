"""
logger.py — Logger de execução: grava em arquivo, envia linhas para a
fila da GUI e acumula estatísticas para o relatório final.

Tags usadas: normal, section, ok, warn, err, cache.
"""

from datetime import datetime

import settings
from utils import format_duration


class Logger:
    def __init__(self, gui_queue):
        self.queue = gui_queue
        self._f    = None
        self.t0    = None
        self.stats = dict(
            f1_total=0, f1_ok=0, f1_err=0, f1_cache=0,
            f2_total=0, f2_ok=0, f2_err=0, f2_cache=0,
            f1_durations=[], f2_durations=[],
            errors=[],
        )

    def open(self):
        log_path = settings.path("arquivo_log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(log_path, "w", encoding="utf-8")
        self.t0 = datetime.now()
        self._raw(
            f"LOG DE EXECUÇÃO\n"
            f"Data: {self.t0:%d/%m/%Y %H:%M:%S}\n"
            f"Série: {settings.cfg('series_name')}\n\n"
        )

    def close(self):
        self._summary()
        if self._f:
            self._f.close()
            self._f = None

    def log(self, msg: str, tag: str = "normal"):
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.queue.put(("log", line + "\n", tag))
        self._raw(line + "\n")

    def _raw(self, text: str):
        if self._f:
            self._f.write(text)
            self._f.flush()

    def _summary(self):
        s  = self.stats
        dt = datetime.now() - self.t0 if self.t0 else None
        h = m = sec = 0
        if dt:
            h, r   = divmod(int(dt.total_seconds()), 3600)
            m, sec = divmod(r, 60)
        lines = ["", "═" * 50, "RELATÓRIO FINAL", "═" * 50,
                 f"Duração total: {h}h {m:02d}min {sec:02d}s"]
        if s["f1_total"] > 0:
            lines += ["", "TRANSCRIÇÃO",
                      f"  ✓ Transcritos : {s['f1_ok']} / {s['f1_total']}",
                      f"  → Já existiam : {s['f1_cache']}",
                      f"  ✗ Erros       : {s['f1_err']}"]
            if s["f1_durations"]:
                avg = sum(s["f1_durations"]) / len(s["f1_durations"])
                lines.append(f"  Tempo médio/vídeo: {format_duration(avg)}")
        if s["f2_total"] > 0:
            lines += ["", "ANÁLISE",
                      f"  ✓ Analisados  : {s['f2_ok']} / {s['f2_total']}",
                      f"  → Cache       : {s['f2_cache']}",
                      f"  ✗ Erros       : {s['f2_err']}"]
            if s["f2_durations"]:
                avg = sum(s["f2_durations"]) / len(s["f2_durations"])
                lines.append(f"  Tempo médio/episódio: {format_duration(avg)}")
        if s["errors"]:
            lines += ["", "ERROS DETALHADOS"]
            lines += [f"  • {e}" for e in s["errors"]]
        text = "\n".join(lines) + "\n"
        self._raw(text)
        self.queue.put(("log", text, "warn"))
