<script>
  /* Alle Einstellungen, die frueher in der config.yaml standen. Jede Karte
     speichert fuer sich und schickt nur geaenderte Felder — so ueberschreibt
     ein Klick bei TMDB nie versehentlich etwas beim Sprachmodell, und ein
     leer gelassenes Schluesselfeld bedeutet ueberall "behalten".
     Der Zeitplan bleibt auf /admin bei den Jobs, weil man ihn dort zusammen
     mit deren Laufzeiten sieht. */
  import { onMount, onDestroy } from 'svelte';
  import { goto } from '$app/navigation';
  import { api, adresse, sekunden, zahl, zeitpunkt, appInfoVergessen } from '$lib/api.js';
  import AdminNav from '$lib/components/AdminNav.svelte';
  import AnbieterFormular from '$lib/components/AnbieterFormular.svelte';
  import JellyfinFormular from '$lib/components/JellyfinFormular.svelte';
  import { t, SPRACHEN, vorgabeUebernehmen } from '$lib/i18n.svelte.js';
  import '$lib/formular.css';

  // $derived, damit die Namen beim Sprachwechsel mitgehen.
  const KARTEN = $derived([
    { id: 'jellyfin', name: t('einstellungen.kJellyfin') },
    { id: 'sprachmodell', name: t('einstellungen.kSprachmodell') },
    { id: 'rueckfall', name: t('einstellungen.kRueckfall') },
    { id: 'embeddings', name: t('einstellungen.kEmbeddings') },
    { id: 'tmdb', name: t('einstellungen.kTmdb') },
    { id: 'seerr', name: t('einstellungen.kSeerr') },
    { id: 'allgemein', name: t('einstellungen.kAllgemein') },
  ]);

  let einstellungen = $state(null);
  let anbieter = $state([]);
  let ladeFehler = $state('');
  let weg = false;

  const URL_MUSTER = /^https?:\/\/\S+/i;

  onMount(async () => {
    const r = await api('/api/admin/settings');
    if (weg) return;
    if (!r.ok) {
      if (r.status === 401 || r.status === 403) { goto('/'); return; }
      ladeFehler = r.fehler;
      return;
    }
    einstellungen = r.daten?.einstellungen ?? null;
    anbieter = r.daten?.anbieter ?? [];
    entwuerfe();
    einbettungLaden();
  });

  onDestroy(() => { weg = true; clearTimeout(ebTimer); });

  function anbieterName(id) {
    return anbieter.find((a) => a.id === id)?.name ?? id ?? '';
  }

  // ------------------------------------------------------- einfache Karten
  let tmdbToken = $state('');
  let tmdbTest = $state(null);
  let tmdbMeldung = $state(null);
  let tmdbLaeuft = $state('');

  let seerrUrl = $state('');
  let seerrMeldung = $state(null);
  let seerrLaeuft = $state(false);

  let allg = $state({ session_lifetime_days: 30, media_root: '/media', language: 'de' });
  let allgMeldung = $state(null);
  let allgLaeuft = $state(false);

  function entwuerfe() {
    tmdbToken = '';
    seerrUrl = einstellungen?.seerr?.url ?? '';
    allg = {
      session_lifetime_days: einstellungen?.general?.session_lifetime_days ?? 30,
      media_root: einstellungen?.general?.media_root ?? '/media',
      language: einstellungen?.general?.language ?? 'de',
    };
  }

  /** PUT eines Abschnitts; legt die Antwort in `einstellungen` ab. */
  async function abschnittSpeichern(abschnitt, body) {
    const r = await api(`/api/admin/settings/${abschnitt}`, { method: 'PUT', body });
    if (r.ok && r.daten?.abschnitt) einstellungen[abschnitt] = r.daten.abschnitt;
    return r;
  }

  async function tmdbTesten() {
    const bearer = tmdbToken.trim();
    if (!bearer && !einstellungen?.tmdb?.bearer_gesetzt) {
      tmdbTest = { art: 'fehler', text: t('einstellungen.tokenZuerst') };
      return;
    }
    tmdbLaeuft = 'test';
    tmdbTest = { art: 'info', text: t('formular.teste') };
    const r = await api('/api/admin/settings/testen', { method: 'POST', body: { abschnitt: 'tmdb', bearer } });
    tmdbLaeuft = '';
    if (!r.ok) { tmdbTest = { art: 'fehler', text: r.fehler }; return; }
    const d = r.daten ?? {};
    tmdbTest = d.ok
      ? { art: 'ok', text: t('einstellungen.tokenGueltig', { zeit: d.ms != null ? sekunden(d.ms) : '' }) }
      : { art: 'fehler', text: d.fehler || t('einstellungen.tokenAbgelehnt') };
  }

  async function tmdbSpeichern(ev) {
    ev.preventDefault();
    const bearer = tmdbToken.trim();
    if (!bearer) { tmdbMeldung = { art: 'info', text: t('formular.keineAenderungen') }; return; }
    tmdbLaeuft = 'speichern';
    const r = await abschnittSpeichern('tmdb', { bearer });
    tmdbLaeuft = '';
    if (!r.ok) { tmdbMeldung = { art: 'fehler', text: r.fehler }; return; }
    tmdbToken = '';
    tmdbMeldung = { art: 'ok', text: t('formular.gespeichert') };
  }

  async function tmdbEntfernen() {
    if (!confirm(t('einstellungen.tokenEntfernenFrage'))) return;
    tmdbLaeuft = 'entfernen';
    const r = await abschnittSpeichern('tmdb', { bearer: null });
    tmdbLaeuft = '';
    tmdbMeldung = r.ok ? { art: 'ok', text: t('einstellungen.tokenEntfernt') } : { art: 'fehler', text: r.fehler };
  }

  async function seerrSpeichern(ev) {
    ev.preventDefault();
    const url = adresse(seerrUrl);
    if (url && !URL_MUSTER.test(url)) {
      seerrMeldung = { art: 'fehler', text: t('formular.adresseSchema') };
      return;
    }
    if (url === adresse(einstellungen.seerr?.url)) { seerrMeldung = { art: 'info', text: t('formular.keineAenderungen') }; return; }
    seerrLaeuft = true;
    const r = await abschnittSpeichern('seerr', { url });
    seerrLaeuft = false;
    if (!r.ok) { seerrMeldung = { art: 'fehler', text: r.fehler }; return; }
    seerrUrl = einstellungen.seerr?.url ?? '';
    // Die Filmliste holt die Seerr-Adresse aus /api/app-info und merkt sie sich.
    appInfoVergessen();
    seerrMeldung = { art: 'ok', text: url ? t('formular.gespeichert') : t('einstellungen.seerrAusgeblendet') };
  }

  async function allgSpeichern(ev) {
    ev.preventDefault();
    const tage = Number(allg.session_lifetime_days);
    if (!Number.isInteger(tage) || tage < 1 || tage > 365) {
      allgMeldung = { art: 'fehler', text: t('einstellungen.sitzungsdauerGrenzen') };
      return;
    }
    const pfad = String(allg.media_root ?? '').trim();
    if (!pfad.startsWith('/')) {
      allgMeldung = { art: 'fehler', text: t('einstellungen.medienpfadFehler') };
      return;
    }
    const neu = {};
    if (tage !== einstellungen.general?.session_lifetime_days) neu.session_lifetime_days = tage;
    if (pfad !== einstellungen.general?.media_root) neu.media_root = pfad;
    if (allg.language !== (einstellungen.general?.language ?? 'de')) neu.language = allg.language;
    if (!Object.keys(neu).length) { allgMeldung = { art: 'info', text: t('formular.keineAenderungen') }; return; }
    allgLaeuft = true;
    const r = await abschnittSpeichern('general', neu);
    allgLaeuft = false;
    allgMeldung = r.ok ? { art: 'ok', text: t('formular.gespeichert') } : { art: 'fehler', text: r.fehler };
    /* Die Vorgabe gilt sofort auch hier, falls dieser Browser noch keine
       eigene Sprache gewaehlt hat (sonst bleibt die eigene Wahl). */
    if (r.ok && neu.language) vorgabeUebernehmen(neu.language);
  }

  // ------------------------------------------------------------- Embeddings
  let einbettung = $state(null);
  let ebMeldung = $state(null);
  let ebLaeuft = $state('');          // '' | 'fehlend' | 'neu' | 'kalibrieren'
  let ebTimer = null;

  async function einbettungLaden() {
    clearTimeout(ebTimer);
    const r = await api('/api/admin/einbettung');
    if (weg) return;
    if (r.ok) einbettung = r.daten;
    else ebMeldung = { art: 'fehler', text: t('einstellungen.statusNichtAbrufbar', { fehler: r.fehler }) };
    // Waehrend eines Laufs alle 3 s nachsehen, sonst gar nicht.
    if (einbettung?.laeuft) ebTimer = setTimeout(einbettungLaden, 3000);
  }

  /* Nach dem Speichern: bei einem Modellwechsel startet das Backend die
     Einbettung im Hintergrund. Die Antwort kann noch vor dem Start kommen,
     deshalb kurz danach noch einmal nachsehen. */
  function einbettungGespeichert(d) {
    if (!d) return;
    if (d.einbettung) einbettung = d.einbettung;
    clearTimeout(ebTimer);
    ebTimer = setTimeout(einbettungLaden, 1500);
  }

  async function einbettungStarten(force) {
    if (force && !confirm(t('einstellungen.neuEinbettenFrage'))) return;
    ebLaeuft = force ? 'neu' : 'fehlend';
    ebMeldung = null;
    const r = await api(`/api/admin/einbettung/starten?force=${force ? 'true' : 'false'}`, { method: 'POST' });
    ebLaeuft = '';
    if (!r.ok) { ebMeldung = { art: 'fehler', text: r.fehler }; return; }
    ebMeldung = r.daten?.status === 'laeuft_bereits'
      ? { art: 'info', text: t('einstellungen.einbettungLaeuftSchon') }
      : { art: 'ok', text: force ? t('einstellungen.neuEingebettetGestartet')
                                 : t('einstellungen.fehlendeWerdenEingebettet') };
    einbettungLaden();
  }

  async function kalibrieren() {
    ebLaeuft = 'kalibrieren';
    ebMeldung = { art: 'info', text: t('einstellungen.messeSkala') };
    const r = await api('/api/admin/einbettung/kalibrieren', { method: 'POST' });
    ebLaeuft = '';
    if (!r.ok) { ebMeldung = { art: 'fehler', text: r.fehler }; return; }
    if (einbettung && r.daten?.kalibrierung) einbettung.kalibrierung = r.daten.kalibrierung;
    ebMeldung = { art: 'ok', text: t('einstellungen.skalaGemessen') };
  }

  const skala = $derived(einbettung?.kalibrierung ?? null);
  const fortschritt = $derived(einbettung?.laeuft ? einbettung.fortschritt : null);

  function quelleText(k) {
    if (!k) return '—';
    if (k.quelle === 'gemessen') {
      return k.gemessen_am
        ? t('einstellungen.quelleGemessenAm', { zeit: zeitpunkt(k.gemessen_am) })
        : t('einstellungen.quelleGemessen');
    }
    if (k.quelle === 'voreingestellt') return t('einstellungen.quelleVoreingestellt');
    if (k.quelle === 'vorlaeufig') return t('einstellungen.quelleVorlaeufig');
    return k.quelle ?? '—';
  }

  function restText(min) {
    if (min == null) return '';
    if (min < 1) return t('einstellungen.restMinute');
    if (min < 90) return t('einstellungen.restMinuten', { n: zahl(Math.ceil(min)) });
    return t('einstellungen.restStunden', { n: zahl(min / 60, 1) });
  }
