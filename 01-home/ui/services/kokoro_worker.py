"""Kokoro voice worker -- Anam's own, no Docker."""
import io
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

# THREADS, PINNED ON PURPOSE. Left to its defaults inside the Anam stack, the
# worker burned 75 CPU-seconds for 12 seconds of audio (24s wall) while the
# same model alone did it in 3.5s: OpenMP threads spin-waiting against forty
# other Python processes on this box. Half the cores, no spin (KMP_BLOCKTIME=0),
# is the setting that measured fastest AND kindest to everything else running.
_THREADS = str(max(2, min(8, (os.cpu_count() or 4) // 2)))
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, _THREADS)
os.environ.setdefault("KMP_BLOCKTIME", "0")
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")

import torch  # noqa: E402  (after the env pins; torch reads them at import)

torch.set_num_threads(int(_THREADS))

_pipelines: dict = {}


def _lang_code(voice: str) -> str:
    return "b" if voice.startswith("b") else "a"


def _pipeline(voice: str):
    code = _lang_code(voice)
    if code not in _pipelines:
        from kokoro import KPipeline

        try:
            _pipelines[code] = KPipeline(lang_code=code, repo_id="hexgrad/Kokoro-82M")
        except TypeError:
            _pipelines[code] = KPipeline(lang_code=code)
    return _pipelines[code]


def synthesize(text: str, voice: str, speed: float) -> bytes:
    import numpy as np
    import soundfile as sf

    parts = [audio for _g, _p, audio in _pipeline(voice)(text, voice=voice, speed=speed)]
    if not parts:
        return b""
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(parts), 24000, format="WAV")
    return buf.getvalue()


def main() -> None:
    out = sys.stdout.buffer
    for line in sys.stdin.buffer:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line.decode("utf-8"))
        rid = req.get("id")
        try:
            wav = synthesize(str(req.get("text") or ""), str(req.get("voice") or "am_fenrir"),
                             float(req.get("speed") or 1.0))
            header = {"id": rid, "bytes": len(wav)}
        except Exception as exc:  # the parent decides what to do; the worker keeps serving
            wav = b""
            header = {"id": rid, "bytes": 0, "error": "%s: %s" % (type(exc).__name__, exc)}
        out.write((json.dumps(header) + "\n").encode("utf-8"))
        if wav:
            out.write(wav)
        out.flush()


if __name__ == "__main__":
    main()
