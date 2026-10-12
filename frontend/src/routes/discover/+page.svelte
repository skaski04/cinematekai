<script>
  /* Der Fragebogen zur Empfehlung. Die Abfragelogik ist unveraendert
     uebernommen — Reihenfolge der Fragen, Schluessel, Werte, Endpunkt und
     Zustandsnamen sind dieselben wie vorher.

     Neu ist nur die Darstellung: die Antworten trugen ihre Bedeutung bisher
     ueber ein vorangestelltes Emoji. Jetzt sind es Kacheln in der Form der
     Zeitkacheln auf der Startseite — Titel gross in Cormorant, darunter die
     erklaerende Zeile klein und gedimmt. Die Trefferliste nutzt PosterCard,
     damit ein Vorschlag hier genauso aussieht wie in der Bibliothek. */
  import PosterCard from '$lib/components/PosterCard.svelte';
  import { t, genre } from '$lib/i18n.svelte.js';

  let step = $state(0);
  let answers = $state({
    media_type: 'any', mood: '', time_bucket: '', familiarity: '', company: '', genre: 'any',
  });
  let results = $state(null);
  let loading = $state(false);
  /* Der Fehler wird als Schluessel gemerkt und erst beim Anzeigen
     uebersetzt — so wechselt auch eine stehende Meldung die Sprache.
     `text` ist fuer Meldungen, die nicht aus dem Woerterbuch kommen. */
  let fehlerInfo = $state(/** @type {{key?:string, werte?:object, text?:string}|null} */(null));
  let error = $derived(fehlerInfo ? (fehlerInfo.text ?? t(fehlerInfo.key, fehlerInfo.werte)) : '');

  /* Beschriftungen ueber t() und deshalb $derived: beim Umschalten der
     Sprache wird die Liste neu gebaut. Die Werte (value) bleiben gleich. */
  const QUESTIONS = $derived([
    {
      key: 'media_type',
      title: t('discover.typTitel'),
      options: [
        { value: 'any',    label: t('discover.typEgal'),  sub: t('discover.typEgalSub') },
        { value: 'movie',  label: t('discover.typFilm'),  sub: t('discover.typFilmSub') },
        { value: 'series', label: t('discover.typSerie'), sub: t('discover.typSerieSub') },
      ],
    },
    {
      key: 'mood',
      title: t('discover.stimmungTitel'),
      options: [
        { value: 'relaxed',    label: t('discover.stimmungEntspannt'),    sub: t('discover.stimmungEntspanntSub') },
        { value: 'exciting',   label: t('discover.stimmungAufgedreht'),   sub: t('discover.stimmungAufgedrehtSub') },
        { value: 'thoughtful', label: t('discover.stimmungNachdenklich'), sub: t('discover.stimmungNachdenklichSub') },
        { value: 'funny',      label: t('discover.stimmungLustig'),       sub: t('discover.stimmungLustigSub') },
      ],
    },
    {
      key: 'time_bucket',
      title: t('discover.zeitTitel'),
      options: [
        { value: 'short',  label: t('discover.zeitKurz'),   sub: t('discover.zeitKurzSub') },
        { value: 'medium', label: t('discover.zeitMittel'), sub: t('discover.zeitMittelSub') },
        { value: 'any',    label: t('discover.zeitEgal'),   sub: t('discover.zeitEgalSub') },
      ],
    },
    {
      key: 'familiarity',
      title: t('discover.bekanntTitel'),
      options: [
        { value: 'unseen',   label: t('discover.bekanntNeu'),        sub: t('discover.bekanntNeuSub') },
        { value: 'classic',  label: t('discover.bekanntKlassiker'),  sub: t('discover.bekanntKlassikerSub') },
        { value: 'surprise', label: t('discover.bekanntUeberrasch'), sub: t('discover.bekanntUeberraschSub') },
      ],
    },
    {
      key: 'company',
      title: t('discover.begleitungTitel'),
      options: [
        { value: 'alone',   label: t('discover.begleitungAllein'),  sub: t('discover.begleitungAlleinSub') },
        { value: 'partner', label: t('discover.begleitungPartner'), sub: t('discover.begleitungPartnerSub') },
        { value: 'friends', label: t('discover.begleitungFreunde'), sub: t('discover.begleitungFreundeSub') },
      ],
    },
    {
      key: 'genre',
      title: t('discover.genreTitel'),
      optional: true,
      /* Die Werte muessen wortgleich zu den gespeicherten Genrenamen sein:
         quiz_recommend vergleicht sie kleingeschrieben direkt mit den Genres
         der Titel, und die stehen seit der Uebersetzung in jellyfin.py
         (_GENRE_DE) auf Deutsch in der Datenbank. Mit "comedy" traf der
         Filter nie zu und die Antwort "Komödie" lieferte Actionfilme.
         Deshalb bleiben die Werte auch in der englischen Oberflaeche
         deutsch — nur die Beschriftung wechselt. */
      options: [
        { value: 'any',        label: t('discover.genreEgal'),    sub: t('discover.genreEgalSub') },
        { value: 'Action',     label: t('discover.genreAction') },
        { value: 'Drama',      label: t('discover.genreDrama') },
        { value: 'Komödie',    label: t('discover.genreKomoedie') },
        { value: 'Thriller',   label: t('discover.genreThriller') },
        { value: 'Science Fiction', label: t('discover.genreSciFi') },
        { value: 'Horror',     label: t('discover.genreHorror') },
        { value: 'Liebesfilm', label: t('discover.genreRomantik') },
      ],
    },
  ]);

  async function pick(key, value) {
    answers[key] = value;
    if (step < QUESTIONS.length - 1) {
      step += 1;
    } else {
      await submit();
    }
  }

  async function submit() {
    loading = true; fehlerInfo = null; results = null;
    try {
      // Strip "any" sentinel — backend treats null/missing as "any"
      const payload = { ...answers };
      if (payload.media_type === 'any') payload.media_type = null;
      if (payload.genre === 'any') delete payload.genre;
      const r = await fetch('/api/recommendations/quiz', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        fehlerInfo = r.status === 401 ? { key: 'discover.sitzungAbgelaufen' }
          : r.status >= 500 ? { key: 'discover.dienstAntwortetNicht' }
          : { key: 'discover.abgelehnt', werte: { status: r.status } };
        return;
      }
      results = await r.json();
    } catch (e) {
      /* Die Meldung ist zu sehen, seit der Fehlerzweig erreichbar ist — vorher
         stand hier String(e), was "Error: HTTP 503" ergab. Netzwerkfehler
         kommen als TypeError ohne brauchbaren Text. */
      fehlerInfo = e instanceof TypeError
        ? { key: 'discover.keineVerbindung' }
        : { text: e?.message || String(e) };
    } finally {
      loading = false;
    }
  }

  function restart() {
    step = 0;
    /* media_type fehlte hier: nach "Nochmal fragen" war es undefined statt
       'any', die Pruefung in submit() griff nicht mehr, und undefined ging
       ans Backend. Muss dieselben Felder tragen wie die Initialisierung. */
    answers = { media_type: 'any', mood: '', time_bucket: '', familiarity: '', company: '', genre: 'any' };
    results = null;
    fehlerInfo = null;
  }

  let current = $derived(QUESTIONS[step]);

  /* Dieselbe Faktenzeile wie in der Bibliothek, damit ein Vorschlag hier
     nicht anders beschriftet ist als derselbe Film unter /movies.
     Wird im Template aufgerufen, ist also reaktiv auf die Sprache. Die
     Bewertung bleibt bei toFixed(1) ("★ 7.5") wie in der Bibliothek. */
  const fakten = (r) => [
    r.year,
    r.runtime_min ? `${r.runtime_min} min` : null,
    r.rating ? `★ ${r.rating.toFixed(1)}` : null,
    (r.genres ?? []).slice(0, 2).map(genre).join(', '),
  ].filter(Boolean).join(' · ');
