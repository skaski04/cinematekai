/* Texte der Seite /chat. Die Vorschlaege (vorschlag*) gehen als Frage an
   das Backend; der naechtliche Vorlauf in backend/cron.py (CHAT_SUGGESTIONS)
   muss dieselben Fragen wortgleich stellen, sonst wird die vorbereitete
   Antwort nicht genommen und die Frage live gestellt. */
export default {
  de: {
    seitentitel: 'Chat — CinematekAI',
    ueberschriftSr: 'CinematekAI — Chat',
    titel: 'Film-Chat',
    verlaufHinweis: 'Verlauf bleibt 24 h erhalten',
    verlaufLoeschen: 'Verlauf löschen',
    verlaufLoeschenFrage: 'Chat-Verlauf löschen?',
    ladeVerlauf: 'Lade Verlauf…',
    start: 'Frag mich nach Empfehlungen aus deiner Bibliothek.',
    vorbereitet: 'Antwort ist vorbereitet',

    vorschlagRecent: 'Was habe ich zuletzt geschaut?',
    vorschlagSunday: 'Empfiehl mir einen Film für einen ruhigen Sonntagabend',
    vorschlagThriller: 'Ich mag düstere Thriller, was passt?',
    vorschlagMcu: 'Welche MCU-Filme habe ich noch nicht gesehen?',

    erneut: 'Erneut versuchen',
    geduldLang: 'CinematekAI denkt noch nach. Das dauert heute ungewöhnlich lange — du kannst die Seite verlassen und später zurückkommen, der Verlauf bleibt 24 h erhalten.',
    geduldVor: 'CinematekAI denkt nach und antwortet meist nach ',
    geduldDauer: '20–60 Sekunden',
    geduldNach: '.',

    platzhalterAntwortet: 'CinematekAI antwortet…',
    platzhalter: 'Frage stellen — Enter zum Senden',
    nachricht: 'Nachricht',
    senden: 'Nachricht senden',

    dienstWeg: 'Der Chat-Dienst ist gerade nicht erreichbar (503).',
    sitzungAbgelaufen: 'Deine Sitzung ist abgelaufen — bitte neu anmelden.',
    serverfehler: 'Serverfehler ({status}). Das lag nicht an dir.',
    abgelehnt: 'Die Anfrage wurde abgelehnt ({status}).',
    keineVerbindung: 'Keine Verbindung zum Server. Prüfe, ob CinematekAI erreichbar ist.',
    fehlerImStrom: '[Fehler: {fehler}]',
  },
  en: {
    seitentitel: 'Chat — CinematekAI',
    ueberschriftSr: 'CinematekAI — Chat',
    titel: 'Movie chat',
    verlaufHinweis: 'History is kept for 24 h',
    verlaufLoeschen: 'Clear history',
    verlaufLoeschenFrage: 'Clear chat history?',
    ladeVerlauf: 'Loading history…',
    start: 'Ask me for recommendations from your library.',
    vorbereitet: 'Answer is ready',

    vorschlagRecent: 'What did I watch last?',
    vorschlagSunday: 'Recommend a movie for a quiet Sunday evening',
    vorschlagThriller: 'I like dark thrillers, what fits?',
    vorschlagMcu: 'Which MCU movies haven’t I seen yet?',

    erneut: 'Try again',
    geduldLang: 'CinematekAI is still thinking. It’s taking unusually long today — you can leave the page and come back later, the history is kept for 24 h.',
    geduldVor: 'CinematekAI is thinking and usually replies within ',
    geduldDauer: '20–60 seconds',
    geduldNach: '.',

    platzhalterAntwortet: 'CinematekAI is replying…',
    platzhalter: 'Ask a question — Enter to send',
    nachricht: 'Message',
    senden: 'Send message',

    dienstWeg: 'The chat service is unavailable right now (503).',
    sitzungAbgelaufen: 'Your session has expired — please sign in again.',
    serverfehler: 'Server error ({status}). It wasn’t your fault.',
    abgelehnt: 'The request was rejected ({status}).',
    keineVerbindung: 'No connection to the server. Check that CinematekAI is reachable.',
    fehlerImStrom: '[Error: {fehler}]',
  },
};
