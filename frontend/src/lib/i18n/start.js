/* Texte der Startseite (src/routes/+page.svelte). */
export default {
  de: {
    startseite: 'CinematekAI — Startseite',

    // Datenstand ganz unten
    standHeute: 'Stand: heute, {uhr}',
    standGestern: 'Stand: gestern, {uhr}',
    standTag: 'Stand: {tag}, {uhr}',
    letzterAbgleich: 'Letzter Abgleich mit Jellyfin',

    // Begruendung aus tmdb_because, wenn die KI nichts geschrieben hat
    tmdbNebenZwei: 'Taucht bei TMDB immer wieder neben „{a}“ und „{b}“ auf.',
    tmdbNebenEins: 'Taucht bei TMDB immer wieder neben „{a}“ auf.',

    // Rueckmeldung zum Ausblenden
    ausgeblendet: '„{name}“ ausgeblendet — 60 Tage',
    ausblendenFehler: 'Ausblenden hat nicht geklappt.',
    wiederEingeblendet: 'Wieder eingeblendet.',
    rueckgaengigFehler: 'Rückgängig hat nicht geklappt.',
    rueckgaengig: 'Rückgängig',

    // Lade- und Fehlerzustand
    ladeEmpfehlungen: 'Lade Empfehlungen…',
    ladeFehler: 'Die Startseite konnte nicht geladen werden — CinematekAI ist gerade nicht erreichbar.',
    erneutVersuchen: 'Erneut versuchen',

    // Hero
    filmDesTages: 'Film des Tages',
    filmDesTagesKi: 'Film des Tages · KI-kuratiert',

    // Fakten
    minuten: '{n} min',
    folgen: ({ n }) => `${n} Folgen`,
    folgenVon: '{gesehen} / {gesamt} Folgen',

    // Weiterschauen
    weiterschauen: 'Weiterschauen',
    weiterschauenSub: 'wo du stehen geblieben bist',
    reiheTeil: 'Reihe · Teil {n}',
    nochMinuten: 'noch {n} min',
    reiheWeiterMit: '{gesehen} / {gesamt} · weiter mit {name}',

    // Diese Woche
    dieseWoche: 'Diese Woche',
    serieDerWoche: 'Serie der Woche · {woche}',
    weiterOderWeg: 'Weiter oder weg?',
    weiterMit: 'weiter mit {folge}',
    coqGrund: 'Du hast nur ein paar Folgen geschaut und dann nichts mehr.',
    weitermachen: 'Weitermachen',
    ausDemStapel: 'Aus dem Stapel',

    // Kuratiert
    auchInteressant: 'Auch interessant',
    rausAusDerBlase: 'Raus aus der Blase',
    rausAusDerBlaseSub: 'Genres, die sonst selten bei dir vorkommen',
    fehltInSammlung: 'Fehlt in deiner Sammlung',
    ausTmdb: 'Aus TMDB-Empfehlungen',
    klickSeerr: 'Klick öffnet Seerr',
    tmdbQuelle: 'Quelle der Titel und Poster: TMDB – The Movie Database (öffnet in neuem Tab)',

    // Wie viel Zeit hast du?
    wieVielZeit: 'Wie viel Zeit hast du?',
    bisMin: 'Bis {n} min',
    zeitFolge: 'Eine Folge, schnell',
    zeitKompakt: 'Kompakter Film',
    zeitNormal: 'Normaler Abend',
    zeitViel: 'Ich hab Zeit',
    suche: 'Suche…',
    passendUngesehen: ({ n }) => `${n} passende Filme ungesehen`,
    bisMinuten: 'Bis {n} Minuten',
    nichtsImZeitfenster: 'Nichts Ungesehenes in diesem Zeitfenster.',

    // Archiv
    merkliste: 'Deine Merkliste',
    serie: 'Serie',
    ausDeinenSerien: 'Aus deinen Serien',
    alleSerien: 'alle Serien',
    laeuft: 'läuft',
    zuletztHinzugefuegt: 'Zuletzt hinzugefügt',
    alleFilme: 'alle Filme',
  },
  en: {
    startseite: 'CinematekAI — Home',

    // Datenstand ganz unten
    standHeute: 'Updated today, {uhr}',
    standGestern: 'Updated yesterday, {uhr}',
    standTag: 'Updated {tag}, {uhr}',
    letzterAbgleich: 'Last sync with Jellyfin',

    // Begruendung aus tmdb_because, wenn die KI nichts geschrieben hat
    tmdbNebenZwei: 'Keeps showing up on TMDB next to “{a}” and “{b}”.',
    tmdbNebenEins: 'Keeps showing up on TMDB next to “{a}”.',

    // Rueckmeldung zum Ausblenden
    ausgeblendet: '“{name}” hidden — 60 days',
    ausblendenFehler: "Couldn't hide it.",
    wiederEingeblendet: 'Shown again.',
    rueckgaengigFehler: "Couldn't undo it.",
    rueckgaengig: 'Undo',

    // Lade- und Fehlerzustand
    ladeEmpfehlungen: 'Loading recommendations…',
    ladeFehler: "The home page couldn't be loaded — CinematekAI can't be reached right now.",
    erneutVersuchen: 'Try again',

    // Hero
    filmDesTages: 'Movie of the Day',
    filmDesTagesKi: 'Movie of the Day · AI-curated',

    // Fakten
    minuten: '{n} min',
    folgen: ({ n }) => (n === 1 ? '1 episode' : `${n} episodes`),
    folgenVon: '{gesehen} / {gesamt} episodes',

    // Weiterschauen
    weiterschauen: 'Continue watching',
    weiterschauenSub: 'where you left off',
    reiheTeil: 'Collection · Part {n}',
    nochMinuten: '{n} min left',
    reiheWeiterMit: '{gesehen} / {gesamt} · next: {name}',

    // Diese Woche
    dieseWoche: 'This week',
    serieDerWoche: 'Series of the week · {woche}',
    weiterOderWeg: 'Keep going or drop it?',
    weiterMit: 'next: {folge}',
    coqGrund: 'You watched a few episodes and then stopped.',
    weitermachen: 'Keep watching',
    ausDemStapel: 'Drop it',

    // Kuratiert
    auchInteressant: 'Also worth a look',
    rausAusDerBlase: 'Out of your bubble',
    rausAusDerBlaseSub: "Genres you don't usually watch",
    fehltInSammlung: 'Missing from your collection',
    ausTmdb: 'From TMDB recommendations',
    klickSeerr: 'Click opens Seerr',
    tmdbQuelle: 'Source of titles and posters: TMDB – The Movie Database (opens in a new tab)',

    // Wie viel Zeit hast du?
    wieVielZeit: 'How much time do you have?',
    bisMin: 'Up to {n} min',
    zeitFolge: 'One episode, quick',
    zeitKompakt: 'A short movie',
    zeitNormal: 'A regular evening',
    zeitViel: "I've got time",
    suche: 'Searching…',
    passendUngesehen: ({ n }) => (n === 1
      ? '1 matching unwatched movie'
      : `${n} matching unwatched movies`),
    bisMinuten: 'Up to {n} minutes',
    nichtsImZeitfenster: 'Nothing unwatched in this time slot.',

    // Archiv
    merkliste: 'Your watchlist',
    serie: 'Series',
    ausDeinenSerien: 'From your series',
    alleSerien: 'all series',
    laeuft: 'ongoing',
    zuletztHinzugefuegt: 'Recently added',
    alleFilme: 'all movies',
  },
};
