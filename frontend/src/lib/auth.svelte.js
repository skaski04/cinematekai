/** Reactive auth state for CinematekAI. Mutate properties, never reassign. */
import { sprachWaehlen, vorgabeUebernehmen } from '$lib/i18n.svelte.js';

export let user = $state({
  loggedIn: false,
  userId: null,
  displayName: '',
  isAdmin: false,
  username: '',
});

/* Version des laufenden Backends, für den Abspann. Kommt mit /api/auth/me,
   das ohnehin bei jedem Laden und nach jeder Anmeldung abgefragt wird — so
   braucht es dafür keine eigene Anfrage. Bleibt beim Abmelden stehen: die
   Version hängt nicht am Nutzer. */
export let backend = $state({ version: '' });

export async function fetchMe() {
  try {
    const r = await fetch('/api/auth/me');
    if (!r.ok) { _reset(); return; }
    const d = await r.json();
    if (d.logged_in) {
      if (d.version) backend.version = d.version;
      user.loggedIn = true;
      user.userId = d.user_id;
      user.displayName = d.display_name;
      user.isAdmin = d.is_admin;
      user.username = d.jellyfin_username;
      // Eigene Wahl des Nutzers gewinnt (gilt auf jedem Geraet); ohne
      // eigene Wahl nur die Vorgabe, falls dieser Browser nichts gewaehlt hat.
      if (d.sprache_gewaehlt) sprachWaehlen(d.sprache);
      else if (d.sprache) vorgabeUebernehmen(d.sprache);
    } else {
      _reset();
    }
  } catch {
    _reset();
  }
}

export async function doLogout() {
  await fetch('/api/auth/logout', { method: 'POST' });
  _reset();
}

function _reset() {
  user.loggedIn = false;
  user.userId = null;
  user.displayName = '';
  user.isAdmin = false;
  user.username = '';
}
