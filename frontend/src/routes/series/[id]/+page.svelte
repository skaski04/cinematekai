<script>
  import { page } from '$app/stores';
  import { t, genre } from '$lib/i18n.svelte.js';

  let detail = $state(null);
  let episodes = $state([]);
  let episodesLoaded = $state(false);
  let showEpisodes = $state(false);
  let loading = $state(true);
  let error = $state('');
  let favPending = $state(false);
  let isFav = $state(false);
  let toast = $state('');
  let toastTimer = null;

  let id = $derived($page.params.id);

  /* Zaehlt jeden Seitenaufruf. Wechselt man von Serie zu Serie, koennen die
     Abfragen der vorigen spaeter zurueckkommen — ohne diesen Token blieb die
     Episodenliste dann beim Vorgaenger stehen. */
  let lauf = 0;

  async function loadDetail(sid, meins) {
    loading = true; error = '';
    try {
      const r = await fetch(`/api/series/${sid}/detail`, { credentials: 'include' });
      if (r.status === 401) { window.location.href = '/login'; return; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      if (meins !== lauf) return;
      detail = d;
      isFav = !!detail?.progress?.is_favorite;
    } catch (e) {
      if (meins !== lauf) return;
      error = String(e);
    } finally {
      if (meins === lauf) loading = false;
    }
  }

  async function loadEpisodes() {
    if (episodesLoaded) { showEpisodes = !showEpisodes; return; }
    const meins = lauf;
    try {
      const r = await fetch(`/api/series/${id}/episodes`, { credentials: 'include' });
      if (r.ok) {
        const d = await r.json();
        if (meins !== lauf) return;
        episodes = d.items || [];
        episodesLoaded = true;
        showEpisodes = true;
      }
    } catch {}
  }

  function showToast(msg) {
    toast = msg;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast = ''; }, 2400);
  }

  async function toggleFav() {
    if (favPending) return;
    favPending = true;
    const next = !isFav;
    try {
      const r = await fetch(`/api/series/${id}/favorite`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ favorite: next }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      isFav = next;
      if (!data.jellyfin_synced) showToast(t('serie.lokalGespeichert'));
      else showToast(next ? t('serie.favoritGesetzt') : t('serie.favoritEntfernt'));
    } catch (e) {
      showToast(t('serie.fehlerText', { text: e?.message || e }));
    } finally {
      favPending = false;
    }
  }

  /* Der einzige Ladeweg: onMount und dieses $effect haben beim Aufruf
     dieselben Daten doppelt geholt. Beim Wechsel raeumt es die Episoden der
     vorigen Serie weg, damit unten nichts Fremdes stehen bleibt. */
  $effect(() => {
    const sid = id;
    if (!sid) return;
    const meins = ++lauf;
    episodes = []; episodesLoaded = false; showEpisodes = false;
    loadDetail(sid, meins);
  });

  function scoreColor(score) {
    if (score >= 75) return '#4caf50';
    if (score >= 50) return 'var(--accent)';
    if (score >= 30) return '#e09048';
    return '#a05050';
  }

  const R = 52;
  const CIRC = 2 * Math.PI * R;

  function yearLabel(s) {
    if (!s.year) return '';
    if (s.status === 'ended' && s.end_year && s.end_year !== s.year) return `${s.year}–${s.end_year}`;
    if (s.status === 'continuing') return t('serie.laufend', { jahr: s.year });
    return `${s.year}`;
  }

  // Remaining unwatched runtime (sum of unplayed episodes' length).
  // If < 2h AND series is "continuing" → suggest downloading next season.
  // If < 2h AND series is "ended" but no more local eps → just inform.
  const remaining = $derived.by(() => {
    if (!episodes.length) return null;
    let unwatched = 0, mins = 0, total = 0;
    for (const e of episodes) {
      total++;
      if (!e.played) {
        unwatched++;
        if (e.runtime_min) mins += e.runtime_min;
      }
    }
    return { unwatched, mins, total };
  });

  // Auto-load episodes once for the banner — even if collapsed.
  $effect(() => {
    if (detail && !episodesLoaded) {
      const meins = lauf;
      fetch(`/api/series/${id}/episodes`, { credentials: 'include' })
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (d && meins === lauf) { episodes = d.items || []; episodesLoaded = true; }
        })
        .catch(() => {});
    }
  });

  // Group episodes by season
  const grouped = $derived.by(() => {
    const m = new Map();
    for (const e of episodes) {
      const k = e.season ?? 0;
      if (!m.has(k)) m.set(k, []);
      m.get(k).push(e);
    }
    for (const arr of m.values()) arr.sort((a, b) => (a.episode ?? 0) - (b.episode ?? 0));
    // Staffel 0 (Specials) ans Ende — sonst scrollt man erst an Bonusmaterial vorbei.
    return [...m.entries()].sort((a, b) => (a[0] === 0) - (b[0] === 0) || a[0] - b[0]);
  });
