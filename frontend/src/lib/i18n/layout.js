/* Rahmen der Oberfläche: obere Leiste und Abspann (+layout.svelte),
   Fehlerseite (+error.svelte) und die Unternavigation des Admin-Bereichs
   (AdminNav). */

/* Pflichthinweis aus den TMDB-API-Nutzungsbedingungen, Abschnitt 3
   „Attribution“ (https://www.themoviedb.org/api-terms-of-use, Stand
   20.10.2023), mit „product“ aus der dort zur Wahl gestellten Klammer. Die
   kürzere Fassung aus der TMDB-FAQ („…uses the TMDB API but is not endorsed
   or certified by TMDB.“) ist darin vollständig enthalten; maßgeblich ist der
   Vertragstext. Er steht nur auf Englisch, und die Regeln sagen nichts über
   Übersetzungen — deshalb zeigt die deutsche Oberfläche unter ihrer
   Übersetzung zusätzlich den Originalsatz. Wortlaut nicht verändern. */
const TMDB_HINWEIS = 'This product uses TMDB and the TMDB APIs but is not endorsed, certified, '
  + 'or otherwise approved by TMDB.';

export default {
  de: {
    // Leiste
    zumInhalt: 'Zum Inhalt springen',
    menue: 'Menü',
    home: 'Home',
    filme: 'Filme',
    serien: 'Serien',
    discover: 'Discover',
    favoriten: 'Favoriten',
    chat: 'Chat',
    admin: 'Admin',
    logout: 'Logout',
    laden: 'Lade…',

    // Abspann (Quellenangabe TMDB)
    abspann: 'Abspann',
    tmdbLink: 'TMDB – The Movie Database (öffnet in neuem Tab)',
    tmdbHinweis: 'Dieses Produkt nutzt TMDB und die TMDB-APIs, wird aber von TMDB nicht befürwortet, '
      + 'zertifiziert oder anderweitig genehmigt.',
    tmdbHinweisOriginal: TMDB_HINWEIS,

    // Admin-Unternavigation
    adminBereiche: 'Admin-Bereiche',
    adminBenutzerJobs: 'Benutzer & Jobs',
    adminEinstellungen: 'Einstellungen',

    // Fehlerseite
    fehlerSeitentitel: 'Seite nicht gefunden — CinematekAI',
    nichtGefunden: 'Seite nicht gefunden',
    schiefgelaufen: 'Da ist etwas schiefgelaufen',
    nichtGefundenText: 'Diese Adresse gibt es nicht. Vielleicht wurde der Titel aus der Bibliothek entfernt oder der Link ist veraltet.',
    unbekannterFehler: 'Unbekannter Fehler',
    fehlerCode: '(Code {status})',
    zurStartseite: 'Zur Startseite',
    zurFilmliste: 'Zur Filmliste',
  },
  en: {
    // Leiste
    zumInhalt: 'Skip to content',
    menue: 'Menu',
    home: 'Home',
    filme: 'Movies',
    serien: 'Series',
    discover: 'Discover',
    favoriten: 'Favorites',
    chat: 'Chat',
    admin: 'Admin',
    logout: 'Log out',
    laden: 'Loading…',

    // Abspann (Quellenangabe TMDB)
    abspann: 'Credits',
    tmdbLink: 'TMDB – The Movie Database (opens in a new tab)',
    tmdbHinweis: TMDB_HINWEIS,
    // Leer: auf Englisch steht der Originalsatz schon oben.
    tmdbHinweisOriginal: '',

    // Admin-Unternavigation
    adminBereiche: 'Admin sections',
    adminBenutzerJobs: 'Users & Jobs',
    adminEinstellungen: 'Settings',

    // Fehlerseite
    fehlerSeitentitel: 'Page not found — CinematekAI',
    nichtGefunden: 'Page not found',
    schiefgelaufen: 'Something went wrong',
    nichtGefundenText: "This address doesn't exist. Maybe the title was removed from the library or the link is out of date.",
    unbekannterFehler: 'Unknown error',
    fehlerCode: '(code {status})',
    zurStartseite: 'Back to home',
    zurFilmliste: 'Go to movies',
  },
};
