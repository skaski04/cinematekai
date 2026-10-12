<script>
  /* Unternavigation des Admin-Bereichs. Eine eigene Komponente, weil sie auf
     beiden Admin-Seiten genau gleich aussehen muss — stuende sie zweimal im
     Markup, liefe sie beim naechsten Umbau auseinander.
     aria-current statt nur einer Farbe: ein Screenreader sagt dann, auf
     welcher der beiden Seiten man gerade ist. */
  import { page } from '$app/stores';
  import { t } from '$lib/i18n.svelte.js';

  // $derived, damit die Beschriftungen beim Sprachwechsel mitgehen.
  const BEREICHE = $derived([
    { href: '/admin', text: t('layout.adminBenutzerJobs') },
    { href: '/admin/einstellungen', text: t('layout.adminEinstellungen') },
  ]);
</script>

<nav class="unter" aria-label={t('layout.adminBereiche')}>
  {#each BEREICHE as b (b.href)}
    {@const aktiv = $page.url.pathname === b.href}
    <a href={b.href} class:aktiv aria-current={aktiv ? 'page' : undefined}>{b.text}</a>
  {/each}
</nav>

<style>
  .unter {
    display: flex;
    gap: var(--s-1);
    border-bottom: 1px solid var(--border);
    margin: 0 0 var(--s-5);
    /* Zwei kurze Eintraege passen auch auf 320px; falls nicht, umbrechen
       statt die Seite seitlich scrollen zu lassen. */
    flex-wrap: wrap;
  }
  a {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 0 var(--s-4);
    color: var(--text-muted);
    text-decoration: none;
    font-size: 14px;
    letter-spacing: 0.03em;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    transition: color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease);
  }
  a:hover { color: var(--accent); }
  a.aktiv {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }
  @media (max-width: 560px) {
    a { flex: 1 1 auto; justify-content: center; padding: 0 var(--s-2); }
  }
</style>
