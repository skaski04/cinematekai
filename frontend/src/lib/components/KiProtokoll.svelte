<script>
  /* KI-Protokoll: wie oft ging ein Aufruf eines Sprachmodells schief, wie oft
     musste der Rückfall einspringen, wie lange dauerte es? Daten aus
     GET /api/admin/ki-protokoll (backend/ki_protokoll.py). Gedacht für den
     Blick ab und zu und für Reviews — deshalb ohne automatisches Neuladen. */
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { t, hat, datum, zahl } from '$lib/i18n.svelte.js';

  const ZEITRAEUME = [7, 30, 90];
  let tage = $state(30);
  let daten = $state(null);
  let fehler = $state('');
  let laedt = $state(false);

  async function laden() {
    laedt = true;
    fehler = '';
    const r = await api(`/api/admin/ki-protokoll?tage=${tage}`);
    laedt = false;
    if (r.ok) daten = r.daten;
    else fehler = r.fehler;
  }

  onMount(laden);

  // Unbekannte Schlüssel (neue Aufgaben, seltene Fehlerarten wie http_504)
  // stehen so da, wie das Backend sie liefert.
  function aufgabe(a) {
    return hat(`kiprotokoll.aufgabe_${a}`) ? t(`kiprotokoll.aufgabe_${a}`) : a;
  }
  function rolle(r) {
    return hat(`kiprotokoll.rolle_${r}`) ? t(`kiprotokoll.rolle_${r}`) : r;
  }
  function fehlerart(f) {
    if (/^http_\d+$/.test(f)) return `HTTP ${f.slice(5)}`;
    return hat(`kiprotokoll.fehler_${f}`) ? t(`kiprotokoll.fehler_${f}`) : f;
  }
  function sek(s) {
    return s == null ? '—' : t('kiprotokoll.sek', { s: zahl(s, 1) });
  }
</script>

