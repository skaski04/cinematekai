<script>
  /* Neue Startseite. Liegt bewusst auf einer eigenen Route — die alte
     bleibt unter "/" unveraendert erreichbar, bis diese hier uebernimmt.

     Der Umbau gegenueber "/": aus zehn gleich lauten Sektionen werden drei
     Ebenen. Der Film des Tages steigt aus einem umrandeten Kasten in der
     Seitenmitte in einen Hero mit Backdrop. Poster tragen ihren Titel im
     Overlay statt darunter, dadurch passen sie doppelt so gross in dieselbe
     Reihenhoehe. Emoji-Symbole entfallen; die Hierarchie traegt die
     Schriftgroesse. */
  import { onMount } from 'svelte';
  import Hero from '$lib/components/Hero.svelte';
  import Row from '$lib/components/Row.svelte';
  import PosterCard from '$lib/components/PosterCard.svelte';
  import WideCard from '$lib/components/WideCard.svelte';
  import SpotlightTile from '$lib/components/SpotlightTile.svelte';
  import ChatDock from '$lib/components/ChatDock.svelte';
  import { t, datum, genre } from '$lib/i18n.svelte.js';

  let continueItems = $state([]);
  let coqItems = $state([]);
  let coqPending = $state(null);
  let weeklySeries = $state(null);
  let daily = $state(null);
  let dailyPlayUrl = $state(null);
  let dailyLoading = $state(false);
  let dismissing = $state(false);
  let missing = $state(null);
  let topSeries = $state([]);
  let recentMovies = $state([]);
  let watchlist = $state([]);

  /* Zeitpunkt des letzten Jellyfin-Abgleichs. Bisher stand nirgends, wie
     frisch die Daten sind — im Test meldete der Chat "noch nichts gesehen",
     weil der naechtliche Abgleich fuer das neue Konto schlicht noch nicht
     gelaufen war. Das sah nach einem Fehler aus und war keiner. */
  let stand = $state(null);

  /* Die Seite hatte weder Lade- noch Fehlerzustand: war das Backend nicht
     erreichbar, lieferte `j` ueberall null, und es stand nichts da — ohne
     einen Hinweis, woran es liegt. */
  let laedt = $state(true);
  let fehler = $state(false);

  const ZEIT_OPTIONEN = [60, 90, 120, 150];
  let zeitMinuten = $state(null);
  let zeitResults = $state([]);
  let zeitLoading = $state(false);
  let zeitPool = $state(0);

  const j = async (url) => {
    try {
      const r = await fetch(url, { credentials: 'include' });
      if (r.status === 401) { window.location.href = '/login'; return null; }
      return r.ok ? await r.json() : null;
    } catch { return null; }
  };

  /* /status schickt last_sync_at als reine UTC-Zeit ohne Kennung
     ("2026-09-29T08:30:13.832067" — datetime.utcnow() in backend/sync.py).
     Einen solchen String liest JavaScript als Ortszeit; im Sommer waeren das
     zwei Stunden zu frueh. Darum das Z anhaengen, wenn keine Zone dasteht. */
  function alsDatum(iso) {
    if (!iso) return null;
    const mitZone = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
    const d = new Date(mitZone);
    return isNaN(d.getTime()) ? null : d;
  }

  const tagesbeginn = (d) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

  /* „heute" und „gestern" zaehlen Kalendertage, nicht 24-Stunden-Schritte:
     ein Abgleich um 10:30 ist am naechsten Morgen um 9:00 „gestern" und
     nicht „vor 22 Stunden". */
  const standZeile = $derived.by(() => {
    if (!stand) return null;
    const uhr = datum(stand, { hour: '2-digit', minute: '2-digit' });
    const tage = Math.round((tagesbeginn(new Date()) - tagesbeginn(stand)) / 86400000);
    if (tage <= 0) return t('start.standHeute', { uhr });
    if (tage === 1) return t('start.standGestern', { uhr });
    const tag = datum(stand, { day: '2-digit', month: '2-digit', year: 'numeric' });
    return t('start.standTag', { tag, uhr });
  });

  /* Fakten in einer Zeile. Absichtlich hier zusammengebaut statt in der
     Karte: so bleibt PosterCard frei von Wissen ueber Filme. */
  function facts(...parts) {
    return parts.filter(Boolean).join(' · ');
  }
  const genreLine = (g, n = 2) => (g ?? []).slice(0, n).map((x) => genre(x)).join(', ');

  /* An Tagen ohne KI-Kuratierung fehlt den thematischen Vorschlaegen das Feld
     `curator_reason`, und die Kachel blieb ohne Begruendung. Einen Satz pro
     Titel liefert /recommendations/daily dann zwar nicht, wohl aber
     `tmdb_because`: die eigenen Titel, ueber die der Film gefunden wurde.
     Daraus wird hier dieselbe mechanische Begruendung, die das Backend fuer
     den Film des Tages baut. */
  function themaGrund(film) {
    if (film.curator_reason) return film.curator_reason;
    const weil = film.tmdb_because ?? [];
    if (weil.length >= 2)
      return t('start.tmdbNebenZwei', { a: weil[0], b: weil[1] });
    if (weil.length) return t('start.tmdbNebenEins', { a: weil[0] });
    return null;
  }

  /* Die Kacheln der Reihe "Fehlt in deiner Sammlung" fuehren nach Seerr, also
     aus der App heraus — vor dem Umbau ging das in einem neuen Tab auf.
     PosterCard nimmt kein target entgegen und soll es auch nicht lernen: die
     Karte weiss nichts darueber, wohin sie fuehrt. Die Anker bekommen die
     Attribute deshalb hier nachtraeglich. Der Traeger sitzt um die ganze
     Reihe statt um jede Karte, damit die Karten Flex-Kinder der Reihe
     bleiben und ihre Breite behalten. */
  let seerrReihe = $state(null);
  $effect(() => {
    missing?.items;            // erneut laufen, wenn die Reihe neu geladen wurde
    if (!seerrReihe) return;
    for (const a of seerrReihe.querySelectorAll('a[href]')) {
      a.target = '_blank';
      a.rel = 'noopener';
    }
  });

  /* Die Empfehlungslisten schwanken stark: "Raus aus der Blase" liefert mal
     einen Treffer, mal acht. Eine Reihe mit einer einzelnen Karte laesst
     vier Fuenftel der Breite leer und sieht aus wie ein Ladefehler — bis zu
     dieser Zahl werden die Eintraege deshalb als breite Kacheln gesetzt,
     die die Zeile fuellen. Darueber lohnt sich die scrollbare Reihe. */
  const KACHEL_BIS = 4;

  async function loadDaily(force = false) {
    dailyLoading = true;
    daily = await j('/api/recommendations/daily' + (force ? '?force=true' : ''));
    dailyLoading = false;
    dailyPlayUrl = null;
    /* Der Hero braucht den Jellyfin-Link. Die Tagesempfehlung liefert ihn
       nicht mit, das Detail schon — ein Nachschlag, damit der wichtigste
       Knopf der Seite direkt in den Player fuehrt statt auf eine Zwischenseite. */
    const id = daily?.film_of_day?.movie_id;
    if (!id) return;

    const d = await j(`/api/movies/${id}/detail`);
    dailyPlayUrl = d?.play_url ?? null;
  }

  /* Das Ausblenden lief bisher stumm: der Hero wechselte einfach den Film,
     und wer versehentlich auf das Kreuz kam, hatte einen Titel fuer 60 Tage
     weg — ohne Hinweis, dass ueberhaupt etwas passiert ist, und ohne Weg
     zurueck. Acht Sekunden sind lang genug zum Zurueckklicken und kurz
     genug, um nicht im Weg zu stehen. */
  const HINWEIS_MS = 8000;
  let hinweis = $state(null);   // { text, zurueck } — zurueck ist optional
  let hinweisUhr = null;
  let zurueckPending = $state(false);

  function zeigeHinweis(text, zurueck = null, ms = HINWEIS_MS) {
    hinweis = { text, zurueck };
    clearTimeout(hinweisUhr);
    hinweisUhr = setTimeout(() => { hinweis = null; }, ms);
  }

  // Beim Verlassen der Seite darf kein Timer mehr auf die Zustandsvariable
  // schreiben.
  $effect(() => () => clearTimeout(hinweisUhr));

  async function dismissDaily() {
    const f = daily?.film_of_day;
    if (!f || dismissing) return;
    const id = f.movie_id;
    /* Den bisherigen Vorschlag festhalten, bevor er ersetzt wird: das
       Zuruecknehmen soll genau den Film wiederherstellen, den der Nutzer
       gerade weggeklickt hat. Ein erneutes Laden wuerde das nicht leisten —
       der Server waehlt dann frei aus dem Kandidatenpool. */
    const vorher = daily;
    const vorherPlay = dailyPlayUrl;
    dismissing = true;
    try {
      const r = await fetch(`/api/recommendations/dismiss/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ days: 60 }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      await loadDaily(true);
      zeigeHinweis(t('start.ausgeblendet', { name: f.name }),
                   () => ausblendenZurueck(id, vorher, vorherPlay));
    } catch {
      zeigeHinweis(t('start.ausblendenFehler'), null, 5000);
    } finally { dismissing = false; }
  }

  /* Nimmt die Ausblendung zurueck (DELETE /recommendations/dismiss/{id},
     backend/main.py:861) und setzt den alten Vorschlag wieder ein. Der
     Endpunkt leert dabei den Empfehlungs-Cache des Nutzers, der naechste
     Seitenaufruf rechnet also ohnehin neu. */
  async function ausblendenZurueck(id, vorher, vorherPlay) {
    if (zurueckPending) return;
    zurueckPending = true;
    try {
      const r = await fetch(`/api/recommendations/dismiss/${id}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      daily = vorher;
      dailyPlayUrl = vorherPlay;
      zeigeHinweis(t('start.wiederEingeblendet'), null, 4000);
    } catch {
      zeigeHinweis(t('start.rueckgaengigFehler'), null, 5000);
    } finally { zurueckPending = false; }
  }

  async function decideCoQ(it, decision) {
    if (coqPending) return;
    coqPending = it.id;
    try {
      const r = await fetch(`/api/series/${it.id}/coq-decision`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      });
      if (r.ok) coqItems = coqItems.filter(x => x.id !== it.id);
    } catch {} finally { coqPending = null; }
  }

  async function sucheNachZeit(min) {
    if (zeitMinuten === min) { zeitMinuten = null; zeitResults = []; return; }
    zeitMinuten = min; zeitLoading = true; zeitResults = [];
    const d = await j(`/api/recommendations/by-time?minutes=${min}&k=12`);
    zeitResults = d?.results ?? [];
    zeitPool = d?.pool_size ?? 0;
    zeitLoading = false;
  }

  /* Einzelne leere Reihen sind hier normal — ein Tag ohne Ausreisser, eine
     leere Merkliste. Erst wenn auch die Tagesempfehlung und beide
     Bibliothekslisten nichts liefern, liegt es nicht am Bestand, sondern am
     Server. Nur diese drei entscheiden deshalb ueber den Fehlerzustand. */
  async function ladeStartseite() {
    laedt = true;
    fehler = false;
    /* Jede Reihe erscheint, sobald ihre eigene Antwort da ist. Vorher wurden
       Serien und Filme erst nach Promise.all gesetzt und warteten damit auf
       die langsamste der acht Abfragen. */
    const [serien, filme] = await Promise.all([
      j('/api/series?sort=ai_match&order=desc&limit=12').then(d => (topSeries = d?.items ?? [], d)),
      j('/api/movies?sort=added&order=desc&limit=16').then(d => (recentMovies = d?.items ?? [], d)),
      j('/api/home/continue-watching').then(d => continueItems = d?.items ?? []),
      j('/api/home/continue-or-quit').then(d => coqItems = d?.items ?? []),
      j('/api/recommendations/weekly-series').then(d => weeklySeries = d),
      j('/api/recommendations/missing').then(d => missing = d),
      j('/api/watchlist').then(d => watchlist = d?.items ?? []),
      j('/api/status').then(d => stand = alsDatum(d?.last_sync_at)),
      loadDaily(),
    ]);
    fehler = !daily && !serien && !filme;
    laedt = false;
  }

  onMount(ladeStartseite);

  /* Label und Fortschritt fuer "Weiterschauen" haengen am Typ: Film, Serie
     oder Filmreihe. Die alte Seite loeste das mit drei ineinander
     geschachtelten Bedingungen mitten im Markup. */
  function continueLabel(it) {
    if (it.type === 'series') return it.next_up_label ?? null;
    if (it.type === 'collection') return t('start.reiheTeil', { n: it.watched_count + 1 });
    return t('start.nochMinuten', { n: it.remaining_min ?? 0 });
  }
  function continueSub(it) {
    if (it.type === 'series')
      return t('start.folgenVon', { gesehen: it.episodes_watched, gesamt: it.total_episodes });
    if (it.type === 'collection')
      return t('start.reiheWeiterMit',
               { gesehen: it.watched_count, gesamt: it.total_count, name: it.name });
    return facts(`${it.progress_pct}%`, it.runtime_min ? t('start.minuten', { n: it.runtime_min }) : null);
  }
  function continueProgress(it) {
    if (it.type === 'collection' && it.total_count)
      return Math.round((it.watched_count / it.total_count) * 100);
    if (it.type === 'series' && it.total_episodes)
      return Math.round((it.episodes_watched / it.total_episodes) * 100);
    return it.progress_pct ?? 0;
  }
