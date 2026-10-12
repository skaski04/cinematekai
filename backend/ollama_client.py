"""Ollama-Protokoll fuer den Chat — Adresse und Modell kommen vom Aufrufer.

Frueher standen Adresse und Modell fest aus der config.yaml hier drin. Seit
die Einstellungen in der Oberflaeche liegen, kann Ollama Hauptmodell,
Rueckfall oder gar nicht im Spiel sein; llm_client.py entscheidet, welche
Verbindung gilt, und reicht sie herein. Einbettungen laufen ueber anbieter.py.
"""
import json
import logging
from typing import Iterator

import httpx

log = logging.getLogger("cinematekai.ollama")

# Per-stream timeouts — separate so a stuck model doesn't hang forever.
# read_timeout is per-chunk: if no token arrives for that many seconds, abort.
# Hermes3 8b on CPU is slow — first-token latency can be 2–5 min when other
# 8b models are co-resident. We deliberately keep httpx's read timeout
# loose; the chat_engine adds an inter-chunk watchdog separately.
_DEFAULT_READ_TIMEOUT = 360.0     # generous: covers cold first-token wait
_DEFAULT_CONNECT_TIMEOUT = 15.0
_DEFAULT_WRITE_TIMEOUT = 30.0


def _build_timeout(read: float = _DEFAULT_READ_TIMEOUT) -> httpx.Timeout:
    return httpx.Timeout(
        connect=_DEFAULT_CONNECT_TIMEOUT,
        read=read,
        write=_DEFAULT_WRITE_TIMEOUT,
        pool=_DEFAULT_CONNECT_TIMEOUT,
    )


def _body(model: str, messages: list[dict], stream: bool, num_ctx: int,
          temperature: float) -> dict:
    # Ohne explizites num_ctx faellt Ollama auf 4096 zurueck. Das reicht
    # nicht fuer System-Prompt + RAG-Kandidaten + Verlauf — das Modell
    # verliert dann die Formatanweisungen und plappert den Kontext nach.
    return {
        "model": model,
        "messages": messages,
        "stream": stream,
        "keep_alive": "30m",
        "options": {"num_ctx": int(num_ctx), "temperature": float(temperature)},
    }


def chat(url: str, model: str, messages: list[dict], *, headers: dict | None = None,
         num_ctx: int = 8192, temperature: float = 0.7, timeout: float = 300.0) -> str:
    """Ganze Antwort als Text."""
    with httpx.Client(timeout=_build_timeout(read=timeout)) as c:
        r = c.post(f"{url}/api/chat", headers=headers,
                   json=_body(model, messages, False, num_ctx, temperature))
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")


def chat_stream(url: str, model: str, messages: list[dict], *, headers: dict | None = None,
                num_ctx: int = 8192, temperature: float = 0.7,
                read_timeout: float = _DEFAULT_READ_TIMEOUT) -> Iterator[str]:
    """Antwort Stueck fuer Stueck. Bricht ab, wenn ``read_timeout`` Sekunden
    lang nichts kommt (httpx ReadTimeout)."""
    with httpx.Client(timeout=_build_timeout(read=read_timeout)) as c:
        with c.stream("POST", f"{url}/api/chat", headers=headers,
                      json=_body(model, messages, True, num_ctx, temperature)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content
                if chunk.get("done"):
                    break


def warm(url: str, model: str, headers: dict | None = None) -> bool:
    """Modell einmal laden, damit der erste echte Aufruf nicht kalt startet.
    Ollama haelt es danach ueber keep_alive im Speicher."""
    try:
        with httpx.Client(timeout=600.0) as c:
            c.post(f"{url}/api/generate", headers=headers, json={
                "model": model,
                "prompt": "hi",
                "stream": False,
                "keep_alive": "30m",
            })
        log.info("Ollama-Modell %s vorgewaermt", model)
        return True
    except Exception as e:
        log.warning("Vorwaermen von %s fehlgeschlagen: %s", model, e)
        return False
