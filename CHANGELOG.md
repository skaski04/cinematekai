# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-10-14

> **Updating from 1.0.x? Export your vector index first**, or it will be rebuilt from scratch (with a local
> embedding model on a CPU that can take hours). While the old version is still running, from the folder with this
> repository:
>
> ```bash
> docker exec -i cinematekai python - < tools/chroma_export.py
> ```
>
> Then update as usual; 1.1.0 imports the export on its first start. Without an export nothing breaks - search and
> match values fill up again as the library is re-embedded.

### Changed

- **Own vector store instead of ChromaDB.** Vectors now live in a table of the app's SQLite database and are searched
  exactly (ChromaDB searched approximately). Results and match percentages stay the same.
- **Much smaller image:** 31 instead of about 95 Python packages; ChromaDB with onnxruntime, Kubernetes client,
  gRPC and its telemetry library is gone (estimated ~90 MB instead of 163 MB compressed).
- Build tools of the web interface updated (Vite 6.4); this closes the open security advisories for Vite, esbuild,
  source-map-js and cookie. All of them only affected building or the local development server.

### Removed

- The ChromaDB dependency, including its three security advisories (they concerned ChromaDB's server mode, which
  CinematekAI never used) and the telemetry error lines in the log.

## [1.0.1] - 2026-10-13

### Security

- The image proxy (`/api/image/...`) now requires a signed-in user, accepts only Jellyfin item IDs, encodes them
  before passing them on and returns raster images only. Before, anyone who could reach the port could make the
  backend send arbitrary requests to Jellyfin with its admin key.
- `/api/status` and `/api/ollama/status` require a signed-in user.
- Failed sign-ins no longer reveal the internal Jellyfin address; wrong credentials and an unreachable Jellyfin
  are reported separately.
- CORS is off by default (interface and API share one origin); set `CINEMATEKAI_CORS_REGEX` only if you serve the
  interface from another address.
- Security headers on every response (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`).
- The interactive API documentation is off by default; `CINEMATEKAI_API_DOCS=1` turns it on.
- Telemetry of the bundled vector database (Chroma) is switched off.

### Added

- **Per-account visibility**: everyone only sees and gets recommended what their own Jellyfin account may see
  (library access, parental rating, blocked tags) - in lists, search, recommendations, chat, images and trailers.
  The allowed titles are read at every sync; until the first sync after the update nothing is filtered.
- The running version is shown in the credits and returned by `/health`.

### Changed

- The web interface is built with Node 22 (Node 20 is end-of-life).
- Local trailers use the current Jellyfin item route instead of a deprecated one.
- Removed the unused `python-multipart` dependency.

## [1.0.0] - 2026-10-12

First public release.

### Added

- **Movie of the day** with an AI curator that picks a film per user from the library and explains the choice.
  Without a suitable language model the pick stays mechanical.
- **Search by mood** with genre detection, based on embeddings of the library.
- **Match in percent** for every film, derived from each user's watch history and ratings. The scale measures
  itself again whenever the embedding model changes.
- **Chat about your collection**, streamed, with an optional fallback model that takes over when the main model
  fails or stays silent.
- **"Missing from your collection"** (optional, needs a TMDB token): good films that are not in the library yet,
  taken from TMDB's "people who liked this also liked" lists, with an optional link to a Seerr instance.
- Browsing of **movies and series**, favourites, sign-in with Jellyfin accounts and an allowlist managed by admins.
- **German and English** interface, switchable per user, with a configurable default language.
- **AI log** in the admin area: every language model call for 180 days, with success rate, response times,
  fallback use and unusable answers.
- **Setup wizard** (`/setup`) and **Admin -> Settings**: Jellyfin, language model, embeddings, TMDB, Seerr,
  schedule, fallback model and media path are all set in the browser and stored in the database. No configuration
  file is needed, and changes apply without a restart.
- **Any common AI provider**: OpenAI, Anthropic, Google Gemini, Mistral, DeepSeek, OpenRouter, Groq, Together AI,
  Fireworks AI, xAI, Cerebras, Cohere, ArliAI, Voyage AI, Jina AI, Azure OpenAI, Ollama, LM Studio and any
  OpenAI-compatible service. Chat and embeddings are configured separately.
- **Nightly job chain** (library sync, watch state, embeddings, daily picks, chat suggestions) with a configurable
  time and time zone.
- **Single container** serving the API and the web interface on port 8095, with a health check, `/health` and a
  Prometheus-style `/metrics` endpoint.
- Installable as a web app on phones (manifest and icons).
- Fonts are bundled, so the interface makes no requests to external font servers.
- **TMDB attribution** as TMDB's terms require: a small credits footer with the official TMDB logo (bundled, not
  loaded from TMDB) and the required notice (the German interface also shows its original English wording), plus the
  logo as a source mark next to "Missing from your collection".
- **TMDB data retention**: lists fetched from TMDB and all cached suggestions are deleted after 170 days at the
  latest, so nothing from TMDB is kept longer than the six months TMDB's terms of use allow. The cleanup runs daily,
  also when the nightly job chain is switched off.
- Published as a container image on the GitHub Container Registry (`ghcr.io`), built by a GitHub Actions workflow
  on version tags.

### Notes

- Licensed under the PolyForm Noncommercial License 1.0.0; commercial use is not permitted.
- Installations that still have a `config.yaml` from before the settings moved into the database have its values
  imported once at first start; the file is not read afterwards.
