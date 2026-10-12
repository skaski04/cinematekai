<script>
  /* Jellyfin-Verbindung fuer Admins mit gueltiger Sitzung (Einstellungsseite).
     Die Ersteinrichtung laeuft anders — dort meldet sich ein Jellyfin-Admin
     mit Benutzername und Passwort an, und das Backend legt den API-Schluessel
     selbst an (siehe routes/setup).

     Bewusst NICHT moeglich: Adresse oder Schluessel leeren. Ohne beides
     gilt CinematekAI als nicht eingerichtet, die Oberflaeche leitet dann auf
     /setup — und dort ist die Einrichtung gesperrt, sobald es Nutzer gibt.
     Ein Klick haette die Installation fuer alle unbenutzbar gemacht. */
  import { onMount } from 'svelte';
  import { api, adresse, appInfoVergessen } from '$lib/api.js';
  import { t } from '$lib/i18n.svelte.js';
  import '$lib/formular.css';

  let {
    werte = $bindable(),       // maskierter Abschnitt "jellyfin" aus /api/admin/settings
    ongespeichert = () => {},
  } = $props();

  const uid = $props.id();

  let e = $state({ url: '', public_url: '', user_id: '' });
  let schluessel = $state('');
  let konten = $state([]);
  let kontenInfo = $state(null);
  let kontenLaeuft = $state(false);
  let test = $state(null);
  let meldung = $state(null);
  let laeuft = $state('');          // '' | 'test' | 'speichern'

  /* Meldungen: { art, k, w } (Schluessel im Woerterbuch, erst beim Anzeigen
     uebersetzt — so wechseln sie mit der Sprache), { art, f } (Funktion fuer
     zusammengesetzte Texte) oder { art, text } (Text vom Backend). */
  function txt(m) {
    if (!m) return '';
    if (m.f) return m.f();
    if (m.k) return t(m.k, m.w);
    return m.text ?? '';
  }

  $effect.pre(() => {
    const w = werte ?? {};
    e = { url: w.url ?? '', public_url: w.public_url ?? '', user_id: w.user_id ?? '' };
    schluessel = '';
  });

  const basis = $derived(werte ?? {});
  /* Ein gespeichertes Konto, das Jellyfin nicht (mehr) meldet, bleibt als
     eigene Option stehen — sonst zeigte die Auswahl stillschweigend das
     erste Konto, und ein Speichern-Klick wechselte die Bibliothek. */
  const kontoUnbekannt = $derived(!!e.user_id && !konten.some((k) => k.id === e.user_id));

  async function kontenLaden() {
    kontenLaeuft = true;
    const r = await api('/api/admin/jellyfin/nutzer');
    kontenLaeuft = false;
    if (!r.ok) {
      konten = [];
      kontenInfo = { art: 'fehler', k: 'formular.kontenNichtAbrufbar', w: { fehler: r.fehler } };
      return;
    }
    konten = Array.isArray(r.daten) ? r.daten : (r.daten?.nutzer ?? []);
    kontenInfo = konten.length ? null : { art: 'info', k: 'formular.keineKonten' };
  }

  onMount(kontenLaden);

  function mitSchema(u) {
    const a = adresse(u);
    return a && !/^https?:\/\//i.test(a) ? `http://${a}` : a;
  }

  async function testen() {
    const url = mitSchema(e.url);
    if (!url) { test = { art: 'fehler', k: 'formular.zuerstAdresse' }; return; }
    e.url = url;
    laeuft = 'test';
    test = { art: 'info', k: 'formular.teste' };
    const r = await api('/api/admin/settings/testen', {
      method: 'POST', body: { abschnitt: 'jellyfin', url, api_key: schluessel.trim() } });
    laeuft = '';
    if (!r.ok) { test = { art: 'fehler', text: r.fehler }; return; }
    const d = r.daten ?? {};
    if (!d.ok) {
      test = d.fehler ? { art: 'fehler', text: d.fehler } : { art: 'fehler', k: 'formular.testFehlgeschlagen' };
      return;
    }
    test = {
      art: 'ok',
      f: () => {
        const teile = [t('formular.ok')];
        if (d.server_name) teile.push(d.server_name);
        if (d.version) teile.push(`Jellyfin ${d.version}`);
        if (d.nutzer_anzahl != null) teile.push(t('formular.konten', { n: d.nutzer_anzahl }));
        return teile.join(' · ');
      },
    };
  }

  async function speichern(ev) {
    ev.preventDefault();
    meldung = null;
    const url = mitSchema(e.url);
    const oeffentlich = mitSchema(e.public_url);
    if (!url) { meldung = { art: 'fehler', k: 'formular.ohneAdresse' }; return; }
    if (!basis.api_key_gesetzt && !schluessel.trim()) {
      meldung = { art: 'fehler', k: 'formular.schluesselFehlt' };
      return;
    }
    if (!e.user_id) { meldung = { art: 'fehler', k: 'formular.kontoWaehlen' }; return; }
    e.url = url;
    e.public_url = oeffentlich;

    const neu = {};
    if (url !== adresse(basis.url)) neu.url = url;
    if (oeffentlich !== adresse(basis.public_url)) neu.public_url = oeffentlich;
    if (e.user_id !== (basis.user_id ?? '')) neu.user_id = e.user_id;
    if (schluessel.trim()) neu.api_key = schluessel.trim();
    if (!Object.keys(neu).length) { meldung = { art: 'info', k: 'formular.keineAenderungen' }; return; }

    laeuft = 'speichern';
    const r = await api('/api/admin/settings/jellyfin', { method: 'PUT', body: neu });
    laeuft = '';
    if (!r.ok) { meldung = { art: 'fehler', text: r.fehler }; return; }
    werte = r.daten.abschnitt;
    // Die oeffentliche Adresse steckt in /api/app-info, das die Seiten merken.
    appInfoVergessen();
    meldung = { art: 'ok', k: 'formular.gespeichert' };
    // Neue Adresse oder neuer Schluessel: die Kontenliste kommt von dort.
    if ('url' in neu || 'api_key' in neu) kontenLaden();
    ongespeichert(r.daten);
  }