</script>

<svelte:head><title>CinematekAI</title></svelte:head>

<h1 class="sr-only">{t('start.startseite')}</h1>

{#if daily?.film_of_day}
  {@const f = daily.film_of_day}
  <Hero
    id={f.movie_id}
    name={f.name}
    year={f.year}
    facts={facts(f.year, genreLine(f.genres, 3),
                 f.runtime_min ? t('start.minuten', { n: f.runtime_min }) : null,
                 f.rating ? `★ ${f.rating.toFixed(1)}` : null)}
    reason={daily.llm_reason || daily.reason}
    overview={f.overview}
    playHref={dailyPlayUrl}
    trailerSrc={`/api/items/${f.movie_id}/trailer`}
    detailHref={`/movie/${f.movie_id}`}
    label={daily.curated ? t('start.filmDesTagesKi') : t('start.filmDesTages')}
    onRefresh={() => loadDaily(true)}
    onDismiss={dismissDaily}
    busy={dailyLoading || dismissing}
  />
{/if}

<main class="sections" class:mit-hero={!!daily?.film_of_day} id="inhalt">

  {#if laedt}
    <p class="zustand">{t('start.ladeEmpfehlungen')}</p>
  {:else if fehler}
    <div class="zustand fehler">
      <p>{t('start.ladeFehler')}</p>
      <button class="mini" onclick={ladeStartseite}>{t('start.erneutVersuchen')}</button>
    </div>
  {/if}

  <!-- ——— Ebene 1: laufend ——— -->
  {#if continueItems.length}
    <Row title={t('start.weiterschauen')} level={1} sub={t('start.weiterschauenSub')} wide>
      {#each continueItems as it (`${it.type}-${it.id}`)}
        <WideCard
          id={it.id}
          name={it.type === 'collection' ? it.collection_name : it.name}
          href={it.detail_url}
          sub={continueSub(it)}
          badge={continueLabel(it)}
          badgeStrong={it.type === 'collection'}
          progress={continueProgress(it)}
        />
      {/each}
    </Row>
  {/if}

  {#if weeklySeries?.series || coqItems.length}
    <section class="spotlight">
      <h2 class="spot-head">{t('start.dieseWoche')}</h2>
      <div class="duo">
        {#if weeklySeries?.series}
          {@const s = weeklySeries.series}
          <SpotlightTile
            id={s.movie_id}
            name={s.name}
            href={`/series/${s.movie_id}`}
            label={t('start.serieDerWoche', { woche: weeklySeries.week ?? '' })}
            facts={facts(s.year, genreLine(s.genres, 3),
                         s.episode_count ? t('start.folgen', { n: s.episode_count }) : null,
                         s.rating ? `★ ${s.rating.toFixed(1)}` : null)}
            reason={weeklySeries.reason}
          />
        {/if}

        {#each coqItems.slice(0, 1) as it (it.id)}
          <SpotlightTile
            id={it.id}
            name={it.name}
            href={it.detail_url}
            label={t('start.weiterOderWeg')}
            facts={facts(t('start.folgenVon', { gesehen: it.episodes_watched, gesamt: it.total_episodes }),
                         it.next_up_label ? t('start.weiterMit', { folge: it.next_up_label }) : null)}
            reason={t('start.coqGrund')}
          >
            {#snippet children()}
              <button class="mini" disabled={coqPending}
                      onclick={() => decideCoQ(it, 'keep')}>↻ {t('start.weitermachen')}</button>
              <button class="mini" disabled={coqPending}
                      onclick={() => decideCoQ(it, 'drop')}>✕ {t('start.ausDemStapel')}</button>
            {/snippet}
          </SpotlightTile>
        {/each}
      </div>
    </section>
  {/if}

  <!-- ——— Ebene 2: kuratiert ——— -->
  {#if daily?.thematic?.length}
    {#if daily.thematic.length <= KACHEL_BIS}
      <section class="spotlight">
        <h2 class="spot-head">{t('start.auchInteressant')}</h2>
        <div class="duo">
          {#each daily.thematic as th (th.movie_id)}
            <SpotlightTile
              id={th.movie_id}
              name={th.name}
              href={`/movie/${th.movie_id}`}
              facts={facts(th.year, genreLine(th.genres, 3))}
              reason={themaGrund(th)}
            />
          {/each}
        </div>
      </section>
    {:else}
      <Row title={t('start.auchInteressant')} level={2}>
        {#each daily.thematic as th (th.movie_id)}
          <PosterCard
            id={th.movie_id}
            name={th.name}
            href={`/movie/${th.movie_id}`}
            facts={facts(th.year, genreLine(th.genres))}
            hint={themaGrund(th)}
          />
        {/each}
      </Row>
    {/if}
  {/if}

  {#if daily?.exploration?.length}
    {#if daily.exploration.length <= KACHEL_BIS}
      <section class="spotlight">
        <h2 class="spot-head">
          {t('start.rausAusDerBlase')}
          <span class="spot-sub">{t('start.rausAusDerBlaseSub')}</span>
        </h2>
        <div class="duo">
          {#each daily.exploration as e (e.movie_id)}
            <SpotlightTile
              id={e.movie_id}
              name={e.name}
              href={`/movie/${e.movie_id}`}
              facts={facts(e.year, genreLine(e.genres, 3))}
              reason={e.exploration_reason}
            />
          {/each}
        </div>
      </section>
    {:else}
      <Row title={t('start.rausAusDerBlase')} level={2}
           sub={t('start.rausAusDerBlaseSub')}>
        {#each daily.exploration as e (e.movie_id)}
          <PosterCard
            id={e.movie_id}
            name={e.name}
            href={`/movie/${e.movie_id}`}
            facts={facts(e.year, genreLine(e.genres))}
            hint={e.exploration_reason}
          />
        {/each}
      </Row>
    {/if}
  {/if}

  {#if missing?.items?.length}
    <!-- Ohne eingetragene Seerr-Adresse liefert das Backend seerr_url: null.
         Die Karte wird dann zum blossen Hinweis ohne Link (kein href = kein
         Fokus, kein Klickziel), und der Untertitel verspricht nichts.
         Der Untertitel nennt immer nur die Quelle, unabhängig von
         missing.curated: Die Reihe ist eine eigenständige TMDB-Funktion und
         wird nicht als Teil der KI-Funktionen beschrieben. -->
    {@const mitSeerr = missing.items.some((m) => m.seerr_url)}
    <div bind:this={seerrReihe}>
      <Row title={t('start.fehltInSammlung')} level={2}
           sub={t('start.ausTmdb') + (mitSeerr ? ` · ${t('start.klickSeerr')}` : '')}>
        <!-- Quellenangabe: Titel und Poster dieser Reihe kommen von TMDB, und
             die TMDB-Nutzungsbedingungen verlangen, die Nutzung mit dem
             TMDB-Logo kenntlich zu machen. Klein wie ein Wort der Beizeile,
             also deutlich leiser als das CINEMATEK-Logo oben; der
             Pflichthinweis selbst steht im Abspann (+layout.svelte). -->
        {#snippet quelle()}
          <a class="tmdb-quelle" href="https://www.themoviedb.org" target="_blank"
             rel="noopener noreferrer" aria-label={t('start.tmdbQuelle')}>
            <img src="/tmdb/tmdb-logo.svg" alt="TMDB" width="77" height="10" />
          </a>
        {/snippet}
        {#each missing.items as m (m.tmdb_id)}
          <PosterCard
            id={m.tmdb_id}
            name={m.title}
            href={m.seerr_url || undefined}
            imgSrc={m.poster_url}
            facts={facts(m.year, m.vote_average ? `★ ${m.vote_average.toFixed(1)}` : null)}
            hint={m.reason}
          />
        {/each}
      </Row>
    </div>
  {/if}

  <section class="zeit">
    <h2 class="spot-head">{t('start.wieVielZeit')}</h2>
    <div class="zeit-grid">
      {#each ZEIT_OPTIONEN as m}
        <button class="mood" class:active={zeitMinuten === m}
                onclick={() => sucheNachZeit(m)} disabled={zeitLoading}>
          <b>{t('start.bisMin', { n: m })}</b>
          <i>{m <= 60 ? t('start.zeitFolge') : m <= 90 ? t('start.zeitKompakt')
             : m <= 120 ? t('start.zeitNormal') : t('start.zeitViel')}</i>
        </button>
      {/each}
    </div>
    {#if zeitMinuten}
      <p class="zeit-hint">
        {zeitLoading ? t('start.suche') : t('start.passendUngesehen', { n: zeitPool })}
      </p>
    {/if}
  </section>

  {#if zeitMinuten && zeitResults.length}
    <Row title={t('start.bisMinuten', { n: zeitMinuten })} level={2}>
      {#each zeitResults as r (r.movie_id)}
        <PosterCard
          id={r.movie_id}
          name={r.name}
          href={`/movie/${r.movie_id}`}
          facts={facts(r.year, r.runtime_min ? t('start.minuten', { n: r.runtime_min }) : null)}
        />
      {/each}
    </Row>
  {:else if zeitMinuten && !zeitLoading}
    <p class="leer">{t('start.nichtsImZeitfenster')}</p>
  {/if}

  <!-- ——— Ebene 3: Archiv ——— -->
  {#if watchlist.length}
    <Row title={t('start.merkliste')} level={3}>
      {#each watchlist as w (w.movie_id)}
        <PosterCard
          id={w.movie_id}
          name={w.name}
          href={w.media_type === 'series' ? `/series/${w.movie_id}` : `/movie/${w.movie_id}`}
          facts={facts(w.year, w.runtime_min ? t('start.minuten', { n: w.runtime_min }) : null)}
          badge={w.media_type === 'series' ? t('start.serie') : null}
        />
      {/each}
    </Row>
  {/if}

  {#if topSeries.length}
    <Row title={t('start.ausDeinenSerien')} level={3} moreHref="/series"
         moreLabel={t('start.alleSerien')}>
      {#each topSeries as s (s.id)}
        <PosterCard
          id={s.id}
          name={s.name}
          href={`/series/${s.id}`}
          facts={facts(s.year, s.episode_count ? t('start.folgen', { n: s.episode_count }) : null)}
          badge={s.status === 'continuing' ? t('start.laeuft') : null}
          badgeStrong={s.status === 'continuing'}
          score={typeof s.match_score === 'number' ? s.match_score : null}
        />
      {/each}
    </Row>
  {/if}

  {#if recentMovies.length}
    <Row title={t('start.zuletztHinzugefuegt')} level={3} moreHref="/movies"
         moreLabel={t('start.alleFilme')}>
      {#each recentMovies as m (m.id)}
        <PosterCard
          id={m.id}
          name={m.name}
          href={`/movie/${m.id}`}
          facts={facts(m.year, m.runtime_min ? t('start.minuten', { n: m.runtime_min }) : null)}
        />
      {/each}
    </Row>
  {/if}

  <!-- Datenstand. Bewusst klein und ganz unten: eine Angabe zum Nachsehen,
       kein Hinweis, der jeden Besuch unterbricht. -->
  {#if standZeile}
    <footer class="fuss">
      <span title={t('start.letzterAbgleich')}>{standZeile}</span>
    </footer>
  {/if}

</main>

<!-- Rueckmeldung zum Ausblenden. Liegt ausserhalb von <main>, damit sie beim
     Scrollen unten stehen bleibt. -->
{#if hinweis}
  <div class="rueckmeldung" role="status" aria-live="polite">
    <span>{hinweis.text}</span>
    {#if hinweis.zurueck}
      <button class="mini" onclick={hinweis.zurueck} disabled={zurueckPending}>
        {t('start.rueckgaengig')}
      </button>
    {/if}
  </div>
{/if}

<ChatDock context={daily?.film_of_day?.name ?? null} />

<style>
  /* Der Hero laeuft nach unten ins Schwarz aus; die erste Reihe schiebt
     sich ein Stueck darueber, damit die Kante nicht als Linie liest.

     Der negative Rand haengt am Hero: ohne Tagesempfehlung (Backend weg oder
     leerer Kandidatenpool) gibt es nichts, worunter man sich schieben
     koennte — der Inhalt begann dann oberhalb des Bildschirms und lag unter
     der fixierten Kopfleiste. Ohne Hero traegt die Seite deren Hoehe selbst. */
  .sections {
    position: relative; z-index: 2;
    padding-top: var(--topbar-h);
    padding-bottom: var(--s-9);
  }
  .sections.mit-hero { margin-top: calc(var(--s-8) * -1); padding-top: 0; }

  .zustand { padding: var(--s-5) var(--s-7); color: var(--text-dim); font-style: italic; }
  .zustand.fehler { font-style: normal; }
  .zustand.fehler p { color: var(--danger); font-size: 14px; margin: 0 0 var(--s-4); }

  .spotlight, .zeit { margin-top: var(--s-8); }
  .spot-head {
    font-family: var(--font-ui); font-size: 17px; font-weight: 500;
    letter-spacing: 0.02em; margin: 0 0 var(--s-3); padding: 0 var(--s-7);
    text-transform: none;
  }
  /* Gleiche Beizeile wie bei Row — kursiv in Cormorant, damit Kachel- und
     Reihenform derselben Sektion nicht unterschiedlich ueberschrieben sind. */
  .spot-sub {
    font-family: var(--font-display); font-style: italic;
    font-size: 16px; font-weight: 400; color: var(--text-dim);
    margin-left: var(--s-3);
  }

  /* Obergrenze, weil auto-fit mit 1fr die Kacheln auf einem 4K-Schirm auf
     1888px gedehnt hat — zwei Zeilen Text in einer Flaeche, die breiter ist
     als mancher Monitor. Bis 760px bleibt das Verhaeltnis von Bild zu Text
     stimmig, darueber laeuft die Zeile aus. */
  .duo {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 760px));
    gap: var(--s-4); padding: 0 var(--s-7);
  }

  .zeit-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: var(--s-3); padding: 0 var(--s-7);
  }
  .mood {
    height: 92px; border-radius: var(--r-md);
    border: 1px solid rgba(255, 255, 255, 0.07);
    background: var(--layer-2); color: var(--text);
    display: flex; flex-direction: column; justify-content: center;
    padding: 0 var(--s-5); cursor: pointer; text-align: left;
    font-family: var(--font-ui);
    transition: background var(--dur-base) var(--ease),
                border-color var(--dur-base) var(--ease),
                transform var(--dur-base) var(--ease);
  }
  .mood:hover:not(:disabled) {
    background: var(--layer-3); border-color: var(--gold-ring);
    transform: translateY(-3px);
  }
  .mood.active { border-color: var(--accent); background: var(--layer-3); }
  .mood:disabled { opacity: 0.6; cursor: default; }
  .mood b {
    font-family: var(--font-display); font-size: 21px; font-weight: 600;
    color: var(--gold-bright);
  }
  .mood i { font-style: normal; font-size: 12px; color: var(--text-dim); margin-top: 3px; }

  .zeit-hint {
    font-size: 12px; color: var(--text-dim);
    margin: var(--s-3) 0 0; padding: 0 var(--s-7);
  }
  .leer {
    color: var(--text-dim); font-style: italic;
    padding: var(--s-4) var(--s-7);
  }

  .fuss {
    margin-top: var(--s-8); padding: 0 var(--s-7);
    font-size: 12px; color: var(--text-dim);
  }

  /* TMDB-Logo in der Beizeile von "Fehlt in deiner Sammlung". 10px hoch, so
     hoch wie die Versalien der 16px-Beizeile; die Unterkante sitzt auf deren
     Grundlinie, das Logo liest sich also wie ein Wort. Nur die Hoehe wird
     gesetzt: TMDB verbietet Aenderungen an Farbe und Seitenverhaeltnis, darum
     auch keine Deckkraft und kein Filter. */
  .tmdb-quelle {
    display: inline-block; vertical-align: baseline;
    margin-left: var(--s-1); border-radius: 2px;
  }
  .tmdb-quelle img { display: block; height: 10px; width: auto; }

  /* Sitzt unten links statt mittig: unten rechts klebt der Chat-Knopf. Der
     Kasten bleibt hinter ihm (z-index 150 gegen 200), und die Breite laesst
     ihm auch auf dem Handy seinen Platz. */
  .rueckmeldung {
    position: fixed; z-index: 150;
    /* border-box, weil die Deckelung sonst nur den Inhalt meint: mit 16px
       Polster links und rechts war der Kasten 34px breiter als gerechnet und
       lag auf dem Chat-Knopf. */
    box-sizing: border-box;
    left: var(--s-5); bottom: var(--s-5);
    max-width: min(440px, calc(100vw - 2 * var(--s-5) - 76px));
    display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-3);
    padding: var(--s-3) var(--s-4);
    background: var(--layer-3); border: 1px solid var(--border-strong);
    border-radius: var(--r-md); box-shadow: var(--elev-2);
    font-size: 13px; color: var(--text); line-height: 1.4;
    animation: hereinschieben var(--dur-base) var(--ease);
  }
  @keyframes hereinschieben {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: none; }
  }

  /* Sekundaere Handlungen: Goldrand statt Goldflaeche. Vollgold bleibt
     dem Abspielen-Knopf im Hero vorbehalten. */
  .mini {
    background: transparent; border: 1px solid var(--border-strong);
    color: var(--text-muted); padding: 7px 12px;
    border-radius: var(--r-sm); font-size: 12px;
    font-family: var(--font-ui); cursor: pointer; white-space: nowrap;
    transition: border-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
  }
  .mini:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .mini:disabled { opacity: 0.5; cursor: wait; }

  @media (max-width: 820px) {
    .sections.mit-hero { margin-top: calc(var(--s-6) * -1); }
    .spotlight, .zeit { margin-top: var(--s-6); }
    .spot-head, .duo, .zeit-grid, .zeit-hint, .zustand, .fuss { padding-left: var(--s-4); padding-right: var(--s-4); }
    .leer { padding-left: var(--s-4); }
    .rueckmeldung { left: var(--s-4); bottom: var(--s-4); }
    .duo { grid-template-columns: 1fr; }
  }
</style>
