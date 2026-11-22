/*
 * Ångra och gör om. Ett steg är en ändring av ett objekt (läget före och efter); steg med
 * samma grupp hör ihop och ångras och görs om tillsammans. Ångra sätter tillbaka läget före.
 *
 * Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af, packages/map-revision/src/history.ts):
 * `HistoryStep`, `recordStep(s)`, `trimmed` (högst 200 enheter, en grupp kapas aldrig mitt i)
 * och `undoStep`/`redoStep`. O-Tid synkar ändringar mellan flera personer och tar bort ett steg
 * som någon annan ändrat sedan; här finns en enda användare, så ett steg går alltid att ångra.
 */

export interface Steg<K extends string = string, T = unknown> {
  key: K;
  before: T | null;
  after: T | null;
  /** Steg med samma grupp ångras och görs om tillsammans. */
  group?: string | undefined;
}

export interface Historik<K extends string = string, T = unknown> {
  undo: ReadonlyArray<Steg<K, T>>;
  redo: ReadonlyArray<Steg<K, T>>;
}

export const tomHistorik = <K extends string, T>(): Historik<K, T> => ({ undo: [], redo: [] });

/** Högst så många enheter sparas; steg i samma grupp räknas som en. */
export const HISTORIK_GRANS = 200;

function kapa<K extends string, T>(steg: ReadonlyArray<Steg<K, T>>): Array<Steg<K, T>> {
  let enheter = 0;
  let fran = steg.length;
  while (fran > 0) {
    const grupp = steg[fran - 1]?.group;
    let start = fran - 1;
    while (grupp && start > 0 && steg[start - 1]?.group === grupp) start--;
    if (enheter + 1 > HISTORIK_GRANS) break;
    enheter++;
    fran = start;
  }
  return steg.slice(fran);
}

/** Ett nytt eget steg: gör om töms. */
export function registreraSteg<K extends string, T>(h: Historik<K, T>, steg: ReadonlyArray<Steg<K, T>>): Historik<K, T> {
  return steg.length ? { undo: kapa([...h.undo, ...steg]), redo: [] } : h;
}

/** Stegen som ska sättas tillbaka (ångra) eller göras om, och den nya historiken. Tom lista = inget att göra. */
export function ta(h: Historik<string, unknown>, riktning: "undo" | "redo"): { steg: Array<Steg>; historik: Historik } {
  const fran = riktning === "undo" ? h.undo : h.redo;
  const sista = fran[fran.length - 1];
  if (!sista) return { steg: [], historik: h };
  let start = fran.length - 1;
  while (sista.group && start > 0 && fran[start - 1]?.group === sista.group) start--;
  const grupp = fran.slice(start);
  const rest = fran.slice(0, start);
  const historik: Historik =
    riktning === "undo" ? { undo: rest, redo: [...h.redo, ...grupp] } : { undo: [...h.undo, ...grupp], redo: rest };
  // Ångra går baklänges genom gruppen, gör om framlänges.
  return { steg: riktning === "undo" ? [...grupp].reverse() : grupp, historik };
}
