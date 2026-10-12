/* Vor allem anderen: das Sprachmodul legt beim Laden fest, dass jede
   Anfrage an /api die Sprache im Kopf X-Sprache mitschickt. */
import '$lib/i18n.svelte.js';

/* Reine Browser-App: das Backend liefert nur die gebaute index.html aus, einen
   Node-Server fuer serverseitiges Rendern gibt es nicht mehr.
   ssr = false: Seiten entstehen nur im Browser, die index.html ist eine leere
   Huelle, die fuer jeden Pfad passt.
   prerender = false: beim Bauen wird nichts vorab gerendert — jede Seite
   braucht Anmeldung und Backend, und beides gibt es beim Bauen nicht. */
export const ssr = false;
export const prerender = false;