</script>

<svelte:head><title>{t('discover.seitentitel')}</title></svelte:head>

<main id="inhalt">
  <h1 class="sr-only">{t('discover.ueberschriftSr')}</h1>

  <div class="kopf">
    <h2>{t('discover.frage')}</h2>
    <span class="zahl">{t('discover.kurzeFragen', { n: QUESTIONS.length })}</span>
  </div>

  <!-- !error ist neu: vorher griff bei einem Fehlschlag schon dieser Zweig
       (results null, loading false) und zeigte wieder den Fragebogen — die
       Fehlermeldung weiter unten war unerreichbar. -->
  {#if !results && !loading && !error}
    <!-- Balken und Zaehlzeile stehen zusammen: der Balken zeigt auf einen
         Blick, wie weit es noch ist, die Zeile nennt die genaue Stelle. -->
    <div class="fortschritt">
      <div class="schiene" role="progressbar" aria-label={t('discover.fortschritt')}
           aria-valuemin="0" aria-valuemax={QUESTIONS.length} aria-valuenow={step + 1}>
        <i style="width: {((step + 1) / QUESTIONS.length) * 100}%"></i>
      </div>
      <span class="zaehler">{t('discover.zaehler', { nr: step + 1, n: QUESTIONS.length })}</span>
    </div>

    <section class="frage">
      <h3>{current.title}</h3>
      <div class="kacheln">
        {#each current.options as opt}
          <button class="mood" onclick={() => pick(current.key, opt.value)}>
            <b>{opt.label}</b>
            {#if opt.sub}<i>{opt.sub}</i>{/if}
          </button>
        {/each}
      </div>
      {#if step > 0}
        <button class="zurueck" onclick={() => step -= 1}>← {t('allgemein.zurueck')}</button>
      {/if}
    </section>
  {:else if loading}
    <div class="laedt">
      <div class="spinner" aria-hidden="true"></div>
      <p>{t('discover.sucht')}</p>
    </div>
  {:else if error}
    <div class="fehler">
      <p>{error}</p>
      <div class="fehler-knoepfe">
        <button class="haupt" onclick={submit}>{t('discover.nochmalVersuchen')}</button>
        <!-- Ohne diesen Knopf steckte man fest, wenn der Dienst dauerhaft
             nicht antwortet: der einzige Weg fuehrte zurueck in denselben Fehler. -->
        <button class="neben" onclick={restart}>{t('discover.vonVorn')}</button>
      </div>
    </div>
  {:else if results}
    <section class="treffer">
      <h3 class="abschnitt">{t('discover.dreiVorschlaege')}</h3>
      <div class="gitter">
        {#each results.results as r (r.movie_id)}
          <article class="vorschlag">
            <PosterCard
              id={r.movie_id}
              name={r.name}
              href={r.media_type === 'series' ? `/series/${r.movie_id}` : `/movie/${r.movie_id}`}
              facts={fakten(r)}
              badge={r.media_type === 'series' ? t('discover.serie') : null}
              score={typeof r.match_score === 'number' ? r.match_score : null}
            />
            {#if r.match_reason}
              <p class="grund">{r.match_reason}</p>
            {/if}
          </article>
        {/each}
      </div>
      {#if !results.results.length}
        <p class="leer">{t('discover.leer')}</p>
      {/if}
      <div class="aktionen">
        <button class="haupt" onclick={restart}>{t('discover.nochmalFragen')}</button>
      </div>
    </section>
  {/if}
</main>

<style>
  main {
    max-width: 900px; margin: 0 auto;
    padding: var(--s-6) var(--s-7) var(--s-9);
    color: var(--text); font-family: var(--font-ui);
  }

  .kopf {
    display: flex; align-items: baseline; gap: var(--s-4);
    flex-wrap: wrap; margin-bottom: var(--s-5);
  }
  .kopf h2 {
    font-family: var(--font-display); font-size: 30px; font-weight: 600;
    margin: 0; letter-spacing: 0.01em;
  }
  .zahl { font-size: 13px; color: var(--text-dim); }

  .fortschritt {
    display: flex; align-items: center; gap: var(--s-3);
    margin-bottom: var(--s-6);
  }
  .schiene {
    flex: 1; height: 3px; overflow: hidden;
    border-radius: var(--r-full); background: var(--layer-2);
  }
  .schiene i {
    display: block; height: 100%; background: var(--accent);
    transition: width var(--dur-base) var(--ease);
  }
  .zaehler {
    font-size: 12px; color: var(--text-dim); white-space: nowrap;
    letter-spacing: 0.1em; text-transform: uppercase;
  }

  .frage h3 {
    font-family: var(--font-display); font-size: 34px; font-weight: 600;
    letter-spacing: 0.01em; margin: 0 0 var(--s-5);
  }

  .kacheln {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: var(--s-3);
  }
  /* Form der Zeitkacheln von der Startseite. min-height statt fester Hoehe,
     weil "Etwa 2 Stunden" in schmalen Spalten umbricht. */
  .mood {
    min-height: 92px; border-radius: var(--r-md);
    border: 1px solid rgba(255, 255, 255, 0.07);
    background: var(--layer-2); color: var(--text);
    display: flex; flex-direction: column; justify-content: center;
    padding: var(--s-4) var(--s-5); cursor: pointer; text-align: left;
    font-family: var(--font-ui);
    transition: background var(--dur-base) var(--ease),
                border-color var(--dur-base) var(--ease),
                transform var(--dur-base) var(--ease);
  }
  .mood:hover {
    background: var(--layer-3); border-color: var(--gold-ring);
    transform: translateY(-3px);
  }
  .mood b {
    font-family: var(--font-display); font-size: 21px; font-weight: 600;
    line-height: 1.2; color: var(--gold-bright);
  }
  .mood i {
    font-style: normal; font-size: 12px; color: var(--text-dim);
    margin-top: var(--s-1);
  }

  /* Sekundaere Handlung: Rand statt Flaeche. */
  .zurueck, .neben {
    min-height: 44px; padding: 0 var(--s-4);
    background: transparent; color: var(--text-muted);
    border: 1px solid var(--border-strong); border-radius: var(--r-full);
    font-family: var(--font-ui); font-size: 13px; cursor: pointer;
    transition: border-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
  }
  .zurueck:hover, .neben:hover { border-color: var(--accent); color: var(--accent); }
  .zurueck { margin-top: var(--s-5); }

  .laedt { text-align: center; padding: var(--s-9) var(--s-4); }
  .spinner {
    width: 44px; height: 44px; margin: 0 auto;
    border: 2px solid var(--layer-3); border-top-color: var(--accent);
    border-radius: var(--r-full); animation: dreh 0.9s linear infinite;
  }
  @keyframes dreh { to { transform: rotate(360deg); } }
  /* Was die KI sagt, spricht Serif. */
  .laedt p {
    font-family: var(--font-display); font-style: italic; font-size: 19px;
    color: var(--gold-bright); margin: var(--s-5) 0 0;
  }

  .fehler { text-align: center; padding: var(--s-7) var(--s-4); }
  .fehler p { color: var(--danger); font-size: 14px; margin: 0 0 var(--s-4); }
  .fehler-knoepfe {
    display: flex; gap: var(--s-3); justify-content: center; flex-wrap: wrap;
  }

  .abschnitt {
    font-family: var(--font-ui); font-size: 17px; font-weight: 500;
    letter-spacing: 0.02em; text-transform: none; margin: 0 0 var(--s-5);
  }

  .gitter {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
    gap: var(--s-5);
  }
  .vorschlag { display: flex; flex-direction: column; gap: var(--s-3); }
  /* Die Begruendung steht unter der Karte statt im Hover-Overlay von
     PosterCard: sie ist hier das eigentliche Ergebnis der Befragung und
     muss auch auf Touch ohne Umweg lesbar sein. */
  .grund {
    margin: 0; font-family: var(--font-display); font-style: italic;
    font-size: 15px; line-height: 1.4; color: var(--gold-bright);
  }

  .leer {
    margin: 0; text-align: center; padding: var(--s-7) 0;
    color: var(--text-dim); font-style: italic;
  }

  .aktionen { margin-top: var(--s-6); text-align: center; }
  /* Der einzige Vollgold-Knopf der Ansicht. */
  .haupt {
    min-height: 44px; padding: 0 var(--s-5);
    background: var(--accent); color: var(--bg); border: 0;
    border-radius: var(--r-full); cursor: pointer;
    font-family: var(--font-ui); font-size: 14px; font-weight: 500;
    transition: background var(--dur-fast) var(--ease);
  }
  .haupt:hover { background: var(--gold-bright); }

  /* Ohne Hover muss die Kachel schon im Ruhezustand als Knopf lesbar sein. */
  @media (hover: none) {
    .mood { border-color: var(--border-strong); }
    .mood:hover { transform: none; background: var(--layer-2); }
  }

  @media (max-width: 820px) {
    main { padding: var(--s-4) var(--s-4) var(--s-8); }
    .kopf h2 { font-size: 24px; }
    .frage h3 { font-size: 26px; }
    .kacheln { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--s-2); }
    .mood { min-height: 80px; padding: var(--s-3) var(--s-4); }
    .mood b { font-size: 18px; }
    .gitter { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: var(--s-4); }
    .grund { font-size: 14px; }
  }
</style>