</script>

<form class="jf" onsubmit={speichern} novalidate>
  <div class="e-raster">
    <div class="e-feld">
      <label for="{uid}-url">{t('formular.adresse')}</label>
      <input id="{uid}-url" type="text" inputmode="url" autocomplete="off" autocapitalize="off"
             spellcheck="false" bind:value={e.url} placeholder="http://192.168.1.10:8096"
             aria-describedby="{uid}-url-hilfe" />
      <p class="e-klein" id="{uid}-url-hilfe">{t('formular.adresseHilfe')}</p>
    </div>
    <div class="e-feld">
      <label for="{uid}-pub">{t('formular.oeffentlich')}</label>
      <input id="{uid}-pub" type="text" inputmode="url" autocomplete="off" autocapitalize="off"
             spellcheck="false" bind:value={e.public_url} placeholder={t('formular.oeffentlichBeispiel')}
             aria-describedby="{uid}-pub-hilfe" />
      <p class="e-klein" id="{uid}-pub-hilfe">{t('formular.oeffentlichHilfe')}</p>
    </div>

    <div class="e-feld breit">
      <label for="{uid}-key">{t('formular.apiSchluessel')}</label>
      <input id="{uid}-key" type="password" autocomplete="new-password" spellcheck="false"
             bind:value={schluessel} aria-describedby="{uid}-key-stand"
             placeholder={basis.api_key_gesetzt ? t('formular.leerBehalten') : t('formular.schluesselEintragen')} />
      <div class="stand" id="{uid}-key-stand">
        {#if basis.api_key_gesetzt}
          <span class="e-gesetzt">✓&#xFE0E; {t('formular.gesetzt')}{basis.api_key_ende ? ` (…${basis.api_key_ende})` : ''}</span>
        {:else}
          <span class="e-gesetzt leer">{t('formular.keinSchluessel')}</span>
        {/if}
      </div>
    </div>

    <div class="e-feld breit">
      <label for="{uid}-konto">{t('formular.bibliothekskonto')}</label>
      <div class="konto-zeile">
        <select id="{uid}-konto" bind:value={e.user_id} aria-describedby="{uid}-konto-hilfe {uid}-konto-info">
          {#if !e.user_id}<option value="">{t('formular.bitteWaehlen')}</option>{/if}
          {#each konten as k (k.id)}
            <option value={k.id}>{k.name}{k.is_admin ? ' (Admin)' : ''}</option>
          {/each}
          {#if kontoUnbekannt}
            <option value={e.user_id}>{t('formular.gespeichertesKonto', { id: e.user_id.slice(0, 8) })}</option>
          {/if}
        </select>
        <button type="button" class="e-knopf" class:laeuft={kontenLaeuft} onclick={kontenLaden}
                disabled={kontenLaeuft}>
          {kontenLaeuft ? t('allgemein.laden') : t('formular.neuLaden')}
        </button>
      </div>
      <p class="e-klein" id="{uid}-konto-hilfe">
        {t('formular.kontoHilfe')}
      </p>
      <p class="e-meldung {kontenInfo?.art ?? ''}" id="{uid}-konto-info" aria-live="polite">{txt(kontenInfo)}</p>
    </div>
  </div>

  <div class="e-knoepfe">
    <button type="button" class="e-knopf" class:laeuft={laeuft === 'test'} onclick={testen}
            disabled={!!laeuft}>
      {laeuft === 'test' ? t('formular.teste') : t('formular.verbindungTesten')}
    </button>
    <button type="submit" class="e-knopf primaer" class:laeuft={laeuft === 'speichern'}
            disabled={!!laeuft}>
      {laeuft === 'speichern' ? t('formular.speichere') : t('allgemein.speichern')}
    </button>
  </div>
  <p class="e-meldung {test?.art ?? ''}" aria-live="polite">{txt(test)}</p>
  <p class="e-meldung {meldung?.art ?? ''}" aria-live="polite">{txt(meldung)}</p>
</form>

<style>
  .jf { min-width: 0; }
  .stand { display: flex; align-items: center; min-height: 24px; }
  .konto-zeile { display: flex; gap: var(--s-2); flex-wrap: wrap; }
  .konto-zeile select { flex: 1 1 220px; min-width: 0; }
  .konto-zeile .e-knopf { flex: 0 0 auto; min-height: 44px; }
  @media (max-width: 560px) {
    .konto-zeile .e-knopf { flex: 1 1 100%; }
  }
</style>
