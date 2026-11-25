/*
 * Varningar från geografins mappning och från projektets skapande, som en kort sammanfattning och en hopfällbar lista
 * grupperad per slag. Geografin kan ge hundratals rader (en per byggnad som utelämnats, en per ruta med saknade höjder):
 * vänsterspalten visar bara "32 varningar vid mappningen" och listan öppnas med ett klick (`<details>`).
 *
 * Gruppering: raden delas i ett inledande id ("byggnad-17: ", "mm_3: ", högst 60 tecken utan blanksteg före första
 * kolonet) och resten; resten med siffror utbytta mot "…" är slaget. Rader med samma slag hamnar i samma grupp, största
 * gruppen först. Ren kod utan webbläsare. Tal genom `tal.ts`.
 */
import { esc } from "../sidor.ts";
import { formatera } from "../tal.ts";

export interface Varningsgrupp {
  /** Slaget: raden utan id, med siffror utbytta mot "…". */
  rubrik: string;
  /** Raderna i gruppen i ursprunglig ordning (hela texten, med id). */
  rader: string[];
}

const ID_FORE_KOLON = /^([^\s:]{1,60}):\s+(.*)$/s;

/** Slaget för en rad: texten efter ett inledande id, siffror utbytta mot "…". */
export function varningsslag(rad: string): string {
  const m = ID_FORE_KOLON.exec(rad.trim());
  const rest = (m ? (m[2] as string) : rad).trim();
  return rest.replace(/-?\d+(?:[.,]\d+)?/g, "…").replace(/(…\s*){2,}/g, "… ").replace(/\s{2,}/g, " ");
}

/** Grupperar raderna per slag, största gruppen först (lika stora i den ordning de först förekom). Tomma rader hoppas över. */
export function grupperaVarningar(rader: readonly string[]): Varningsgrupp[] {
  const grupper = new Map<string, Varningsgrupp>();
  for (const r of rader) {
    if (r.trim() === "") continue;
    const nyckel = varningsslag(r);
    const g = grupper.get(nyckel) ?? { rubrik: nyckel, rader: [] };
    g.rader.push(r.trim());
    grupper.set(nyckel, g);
  }
  return [...grupper.values()]
    .map((g, i) => ({ g, i }))
    .sort((a, b) => b.g.rader.length - a.g.rader.length || a.i - b.i)
    .map((x) => x.g);
}

export function sammanfattning(antal: number, vid: string): string {
  return `${formatera(antal, 0)} varning${antal === 1 ? "" : "ar"} ${vid}`;
}

/** Högst så många rader per grupp skrivs ut; resten räknas ("och N till"). */
export const MAX_RADER_PER_GRUPP = 100;

/**
 * Sammanfattningen och den hopfällbara listan. `vid` är fortsättningen av meningen ("vid mappningen").
 * Tom sträng utan varningar. `id` ger `<details id="…">` så att test och stil kan nå den.
 */
export function varningarHtml(rader: readonly string[], vid: string, id: string): string {
  const grupper = grupperaVarningar(rader);
  const antal = grupper.reduce((a, g) => a + g.rader.length, 0);
  if (antal === 0) return "";
  const delar = grupper.map((g) => {
    if (g.rader.length === 1) return `<li>${esc(g.rader[0] as string)}</li>`;
    const synliga = g.rader.slice(0, MAX_RADER_PER_GRUPP);
    const rest = g.rader.length - synliga.length;
    return `<li><details class="flode-varningsgrupp"><summary>${esc(`${formatera(g.rader.length, 0)} rader: ${g.rubrik}`)}</summary><ul>${synliga
      .map((r) => `<li>${esc(r)}</li>`)
      .join("")}${rest > 0 ? `<li class="flode-dampad">och ${formatera(rest, 0)} till</li>` : ""}</ul></details></li>`;
  });
  return `<details class="flode-varningar-ruta" id="${esc(id)}"><summary>${esc(sammanfattning(antal, vid))}</summary><ul class="flode-varningar">${delar.join("")}</ul></details>`;
}
