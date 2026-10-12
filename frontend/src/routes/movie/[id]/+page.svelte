<script>
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { t, sprache, locale, datum, genre as genreName } from '$lib/i18n.svelte.js';

  let detail = $state(null);
  let similar = $state([]);
  let loading = $state(true);
  let error = $state('');
  let favPending = $state(false);
  let favError = $state('');
  /* favError haelt nur, WAS schiefging; der Text entsteht erst hier, damit er
     beim Umschalten der Sprache mitwechselt. */
  const favFehlerText = $derived(({
    bewertung: t('film.fehlerBewertung'),
    merkliste: t('film.fehlerMerkliste'),
    favorit: t('film.fehlerFavorit'),
  })[favError] ?? '');
  let wlPending = $state(false);
  let ratingPending = $state(false);
  let ratingHover = $state(0);

  const STARS = [1, 2, 3, 4, 5];
  // Jellyfin speichert 0-10, angezeigt werden 5 Sterne.
  const toStars = (r) => (r == null ? 0 : Math.round(r / 2));

  async function setRating(stars) {
    if (!detail || ratingPending) return;
    const current = toStars(detail.watch_state.user_rating);
    // Nochmal auf denselben Stern tippen = Bewertung zuruecknehmen.
    const value = stars === current ? null : stars * 2;
    const before = detail.watch_state.user_rating;
    ratingPending = true; favError = '';
    detail.watch_state.user_rating = value;
    try {
      const r = await fetch(`/api/movies/${id}/rating`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ rating: value }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
    } catch (e) {
      detail.watch_state.user_rating = before;
      favError = 'bewertung';
    } finally {
      ratingPending = false;
    }
  }

  async function toggleWatchlist() {
    if (!detail || wlPending) return;
    const next = !detail.watch_state.watchlist;
    wlPending = true; favError = '';
    detail.watch_state.watchlist = next;
    try {
      const r = await fetch(`/api/watchlist/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ on: next, media_type: 'movie' }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
    } catch (e) {
      detail.watch_state.watchlist = !next;
      favError = 'merkliste';
    } finally {
      wlPending = false;
    }
  }

  let id = $derived($page.params.id);

  async function toggleFavorite() {
    if (!detail || favPending) return;
    const next = !detail.watch_state.favorite;
    favPending = true; favError = '';
    // Optimistisch umschalten, bei Fehler zuruecknehmen — der Aufruf geht bis
    // nach Jellyfin durch und braucht spuerbar laenger als ein Klick.
    detail.watch_state.favorite = next;
    try {
      const r = await fetch(`/api/movies/${id}/favorite`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ favorite: next }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
    } catch (e) {
      detail.watch_state.favorite = !next;
      favError = 'favorit';
    } finally {
      favPending = false;
    }
  }

  /* Zaehlt jeden Seitenaufruf. Antworten eines Films, den man inzwischen
     verlassen hat, werden verworfen — sonst blieben die Listen weiter unten
     beim Vorgaenger stehen, wenn dessen Abfrage spaeter zurueckkam. */
  let lauf = 0;

  async function loadDetail(mid, meins) {
    loading = true; error = '';
    try {
      const r = await fetch(`/api/movies/${mid}/detail`);
      if (r.status === 401) { window.location.href = '/login'; return; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      if (meins !== lauf) return;
      detail = d;
    } catch (e) {
      if (meins !== lauf) return;
      error = String(e);
    } finally {
      if (meins === lauf) loading = false;
    }
  }

  // "Mehr von ...": andere Filme desselben Regisseurs. Die Detailseite endete
  // bisher nach den aehnlichen Filmen; der naheliegendste Anschluss fehlte.
  let vonRegie = $state([]);
  let regieName = $state('');

  async function loadVonRegie(mid, meins) {
    const regie = detail?.movie?.directors?.[0];
    if (!regie || meins !== lauf) return;
    try {
      const r = await fetch(`/api/movies?q=${encodeURIComponent(regie)}&limit=60`,
                            { credentials: 'include' });
      if (!r.ok) return;
      const d = await r.json();
      if (meins !== lauf) return;
      const treffer = (d.items || [])
        .filter(m => m.id !== mid && (m.directors || []).includes(regie));
      if (treffer.length) { regieName = regie; vonRegie = treffer.slice(0, 8); }
    } catch {}
  }

  async function loadSimilar(mid, meins) {
    try {
      const r = await fetch(`/api/recommendations/similar/${mid}?k=8`);
      if (r.ok) {
        const d = await r.json();
        if (meins !== lauf) return;
        similar = d.unseen_only?.length ? d.unseen_only : (d.similar || []);
      }
    } catch {}
  }

  /* Der einzige Ladeweg. Vorher holten onMount und dieses $effect beim
     Aufruf dieselben Daten doppelt — das $effect laeuft nach dem Einhaengen
     ohnehin und deckt den Wechsel von Film zu Film gleich mit ab. */
  $effect(() => {
    const mid = id;
    if (!mid) return;
    const meins = ++lauf;
    similar = []; vonRegie = []; regieName = '';
    loadDetail(mid, meins).then(() => loadVonRegie(mid, meins));
    loadSimilar(mid, meins);
  });

  /* Die Datenbank haelt ISO-Kuerzel ("de,en,fr"). Auf der Seite stehen
     deutsche Namen; was nicht in der Liste steht, bleibt als Kuerzel stehen —
     besser ein "fil" als eine erfundene Sprache. Ab fuenf Sprachen wird
     zusammengefasst, sonst frisst die Zeile den halben Block.
     In der englischen Oberflaeche kommen die Namen aus Intl.DisplayNames,
     aber nur fuer die Kuerzel dieser Liste — dieselbe Auswahl in beiden
     Sprachen, und ein unbekanntes Kuerzel bleibt auch dort ein Kuerzel. */
  const SPRACHEN = {
    de: 'Deutsch', en: 'Englisch', fr: 'Französisch', es: 'Spanisch',
    it: 'Italienisch', nl: 'Niederländisch', pt: 'Portugiesisch',
    ja: 'Japanisch', ko: 'Koreanisch', zh: 'Chinesisch', ru: 'Russisch',
    pl: 'Polnisch', tr: 'Türkisch', da: 'Dänisch', fi: 'Finnisch',
    no: 'Norwegisch', sv: 'Schwedisch', cs: 'Tschechisch', hu: 'Ungarisch',
    ro: 'Rumänisch', ca: 'Katalanisch', th: 'Thailändisch', el: 'Griechisch',
    he: 'Hebräisch', ar: 'Arabisch', hi: 'Hindi', uk: 'Ukrainisch',
    bg: 'Bulgarisch', sk: 'Slowakisch', sl: 'Slowenisch', hr: 'Kroatisch',
    sr: 'Serbisch', et: 'Estnisch', lv: 'Lettisch', lt: 'Litauisch',
    id: 'Indonesisch', ms: 'Malaiisch', vi: 'Vietnamesisch', fa: 'Persisch',
  };

  let anzeigeNamen = null;
  function sprachName(k) {
    if (!SPRACHEN[k]) return k;
    if (sprache.code === 'de') return SPRACHEN[k];
    try {
      const loc = locale();
      if (anzeigeNamen?.loc !== loc) {
        anzeigeNamen = { loc, dn: new Intl.DisplayNames([loc], { type: 'language' }) };
      }
      return anzeigeNamen.dn.of(k) || k;
    } catch {
      return k;
    }
  }

  function sprachen(roh) {
    const teile = (roh || '').split(',').map(s => s.trim()).filter(Boolean);
    if (!teile.length) return '';
    /* Deutsch zuerst: das ist die Angabe, auf die es hier ankommt. */
    teile.sort((a, b) => (a === 'de' ? -1 : b === 'de' ? 1 : 0));
    const namen = teile.map(sprachName);
    if (namen.length <= 4) return namen.join(', ');
    return t('film.sprachenMehr', { namen: namen.slice(0, 2).join(', '), n: namen.length - 2 });
  }

  function scoreColor(score) {
    if (score >= 75) return '#4caf50';
    if (score >= 50) return '#e0c068';
    if (score >= 30) return '#e09048';
    return '#a05050';
  }

  // Circumference for r=52 circle
  const R = 52;
  const CIRC = 2 * Math.PI * R;
</script>

<svelte:head><title>{detail?.movie?.name ?? t('film.film')} — CinematekAI</title></svelte:head>

<div class="film" id="inhalt">
  <a class="back" href="/movies">{t('film.zurueckZuFilmen')}</a>

  {#if loading}
    <div class="loading">{t('film.lade')}</div>
  {:else if error}
    <div class="err">
      {String(error).includes('404')
        ? t('film.gibtEsNicht')
        : t('film.nichtGeladen', { fehler: error })}
      <a href="/movies">{t('film.zurFilmliste')}</a>
    </div>
  {:else if detail}
    <div class="backdrop" style="background-image: url(/api/image/{detail.movie.id}?type=Backdrop)">
      <div class="backdrop-fade"></div>
    </div>

    <div class="content">
      <div class="poster" style="background-image: url(/api/image/{detail.movie.id})"></div>

      <div class="info">
        <h1>{detail.movie.name}</h1>
        <div class="meta">
          {detail.movie.year ?? ''}
          {#if detail.movie.runtime_min}&nbsp;· {t('film.laufzeit', { n: detail.movie.runtime_min })}{/if}
          {#if detail.movie.official_rating}&nbsp;· {detail.movie.official_rating}{/if}
          {#if detail.movie.rating}&nbsp;· ★ {detail.movie.rating.toFixed(1)}{/if}
        </div>
        <div class="genres">
          {#each detail.movie.genres as g}
            <a class="tag" href={`/movies?genre=${encodeURIComponent(g)}`}>{genreName(g)}</a>
          {/each}
          {#if detail.collection}
            <span class="tag collection">{t('film.reihe', { name: detail.collection.name })}</span>
          {/if}
        </div>

        {#if detail.tagline}
          <p class="tagline">{t('film.tagline', { text: detail.tagline })}</p>
        {/if}

        <div class="match-row">
          <svg class="match-ring" viewBox="0 0 120 120">
            <circle cx="60" cy="60" r={R} fill="none" stroke="#222" stroke-width="8"/>
            <circle cx="60" cy="60" r={R} fill="none"
                    stroke={scoreColor(detail.match_score)}
                    stroke-width="8" stroke-linecap="round"
                    stroke-dasharray={CIRC}
                    stroke-dashoffset={CIRC - (detail.match_score / 100) * CIRC}
                    transform="rotate(-90 60 60)"/>
            <text x="60" y="58" text-anchor="middle" fill="#eee" font-size="28" font-weight="700">
              {detail.match_score}
            </text>
            <text x="60" y="78" text-anchor="middle" fill="#888" font-size="11">
              {t('film.match')}
            </text>
          </svg>
          <div class="match-reason">
            <div class="label">{t('film.warumFuerDich')}</div>
            <p>{detail.match_reason}</p>
            {#if detail.watch_state.played}
              <div class="state">{t('film.bereitsGesehen')}{detail.watch_state.last_played ? ` · ${datum(detail.watch_state.last_played, { year: 'numeric', month: 'numeric', day: 'numeric' })}` : ''}</div>
            {/if}
            <!-- Sterne wirken auf das Empfehlungsprofil und werden nach
                 Jellyfin gespiegelt. Nochmal auf denselben Stern = zuruecknehmen. -->
            <div class="rating-row">
              <span class="rating-label">{t('film.deineWertung')}</span>
              <div class="stars" role="group" aria-label={t('film.deineWertung')}
                   onmouseleave={() => (ratingHover = 0)}>
                {#each STARS as n}
                  <button class="star"
                          class:filled={n <= (ratingHover || toStars(detail.watch_state.user_rating))}
                          disabled={ratingPending}
                          onmouseenter={() => (ratingHover = n)}
                          onclick={() => setRating(n)}
                          aria-label={t('film.sterneAria', { n })}
                          title={n === toStars(detail.watch_state.user_rating)
                                  ? t('film.wertungZuruecknehmen')
                                  : t('film.sterneTitel', { n })}>★</button>
                {/each}
              </div>
              {#if detail.watch_state.user_rating != null}
                <span class="rating-value">{detail.watch_state.user_rating}/10</span>
              {/if}
            </div>
          </div>
        </div>

        <div class="actions">
          <a class="play" href={detail.play_url} target="_blank" rel="noopener">
            {t('film.abspielen')}
          </a>
          <!-- Vorher stand der Favoriten-Status hier nur als Text, obwohl der
               Endpunkt und der Toggle in der Kachelansicht laengst existieren. -->
          <button class="fav-btn" class:on={detail.watch_state.favorite}
                  onclick={toggleFavorite} disabled={favPending}
                  title={detail.watch_state.favorite
                          ? t('film.favoritEntfernen')
                          : t('film.favoritMarkieren')}>
            {detail.watch_state.favorite ? t('film.favorit') : t('film.alsFavorit')}
          </button>
          <button class="fav-btn wl" class:on={detail.watch_state.watchlist}
                  onclick={toggleWatchlist} disabled={wlPending}
                  title={detail.watch_state.watchlist
                          ? t('film.vonMerkliste')
                          : t('film.aufMerkliste')}>
            {detail.watch_state.watchlist ? t('film.gemerkt') : t('film.merken')}
          </button>
        </div>
        {#if favFehlerText}
          <div class="fav-error">{favFehlerText}</div>
        {/if}

        <!-- Fortschritt in der Filmreihe. Die Startseite zeigt das in
             „Weiterschauen" laengst; hier stand bisher nur der Reihen-Name. -->
        {#if detail.collection?.parts?.length > 1}
          <section class="coll-progress">
            <div class="coll-head">
              <h2>{detail.collection.name}</h2>
              <span class="coll-count">
                {t('film.reiheGesehen', { gesehen: detail.collection.watched_count, gesamt: detail.collection.total_count })}
              </span>
            </div>
            <ol class="coll-parts">
              {#each detail.collection.parts as p (p.id)}
                <li class:seen={p.played} class:current={p.is_current}>
                  {#if p.is_current}
                    <span class="coll-name">{p.position}. {p.name}</span>
                  {:else}
                    <a class="coll-name" href={`/movie/${p.id}`}>{p.position}. {p.name}</a>
                  {/if}
                  <span class="coll-year">{p.year ?? ''}</span>
                  <span class="coll-mark">{p.played ? '✓' : '○'}</span>
                </li>
              {/each}
            </ol>
            {#if detail.collection.next_unwatched}
              <a class="coll-next" href={`/movie/${detail.collection.next_unwatched.id}`}>
                {t('film.dirFehltNoch', { name: detail.collection.next_unwatched.name })}
              </a>
            {:else}
              <div class="coll-next done">{t('film.reiheVollstaendig')}</div>
            {/if}
          </section>
        {/if}

        {#if detail.movie.overview}
          <section class="overview">
            <h2>{t('film.handlung')}</h2>
            <p>{detail.movie.overview}</p>
          </section>
        {/if}

        <section class="credits">
          {#if detail.movie.directors.length}
            <div><span class="label">{t('film.regie')}</span> {detail.movie.directors.join(', ')}</div>
          {/if}
          {#if detail.movie.actors.length}
            <div><span class="label">{t('film.besetzung')}</span> {detail.movie.actors.slice(0, 8).join(', ')}</div>
          {/if}
          {#if detail.movie.studios.length}
            <div><span class="label">{t('film.studio')}</span> {detail.movie.studios.join(', ')}</div>
          {/if}
          {#if sprachen(detail.movie.audio_languages)}
            <div>
              <span class="label">{t('film.ton')}</span> {sprachen(detail.movie.audio_languages)}
              {#if sprachen(detail.movie.subtitle_languages)}
                &nbsp;· <span class="label">{t('film.untertitel')}</span> {sprachen(detail.movie.subtitle_languages)}
              {/if}
            </div>
          {/if}
        </section>
      </div>
    </div>

    {#if similar.length}
      <section class="similar">
        <h2>{t('film.aehnlicheFilme')}</h2>
        <div class="mini-grid">
          {#each similar as s (s.movie_id)}
            <a class="mini-card" href={`/movie/${s.movie_id}`} title={s.name}>
              <div class="mini-poster">
                <img src="/api/image/{s.movie_id}?w=300" alt="" loading="lazy" />
                {#if s.collection_boost}<span class="boost" title={t('film.ausDerselbenReihe')}>{t('film.reiheMarke')}</span>{/if}
              </div>
              <div class="mini-name">{s.name}</div>
              <div class="mini-sub">{s.year ?? ''} · {(s.score * 100).toFixed(0)}%</div>
            </a>
          {/each}
        </div>
      </section>
    {/if}

    {#if vonRegie.length}
      <section class="similar">
        <h2>{t('film.mehrVon', { name: regieName })}</h2>
        <div class="mini-grid">
          {#each vonRegie as m (m.id)}
            <a class="mini-card" href={`/movie/${m.id}`} title={m.name}>
              <div class="mini-poster">
                <img src="/api/image/{m.id}?w=300" alt="" loading="lazy" />
                {#if m.played}<span class="boost" title={t('film.schonGesehen')}>✓</span>{/if}
              </div>
              <div class="mini-name">{m.name}</div>
              <div class="mini-sub">{m.year ?? ''}{#if m.runtime_min}&nbsp;· {t('film.laufzeit', { n: m.runtime_min })}{/if}</div>
            </a>
          {/each}
        </div>
      </section>
    {/if}
  {/if}
</div>

<style>
  .film { min-height: 100vh; color: #eee; }
  .back {
    position: absolute; top: 70px; left: 24px; z-index: 10;
    color: #eee; text-decoration: none; font-size: 14px;
    background: #000a; padding: 6px 12px; border-radius: 4px;
    backdrop-filter: blur(6px);
  }
  .back:hover { background: #000d; }
  .loading, .err { padding: 60px 28px; color: #888; }
  .err { color: #f66; }

  /* Vorher 420px hoch mit einem Verlauf, der ab 0% von links mit 85%
     Deckung einsetzte — auf einem 900px-Schirm blieb vom Bild praktisch
     nichts uebrig. Jetzt traegt es die obere Bildschirmhaelfte, und der
     linke Verlauf setzt erst dort an, wo der Text beginnt. */
  .backdrop {
    position: relative; height: 62vh; min-height: 460px; max-height: 720px;
    width: 100%;
    background-size: cover; background-position: center 25%;
    background-color: #111;
  }
  .backdrop-fade {
    position: absolute; inset: 0;
    background: linear-gradient(to bottom, rgba(10,10,12,0.35) 0%,
                                rgba(10,10,12,0.05) 30%,
                                rgba(10,10,12,0.75) 72%, var(--bg) 100%),
                linear-gradient(to right, rgba(10,10,12,0.8) 0%,
                                rgba(10,10,12,0.3) 42%, transparent 72%);
  }

  /* Waechst mit dem Schirm und steht mittig — genau wie die Seriendetailseite.
     Vorher: feste 1200px ohne "auto"-Rand, der Block klebte also am linken
     Bildschirmrand, und auf 3840px lagen 2560px brach.

     Der Block nimmt dieselbe Breite wie die Posterreihen darunter, damit die
     Seite eine einzige linke Kante hat. Gedeckelt wird nicht die Spalte,
     sondern das, was darin steht: Fliesstext auf --lese, Kaesten auf
     --kasten. Eine schmale Spalte haette sonst dasselbe Bild ergeben und die
     Reihen darunter waeren sichtbar eingerueckt gewesen. */
  .content {
    position: relative; box-sizing: border-box;
    width: 100%; max-width: var(--seite-max);
    margin: -220px auto 0; padding: 0 var(--seite-pad) 40px;
    display: grid;
    grid-template-columns: var(--spalte-poster) minmax(0, 1fr);
    gap: clamp(20px, 2.2vw, 48px);
  }
  .poster {
    width: 100%; aspect-ratio: 2/3; border-radius: 8px;
    background-size: cover; background-position: center;
    background-color: #222;
    box-shadow: 0 12px 32px #000c;
  }
  .info { padding-top: 100px; min-width: 0; }
  h1 {
    font-size: 36px; margin: 0 0 8px; font-weight: 600;
    text-shadow: 0 2px 8px #000a;
  }
  .meta { color: #aaa; font-size: 14px; margin-bottom: 8px; }
  .genres { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
  .tag {
    background: #1a1a22; border: 1px solid #333; color: #ccc;
    padding: 3px 10px; border-radius: 12px; font-size: 12px;
    text-decoration: none; cursor: pointer;
    transition: background 0.15s, border-color 0.15s, color 0.15s;
  }
  a.tag:hover { background: rgba(200,169,110,0.15); border-color: var(--accent); color: var(--accent); }
  .tag.collection { border-color: #5a4a22; color: #e0c068; }

  .tagline {
    font-style: italic; color: #bbb; font-size: 16px;
    margin: 12px 0 20px; border-left: 3px solid var(--accent); padding-left: 12px;
    max-width: var(--lese);
  }

  .match-row {
    display: flex; align-items: center; gap: 20px;
    background: #14141a; border: 1px solid #2a2a33;
    border-radius: 10px; padding: 16px; margin: 16px 0;
    /* Gedeckelt, damit die Begruendung darin bei ihrer Lesebreite bleibt.
       Ohne Deckel liefe der Satz auf einem breiten Schirm ueber die ganze
       Spalte. */
    max-width: min(100%, var(--kasten));
  }
  .match-ring { width: 96px; height: 96px; flex-shrink: 0; }
  .match-reason { flex: 1; min-width: 0; }
  .match-reason .label {
    font-size: 11px; color: #888; letter-spacing: 1.2px;
    text-transform: uppercase; margin-bottom: 4px;
  }
  /* Die Begruendung ist Fliesstext und bekommt dieselbe Lesebreite wie die
     Handlung — der Kasten darf breiter sein, der Satz darin nicht. */
  .match-reason p {
    margin: 0; color: #ddd; font-size: 14px; line-height: 1.5;
    max-width: var(--lese);
  }
  .state { font-size: 12px; color: #8a8; margin-top: 6px; }

  .actions { margin: 16px 0 24px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

  .fav-btn {
    background: transparent; color: var(--text-muted);
    border: 1px solid var(--border-strong);
    padding: 12px 22px; border-radius: 2px; cursor: pointer;
    font-size: 13px; letter-spacing: 0.12em; text-transform: uppercase;
    font-family: inherit;
    transition: color 0.15s, border-color 0.15s;
  }
  .fav-btn:hover:not(:disabled) { color: #e09090; border-color: #e09090; }
  .fav-btn.on { color: #e09090; border-color: #e09090; }
  .fav-btn:disabled { opacity: 0.5; cursor: default; }
  .fav-btn.wl:hover:not(:disabled), .fav-btn.wl.on { color: var(--accent); border-color: var(--accent); }
  .fav-error { color: #e09090; font-size: 13px; margin: -12px 0 20px; }

  .rating-row { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
  .rating-label { font-size: 12px; color: var(--text-dim); }
  .stars { display: flex; gap: 2px; }
  .star {
    background: none; border: none; padding: 0 1px; cursor: pointer;
    font-size: 19px; line-height: 1; color: #444;
    transition: color 0.12s, transform 0.12s;
  }
  .star.filled { color: var(--accent); }
  .star:hover:not(:disabled) { transform: scale(1.15); }
  .star:disabled { cursor: default; opacity: 0.6; }
  .rating-value { font-size: 12px; color: var(--text-dim); }

  .coll-progress {
    margin: 0 0 26px; padding: 16px 18px;
    border: 1px solid var(--border); border-radius: 3px;
    background: rgba(255,255,255,0.02);
    /* Wie der Begruendungskasten: eine Liste aus Titel links und Haken
       rechts wird unlesbar, wenn zwischen beiden ein Meter Leere liegt. */
    max-width: min(100%, var(--kasten));
  }
  .coll-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
  .coll-head h2 { margin: 0; font-size: 15px; letter-spacing: 0.06em; }
  .coll-count { font-size: 12px; color: var(--text-dim); }
  .coll-parts { list-style: none; margin: 12px 0 0; padding: 0; }
  .coll-parts li {
    display: flex; align-items: baseline; gap: 10px;
    padding: 5px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
    font-size: 14px;
  }
  .coll-parts li:last-child { border-bottom: none; }
  .coll-parts li.seen .coll-name { color: var(--text-dim); }
  .coll-parts li.current { background: rgba(200,169,110,0.07); }
  .coll-parts li.current .coll-name { color: var(--accent); font-weight: 600; }
  .coll-name { flex: 1; color: var(--text); text-decoration: none; }
  a.coll-name:hover { color: var(--accent); text-decoration: underline; }
  .coll-year { font-size: 12px; color: var(--text-dim); }
  .coll-mark { width: 16px; text-align: center; color: #6a8; }
  .coll-parts li:not(.seen) .coll-mark { color: #555; }
  .coll-next {
    display: inline-block; margin-top: 12px; font-size: 13px;
    color: var(--accent); text-decoration: none;
  }
  .coll-next:hover { text-decoration: underline; }
  .coll-next.done { color: var(--text-dim); }
  /* Stand zweimal im Stylesheet, die zweite Regel hat die erste halb
     ueberschrieben (color: white gegen color: var(--bg) !important).
     Zusammengefasst; der Radius folgt jetzt der Token-Skala statt 2px. */
  .play {
    display: inline-flex; align-items: center; gap: 10px;
    background: var(--accent); color: var(--bg);
    padding: 14px 30px; border-radius: var(--r-md);
    font-size: 15px; font-weight: 700; text-decoration: none;
    letter-spacing: 0.06em;
    box-shadow: 0 6px 20px rgba(200, 169, 110, 0.28);
    transition: background var(--dur-base) var(--ease),
                transform var(--dur-base) var(--ease),
                box-shadow var(--dur-base) var(--ease);
  }
  /* Vorher wurde der Knopf beim Ueberfahren hell und damit zum lautesten
     Element ueberhaupt. Das helle Gold bleibt in der Familie. */
  .play:hover {
    background: var(--gold-bright); transform: translateY(-1px);
    box-shadow: 0 10px 28px rgba(200, 169, 110, 0.38);
  }

  .overview { margin: 24px 0; }
  /* Die Seite sprang von h1 auf h3 — dazwischen fehlte jede Ebene. Alle
     Abschnitte dieser Seite sind jetzt h2; die Gestalt bleibt unveraendert,
     weil Groesse und Farbe hier ohnehin gesetzt werden. */
  .overview h2, .credits, .similar h2 {
    font-size: 13px; color: #888; letter-spacing: 1.5px;
    text-transform: uppercase; font-weight: 600;
  }
  /* 720px waren bei 14px rund 100 Zeichen pro Zeile — am oberen Rand des
     Lesbaren. Jetzt rechnet die Breite in Zeichen statt in Pixeln. */
  .overview p { color: #ccc; line-height: 1.6; font-size: 14px; max-width: var(--lese); }

  .credits {
    display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: #bbb;
    max-width: var(--lese);
  }
  .credits .label { color: #777; min-width: 80px; display: inline-block; }

  /* Die Posterreihen duerfen die ganze Seitenbreite nehmen — hier zahlt sich
     ein grosser Schirm aus, anders als beim Fliesstext. */
  .similar {
    box-sizing: border-box; width: 100%; max-width: var(--seite-max);
    margin: 0 auto; padding: 0 var(--seite-pad) 60px;
  }
  .similar h2 { margin-bottom: 12px; }
  /* Beide Reihen liefern genau acht Eintraege. Mit auto-fill passten in das
     1200px breite Gitter nur sieben Spalten (1200 + 14 Luecke, geteilt durch
     154) — der achte Eintrag hing allein in einer zweiten Zeile. Deshalb
     feste Spaltenzahlen, und zwar nur Teiler von acht: so ist jede Zeile
     voll, egal wie breit der Schirm ist (2x4, 4x2, 1x8). Die Schwelle fuer
     acht Spalten liegt bei 1180px, weil die Karten dort noch 125px breit
     sind — darunter waeren sie schmaler als die Poster in den Reihen.

     minmax(0, 1fr) statt 1fr: "1fr" heisst ausgeschrieben minmax(auto, 1fr),
     und dieses "auto" ist die Mindestbreite des Inhalts. Weil der Titel
     darunter auf einer Zeile stand, war das die Breite des laengsten Wortes
     — auf der Interstellar-Seite kamen so Spalten von 91 bis 194px heraus.
     Mit der Null als Minimum sind alle Spalten gleich breit, und der Titel
     muss umbrechen (siehe .mini-name). */
  .mini-grid {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px;
  }
  @media (min-width: 620px) {
    .mini-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  }
  @media (min-width: 1180px) {
    .mini-grid { grid-template-columns: repeat(8, minmax(0, 1fr)); }
  }
  .mini-card {
    text-decoration: none; color: inherit;
    transition: transform 0.15s;
    min-width: 0;   /* damit die Karte die Spalte nicht doch noch aufdrueckt */
  }
  .mini-card:hover { transform: scale(1.04); }
  .mini-poster {
    aspect-ratio: 2/3; background-size: cover; background-position: center;
    background-color: #222; border-radius: 6px; position: relative;
    box-shadow: 0 4px 10px #0008;
  }
  /* Gleiche Gestalt wie das Kennzeichen auf PosterCard ("läuft"), damit
     "Reihe" hier nicht wie ein Fremdkörper wirkt. Stand vorher als
     Buecher-Emoji auf einem rohen Schwarzwert. */
  .boost {
    position: absolute; top: var(--s-1); right: var(--s-1);
    padding: 3px 7px; border-radius: var(--r-sm);
    background: rgba(8, 8, 10, 0.8); backdrop-filter: blur(6px);
    color: var(--accent); font-size: 9px; letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .mini-poster { position: relative; }
  .mini-poster img {
    position: absolute; inset: 0; width: 100%; height: 100%;
    object-fit: cover; display: block; border-radius: inherit;
  }
  /* Vorher einzeilig mit Auslassungspunkten — das zwang die Spalte auf die
     Breite des laengsten Wortes (siehe .mini-grid). Jetzt bricht der Titel
     nach hoechstens zwei Zeilen um; die feste Mindesthoehe haelt die
     Jahreszeile darunter ueber alle Karten auf gleicher Hoehe. */
  .mini-name {
    font-size: 12px; margin-top: 6px; line-height: 1.3; min-height: 2.6em;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; overflow-wrap: anywhere;
  }
  .mini-sub { font-size: 11px; color: #777; }

  @media (max-width: 720px) {
    /* Auf dem Handy traegt der Verlauf rein vertikal — der seitliche haette
       auf 375px Breite das halbe Bild abgedunkelt. */
    .backdrop { height: 42vh; min-height: 280px; }
    .backdrop-fade {
      background: linear-gradient(to bottom, rgba(10,10,12,0.4) 0%,
                                  rgba(10,10,12,0.1) 26%,
                                  rgba(10,10,12,0.82) 66%, var(--bg) 100%);
    }
    .back { top: 60px; left: 12px; }
    .content {
      grid-template-columns: 1fr; gap: 16px;
      margin-top: -120px; padding: 0 16px 32px;
    }
    .poster { width: 160px; margin: 0 auto; }
    .info { padding-top: 0; text-align: center; }
    h1 { font-size: 26px; }
    .genres, .actions { justify-content: center; }
    .match-row { flex-direction: column; text-align: center; gap: 12px; }
    .match-ring { margin: 0 auto; }
    .similar { padding: 0 16px 40px; }
    /* Spaltenzahl bleibt ein Teiler von acht (siehe oben), nur die Luecke
       wird enger. */
    .mini-grid { gap: 10px; }
  }
</style>
