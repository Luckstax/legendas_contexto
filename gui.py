"""
gui.py — Interface Tkinter unificada.

  App             → janela principal: etapas, manter ou apagar vídeos, desligamento
                    agendado, quatro barras de progresso, log.
  SettingsWindow  → tela de Configurações: .env (Telegram) e config.json.

Nenhuma lógica de negócio fica aqui. A comunicação com o worker é feita
só pela fila, porque widgets do Tkinter não são seguros fora da thread principal.
"""

import asyncio
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, simpledialog, ttk

import settings
from controle import Controle, decidir_desligamento
from logger import Logger
from pipeline import run_pipeline

BG     = "#1a1b26"
PANEL  = "#24283b"
FG     = "#c0caf5"
ACCENT = "#7aa2f7"
GREEN  = "#9ece6a"
YELLOW = "#e0af68"
RED    = "#f7768e"
MUTED  = "#565f89"
BTN    = "#414868"


class UI:
    """Ponte entre o worker e a GUI. Tudo passa pela fila."""

    def __init__(self, q: queue.Queue):
        self._q = q

    def status(self, text: str):
        self._q.put(("status", text))

    def bar(self, chave: str, feito: float, total: float, texto: str = ""):
        self._q.put(("bar", chave, feito, total, texto))

    def ask(self, title: str, prompt: str, show=None):
        reply = queue.Queue()
        self._q.put(("ask", title, prompt, show, reply))
        return reply.get()


def _desligar_computador():
    """Desligamento imediato no Windows. Comando mínimo, sem copiar a lógica do desligar_pc."""
    subprocess.run(["shutdown", "/s", "/t", "0"], check=False)


_NOMES_TIPO = {
    "download":   "Download",
    "transcricao": "Transcrição",
    "analise":    "Análise",
    "*":          "Execução",
}


def _texto_decisao(decisao: dict) -> str:
    """Ex.: 'Download: terminando o arquivo atual · Transcrição: interrompendo'."""
    partes = []
    for tipo, acao in decisao.items():
        nome = _NOMES_TIPO.get(tipo, tipo)
        if acao == "terminar":
            partes.append(f"{nome}: terminando o item atual")
        else:
            partes.append(f"{nome}: interrompendo")
    return " · ".join(partes)


# ══════════════════════════════════════════════════════════════════
#  JANELA PRINCIPAL
# ══════════════════════════════════════════════════════════════════

