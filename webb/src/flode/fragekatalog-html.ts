/*
 * Frågekatalogens HTML (ren kod utan DOM, så att den går att testa med Nodes testkörare).
 */
import { esc } from "../sidor.ts";
import type { ProjektListaPost } from "../types.ts";
import { flodeAdress } from "./adress.ts";
import type { FragelistaSvar } from "./fragor.ts";
import { AMNEN, etikett, raknaPerAmne } from "./status.ts";

/** Ämnesraden: samma knappar som i steg 3, med antal. */
export function katalogAmnenHtml(lista: FragelistaSvar | null, amne: string): string {
  const f = lista?.fragor ?? [];
  return AMNEN.map((a) => {
    const antal = raknaPerAmne(f, a.id);
    return `<button type="button" data-amne="${a.id}" aria-pressed="${a.id === amne}"${antal === 0 ? ' class="tom"' : ""}>${esc(a.namn)}${antal > 0 ? ` · ${antal}` : ""}</button>`;
  }).join("");
}

/** Frågorna i ett ämne, utan kryssrutor. */
export function katalogListaHtml(lista: FragelistaSvar, amne: string): string {
  const rader = lista.fragor.filter((f) => f.amne === amne);
  if (rader.length === 0) return `<p class="flode-dampad flode-tomt">Inga frågor i det här ämnet än.</p>`;
  return rader
    .map(
      (f) => `<div class="flode-fraga las">
        <span class="flode-fraga-text${f.niva === "saknas" ? " dampad" : ""}">${lista.kalla === "katalog" ? `${esc(f.id)} ` : ""}${esc(f.text)}</span>
        <span class="flode-etikett ${f.niva}"${f.saknat ? ` title="${esc(f.saknat)}"` : ""}>${esc(etikett(f.niva))}</span>
      </div>`,
    )
    .join("");
}

/** Länkarna till steg 3 per projekt. */
export function katalogProjektHtml(projekt: ReadonlyArray<Pick<ProjektListaPost, "id" | "namn">>): string {
  if (projekt.length === 0) return `<p class="flode-dampad">Du har inga projekt än. <a href="#/">Öppna projektlistan</a>.</p>`;
  return `<ul class="flode-lankar">${projekt
    .map((p) => `<li><a href="${flodeAdress(p.id, "fragor")}">Välj frågor i ${esc(p.namn)}</a></li>`)
    .join("")}</ul>`;
}