</script>

<svelte:head><title>{t('einstellungen.titel')}</title></svelte:head>

<div class="einst" id="inhalt">
  <h1 class="sr-only">{t('einstellungen.seitenueberschrift')}</h1>
  <AdminNav />

  {#if ladeFehler}
    <p class="e-meldung fehler" role="alert">{t('einstellungen.ladeFehler', { fehler: ladeFehler })}</p>
  {:else if !einstellungen}
    <p class="e-text" aria-live="polite">{t('allgemein.laden')}</p>
  {:else}
    <div class="raster">
      <nav class="sprung" aria-label={t('einstellungen.abschnitte')}>
        <ul>
          {#each KARTEN as k (k.id)}
            <li><a href="#{k.id}">{k.name}</a></li>
          {/each}
        </ul>
      </nav>

      <div class="karten">
        <section class="e-karte" id="jellyfin" aria-labelledby="t-jellyfin">
          <h2 id="t-jellyfin">{t('einstellungen.kJellyfin')}</h2>
          <p class="e-text">{t('einstellungen.jellyfinText')}</p>
          <JellyfinFormular bind:werte={einstellungen.jellyfin} />
        </section>

        <section class="e-karte" id="sprachmodell" aria-labelledby="t-llm">
          <h2 id="t-llm">{t('einstellungen.kSprachmodell')}</h2>
          <p class="e-text">{t('einstellungen.sprachmodellText')}</p>
          <AnbieterFormular abschnitt="llm" bind:werte={einstellungen.llm} {anbieter} />
        </section>

        <section class="e-karte" id="rueckfall" aria-labelledby="t-rueckfall">
          <h2 id="t-rueckfall">{t('einstellungen.kRueckfall')}</h2>
          <p class="e-text">{t('einstellungen.rueckfallText')}</p>
          <AnbieterFormular abschnitt="llm_fallback" bind:werte={einstellungen.llm_fallback} {anbieter} />
        </section>

        <section class="e-karte" id="embeddings" aria-labelledby="t-embedding">
          <h2 id="t-embedding">{t('einstellungen.kEmbeddings')}</h2>
          <p class="e-text">{t('einstellungen.embeddingsText')}</p>

          <div class="status">
            <h3 id="t-eb-status">{t('einstellungen.stand')}</h3>
            {#if einbettung}
              <dl class="e-fakten">
                <div>
                  <dt>{t('einstellungen.modell')}</dt>
                  <dd>
                    {#if einbettung.modell}
                      {einbettung.modell}{einbettung.anbieter ? ` · ${anbieterName(einbettung.anbieter)}` : ''}
                    {:else}
                      {t('einstellungen.nichtEingerichtet')}
                    {/if}
                  </dd>
                </div>
                <div><dt>{t('einstellungen.sammlung')}</dt><dd>{einbettung.sammlung || '—'}</dd></div>
                <div><dt>{t('einstellungen.vektoren')}</dt><dd>{zahl(einbettung.vektoren)}</dd></div>
                <div>
                  <dt>{t('einstellungen.eingebettet')}</dt>
                  <dd>{t('einstellungen.eingebettetVon', {
                    fertig: zahl(einbettung.eingebettet), gesamt: zahl(einbettung.eintraege_gesamt) })}</dd>
                </div>
                <div><dt>{t('einstellungen.fehlend')}</dt><dd class:warn={einbettung.fehlend > 0}>{zahl(einbettung.fehlend)}</dd></div>
                <div>
                  <dt>{t('einstellungen.letzterLauf')}</dt>
                  <dd>
                    {#if einbettung.letzter_lauf}
                      {zeitpunkt(einbettung.letzter_lauf.beendet_am)}<br />
                      <span class="e-klein">
                        {t('einstellungen.laufErgebnis', {
                          ok: zahl(einbettung.letzter_lauf.ok),
                          fehl: einbettung.letzter_lauf.fehlgeschlagen
                            ? zahl(einbettung.letzter_lauf.fehlgeschlagen) : '' })}
                      </span>
                    {:else}
                      —
                    {/if}
                  </dd>
                </div>
              </dl>

              {#if einbettung.laeuft}
                <div class="fortschritt">
                  <div class="zeile-text">
                    <span>
                      {#if fortschritt}
                        {t('einstellungen.fortschritt', {
                          phase: fortschritt.phase, fertig: zahl(fortschritt.fertig),
                          gesamt: zahl(fortschritt.gesamt) })}
                      {:else}
                        {t('einstellungen.einbettungLaeuft')}
                      {/if}
                    </span>
                    {#if fortschritt?.rest_min != null}
                      <span class="e-klein">{restText(fortschritt.rest_min)}</span>
                    {/if}
                  </div>
                  <progress class="e-balken" max={fortschritt?.gesamt || 1} value={fortschritt?.fertig ?? 0}
                            aria-label={t('einstellungen.fortschrittLabel')}></progress>
                </div>
              {/if}

              {#if einbettung.letzter_lauf?.fehler}
                <p class="e-meldung fehler">{t('einstellungen.letzterLaufFehler', { fehler: einbettung.letzter_lauf.fehler })}</p>
              {/if}

              <div class="skala">
                <h4>{t('einstellungen.skalaTitel')}</h4>
                <p class="e-klein">{quelleText(skala)}</p>
                {#if skala}
                  <p class="werte">
                    {t('einstellungen.skalaPassung', {
                      von: zahl(skala.match_floor, 2), bis: zahl(skala.match_ceil, 2) })}
                    <span aria-hidden="true">·</span>
                    {t('einstellungen.skalaStimmung', {
                      min: zahl(skala.mood_min, 2), von: zahl(skala.mood_floor, 2),
                      bis: zahl(skala.mood_ceil, 2) })}
                  </p>
                {/if}
                {#if skala?.quelle === 'vorlaeufig'}
                  <p class="e-klein">{t('einstellungen.skalaHinweis')}</p>
                {/if}
              </div>

              <div class="e-knoepfe">
                <button type="button" class="e-knopf" class:laeuft={ebLaeuft === 'fehlend'}
                        onclick={() => einbettungStarten(false)}
                        disabled={!!ebLaeuft || einbettung.laeuft || !einbettung.eingerichtet}>
                  {ebLaeuft === 'fehlend' ? t('einstellungen.starte') : t('einstellungen.fehlendeEinbetten')}
                </button>
                <button type="button" class="e-knopf" class:laeuft={ebLaeuft === 'neu'}
                        onclick={() => einbettungStarten(true)}
                        disabled={!!ebLaeuft || einbettung.laeuft || !einbettung.eingerichtet}>
                  {ebLaeuft === 'neu' ? t('einstellungen.starte') : t('einstellungen.allesNeuEinbetten')}
                </button>
                <button type="button" class="e-knopf" class:laeuft={ebLaeuft === 'kalibrieren'}
                        onclick={kalibrieren}
                        disabled={!!ebLaeuft || einbettung.laeuft || !einbettung.eingerichtet}>
                  {ebLaeuft === 'kalibrieren' ? t('einstellungen.messe') : t('einstellungen.skalaNeuMessen')}
                </button>
              </div>
            {/if}
            <p class="e-meldung {ebMeldung?.art ?? ''}" aria-live="polite">{ebMeldung?.text ?? ''}</p>
          </div>

          <h3 class="unter-titel">{t('einstellungen.anbieterUndModell')}</h3>
          <AnbieterFormular abschnitt="embedding" bind:werte={einstellungen.embedding} {anbieter}
                            ongespeichert={einbettungGespeichert} />
        </section>

        <section class="e-karte" id="tmdb" aria-labelledby="t-tmdb">
          <h2 id="t-tmdb">{t('einstellungen.kTmdb')}</h2>
          <p class="e-text">{t('einstellungen.tmdbText')}</p>
          <form onsubmit={tmdbSpeichern} novalidate>
            <div class="e-raster">
              <div class="e-feld breit">
                <label for="tmdb-token">{t('einstellungen.token')}</label>
                <input id="tmdb-token" type="password" autocomplete="off" spellcheck="false"
                       bind:value={tmdbToken} aria-describedby="tmdb-stand"
                       placeholder={einstellungen.tmdb?.bearer_gesetzt ? t('formular.leerBehalten') : 'eyJhbGciOi…'} />
                <div class="stand" id="tmdb-stand">
                  {#if einstellungen.tmdb?.bearer_gesetzt}
                    <span class="e-gesetzt">✓&#xFE0E; {t('formular.gesetzt')}{einstellungen.tmdb.bearer_ende ? ` (…${einstellungen.tmdb.bearer_ende})` : ''}</span>
                    <button type="button" class="e-knopf leise gefahr" onclick={tmdbEntfernen} disabled={!!tmdbLaeuft}>
                      {tmdbLaeuft === 'entfernen' ? t('formular.entferne') : t('einstellungen.tokenEntfernen')}
                    </button>
                  {:else}
                    <span class="e-gesetzt leer">{t('einstellungen.keinToken')}</span>
                  {/if}
                </div>
              </div>
            </div>
            <div class="e-knoepfe">
              <button type="button" class="e-knopf" class:laeuft={tmdbLaeuft === 'test'} onclick={tmdbTesten}
                      disabled={!!tmdbLaeuft}>
                {tmdbLaeuft === 'test' ? t('formular.teste') : t('formular.verbindungTesten')}
              </button>
              <button type="submit" class="e-knopf primaer" class:laeuft={tmdbLaeuft === 'speichern'}
                      disabled={!!tmdbLaeuft}>
                {tmdbLaeuft === 'speichern' ? t('formular.speichere') : t('allgemein.speichern')}
              </button>
            </div>
            <p class="e-meldung {tmdbTest?.art ?? ''}" aria-live="polite">{tmdbTest?.text ?? ''}</p>
            <p class="e-meldung {tmdbMeldung?.art ?? ''}" aria-live="polite">{tmdbMeldung?.text ?? ''}</p>
          </form>
        </section>

        <section class="e-karte" id="seerr" aria-labelledby="t-seerr">
          <h2 id="t-seerr">{t('einstellungen.kSeerr')}</h2>
          <p class="e-text">{t('einstellungen.seerrText')}</p>
          <form onsubmit={seerrSpeichern} novalidate>
            <div class="e-raster">
              <div class="e-feld breit">
                <label for="seerr-url">{t('formular.adresse')}</label>
                <input id="seerr-url" type="text" inputmode="url" autocomplete="off" autocapitalize="off"
                       spellcheck="false" placeholder={t('einstellungen.seerrBeispiel')} bind:value={seerrUrl}
                       aria-describedby="seerr-hilfe" />
                <p class="e-klein" id="seerr-hilfe">{t('einstellungen.seerrHilfe')}</p>
              </div>
            </div>
            <div class="e-knoepfe">
              <button type="submit" class="e-knopf primaer" class:laeuft={seerrLaeuft} disabled={seerrLaeuft}>
                {seerrLaeuft ? t('formular.speichere') : t('allgemein.speichern')}
              </button>
            </div>
            <p class="e-meldung {seerrMeldung?.art ?? ''}" aria-live="polite">{seerrMeldung?.text ?? ''}</p>
          </form>
        </section>

        <section class="e-karte" id="allgemein" aria-labelledby="t-allgemein">
          <h2 id="t-allgemein">{t('einstellungen.kAllgemein')}</h2>
          <form onsubmit={allgSpeichern} novalidate>
            <div class="e-raster">
              <div class="e-feld">
                <label for="allg-tage">{t('einstellungen.sitzungsdauer')}</label>
                <input id="allg-tage" type="number" inputmode="numeric" min="1" max="365" step="1"
                       bind:value={allg.session_lifetime_days} aria-describedby="allg-tage-hilfe" />
                <p class="e-klein" id="allg-tage-hilfe">{t('einstellungen.sitzungsdauerHilfe')}</p>
              </div>
              <div class="e-feld">
                <label for="allg-medien">{t('einstellungen.medienpfad')}</label>
                <input id="allg-medien" type="text" autocomplete="off" autocapitalize="off" spellcheck="false"
                       bind:value={allg.media_root} aria-describedby="allg-medien-hilfe" />
                <p class="e-klein" id="allg-medien-hilfe">{t('einstellungen.medienpfadHilfe')}</p>
              </div>
              <div class="e-feld">
                <label for="allg-sprache">{t('einstellungen.spracheVorgabe')}</label>
                <select id="allg-sprache" bind:value={allg.language} aria-describedby="allg-sprache-hilfe">
                  {#each SPRACHEN as s (s.code)}
                    <option value={s.code}>{s.name}</option>
                  {/each}
                </select>
                <p class="e-klein" id="allg-sprache-hilfe">{t('einstellungen.spracheVorgabeHilfe')}</p>
              </div>
            </div>
            <div class="e-knoepfe">
              <button type="submit" class="e-knopf primaer" class:laeuft={allgLaeuft} disabled={allgLaeuft}>
                {allgLaeuft ? t('formular.speichere') : t('allgemein.speichern')}
              </button>
            </div>
            <p class="e-meldung {allgMeldung?.art ?? ''}" aria-live="polite">{allgMeldung?.text ?? ''}</p>
          </form>
          <p class="e-klein hinweis-plan">
            {t('einstellungen.zeitplanVor')}
            <a href="/admin">{t('einstellungen.benutzerJobs')}</a>{t('einstellungen.zeitplanNach')}
          </p>
        </section>
      </div>
    </div>
  {/if}
</div>

<style>
  .einst {
    padding: 24px 28px var(--s-8);
    max-width: 1240px;
    box-sizing: border-box;
  }

  .raster { display: block; }
  .sprung { display: none; }
  .karten {
    display: flex;
    flex-direction: column;
    gap: var(--s-5);
    min-width: 0;
  }
  /* Sprungziele nicht unter den oberen Rand rutschen lassen. */
  .karten > section { scroll-margin-top: var(--s-4); }

  /* Ab 1100px: links eine Sprungliste, die beim Scrollen stehen bleibt —
     sieben Karten untereinander sind sonst eine lange Suche. Darunter
     fehlt der Platz dafuer, und die Karten stehen einfach untereinander. */
  @media (min-width: 1100px) {
    .raster {
      display: grid;
      grid-template-columns: 180px minmax(0, 1fr);
      gap: var(--s-6);
      align-items: start;
    }
    .sprung {
      display: block;
      position: sticky;
      top: var(--s-4);
    }
    .sprung ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
    .sprung a {
      display: block;
      padding: 8px 12px;
      border-left: 2px solid var(--border);
      color: var(--text-muted);
      text-decoration: none;
      font-size: 14px;
      transition: color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease);
    }
    .sprung a:hover { color: var(--accent); border-left-color: var(--accent); }
  }

  .status {
    background: var(--layer-1);
    border: 1px solid var(--border);
    border-radius: var(--r-md);
    padding: var(--s-4);
    margin-bottom: var(--s-5);
  }
  .status h3,
  .unter-titel {
    font-family: var(--font-display);
    font-size: 20px;
    margin: 0 0 var(--s-3);
    color: var(--text);
  }
  .status .e-fakten { margin-bottom: var(--s-3); }
  dd.warn { color: var(--accent); }
  .status .e-knoepfe { margin-top: var(--s-3); }

  .fortschritt { margin: var(--s-2) 0 var(--s-3); }
  .zeile-text {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: baseline;
    gap: var(--s-1) var(--s-3);
    margin-bottom: var(--s-2);
    font-size: 14px;
    color: var(--text);
  }

  .skala {
    border-top: 1px solid var(--border);
    padding-top: var(--s-3);
    margin-top: var(--s-3);
  }
  .skala h4 {
    margin: 0 0 2px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-dim);
  }
  .skala .werte { margin: var(--s-1) 0 var(--s-1); font-size: 14px; color: var(--text); }

  .stand {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: var(--s-2);
    min-height: 32px;
  }
  .hinweis-plan { margin-top: var(--s-4); }

  @media (max-width: 720px) {
    .einst { padding: 16px 14px var(--s-7); }
    .status { padding: var(--s-3); }
  }
</style>