BARRAS = [
    ("dl_arq",   "Download — arquivo atual"),
    ("dl_total", "Download — total de arquivos"),
    ("tr_arq",   "Transcrição — arquivo atual"),
    ("tr_total", "Transcrição — total"),
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Legendas — Contexto da série")
        self.configure(bg=BG)
        self.geometry("960x860")
        self.minsize(780, 640)

        self.q       = queue.Queue()
        self.ctl     = Controle()
        self.worker  = None

        self.agendado_para  = None     # datetime do desligamento agendado
        self.desligar_ao_fim = False   # item atual terminando, desliga quando o worker acabar

        self.v_fetch      = tk.BooleanVar(value=True)
        self.v_transcribe = tk.BooleanVar(value=True)
        self.v_analyze    = tk.BooleanVar(value=True)
        self.v_keep       = tk.BooleanVar(value=True)
        # Último agendamento salvo em config.json (hora e se estava marcado).
        self.v_desligar   = tk.BooleanVar(value=bool(settings.cfg("desligar_ativo")))
        self.v_hora       = tk.StringVar(value=str(settings.cfg("desligar_hora") or ""))

        self.bars = {}   # chave -> (Progressbar, Label)

        self._build()
        self._refresh_info()
        if self.v_desligar.get():
            self._armar()   # reagenda para a próxima ocorrência da hora salva
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.after(100, self._poll)
        self.after(1000, self._tick)

    # ── construção ───────────────────────────────────────────────

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(top, text="Contexto para legendagem", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Button(top, text="⚙ Configurações", command=self._open_settings,
                  bg=BTN, fg=FG, relief="flat", padx=12, pady=4,
                  activebackground=PANEL, activeforeground=FG).pack(side="right")

        self.lbl_info = tk.Label(self, text="", bg=BG, fg=MUTED, anchor="w", justify="left")
        self.lbl_info.pack(fill="x", padx=16)

        box = tk.Frame(self, bg=PANEL)
        box.pack(fill="x", padx=16, pady=10)

        tk.Label(box, text="Etapas", bg=PANEL, fg=FG,
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))
        tk.Label(box, text="Depois da transcrição, o vídeo:", bg=PANEL, fg=FG,
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w", padx=14, pady=(10, 4))

        check = dict(bg=PANEL, fg=FG, selectcolor=BG, activebackground=PANEL,
                     activeforeground=FG, highlightthickness=0)
        tk.Checkbutton(box, text="Baixar vídeos do Telegram", variable=self.v_fetch,
                       **check).grid(row=1, column=0, sticky="w", padx=14, pady=2)
        tk.Checkbutton(box, text="Transcrever (Whisper)", variable=self.v_transcribe,
                       **check).grid(row=2, column=0, sticky="w", padx=14, pady=2)
        tk.Checkbutton(box, text="Analisar com LM Studio → context.json", variable=self.v_analyze,
                       **check).grid(row=3, column=0, sticky="w", padx=14, pady=2)

        radio = dict(bg=PANEL, fg=FG, selectcolor=BG, activebackground=PANEL,
                     activeforeground=FG, highlightthickness=0)
        tk.Radiobutton(box, text="Manter os vídeos baixados", variable=self.v_keep,
                       value=True, **radio).grid(row=1, column=1, sticky="w", padx=14, pady=2)
        tk.Radiobutton(box, text="Apagar o vídeo após transcrever", variable=self.v_keep,
                       value=False, **radio).grid(row=2, column=1, sticky="w", padx=14, pady=2)
        tk.Label(box, text="Vale só para esta execução. A análise usa as transcrições.",
                 bg=PANEL, fg=MUTED).grid(row=3, column=1, sticky="w", padx=14, pady=2)

        # Desligamento agendado
        sep = tk.Frame(box, bg=BG, height=1)
        sep.grid(row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 6))

        tk.Checkbutton(box, text="Desligar o PC no horário:", variable=self.v_desligar,
                       command=self._armar, **check).grid(row=5, column=0, sticky="w", padx=14, pady=(2, 10))
        linha = tk.Frame(box, bg=PANEL)
        linha.grid(row=5, column=1, sticky="w", padx=14, pady=(2, 10))
        entrada = tk.Entry(linha, textvariable=self.v_hora, width=8, bg=BG, fg=FG,
                           insertbackground=FG, relief="flat")
        entrada.pack(side="left")
        entrada.bind("<Return>", lambda e: self._armar())
        entrada.bind("<FocusOut>", lambda e: self._armar())
        tk.Label(linha, text="HH:MM", bg=PANEL, fg=MUTED).pack(side="left", padx=6)
        self.lbl_agenda = tk.Label(linha, text="", bg=PANEL, fg=YELLOW)
        self.lbl_agenda.pack(side="left", padx=6)

        # Botões
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=16)
        self.btn_start = tk.Button(bar, text="▶ Iniciar", command=self._start,
                                   bg=GREEN, fg=BG, relief="flat", padx=18, pady=6,
                                   font=("Segoe UI", 10, "bold"))
        self.btn_start.pack(side="left")
        self.btn_stop = tk.Button(bar, text="■ Parar", command=self._stop, state="disabled",
                                  bg=RED, fg=BG, relief="flat", padx=18, pady=6)
        self.btn_stop.pack(side="left", padx=8)
        self.btn_skip = tk.Button(bar, text="⏭ Pular download atual", command=self._skip,
                                  state="disabled", bg=BTN, fg=FG, relief="flat", padx=14, pady=6)
        self.btn_skip.pack(side="left")

        # Barras de progresso
        pro = tk.Frame(self, bg=BG)
        pro.pack(fill="x", padx=16, pady=(12, 2))
        for chave, titulo in BARRAS:
            tk.Label(pro, text=titulo, bg=BG, fg=MUTED, anchor="w").pack(fill="x")
            pb = ttk.Progressbar(pro, mode="determinate")
            pb.pack(fill="x", pady=(0, 2))
            lbl = tk.Label(pro, text="—", bg=BG, fg=FG, anchor="w")
            lbl.pack(fill="x", pady=(0, 8))
            self.bars[chave] = (pb, lbl)

        self.lbl_current = tk.Label(self, text="Pronto.", bg=BG, fg=FG, anchor="w")
        self.lbl_current.pack(fill="x", padx=16)

        # Log
        frame = tk.Frame(self, bg=BG)
        frame.pack(fill="both", expand=True, padx=16, pady=(8, 16))
        self.txt = tk.Text(frame, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                           font=("Consolas", 10), wrap="word", state="disabled")
        sb = tk.Scrollbar(frame, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        for tag, color in (("normal", FG), ("section", ACCENT), ("ok", GREEN),
                           ("warn", YELLOW), ("err", RED), ("cache", MUTED)):
            self.txt.tag_configure(tag, foreground=color)
        self.txt.tag_configure("section", foreground=ACCENT, font=("Consolas", 10, "bold"))

    # ── informações e agenda ─────────────────────────────────────

    def _refresh_info(self):
        canal = settings.env("CHANNEL") or "(não configurado)"
        self.lbl_info.configure(text=(
            f"Canal: {canal}   •   Série: {settings.cfg('series_name') or '(sem nome)'}\n"
            f"Vídeos: {settings.path('videos_dir')}   •   Transcrições: {settings.path('pasta_transcricoes')}\n"
            f"Saída: {settings.path('arquivo_final')}"
        ))

    def _refresh_agenda(self):
        if self.desligar_ao_fim:
            texto = "Aguardando o término do item atual para desligar..."
        elif self.agendado_para:
            texto = f"Desliga em {self.agendado_para:%d/%m às %H:%M}"
        else:
            texto = ""
        self.lbl_agenda.configure(text=texto)

    def _salvar_agenda(self):
        """Grava a opção e a hora em config.json, para valerem na próxima abertura."""
        try:
            settings.set_cfg("desligar_ativo", bool(self.v_desligar.get()))
            settings.set_cfg("desligar_hora", self.v_hora.get().strip())
            settings.save()
        except OSError as e:
            self.lbl_current.configure(text=f"⚠ Não consegui salvar o horário do desligamento: {e}")

    def _armar(self):
        """Agenda o desligamento quando a opção está marcada e a hora é válida."""
        if not self.v_desligar.get():
            self.agendado_para   = None
            self.desligar_ao_fim = False
            self._refresh_agenda()
            self._salvar_agenda()
            return

        try:
            h, m = map(int, self.v_hora.get().strip().split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            messagebox.showerror("Desligamento", "Hora inválida. Use HH:MM (horas 0-23, minutos 0-59).", parent=self)
            self.v_desligar.set(False)
            self.agendado_para = None
            self._refresh_agenda()
            self._salvar_agenda()
            return

        agora = datetime.now()
        alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
        if alvo <= agora:
            alvo += timedelta(days=1)
        self.agendado_para = alvo
        self._refresh_agenda()
        self._salvar_agenda()

    def _tick(self):
        """Checa a cada segundo se chegou a hora do desligamento agendado."""
        try:
            if self.agendado_para and datetime.now() >= self.agendado_para:
                self.agendado_para = None
                vivo = self.worker is not None and self.worker.is_alive()
                acao, decisao = decidir_desligamento(vivo, self.ctl.ativos())

                if acao == "desligar_agora":
                    self.lbl_current.configure(text="Desligando o computador...")
                    _desligar_computador()
                else:
                    self.ctl.aplicar(decisao)
                    self.desligar_ao_fim = True
                    self.lbl_current.configure(
                        text="Horário chegou. " + _texto_decisao(decisao) + ". Depois, desliga."
                    )
                self._refresh_agenda()
        finally:
            self.after(1000, self._tick)

    # ── controle ─────────────────────────────────────────────────

    def _set_running(self, running: bool):
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_skip.configure(state="normal" if running else "disabled")

    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        opts = {
            "fetch":       self.v_fetch.get(),
            "transcribe":  self.v_transcribe.get(),
            "analyze":     self.v_analyze.get(),
            "keep_videos": self.v_keep.get(),
        }
        if not any((opts["fetch"], opts["transcribe"], opts["analyze"])):
            messagebox.showwarning("Etapas", "Marque pelo menos uma etapa.", parent=self)
            return
        if opts["fetch"]:
            ok, missing = settings.telegram_ready()
            if not ok:
                messagebox.showwarning("Configurações",
                                       "Preencha em ⚙ Configurações: " + ", ".join(missing), parent=self)
                return
        if opts["analyze"] and not settings.cfg("series_name"):
            messagebox.showwarning("Configurações",
                                   "Preencha o 'Nome da série' em ⚙ Configurações.", parent=self)
            return

        if self.v_desligar.get():
            self._armar()
            if not self.v_desligar.get():
                return

        self.ctl.limpar()
        self.desligar_ao_fim = False
        self._set_running(True)
        self.lbl_current.configure(text="Iniciando...")
        self.worker = threading.Thread(target=self._worker, args=(opts,), daemon=True)
        self.worker.start()

    def _stop(self):
        self.ctl.global_stop.set()
        self.lbl_current.configure(text="Parando o item atual...")

    def _skip(self):
        self.ctl.skip.set()

    def _open_settings(self):
        SettingsWindow(self, on_save=self._refresh_info)

    def _worker(self, opts):
        logger = Logger(self.q)
        ui = UI(self.q)
        try:
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            logger.open()
            run_pipeline(opts, logger, ui, self.ctl)
        except Exception as e:
            logger.log(f"✗ Erro: {e}", "err")
            logger.log(traceback.format_exc(), "err")
        finally:
            try:
                logger.close()
            except Exception:
                pass
            self.q.put(("done",))

    def _ao_fechar(self):
        ocupado = (self.worker is not None and self.worker.is_alive()) or self.agendado_para is not None
        if ocupado and not messagebox.askokcancel(
            "Sair",
            "Há uma execução em andamento ou um desligamento agendado.\n"
            "Se você fechar, tudo será interrompido e o agendamento será perdido.\n\nFechar mesmo assim?",
            parent=self,
        ):
            return
        self.ctl.global_stop.set()
        self.destroy()

    # ── fila ─────────────────────────────────────────────────────

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1], item[2])
                elif kind == "status":
                    self.lbl_current.configure(text=item[1])
                elif kind == "bar":
                    _, chave, feito, total, texto = item
                    if chave in self.bars:
                        pb, lbl = self.bars[chave]
                        pb.configure(maximum=max(float(total), 1.0), value=float(feito))
                        lbl.configure(text=texto or "—")
                elif kind == "ask":
                    _, title, prompt, show, reply = item
                    value = simpledialog.askstring(title, prompt, show=show, parent=self)
                    reply.put(value)
                elif kind == "done":
                    self._set_running(False)
                    if self.desligar_ao_fim:
                        self.desligar_ao_fim = False
                        self._refresh_agenda()
                        self.lbl_current.configure(text="Desligando o computador...")
                        _desligar_computador()
                    else:
                        self.lbl_current.configure(text="Parado." if self.ctl.global_stop.is_set() else "Concluído.")
                    self._refresh_info()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _append_log(self, text: str, tag: str):
        self.txt.configure(state="normal")
        self.txt.insert("end", text, tag)
        self.txt.see("end")
        self.txt.configure(state="disabled")


