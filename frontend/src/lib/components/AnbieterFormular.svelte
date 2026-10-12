<script>
  /* Ein Formular fuer alle drei KI-Abschnitte: Sprachmodell (llm),
     Rueckfallmodell (llm_fallback) und Embeddings (embedding). Einrichtung
     und Einstellungsseite nutzen dasselbe, damit sich ein Anbieter an beiden
     Stellen gleich verhaelt.

     Was hier bewusst so gebaut ist:
     - Gespeichert werden nur geaenderte Felder. Das Backend behandelt ein
       fehlendes Schluesselfeld als "behalten"; schickte das Formular immer
       alles, waere jeder Speichern-Klick zugleich ein Schluesselwechsel.
     - Der Schluessel kommt nie zurueck in den Browser. Das Feld bleibt leer,
       daneben steht nur, ob einer gespeichert ist (und seine letzten vier
       Zeichen, wenn er lang genug ist).
     - Wechselt Anbieter oder Adresse ohne neuen Schluessel, verwirft das
       Backend den alten (sonst ginge der Schluessel des einen Anbieters an den
       naechsten). Das Formular sagt das vorher, statt es still passieren zu
       lassen. */
  import { tick } from 'svelte';
  import { api, adresse } from '$lib/api.js';
  import { t, zahl } from '$lib/i18n.svelte.js';
  import '$lib/formular.css';

  let {
    abschnitt,                 // 'llm' | 'llm_fallback' | 'embedding'
    werte = $bindable(),       // maskierter Abschnitt aus /api/admin/settings
    anbieter = [],             // PRESETS aus /api/admin/settings
    knopfText = '',            // leer = "Speichern" in der aktuellen Sprache
    /* Nach dem Speichern: bekommt die Antwort des Backends ({abschnitt,
       einbettung}) oder null, wenn es nichts zu speichern gab. Die
       Einrichtung geht damit zum naechsten Schritt. */
    ongespeichert = () => {},
  } = $props();

  const uid = $props.id();

  const FELDER = {
    llm: ['provider', 'url', 'model', 'fast_model', 'parallel', 'context_tokens'],
    llm_fallback: ['provider', 'url', 'model', 'context_tokens'],
    embedding: ['provider', 'url', 'model'],
  };
  const ZAHLEN = new Set(['parallel', 'context_tokens']);

  /* Die Fragen vor dem Speichern der Embeddings (formular.einbettFrage,
     formular.einbettAusFrage): Wortlaut aus der Spezifikation; die Frage
     kommt nur, wenn schon ein Modell eingetragen war — beim ersten
     Einrichten gibt es nichts zu ersetzen. "— keiner —" bei den Embeddings
     schaltet Suche und Passung ab. Mit der ersten Frage ("wird neu
     eingebettet") stuende dort das Gegenteil dessen, was passiert. */

  const istLlm = $derived(abschnitt === 'llm');
  const hatKontext = $derived(abschnitt === 'llm' || abschnitt === 'llm_fallback');
  const istEinbettung = $derived(abschnitt === 'embedding');
  const auswahl = $derived(anbieter.filter((a) => (istEinbettung ? a.einbettung : a.chat)));

  let e = $state({ provider: '', url: '', model: '', fast_model: '', parallel: 1, context_tokens: 8192 });
  let schluessel = $state('');
  let modelle = $state([]);
  let modelleInfo = $state(null);   // Meldung, siehe txt()
  let test = $state(null);          // Meldung, dazu schnell?: Meldung
  let meldung = $state(null);
  let laeuft = $state('');          // '' | 'modelle' | 'test' | 'speichern' | 'entfernen'

  /* Meldungen: { art, k, w } (Schluessel im Woerterbuch, erst beim Anzeigen
     uebersetzt — so wechseln sie mit der Sprache), { art, f } (Funktion fuer
     zusammengesetzte Texte) oder { art, text } (Text vom Backend). */
  function txt(m) {
    if (!m) return '';
    if (m.f) return m.f();
    if (m.k) return t(m.k, m.w);
    return m.text ?? '';
  }

  /** 1234 -> "1,2 s" / "1.2 s" */
  function sekunden(ms) {
    if (ms == null || Number.isNaN(Number(ms))) return '';
    return `${zahl(Number(ms) / 1000, 1)} s`;
  }

  /* Entwurf aus den gespeicherten Werten. Laeuft beim Start und immer dann,
     wenn die Seite neue Werte hereingibt (nach dem Speichern). */
  $effect.pre(() => {
    const w = werte ?? {};
    e = {
      provider: w.provider ?? '',
      url: w.url ?? '',
      model: w.model ?? '',
      fast_model: w.fast_model ?? '',
      parallel: w.parallel ?? 1,
      context_tokens: w.context_tokens ?? 8192,
    };
    schluessel = '';
  });

  const basis = $derived(werte ?? {});
  const p = $derived(anbieter.find((a) => a.id === e.provider) ?? null);
  const brauchtSchluesselFeld = $derived(!!p && p.schluessel !== 'nein');
  const anbieterNeu = $derived(e.provider !== (basis.provider ?? ''));
  const umgezogen = $derived(anbieterNeu || adresse(e.url) !== adresse(basis.url));
  const schluesselVerworfen = $derived(umgezogen && !!basis.api_key_gesetzt && !schluessel.trim());
  const schluesselDa = $derived(!!schluessel.trim() || (!!basis.api_key_gesetzt && !umgezogen));

  function anbieterWaehlen(id) {
    e.provider = id;
    if (id === (basis.provider ?? '')) {
      // Zurueck zum gespeicherten Anbieter: dessen Werte wiederherstellen.
      e.url = basis.url ?? '';
      e.model = basis.model ?? '';
      e.fast_model = basis.fast_model ?? '';
      if (hatKontext) e.context_tokens = basis.context_tokens ?? e.context_tokens;
    } else {
      /* Modellnamen gelten nur beim jeweiligen Anbieter; ein stehengebliebener
         Name fuehrte beim neuen nur zu einem Fehler beim ersten Aufruf. */
      e.url = '';
      e.model = '';
      e.fast_model = '';
      const neu = anbieter.find((a) => a.id === id);
      if (hatKontext && neu?.kontext) e.context_tokens = neu.kontext;
    }
    modelle = [];
    modelleInfo = null;
    test = null;
    meldung = null;
  }

  function verbindungsDaten() {
    return {
      abschnitt,
      provider: e.provider,
      url: adresse(e.url),
      api_key: brauchtSchluesselFeld ? schluessel.trim() : '',
    };
  }

  async function modelleLaden() {
    if (!e.provider) { modelleInfo = { art: 'fehler', k: 'formular.zuerstAnbieter' }; return; }
    laeuft = 'modelle';
    modelleInfo = { art: 'info', k: 'formular.frageModelle' };
    const r = await api('/api/admin/settings/modelle', { method: 'POST', body: verbindungsDaten() });
    laeuft = '';
    if (!r.ok) { modelleInfo = { art: 'fehler', text: r.fehler }; return; }
    modelle = r.daten?.modelle ?? [];
    const n = modelle.length;
    modelleInfo = n
      ? { art: 'ok', k: 'formular.modelleGefunden', w: { n } }
      : { art: 'info', k: 'formular.keineModelle' };
  }

  function ergebnisText(d) {
    let text = `${t('formular.ok')} · ${sekunden(d.ms)}`;
    if (d.antwort != null && d.antwort !== '') text += ` · ${t('formular.antwort', { antwort: d.antwort })}`;
    if (d.dimension) text += ` · ${t('formular.dimensionen', { n: d.dimension })}`;
    return text;
  }

  /* Ergebnis eines Tests als Meldung; der Fehlertext kommt vom Backend. */
  function testMeldung(d) {
    if (d.ok) return { art: 'ok', f: () => ergebnisText(d) };
    return d.fehler ? { art: 'fehler', text: d.fehler } : { art: 'fehler', k: 'formular.testFehlgeschlagen' };
  }

  async function testen() {
    if (!e.provider) { test = { art: 'fehler', k: 'formular.zuerstAnbieter' }; return; }
    if (!e.model.trim()) { test = { art: 'fehler', k: 'formular.zuerstModell' }; return; }
    laeuft = 'test';
    test = { art: 'info', k: p?.api === 'ollama' || p?.id === 'lmstudio'
      ? 'formular.testeLokal'
      : 'formular.teste' };
    const body = { ...verbindungsDaten(), model: e.model.trim() };
    if (istLlm && e.fast_model.trim()) body.fast_model = e.fast_model.trim();
    const r = await api('/api/admin/settings/testen', { method: 'POST', body });
    laeuft = '';
    if (!r.ok) { test = { art: 'fehler', text: r.fehler }; return; }
    const d = r.daten ?? {};
    test = testMeldung(d);
    if (d.schnell) {
      const s = testMeldung(d.schnell);
      test.schnell = { art: s.art, f: () => t('formular.schnellesModellErgebnis', { text: txt(s) }) };
    }
  }

  function aenderungen() {
    const aus = {};
    for (const f of FELDER[abschnitt]) {
      if (ZAHLEN.has(f)) {
        if (Number(e[f]) !== Number(basis[f])) aus[f] = Number(e[f]);
        continue;
      }
      const neu = f === 'url' ? adresse(e[f]) : String(e[f] ?? '').trim();
      const alt = f === 'url' ? adresse(basis[f]) : String(basis[f] ?? '').trim();
      if (neu !== alt) aus[f] = neu;
    }
    if (brauchtSchluesselFeld && schluessel.trim()) aus.api_key = schluessel.trim();
    return aus;
  }

  /* Was das Backend zwar annaehme, aber nicht benutzen koennte: ohne Modell
     oder ohne Pflichtschluessel gilt der Abschnitt dort als nicht
     eingerichtet. Besser gleich sagen als spaeter rätseln lassen.
     Ergebnis: null oder { k, w } fuer die Meldung. */
  function pruefen() {
    if (!e.provider) return null;
    if (!p) return { k: 'formular.unbekannterAnbieter' };
    if (!adresse(e.url) && !p.url) return { k: 'formular.brauchtAdresse', w: { name: p.name } };
    // Das Backend lehnt es auch ab, sagt es aber mit Feldnamen ("llm.url: …").
    if (adresse(e.url) && !/^https?:\/\/\S+/i.test(adresse(e.url)))
      return { k: 'formular.adresseSchema' };
    if (p.schluessel === 'pflicht' && !schluesselDa) return { k: 'formular.brauchtSchluessel', w: { name: p.name } };
    if (!e.model.trim()) return { k: 'formular.modellFehlt' };
    if (hatKontext) {
      const k = Number(e.context_tokens);
      if (!Number.isInteger(k) || k < 2048 || k > 2000000)
        return { k: 'formular.kontextGrenzen' };
    }
    if (istLlm) {
      const n = Number(e.parallel);
      if (!Number.isInteger(n) || n < 1 || n > 32) return { k: 'formular.parallelGrenzen' };
    }
    return null;
  }

  async function speichern(ev) {
    ev?.preventDefault();
    meldung = null;
    const neu = aenderungen();
    if (!Object.keys(neu).length) {
      meldung = { art: 'info', k: 'formular.keineAenderungen' };
      ongespeichert(null);
      return;
    }
    const fehler = pruefen();
    if (fehler) { meldung = { art: 'fehler', ...fehler }; return; }
    if (istEinbettung && 'model' in neu && String(basis.model ?? '').trim()
        && !confirm(t(neu.model ? 'formular.einbettFrage' : 'formular.einbettAusFrage'))) return;
    const verworfen = schluesselVerworfen;
    laeuft = 'speichern';
    const r = await api(`/api/admin/settings/${abschnitt}`, { method: 'PUT', body: neu });
    laeuft = '';
    if (!r.ok) { meldung = { art: 'fehler', text: r.fehler }; return; }
    werte = r.daten.abschnitt;
    meldung = { art: 'ok', k: verworfen ? 'formular.gespeichertVerworfen' : 'formular.gespeichert' };
    ongespeichert(r.daten);
  }

  async function schluesselEntfernen() {
    if (!confirm(t('formular.schluesselEntfernenFrage'))) return;
    laeuft = 'entfernen';
    const r = await api(`/api/admin/settings/${abschnitt}`, { method: 'PUT', body: { api_key: null } });
    laeuft = '';
    if (!r.ok) { meldung = { art: 'fehler', text: r.fehler }; return; }
    /* Nur der Schluessel ist weg — was sonst gerade im Formular steht und noch
       nicht gespeichert ist, soll dabei nicht verloren gehen. */
    const entwurf = { ...e };
    werte = r.daten.abschnitt;
    await tick();
    e = entwurf;
    meldung = { art: 'ok', k: 'formular.schluesselEntfernt' };
  }
