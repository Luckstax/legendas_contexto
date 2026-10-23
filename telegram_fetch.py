"""
telegram_fetch.py — Baixa os vídeos de um canal do Telegram (Telethon).

Login, listagem, download com arquivo temporário (.tmp) que é retomado
se o download parar, velocidade e duas barras de progresso (arquivo atual
e total de arquivos).

O login é feito pelas caixas da interface (ui.ask). Não usamos
`async with TelegramClient`, porque ele chama start(), que tenta ler o
telefone do terminal com input(). Num .pyw não existe terminal.

Se on_video for informado, cada vídeo baixado é entregue a ele logo depois
(em thread separada), para ser transcrito antes do próximo download.
"""

import asyncio
import os
import re
import time
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename

import settings
from utils import format_duration


class _SkipDownload(Exception):
    """Sinal interno: o usuário pulou o download atual (ou o agendamento interrompeu)."""


# Tamanho do bloco usado para retomar. O offset de retomada é sempre múltiplo disto.
CHUNK_RETOMADA = 512 * 1024


def slugify(text: str, max_len: int = 60) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:max_len].strip("_")


def is_video(msg) -> bool:
    if msg.video:
        return True
    if msg.document and msg.document.attributes:
        if any(isinstance(a, DocumentAttributeVideo) for a in msg.document.attributes):
            return True
    if msg.document and msg.document.mime_type:
        return msg.document.mime_type.startswith("video/")
    return False


def get_extension(msg) -> str:
    if msg.document and msg.document.attributes:
        for attr in msg.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                _, ext = os.path.splitext(attr.file_name)
                if ext:
                    return ext.lower()
    if msg.document and msg.document.mime_type:
        ext = msg.document.mime_type.split("/")[-1]
        return ".mkv" if ext in ("x-matroska", "mkv") else f".{ext}"
    return ".mp4"


def build_filename(msg) -> str:
    """Usa a legenda da mensagem como nome; sem legenda, usa o id da mensagem."""
    caption = slugify((msg.text or "").strip())
    ext = get_extension(msg)
    return f"{caption}{ext}" if caption else f"video_{msg.id}{ext}"


def _session_path() -> str:
    """O .session fica na pasta do projeto, e não no diretório de onde o programa foi aberto."""
    name = Path(settings.env("SESSION") or "tg_session")
    return str(name if name.is_absolute() else settings.BASE_DIR / name)


async def _login(client, ui, logger) -> bool:
    """Login manual: telefone, código e 2FA pelas caixas da interface."""
    phone = ui.ask("Login", "Número (ex: +5511999999999):")
    if not phone:
        logger.log("Login cancelado.", "err")
        return False
    await client.send_code_request(phone)
    code = ui.ask("Código", "Código recebido no Telegram:")
    if not code:
        logger.log("Login cancelado.", "err")
        return False
    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        pw = ui.ask("2FA", "Senha de dois fatores:", show="*")
        if not pw:
            logger.log("Login cancelado.", "err")
            return False
        await client.sign_in(password=pw)
    return True


