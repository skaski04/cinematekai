<script>
  import { goto } from '$app/navigation';
  import { fetchMe } from '$lib/auth.svelte.js';
  import { t, sprache, sprachWaehlen } from '$lib/i18n.svelte.js';
  import SprachWahl from '$lib/components/SprachWahl.svelte';

  let username = $state('');
  let password = $state('');
  let error = $state('');
  let loading = $state(false);

  /* Wer schon hier umschaltet, meint es: nach der Anmeldung wird die Wahl
     beim Nutzer gespeichert. Sonst holte fetchMe() eine frueher gespeicherte
     Sprache zurueck und die Seite spraeche ploetzlich wieder die andere.
     Die Vorgabe des Admins ist zu diesem Zeitpunkt schon angewandt (das
     Layout rendert die Seite erst danach), jede Abweichung kommt also vom
     Umschalter. */
  const startSprache = sprache.code;

  async function submit(e) {
    e.preventDefault();
    error = ''; loading = true;
    try {
      const r = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        error = d.detail || t('allgemein.fehlerStatus', { status: r.status });
        return;
      }
      if (sprache.code !== startSprache) {
        await sprachWaehlen(sprache.code, { beimNutzer: true });
      }
      await fetchMe();
      goto('/');
    } catch {
      error = t('allgemein.keineVerbindung');
    } finally {
      loading = false;
    }
  }
</script>

<div class="login-page">
  <div class="login-card">
    <div class="sprache"><SprachWahl kompakt /></div>
    <img src="/cinematek_banner.png" alt="CinematekAI" class="banner" />
    <p class="subtitle">{t('login.untertitel')}</p>
    <form onsubmit={submit}>
      <input type="text" placeholder={t('login.benutzername')} bind:value={username} required autocomplete="username" />
      <input type="password" placeholder={t('login.passwort')} bind:value={password} required autocomplete="current-password" />
      {#if error}<div class="error">{error}</div>{/if}
      <button type="submit" disabled={loading}>{loading ? t('login.meldetAn') : t('login.anmelden')}</button>
    </form>
  </div>
</div>

<style>
  .login-page {
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; background: var(--bg);
    padding: 20px;
  }
  .login-card {
    background: var(--surface);
    padding: 40px 36px;
    border: 1px solid var(--border);
    border-radius: 2px;
    width: 100%; max-width: 400px;
    box-shadow: 0 20px 60px #0009;
  }
  /* Oben rechts in der Karte, im Fluss statt absolut: so liegt der
     Umschalter auch auf niedrigen Bildschirmen nie ueber dem Banner. */
  .sprache {
    display: flex; justify-content: flex-end;
    margin: -16px -12px 4px 0;
  }
  .banner {
    display: block; width: 100%; max-width: 280px;
    margin: 0 auto 10px; height: auto;
  }
  .subtitle {
    color: var(--text-muted); font-size: 13px;
    margin: 0 0 28px; text-align: center;
    font-family: var(--font-display); font-style: italic;
    letter-spacing: 0.02em;
  }
  input {
    display: block; width: 100%; padding: 13px 14px; margin: 0 0 12px;
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border-strong);
    border-radius: 2px; font-size: 15px;
    box-sizing: border-box; font-family: var(--font-ui);
    transition: border-color 0.15s;
  }
  input:focus { outline: none; border-color: var(--accent); }
  .error { color: var(--danger); font-size: 13px; margin: 0 0 12px; }
  button {
    width: 100%; padding: 13px;
    background: var(--accent); color: var(--bg);
    border: 0; border-radius: 2px;
    font-size: 14px; font-weight: 600;
    letter-spacing: 0.12em; text-transform: uppercase;
    cursor: pointer; transition: background 0.15s;
    margin-top: 6px;
  }
  button:hover:not(:disabled) { background: var(--text); }
  button:disabled { opacity: 0.5; cursor: wait; }

  @media (max-width: 720px) {
    .login-card { padding: 28px 22px; }
    .sprache { margin: -10px -8px 4px 0; }
    .banner { max-width: 240px; }
  }
</style>
