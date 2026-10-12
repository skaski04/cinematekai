<script>
  import { onMount, onDestroy } from 'svelte';
  import PosterCard from '$lib/components/PosterCard.svelte';
  import { t, genre } from '$lib/i18n.svelte.js';

  let movies = $state([]);          // pool to swipe through (filtered)
  let allMovies = $state([]);       // raw library cache (movies)
  let allSeries = $state([]);       // raw series cache
  let idx = $state(0);              // current index in `movies`
  let loading = $state(true);
  let error = $state('');
  let mode = $state('unrated');     // 'unrated' | 'seen' | 'all' | 'pilots'
  let view = $state('swipe');       // 'swipe' | 'grid'
  let pending = $state(false);
  let toast = $state('');
  let toastTimer = null;
  let history = $state([]);         // [{id, action: 'fav'|'skip', prevFav}] for undo
  let leaving = $state(null);       // 'left' | 'right' for animation

  async function load() {
    loading = true;
    error = '';
    try {
      const [mr, sr] = await Promise.all([
        fetch('/api/movies?sort=name&order=asc&limit=2000', { credentials: 'include' }),
        fetch('/api/series?sort=name&order=asc&limit=2000', { credentials: 'include' }),
      ]);
      if (!mr.ok) throw new Error(`movies HTTP ${mr.status}`);
      const mdata = await mr.json();
      allMovies = (mdata.items || []).map(m => ({ ...m, _type: 'movie' }));
      if (sr.ok) {
        const sdata = await sr.json();
        allSeries = (sdata.items || []).map(s => ({
          ...s,
          _type: 'series',
          // Normalize so the swipe card can reuse the same field names
          favorite: !!s.progress?.is_favorite,
          played: s.progress?.state === 'completed',
          runtime_min: s.runtime_min_avg,
          rating: s.rating,
        }));
      }
      rebuildPool();
    } catch (e) {
      error = String(e?.message || e);
    } finally {
      loading = false;
    }
  }

  function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  // Bewertung direkt im Stapel — der Weg ueber die Detailseite ist zu
  // umstaendlich, um ihn im Alltag wirklich zu nutzen.
  const STARS = [1, 2, 3, 4, 5];
  const toStars = (r) => (r == null ? 0 : Math.round(r / 2));

  async function rate(stars) {
    if (!current || pending || (current._type || 'movie') === 'series') return;
    const m = current;
    const prevRating = m.user_rating ?? null;
    const value = stars * 2;              // 5 Sterne -> Jellyfins 0-10
    leaving = 'right';
    pending = true;
    try {
      const r = await fetch(`/api/movies/${m.id}/rating`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ rating: value }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      patchRatingLocal(m.id, value);
      history = [...history, { id: m.id, type: 'movie', action: 'rate', prevRating }];
      setTimeout(() => { idx = idx + 1; leaving = null; pending = false; }, 220);
    } catch (e) {
      pending = false;
      leaving = null;
      showToast(t('favoriten.fehlerText', { text: e?.message || e }));
    }
  }

  function patchRatingLocal(id, value) {
    allMovies = allMovies.map(m => (m.id === id ? { ...m, user_rating: value } : m));
    movies = movies.map(m => (m.id === id ? { ...m, user_rating: value } : m));
  }

  function rebuildPool() {
    let pool;
    if (mode === 'pilots') {
      // Series the user hasn't started yet — Tinder for pilot decisions.
      pool = allSeries.filter(s =>
        (s.progress?.state ?? 'not_started') === 'not_started' && !s.favorite
      );
    } else if (mode === 'norating') {
      // Wirklich unbewertet — im Gegensatz zu 'unrated', das nur
      // "gesehen, aber kein Favorit" meint.
      pool = allMovies.filter(m => m.played && m.user_rating == null);
    } else if (mode === 'unrated') {
      pool = allMovies.filter(m => m.played && !m.favorite);
    } else if (mode === 'seen') {
      pool = allMovies.filter(m => m.played);
    } else {
      pool = allMovies;   // 'all' = everything
    }
    movies = shuffle(pool);
    idx = 0;
    history = [];
  }

  function changeMode(next) {
    mode = next;
    rebuildPool();
  }

  const current = $derived(movies[idx] || null);
  const remaining = $derived(Math.max(0, movies.length - idx));
  const done = $derived(idx >= movies.length && !loading);
  const favCount = $derived(allMovies.filter(m => m.favorite).length);

  function showToast(msg) {
    toast = msg;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast = ''; }, 2400);
  }

  async function postFavorite(id, fav, type = 'movie') {
    const path = type === 'series' ? `/api/series/${id}/favorite`
                                   : `/api/movies/${id}/favorite`;
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ favorite: fav }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  function patchLocal(id, fav, type = 'movie') {
    const arr = type === 'series' ? allSeries : allMovies;
    const i = arr.findIndex(x => x.id === id);
    if (i >= 0) arr[i] = { ...arr[i], favorite: fav };
    const j = movies.findIndex(x => x.id === id);
    if (j >= 0) movies[j] = { ...movies[j], favorite: fav };
  }

  async function decide(action) {
    if (!current || pending) return;
    const m = current;
    const typ = m._type || 'movie';
    const prevFav = !!m.favorite;
    leaving = action === 'fav' ? 'right' : 'left';
    pending = true;
    try {
      if (action === 'fav') {
        await postFavorite(m.id, true, typ);
        patchLocal(m.id, true, typ);
      }
      history = [...history, { id: m.id, type: typ, action, prevFav }];
      setTimeout(() => {
        idx = idx + 1;
        leaving = null;
        pending = false;
      }, 220);
    } catch (e) {
      pending = false;
      leaving = null;
      showToast(t('favoriten.fehlerText', { text: e?.message || e }));
    }
  }

  async function undo() {
    if (pending || history.length === 0) return;
    const last = history[history.length - 1];
    pending = true;
    try {
      if (last.action === 'fav' && last.prevFav === false) {
        await postFavorite(last.id, false, last.type || 'movie');
        patchLocal(last.id, false, last.type || 'movie');
      } else if (last.action === 'rate') {
        // Auf den vorherigen Wert zurueck — null loescht die Bewertung
        // auch in Jellyfin (dort per DELETE, siehe jellyfin.set_rating).
        await fetch(`/api/movies/${last.id}/rating`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ rating: last.prevRating ?? null }),
        });
        patchRatingLocal(last.id, last.prevRating ?? null);
      }
      history = history.slice(0, -1);
      idx = Math.max(0, idx - 1);
    } catch (e) {
      showToast(t('favoriten.undoFehler', { text: e?.message || e }));
    } finally {
      pending = false;
    }
  }

  // Keyboard: ←/A = skip, →/D = favorite, Backspace = undo
  function onKey(ev) {
    if (view !== 'swipe' || done || loading || error) return;
    if (ev.target?.tagName === 'INPUT' || ev.target?.tagName === 'TEXTAREA') return;
    if (ev.key === 'ArrowRight' || ev.key === 'd' || ev.key === 'D') {
      ev.preventDefault();
      decide('fav');
    } else if (ev.key === 'ArrowLeft' || ev.key === 'a' || ev.key === 'A') {
      ev.preventDefault();
      decide('skip');
    } else if (ev.key >= '1' && ev.key <= '5') {
      ev.preventDefault();
      rate(Number(ev.key));
    } else if (ev.key === 'Backspace' || ev.key === 'z' || ev.key === 'Z') {
      ev.preventDefault();
      undo();
    }
  }

  // Touch swipe support
  let touchStartX = 0;
  let touchStartY = 0;
  let dragX = $state(0);
  let dragging = $state(false);

  function onTouchStart(ev) {
    if (pending) return;
    touchStartX = ev.touches[0].clientX;
    touchStartY = ev.touches[0].clientY;
    dragging = true;
  }
  function onTouchMove(ev) {
    if (!dragging) return;
    const dx = ev.touches[0].clientX - touchStartX;
    const dy = ev.touches[0].clientY - touchStartY;
    if (Math.abs(dy) > Math.abs(dx)) return;
    dragX = dx;
  }
  function onTouchEnd() {
    if (!dragging) return;
    dragging = false;
    const threshold = 100;
    if (dragX > threshold) { dragX = 0; decide('fav'); }
    else if (dragX < -threshold) { dragX = 0; decide('skip'); }
    else { dragX = 0; }
  }

  // Toggle favorite directly in grid view
  async function toggleGridFav(m, ev) {
    ev?.preventDefault?.();
    ev?.stopPropagation?.();
    const typ = m._type || 'movie';
    const next = !m.favorite;
    patchLocal(m.id, next, typ);
    try {
      const data = await postFavorite(m.id, next, typ);
      if (!data.jellyfin_synced) {
        showToast(t('favoriten.lokalGespeichert'));
      }
    } catch (e) {
      patchLocal(m.id, !next, typ);
      showToast(t('favoriten.fehlerText', { text: e?.message || e }));
    }
  }

  onMount(() => {
    load();
    window.addEventListener('keydown', onKey);
  });
  onDestroy(() => {
    window.removeEventListener('keydown', onKey);
    if (toastTimer) clearTimeout(toastTimer);
  });
