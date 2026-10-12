<script>
  /* Die Bibliothek. Umgebaut gegenueber der vorherigen Fassung:

     - Der "Film des Tages" samt "Auch interessant" und "Raus aus der Blase"
       ist entfallen. Er stand hier ein zweites Mal, obwohl die Startseite
       ihn schon zeigt — wer die Bibliothek oeffnet, will stoebern, nicht
       noch einmal dieselbe Empfehlung sehen.
     - Aus "Stimmungssuche" und "Titelsuche" wird ein Feld. Vorher musste man
       sich vor der Eingabe entscheiden, welche Art Suche man macht.
     - Die Kacheln kommen aus PosterCard, also mit demselben Verhalten wie
       auf der Startseite. */
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/stores';
  import { user } from '$lib/auth.svelte.js';
  import PosterCard from '$lib/components/PosterCard.svelte';
  import Row from '$lib/components/Row.svelte';
  import { appInfo } from '$lib/api.js';
  import { t, genre as genreName } from '$lib/i18n.svelte.js';

  let movies = $state([]);
  let total = $state(0);
  let facets = $state({ genres: [], years: [] });
  let loading = $state(true);
  let error = $state('');

  let q = $state('');
  let genre = $state('');
  let year = $state('');
  let played = $state('');
  let favOnly = $state(false);
  /* Nur 65 von 450 Filmen haben eine deutsche Tonspur — fuer einen
     Familienabend ist das der schnellste Weg zum passenden Film. Der Filter
     laeuft im Backend, weil die Liste seitenweise kommt. */
  let deutschOnly = $state(false);
  let sort = $state('name');
  let order = $state('asc');
  let syncing = $state(false);
  let syncResult = $state(null);

  /* Die KI-Suche laeuft zusaetzlich zur Titelsuche, sobald die Eingabe
     satzartig wird. Dieselbe Schwelle wie im Suchfeld der Kopfleiste. */
  const KI_AB_WOERTERN = 3;
  let kiTreffer = $state([]);
  let kiLaeuft = $state(false);
  const wortzahl = (s) => s.trim().split(/\s+/).filter(Boolean).length;
  const kiGefragt = $derived(wortzahl(q) >= KI_AB_WOERTERN);

  /* Wie bei der KI-Suche: beim Tippen loest jede Eingabe eine Abfrage aus,
     und die Antworten kommen nicht zwingend in der Reihenfolge zurueck, in
     der sie losgeschickt wurden. Ohne Lauf-Token konnte die Liste zu "Her"
     die zu "Herr" ueberschreiben. */
  let ladeLauf = 0;

  async function load() {
    const meins = ++ladeLauf;
    loading = true; error = '';
    const params = new URLSearchParams({ sort, order, limit: '500' });
    if (q) params.set('q', q);
    if (genre) params.set('genre', genre);
    if (year) params.set('year', year);
    if (played) params.set('played', played);
    if (favOnly) params.set('favorites_only', 'true');
    if (deutschOnly) params.set('audio_lang', 'de');
    try {
      const r = await fetch('/api/movies?' + params.toString());
      if (r.status === 401) { window.location.href = '/login'; return; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      if (meins !== ladeLauf) return;
      movies = d.items;
      total = d.total;
    } catch (e) {
      if (meins !== ladeLauf) return;
      error = String(e);
    } finally {
      if (meins === ladeLauf) loading = false;
    }
  }

  let kiLauf = 0;
  async function ladeKi(text) {
    const meins = ++kiLauf;
    kiLaeuft = true;
    try {
      const r = await fetch('/api/recommendations/mood?q=' + encodeURIComponent(text) + '&k=12');
      if (meins !== kiLauf) return;
      kiTreffer = r.ok ? (await r.json()).results || [] : [];
    } catch { if (meins === kiLauf) kiTreffer = []; }
    finally { if (meins === kiLauf) kiLaeuft = false; }
  }

  async function loadFacets() {
    try {
      const r = await fetch('/api/facets');
      if (r.ok) facets = await r.json();
    } catch {}
  }

  async function triggerSync() {
    syncing = true; syncResult = null;
    try {
      const r = await fetch('/api/sync', { method: 'POST' });
      syncResult = await r.json();
      if (syncResult.detail) { syncResult = { error: syncResult.detail }; }
      else {
        await fetch('/api/sync/watch', { method: 'POST' });
        await loadFacets();
        await load();
      }
    } catch (e) {
      syncResult = { error: String(e) };
    } finally {
      syncing = false;
    }
  }

  /* Kommt man ueber den Zurueck-Knopf auf die Seite, spielt SvelteKit den
     Schnappschuss ein — und onMount laeuft trotzdem. Vorher lud beides, also
     zweimal, und je nach Reihenfolge der Antworten gewann die ungefilterte
     Abfrage ueber die wiederhergestellten Filter. Beide Wege melden den
     Start jetzt nur an; ausgefuehrt wird er einmal, am Ende des Ticks, mit
     den Filtern, die dann gesetzt sind. */
  let ausSchnappschuss = false;
  let startTimer = null;
  let zielScrollY = 0;

  function starteLadung(scrollY = 0) {
    zielScrollY = scrollY;
    clearTimeout(startTimer);
    startTimer = setTimeout(async () => {
      await load();
      if (kiGefragt) ladeKi(q);
      if (zielScrollY && typeof window !== 'undefined') {
        requestAnimationFrame(() => window.scrollTo(0, zielScrollY));
      }
    }, 0);
  }

  onMount(() => {
    /* Lief die Wiederherstellung schon, dann wuerden die Adressparameter die
       gerade zurueckgeholten Filter wieder ueberschreiben — und ihre Ladung
       ist ohnehin angemeldet, eine zweite naehme ihr nur die Scrollposition. */
    if (!ausSchnappschuss) {
      const sp = $page.url.searchParams;
      if (sp.get('genre')) genre = sp.get('genre');
      if (sp.get('year')) year = sp.get('year');
      if (sp.get('q')) q = sp.get('q');
      starteLadung();
    }
    loadFacets();
    /* Die Seerr-Adresse steht in den Einstellungen, nicht mehr fest im Code:
       eine frische Installation hat vielleicht gar kein Seerr, dann fehlt
       der Link, statt auf eine fremde Adresse zu zeigen. */
    appInfo().then((info) => { seerrBasis = (info?.seerr_url ?? '').replace(/\/+$/, ''); });
  });

  let seerrBasis = $state('');

  /* Beide Zeitgeber ueberleben die Seite sonst: wer sofort weiterklickt,
     loest hinterher noch eine Ladung auf einer abgebauten Seite aus. */
  onDestroy(() => { clearTimeout(startTimer); clearTimeout(sucheTimer); });

  let sucheTimer;
  function beiEingabe() {
    clearTimeout(sucheTimer);
    sucheTimer = setTimeout(() => {
      load();
      const text = q.trim();
      if (wortzahl(text) >= KI_AB_WOERTERN) ladeKi(text);
      else { kiTreffer = []; kiLauf++; kiLaeuft = false; }
    }, 250);
  }

  function zuruecksetzen() {
    q = ''; genre = ''; year = ''; played = ''; favOnly = false; deutschOnly = false;
    kiTreffer = []; kiLauf++; kiLaeuft = false;
    load();
  }

  const filterAktiv = $derived(!!(q || genre || year || played || favOnly || deutschOnly));

  const fakten = (m) => [m.year, m.runtime_min ? t('filme.laufzeit', { n: m.runtime_min }) : null,
                         m.rating ? `★ ${m.rating.toFixed(1)}` : null,
                         (m.genres ?? []).slice(0, 2).map(genreName).join(', ')]
                        .filter(Boolean).join(' · ');

  export const snapshot = {
    capture: () => ({
      q, genre, year, played, favOnly, deutschOnly, sort, order,
      scrollY: typeof window !== 'undefined' ? window.scrollY : 0,
    }),
    restore: (s) => {
      if (!s) return;
      ausSchnappschuss = true;
      q = s.q ?? ''; genre = s.genre ?? ''; year = s.year ?? '';
      played = s.played ?? ''; favOnly = !!s.favOnly; deutschOnly = !!s.deutschOnly;
      sort = s.sort ?? 'name'; order = s.order ?? 'asc';
      starteLadung(s.scrollY ?? 0);
    },
  };
</script>

<svelte:head><title>{t('filme.seitenTitel')}</title></svelte:head>

<main id="inhalt">
  <h1 class="sr-only">{t('filme.ueberschriftSr')}</h1>

  {#if syncResult}
    <div class="meldung" class:fehler={syncResult.error}>
      {#if syncResult.error}
        {t('filme.fehlerKurz', { fehler: syncResult.error })}
      {:else}
        {t('filme.syncFertig', syncResult)}
      {/if}
    </div>
  {/if}

  <div class="kopf">
    <h2>{t('filme.deineFilme')}</h2>
    <span class="zahl">{loading ? '…' : t('filme.anzahl', { n: movies.length, total })}</span>
  </div>

  <!-- Ein Feld statt zwei. Kurze Eingaben filtern die Liste, ganze Saetze
       fragen zusaetzlich die KI — ohne dass man vorher waehlen muss. -->
  <div class="suchzeile">
    <label class="feld">
      <span class="lupe" aria-hidden="true">⌕</span>
      <input type="search" bind:value={q} oninput={beiEingabe}
             placeholder={t('filme.suchePlatzhalter')}
             aria-label={t('filme.sucheAria')} />
      {#if q}
        <button class="leeren" onclick={() => { q = ''; beiEingabe(); }}
                aria-label={t('filme.sucheLeeren')}>✕</button>
      {/if}
    </label>
    {#if filterAktiv}
      <button class="zuruecksetzen" onclick={zuruecksetzen}>{t('filme.filterZuruecksetzen')}</button>
    {/if}
  </div>

  <div class="filter">
    <select bind:value={genre} onchange={load} aria-label={t('filme.genre')}>
      <option value="">{t('filme.alleGenres')}</option>
      {#each facets.genres as g}
        <option value={g.name}>{genreName(g.name)} ({g.count})</option>
      {/each}
    </select>
    <select bind:value={year} onchange={load} aria-label={t('filme.jahr')}>
      <option value="">{t('filme.alleJahre')}</option>
      {#each facets.years as y}
        <option value={y.year}>{y.year}</option>
      {/each}
    </select>
    <select bind:value={played} onchange={load} aria-label={t('filme.gesehenStatus')}>
      <option value="">{t('filme.gesehenUndUngesehen')}</option>
      <option value="false">{t('filme.nurUngesehen')}</option>
      <option value="true">{t('filme.nurGesehen')}</option>
    </select>
    <label class="schalter" class:an={favOnly}>
      <input type="checkbox" bind:checked={favOnly} onchange={load} />
      <span>♥︎ {t('filme.favoriten')}</span>
    </label>
    <label class="schalter" class:an={deutschOnly}>
      <input type="checkbox" bind:checked={deutschOnly} onchange={load} />
      <span>{t('filme.deutscherTon')}</span>
    </label>
    <select bind:value={sort} aria-label={t('filme.sortierung')}
            onchange={() => { if (sort === 'ai_match' && order === 'asc') order = 'desc'; load(); }}>
      <option value="name">{t('filme.sortTitel')}</option>
      <option value="year">{t('filme.sortJahr')}</option>
      <option value="rating">{t('filme.sortBewertung')}</option>
      <option value="added">{t('filme.sortHinzugefuegt')}</option>
      <option value="ai_match">{t('filme.sortPassung')}</option>
    </select>
    <select bind:value={order} onchange={load} aria-label={t('filme.reihenfolge')}>
      <option value="asc">{t('filme.aufsteigend')}</option>
      <option value="desc">{t('filme.absteigend')}</option>
    </select>
    {#if user.isAdmin}
      <button class="sync" onclick={triggerSync} disabled={syncing}>
        {syncing ? t('filme.syncLaeuft') : t('filme.sync')}
      </button>
    {/if}
  </div>

  {#if kiGefragt}
    {#if kiTreffer.length}
      <Row title={t('filme.kiVorschlaege')} level={2}
           sub={kiLaeuft ? t('filme.kiSuchtNoch') : t('filme.kiPassendZu', { q })}>
        {#each kiTreffer as r (r.movie_id)}
          <PosterCard
            id={r.movie_id}
            name={r.name}
            href={r.media_type === 'series' ? `/series/${r.movie_id}` : `/movie/${r.movie_id}`}
            facts={[r.year, r.media_type === 'series' ? t('filme.serie') : null].filter(Boolean).join(' · ')}
            badge={r.relevanz != null ? t('filme.prozent', { n: r.relevanz }) : null}
          />
        {/each}
      </Row>
    {:else if kiLaeuft}
      <p class="hinweis">{t('filme.kiSucht')}</p>
    {/if}
  {/if}

  {#if error}
    <p class="hinweis fehler">{t('filme.fehlerLaden', { fehler: error })}</p>
  {:else if loading}
    <p class="hinweis">{t('filme.ladeFilme')}</p>
  {:else if movies.length === 0 && q.trim()}
    <!-- Wer einen Titel sucht, den es nicht gibt, will ihn meistens
         anfragen — Seerr liegt im selben Haus. -->
    <div class="leer">
      <p>{t('filme.keineTreffer', { q })}</p>
      {#if seerrBasis}
        <a class="seerr" href={`${seerrBasis}/search?query=${encodeURIComponent(q)}`}
           target="_blank" rel="noopener">{t('filme.inSeerr')}</a>
      {/if}
    </div>
  {:else if movies.length === 0}
    <div class="leer"><p>{t('filme.keinFilmFilter')}</p></div>
  {:else}
    <div class="gitter">
      {#each movies as m (m.id)}
        <PosterCard
          id={m.id}
          name={m.name}
          href={`/movie/${m.id}`}
          facts={fakten(m)}
          seen={m.played}
          favorite={m.favorite}
          score={typeof m.match_score === 'number' ? m.match_score : null}
        />
      {/each}
    </div>
  {/if}
</main>

<style>
  main {
    padding: var(--s-6) var(--s-7) var(--s-9);
    color: var(--text); font-family: var(--font-ui);
  }

  .kopf {
    display: flex; align-items: baseline; gap: var(--s-4);
    margin-bottom: var(--s-5);
  }
  .kopf h2 {
    font-family: var(--font-display); font-size: 30px; font-weight: 600;
    margin: 0; letter-spacing: 0.01em;
  }
  .zahl { font-size: 13px; color: var(--text-dim); }

  .meldung {
    background: var(--gold-wash); border-left: 2px solid var(--accent);
    padding: var(--s-3) var(--s-4); margin-bottom: var(--s-5);
    border-radius: 0 var(--r-sm) var(--r-sm) 0;
    font-size: 14px; color: var(--text-muted);
  }
  .meldung.fehler { border-left-color: var(--danger); color: var(--danger); }

  .suchzeile {
    display: flex; gap: var(--s-3); align-items: center;
    margin-bottom: var(--s-3); flex-wrap: wrap;
  }
  .feld {
    flex: 1; min-width: 260px;
    display: flex; align-items: center; gap: var(--s-3);
    height: 46px; padding: 0 var(--s-4);
    border-radius: var(--r-full); background: var(--layer-2);
    border: 1px solid transparent; cursor: text;
    transition: border-color var(--dur-base) var(--ease),
                box-shadow var(--dur-base) var(--ease);
  }
  .feld:focus-within {
    border-color: var(--gold-ring); box-shadow: 0 0 0 4px var(--gold-wash);
  }
  .lupe { color: var(--text-dim); font-size: 17px; }
  .feld input {
    flex: 1; min-width: 0; background: none; border: 0; outline: 0;
    color: var(--text); font-family: var(--font-ui); font-size: 15px;
  }
  .feld input::placeholder { color: var(--text-dim); }
  .feld input::-webkit-search-cancel-button { display: none; }
  .leeren {
    background: none; border: 0; color: var(--text-dim);
    cursor: pointer; font-size: 14px; padding: 2px 4px;
  }
  .leeren:hover { color: var(--text); }

  .zuruecksetzen {
    background: transparent; border: 1px solid var(--border-strong);
    color: var(--text-muted); padding: 10px var(--s-4);
    border-radius: var(--r-full); font-size: 13px;
    font-family: var(--font-ui); cursor: pointer; white-space: nowrap;
    transition: border-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
  }
  .zuruecksetzen:hover { border-color: var(--accent); color: var(--accent); }

  .filter {
    display: flex; gap: var(--s-2); flex-wrap: wrap; align-items: center;
    margin-bottom: var(--s-6);
  }
  .filter select {
    height: 38px; padding: 0 var(--s-3);
    background: var(--layer-2); color: var(--text);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: var(--r-sm);
    font-family: var(--font-ui); font-size: 13px; cursor: pointer;
    transition: border-color var(--dur-fast) var(--ease);
  }
  .filter select:hover { border-color: var(--gold-ring); }
  .filter select:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }

  .schalter {
    display: flex; align-items: center; gap: 7px;
    height: 38px; padding: 0 var(--s-3);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: var(--r-sm); cursor: pointer;
    font-size: 13px; color: var(--text-muted); user-select: none;
    transition: all var(--dur-fast) var(--ease);
  }
  .schalter:hover { border-color: var(--gold-ring); }
  .schalter.an { color: var(--accent); border-color: var(--accent); background: var(--gold-wash); }
  .schalter input { accent-color: var(--accent); margin: 0; }

  /* Sekundaere Handlung: Rand statt Flaeche. Vollgold bleibt dem
     Abspielen-Knopf vorbehalten. */
  .sync {
    height: 38px; padding: 0 var(--s-4); margin-left: auto;
    background: transparent; color: var(--text-muted);
    border: 1px solid var(--border-strong); border-radius: var(--r-sm);
    font-family: var(--font-ui); font-size: 13px; cursor: pointer;
    transition: border-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
  }
  .sync:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .sync:disabled { opacity: 0.5; cursor: wait; }

  /* Die Kacheln wachsen mit dem Fenster, statt bei fester Breite zu bleiben:
     auf einem 4K-Schirm standen sonst zwanzig Briefmarken nebeneinander. */
  /* Volle Breite, Spaltenzahl in festen Stufen. Vorher deckelte main auf
     1800px: auf einem 3840px-Schirm blieben 1944px ungenutzt, waehrend die
     Kacheln bei 243px klein blieben. Jetzt 12 Spalten a 297px statt 7 a 243px
     — groessere Poster UND weniger Scrollen, bezahlt aus dem Leerraum.
     Stufen statt clamp(vw): vw skaliert linear und sprang auf Laptopbreiten
     unguenstig um. */
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

  .hinweis { color: var(--text-dim); font-style: italic; padding: var(--s-5) 0; }
  .hinweis.fehler { color: var(--danger); font-style: normal; }

  .leer { padding: var(--s-7) 0; }
  .leer p { color: var(--text-muted); margin: 0 0 var(--s-3); }
  .seerr {
    color: var(--accent); text-decoration: none; font-size: 14px;
    border-bottom: 1px solid var(--gold-ring); padding-bottom: 2px;
  }
  .seerr:hover { color: var(--gold-bright); }

  @media (max-width: 820px) {
    main { padding: var(--s-4) var(--s-4) var(--s-8); }
    .kopf h2 { font-size: 24px; }
    .filter select, .schalter { flex: 1 1 calc(50% - var(--s-2)); }
    .sync { margin-left: 0; flex: 1 1 100%; }
    .gitter { gap: var(--s-3); }
  }
</style>
