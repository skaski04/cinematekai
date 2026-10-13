<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { user, backend, fetchMe, doLogout } from '$lib/auth.svelte.js';
  import { api } from '$lib/api.js';
  import SearchBar from '$lib/components/SearchBar.svelte';
  import SprachWahl from '$lib/components/SprachWahl.svelte';
  import { vorgabeUebernehmen, t } from '$lib/i18n.svelte.js';
  import '$lib/theme.css';

  let { children } = $props();
  let ready = $state(false);
  let menuOpen = $state(false);

  const istSetup = (pfad) => pfad === '/setup' || pfad.startsWith('/setup/');

  onMount(async () => {
    /* Erst fragen, ob CinematekAI ueberhaupt eingerichtet ist. Ein frischer
       Container hat weder Jellyfin noch Nutzer — dort fuehrte der Weg sonst
       auf eine Anmeldung, die gar nicht klappen kann. Schlaegt die Abfrage
       fehl (aelteres Backend, Netzfehler), bleibt alles wie bisher.
       Von /setup wird nie weggeleitet: nach Schritt 1 gilt die Einrichtung
       als erledigt, der Assistent hat aber noch vier Schritte vor sich. */
    const status = await api('/api/setup/status');
    // Vorgabe-Sprache des Admins fuer Browser ohne eigene Wahl.
    if (status.ok && status.daten?.sprache) vorgabeUebernehmen(status.daten.sprache);
    if (status.ok && status.daten?.eingerichtet === false && !istSetup($page.url.pathname)) {
      // Erst umleiten, dann rendern — sonst laedt die Startseite kurz an und
      // feuert lauter Anfragen, die alle mit 503 enden.
      await goto('/setup', { replaceState: true });
    }
    await fetchMe();
    ready = true;
    const pfad = $page.url.pathname;
    if (!user.loggedIn && !pfad.startsWith('/login') && !istSetup(pfad)) {
      goto('/login');
    }
  });

  async function logout() {
    await doLogout();
    goto('/login');
  }

  function closeMenu() { menuOpen = false; }

  /* Die Startseite beginnt mit einem randlosen Hero. Damit das Bild bis unter
     die Leiste laeuft, schwebt sie dort ueber dem Inhalt statt ihn nach unten
     zu schieben. Alle anderen Seiten behalten die feste Leiste. */
  let ueberlagernd = $derived($page.url.pathname === '/');

  /* Der Assistent zeigt wie die Anmeldung keine Leiste: nach Schritt 1 ist
     man zwar angemeldet, die Links fuehrten aber auf Seiten, die ohne
     Bibliothek leer sind — und weg vom Assistenten. */
  let ohneLeiste = $derived(istSetup($page.url.pathname));

  /* Abspann mit der Quellenangabe fuer TMDB. Pflicht nach den TMDB-API-
     Nutzungsbedingungen (Abschnitt 3 "Attribution"): TMDB-Logo plus
     Hinweis, im "About"- oder "Credits"-Bereich der Anwendung (TMDB-FAQ).
     Eine eigene Ueber-Seite gibt es nicht, also ein Abspann unter jeder
     Seite, die auch die Leiste hat.

     Immer, nicht nur bei eingetragenem TMDB-Token: weder /api/setup/status
     noch /api/app-info sagen, ob TMDB eingerichtet ist, und nach dem
     Entfernen des Tokens zeigt die Startseite die zwischengespeicherten
     TMDB-Titel weiter an.

     Nicht im Chat: der fuellt das Fenster genau bis zur Unterkante
     (100dvh minus Leiste), ein Abspann darunter braechte die Bildlaufleiste
     zurueck, die dort eigens entfernt wurde. Anmeldung und Assistent zeigen
     keine TMDB-Daten und haben schon keine Leiste. */
  let mitAbspann = $derived(user.loggedIn && !ohneLeiste && !$page.url.pathname.startsWith('/chat'));

  /* Auf der Startseite schwebt unten rechts der Chat-Knopf (ChatDock:
     60px gross, var(--s-5) vom Rand). Ganz nach unten gescrollt laege er
     sonst auf dem Hinweistext. */
  let mitChatKnopf = $derived($page.url.pathname === '/');