async def _baixar_arquivo(client, msg, tmp: Path, final: Path, filename: str, logger, ui, ctl) -> str:
    """
    Baixa um arquivo com velocidade e progresso. Retorna 'ok', 'pulado' ou 'erro'.

    O .tmp NUNCA é apagado quando o download para (pulado, erro ou encerramento).
    Na próxima execução, o download é retomado do ponto onde parou.
    """
    ctl.skip.clear()
    ctl.atualizar("download", 0)
    documento = msg.document
    total     = documento.size
    inicio    = time.time()

    # Se já existe um .tmp de uma tentativa anterior, continua dele.
    # Telethon exige offset múltiplo de 512 KB, então recua até o último bloco inteiro.
    ja_baixado = tmp.stat().st_size if tmp.exists() else 0
    if ja_baixado > total:
        ja_baixado = 0  # tmp maior que o arquivo: está corrompido, recomeça
    offset = (ja_baixado // CHUNK_RETOMADA) * CHUNK_RETOMADA
    if offset:
        logger.log(f"[↻] Retomando {filename} a partir de {offset / (1024 * 1024):.1f} MB", "cache")

    estado = {"t": inicio, "cur": offset}

    try:
        with open(tmp, "r+b" if tmp.exists() else "wb") as f:
            f.truncate(offset)
            f.seek(offset)
            cur = offset

            async for bloco in client.iter_download(documento, offset=offset, file_size=total):
                if ctl.skip.is_set() or ctl.abortar("download"):
                    raise _SkipDownload()

                f.write(bloco)
                cur += len(bloco)

                agora = time.time()
                pct = cur * 100 / total if total else 0.0
                ctl.atualizar("download", pct)

                if agora - estado["t"] >= 0.5 or cur >= total:
                    dt = agora - estado["t"]
                    velocidade = (cur - estado["cur"]) / dt if dt > 0 else 0.0
                    estado["t"] = agora
                    estado["cur"] = cur
                    restante = (total - cur) / velocidade if (velocidade > 0 and total) else 0.0
                    decorrido = agora - inicio
                    mb_s = velocidade / (1024 * 1024)
                    ui.bar(
                        "dl_arq", pct, 100,
                        f"{filename}: {pct:.0f}%  ·  {mb_s:.2f} MB/s  ·  "
                        f"decorrido {format_duration(decorrido)}  ·  restante {format_duration(restante)}",
                    )

        if tmp.stat().st_size != total:
            # Terminou sem erro, mas o tamanho não bate: mantém o .tmp para tentar de novo.
            logger.log(f"[✗] Tamanho incorreto em {filename} (esperado {total}, obtido {tmp.stat().st_size}).", "err")
            ui.bar("dl_arq", 0, 100, f"{filename}: erro")
            return "erro"

        os.replace(tmp, final)
        ui.bar("dl_arq", 100, 100, f"{filename}: concluído")
        return "ok"
    except _SkipDownload:
        ui.bar("dl_arq", 0, 100, f"{filename}: pausado (será retomado)")
        return "pulado"
    except Exception as e:
        logger.log(f"[✗] Erro no download (o .tmp foi mantido para retomar): {e}", "err")
        ui.bar("dl_arq", 0, 100, f"{filename}: erro")
        return "erro"


def ler_baixados(arquivo: Path) -> set:
    """Nomes dos vídeos já baixados, lidos de baixados.txt (um por linha)."""
    if not arquivo.exists():
        return set()
    # utf-8-sig aceita o BOM que o Bloco de Notas do Windows às vezes grava no início.
    return {linha.strip() for linha in arquivo.read_text(encoding="utf-8-sig").splitlines() if linha.strip()}


def registrar_baixado(arquivo: Path, filename: str):
    """Acrescenta o nome do vídeo ao baixados.txt assim que o download termina."""
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with open(arquivo, "a", encoding="utf-8") as f:
        f.write(filename + "\n")


async def fetch_channel(out_dir: Path, is_transcribed, logger, ui, ctl,
                        on_video=None, on_listing=None) -> set:
    """
    is_transcribed(stem) -> bool  : vídeo já transcrito não é baixado de novo.
    on_video(path)                : chamado após cada download, se ainda for permitido
                                    iniciar novo trabalho. Opcional.
    on_listing(n_pendentes)       : informa quantos vídeos do canal ainda precisam
                                    de transcrição. Opcional.

    Retorna o conjunto de nomes (stems) de todos os vídeos do canal.
    """
    api_id    = int(settings.env("API_ID"))
    api_hash  = settings.env("API_HASH")
    channel   = settings.env("CHANNEL")
    limit_raw = settings.env("LIMIT")
    limit     = int(limit_raw) if limit_raw.isdigit() else None

    out_dir.mkdir(parents=True, exist_ok=True)
    logger.log("Conectando ao Telegram...")

    client = TelegramClient(
        _session_path(), api_id, api_hash,
        flood_sleep_threshold=0,
        request_retries=5,
    )
    # connect() não pede nada no terminal; só start() faria isso.
    await client.connect()
    try:
        if not await client.is_user_authorized():
            if not await _login(client, ui, logger):
                return set()

        me = await client.get_me()
        logger.log(f"Logado como {me.first_name}", "ok")

        ui.status("Escaneando canal...")
        entradas = []
        async for msg in client.iter_messages(channel, limit=limit):
            if is_video(msg):
                entradas.append((msg.id, build_filename(msg)))
        entradas.reverse()

        total = len(entradas)
        stems = {Path(fn).stem for _, fn in entradas}
        pendentes = sum(1 for _, fn in entradas if not is_transcribed(Path(fn).stem))
        logger.log(f"Encontrados {total} vídeos no canal ({pendentes} sem transcrição).")
        if on_listing is not None:
            on_listing(pendentes)

        # Lista de baixados vem do baixados.txt, e não da pasta de vídeos.
        arquivo_baixados = settings.path("arquivo_baixados")
        baixados = ler_baixados(arquivo_baixados)
        nomes_canal = [fn for _, fn in entradas]

        # Vídeos que já estão na pasta de saída e ainda não estão na lista entram no baixados.txt,
        # sem repetir nomes que já estão lá.
        ja_na_pasta = [fn for fn in nomes_canal if (out_dir / fn).exists() and fn not in baixados]
        for fn in ja_na_pasta:
            registrar_baixado(arquivo_baixados, fn)
            baixados.add(fn)
        if ja_na_pasta:
            logger.log(f"Adicionados ao baixados.txt (já estavam na pasta): {len(ja_na_pasta)}", "cache")
        # Conta como feito se está na lista OU se o arquivo já está na pasta (o mesmo critério do laço abaixo).
        feitos = [fn for fn in nomes_canal if fn in baixados or (out_dir / fn).exists()]
        proximos = [fn for fn in nomes_canal if fn not in baixados and not (out_dir / fn).exists()]
        logger.log(f"Lendo lista de baixados em: {arquivo_baixados}  ({len(baixados)} nomes)")
        logger.log(f"Já baixados: {len(feitos)}  ·  faltam baixar: {len(proximos)}")
        for fn in proximos[:5]:
            logger.log(f"  próximo: {fn}", "cache")
        if len(proximos) > 5:
            logger.log(f"  ... e mais {len(proximos) - 5}", "cache")

        ui.bar("dl_total", 0, total, f"Arquivos: 0/{total}")
        ui.bar("dl_arq", 0, 100, "Aguardando...")
        ctl.registrar("download", 0)
        downloaded = skipped = errors = 0

        for i, (msg_id, _) in enumerate(entradas, 1):
            if not ctl.pode_iniciar_novo("download"):
                logger.log("⚠ Download interrompido: não iniciando novos arquivos.", "warn")
                break

            msg = await client.get_messages(channel, ids=msg_id)
            if not msg:
                ui.bar("dl_total", i, total, f"Arquivos: {i}/{total}")
                continue

            filename = build_filename(msg)
            stem     = Path(filename).stem
            final    = out_dir / filename
            tmp      = out_dir / (filename + ".tmp")

            if is_transcribed(stem):
                logger.log(f"[SKIP] já transcrito: {filename}", "cache")
                skipped += 1
                ui.bar("dl_total", i, total, f"Arquivos: {i}/{total}")
                continue

            if filename in baixados or final.exists():
                # Arquivo que já estava na pasta também entra na lista, para ficar completa.
                if final.exists() and filename not in baixados:
                    registrar_baixado(arquivo_baixados, filename)
                    baixados.add(filename)
                if final.exists() and on_video is not None and ctl.pode_iniciar_novo("transcricao"):
                    logger.log(f"[→] já baixado, enviado para transcrição: {filename}", "cache")
                    on_video(final)
                else:
                    logger.log(f"[SKIP] já existe: {filename}", "cache")
                    skipped += 1
                ui.bar("dl_total", i, total, f"Arquivos: {i}/{total}")
                continue

            logger.log(f"[↓] [{i}/{total}] {filename}", "section")
            resultado = await _baixar_arquivo(client, msg, tmp, final, filename, logger, ui, ctl)

            if resultado == "ok":
                downloaded += 1
                registrar_baixado(arquivo_baixados, filename)
                baixados.add(filename)
                logger.log(f"[✓] {filename}", "ok")
            elif resultado == "pulado":
                skipped += 1
                logger.log(f"[⏭] Pulado: {filename}", "cache")
            else:
                errors += 1

            ui.bar("dl_total", i, total, f"Arquivos: {i}/{total}")

            if resultado == "ok" and on_video is not None:
                if not on_video(final):
                    logger.log("Transcrição deste arquivo ficou para a próxima execução.", "warn")

        logger.log(
            f"Download concluído: ✓{downloaded} baixados, ↷{skipped} pulados, ✗{errors} erros",
            "ok",
        )
        return stems
    finally:
        ctl.liberar("download")
        await client.disconnect()
