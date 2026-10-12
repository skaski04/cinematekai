"""Ein Prozess, ein Port: API und Oberflaeche aus demselben Container.

Bis Oktober 2026 liefen zwei Container — das SvelteKit-Frontend (Node) auf
Port 8095, das per Proxy-Route alles unter /api an das Backend auf Port 8094
weiterreichte. Jetzt ist die Oberflaeche statisch gebaut, und dieser kleine
ASGI-Verteiler liefert beides aus. Start:

    uvicorn server:app --host 0.0.0.0 --port 8095

Verteilt wird so:

  * ``lifespan`` geht an ``main.app``. Sonst liefen dessen Startup-Handler
    (Datenbank, Migrationen, Zeitplan) nie — uvicorn schickt das Ereignis nur
    an die oberste App, und das ist diese hier.
  * ``/api`` und ``/api/...`` gehen an ``main.app``, mit abgenommenem
    Praefix. Das Backend kennt seine Routen ohne /api (so wie bisher hinter
    dem Proxy des Frontends), nichts dort musste sich aendern.
  * ``/health`` und ``/metrics`` gehen unveraendert an ``main.app``. So
    erreichen ein Monitoring (``/health``) und ein Prometheus-Scraper
    (``/metrics``) sie weiter ohne /api-Praefix - auch ueber einen
    zusaetzlich veroeffentlichten Port wie den frueheren Backend-Port 8094.
  * Alles andere sind Dateien der gebauten Oberflaeche aus
    ``config.STATIC_DIR`` — oder ``index.html``, damit ein Neuladen auf
    /movies/123 die Seite zeigt statt 404 (SPA-Fallback).

Websockets gibt es in CinematekAI nicht; der Chat streamt per SSE ueber
normales HTTP.
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

import anyio
from starlette.datastructures import Headers
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.websockets import WebSocketClose

import config
import main as _backend

_API = "/api"
_API_ROH = _API.encode("ascii")
_DURCHREICHEN = frozenset({"/health", "/metrics"})

# SvelteKit legt alles mit Inhalts-Hash im Namen unter _app/immutable ab: eine
# neue Version bekommt neue Namen, die alte Datei aendert sich nie. Die darf
# der Browser ein Jahr behalten. Alles andere (index.html, favicon,
# _app/version.json) muss er bei jedem Aufruf neu pruefen — sonst zeigte er
# nach einem Update die alte Seite mit Verweisen auf Dateien, die es nicht
# mehr gibt.
_IMMUTABLE = "/_app/immutable/"
_CACHE_FUER_IMMER = "public, max-age=31536000, immutable"
_CACHE_NEU_PRUEFEN = "no-cache"

# Eigene Zuordnung statt nur mimetypes: das liest je nach System die Tabelle
# des Betriebssystems (unter Windows die Registry, die .js gern als text/plain
# fuehrt). Ein Skript mit falschem Typ fuehrt der Browser als Modul nicht aus.
_TYPEN = {
    ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
    ".css": "text/css", ".json": "application/json", ".map": "application/json",
    ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".avif": "image/avif", ".gif": "image/gif",
    ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".otf": "font/otf", ".txt": "text/plain",
    ".xml": "application/xml", ".wasm": "application/wasm",
}

_NICHT_GEBAUT = "Oberfläche nicht gebaut"


# ---------------------------------------------------------------------------
# Verteiler
# ---------------------------------------------------------------------------

async def app(scope, receive, send):
    art = scope["type"]
    if art == "lifespan":
        await _backend.app(scope, receive, send)
        return

    pfad = scope.get("path") or "/"
    if pfad == _API or pfad.startswith(_API + "/"):
        await _backend.app(_ohne_praefix(scope), receive, send)
        return
    if pfad in _DURCHREICHEN:
        await _backend.app(scope, receive, send)
        return
    if art != "http":
        # Ein Websocket ausserhalb von /api: es gibt keinen. Sauber schliessen
        # statt die Verbindung haengen zu lassen (uvicorn antwortet dann 403).
        await WebSocketClose()(scope, receive, send)
        return
    await _statisch(scope, receive, send)


def _ohne_praefix(scope: dict) -> dict:
    """Kopie des Scopes ohne /api vorn in ``path`` und ``raw_path``.

    ``root_path`` bekommt das /api dazu. Fuer das Routing in FastAPI zaehlt nur
    ``path``; ``root_path`` sorgt dafuer, dass die automatische API-Doku unter
    /api/docs ihre openapi.json unter /api/openapi.json sucht und nicht bei
    der Oberflaeche.
    """
    neu = dict(scope)
    rest = scope["path"][len(_API):] or "/"
    neu["path"] = rest
    roh = scope.get("raw_path")
    if roh is not None:
        if roh == _API_ROH or roh.startswith(_API_ROH + b"/"):
            neu["raw_path"] = roh[len(_API_ROH):] or b"/"
        else:
            # Praefix im Rohpfad kodiert (z. B. "/%61pi/..."): aus dem schon
            # dekodierten Rest neu bilden, damit path und raw_path
            # zusammenpassen.
            neu["raw_path"] = quote(rest).encode("ascii")
    neu["root_path"] = scope.get("root_path", "") + _API
    return neu


# ---------------------------------------------------------------------------
# Gebaute Oberflaeche
# ---------------------------------------------------------------------------

def _wurzel() -> Path | None:
    """STATIC_DIR, aufgeloest, oder ``None``, wenn es ihn nicht gibt.

    Bei jedem Aufruf neu: so faellt ein spaeter eingehaengter Ordner (etwa
    beim Entwickeln) ohne Neustart auf, und der Test kann den Ort setzen.
    """
    try:
        wurzel = Path(config.STATIC_DIR).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return wurzel if wurzel.is_dir() else None


def _nachschlagen(wurzel: Path, pfad: str) -> tuple[Path, os.stat_result] | None:
    """Datei zum URL-Pfad, oder ``None``.

    Nur Dateien, die nach Aufloesen aller Symlinks innerhalb der Wurzel
    liegen. ``..`` wird schon vorher abgewiesen; die Pruefung nach dem
    Aufloesen faengt zusaetzlich Symlinks, die nach draussen zeigen, und unter
    Windows Pfade mit Laufwerksbuchstaben ("/C:/...") ab, die beim Verketten
    die Wurzel ersetzen wuerden.
    """
    rel = pfad.lstrip("/")
    if not rel or rel.endswith("/") or "\x00" in rel or "\\" in rel:
        return None
    if any(teil in ("", ".", "..") for teil in rel.split("/")):
        return None
    try:
        echt = (wurzel / rel).resolve(strict=True)
        if not echt.is_relative_to(wurzel) or not echt.is_file():
            return None
        return echt, echt.stat()
    except (OSError, RuntimeError, ValueError):
        return None


def _typ(datei: Path) -> str:
    return (_TYPEN.get(datei.suffix.lower())
            or mimetypes.guess_type(datei.name)[0]
            or "application/octet-stream")


def _datei_antwort(scope, datei: Path, st: os.stat_result, cache: str) -> Response:
    antwort = FileResponse(datei, media_type=_typ(datei), stat_result=st,
                           headers={"Cache-Control": cache})
    # "no-cache" heisst: der Browser fragt jedes Mal nach, schickt aber das
    # ETag mit. Stimmt es, reicht ein leeres 304 statt der ganzen Datei.
    etag = antwort.headers.get("etag")
    gefragt = Headers(scope=scope).get("if-none-match")
    if etag and gefragt:
        kandidaten = {t.strip().removeprefix("W/") for t in gefragt.split(",")}
        if etag in kandidaten or "*" in kandidaten:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache})
    return antwort


async def _statisch(scope, receive, send):
    methode = scope.get("method", "GET").upper()
    if methode not in ("GET", "HEAD"):
        antwort: Response = PlainTextResponse("Methode nicht erlaubt", status_code=405,
                                              headers={"Allow": "GET, HEAD"})
        await antwort(scope, receive, send)
        return

    pfad = scope.get("path") or "/"
    # Dateisystemzugriffe im Thread: die Ereignisschleife bedient sonst
    # waehrenddessen auch keinen Chat-Strom.
    wurzel = await anyio.to_thread.run_sync(_wurzel)
    if wurzel is None:
        antwort = PlainTextResponse(_NICHT_GEBAUT, status_code=404,
                                    headers={"Cache-Control": _CACHE_NEU_PRUEFEN})
        await antwort(scope, receive, send)
        return

    gefunden = await anyio.to_thread.run_sync(_nachschlagen, wurzel, pfad)
    if gefunden is not None:
        datei, st = gefunden
        cache = _CACHE_FUER_IMMER if pfad.startswith(_IMMUTABLE) else _CACHE_NEU_PRUEFEN
        antwort = _datei_antwort(scope, datei, st, cache)
    elif pfad.startswith("/_app/"):
        # Fehlendes Skript oder Stylesheet (alter Tab nach einem Update): ehrlich
        # 404. index.html mit 200 an dieser Stelle liesse der Browser als
        # Skript scheitern, mit einer irrefuehrenden Meldung zum MIME-Typ.
        antwort = PlainTextResponse("Nicht gefunden", status_code=404,
                                    headers={"Cache-Control": _CACHE_NEU_PRUEFEN})
    else:
        index = await anyio.to_thread.run_sync(_nachschlagen, wurzel, "/index.html")
        if index is None:
            antwort = PlainTextResponse(_NICHT_GEBAUT, status_code=404,
                                        headers={"Cache-Control": _CACHE_NEU_PRUEFEN})
        else:
            antwort = _datei_antwort(scope, index[0], index[1], _CACHE_NEU_PRUEFEN)
    await antwort(scope, receive, send)
