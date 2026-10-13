# CinematekAI - ein Image fuer Oberflaeche und Backend.
#
# Gebaut wird aus dem Repo-Wurzelordner (docker compose build tut dasselbe):
#
#     docker build -t cinematekai:latest .
#
# Stufe 1 baut die SvelteKit-Oberflaeche zu statischen Dateien, Stufe 2 ist
# das Python-Backend, das diese Dateien neben der API unter demselben Port
# ausliefert (server.py). Node wird nur zum Bauen gebraucht und landet nicht
# im fertigen Image - frueher lief dafuer ein zweiter Container dauerhaft mit.

# ---------------------------------------------------------------------------
# Stufe 1: Oberflaeche
# ---------------------------------------------------------------------------
# Node 22 (LTS) statt 20: Node 20 bekommt seit Ende April 2026 keine
# Sicherheitsupdates mehr. Betrifft nur das Bauen - im fertigen Image steckt
# kein Node.
FROM node:22-alpine AS oberflaeche
WORKDIR /src

# Erst nur die Paketlisten: solange sie sich nicht aendern, nimmt Docker die
# installierten Pakete aus dem Zwischenspeicher, und eine geaenderte
# Svelte-Datei baut erst ab dem naechsten COPY neu. npm ci haelt sich exakt
# an das Lockfile - derselbe Stand wie beim Entwickeln, kein stilles Update.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# node_modules und alte Builds haelt .dockerignore fern. Kaemen die Pakete
# vom Windows-Rechner mit, ueberschrieben sie die eben fuer Linux
# installierten (esbuild und rollup bringen je Plattform eigene Programme mit).
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stufe 2: Laufzeit
# ---------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# curl nur fuer den HEALTHCHECK. Einen Compiler (frueher build-essential)
# braucht es nicht mehr: chromadb und seine kompilierten Abhaengigkeiten
# (chroma-hnswlib 0.7.6, onnxruntime, numpy, tokenizers ...) gibt es fuer
# Python 3.12 auf x86_64 als fertige Wheels (auf PyPI nachgesehen am
# 10.10.2026). Faellt ein Bau doch auf das Kompilieren aus dem Quelltext
# zurueck, hier build-essential wieder ergaenzen.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Nur die Module der obersten Ebene. Die Wegwerf-Skripte in backend/dev/
# landen dadurch nicht im Image (und schon gar nicht im Build-Kontext, siehe
# .dockerignore).
COPY backend/*.py ./
COPY --from=oberflaeche /src/build ./static

# CINEMATEKAI_CONFIG wird nur noch fuer die einmalige Uebernahme einer alten
# config.yaml gelesen (settings.py); fehlt die Datei, startet alles trotzdem.
# PYTHONUNBUFFERED, damit Log-Zeilen sofort in `docker logs` stehen und nicht
# erst, wenn ein Puffer voll ist.
# ANONYMIZED_TELEMETRY=False schaltet die Nutzungsstatistik von Chroma ab.
# embeddings.py tut das schon selbst; die Variable gilt zusaetzlich fuer
# alles, was Chroma im Container sonst noch anlegt.
ENV CINEMATEKAI_DATA=/app/data \
    CINEMATEKAI_STATIC=/app/static \
    CINEMATEKAI_CONFIG=/app/config.yaml \
    PYTHONUNBUFFERED=1 \
    ANONYMIZED_TELEMETRY=False

EXPOSE 8095

# Bewusst ueber /api/health und nicht /health: so nimmt die Pruefung denselben
# Weg durch server.py wie jede API-Anfrage aus dem Browser (Praefix abnehmen,
# weiter an main.app). /health ohne Praefix bleibt fuer ein externes
# Monitoring erhalten, das den Dienst ohne /api-Praefix abfragt.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8095/api/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8095"]