# ══════════════════════════════════════════════════════════════════
#  JANELA DE CONFIGURAÇÕES
# ══════════════════════════════════════════════════════════════════

# (chave, rótulo, tipo)  tipos: text | secret | dir | file | int | int_opt
SECTIONS = [
    ("Telegram  (salvo no arquivo .env)", "env", [
        ("API_ID",   "API_ID (my.telegram.org)",              "text"),
        ("API_HASH", "API_HASH",                              "secret"),
        ("CHANNEL",  "Canal (ex: @nomedocanal)",              "text"),
        ("LIMIT",    "Limite de mensagens (vazio = todas)",   "int_opt"),
        ("SESSION",  "Nome do arquivo de sessão",             "text"),
    ]),
    ("Série e pastas  (salvo no config.json)", "json", [
        ("series_name",        "Nome da série",                 "text"),
        ("videos_dir",         "Pasta dos vídeos",              "dir"),
        ("pasta_transcricoes", "Pasta das transcrições",        "dir"),
        ("pasta_analises",     "Pasta das análises",            "dir"),
        ("arquivo_final",      "Arquivo context.json final",    "file"),
        ("arquivo_log",        "Arquivo de relatório (log)",    "file"),
    ]),
    ("Whisper  (transcrição)", "json", [
        ("whisper_model",    "Modelo Whisper (medium = bom equilíbrio)", "text"),
        ("whisper_language", "Idioma do áudio (pt, en, fr…)",            "text"),
        ("whisper_device",   "Dispositivo (cpu ou cuda)",                "text"),
        ("whisper_compute",  "Precisão (int8, float16…)",                "text"),
    ]),
    ("LM Studio  (análise)", "json", [
        ("lms_model_id",       "ID do modelo no LM Studio",     "text"),
        ("lms_model_quant",    "Quantização (opcional)",        "text"),
        ("lms_port",           "Porta do servidor",             "int"),
        ("lms_context_length", "Tamanho do contexto (tokens)",  "int"),
        ("chunk_minutes",      "Minutos de transcrição por chunk", "int"),
        ("max_tokens",         "Máximo de tokens na resposta",  "int"),
    ]),
]


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, on_save):
        super().__init__(parent)
        self.title("Configurações")
        self.configure(bg=BG)
        self.transient(parent)
        self.resizable(False, True)
        self._on_save = on_save
        self.entries = {}   # chave -> (Entry, tipo, origem)
        self._build()
        self._load()
        self.grab_set()

    def _build(self):
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=14)

        row = 0
        for title, src, fields in SECTIONS:
            tk.Label(wrap, text=title, bg=BG, fg=ACCENT,
                     font=("Segoe UI", 11, "bold")).grid(row=row, column=0, columnspan=3,
                                                         sticky="w", pady=(12, 4))
            row += 1
            for key, label, kind in fields:
                tk.Label(wrap, text=label, bg=BG, fg=FG, anchor="w").grid(
                    row=row, column=0, sticky="w", padx=(0, 12), pady=3)
                entry = tk.Entry(wrap, width=46, bg=PANEL, fg=FG, insertbackground=FG,
                                 relief="flat", show="•" if kind == "secret" else "")
                entry.grid(row=row, column=1, sticky="ew", pady=3)
                if kind in ("dir", "file"):
                    tk.Button(wrap, text="Procurar", bg=BTN, fg=FG, relief="flat",
                              command=lambda k=key, kd=kind: self._browse(k, kd)).grid(
                        row=row, column=2, padx=(6, 0))
                self.entries[key] = (entry, kind, src)
                row += 1

        tk.Label(wrap, text="As credenciais do Telegram ficam só no .env, fora do git.",
                 bg=BG, fg=MUTED).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        row += 1

        btns = tk.Frame(wrap, bg=BG)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(14, 0))
        tk.Button(btns, text="Cancelar", command=self.destroy, bg=BTN, fg=FG,
                  relief="flat", padx=14, pady=4).pack(side="right", padx=(8, 0))
        tk.Button(btns, text="Salvar", command=self._save, bg=GREEN, fg=BG,
                  relief="flat", padx=14, pady=4, font=("Segoe UI", 10, "bold")).pack(side="right")

    def _load(self):
        env_vals = settings.env_all()
        for key, (entry, kind, src) in self.entries.items():
            value = env_vals.get(key, "") if src == "env" else settings.cfg(key)
            entry.delete(0, "end")
            entry.insert(0, "" if value is None else str(value))

    def _browse(self, key, kind):
        entry = self.entries[key][0]
        current = entry.get().strip()
        if kind == "dir":
            chosen = filedialog.askdirectory(parent=self, initialdir=current or None)
        else:
            chosen = filedialog.asksaveasfilename(
                parent=self, initialfile=current or None, defaultextension=".json",
                filetypes=[("JSON", "*.json"), ("Texto", "*.txt"), ("Todos", "*.*")],
            )
        if chosen:
            entry.delete(0, "end")
            entry.insert(0, chosen)

    def _save(self):
        env_vals, json_vals = {}, {}
        for key, (entry, kind, src) in self.entries.items():
            raw = entry.get().strip()
            if kind == "int" and not raw.isdigit():
                messagebox.showerror("Configurações", f"'{key}' precisa ser um número.", parent=self)
                return
            if kind == "int_opt" and raw and not raw.isdigit():
                messagebox.showerror("Configurações", f"'{key}' precisa ser um número ou ficar vazio.", parent=self)
                return
            if src == "env":
                env_vals[key] = raw
            else:
                json_vals[key] = int(raw) if kind == "int" else raw

        settings.save_env(env_vals)
        for key, value in json_vals.items():
            settings.set_cfg(key, value)
        settings.save()
        self._on_save()
        self.destroy()