</script>

<svelte:head><title>{t('favoriten.seitentitel')}</title></svelte:head>

<main id="inhalt" class:stapel={view === 'swipe'}>
  <h1 class="sr-only">{t('favoriten.ueberschrift')}</h1>

  <div class="kopf">
    <h2>{t('favoriten.frage')}</h2>
    <span class="zahl">{t('favoriten.zaehler', { fav: favCount, stapel: remaining })}</span>
  </div>

  <p class="lead">
    {t('favoriten.leadEiner')} <kbd>→</kbd> {t('favoriten.leadFavorit')}
    <kbd>←</kbd> {t('favoriten.leadSkip')} <kbd>⌫</kbd> {t('favoriten.leadUndo')}
    {t('favoriten.leadJellyfin')}
  </p>

  <!-- Umschaltleiste: links der Stapelinhalt, rechts die Darstellung.
       Ohne Kasten drumherum — die beiden Gruppen trennt der Abstand. -->
  <div class="leiste">
    <div class="gruppe" role="group" aria-label={t('favoriten.stapelInhalt')}>
      <button class:active={mode === 'norating'} aria-pressed={mode === 'norating'}
              onclick={() => changeMode('norating')}>{t('favoriten.modusNichtBewertet')}</button>
      <button class:active={mode === 'unrated'} aria-pressed={mode === 'unrated'}
              onclick={() => changeMode('unrated')}>{t('favoriten.modusKeinFavorit')}</button>
      <button class:active={mode === 'seen'} aria-pressed={mode === 'seen'}
              onclick={() => changeMode('seen')}>{t('favoriten.modusGesehen')}</button>
      <button class:active={mode === 'all'} aria-pressed={mode === 'all'}
              onclick={() => changeMode('all')}>{t('favoriten.modusAlle')}</button>
      <button class:active={mode === 'pilots'} aria-pressed={mode === 'pilots'}
              onclick={() => changeMode('pilots')}>{t('favoriten.modusPilots')}</button>
    </div>
    <div class="gruppe" role="group" aria-label={t('favoriten.darstellung')}>
      <button class:active={view === 'swipe'} aria-pressed={view === 'swipe'}
              onclick={() => view = 'swipe'}>{t('favoriten.ansichtStapel')}</button>
      <button class:active={view === 'grid'} aria-pressed={view === 'grid'}
              onclick={() => view = 'grid'}>{t('favoriten.ansichtUebersicht')}</button>
    </div>
  </div>

  {#if loading}
    <p class="hinweis">{t('favoriten.ladeFilme')}</p>
  {:else if error}
    <p class="hinweis fehler">{t('favoriten.fehlerText', { text: error })}</p>
  {:else if view === 'swipe'}
    {#if movies.length === 0}
      <!-- Jeder Modus fuellt den Stapel anders, also braucht jeder seine
           eigene Erklaerung samt eines Knopfes, den es in der Leiste
           darueber wirklich gibt. -->
      <p class="hinweis">
        {#if mode === 'norating'}
          {t('favoriten.leerNichtBewertet')}
        {:else if mode === 'unrated'}
          {t('favoriten.leerKeinFavorit')}
        {:else if mode === 'seen'}
          {t('favoriten.leerGesehen')}
        {:else if mode === 'pilots'}
          {t('favoriten.leerPilots')}
        {:else}
          {t('favoriten.leerStapel')}
        {/if}
      </p>
    {:else if done}
      <div class="fertig">
        <h2>{t('favoriten.fertigTitel')}</h2>
        <p>{t('favoriten.fertigText')}</p>
        <button class="knopf" onclick={rebuildPool}>{t('favoriten.nochmalMischen')}</button>
      </div>
    {:else if current}
      <div class="stage">
        <div class="counter">{idx + 1} / {movies.length}</div>
        <!-- Das Wischen ist eine Abkuerzung, kein eigener Bedienweg: dieselben
             Entscheidungen liegen unten als Knoepfe und auf den Pfeiltasten.
             Deshalb braucht die Karte selbst keine interaktive Rolle. -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="card-wrap"
          class:leaving-left={leaving === 'left'}
          class:leaving-right={leaving === 'right'}
          style:transform={dragging ? `translateX(${dragX}px) rotate(${dragX * 0.04}deg)` : ''}
          ontouchstart={onTouchStart}
          ontouchmove={onTouchMove}
          ontouchend={onTouchEnd}
        >
          {#if dragging && Math.abs(dragX) > 30}
            <div class="wischmarke" class:fav={dragX > 0} class:skip={dragX < 0}>
              {dragX > 0 ? t('favoriten.wischFavorit') : t('favoriten.wischSkip')}
            </div>
          {/if}
          <div class="card">
            <div class="poster">
              <img src="/api{current.poster_url}" alt={current.name} />
            </div>
            <div class="info">
              <h2 class="title">{current.name}</h2>
              <div class="sub">
                {#if current.year}{current.year}{/if}
                {#if current.year && current.runtime_min}<span class="dot">·</span>{/if}
                {#if current.runtime_min}{current.runtime_min} min{/if}
                {#if current.rating}<span class="dot">·</span>★ {current.rating.toFixed(1)}{/if}
              </div>
              {#if current.genres?.length}
                <div class="genres">
                  {#each current.genres.slice(0, 4) as g}<span class="chip">{genre(g)}</span>{/each}
                </div>
              {/if}
              {#if current._type === 'series' && current.creators?.length}
                <div class="meta-line"><em>{t('favoriten.showrunner')}</em> {current.creators.slice(0, 2).join(', ')}</div>
              {:else if current.directors?.length}
                <div class="meta-line"><em>{t('favoriten.regie')}</em> {current.directors.slice(0, 2).join(', ')}</div>
              {/if}
              {#if current.actors?.length}
                <div class="meta-line"><em>{t('favoriten.besetzung')}</em> {current.actors.slice(0, 4).join(', ')}</div>
              {/if}
              {#if current._type === 'series' && current.episode_count}
                <div class="meta-line"><em>{t('favoriten.umfang')}</em> {t('favoriten.umfangText', { staffeln: current.season_count, folgen: current.episode_count })}</div>
              {/if}
              {#if current.overview}
                <p class="overview">{current.overview}</p>
              {/if}
            </div>
          </div>
        </div>

        {#if (current._type || 'movie') !== 'series'}
          <div class="rate-row">
            <span class="rate-label">{t('favoriten.bewerten')}</span>
            {#each STARS as n}
              <button class="rate-star" class:filled={n <= toStars(current.user_rating)}
                      onclick={() => rate(n)} disabled={pending}
                      aria-label={t('favoriten.sterneLabel', { n })}
                      title={t('favoriten.sterneTitel', { n })}>★</button>
            {/each}
            <span class="rate-hint">{t('favoriten.tastenHinweis')}</span>
          </div>
        {/if}

        <div class="controls">
          <button class="ctrl skip" onclick={() => decide('skip')} disabled={pending} title={t('favoriten.titelSkip')}>
            <span class="icon" aria-hidden="true">✗</span>
            <span class="label">{t('favoriten.skip')}</span>
            <span class="key" aria-hidden="true">←</span>
          </button>
          <button class="ctrl undo" onclick={undo} disabled={pending || history.length === 0} title={t('favoriten.titelUndo')}>
            <span class="icon" aria-hidden="true">↶</span>
            <span class="label">{t('favoriten.undo')}</span>
            <span class="key" aria-hidden="true">⌫</span>
          </button>
          <!-- Der einzige Vollgold-Knopf der Seite: die Hauptbestaetigung. -->
          <button class="ctrl fav" onclick={() => decide('fav')} disabled={pending} title={t('favoriten.titelFavorit')}>
            <span class="icon" aria-hidden="true">♥︎</span>
            <span class="label">{t('favoriten.favorit')}</span>
            <span class="key" aria-hidden="true">→</span>
          </button>
        </div>
      </div>
    {/if}
  {:else}
    <!-- grid view -->
    {#if movies.length === 0}
      <p class="hinweis">{t('favoriten.keineFilme')}</p>
    {:else}
      <div class="gitter">
        {#each movies as m (m.id)}
          <!-- PosterCard traegt Bild, Titel und Fakten; das Herz liegt als
               eigener Knopf darueber, weil es hier anklickbar sein muss.
               Das Ziel haengt am Typ wie in decide() und postFavorite(): im
               Modus "Serien-Pilots" liegen Serien im Stapel, und /movie/<id>
               antwortet fuer sie mit 404. -->
          <div class="kachel">
            <PosterCard
              id={m.id}
              name={m.name}
              href={(m._type || 'movie') === 'series' ? `/series/${m.id}` : `/movie/${m.id}`}
              imgSrc={`/api${m.poster_url}`}
              facts={[m.year, m.rating ? `★ ${m.rating.toFixed(1)}` : null]
                       .filter(Boolean).join(' · ')}
            />
            <button
              class="herz"
              class:an={m.favorite}
              onclick={(e) => toggleGridFav(m, e)}
              aria-pressed={m.favorite}
              aria-label={m.favorite ? t('favoriten.favoritEntfernen') : t('favoriten.favoritSetzen')}
            >{m.favorite ? '♥︎' : '♡'}</button>
          </div>
        {/each}
      </div>
    {/if}
  {/if}

  {#if toast}<div class="toast">{toast}</div>{/if}
</main>

<style>
  /* Der Stapel bleibt schmal — eine grosse Karte in der Mitte liest sich
     besser als eine ueber 3840px gezogene. Die Uebersicht dagegen nutzt die
     volle Breite wie /movies, sonst standen auf 4K auch hier nur sieben
     kleine Kacheln in einer Spalte. */
  main {
    padding: var(--s-6) var(--s-7) var(--s-9);
    color: var(--text);
    font-family: var(--font-ui);
  }

  main.stapel { max-width: 1100px; margin: 0 auto; }

  .kopf {
    display: flex; align-items: baseline; gap: var(--s-4);
    margin-bottom: var(--s-2);
  }
  .kopf h2 {
    font-family: var(--font-display); font-size: 30px; font-weight: 600;
    margin: 0; letter-spacing: 0.01em;
  }
  .zahl { font-size: 13px; color: var(--text-dim); }

  .lead {
    color: var(--text-muted);
    max-width: 720px; line-height: 1.6;
    margin: 0 0 var(--s-5);
    font-size: 14px;
  }
  .lead kbd {
    font-family: var(--font-ui); font-size: 13px;
    color: var(--accent); background: var(--gold-wash);
    padding: var(--s-1) var(--s-2); border-radius: var(--r-sm);
  }
  .dot { opacity: 0.5; margin: 0 var(--s-1); }

  .leiste {
    display: flex; flex-wrap: wrap; align-items: center;
    justify-content: space-between; gap: var(--s-4);
    margin-bottom: var(--s-6);
  }
  .gruppe { display: flex; gap: var(--s-2); flex-wrap: wrap; }
  .gruppe button {
    min-height: 40px; padding: 0 var(--s-4);
    background: transparent; color: var(--text-muted);
    border: 1px solid var(--border-strong); border-radius: var(--r-full);
    font-family: var(--font-ui); font-size: 12px;
    letter-spacing: 0.05em; text-transform: uppercase;
    white-space: nowrap; cursor: pointer;
    transition: color var(--dur-fast) var(--ease),
                border-color var(--dur-fast) var(--ease),
                background var(--dur-fast) var(--ease);
  }
  .gruppe button:hover { color: var(--accent); border-color: var(--accent); }
  .gruppe button.active {
    color: var(--accent); border-color: var(--accent);
    background: var(--gold-wash);
  }

  .hinweis {
    color: var(--text-dim); font-style: italic;
    padding: var(--s-7) 0; text-align: center;
  }
  .hinweis.fehler { color: var(--danger); font-style: normal; }

  .fertig { padding: var(--s-7) 0; text-align: center; }
  .fertig h2 {
    font-family: var(--font-display); font-size: 30px;
    margin: 0 0 var(--s-2); color: var(--text);
  }
  .fertig p { color: var(--text-muted); margin: 0 0 var(--s-5); }

  /* Sekundaere Handlung: Rand statt Flaeche. */
  .knopf {
    min-height: 44px; padding: 0 var(--s-6);
    background: transparent; color: var(--text-muted);
    border: 1px solid var(--border-strong); border-radius: var(--r-full);
    font-family: var(--font-ui); font-size: 13px;
    letter-spacing: 0.06em; text-transform: uppercase; cursor: pointer;
    transition: color var(--dur-fast) var(--ease),
                border-color var(--dur-fast) var(--ease);
  }
  .knopf:hover { color: var(--accent); border-color: var(--accent); }

  /* STAPEL */
  .stage {
    display: flex; flex-direction: column;
    align-items: center; gap: var(--s-4);
  }
  .counter {
    color: var(--text-dim); font-size: 12px;
    letter-spacing: 0.1em; text-transform: uppercase;
  }
  .card-wrap {
    width: 100%; max-width: 760px;
    position: relative;
    /* 0,22 s ist kein freier Wert: die Karte muss draussen sein, bevor der
       Index nach 220 ms weiterspringt (setTimeout in decide/rate). */
    transition: transform 0.22s cubic-bezier(.5,.1,.3,1), opacity 0.22s;
    will-change: transform;
    user-select: none;
    touch-action: pan-y;
  }
  .card-wrap.leaving-left {
    transform: translateX(-130%) rotate(-15deg) !important;
    opacity: 0;
  }
  .card-wrap.leaving-right {
    transform: translateX(130%) rotate(15deg) !important;
    opacity: 0;
  }

  /* Die grosse Karte bleibt eigenstaendig — PosterCard waere hier zu klein.
     Kein Rand: die hellere Flaeche und der Schatten setzen sie ab. */
  .card {
    display: grid;
    grid-template-columns: 280px 1fr;
    background: var(--layer-2);
    border-radius: var(--r-lg);
    overflow: hidden;
    box-shadow: var(--elev-3);
  }
  .poster {
    aspect-ratio: 2/3;
    background: var(--layer-1);
  }
  .poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .info {
    padding: var(--s-5);
    display: flex; flex-direction: column; gap: var(--s-3);
  }
  .title {
    font-family: var(--font-display);
    font-weight: 600; font-size: 28px;
    line-height: 1.15; margin: 0; color: var(--text);
  }
  .sub { color: var(--text-dim); font-size: 13px; letter-spacing: 0.04em; }
  .genres { display: flex; gap: var(--s-2); flex-wrap: wrap; }
  .chip {
    background: var(--gold-wash); color: var(--accent);
    padding: var(--s-1) var(--s-3); border-radius: var(--r-full);
    font-size: 11px; letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .meta-line { color: var(--text-muted); font-size: 13px; line-height: 1.4; }
  .meta-line em {
    font-style: normal; color: var(--text-dim);
    text-transform: uppercase; font-size: 11px;
    letter-spacing: 0.08em; margin-right: var(--s-1);
  }
  .overview {
    color: var(--text); font-size: 14px;
    line-height: 1.55; margin: var(--s-1) 0 0;
    display: -webkit-box;
    -webkit-line-clamp: 8; -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .wischmarke {
    position: absolute; top: var(--s-5);
    padding: var(--s-2) var(--s-4); border-radius: var(--r-sm);
    font-family: var(--font-ui); font-weight: 700;
    font-size: 20px; letter-spacing: 0.12em;
    border: 3px solid; pointer-events: none;
    z-index: 5;
  }
  .wischmarke.fav {
    right: var(--s-5); transform: rotate(12deg);
    color: var(--accent); border-color: var(--accent);
  }
  .wischmarke.skip {
    left: var(--s-5); transform: rotate(-12deg);
    color: var(--danger); border-color: var(--danger);
  }

  .rate-row {
    display: flex; align-items: center; justify-content: center;
    gap: var(--s-1); margin-top: var(--s-2);
  }
  .rate-label {
    font-size: 12px; color: var(--text-dim);
    margin-right: var(--s-2); letter-spacing: 0.06em;
  }
  .rate-star {
    background: none; border: none; cursor: pointer;
    padding: var(--s-1) var(--s-1);
    font-size: 26px; line-height: 1;
    color: var(--text-dim); opacity: 0.4;
    transition: color var(--dur-fast) var(--ease),
                opacity var(--dur-fast) var(--ease),
                transform var(--dur-fast) var(--ease);
  }
  .rate-star.filled { color: var(--accent); opacity: 1; }
  .rate-star:hover:not(:disabled) { color: var(--accent); opacity: 1; transform: scale(1.18); }
  .rate-star:disabled { cursor: default; opacity: 0.3; }
  .rate-hint { font-size: 11px; color: var(--text-dim); margin-left: var(--s-2); }

  .controls {
    display: flex; gap: var(--s-3); justify-content: center; flex-wrap: wrap;
  }
  .ctrl {
    display: flex; flex-direction: column; align-items: center;
    gap: var(--s-1); padding: var(--s-3) var(--s-5); min-width: 110px;
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--text-muted);
    border-radius: var(--r-md); cursor: pointer;
    font-family: var(--font-ui);
    transition: color var(--dur-fast) var(--ease),
                border-color var(--dur-fast) var(--ease),
                transform var(--dur-fast) var(--ease);
  }
  .ctrl:hover:not(:disabled) { transform: translateY(-2px); }
  .ctrl:disabled { opacity: 0.4; cursor: not-allowed; }
  .ctrl .icon { font-size: 22px; line-height: 1; }
  .ctrl .label {
    font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
  }
  .ctrl .key {
    font-size: 12px; color: var(--text-dim);
    border: 1px solid var(--border); padding: 0 var(--s-2);
    border-radius: var(--r-sm); font-family: var(--font-ui);
    font-variant-numeric: tabular-nums;
  }
  .ctrl.skip:hover:not(:disabled) {
    color: var(--danger); border-color: var(--danger);
  }
  .ctrl.undo:hover:not(:disabled) {
    color: var(--accent); border-color: var(--accent);
  }

  /* Vollgold — einmal pro Ansicht, hier fuer den Favoriten. */
  .ctrl.fav {
    background: var(--accent); border-color: var(--accent);
    color: var(--bg); font-weight: 500;
  }
  .ctrl.fav .key { color: var(--bg); border-color: currentColor; opacity: 0.55; }
  .ctrl.fav:hover:not(:disabled) {
    background: var(--gold-bright); border-color: var(--gold-bright);
  }

  /* UEBERSICHT */
  /* Dieselben Stufen wie /movies und /series — clamp(vw) sprang auf
     Laptopbreiten unguenstig um und passte nicht zu den anderen Listen. */
  .gitter {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: var(--s-4);
  }
  @media (min-width: 1200px) {
    .gitter { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
  }
  @media (min-width: 2000px) {
    .gitter { grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
  }
  @media (min-width: 3000px) {
    .gitter { grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); }
  }
  .kachel { position: relative; }
  .herz {
    position: absolute; top: var(--s-2); right: var(--s-2);
    z-index: 12;
    width: 36px; height: 36px;
    display: flex; align-items: center; justify-content: center;
    background: rgba(8, 8, 10, 0.72);
    backdrop-filter: blur(6px);
    color: var(--text-muted);
    border: 1px solid var(--border-strong);
    border-radius: var(--r-full);
    font-size: 18px; line-height: 1; cursor: pointer;
    transition: color var(--dur-fast) var(--ease),
                border-color var(--dur-fast) var(--ease),
                background var(--dur-fast) var(--ease);
  }
  .herz:hover { color: var(--accent); border-color: var(--accent); }
  .herz.an {
    color: var(--accent); border-color: var(--accent);
    background: var(--gold-wash);
  }

  .toast {
    position: fixed; bottom: var(--s-5); left: 50%;
    transform: translateX(-50%);
    background: var(--layer-3);
    border: 1px solid var(--gold-ring);
    color: var(--text);
    padding: var(--s-3) var(--s-5); border-radius: var(--r-md);
    font-size: 14px;
    box-shadow: var(--elev-2);
    z-index: 100;
  }

  /* Ohne Hover muss alles im Ruhezustand erkennbar sein — das Herz sitzt
     dauerhaft auf der Kachel, die Knoepfe tragen ihre Beschriftung selbst. */
  @media (hover: none) {
    .ctrl:hover:not(:disabled) { transform: none; }
    .rate-star:hover:not(:disabled) { transform: none; }
  }

  @media (max-width: 820px) {
    main { padding: var(--s-4) var(--s-4) var(--s-8); }
    .kopf { flex-wrap: wrap; gap: var(--s-2); }
    .kopf h2 { font-size: 24px; }
    .leiste { gap: var(--s-3); }
    .gruppe { width: 100%; }
    .gruppe button { min-height: 44px; flex: 1 1 auto; padding: 0 var(--s-3); }
    .card { grid-template-columns: 1fr; }
    .poster { aspect-ratio: 16/10; }
    .info { padding: var(--s-4); }
    .title { font-size: 22px; }
    .overview { -webkit-line-clamp: 5; font-size: 13px; }
    .ctrl { padding: var(--s-3) var(--s-4); min-width: 90px; min-height: 44px; }
    .ctrl .key { display: none; }
    /* Auf Touch sind Sterne und Herz der einzige Bewertungsweg — die Tasten
       1 bis 5 gibt es dort nicht. Beide lagen unter den 44px, die ein
       Finger verlaesslich trifft. */
    .rate-star { min-width: 44px; min-height: 44px; }
    .herz { width: 44px; height: 44px; }
    .gitter { gap: var(--s-3); }
  }
</style>