</script>

{#if ready}
  {#if user.loggedIn && !ohneLeiste}
    <a class="skip-link" href="#inhalt">{t('layout.zumInhalt')}</a>
    <nav class="topbar" class:ueber={ueberlagernd}>
      <a href="/" class="brand" onclick={closeMenu}>
        <img src="/cinematek_logo_128.png" alt="" class="brand-mark" />
        <span class="brand-text">CinematekAI</span>
      </a>
      <button class="hamburger" aria-label={t('layout.menue')} onclick={() => menuOpen = !menuOpen}>
        <span></span><span></span><span></span>
      </button>
      <div class="nav-right" class:open={menuOpen}>
        <a href="/" class="nav-link" class:active={$page.url.pathname === '/'} onclick={closeMenu}>{t('layout.home')}</a>
        <a href="/movies" class="nav-link" class:active={$page.url.pathname.startsWith('/movies') || $page.url.pathname.startsWith('/movie/')} onclick={closeMenu}>{t('layout.filme')}</a>
        <a href="/series" class="nav-link" class:active={$page.url.pathname.startsWith('/series')} onclick={closeMenu}>{t('layout.serien')}</a>
        <a href="/discover" class="nav-link" class:active={$page.url.pathname.startsWith('/discover')} onclick={closeMenu}>{t('layout.discover')}</a>
        <a href="/favorites" class="nav-link" class:active={$page.url.pathname.startsWith('/favorites')} onclick={closeMenu}>{t('layout.favoriten')}</a>
        <a href="/chat" class="nav-link" class:active={$page.url.pathname.startsWith('/chat')} onclick={closeMenu}>{t('layout.chat')}</a>
        {#if user.isAdmin}
          <a href="/admin" class="nav-link" class:active={$page.url.pathname.startsWith('/admin')} onclick={closeMenu}>{t('layout.admin')}</a>
        {/if}
        {#if ueberlagernd}
          <!-- Nur auf der Startseite. Filme und Serien haben ein eigenes Feld
               direkt ueber ihrer Liste, weil es dort zugleich filtert. -->
          <div class="such-slot"><SearchBar /></div>
        {/if}
        <SprachWahl kompakt />
        <span class="user-name">{user.displayName}</span>
        <button class="logout-btn" onclick={logout}>{t('layout.logout')}</button>
      </div>
    </nav>
  {/if}
  {@render children()}
  {#if mitAbspann}
    <!-- Logo: unveraenderte Datei "Alt short (blue)" von
         https://www.themoviedb.org/about/logos-attribution, lokal
         mitgeliefert wie die Schriften (keine Anfrage an fremde Server).
         12px hoch gegen 32px Bildmarke plus 24px Schriftzug oben — TMDB
         verlangt, dass ihr Logo weniger auffaellt als das der App. -->
    <footer class="abspann" class:mit-chat-knopf={mitChatKnopf} aria-labelledby="abspann-titel">
      <h2 id="abspann-titel" class="abspann-titel">{t('layout.abspann')}</h2>
      <div class="abspann-zeile">
        <a class="abspann-logo" href="https://www.themoviedb.org" target="_blank"
           rel="noopener noreferrer" aria-label={t('layout.tmdbLink')}>
          <img src="/tmdb/tmdb-logo.svg" alt="TMDB" width="92" height="12" />
        </a>
        <p class="abspann-text">
          {t('layout.tmdbHinweis')}
          {#if t('layout.tmdbHinweisOriginal')}
            <span class="abspann-original" lang="en">{t('layout.tmdbHinweisOriginal')}</span>
          {/if}
        </p>
      </div>
      {#if backend.version}
        <!-- Version des laufenden Backends (aus /api/auth/me), nicht die beim
             Bauen: nach einem Update steht hier, was wirklich läuft. In beiden
             Sprachen gleich, nur Name und Nummer. -->
        <p class="abspann-version">CinematekAI {backend.version}</p>
      {/if}
    </footer>
  {/if}
{:else}
  <div style="color:var(--text-muted); padding:40px; text-align:center; font-family:var(--font-ui)">{t('layout.laden')}</div>
{/if}

<style>
  .skip-link {
    position: absolute; left: -9999px; top: 8px; z-index: 100;
    background: var(--accent); color: var(--bg); padding: 8px 14px;
    border-radius: 2px; font-size: 14px; text-decoration: none;
  }
  .skip-link:focus { left: 8px; }

  :global(body) {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-ui);
  }
  /* Die Hoehe wird als Variable veroeffentlicht, damit Seiten, die den Rest
     des Fensters fuellen wollen (der Chat), sie nicht raten muessen. Vorher
     stand dort 52px — tatsaechlich sind es 14 + 32 + 14 + 1 = 61px. */
  :global(:root) { --topbar-h: 61px; }
  /* 900px statt 720px: bei 768px (Tablet hochkant) passte die ausgeklappte
     Leiste nicht mehr — Menue und Logout ragten 29px ueber den Rand. */
  @media (max-width: 900px) { :global(:root) { --topbar-h: 57px; } }

  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 28px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    position: relative;
  }
  .brand {
    display: flex; align-items: center; gap: 10px;
    font-family: var(--font-display);
    font-size: 24px; font-weight: 600; letter-spacing: 0.08em;
    color: var(--accent); text-decoration: none;
  }
  .brand-mark { width: 32px; height: 32px; object-fit: contain; }
  .brand-text { line-height: 1; }

  /* Ueberlagernde Variante: nur auf der Startseite. Der Verlauf haelt die
     Beschriftung lesbar, solange das Backdrop dahinter hell ist. */
  .topbar.ueber {
    position: fixed; inset: 0 0 auto 0; z-index: 100;
    background: linear-gradient(180deg, rgba(8, 8, 10, 0.92), rgba(8, 8, 10, 0));
    border-bottom: 0;
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
  }
  /* Der Farbton der Marke soll auch ueber hellen Szenen stehen. */
  .topbar.ueber .brand { text-shadow: 0 2px 10px rgba(0, 0, 0, 0.8); }
  .topbar.ueber .nav-link { text-shadow: 0 1px 6px rgba(0, 0, 0, 0.7); }

  .such-slot { margin-left: var(--s-2); }

  .nav-right { display: flex; align-items: center; gap: 18px; }
  .nav-link {
    color: var(--text-muted); text-decoration: none; font-size: 14px;
    letter-spacing: 0.03em; transition: color 0.15s;
  }
  .nav-link:hover { color: var(--accent); }
  .nav-link.active {
    color: var(--accent);
    border-bottom: 2px solid var(--accent);
    padding-bottom: 2px;
  }
  .user-name {
    color: var(--text-dim); font-size: 12px;
    font-family: var(--font-display); font-style: italic;
  }
  .logout-btn {
    background: transparent; color: var(--text-muted);
    border: 1px solid var(--border-strong);
    padding: 5px 14px; border-radius: 2px; cursor: pointer; font-size: 12px;
    letter-spacing: 0.08em; text-transform: uppercase;
    transition: all 0.15s;
  }
  .logout-btn:hover { color: var(--accent); border-color: var(--accent); }

  .hamburger {
    display: none; background: transparent; border: 0; cursor: pointer;
    width: 32px; height: 32px; padding: 6px; flex-direction: column;
    justify-content: space-between;
  }
  .hamburger span {
    display: block; height: 2px; background: var(--accent); border-radius: 1px;
  }

  /* Laptop-Breiten: Die volle Leiste (sieben Links, Suche auf der Startseite,
     Sprache, Name, Abmelden) ist breiter als das Fenster. Der Anzeigename ist
     das Entbehrlichste — er steht auch im Klappmenue — und die Abstaende
     werden enger. Gemessen am 11.10.2026: bei 1024 px lag Abmelden vorher
     275 px ausserhalb des Fensters. */
  @media (min-width: 901px) and (max-width: 1180px) {
    .nav-right { gap: 10px; }
    .topbar { padding: 14px 20px; }
    .such-slot { margin-left: 0; }
    .user-name { display: none; }
  }

  @media (max-width: 900px) {
    .topbar { padding: 12px 16px; }
    .brand { font-size: 20px; }
    .hamburger { display: flex; }
    .nav-right {
      display: none; position: absolute; top: 100%; left: 0; right: 0;
      flex-direction: column; align-items: stretch;
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 12px 16px; gap: 14px; z-index: 50;
    }
    .nav-right.open { display: flex; }
    .nav-link, .user-name, .logout-btn {
      min-height: 44px; display: flex; align-items: center;
    }
    .logout-btn { justify-content: center; }
  }

  /* Abspann. Leise wie die Datenstand-Zeile der Startseite: kleine Schrift in
     --text-dim (5,42:1 auf --bg, AA). Seitenrand wie die Reihen der
     Startseite (48px, schmal 16px), damit Logo und Text mit ihnen fluchten. */
  .abspann {
    margin-top: var(--s-5);
    padding: var(--s-5) var(--s-7) var(--s-6);
    border-top: 1px solid var(--border);
    font-family: var(--font-ui); color: var(--text-dim);
  }
  /* Zwei Klassen: gilt damit auch gegen das schmale Polster weiter unten.
     Der Knopf rueckt am Handy um den sicheren Bereich unten nach oben
     (viewport-fit=cover, ChatDock), also das Polster auch. */
  .abspann.mit-chat-knopf {
    padding-bottom: calc(60px + 2 * var(--s-5) + env(safe-area-inset-bottom, 0px));
  }
  /* Ueberschrift im Stil der Archiv-Reihen (Versalien, gesperrt), nur
     kleiner. Ueberschreibt die Serifen-Vorgabe fuer h2 aus theme.css. */
  .abspann-titel {
    margin: 0 0 var(--s-2);
    font-family: var(--font-ui); font-size: 11px; font-weight: 500;
    letter-spacing: 0.14em; text-transform: uppercase; color: var(--text-muted);
  }
  .abspann-zeile {
    display: flex; flex-wrap: wrap; align-items: flex-start;
    gap: var(--s-2) var(--s-4);
  }
  /* 6px Polster oben und unten machen die Klickflaeche 24px hoch
     (WCAG 2.5.8), ohne das Logo selbst zu vergroessern; -3px oben setzt es
     mittig auf die erste Textzeile (18px hoch). Nur die Hoehe wird gesetzt:
     Farbe und Seitenverhaeltnis darf man laut TMDB nicht aendern. */
  .abspann-logo {
    flex: none; display: block; padding: 6px 0; margin-top: -3px;
    border-radius: 2px;
  }
  .abspann-logo img { display: block; height: 12px; width: auto; }
  .abspann-text {
    flex: 1 1 260px; max-width: var(--lese);
    margin: 0; font-size: 12px; line-height: 1.5;
  }
  .abspann-original { display: block; margin-top: 2px; font-size: 11px; font-style: italic; }
  /* Noch leiser als der Hinweis darüber: eine Zeile für Fehlerberichte, kein
     Inhalt. Dieselbe Farbe, damit der Kontrast (AA) erhalten bleibt. */
  .abspann-version {
    margin: var(--s-3) 0 0;
    font-size: 11px; letter-spacing: 0.04em;
  }

  /* viewport-fit=cover (app.html): ohne den sicheren Bereich laege die
     letzte Zeile auf dem iPhone unter dem Home-Balken. */
  @media (max-width: 820px) {
    .abspann {
      padding: var(--s-4) var(--s-4) calc(var(--s-5) + env(safe-area-inset-bottom, 0px));
    }
  }
</style>
