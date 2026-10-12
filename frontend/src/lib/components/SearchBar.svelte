<script>
  /* Ein Feld statt zwei. Auf /movies stehen bisher "Stimmungssuche" und
     "Titelsuche" als getrennte Zeilen mit Beschriftung daneben — man muss
     vorher entscheiden, welche Art Suche man macht, bevor man weiss, was
     man sucht.

     Die Weiche raet nicht: Titeltreffer laufen immer (billig, sofort). Die
     KI-Suche kommt zusaetzlich dazu, sobald die Eingabe satzartig wird —
     ab drei Woertern. "Nolan" liefert Titel, "dunkler Thriller fuer heute
     Abend" liefert beides, und wer sich vertippt, verliert nichts. */
  import { tick } from 'svelte';
  import { goto } from '$app/navigation';
  import { t } from '$lib/i18n.svelte.js';

  /* Ohne eigenen Platzhalter gilt der aus dem Woerterbuch — erst im Markup
     geholt, damit er beim Sprachwechsel mitgeht (ein Vorgabewert der Prop
     wuerde nur einmal ausgewertet). */
  let { placeholder = null } = $props();

  let q = $state('');
  let offen = $state(false);
  let titel = $state([]);
  let stimmung = $state([]);
  let laedt = $state(false);
  let kiLaeuft = $state(false);
  let feld = $state(null);
  let lauf = 0;
  let aktiv = $state(-1);   // per Tastatur angesteuerte Zeile, -1 = keine

  const listeId = $props.id();

  /* Frueher stand neben der Suche eine Auswahl, ob einzelne Folgen mitgesucht
     werden sollen; beim Umbau ist sie verlorengegangen. Im Backend heisst der
     Parameter include_episodes (/recommendations/mood) und schaltet die
     Semantiksuche auf "any" um, sodass auch Folgen in den Treffern landen. */
  const SPEICHER = 'cinematekai.folgen-mitsuchen';
  let folgen = $state(false);
  /* Beim Serverrendern gibt es kein localStorage, und im privaten Modus kann
     der Zugriff werfen — dann bleibt es bei der Voreinstellung. */
  try { folgen = localStorage.getItem(SPEICHER) === '1'; } catch { /* egal */ }

  const KI_AB_WOERTERN = 3;
  const wortzahl = (s) => s.trim().split(/\s+/).filter(Boolean).length;

  const titelTreffer = $derived(titel.slice(0, 8));
  const kiTreffer = $derived(stimmung.slice(0, 8));
  /* Eine flache Liste ueber beide Gruppen: die Pfeiltasten laufen durch die
     Zeilen, die Zwischenueberschriften ueberspringen sie. */
  const zeilen = $derived([
    ...titelTreffer.map((m) => `/movie/${m.id}`),
    ...kiTreffer.map((r) => href(r)),
  ]);

  async function hole(url) {
    try {
      const r = await fetch(url, { credentials: 'include' });
      return r.ok ? await r.json() : null;
    } catch { return null; }
  }

  let timer;
  function tippen() {
    clearTimeout(timer);
    const text = q.trim();
    if (text.length < 2) { verwerfen(); return; }
    offen = true;
    aktiv = -1;
    timer = setTimeout(() => suche(text), 220);
  }

  /* Der Lauf-Zaehler wird auch beim Leeren hochgezaehlt. Sonst schreibt eine
     noch unterwegs befindliche Antwort die geleerte Liste wieder voll — man
     loeschte das Feld und bekam Sekunden spaeter die alten Treffer zurueck. */
  function verwerfen() {
    clearTimeout(timer);
    lauf++;
    titel = []; stimmung = [];
    laedt = false; kiLaeuft = false;
    aktiv = -1; offen = false;
  }

  /* Der Entprell-Zeitgeber ueberlebte bisher das Zerstoeren der Komponente
     und lief danach ins Leere. */
  $effect(() => () => clearTimeout(timer));

  async function suche(text) {
    const meins = ++lauf;
    laedt = true;
    stimmung = [];
    aktiv = -1;

    const d = await hole(`/api/movies?q=${encodeURIComponent(text)}&limit=12`);
    if (meins !== lauf) return;            // eine neuere Eingabe hat uebernommen
    titel = d?.items ?? [];
    laedt = false;

    if (wortzahl(text) >= KI_AB_WOERTERN) {
      kiLaeuft = true;
      const zusatz = folgen ? '&include_episodes=true' : '';
      const m = await hole(
        `/api/recommendations/mood?q=${encodeURIComponent(text)}&k=12${zusatz}`);
      if (meins !== lauf) return;
      stimmung = m?.results ?? [];
      kiLaeuft = false;
    }
  }

  function folgenGemerkt() {
    try { localStorage.setItem(SPEICHER, folgen ? '1' : '0'); } catch { /* egal */ }
    /* Nur die KI-Suche kennt den Parameter — die Titelsuche noch einmal zu
       starten braechte dieselben Treffer zurueck. */
    const text = q.trim();
    if (wortzahl(text) >= KI_AB_WOERTERN) suche(text);
  }

  function schliessen() { offen = false; aktiv = -1; }

  function href(r) {
    if (r.media_type === 'series') return `/series/${r.movie_id}`;
    if (r.media_type === 'episode') return `/series/${r.series_id}`;
    return `/movie/${r.movie_id ?? r.id}`;
  }

  /* Folgentreffer kommen als eine Zeile "Serie · S2E2: Titel" herein
     (backend/recommendations.py, _hydrate). Fuer die Liste trennen wir sie
     wieder auf: der Folgentitel oben, Serie und Kennung darunter. */
  const FOLGE = /^(.*?) · (S\d+E\d+): (.*)$/;
  function folge(r) {
    const f = FOLGE.exec(r.name ?? '');
    if (f) return { serie: f[1], kennung: f[2], titel: f[3] };
    return { serie: '', kennung: r.label ?? '', titel: r.name ?? '' };
  }

  async function fokus() {
    await tick();
    feld?.focus();
  }

  function taste(e) {
    if (e.key === 'Escape') {
      /* Escape schliesst nur die Liste. Der Fokus bleibt im Feld, damit man
         die Eingabe abwandeln kann, statt sie neu tippen zu muessen. */
      if (offen) { e.preventDefault(); schliessen(); }
      return;
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!zeilen.length) return;
      e.preventDefault();
      offen = true;
      const schritt = e.key === 'ArrowDown' ? 1 : -1;
      aktiv = aktiv < 0
        ? (schritt > 0 ? 0 : zeilen.length - 1)
        : (aktiv + schritt + zeilen.length) % zeilen.length;
      return;
    }
    if (e.key === 'Enter' && offen && aktiv >= 0 && zeilen[aktiv]) {
      e.preventDefault();
      const ziel = zeilen[aktiv];
      schliessen();
      goto(ziel);
    }
  }

  /* Die angesteuerte Zeile muss im Blick bleiben — die Liste ist hoeher als
     ihr Ausschnitt. */
  $effect(() => {
    if (aktiv < 0) return;
    document.getElementById(`${listeId}-${aktiv}`)?.scrollIntoView({ block: 'nearest' });
  });
