"""
controle.py — Controle compartilhado entre pipeline, interface e agendador.

Download e transcrição podem rodar ao mesmo tempo. Cada tipo de trabalho
tem o próprio progresso e a própria decisão no desligamento:

  download, transcricao, analise

Sinais:
  global_stop     → botão Parar: aborta tudo agora
  parar[tipo]     → aborta o item atual daquele tipo (não afeta os outros)
  encerrando      → desligamento decidido: ninguém inicia nada novo
"""

import threading

LIMITE_PARA_TERMINAR = 50.0   # % do item atual a partir do qual esperamos terminar
TIPOS = ("download", "transcricao", "analise")


class Interrompido(Exception):
    """Sinal interno: o trabalho foi interrompido pelo usuário ou pelo agendador."""


class Controle:
    def __init__(self):
        self._lock       = threading.Lock()
        self.global_stop = threading.Event()
        self.encerrando  = threading.Event()
        self.skip        = threading.Event()
        self._parar      = {t: threading.Event() for t in TIPOS}
        self._ativos     = {}   # tipo -> pct do item atual

    def limpar(self):
        with self._lock:
            self._ativos.clear()
        self.global_stop.clear()
        self.encerrando.clear()
        self.skip.clear()
        for ev in self._parar.values():
            ev.clear()

    # ── consultas ────────────────────────────────────────────────

    def abortar(self, tipo: str) -> bool:
        """True se o item atual desse tipo deve ser abortado agora."""
        return self.global_stop.is_set() or self._parar[tipo].is_set()

    def pode_iniciar_novo(self, tipo: str) -> bool:
        """True se ainda é permitido começar um novo item desse tipo."""
        return not (self.global_stop.is_set()
                    or self.encerrando.is_set()
                    or self._parar[tipo].is_set())

    # ── registro dos trabalhos ativos ────────────────────────────

    def registrar(self, tipo: str, pct: float = 0.0):
        with self._lock:
            self._ativos[tipo] = max(0.0, min(100.0, float(pct)))

    def atualizar(self, tipo: str, pct: float):
        with self._lock:
            if tipo in self._ativos:
                self._ativos[tipo] = max(0.0, min(100.0, float(pct)))

    def liberar(self, tipo: str):
        with self._lock:
            self._ativos.pop(tipo, None)

    def ativos(self) -> dict:
        with self._lock:
            return dict(self._ativos)

    # ── aplicar a decisão do desligamento ────────────────────────

    def aplicar(self, decisao: dict):
        """
        decisao: {tipo: 'terminar' | 'parar'}, ou {'*': 'parar'} para tudo.
        'terminar' → o item atual segue até o fim, mas nada novo começa.
        'parar'    → o item atual é abortado e nada novo começa.
        """
        self.encerrando.set()
        if "*" in decisao:
            self.global_stop.set()
            return
        for tipo, acao in decisao.items():
            if acao == "parar":
                self._parar[tipo].set()


def decidir_desligamento(worker_vivo: bool, ativos: dict):
    """
    Regra por trabalho, independente de os outros:
      - nada rodando                     → desligar agora
      - trabalho com 50% ou mais         → terminar o item atual, não iniciar outro
      - trabalho com menos de 50%        → interromper agora

    Retorna (acao, decisao):
      ('desligar_agora', {})
      ('aguardar', {tipo: 'terminar' | 'parar'})
    """
    if not worker_vivo:
        return "desligar_agora", {}
    if not ativos:
        # Há execução, mas nenhum trabalho mensurável (login, listagem, carregar modelo).
        return "aguardar", {"*": "parar"}
    decisao = {
        tipo: ("terminar" if pct >= LIMITE_PARA_TERMINAR else "parar")
        for tipo, pct in ativos.items()
    }
    return "aguardar", decisao