<section class="ki-protokoll" aria-labelledby="ki-protokoll-titel">
  <div class="kopfzeile">
    <h2 id="ki-protokoll-titel">{t('kiprotokoll.titel')}</h2>
    <label class="zeitraum">
      <span>{t('kiprotokoll.zeitraum')}</span>
      <select bind:value={tage} onchange={laden}>
        {#each ZEITRAEUME as n (n)}
          <option value={n}>{t('kiprotokoll.tage', { n })}</option>
        {/each}
      </select>
    </label>
  </div>
  <p class="dim">{t('kiprotokoll.erklaerung')}</p>

  <div aria-live="polite">
    {#if fehler}
      <p class="error">{fehler}</p>
    {:else if !daten || laedt}
      <p class="dim">{t('kiprotokoll.laden')}</p>
    {:else if daten.aufrufe === 0}
      <p class="dim">{t('kiprotokoll.leer')}</p>
    {:else}
      <p class="summe" class:warnung={daten.fehler > 0 || daten.unbrauchbar > 0}>
        {t('kiprotokoll.kopf', { aufrufe: daten.aufrufe, fehler: daten.fehler, rueckfall: daten.rueckfall, unbrauchbar: daten.unbrauchbar ?? 0 })}
      </p>

      <table class="gruppen">
        <thead>
          <tr>
            <th>{t('kiprotokoll.spalteAufgabe')}</th>
            <th>{t('kiprotokoll.spalteModell')}</th>
            <th>{t('kiprotokoll.spalteAufrufe')}</th>
            <th>{t('kiprotokoll.spalteErfolg')}</th>
            <th>{t('kiprotokoll.spalteDauer')}</th>
            <th>{t('kiprotokoll.spalteErstesWort')}</th>
          </tr>
        </thead>
        <tbody>
          {#each daten.je_aufgabe as g (g.aufgabe + g.rolle + g.modell)}
            <tr>
              <td data-label={t('kiprotokoll.spalteAufgabe')}>
                {aufgabe(g.aufgabe)}
                <span class="rolle" class:rueckfall={g.rolle === 'rueckfall'} class:auswertung={g.rolle === 'auswertung'}>{rolle(g.rolle)}</span>
              </td>
              <td data-label={t('kiprotokoll.spalteModell')} class="modell">{g.modell}</td>
              <td data-label={t('kiprotokoll.spalteAufrufe')}>{g.anzahl}</td>
              <td data-label={t('kiprotokoll.spalteErfolg')} class:schlecht={g.quote < 90}>
                {g.quote} % <span class="dim">({g.ok}/{g.anzahl})</span>
              </td>
              <td data-label={t('kiprotokoll.spalteDauer')}>{sek(g.dauer_median_s)} / {sek(g.dauer_p90_s)}</td>
              <td data-label={t('kiprotokoll.spalteErstesWort')}>{sek(g.erstes_wort_median_s)}</td>
            </tr>
          {/each}
        </tbody>
      </table>

      <h3>{t('kiprotokoll.fehlerartenTitel')}</h3>
      {#if daten.fehlerarten.length === 0}
        <p class="dim">{t('kiprotokoll.keineFehler')}</p>
      {:else}
        <ul class="arten">
          {#each daten.fehlerarten as f (f.modell + f.fehlerart)}
            <li><strong>{f.anzahl}×</strong> {fehlerart(f.fehlerart)} <span class="dim">· {f.modell}</span></li>
          {/each}
        </ul>
      {/if}

      {#if daten.letzte_fehler.length}
        <details>
          <summary>{t('kiprotokoll.letzteFehlerTitel', { n: daten.letzte_fehler.length })}</summary>
          <ul class="letzte">
            {#each daten.letzte_fehler as f, i (i)}
              <li>
                <span class="dim">{datum(f.zeit)}</span> ·
                {aufgabe(f.aufgabe)} ({rolle(f.rolle)}) ·
                <strong>{fehlerart(f.fehlerart)}</strong>
                <span class="dim">{t('kiprotokoll.nachSek', { s: zahl(f.dauer_s, 1) })}</span>
                {#if f.fehlertext}<div class="text">{f.fehlertext}</div>{/if}
              </li>
            {/each}
          </ul>
        </details>
      {/if}
    {/if}
  </div>
</section>

<style>
  .ki-protokoll { margin-top: 32px; }
  .kopfzeile {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 12px; flex-wrap: wrap;
  }
  h2 {
    margin: 0;
    font-family: var(--font-display);
    font-weight: 600;
    letter-spacing: 0.02em;
  }
  h3 {
    margin: 20px 0 8px; font-size: 13px; font-weight: 500;
    color: var(--text-muted); letter-spacing: 0.06em; text-transform: uppercase;
  }
  .zeitraum { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-muted); }
  select {
    background: var(--surface); color: var(--text);
    border: 1px solid var(--border-strong); border-radius: 2px; padding: 5px 8px; font: inherit;
  }
  .dim { color: var(--text-dim); font-size: 12px; }
  .error { color: var(--danger); }
  .summe { margin: 12px 0 8px; font-size: 14px; }
  .summe.warnung { color: var(--accent); }
  table { width: 100%; border-collapse: collapse; margin-top: 4px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
  th { color: var(--text-muted); font-size: 12px; letter-spacing: 0.06em;
       text-transform: uppercase; font-weight: 500; }
  td.modell { font-size: 12px; color: var(--text-muted); overflow-wrap: anywhere; }
  td.schlecht { color: var(--danger); }
  .rolle {
    display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 2px;
    font-size: 11px; color: var(--text-dim); border: 1px solid var(--border);
  }
  .rolle.rueckfall { color: var(--accent); border-color: var(--accent); }
  .rolle.auswertung { font-style: italic; }
  ul { margin: 0; padding-left: 18px; }
  li { margin: 4px 0; font-size: 13px; }
  details { margin-top: 16px; }
  summary { cursor: pointer; color: var(--text-muted); font-size: 13px; }
  .letzte .text {
    color: var(--text-dim); font-size: 12px; margin-top: 2px;
    overflow-wrap: anywhere;
  }

  @media (max-width: 720px) {
    table, thead, tbody, tr, th, td { display: block; }
    thead { display: none; }
    tr { border: 1px solid var(--border); margin-bottom: 10px; padding: 8px; }
    td { border: 0; padding: 3px 0; }
    td::before {
      content: attr(data-label); display: block;
      color: var(--text-dim); font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase;
    }
  }
</style>