</script>

<svelte:window on:keydown={(e) => {
  if (e.key === '/' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
    e.preventDefault(); fokus();
  }
}} />

<div class="suche">
  <label class="feld" class:aktiv={offen}>
    <span class="lupe" aria-hidden="true">⌕</span>
    <input bind:this={feld} bind:value={q} oninput={tippen} onkeydown={taste}
           type="search" placeholder={placeholder ?? t('suche.platzhalter')}
           aria-label={t('suche.suche')}
           role="combobox" aria-expanded={offen}
           aria-controls={offen ? listeId : undefined}
           aria-autocomplete="list" autocomplete="off"
           aria-activedescendant={aktiv >= 0 ? `${listeId}-${aktiv}` : undefined} />
    {#if q}
      <button class="leeren" onclick={() => { q = ''; verwerfen(); }}
              aria-label={t('suche.leeren')}>✕</button>
    {:else}
      <kbd>/</kbd>
    {/if}
  </label>

  {#if offen}
    <div class="panel">
      <!-- Steht ueber den Treffern, weil sie aendert, was darunter steht. -->
      <label class="folgen">
        <input type="checkbox" bind:checked={folgen} onchange={folgenGemerkt} />
        {t('suche.folgenMitsuchen')}
      </label>

      <!-- role=presentation an allem, was keine Trefferzeile ist: die Liste
           darf laut ARIA nur Optionen fuehren, die Ueberschriften bleiben
           aber sichtbar und lesbar. -->
      <div class="liste" id={listeId} role="listbox" aria-label={t('suche.suchtreffer')}>
        {#if laedt}
          <p class="hinweis" role="presentation">{t('suche.sucht')}</p>
        {:else if !titelTreffer.length && !kiTreffer.length && !kiLaeuft}
          <p class="hinweis" role="presentation">{t('suche.nichtsGefunden')}</p>
        {/if}

        {#if titelTreffer.length}
          <p class="gruppe" role="presentation">{t('suche.ausBibliothek')}</p>
          <div class="treffer" role="presentation">
            {#each titelTreffer as m, i (m.id)}
              <a class="zeile" class:her={aktiv === i} href={`/movie/${m.id}`}
                 id={`${listeId}-${i}`} role="option" aria-selected={aktiv === i}
                 tabindex="-1" onclick={schliessen}>
                <img src="/api/image/{m.id}?w=100" alt="" loading="lazy" />
                <span class="tx">
                  <b>{m.name}</b>
                  <i>{[m.year, m.runtime_min ? t('suche.minuten', { n: m.runtime_min }) : null]
                       .filter(Boolean).join(' · ')}</i>
                </span>
              </a>
            {/each}
          </div>
        {/if}

        {#if wortzahl(q) >= KI_AB_WOERTERN}
          <p class="gruppe ki" role="presentation">
            {t('suche.kiVorschlaege')}
            {#if kiLaeuft}<span class="punkte">{t('suche.denktNach')}</span>{/if}
          </p>
          {#if kiTreffer.length}
            <div class="treffer" role="presentation">
              {#each kiTreffer as r, i (r.movie_id)}
                {@const idx = titelTreffer.length + i}
                {@const f = folge(r)}
                <a class="zeile" class:her={aktiv === idx} href={href(r)}
                   id={`${listeId}-${idx}`} role="option" aria-selected={aktiv === idx}
                   tabindex="-1" onclick={schliessen}>
                  <img src="/api/image/{r.movie_id}?w=100" alt="" loading="lazy" />
                  <span class="tx">
                    {#if r.media_type === 'episode'}
                      <b>{f.titel}</b>
                      <i>{[f.serie, f.kennung].filter(Boolean).join(' · ')}</i>
                    {:else}
                      <b>{r.name}</b>
                      <i>{r.year ?? ''}{r.media_type === 'series' ? ` · ${t('suche.serie')}` : ''}</i>
                    {/if}
                  </span>
                </a>
              {/each}
            </div>
          {:else if !kiLaeuft}
            <p class="hinweis" role="presentation">{t('suche.nichtsPassendes')}</p>
          {/if}
        {:else if titelTreffer.length}
          <p class="hinweis leise" role="presentation">
            {t('suche.ganzerSatz')}
          </p>
        {/if}
      </div>
    </div>

    <button class="schleier" onclick={schliessen} aria-label={t('allgemein.schliessen')} tabindex="-1"></button>
  {/if}
</div>

<style>
  .suche { position: relative; }

  .feld {
    display: flex; align-items: center; gap: var(--s-3);
    /* Mitwachsend statt fest 340 px: auf Laptop-Breiten (900-1300 px) schob
       das feste Feld Benutzername und Abmelden aus dem Fenster. */
    width: clamp(160px, 18vw, 340px); height: 40px; padding: 0 var(--s-4);
    border-radius: var(--r-full);
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid transparent; cursor: text;
    transition: width var(--dur-base) var(--ease),
                background var(--dur-base) var(--ease),
                border-color var(--dur-base) var(--ease);
  }
  .feld:hover { background: rgba(255, 255, 255, 0.08); }
  .feld:focus-within {
    width: clamp(220px, 30vw, 440px); background: var(--layer-2);
    border-color: var(--gold-ring);
    box-shadow: 0 0 0 4px var(--gold-wash);
  }
  .lupe { color: var(--text-dim); font-size: 16px; }
  .feld input {
    flex: 1; min-width: 0; background: none; border: 0; outline: 0;
    color: var(--text); font-family: var(--font-ui); font-size: 14px;
  }
  .feld input::placeholder { color: var(--text-dim); }
  .feld input::-webkit-search-cancel-button { display: none; }
  kbd {
    font-size: 11px; color: var(--text-dim); font-family: var(--font-ui);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: var(--r-sm); padding: 2px 6px;
  }
  .leeren {
    background: none; border: 0; color: var(--text-dim);
    cursor: pointer; font-size: 13px; padding: 2px 4px;
  }
  .leeren:hover { color: var(--text); }

  .panel {
    position: absolute; top: calc(100% + 10px); right: 0; z-index: 210;
    width: 440px; max-height: 60vh; overflow-y: auto;
    background: var(--layer-2); border-radius: var(--r-md);
    border: 1px solid rgba(255, 255, 255, 0.09);
    box-shadow: var(--elev-3); padding: var(--s-3);
  }

  .gruppe {
    font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--text-dim); margin: var(--s-2) 0 var(--s-2); padding: 0 var(--s-2);
  }
  .gruppe.ki { color: var(--accent); margin-top: var(--s-4); }
  .punkte {
    font-family: var(--font-display); font-style: italic;
    text-transform: none; letter-spacing: 0; color: var(--text-dim);
    margin-left: 6px; font-size: 12px;
  }

  /* Eine Einstellung, kein Treffer — klein, ueber der Liste, mit Trennstrich. */
  .folgen {
    display: flex; align-items: center; gap: var(--s-2);
    padding: var(--s-2); margin-bottom: var(--s-2);
    border-bottom: 1px solid rgba(255, 255, 255, 0.07);
    font-size: 12px; color: var(--text-dim); cursor: pointer;
    transition: color var(--dur-fast) var(--ease);
  }
  .folgen:hover { color: var(--text-muted); }
  .folgen input { margin: 0; accent-color: var(--accent); cursor: pointer; }

  .treffer { display: flex; flex-direction: column; }
  .zeile {
    display: flex; align-items: center; gap: var(--s-3);
    padding: var(--s-2); border-radius: var(--r-sm);
    text-decoration: none; color: inherit;
    transition: background var(--dur-fast) var(--ease);
  }
  .zeile:hover, .zeile:focus-visible { background: var(--layer-3); }
  /* Die per Pfeiltaste angesteuerte Zeile muss sich von der blossen Mausspur
     unterscheiden — dafuer der goldene Strich an der Kante. */
  .zeile.her { background: var(--layer-3); box-shadow: inset 2px 0 0 var(--accent); }
  .zeile img {
    width: 34px; aspect-ratio: 2 / 3; object-fit: cover;
    border-radius: 3px; background: var(--layer-3); flex: none;
  }
  .tx { display: flex; flex-direction: column; min-width: 0; }
  .tx b {
    font-weight: 500; font-size: 14px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .tx i { font-style: normal; font-size: 12px; color: var(--text-dim); }

  .hinweis { font-size: 13px; color: var(--text-dim); padding: var(--s-3) var(--s-2); margin: 0; }
  .hinweis.leise {
    font-family: var(--font-display); font-style: italic; font-size: 14px;
    color: var(--accent-muted);
  }

  /* Faengt den Klick daneben ab, ohne den Inhalt darunter zu verdecken. */
  .schleier {
    position: fixed; inset: 0; z-index: 205;
    background: none; border: 0; cursor: default;
  }

  @media (max-width: 820px) {
    .feld, .feld:focus-within { width: 100%; }
    .panel { width: calc(100vw - 2 * var(--s-4)); right: auto; left: 0; }
  }
</style>
