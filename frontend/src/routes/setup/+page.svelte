<script>
  /* Einrichtungsassistent. Ein frischer Container kennt weder Jellyfin noch
     ein Sprachmodell; bis Oktober 2026 stand all das in einer config.yaml auf
     dem Server. Jetzt fuehrt diese Seite in fuenf Schritten durch:

       1. Jellyfin  — Adresse pruefen, dann als Jellyfin-Admin anmelden. Das
                      Backend legt dabei selbst einen API-Schluessel an und
                      macht dich zum ersten Admin von CinematekAI.
       2. Sprachmodell, 3. Embeddings — dasselbe Formular wie unter
                      Admin → Einstellungen; beides darf man ueberspringen.
       4. Extras    — TMDB, Seerr, Zeitplan, Rueckfallmodell.
       5. Fertig    — erster Lauf ueber die Bibliothek, mit Fortschritt.

     Die Seite zeigt keine Navigationsleiste (siehe +layout.svelte): nach
     Schritt 1 ist man angemeldet, die Links fuehrten aber auf leere Seiten
     und aus dem Assistenten heraus. Nach Schritt 1 gilt CinematekAI als
     eingerichtet; deshalb leitet hier nichts automatisch weg. */
  import { onMount, onDestroy, tick } from 'svelte';
  import { user, fetchMe } from '$lib/auth.svelte.js';
  import { api, adresse, zeitzonen, appInfoVergessen } from '$lib/api.js';
  import { t, zahl, datum, sprache, sprachWaehlen } from '$lib/i18n.svelte.js';
  import AnbieterFormular from '$lib/components/AnbieterFormular.svelte';
  import JellyfinFormular from '$lib/components/JellyfinFormular.svelte';
  import SprachWahl from '$lib/components/SprachWahl.svelte';
  import '$lib/formular.css';

  // Beschriftungen als $derived, damit sie beim Umschalten der Sprache mitgehen.
  const SCHRITTE = $derived([
    t('setup.schrittJellyfin'), t('setup.schrittSprachmodell'), t('setup.schrittEmbeddings'),
    t('setup.schrittExtras'), t('setup.schrittFertig'),
  ]);
  const TITEL = $derived([
    t('setup.titelJellyfin'), t('setup.titelSprachmodell'), t('setup.titelEmbeddings'),
    t('setup.titelExtras'), t('setup.titelFertig'),
  ]);

  let phase = $state('laden');        // laden | fehler | gesperrt | assistent
  let ladeFehler = $state('');
  let status = $state(null);           // /api/setup/status
  let schritt = $state(1);
  let erreicht = $state(1);            // weitester Schritt, bis zu dem man springen darf
  let erledigt = $state({});           // {nr: 'ok' | 'uebersprungen'}
  let einstellungen = $state(null);    // maskiert, aus /api/admin/settings
  let anbieter = $state([]);
  let einstellungenFehler = $state('');
  let ansage = $state(null);
  let ueberschrift = $state(null);

  /* Die Sprache beim Betreten der Seite (das Layout rendert sie erst nach der
     Abfrage der Vorgabe). Wer im Assistenten umschaltet, meint es: sobald
     Schritt 1 den ersten Admin angelegt hat, wird die Wahl bei ihm gespeichert,
     genau wie auf der Anmeldeseite. */
  const startSprache = sprache.code;

  const pad = (n) => String(n ?? 0).padStart(2, '0');
  const sek = (ms) => `${zahl(Number(ms) / 1000, 1)} s`;

  /* Meldungen und Ansagen merken den Schluessel statt des fertigen Texts:
     so wechseln sie beim Umschalten der Sprache mit. Texte vom Backend
     (r.fehler, d.fehler) stehen fertig in `text`. Werte duerfen Funktionen
     sein; sie werden erst beim Anzeigen ausgewertet (Zahlen, Zeiten). */
  const mel = (art, schluessel, werte) => ({ art, schluessel, werte });
  const roh = (art, text) => ({ art, text });
  const an = (schluessel, werte) => mel('', schluessel, werte);

  function zeige(m) {
    if (!m) return '';
    if (!m.schluessel) return m.text ?? '';
    const werte = m.werte && Object.fromEntries(
      Object.entries(m.werte).map(([k, v]) => [k, typeof v === 'function' ? v() : v]));
    return t(m.schluessel, werte);
  }

  /* Ohne Schema ("192.168.1.10:8096") nimmt das Backend die Adresse nicht
     an. Fast jeder Jellyfin im Heimnetz laeuft ohne TLS, also http://. */
  function mitSchema(u) {
    const a = adresse(u);
    return a && !/^https?:\/\//i.test(a) ? `http://${a}` : a;
  }

  async function statusLaden() {
    const r = await api('/api/setup/status');
    if (r.ok) status = r.daten;
    return r;
  }

  async function einstellungenLaden() {
    einstellungenFehler = '';
    const r = await api('/api/admin/settings');
    if (!r.ok) { einstellungenFehler = r.fehler; return false; }
    einstellungen = r.daten?.einstellungen ?? null;
    anbieter = r.daten?.anbieter ?? [];
    extrasVorbelegen();
    return true;
  }

  async function gehe(nr, meldung = null) {
    schritt = nr;
    erreicht = Math.max(erreicht, nr);
    ansage = meldung;
    if (nr === 5) fertigBetreten();
    await tick();
    /* Fokus auf die neue Ueberschrift: wer mit Tastatur oder Screenreader
       unterwegs ist, stuende sonst auf einem Knopf, den es nicht mehr gibt. */
    if (typeof window !== 'undefined') window.scrollTo({ top: 0 });
    ueberschrift?.focus({ preventScroll: true });
  }

  function springbar(nr) {
    return phase === 'assistent' && erledigt[1] === 'ok' && nr >= 2 && nr <= erreicht && nr !== schritt;
  }

  onMount(async () => {
    const r = await statusLaden();
    if (!r.ok) { ladeFehler = r.fehler; phase = 'fehler'; return; }
    if (status.eingerichtet) {
      if (user.loggedIn && user.isAdmin) {
        erledigt[1] = 'ok';
        phase = 'assistent';
        await einstellungenLaden();
        await gehe(2, an('setup.ansageSchonVerbunden'));
      } else {
        phase = 'gesperrt';
      }
      return;
    }
    /* Es gibt schon Nutzer, aber keine Jellyfin-Verbindung (z. B. nach einem
       Umzug des Servers). Die Erst-Anmeldung ist dann gesperrt; ein Admin mit
       noch gueltiger Sitzung traegt die Verbindung ueber die Einstellungen ein. */
    if (status.nutzer) {
      if (user.loggedIn && user.isAdmin) {
        await einstellungenLaden();
        phase = 'assistent';
      } else {
        phase = 'gesperrt';
      }
      return;
    }
    phase = 'assistent';
  });

  // ---------------------------------------------------------------- Schritt 1
  let jf = $state({ url: '', username: '', password: '', public_url: '', api_key: '' });
  let jfServer = $state(null);          // { url, server_name, version }
  let jfMeldung = $state(null);
  let jfLaeuft = $state('');            // '' | 'pruefen' | 'verbinden'
  let nameFeld = $state(null);
  const jfGeprueft = $derived(!!jfServer && jfServer.url === mitSchema(jf.url));

  async function jfPruefen(ev) {
    ev?.preventDefault();
    const url = mitSchema(jf.url);
    if (!url) { jfMeldung = mel('fehler', 'setup.fehltAdresse'); return; }
    jf.url = url;
    jfLaeuft = 'pruefen';
    jfMeldung = mel('info', 'setup.pruefe');
    const r = await api('/api/setup/jellyfin/pruefen', { method: 'POST', body: { url } });
    jfLaeuft = '';
    if (!r.ok) { jfServer = null; jfMeldung = roh('fehler', r.fehler); return; }
    jfServer = { url, server_name: r.daten?.server_name ?? 'Jellyfin', version: r.daten?.version ?? '' };
    jfMeldung = null;
    await tick();
    nameFeld?.focus();
  }

  async function jfVerbinden(ev) {
    ev?.preventDefault();
    if (!jfGeprueft) return jfPruefen();
    if (!jf.username.trim() || !jf.password) {
      jfMeldung = mel('fehler', 'setup.fehltAnmeldung');
      return;
    }
    const oeffentlich = mitSchema(jf.public_url);
    const body = { url: jfServer.url, username: jf.username.trim(), password: jf.password };
    if (oeffentlich) body.public_url = oeffentlich;
    if (jf.api_key.trim()) body.api_key = jf.api_key.trim();
    jfLaeuft = 'verbinden';
    jfMeldung = mel('info', 'setup.verbinde');
    const r = await api('/api/setup/jellyfin', { method: 'POST', body });
    jfLaeuft = '';
    if (!r.ok) { jfMeldung = roh('fehler', r.fehler); return; }
    // Das Passwort wird nicht mehr gebraucht und soll nicht im Speicher liegen bleiben.
    jf.password = '';
    jf.api_key = '';
    jfMeldung = null;
    // Der erste Admin ist jetzt angemeldet: eine Umschaltung der Sprache gilt ab hier bei ihm.
    if (sprache.code !== startSprache) await sprachWaehlen(sprache.code, { beimNutzer: true });
    await fetchMe();
    await statusLaden();
    erledigt[1] = 'ok';
    await einstellungenLaden();
    const server = r.daten?.server_name ?? 'Jellyfin';
    const name = r.daten?.display_name;
    await gehe(2, name ? an('setup.ansageVerbundenAls', { server, name }) : an('setup.ansageVerbunden', { server }));
  }

  // Nachtrag-Fall (Nutzer da, Jellyfin fehlt): nach dem Speichern pruefen.
  async function nachtragGespeichert() {
    await statusLaden();
    if (status?.eingerichtet) {
      erledigt[1] = 'ok';
      await gehe(2, an('setup.ansageJellyfinVerbunden'));
    }
  }

  // ------------------------------------------------------------ Schritte 2, 3
  /* Ansage je Abschnitt: [gespeichert, uebersprungen] (Schluessel, keine Texte). */
  const KI_ANSAGE = {
    llm: ['setup.ansageLlmGespeichert', 'setup.ansageLlmUebersprungen'],
    embedding: ['setup.ansageEmbeddingGespeichert', 'setup.ansageEmbeddingUebersprungen'],
  };

  function eingerichtet(abschnitt) {
    const a = einstellungen?.[abschnitt];
    return !!(a?.provider && String(a?.model ?? '').trim());
  }

  function kiFertig(nr, abschnitt) {
    const ok = eingerichtet(abschnitt);
    erledigt[nr] = ok ? 'ok' : 'uebersprungen';
    gehe(nr + 1, an(KI_ANSAGE[abschnitt][ok ? 0 : 1]));
  }

  function ueberspringen(nr) {
    const abschnitt = nr === 2 ? 'llm' : 'embedding';
    erledigt[nr] = eingerichtet(abschnitt) ? 'ok' : 'uebersprungen';
    einbettungFrage = false;
    gehe(nr + 1, an(KI_ANSAGE[abschnitt][1]));
  }

  let einbettungFrage = $state(false);

  // ---------------------------------------------------------------- Schritt 4
  const ZONEN = zeitzonen();
  const browserZone = (() => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone ?? ''; } catch { return ''; }
  })();

  let extra = $state({ bearer: '', seerr: '', zeit: '10:30', zone: '', aktiv: true });
  let tmdbTest = $state(null);
  let extraMeldung = $state(null);
  let extraLaeuft = $state('');         // '' | 'tmdb' | 'speichern'

  function extrasVorbelegen() {
    const s = einstellungen?.schedule ?? {};
    extra.bearer = '';
    extra.seerr = einstellungen?.seerr?.url ?? '';
    extra.zeit = `${pad(s.hour ?? 10)}:${pad(s.minute ?? 30)}`;
    extra.zone = s.timezone || browserZone || 'Europe/Berlin';
    extra.aktiv = s.enabled ?? true;
  }

  async function tmdbTesten() {
    const bearer = extra.bearer.trim();
    if (!bearer && !einstellungen?.tmdb?.bearer_gesetzt) {
      tmdbTest = mel('fehler', 'setup.tokenZuerst');
      return;
    }
    extraLaeuft = 'tmdb';
    tmdbTest = mel('info', 'setup.teste');
    const r = await api('/api/admin/settings/testen', { method: 'POST', body: { abschnitt: 'tmdb', bearer } });
    extraLaeuft = '';
    if (!r.ok) { tmdbTest = roh('fehler', r.fehler); return; }
    const d = r.daten ?? {};
    if (d.ok) tmdbTest = mel('ok', 'setup.tokenGueltig', { zeit: () => (d.ms != null ? sek(d.ms) : '') });
    else if (d.fehler) tmdbTest = roh('fehler', d.fehler);
    else tmdbTest = mel('fehler', 'setup.tokenAbgelehnt');
  }

  async function extrasSpeichern(ev) {
    ev?.preventDefault();
    extraMeldung = null;
    if (!einstellungen) { extraMeldung = mel('fehler', 'setup.einstellungenNichtGeladen'); return; }
    /* Anders als bei Jellyfin wird hier kein http:// ergaenzt: Seerr laeuft
       oft hinter einem Proxy mit https, und ein geratenes Schema fiele erst
       beim ersten Klick auf einen Link auf. */
    const seerr = adresse(extra.seerr);
    if (seerr && !/^https?:\/\/\S+/i.test(seerr)) {
      extraMeldung = mel('fehler', 'setup.seerrSchema');
      return;
    }
    const m = /^(\d{1,2}):(\d{2})/.exec(extra.zeit || '');
    if (!m || +m[1] > 23 || +m[2] > 59) {
      extraMeldung = mel('fehler', 'setup.zeitFormat');
      return;
    }
    const zone = extra.zone.trim();
    if (!zone) { extraMeldung = mel('fehler', 'setup.zoneFehlt'); return; }

    const aufgaben = [];
    if (extra.bearer.trim()) aufgaben.push(['tmdb', { bearer: extra.bearer.trim() }]);
    if (seerr !== adresse(einstellungen.seerr?.url)) aufgaben.push(['seerr', { url: seerr }]);
    const s = einstellungen.schedule ?? {};
    const plan = {};
    if (+m[1] !== s.hour) plan.hour = +m[1];
    if (+m[2] !== s.minute) plan.minute = +m[2];
    if (extra.aktiv !== s.enabled) plan.enabled = extra.aktiv;
    if (zone !== s.timezone) plan.timezone = zone;
    if (Object.keys(plan).length) aufgaben.push(['schedule', plan]);

    extraLaeuft = 'speichern';
    for (const [abschnitt, body] of aufgaben) {
      const r = await api(`/api/admin/settings/${abschnitt}`, { method: 'PUT', body });
      if (!r.ok) {
        extraLaeuft = '';
        extraMeldung = mel('fehler', 'setup.abschnittFehler', { abschnitt, fehler: r.fehler });
        return;
      }
      einstellungen[abschnitt] = r.daten.abschnitt;
    }
    extraLaeuft = '';
    extra.bearer = '';
    extra.seerr = einstellungen.seerr?.url ?? '';
    // Seerr-Adresse steckt in /api/app-info, das die Seiten zwischenspeichern.
    appInfoVergessen();
    erledigt[4] = 'ok';
    gehe(5, aufgaben.length ? an('setup.ansageExtrasGespeichert') : null);
  }

  // ---------------------------------------------------------------- Schritt 5
  let jobs = $state([]);
  let einbettung = $state(null);
  let laufMeldung = $state(null);
  let gestartet = $state(false);
  let timer = null;
  let weg = false;

  const alle = $derived(jobs.find((j) => j.name === 'all') ?? null);
  const einzelne = $derived(jobs.filter((j) => j.name !== 'all'));
  const kette = $derived(alle?.status === 'running');

  function seit(j) {
    return j?.started_at ? new Date(j.started_at).getTime() : 0;
  }

  /* Ein Job gehoert zum aktuellen Lauf, wenn er nach dessen Start begonnen
     hat. Alles davor ist der Stand eines frueheren Laufs (oder nie gelaufen)
     und steht, solange die Kette laeuft, auf "wartet". `skipped` liefert das
     Backend (der Wortlaut von last_result ist je Sprache anders). */
  function stand(j) {
    const imLauf = alle && seit(j) >= seit(alle) && seit(alle) > 0;
    if (j.status === 'running') return { art: 'laeuft', text: t('setup.standLaeuft') };
    if (kette && !imLauf) return { art: 'wartet', text: t('setup.standWartet') };
    if (j.status === 'error') return { art: 'fehler', text: t('allgemein.fehler') };
    if (j.status === 'ok') {
      return j.skipped
        ? { art: 'still', text: t('setup.standUebersprungen') }
        : { art: 'ok', text: t('setup.standFertig') };
    }
    return { art: 'still', text: '—' };
  }

  const fertigeJobs = $derived(
    alle ? einzelne.filter((j) => seit(j) >= seit(alle) && j.status !== 'running').length : 0);
  const aktuellerJob = $derived(einzelne.find((j) => j.status === 'running') ?? null);

  /* Kurze Ansage fuer Screenreader. Die ganze Liste in einem aria-live-Bereich
     wuerde alle 3 s komplett vorgelesen; so wird nur gesagt, was sich aendert. */
  const laufAnsage = $derived(
    kette
      ? (aktuellerJob
          ? t('setup.ansageLaeuftJob', { name: aktuellerJob.description || aktuellerJob.name })
          : t('setup.ansageLaeuft'))
      : alle?.status === 'ok' && gestartet ? t('setup.ansageFertig', { ergebnis: alle.last_result ?? '' })
      : alle?.status === 'error' && gestartet ? t('setup.ansageFehler', { fehler: alle.error ?? '' }) : '');

  async function abfragen() {
    clearTimeout(timer);
    const [j, e] = await Promise.all([api('/api/admin/cron/jobs'), api('/api/admin/einbettung')]);
    if (weg) return;
    if (j.ok) jobs = j.daten?.jobs ?? [];
    if (e.ok) einbettung = e.daten;
    const a = jobs.find((x) => x.name === 'all');
    if (a?.status === 'running' || einbettung?.laeuft) {
      gestartet = gestartet || a?.status === 'running';
      timer = setTimeout(abfragen, 3000);
    }
  }

  function fertigBetreten() {
    statusLaden();
    abfragen();
  }

  async function einlesen() {
    laufMeldung = mel('info', 'setup.starte');
    const r = await api('/api/admin/cron/run?job=all', { method: 'POST' });
    if (!r.ok) { laufMeldung = roh('fehler', r.fehler); return; }
    laufMeldung = r.daten?.status === 'already_running'
      ? mel('info', 'setup.schonUnterwegs')
      : null;
    gestartet = true;
    abfragen();
  }

  onDestroy(() => { weg = true; clearTimeout(timer); });

  function anbieterName(id) {
    return anbieter.find((a) => a.id === id)?.name ?? id;
  }

  function kiZeile(abschnitt) {
    const a = einstellungen?.[abschnitt];
    return eingerichtet(abschnitt) ? `${anbieterName(a.provider)} · ${a.model}` : t('setup.nichtEingerichtet');
  }

  const zusammenfassung = $derived(einstellungen ? [
    { id: 'jellyfin', name: t('setup.zJellyfin'), ok: status?.jellyfin !== false,
      wert: status?.jellyfin === false ? t('setup.nichtVerbunden') : t('setup.verbunden') },
    { id: 'llm', name: t('setup.zSprachmodell'), wert: kiZeile('llm'), ok: eingerichtet('llm') },
    { id: 'embedding', name: t('setup.zEmbeddings'), wert: kiZeile('embedding'), ok: eingerichtet('embedding') },
    { id: 'tmdb', name: t('setup.zTmdb'), ok: !!einstellungen.tmdb?.bearer_gesetzt,
      wert: einstellungen.tmdb?.bearer_gesetzt ? t('setup.tokenGesetztWert') : t('setup.ohne') },
    { id: 'seerr', name: t('setup.zSeerr'), ok: !!einstellungen.seerr?.url,
      wert: einstellungen.seerr?.url || t('setup.ohne') },
    { id: 'zeitplan', name: t('setup.zZeitplan'), ok: !!einstellungen.schedule?.enabled,
      wert: einstellungen.schedule?.enabled
        ? t('setup.taeglichUm', {
            zeit: `${pad(einstellungen.schedule.hour)}:${pad(einstellungen.schedule.minute)}`,
            zone: einstellungen.schedule.timezone })
        : t('setup.aus') },
  ] : []);
