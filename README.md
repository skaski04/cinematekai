# CinematekAI

**English** | [Deutsch](README.de.md)

Self-hosted recommendations for your own Jellyfin library. CinematekAI picks a movie of the day, finds films
by mood, tells you how well a title fits you and chats about your collection.

Everything runs in **one container**. You configure it in the browser; there is no configuration file.

> [!WARNING]
> **Vibe-coded, not reviewed.** CinematekAI was written almost entirely with an AI coding assistant ("vibe
> coding"). The code has not been reviewed line by line by an experienced developer. There is no guarantee of
> security or correctness - use it at your own risk, ideally only in your home network and not directly reachable
> from the internet.

*CinematekAI is an independent hobby project and is not affiliated with CINEMATEK, the Royal Film Archive of
Belgium.*

## Features

- **Movie of the day** - an AI curator picks one film per user from your library and explains the choice.
- **Search by mood** - describe what you feel like ("a funny animal film for kids") and get matching titles.
  Genres are recognised from the request and narrow the search.
- **Match in percent** - every film shows how well it fits you, based on what you have watched and rated.
- **Chat about your collection** - ask for suggestions and the answer comes from the films you actually own.
- **Movies and series**, favourites, and sign-in with the Jellyfin accounts you already have.
- **German and English** interface, switchable per user.
- **AI log** - every call to a language model is recorded for 180 days (success rate, response times, how often
  the fallback model had to step in, unusable answers), so you can see whether a model holds up.

### Optional: TMDB

With a free [TMDB](https://www.themoviedb.org) token, the start page also shows **Missing from your collection**: good
films you do not have yet, taken from TMDB's "people who liked this also liked" lists for the titles you have
watched. With a [Seerr](https://github.com/seerr-team/seerr) address (formerly Jellyseerr) set, each title links
straight to its page in Seerr, where you can request it.

## Requirements

- A **Jellyfin** server and an account with administrator rights (used once, for setup).
- A **language model provider** for the AI features (chat, curator, explanations). Without one, the daily pick
  stays purely mechanical.
- An **embedding model** for search and match values, for example [Ollama](https://ollama.com) running locally
  with `bge-m3`:

```bash
ollama pull bge-m3
```

Optional: a free [TMDB](https://www.themoviedb.org) API read access token (needed for "Missing from your collection")
and a Seerr address.

## Quick start

With `docker run`:

```bash
docker run -d --name cinematekai --restart unless-stopped -p 8095:8095 -v ./data:/app/data ghcr.io/skaski04/cinematekai:latest
```

Or with Compose - clone or download this repository, then, in the folder with `docker-compose.yml`:

```bash
docker compose up -d
```

Open `http://<your-server>:8095`. The first visit starts the setup wizard.

- Set the time zone of the daily jobs with `-e TZ=Europe/London` (Compose: `TZ` in the file). The default is
  `Europe/Berlin`; the wizard also offers your browser's zone.
- **Local trailers (optional):** mount your media folder read-only at the path under which Jellyfin sees it, for
  example `-v /path/to/media:/media:ro`, and, if that path is not `/media`, enter it under *Admin -> Settings* as
  "Media path in the container".

## Setup in the browser

The wizard has five steps. Everything can be changed later under **Admin -> Settings**, without a restart.

1. **Jellyfin** - enter the address (as the container sees it, see below) and sign in with a Jellyfin
   administrator. CinematekAI creates its own API key in Jellyfin; your password is not stored. The person who
   sets it up becomes the first admin of CinematekAI; further Jellyfin users are added to an allowlist by an admin.
2. **Language model** - for chat, the curator and explanations. Optional.
3. **Embeddings** - for search and match values. Without them there is no search by mood.
4. **Extras** - TMDB token, Seerr address, the time of the nightly run and its time zone, and an optional
   fallback model that takes over when the main model fails.
5. **Read the library** - runs the whole chain once (sync, embed, daily picks). With a local embedding model on a
   CPU this can take hours for a large library; progress is shown.

## Supported providers

Chat and embeddings are configured separately, so you can mix them (for instance a cloud chat model with local
embeddings).

| Provider | Chat | Embeddings | Note |
|---|:-:|:-:|---|
| OpenAI | yes | yes | e.g. `text-embedding-3-small` |
| Anthropic (Claude) | yes | - | no embedding service; use e.g. Voyage AI for that |
| Google Gemini | yes | yes | key from Google AI Studio; e.g. `gemini-embedding-001` |
| Mistral AI | yes | yes | `mistral-embed` |
| DeepSeek | yes | - | |
| OpenRouter | yes | - | many models under one key |
| Groq | yes | - | |
| Together AI | yes | yes | |
| Fireworks AI | yes | yes | |
| xAI (Grok) | yes | - | |
| Cerebras | yes | - | |
| Cohere | yes | yes | e.g. `embed-multilingual-v3.0` |
| ArliAI | yes | - | the Core plan allows only one request at a time |
| Voyage AI | - | yes | e.g. `voyage-3.5` |
| Jina AI | - | yes | e.g. `jina-embeddings-v3` |
| Azure OpenAI | yes | yes | address `https://<resource>.openai.azure.com/openai/v1`, model = name of the deployment |
| Ollama | yes | yes | talked to through its own API so the context window can be set |
| LM Studio | yes | yes | |
| Other (OpenAI-compatible) | yes | yes | vLLM, LocalAI, llama.cpp, LiteLLM and anything that speaks `/chat/completions` |

**Context window.** The AI curator and the full library catalogue in the chat need a main model with a
context window of at least 16,000 tokens (field "Context window (tokens)" in the provider form). With a
smaller window the curator is skipped, so the daily pick stays mechanical, and the chat works without the
catalogue.

## Good to know

- **`localhost` inside the container is the container itself.** If Jellyfin, Ollama or LM Studio run on the
  host or on another machine, enter that machine's network address (for example `http://<host>:11434`),
  not `localhost`.
- **Cloud embeddings send data to the provider.** Title, plot, cast and genres of every film in your library are
  sent to it. Cloud language models see whatever the chat or curator puts into a prompt, such as titles. If that
  is not acceptable, use local models.
- **A new embedding model means re-embedding the whole library.** The vectors of the old model are kept, so
  switching back is instant. The percentage scale is re-measured for the new model (Admin -> Settings ->
  Embeddings -> "Re-measure scale").
- **Keys never leave the backend.** The interface only shows whether a key is set and its last four characters. If you
  change the provider or address without entering a new key, the old key is discarded so that it cannot reach
  the wrong provider.

## Data and backup

Everything the app keeps - database, vector index, settings including API keys - lives in `/app/data`
(`./data` in the examples above). **That folder is all you need to back up.** Stop the container while you copy
it, or the copy may be inconsistent. Treat it as confidential: it contains your API keys.

Lists fetched from TMDB and all cached suggestions are deleted automatically after 170 days at the latest, because
TMDB's terms of use allow caching its data for no more than six months.

## Updating

With Compose:

```bash
docker compose pull && docker compose up -d
```

With `docker run`: fetch the new image, remove the old container and start it again with the same command as in
the quick start (your data in `./data` stays):

```bash
docker pull ghcr.io/skaski04/cinematekai:latest
```

```bash
docker rm -f cinematekai
```

Your data is kept; database changes are applied automatically at start. Besides `latest` there are fixed tags such
as `1`, `1.0` and `1.0.0` if you prefer to update deliberately.

## Build it yourself

```bash
docker build -t cinematekai .
```

To build with Compose instead, uncomment `build: .` in `docker-compose.yml` and run `docker compose up -d --build`.
Local additions to the Compose file (paths, ports, limits) can go into a `docker-compose.override.yml` next to it,
which Compose loads automatically.

## Development

Backend (Python 3.12), from the repository root:

```bash
pip install -r backend/requirements.txt
```

```bash
cd backend && CINEMATEKAI_DATA=../data CINEMATEKAI_STATIC=../frontend/build uvicorn server:app --port 8095
```

Frontend (Node 20 or newer, SvelteKit):

```bash
cd frontend && npm install && npm run dev
```

The Vite dev server (port 5173) forwards `/api` to `http://localhost:8095`; set `BACKEND_URL` to use another
target. `npm run build` creates the static interface in `frontend/build`, which the backend serves when
`CINEMATEKAI_STATIC` points to it.

| Environment variable | Meaning | Default |
|---|---|---|
| `CINEMATEKAI_DATA` | folder for database, vectors and settings | `/app/data` |
| `CINEMATEKAI_STATIC` | built web interface that the backend serves | `/app/static` |
| `CINEMATEKAI_CORS_REGEX` | pattern for allowed cross-origin sites; only needed if the interface is served from a different address than the API | localhost, private IP ranges and common local host-name suffixes |
| `TZ` | default time zone of the daily jobs | `Europe/Berlin` |

## Ports and endpoints

| What | Where |
|---|---|
| Web interface and API | port `8095` (the API under `/api/...`) |
| Health check | `/health` returns `{"ok": true}` (the container's own check uses `/api/health`) |
| Metrics | `/metrics` in Prometheus text format: counters and states only, no titles or names |

`/health` and `/metrics` need no sign-in so that monitoring tools can reach them. Do not publish the port to the
internet without a reverse proxy that restricts access.

## Third-party

- **Fonts** - *Cormorant Garamond* and *DM Sans* are bundled under `frontend/static/fonts` so the interface makes
  no requests to external font servers. Both are licensed under the
  [SIL Open Font License 1.1](https://openfontlicense.org); copyright notices and the license text are in
  `frontend/static/fonts/OFL-CormorantGaramond.txt` and `frontend/static/fonts/OFL-DMSans.txt`.
- **Movie data** - titles and posters in the "Missing from your collection" list, and the "people who liked this
  also liked" lists that feed into the recommendations, come from [TMDB](https://www.themoviedb.org). This product
  uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.
- **TMDB logo** - `frontend/static/tmdb/tmdb-logo.svg` is TMDB's unmodified "Alt short (blue)" logo from its
  [logos & attribution](https://www.themoviedb.org/about/logos-attribution) page. The logo is a trademark of TMDB and
  is used according to TMDB's attribution rules: together with the notice above in the credits at the bottom of the
  pages, and as a source mark next to "Missing from your collection", always smaller than the CinematekAI logo. It is
  not covered by this project's license.
- **Software** - FastAPI, SQLModel, ChromaDB, APScheduler, SvelteKit and other open-source packages; see
  `backend/requirements.txt` and `frontend/package.json` for the full list.

## License

CinematekAI is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).

Free to use for private use, education, research and charitable or other noncommercial organizations. **Commercial
use is not permitted.** If you want to use CinematekAI commercially, get in touch through GitHub.