</script>

<svelte:head><title>{t('serie.seitentitel', { name: detail?.series?.name ?? t('serie.serie') })}</title></svelte:head>

<div class="series" id="inhalt">
  <a class="back" href="/series">{t('serie.zurueck')}</a>

  {#if loading}
    <div class="loading">{t('serie.lade')}</div>
  {:else if error}
    <div class="err">{t('serie.fehlerText', { text: error })}</div>
  {:else if detail}
    <div class="backdrop" style="background-image: url(/api/image/{detail.series.id}?type=Backdrop)">
      <div class="backdrop-fade"></div>
    </div>

    <div class="content">
      <div class="poster" style="background-image: url(/api/image/{detail.series.id})"></div>

      <div class="info">
        <h1>{detail.series.name}</h1>
        <div class="meta">
          {yearLabel(detail.series)}
          {#if detail.series.season_count}<span class="dot">·</span>{t('serie.staffeln', { n: detail.series.season_count })}{/if}
          {#if detail.series.episode_count}<span class="dot">·</span>{t('serie.folgen', { n: detail.series.episode_count })}{/if}
          {#if detail.series.special_count}<span class="dot">·</span>{t('serie.extras', { n: detail.series.special_count })}{/if}
          {#if detail.series.runtime_min_avg}<span class="dot">·</span>{t('serie.minProFolge', { n: detail.series.runtime_min_avg })}{/if}
          {#if detail.series.rating}<span class="dot">·</span>★ {detail.series.rating.toFixed(1)}{/if}
        </div>

        {#if detail.tagline}<p class="tagline">{detail.tagline}</p>{/if}

        <div class="reason-row">
          <div class="score-ring" title={t('serie.matchScore')}>
            <svg viewBox="0 0 120 120">
              <circle cx="60" cy="60" r={R} stroke="rgba(200,169,110,0.18)" stroke-width="8" fill="none" />
              <circle cx="60" cy="60" r={R}
                stroke={scoreColor(detail.match_score)} stroke-width="8" fill="none"
                stroke-dasharray={CIRC}
                stroke-dashoffset={CIRC * (1 - detail.match_score / 100)}
                stroke-linecap="round"
                transform="rotate(-90 60 60)" />
              <text x="60" y="68" text-anchor="middle"
                font-family="var(--font-display)" font-size="32" font-weight="600"
                fill={scoreColor(detail.match_score)}>{detail.match_score}</text>
            </svg>
          </div>
          <div class="reason">
            <div class="reason-label">{t('serie.warum')}</div>
            <div class="reason-text">{detail.match_reason}</div>
          </div>
        </div>

        {#if detail.series.genres?.length}
          <div class="chips">
            {#each detail.series.genres as g}
              <a class="chip" href={`/series?genre=${encodeURIComponent(g)}`}>{genre(g)}</a>
            {/each}
          </div>
        {/if}

        {#if detail.series.creators?.length}
          <div class="line"><em>{t('serie.showrunner')}</em> {detail.series.creators.join(', ')}</div>
        {/if}
        {#if detail.series.actors?.length}
          <div class="line"><em>{t('serie.besetzung')}</em> {detail.series.actors.slice(0, 6).join(', ')}</div>
        {/if}

        {#if detail.series.overview}
          <p class="overview">{detail.series.overview}</p>
        {/if}

        {#if remaining && remaining.unwatched > 0 && remaining.mins > 0 && remaining.mins < 120}
          <div class="banner">
            {#if detail.series.status === 'continuing'}
              <span>
                {t('serie.restVor')} <strong>{t('serie.restFolgen', { n: remaining.unwatched, min: remaining.mins })}</strong>{t('serie.restNach')}
                {t('serie.naechsteStaffel')}
              </span>
            {:else}
              <span>
                {t('serie.fastDurchVor')} <strong>{t('serie.restFolgen', { n: remaining.unwatched, min: remaining.mins })}</strong>{t('serie.fastDurchNach')}
              </span>
            {/if}
          </div>
        {:else if remaining && remaining.unwatched === 0 && remaining.total > 0}
          <div class="banner done">
            <span class="banner-icon">✓</span>
            <span>{t('serie.alleGesehen')}</span>
          </div>
        {/if}

        {#if detail.progress?.state === 'watching' && detail.progress?.next_up_label}
          <div class="next-up-banner">
            <div class="nu-label">{t('serie.alsNaechstes')}</div>
            <div class="nu-title">{detail.progress.next_up_label}</div>
            <div class="nu-meta">
              {t('serie.geschaut', { gesehen: detail.progress.episodes_watched, gesamt: detail.progress.total_episodes })}
            </div>
          </div>
        {:else if detail.progress?.state === 'completed'}
          <div class="next-up-banner done">
            <div class="nu-label">{t('serie.komplett')}</div>
            <div class="nu-meta">{t('serie.abgeschlossen', { n: detail.progress.total_episodes })}</div>
          </div>
        {/if}

        <div class="cta-row">
          {#if detail.progress?.next_up_play_url}
            <a class="play" href={detail.progress.next_up_play_url} target="_blank" rel="noopener">
              {t('serie.starten', { label: detail.progress.next_up_label })}
            </a>
          {:else}
            <a class="play" href={detail.play_url} target="_blank" rel="noopener">
              {t('serie.inJellyfin')}
            </a>
          {/if}
          <button class="fav" class:on={isFav} onclick={toggleFav} disabled={favPending}>
            {isFav ? t('serie.favoritAn') : t('serie.favoritAus')}
          </button>
          <button class="ep-toggle" onclick={loadEpisodes}>
            {showEpisodes ? t('serie.episodenAusblenden') : t('serie.episodenAnzeigen')}
          </button>
        </div>

        {#if showEpisodes}
          <section class="episodes">
            {#if episodes.length === 0}
              <div class="ep-empty">{t('serie.keineEpisoden')}</div>
            {:else}
              {#each grouped as [seasonNum, eps]}
                <details open={seasonNum !== 0} class="season">
                  <summary>
                    {seasonNum === 0
                      ? t('serie.extrasStaffel', { n: eps.length })
                      : t('serie.staffel', { nr: seasonNum, n: eps.length })}
                  </summary>
                  <div class="ep-list">
                    {#each eps as ep}
                      <div class="ep" class:played={ep.played}>
                        <span class="ep-num">S{seasonNum}E{ep.episode ?? '?'}</span>
                        <span class="ep-name">{ep.name}</span>
                        {#if ep.runtime_min}<span class="ep-time">{ep.runtime_min} min</span>{/if}
                        {#if ep.played}<span class="ep-check">✓</span>{/if}
                      </div>
                    {/each}
                  </div>
                </details>
              {/each}
            {/if}
          </section>
        {/if}
      </div>
    </div>
  {/if}

  {#if toast}<div class="toast">{toast}</div>{/if}
</div>

<style>
  .series {
    color: var(--text); font-family: var(--font-ui);
    min-height: 100vh; position: relative;
  }
  .back {
    position: absolute; top: 16px; left: 20px; z-index: 10;
    background: rgba(10,10,12,0.7); color: var(--accent);
    text-decoration: none; padding: 8px 14px; border-radius: 2px;
    border: 1px solid var(--border-strong); font-size: 13px;
    backdrop-filter: blur(6px);
  }
  .back:hover { background: var(--surface); }
  .loading, .err {
    padding: 80px 20px; text-align: center; color: var(--text-muted);
  }

  /* Gleiche Masse wie auf der Filmseite, damit Serie und Film nicht
     unterschiedlich hoch einsteigen. */
  .backdrop {
    height: 62vh; min-height: 460px; max-height: 720px;
    background-size: cover; background-position: center 25%;
    position: relative;
  }
  .backdrop-fade {
    position: absolute; inset: 0;
    background: linear-gradient(to bottom, rgba(10,10,12,0.35) 0%,
                                rgba(10,10,12,0.05) 30%,
                                rgba(10,10,12,0.75) 72%, var(--bg) 100%),
                linear-gradient(to right, rgba(10,10,12,0.8) 0%,
                                rgba(10,10,12,0.3) 42%, transparent 72%);
  }

  /* Dieselben Masse wie auf der Filmdetailseite: waechst mit dem Schirm,
     steht mittig, gedeckelt wird der Inhalt (--lese, --kasten) und nicht die
     Spalte. Vorher endeten beide Seiten bei festen 1200px — auf 3840px
     blieben zwei Drittel leer. */
  .content {
    box-sizing: border-box; width: 100%; max-width: var(--seite-max);
    margin: -220px auto 0; padding: 0 var(--seite-pad) 60px;
    position: relative; display: grid;
    grid-template-columns: var(--spalte-poster) minmax(0, 1fr);
    gap: clamp(20px, 2.2vw, 48px); align-items: start;
  }
  .poster {
    width: 100%; aspect-ratio: 2/3; background-size: cover;
    background-position: center; border-radius: 4px;
    box-shadow: 0 12px 40px rgba(0,0,0,0.6);
    border: 1px solid var(--border-strong);
  }
  .info { padding-top: 12px; }
  h1 {
    font-family: var(--font-display); font-weight: 600;
    font-size: 44px; line-height: 1.1; margin: 0 0 8px;
  }
  .meta { color: var(--text-muted); font-size: 14px; margin-bottom: 14px; }
  .meta .dot { opacity: 0.5; margin: 0 6px; }
  .tagline {
    font-family: var(--font-display); font-style: italic;
    color: var(--accent); font-size: 18px; margin: 4px 0 22px;
    max-width: var(--lese);
  }

  .reason-row {
    display: flex; gap: 18px; align-items: center;
    background: var(--surface); border: 1px solid var(--border);
    padding: 16px; border-radius: 4px; margin-bottom: 18px;
    /* Gedeckelt, damit die Begruendung darin ihre Lesebreite behaelt. */
    max-width: min(100%, var(--kasten));
  }
  .score-ring { width: 96px; height: 96px; flex-shrink: 0; }
  .score-ring svg { width: 100%; height: 100%; }
  .reason-label {
    font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--text-dim); margin-bottom: 4px;
  }
  /* Fliesstext, also dieselbe Lesebreite wie die Handlung. */
  .reason-text {
    color: var(--text); font-size: 15px; line-height: 1.5;
    max-width: var(--lese);
  }

  .chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; }
  .chip {
    background: var(--accent-subtle); color: var(--accent);
    border: 1px solid var(--border); padding: 3px 12px;
    border-radius: 2px; font-size: 11px;
    letter-spacing: 0.05em; text-transform: uppercase;
    text-decoration: none; cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }
  .chip:hover {
    background: rgba(200,169,110,0.18); border-color: var(--accent);
  }
  .line {
    color: var(--text-muted); font-size: 13px; line-height: 1.5; margin-bottom: 4px;
    max-width: var(--lese);
  }
  .line em {
    font-style: normal; color: var(--text-dim);
    text-transform: uppercase; font-size: 11px;
    letter-spacing: 0.08em; margin-right: 6px;
  }
  /* 760px waren bei 15px knapp 100 Zeichen je Zeile. Jetzt rechnet die
     Breite in Zeichen statt in Pixeln — gleiche Regel wie beim Film. */
  .overview {
    color: var(--text); font-size: 15px; line-height: 1.6;
    margin: 14px 0 22px; max-width: var(--lese);
  }

  .banner {
    display: flex; align-items: center; gap: 12px;
    background: rgba(200,169,110,0.1);
    border: 1px solid var(--accent-muted);
    color: var(--text);
    padding: 12px 16px; border-radius: 4px;
    margin-bottom: 16px; font-size: 14px; line-height: 1.5;
    max-width: min(100%, var(--kasten));
  }
  .banner.done { background: rgba(76,175,80,0.08); border-color: #4caf50aa; }

  .next-up-banner {
    background: linear-gradient(135deg, rgba(200,169,110,0.1), rgba(200,169,110,0.04));
    border: 1px solid var(--accent-muted);
    border-radius: 4px; padding: 12px 16px; margin-bottom: 14px;
    max-width: min(100%, var(--kasten));
  }
  .next-up-banner.done {
    background: rgba(76,175,80,0.06); border-color: #4caf5066;
  }
  .nu-label {
    font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--text-dim); margin-bottom: 4px;
  }
  .nu-title {
    font-family: var(--font-display); font-weight: 600;
    font-size: 22px; color: var(--accent); margin-bottom: 4px;
  }
  .nu-meta { color: var(--text-muted); font-size: 13px; }
  .banner strong { color: var(--accent); font-weight: 600; }

  .cta-row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }
  /* Der eine Vollgold-Knopf, gleiche Form wie auf der Filmseite. */
  .play {
    display: inline-flex; align-items: center; gap: 10px;
    background: var(--accent); color: var(--bg);
    text-decoration: none; padding: 14px 30px; border-radius: var(--r-md);
    font-family: var(--font-ui); font-weight: 700;
    letter-spacing: 0.06em; font-size: 15px;
    box-shadow: 0 6px 20px rgba(200, 169, 110, 0.28);
    transition: background var(--dur-base) var(--ease),
                transform var(--dur-base) var(--ease),
                box-shadow var(--dur-base) var(--ease);
  }
  .play:hover {
    background: var(--gold-bright); transform: translateY(-1px);
    box-shadow: 0 10px 28px rgba(200, 169, 110, 0.38);
  }
  .fav, .ep-toggle {
    background: transparent; color: var(--text-muted);
    border: 1px solid var(--border-strong);
    padding: 11px 22px; border-radius: 2px; cursor: pointer;
    font-family: var(--font-ui); font-size: 14px;
    letter-spacing: 0.04em; transition: all 0.15s;
  }
  .fav:hover, .ep-toggle:hover { color: var(--accent); border-color: var(--accent); }
  .fav.on { color: var(--accent); border-color: var(--accent); background: var(--accent-subtle); }

  /* Wie die Kaesten gedeckelt: eine Folgenliste, bei der zwischen Titel und
     Laufzeit ein Meter Leere liegt, liest sich nicht mehr in einer Zeile. */
  .episodes { margin-top: 18px; max-width: min(100%, var(--kasten)); }
  .ep-empty { color: var(--text-dim); font-style: italic; padding: 12px 0; }
  .season {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 4px; margin-bottom: 8px;
  }
  .season summary {
    padding: 12px 16px; cursor: pointer;
    font-family: var(--font-display); color: var(--accent);
    letter-spacing: 0.04em;
  }
  .ep-list { padding: 0 16px 12px; }
  .ep {
    display: grid;
    grid-template-columns: 70px 1fr auto auto;
    gap: 12px; align-items: center;
    padding: 8px 0; border-top: 1px solid var(--border);
    color: var(--text);
  }
  .ep.played .ep-name { color: var(--text-dim); }
  .ep-num {
    font-family: monospace; color: var(--text-dim); font-size: 12px;
  }
  .ep-name { font-size: 14px; }
  .ep-time { color: var(--text-dim); font-size: 12px; }
  .ep-check { color: var(--accent); font-size: 14px; }

  .toast {
    position: fixed; bottom: 24px; left: 50%;
    transform: translateX(-50%);
    background: var(--surface-light);
    border: 1px solid var(--accent-muted);
    color: var(--text);
    padding: 12px 20px; border-radius: 4px;
    font-size: 14px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.4);
    z-index: 100;
  }

  @media (max-width: 720px) {
    /* Rein vertikaler Verlauf wie auf der Filmseite — der seitliche wuerde
       auf Handybreite das halbe Bild abdunkeln. */
    .backdrop { height: 42vh; min-height: 280px; }
    .backdrop-fade {
      background: linear-gradient(to bottom, rgba(10,10,12,0.4) 0%,
                                  rgba(10,10,12,0.1) 26%,
                                  rgba(10,10,12,0.82) 66%, var(--bg) 100%);
    }
    .content {
      grid-template-columns: 1fr; gap: 20px;
      margin-top: -80px; padding: 0 14px 40px;
    }
    .poster {
      width: 140px; height: 210px;
      margin: 0 auto;
    }
    h1 { font-size: 30px; text-align: center; }
    .meta, .tagline, .line { text-align: center; }
    .reason-row { flex-direction: column; text-align: center; }
    .cta-row { justify-content: center; }
    .ep { grid-template-columns: 60px 1fr auto; }
    .ep-time { display: none; }
  }
</style>
