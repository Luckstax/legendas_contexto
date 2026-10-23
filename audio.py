"""
audio.py — Extração de áudio de vídeos via PyAV (mono, 16 kHz),
formato esperado pelo faster-whisper.
"""

from pathlib import Path


def load_audio(video_path: Path):
    import av
    import numpy as np

    c       = av.open(str(video_path))
    streams = [s for s in c.streams if s.type == "audio"]
    if not streams:
        c.close()
        raise RuntimeError("Nenhuma faixa de áudio encontrada.")

    rs     = av.AudioResampler(format="fltp", layout="mono", rate=16000)
    chunks = []

    def absorb(frame):
        if frame is None:
            return
        arr = frame.to_ndarray()
        chunks.append(arr[0] if arr.ndim == 2 else arr)

    for pkt in c.demux(streams[0]):
        for frm in pkt.decode():
            for rf in rs.resample(frm):
                absorb(rf)
    for rf in rs.resample(None):
        absorb(rf)
    c.close()

    if not chunks:
        raise RuntimeError("Áudio vazio após extração.")
    return np.concatenate(chunks)
