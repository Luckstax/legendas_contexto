"""
main.pyw — ponto de entrada. Abre só a janela, sem console.

Se algo impedir a abertura (pacote faltando, erro de configuração ou
erro na interface), o motivo aparece em uma caixa de mensagem. Como
.pyw roda sem terminal, sem essa caixa o erro ficaria invisível.

Também grava o último erro em erro_inicializacao.txt, ao lado deste arquivo.
"""

import os
import subprocess
import sys
import traceback
from pathlib import Path

# Se existir a pasta .venv ao lado deste arquivo, o programa se reabre com o Python dela.
# Assim o duplo clique no main.pyw usa as bibliotecas instaladas no ambiente do projeto,
# mesmo que o Windows abra .pyw com outro Python.
_VENV_PYTHONW = Path(__file__).resolve().parent / ".venv" / "Scripts" / "pythonw.exe"
if _VENV_PYTHONW.exists() and not os.environ.get("LEGENDAS_REABERTO"):
    _env = dict(os.environ, LEGENDAS_REABERTO="1")
    subprocess.Popen([str(_VENV_PYTHONW), str(Path(__file__).resolve())], env=_env)
    sys.exit(0)

BASE_DIR  = Path(__file__).resolve().parent
ERROR_LOG = BASE_DIR / "erro_inicializacao.txt"


def _show_error(title: str, text: str, parent=None):
    try:
        ERROR_LOG.write_text(text, encoding="utf-8")
    except OSError:
        pass
    try:
        from tkinter import messagebox
        messagebox.showerror(title, text, parent=parent)
    except Exception:
        # Se nem o tkinter funcionar, não há o que mostrar além do arquivo de log.
        pass


def main():
    try:
        import settings
        settings.load()
        from gui import App
    except ModuleNotFoundError as e:
        nome_pip = "python-dotenv" if e.name == "dotenv" else e.name
        _show_error(
            "Pacote faltando",
            f"O pacote '{nome_pip}' não está instalado no Python que abriu o programa:\n"
            f"{sys.executable}\n\n"
            "Instale as dependências nesse mesmo Python, com:\n"
            f"    \"{sys.executable}\" -m pip install -r requirements.txt",
        )
        return
    except Exception:
        _show_error("Erro ao iniciar", traceback.format_exc())
        return

    app = App()

    # Erros dentro de callbacks da janela (cliques, filas) não chegam ao mainloop.
    # Sem este gancho, eles ficariam invisíveis num .pyw.
    def _on_tk_error(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        _show_error("Erro na janela", text, parent=app)

    app.report_callback_exception = _on_tk_error
    app.mainloop()


if __name__ == "__main__":
    main()