</script>

<form class="af" onsubmit={speichern} novalidate>
  <div class="e-raster">
    <div class="e-feld">
      <label for="{uid}-anbieter">{t('formular.anbieter')}</label>
      <select id="{uid}-anbieter" value={e.provider}
              onchange={(ev) => anbieterWaehlen(ev.currentTarget.value)}>
        <option value="">{t('formular.keiner')}</option>
        {#each auswahl as a (a.id)}
          <option value={a.id}>{a.name}</option>
        {/each}
        {#if e.provider && !auswahl.some((a) => a.id === e.provider)}
          <option value={e.provider}>{e.provider}</option>
        {/if}
      </select>
    </div>

    {#if e.provider}
      <div class="e-feld">
        <label for="{uid}-url">{t('formular.adresse')}</label>
        <input id="{uid}-url" type="text" inputmode="url" autocomplete="off" autocapitalize="off"
               spellcheck="false" bind:value={e.url} placeholder={p?.url || 'https://…'}
               aria-describedby="{uid}-url-hilfe" />
        <p class="e-klein" id="{uid}-url-hilfe">
          {p?.url ? t('formular.standardAdresse') : t('formular.adressePflicht')}
        </p>
      </div>

      {#if p?.hinweis}
        <p class="e-klein e-feld breit hinweis">{p.hinweis}</p>
      {/if}

      {#if brauchtSchluesselFeld}
        <div class="e-feld breit">
          <label for="{uid}-key">
            {p.schluessel === 'optional' ? t('formular.apiSchluesselOptional') : t('formular.apiSchluessel')}
          </label>
          <input id="{uid}-key" type="password" autocomplete="new-password" spellcheck="false"
                 bind:value={schluessel} aria-describedby="{uid}-key-stand"
                 placeholder={basis.api_key_gesetzt && !umgezogen
                   ? t('formular.leerBehalten')
                   : p.schluessel === 'pflicht' ? t('formular.schluesselEintragen') : t('formular.nurFallsVerlangt')} />
          <div class="schluessel-stand" id="{uid}-key-stand">
            {#if basis.api_key_gesetzt}
              <span class="e-gesetzt">✓&#xFE0E; {t('formular.gesetzt')}{basis.api_key_ende ? ` (…${basis.api_key_ende})` : ''}</span>
              <button type="button" class="e-knopf leise gefahr" onclick={schluesselEntfernen}
                      disabled={!!laeuft}>
                {laeuft === 'entfernen' ? t('formular.entferne') : t('formular.schluesselEntfernen')}
              </button>
            {:else}
              <span class="e-gesetzt leer">{t('formular.keinSchluessel')}</span>
            {/if}
          </div>
        </div>
      {/if}

      {#if schluesselVerworfen}
        <p class="e-warnung e-feld breit">
          {anbieterNeu ? t('formular.verwirftAnbieter') : t('formular.verwirftAdresse')}
          {#if brauchtSchluesselFeld}{t('formular.neuerSchluesselOben')}{/if}
        </p>
      {/if}

      <div class="e-feld breit">
        <label for="{uid}-model">{t('formular.modell')}</label>
        <div class="modell-zeile">
          <input id="{uid}-model" type="text" list="{uid}-liste" autocomplete="off"
                 autocapitalize="off" spellcheck="false" bind:value={e.model}
                 placeholder={t('formular.modellName')} aria-describedby="{uid}-modelle-info" />
          <button type="button" class="e-knopf" class:laeuft={laeuft === 'modelle'}
                  onclick={modelleLaden} disabled={!!laeuft}>
            {laeuft === 'modelle' ? t('allgemein.laden') : t('formular.modelleLaden')}
          </button>
        </div>
        <p class="e-meldung {modelleInfo?.art ?? ''}" id="{uid}-modelle-info" aria-live="polite">{txt(modelleInfo)}</p>
      </div>

      {#if istLlm}
        <div class="e-feld">
          <label for="{uid}-fast">{t('formular.schnellesModell')}</label>
          <input id="{uid}-fast" type="text" list="{uid}-liste" autocomplete="off"
                 autocapitalize="off" spellcheck="false" bind:value={e.fast_model}
                 aria-describedby="{uid}-fast-hilfe" />
          <p class="e-klein" id="{uid}-fast-hilfe">{t('formular.schnellesModellHilfe')}</p>
        </div>
        <div class="e-feld">
          <label for="{uid}-parallel">{t('formular.parallel')}</label>
          <input id="{uid}-parallel" type="number" inputmode="numeric" min="1" max="32" step="1"
                 bind:value={e.parallel} aria-describedby="{uid}-parallel-hilfe" />
          <p class="e-klein" id="{uid}-parallel-hilfe">{t('formular.parallelHilfe')}</p>
        </div>
      {/if}

      {#if hatKontext}
        <div class="e-feld">
          <label for="{uid}-kontext">{t('formular.kontext')}</label>
          <input id="{uid}-kontext" type="number" inputmode="numeric" min="2048" max="2000000" step="1"
                 bind:value={e.context_tokens} aria-describedby="{uid}-kontext-hilfe" />
          <p class="e-klein" id="{uid}-kontext-hilfe">
            {istLlm ? t('formular.kontextHilfeLlm') : t('formular.kontextHilfe')}
          </p>
        </div>
      {/if}
    {/if}
  </div>

  <datalist id="{uid}-liste">
    {#each modelle as m (m)}<option value={m}></option>{/each}
  </datalist>

  <div class="e-knoepfe">
    <button type="button" class="e-knopf" class:laeuft={laeuft === 'test'}
            onclick={testen} disabled={!!laeuft || !e.provider}>
      {laeuft === 'test' ? t('formular.teste') : t('formular.verbindungTesten')}
    </button>
    <button type="submit" class="e-knopf primaer" class:laeuft={laeuft === 'speichern'}
            disabled={!!laeuft}>
      {laeuft === 'speichern' ? t('formular.speichere') : (knopfText || t('allgemein.speichern'))}
    </button>
  </div>
  <div aria-live="polite">
    {#if test}
      <p class="e-meldung {test.art}">{txt(test)}</p>
      {#if test.schnell}<p class="e-meldung {test.schnell.art}">{txt(test.schnell)}</p>{/if}
    {/if}
  </div>
  <p class="e-meldung {meldung?.art ?? ''}" aria-live="polite">{txt(meldung)}</p>
</form>

<style>
  .af { min-width: 0; }
  .hinweis { margin-top: -6px; }
  .modell-zeile {
    display: flex;
    gap: var(--s-2);
    flex-wrap: wrap;
  }
  .modell-zeile input { flex: 1 1 220px; min-width: 0; }
  .modell-zeile .e-knopf { flex: 0 0 auto; min-height: 44px; }
  .schluessel-stand {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: var(--s-2);
    min-height: 32px;
  }
  @media (max-width: 560px) {
    .modell-zeile .e-knopf { flex: 1 1 100%; }
  }
</style>