</script>

<svelte:head><title>{t('setup.seitenTitel')}</title></svelte:head>

<main class="setup" id="inhalt">
  <div class="rahmen">
    <div class="sprache"><SprachWahl /></div>
    <header class="kopf">
      <img src="/cinematek_banner.png" alt="CinematekAI" class="banner" />
      <h1>{t('setup.titel')}</h1>
    </header>

    {#if phase === 'laden'}
      <p class="e-text mitte" aria-live="polite">{t('allgemein.laden')}</p>

    {:else if phase === 'fehler'}
      <section class="e-karte">
        <h2>{t('setup.serverNichtErreichbar')}</h2>
        <p class="e-meldung fehler" role="alert">{ladeFehler}</p>
        <div class="e-knoepfe">
          <button type="button" class="e-knopf primaer" onclick={() => location.reload()}>{t('setup.neuLaden')}</button>
        </div>
      </section>

    {:else if phase === 'gesperrt'}
      <section class="e-karte">
        {#if status?.eingerichtet}
          <h2>{t('setup.schonEingerichtet')}</h2>
          {#if user.loggedIn}
            <p class="e-text">{t('setup.schonEingerichtetAdmin')}</p>
            <div class="e-knoepfe"><a class="e-knopf primaer" href="/">{t('setup.zurStartseite')}</a></div>
          {:else}
            <p class="e-text">{t('setup.schonEingerichtetAnmelden')}</p>
            <div class="e-knoepfe"><a class="e-knopf primaer" href="/login">{t('setup.zurAnmeldung')}</a></div>
          {/if}
        {:else}
          <h2>{t('setup.verbindungFehlt')}</h2>
          <p class="e-text">{t('setup.verbindungFehltText')}</p>
          <div class="e-knoepfe"><a class="e-knopf" href="/login">{t('setup.zurAnmeldung')}</a></div>
        {/if}
      </section>

    {:else}
      <nav aria-label={t('setup.schritteLabel')}>
        <ol class="schritte">
          {#each SCHRITTE as name, i (i)}
            {@const nr = i + 1}
            {@const art = nr === schritt ? 'aktuell' : erledigt[nr] ?? ''}
            {@const zusatz = art === 'uebersprungen' ? t('setup.zusatzUebersprungen') : art === 'ok' ? t('setup.zusatzErledigt') : ''}
            <li class={art} aria-current={nr === schritt ? 'step' : undefined}>
              {#if springbar(nr)}
                <button type="button" class="schritt" onclick={() => gehe(nr)}
                        aria-label={t('setup.schrittLabel', { nr, name, zusatz })}>
                  <span class="punkt" aria-hidden="true">{art === 'ok' ? '✓︎' : nr}</span>
                  <span class="name" aria-hidden="true">{name}</span>
                </button>
              {:else}
                <span class="schritt">
                  <span class="punkt" aria-hidden="true">{art === 'ok' ? '✓︎' : nr}</span>
                  <span class="name">{name}<span class="sr-only">{zusatz}</span></span>
                </span>
              {/if}
            </li>
          {/each}
        </ol>
      </nav>

      <p class="ansage" aria-live="polite">{zeige(ansage)}</p>

      <section class="e-karte schritt-karte" aria-labelledby="schritt-titel">
        <header class="karten-kopf">
          <p class="nummer">{t('setup.schrittVon', { nr: schritt, gesamt: SCHRITTE.length })}</p>
          <h2 id="schritt-titel" tabindex="-1" bind:this={ueberschrift}>{TITEL[schritt - 1]}</h2>
        </header>

        {#if schritt === 1}
          {#if status?.nutzer}
            <!-- Nachtrag: es gibt Nutzer, Jellyfin fehlt, ein Admin ist angemeldet. -->
            <p class="e-text">{t('setup.nachtragText')}</p>
            {#if einstellungen}
              <JellyfinFormular bind:werte={einstellungen.jellyfin} ongespeichert={nachtragGespeichert} />
            {:else}
              <p class="e-meldung fehler" role="alert">{einstellungenFehler || t('setup.einstellungenNichtGeladen')}</p>
            {/if}
          {:else}
            <p class="e-text">{t('setup.jfIntro')}</p>
            <form onsubmit={jfGeprueft ? jfVerbinden : jfPruefen} novalidate>
              <div class="e-raster">
                <div class="e-feld breit">
                  <label for="jf-url">{t('setup.jfAdresse')}</label>
                  <div class="zeile">
                    <input id="jf-url" type="text" inputmode="url" autocomplete="url" autocapitalize="off"
                           spellcheck="false" placeholder="http://192.168.1.10:8096"
                           bind:value={jf.url} aria-describedby="jf-url-hilfe" />
                    <button type="button" class="e-knopf" class:primaer={!jfGeprueft}
                            class:laeuft={jfLaeuft === 'pruefen'} onclick={jfPruefen} disabled={!!jfLaeuft}>
                      {jfLaeuft === 'pruefen' ? t('setup.pruefe') : t('setup.pruefen')}
                    </button>
                  </div>
                  <p class="e-klein" id="jf-url-hilfe">
                    {t('setup.jfAdresseHilfeVor')} <strong>localhost</strong> {t('setup.jfAdresseHilfeNach')}
                  </p>
                </div>
              </div>

              {#if jfGeprueft}
                <p class="gefunden">
                  <span aria-hidden="true">✓&#xFE0E;</span>
                  {t('setup.gefunden')} <strong>{jfServer.server_name}</strong>{jfServer.version ? t('setup.gefundenVersion', { version: jfServer.version }) : ''}
                </p>

                <p class="e-text">
                  {t('setup.jfAnmeldenVor')} <strong>{t('setup.jfAnmeldenFett')}</strong> {t('setup.jfAnmeldenNach')}
                </p>
                <div class="e-raster">
                  <div class="e-feld">
                    <label for="jf-name">{t('setup.benutzername')}</label>
                    <input id="jf-name" type="text" autocomplete="username" autocapitalize="off"
                           spellcheck="false" bind:value={jf.username} bind:this={nameFeld} />
                  </div>
                  <div class="e-feld">
                    <label for="jf-pw">{t('setup.passwort')}</label>
                    <input id="jf-pw" type="password" autocomplete="current-password" bind:value={jf.password} />
                  </div>
                </div>

                <details class="e-mehr">
                  <summary>{t('setup.oeffentlichOptional')}</summary>
                  <div class="e-feld">
                    <label for="jf-pub">{t('setup.oeffentlich')}</label>
                    <input id="jf-pub" type="text" inputmode="url" autocomplete="off" autocapitalize="off"
                           spellcheck="false" placeholder={t('setup.oeffentlichBeispiel')}
                           bind:value={jf.public_url} aria-describedby="jf-pub-hilfe" />
                    <p class="e-klein" id="jf-pub-hilfe">{t('setup.oeffentlichHilfe')}</p>
                  </div>
                </details>
                <details class="e-mehr">
                  <summary>{t('setup.schluesselSelbst')}</summary>
                  <div class="e-feld">
                    <label for="jf-key">{t('setup.schluessel')}</label>
                    <input id="jf-key" type="password" autocomplete="off" spellcheck="false"
                           bind:value={jf.api_key} aria-describedby="jf-key-hilfe" />
                    <p class="e-klein" id="jf-key-hilfe">{t('setup.schluesselHilfe')}</p>
                  </div>
                </details>

                <div class="e-knoepfe">
                  <button type="submit" class="e-knopf primaer" class:laeuft={jfLaeuft === 'verbinden'}
                          disabled={!!jfLaeuft}>
                    {jfLaeuft === 'verbinden' ? t('setup.verbinde') : t('setup.verbinden')}
                  </button>
                </div>
              {/if}
              <p class="e-meldung {jfMeldung?.art ?? ''}" aria-live="polite">{zeige(jfMeldung)}</p>
            </form>
          {/if}

        {:else if !einstellungen}
          <p class="e-meldung fehler" role="alert">
            {einstellungenFehler
              ? t('setup.einstellungenNichtGeladenGrund', { fehler: einstellungenFehler })
              : t('setup.einstellungenNichtGeladen')}
          </p>
          <div class="e-knoepfe">
            <button type="button" class="e-knopf primaer" onclick={einstellungenLaden}>{t('setup.erneutVersuchen')}</button>
          </div>

        {:else if schritt === 2}
          <p class="e-text">{t('setup.llmText')}</p>
          <AnbieterFormular abschnitt="llm" bind:werte={einstellungen.llm} {anbieter}
                            knopfText={t('setup.speichernWeiter')}
                            ongespeichert={() => kiFertig(2, 'llm')} />
          <div class="fuss">
            <p class="e-klein">{t('setup.llmOhne')}</p>
            <button type="button" class="e-knopf" onclick={() => ueberspringen(2)}>
              {t('setup.ueberspringen')}
            </button>
          </div>

        {:else if schritt === 3}
          <p class="e-text">
            {t('setup.embText1')} <strong>{t('setup.embStimmung')}</strong> {t('setup.embText2')}
            <strong>{t('setup.embPassung')}</strong> {t('setup.embText3')} <strong>{t('setup.embAehnlich')}</strong>.
          </p>
          <p class="e-warnung">{t('setup.embDatenschutz')}</p>
          <AnbieterFormular abschnitt="embedding" bind:werte={einstellungen.embedding} {anbieter}
                            knopfText={t('setup.speichernWeiter')}
                            ongespeichert={() => kiFertig(3, 'embedding')} />
          <div class="fuss">
            {#if einbettungFrage}
              <div class="e-warnung gefahr frage" role="alert">
                <p>{t('setup.embOhne')}</p>
                <div class="e-knoepfe">
                  <button type="button" class="e-knopf gefahr" onclick={() => ueberspringen(3)}>
                    {t('setup.trotzdemUeberspringen')}
                  </button>
                  <button type="button" class="e-knopf" onclick={() => (einbettungFrage = false)}>
                    {t('setup.dochEinrichten')}
                  </button>
                </div>
              </div>
            {:else}
              <button type="button" class="e-knopf" onclick={() => gehe(2)}>{t('allgemein.zurueck')}</button>
              <button type="button" class="e-knopf" onclick={() => (einbettungFrage = true)}>
                {t('setup.ueberspringen')}
              </button>
            {/if}
          </div>

        {:else if schritt === 4}
          <p class="e-text">{t('setup.extrasIntro')}</p>
          <form id="extras" onsubmit={extrasSpeichern} novalidate>
            <fieldset class="gruppe">
              <legend>{t('setup.tmdbLegende')}</legend>
              <p class="e-klein abstand">{t('setup.tmdbText')}</p>
              <div class="e-raster">
                <div class="e-feld breit">
                  <label for="tmdb-token">{t('setup.tokenOptional')}</label>
                  <div class="zeile">
                    <input id="tmdb-token" type="password" autocomplete="off" spellcheck="false"
                           bind:value={extra.bearer} aria-describedby="tmdb-stand"
                           placeholder={einstellungen.tmdb?.bearer_gesetzt ? t('setup.tokenBehalten') : 'eyJhbGciOi…'} />
                    <button type="button" class="e-knopf" class:laeuft={extraLaeuft === 'tmdb'}
                            onclick={tmdbTesten} disabled={!!extraLaeuft}>
                      {extraLaeuft === 'tmdb' ? t('setup.teste') : t('setup.testen')}
                    </button>
                  </div>
                  <p class="e-klein" id="tmdb-stand">
                    {#if einstellungen.tmdb?.bearer_gesetzt}
                      <span class="e-gesetzt">✓&#xFE0E; {t('setup.tokenGesetzt')}{einstellungen.tmdb.bearer_ende ? ` (…${einstellungen.tmdb.bearer_ende})` : ''}</span>
                    {:else}
                      <span class="e-gesetzt leer">{t('setup.keinToken')}</span>
                    {/if}
                  </p>
                  <p class="e-meldung {tmdbTest?.art ?? ''}" aria-live="polite">{zeige(tmdbTest)}</p>
                </div>
              </div>
            </fieldset>

            <fieldset class="gruppe">
              <legend>{t('setup.zSeerr')}</legend>
              <div class="e-raster">
                <div class="e-feld breit">
                  <label for="seerr-url">{t('setup.seerrAdresse')}</label>
                  <input id="seerr-url" type="text" inputmode="url" autocomplete="off" autocapitalize="off"
                         spellcheck="false" placeholder={t('setup.seerrBeispiel')} bind:value={extra.seerr}
                         aria-describedby="seerr-hilfe" />
                  <p class="e-klein" id="seerr-hilfe">{t('setup.seerrHilfe')}</p>
                </div>
              </div>
            </fieldset>

            <fieldset class="gruppe">
              <legend>{t('setup.laufLegende')}</legend>
              <p class="e-klein abstand">{t('setup.laufText')}</p>
              <div class="e-raster">
                <div class="e-feld">
                  <label for="plan-zeit">{t('setup.uhrzeit')}</label>
                  <input id="plan-zeit" type="time" step="60" bind:value={extra.zeit} />
                </div>
                <div class="e-feld">
                  <label for="plan-zone">{t('setup.zeitzone')}</label>
                  <input id="plan-zone" type="text" list="setup-zonen" autocomplete="off"
                         autocapitalize="off" spellcheck="false" bind:value={extra.zone}
                         aria-describedby="plan-zone-hilfe" />
                  <p class="e-klein" id="plan-zone-hilfe">
                    {#if browserZone && extra.zone.trim() !== browserZone}
                      {t('setup.browserZone', { zone: browserZone })}
                      <button type="button" class="textknopf" onclick={() => (extra.zone = browserZone)}>{t('setup.uebernehmen')}</button>
                    {:else}
                      {t('setup.zoneBeispiel')}
                    {/if}
                  </p>
                </div>
                <div class="e-feld">
                  <span class="e-label" aria-hidden="true">&nbsp;</span>
                  <label class="e-haken">
                    <input type="checkbox" bind:checked={extra.aktiv} />
                    {t('setup.taeglichAusfuehren')}
                  </label>
                </div>
              </div>
              <datalist id="setup-zonen">
                {#each ZONEN as z (z)}<option value={z}></option>{/each}
              </datalist>
            </fieldset>
          </form>

          <!-- Ausserhalb des Formulars oben: das Rueckfallformular ist selbst ein
               <form>, und Formulare duerfen nicht ineinander stecken. -->
          <details class="e-mehr rueckfall">
            <summary>{t('setup.rueckfall')}</summary>
            <p class="e-text">{t('setup.rueckfallText')}</p>
            <AnbieterFormular abschnitt="llm_fallback" bind:werte={einstellungen.llm_fallback} {anbieter}
                              knopfText={t('setup.rueckfallSpeichern')} />
          </details>

          <div class="fuss">
            <button type="button" class="e-knopf" onclick={() => gehe(3)}>{t('allgemein.zurueck')}</button>
            <button type="button" class="e-knopf" onclick={() => { erledigt[4] = 'uebersprungen'; gehe(5); }}>
              {t('setup.ueberspringen')}
            </button>
            <button type="submit" form="extras" class="e-knopf primaer" class:laeuft={extraLaeuft === 'speichern'}
                    disabled={!!extraLaeuft}>
              {extraLaeuft === 'speichern' ? t('setup.speichere') : t('setup.speichernWeiter')}
            </button>
          </div>
          <p class="e-meldung {extraMeldung?.art ?? ''}" aria-live="polite">{zeige(extraMeldung)}</p>

        {:else}
          <dl class="e-fakten uebersicht">
            {#each zusammenfassung as z (z.id)}
              <div>
                <dt>{z.name}</dt>
                <dd class:aus={!z.ok}>{z.wert}</dd>
              </div>
            {/each}
          </dl>

          <p class="e-text">{t('setup.fertigText')}</p>

          <div class="e-knoepfe">
            <button type="button" class="e-knopf" class:primaer={!gestartet} class:laeuft={kette}
                    onclick={einlesen} disabled={kette}>
              {kette ? t('setup.laeuftKnopf') : gestartet ? t('setup.nochEinmal') : t('setup.jetztEinlesen')}
            </button>
            <a class="e-knopf" class:primaer={gestartet} href="/" onclick={appInfoVergessen}>{t('setup.zurStartseite')}</a>
            <button type="button" class="e-knopf leise" onclick={() => gehe(4)}>{t('allgemein.zurueck')}</button>
          </div>
          <p class="e-meldung {laufMeldung?.art ?? ''}" aria-live="polite">{zeige(laufMeldung)}</p>
          <p class="sr-only" aria-live="polite">{laufAnsage}</p>

          {#if (gestartet || kette) && alle}
            <div class="lauf">
              <div class="lauf-kopf">
                <strong>
                  {#if kette}
                    {t('setup.laeuftSeit', { zeit: datum(alle.started_at) })}
                  {:else if alle.status === 'error'}
                    {t('setup.mitFehler')}
                  {:else}
                    {t('setup.fertig')}{alle.last_result ? ` · ${alle.last_result}` : ''}
                  {/if}
                </strong>
                <span class="e-klein">{t('setup.schritteFertig', { fertig: fertigeJobs, gesamt: einzelne.length })}</span>
              </div>
              <progress class="e-balken" max={einzelne.length || 1} value={fertigeJobs}
                        aria-label={t('setup.fortschrittLauf')}></progress>
              {#if alle.status === 'error' && alle.error}
                <p class="e-meldung fehler">{alle.error}</p>
              {/if}

              {#if einbettung?.laeuft && einbettung.fortschritt}
                {@const f = einbettung.fortschritt}
                <div class="einbett">
                  <div class="lauf-kopf">
                    <span>{t('setup.embFortschritt', { phase: f.phase, fertig: zahl(f.fertig), gesamt: zahl(f.gesamt) })}</span>
                    {#if f.rest_min != null}
                      <span class="e-klein">{t('setup.restZeit', { min: f.rest_min, n: zahl(Math.ceil(f.rest_min)) })}</span>
                    {/if}
                  </div>
                  <progress class="e-balken" max={f.gesamt || 1} value={f.fertig}
                            aria-label={t('setup.fortschrittEmb')}></progress>
                </div>
              {/if}

              <ol class="jobs">
                {#each einzelne as j (j.name)}
                  {@const s = stand(j)}
                  <li class={s.art}>
                    <span class="marke" aria-hidden="true">
                      {s.art === 'ok' ? '✓︎' : s.art === 'fehler' ? '✕︎' : s.art === 'laeuft' ? '●' : '○'}
                    </span>
                    <span class="job-text">
                      <span class="job-name">{j.description || j.name}</span>
                      {#if s.art === 'fehler' && j.error}
                        <span class="job-info fehler">{j.error}</span>
                      {:else if (s.art === 'ok' || s.art === 'still') && j.last_result && s.text !== '—'}
                        <span class="job-info">{j.last_result}</span>
                      {/if}
                    </span>
                    <span class="job-stand">{s.text}</span>
                  </li>
                {/each}
              </ol>
            </div>
          {/if}
        {/if}
      </section>
    {/if}
  </div>
</main>

<style>
  .setup {
    min-height: 100vh;
    min-height: 100dvh;
    box-sizing: border-box;
    padding: clamp(20px, 5vh, 56px) var(--s-4) var(--s-8);
    display: flex;
    justify-content: center;
  }
  /* 880px: breit genug fuer drei Felder nebeneinander, schmal genug, dass
     ein Schritt auf einem grossen Schirm nicht wie ein Formular-Teppich
     wirkt. Die Felder selbst ordnet .e-raster je nach Platz an. */
  .rahmen { width: 100%; max-width: 880px; min-width: 0; }

  /* Eigene Zeile oben rechts: so liegt der Umschalter auch auf schmalen
     Schirmen nie ueber dem Banner. */
  .sprache { display: flex; justify-content: flex-end; margin-bottom: var(--s-2); }
  .kopf { text-align: center; margin-bottom: var(--s-5); }
  .banner { display: block; width: 100%; max-width: 260px; height: auto; margin: 0 auto var(--s-2); }
  .kopf h1 {
    margin: 0;
    font-size: 15px;
    font-family: var(--font-display);
    font-style: italic;
    font-weight: 500;
    color: var(--text-muted);
    letter-spacing: 0.04em;
  }
  .mitte { text-align: center; margin: var(--s-7) auto; }

  /* Schrittanzeige: Punkte mit Verbindungslinie. */
  .schritte {
    list-style: none;
    margin: 0 0 var(--s-3);
    padding: 0;
    display: flex;
  }
  .schritte li {
    flex: 1 1 0;
    min-width: 0;
    position: relative;
    display: flex;
    justify-content: center;
  }
  .schritte li::before {
    content: '';
    position: absolute;
    top: 16px;
    right: calc(50% + 22px);
    left: calc(-50% + 22px);
    height: 1px;
    background: var(--border-strong);
  }
  .schritte li:first-child::before { display: none; }
  .schritte li.ok::before,
  .schritte li.uebersprungen::before,
  .schritte li.aktuell::before { background: var(--accent-muted); }

  .schritt {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    min-width: 0;
    background: none;
    border: 0;
    padding: 0;
    font: inherit;
    color: inherit;
  }
  button.schritt { cursor: pointer; min-height: 44px; }
  .punkt {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    box-sizing: border-box;
    display: grid;
    place-items: center;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-dim);
    background: var(--bg);
    border: 1px solid var(--border-strong);
    transition: border-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
  }
  .name {
    font-size: 12px;
    color: var(--text-dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
  }
  li.aktuell .punkt {
    border-color: var(--accent);
    color: var(--accent);
    box-shadow: 0 0 0 4px var(--gold-wash);
  }
  li.aktuell .name { color: var(--text); }
  li.ok .punkt { background: var(--accent); border-color: var(--accent); color: var(--bg); }
  li.ok .name { color: var(--text-muted); }
  li.uebersprungen .punkt { border-style: dashed; color: var(--text-muted); }
  button.schritt:hover .punkt { border-color: var(--gold-bright); }
  button.schritt:hover .name { color: var(--accent); }

  .ansage {
    min-height: 20px;
    margin: 0 0 var(--s-3);
    text-align: center;
    font-size: 13px;
    color: var(--success);
  }

  .schritt-karte { box-shadow: var(--elev-2); }
  .karten-kopf { margin-bottom: var(--s-3); }
  .nummer {
    margin: 0 0 2px;
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-dim);
  }
  .karten-kopf h2 {
    font-family: var(--font-display);
    font-size: 28px;
    margin: 0;
    color: var(--text);
  }
  .karten-kopf h2:focus { outline: none; }

  .zeile { display: flex; gap: var(--s-2); flex-wrap: wrap; }
  .zeile input { flex: 1 1 240px; min-width: 0; }
  .zeile .e-knopf { flex: 0 0 auto; min-height: 44px; }

  .gefunden {
    margin: calc(-1 * var(--s-2)) 0 var(--s-4);
    font-size: 14px;
    color: var(--success);
  }
  .gefunden strong { color: var(--text); font-weight: 600; }

  .fuss {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: var(--s-2) var(--s-3);
    border-top: 1px solid var(--border);
    margin-top: var(--s-4);
    padding-top: var(--s-4);
  }
  .fuss > .e-klein { flex: 1 1 260px; }
  .fuss .frage { flex: 1 1 100%; margin: 0; }
  .frage p { margin: 0 0 var(--s-2); }

  .gruppe {
    border: 0;
    margin: 0 0 var(--s-4);
    padding: 0 0 var(--s-4);
    border-bottom: 1px solid var(--border);
    min-width: 0;
  }
  .gruppe legend {
    padding: 0;
    margin-bottom: var(--s-2);
    font-family: var(--font-display);
    font-size: 20px;
    font-weight: 600;
    color: var(--text);
  }
  .gruppe .e-raster { margin-bottom: 0; }
  .abstand { margin-bottom: var(--s-3); }
  .rueckfall { margin-top: 0; border-top: 0; padding-top: 0; }

  .textknopf {
    background: none;
    border: 0;
    padding: 0 2px;
    min-height: 24px;
    color: var(--accent);
    font: inherit;
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
  }
  .textknopf:hover { color: var(--gold-bright); }

  .uebersicht dd.aus { color: var(--text-dim); }

  .lauf {
    margin-top: var(--s-5);
    padding: var(--s-4);
    background: var(--layer-1);
    border: 1px solid var(--border);
    border-radius: var(--r-md);
  }
  .lauf-kopf {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: baseline;
    gap: var(--s-1) var(--s-3);
    margin-bottom: var(--s-2);
    font-size: 14px;
  }
  .lauf-kopf strong { font-weight: 600; color: var(--text); }
  .einbett { margin-top: var(--s-4); }

  .jobs {
    list-style: none;
    margin: var(--s-4) 0 0;
    padding: 0;
    display: grid;
    gap: 2px;
  }
  .jobs li {
    display: grid;
    grid-template-columns: 20px minmax(0, 1fr) auto;
    gap: var(--s-2);
    align-items: start;
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
  }
  .jobs li:last-child { border-bottom: 0; }
  .marke { text-align: center; color: var(--text-dim); line-height: 1.5; }
  .job-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .job-name { color: var(--text-muted); line-height: 1.5; overflow-wrap: anywhere; }
  .job-info { color: var(--text-dim); font-size: 12px; overflow-wrap: anywhere; }
  .job-info.fehler { color: var(--danger); }
  .job-stand { color: var(--text-dim); white-space: nowrap; line-height: 1.5; }
  .jobs li.ok .marke, .jobs li.ok .job-stand { color: var(--success); }
  .jobs li.fehler .marke, .jobs li.fehler .job-stand { color: var(--danger); }
  .jobs li.laeuft .marke, .jobs li.laeuft .job-stand { color: var(--accent); }
  .jobs li.laeuft .job-name { color: var(--text); }
  .jobs li.laeuft .marke { animation: puls 1.6s ease-in-out infinite; }
  @keyframes puls { 50% { opacity: 0.35; } }

  @media (max-width: 560px) {
    .setup { padding-top: var(--s-4); }
    .banner { max-width: 200px; }
    /* Auf dem Handy nur die Punkte; welcher Schritt es ist, sagt die Karte
       ("Schritt 2 von 5" und die Ueberschrift). */
    .name {
      position: absolute; width: 1px; height: 1px; overflow: hidden;
      clip: rect(0 0 0 0); white-space: nowrap;
    }
    .karten-kopf h2 { font-size: 24px; }
    .zeile .e-knopf { flex: 1 1 100%; }
    .fuss { justify-content: stretch; }
    .fuss .e-knopf { flex: 1 1 auto; min-height: 44px; }
    .lauf { padding: var(--s-3); }
  }
</style>
