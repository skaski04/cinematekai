<script>
  /* Serienuebersicht, auf dieselben Bausteine gebracht wie die Filmseite:
     PosterCard statt eigener Kachel, gleiche Filterzeile, gleicher Kopf.

     Bewusst ohne KI-Suche: der Endpunkt /recommendations/mood durchsucht
     Filme und Serien gemeinsam und liefert gemischte Treffer — auf einer
     reinen Serienseite waere das mehr verwirrend als hilfreich. Wer so
     sucht, nutzt das Feld in der Kopfleiste. */
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import PosterCard from '$lib/components/PosterCard.svelte';
  import { t, genre as genreText } from '$lib/i18n.svelte.js';

  let items = $state([]);
  let total = $state(0);
  let loading = $state(true);
  let error = $state('');
  let q = $state('');
  let genre = $state('');
  let sort = $state('name');
  let order = $state('asc');

  async function load() {
    loading = true;
    error = '';
    try {
      const params = new URLSearchParams({ sort, order, limit: '500' });
      if (q.trim()) params.set('q', q.trim());
      if (genre) params.set('genre', genre);
      const r = await fetch(`/api/series?${params}`, { credentials: 'include' });
      if (r.status === 401) { window.location.href = '/login'; return; }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      items = data.items || [];
      total = data.total || 0;
    } catch (e) {
      error = String(e?.message || e);
    } finally {
      loading = false;
    }
  }

  let sucheTimer = null;
  function beiEingabe() {
    clearTimeout(sucheTimer);
    sucheTimer = setTimeout(load, 250);
  }

  /* Die Auswahlliste wurde aus den angezeigten Treffern gebaut und schrumpfte
     damit, sobald ein Genre gesetzt war — von einem Genre kam man nicht mehr
     direkt zum naechsten. Ein Serien-Facettenendpunkt existiert nicht, also
     wird der Bestand einmal ungefiltert geholt; danach bleibt die Liste
     vollstaendig, egal was gefiltert ist. */
  let alleGenres = $state([]);

  async function ladeGenres() {
    try {
      const r = await fetch('/api/series?limit=500', { credentials: 'include' });
      if (!r.ok) return;
      const d = await r.json();
      const set = new Set();
      for (const s of (d.items || [])) for (const g of (s.genres || [])) set.add(g);
      alleGenres = [...set].sort();
    } catch {}
  }

  const filterAktiv = $derived(!!(q || genre));
  function zuruecksetzen() { q = ''; genre = ''; load(); }

  function jahrText(s) {
    if (!s.year) return '';
    if (s.status === 'ended' && s.end_year && s.end_year !== s.year) return `${s.year}–${s.end_year}`;
    if (s.status === 'continuing') return `${s.year}–`;
    return `${s.year}`;
  }

  const fakten = (s) => [jahrText(s),
                         s.episode_count ? t('serien.folgen', { n: s.episode_count }) : null,
                         s.rating ? `★ ${s.rating.toFixed(1)}` : null]
                        .filter(Boolean).join(' · ');

  onMount(() => {
    const sp = $page.url.searchParams;
    if (sp.get('genre')) genre = sp.get('genre');
    if (sp.get('q')) q = sp.get('q');
    ladeGenres();
    load();
  });
</script>

<svelte:head><title>{t('serien.seitentitel')}</title></svelte:head>

<main id="inhalt">
  <h1 class="sr-only">{t('serien.ueberschrift')}</h1>

  <div class="kopf">
    <h2>{t('serien.deine')}</h2>
    <span class="zahl">{loading ? '…' : t('serien.anzahl', { n: items.length, gesamt: total })}</span>
  </div>

  <div class="suchzeile">
    <label class="feld">
      <span class="lupe" aria-hidden="true">⌕</span>
      <input type="search" bind:value={q} oninput={beiEingabe}
             placeholder={t('serien.suchePlatzhalter')}
             aria-label={t('serien.sucheLabel')} />
      {#if q}
        <button class="leeren" onclick={() => { q = ''; beiEingabe(); }}
                aria-label={t('serien.sucheLeeren')}>✕</button>
      {/if}
    </label>
    {#if filterAktiv}
      <button class="zuruecksetzen" onclick={zuruecksetzen}>{t('serien.filterZuruecksetzen')}</button>
    {/if}
  </div>

  <div class="filter">
    <select bind:value={genre} onchange={load} aria-label={t('serien.genre')}>
      <option value="">{t('serien.alleGenres')}</option>
      {#each alleGenres as g}<option value={g}>{genreText(g)}</option>{/each}
    </select>
    <select bind:value={sort} aria-label={t('serien.sortierung')}
            onchange={() => { if (sort === 'ai_match' && order === 'asc') order = 'desc'; load(); }}>
      <option value="name">{t('serien.sortTitel')}</option>
      <option value="year">{t('serien.sortJahr')}</option>
      <option value="rating">{t('serien.sortBewertung')}</option>
      <option value="added">{t('serien.sortHinzugefuegt')}</option>
      <option value="ai_match">{t('serien.sortPassung')}</option>
    </select>
    <select bind:value={order} onchange={load} aria-label={t('serien.reihenfolge')}>
      <option value="asc">{t('serien.aufsteigend')}</option>
      <option value="desc">{t('serien.absteigend')}</option>
    </select>
  </div>

  {#if error}
    <p class="hinweis fehler">{t('serien.fehlerText', { text: error })}</p>
  {:else if loading}
    <p class="hinweis">{t('serien.ladeSerien')}</p>
  {:else if items.length === 0}
    <div class="leer">
      <p>{filterAktiv ? t('serien.keinTreffer') : t('serien.nochKeine')}</p>
    </div>
  {:else}
    <div class="gitter">
      {#each items as s (s.id)}
        <PosterCard
          id={s.id}
          name={s.name}
          href={`/series/${s.id}`}
          facts={fakten(s)}
          badge={s.status === 'continuing' ? t('serien.laeuft') : null}
          badgeStrong={s.status === 'continuing'}
          score={typeof s.match_score === 'number' ? s.match_score : null}
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
  .leer p { color: var(--text-muted); margin: 0; }

  @media (max-width: 820px) {
    main { padding: var(--s-4) var(--s-4) var(--s-8); }
    .kopf h2 { font-size: 24px; }
    .filter select { flex: 1 1 calc(50% - var(--s-2)); }
    .gitter { gap: var(--s-3); }
  }
</style>
