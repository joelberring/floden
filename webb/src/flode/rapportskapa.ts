/*
 * Steg 5 · Skapa rapport: ren logik och HTML för panelen (ingen DOM, så att den går att testa). Panelen visar
 * ämnena (med förklaring när ett ämne saknar rapportmall eller beräkning), beställningen från Frågor (skede,
 * mottagare, syfte), knappen "Skapa rapport" och jobbets förlopp. Servern gör jobbet (K48, `POST
 * /api/projekt/<id>/rapporter`): skapa, påståenden med belägg, kontroll och export; webben visar förloppet.
 */
import { esc } from "../sidor.ts";
import type { RapportAmne, RapportJobb } from "../types.ts";
import { MOTTAGARE, type Bestallning } from "./tillstand.ts";

export const JOBB_STEG = ["Skapar rapporten och ritar kartor", "Skriver påståenden med belägg", "Kontrollerar rapporten", "Exporterar PDF och Word"];

export function arPagaende(j: RapportJobb | null): boolean {
  return j !== null && (j.status === "ko" || j.status === "kor");
}

/** Förloppet i procent (0–100) ur jobbets steg; 0 i kön, 100 när det är klart. */
export function framsteg(j: RapportJobb): number {
  if (j.status === "klar") return 100;
  if (j.status === "ko") return 0;
  return Math.round((Math.max(0, j.steg_nr - 1) / Math.max(1, j.steg_totalt)) * 100);
}

export function jobbText(j: RapportJobb): string {
  if (j.status === "fel") return `Rapporten kunde inte skapas: ${j.fel ?? "okänt fel"}`;
  if (j.status === "klar") {
    if (j.kontroll && !j.kontroll.exporterad) {
      return `Rapporten är skapad men kontrollen hittade ${j.kontroll.antal_fel} fel, så PDF och Word är inte exporterade. Felen står under Kontroll.`;
    }
    return "Rapporten är klar. PDF och Word är exporterade.";
  }
  if (j.status === "ko") return "Väntar på sin tur …";
  return `${j.text} (steg ${j.steg_nr} av ${j.steg_totalt}) …`;
}

export interface KnappLage {
  aktiv: boolean;
  /** Text under knappen: varför den inte går att trycka på, eller vad som händer. */
  text: string;
}

/** Om "Skapa rapport" går att trycka på för det valda ämnet, och annars varför inte. */
export function knappLage(amne: RapportAmne | undefined, best: Bestallning, pagar: boolean): KnappLage {
  if (pagar) return { aktiv: false, text: "Rapporten skapas …" };
  if (!amne) return { aktiv: false, text: "Välj ett ämne." };
  if (!amne.kan_skapas) return { aktiv: false, text: amne.forklaring ?? "Rapporten kan inte skapas för det här ämnet än." };
  if (best.skede !== "skiss") return { aktiv: false, text: "Rapporten för planunderlag finns inte än. Välj skedet skiss." };
  return { aktiv: true, text: "Skapar rapporten, kontrollerar den och exporterar PDF och Word. Talen hämtas ur beräkningarna." };
}

export interface SkapaPanelIn {
  amnen: ReadonlyArray<RapportAmne>;
  valt: string;
  best: Bestallning;
  jobb: RapportJobb | null;
  fel: string | null;
}

export function skapaPanelHtml(i: SkapaPanelIn): string {
  const amne = i.amnen.find((a) => a.id === i.valt);
  const pagar = arPagaende(i.jobb);
  const lage = knappLage(amne, i.best, pagar);
  const chips = i.amnen
    .map((a) => `<button type="button" data-rp-amne="${esc(a.id)}" aria-pressed="${a.id === i.valt}"${a.kan_skapas ? "" : ' class="tom"'}${pagar ? " disabled" : ""}>${esc(a.namn)}</button>`)
    .join("");
  const forlopp = i.jobb
    ? `<div class="flode-forlopp" role="status" aria-live="polite">
        <progress max="100" value="${framsteg(i.jobb)}" aria-label="Förlopp"></progress>
        <p class="${i.jobb.status === "fel" ? "flode-fel" : "flode-liten"}"${i.jobb.status === "fel" ? ' role="alert"' : ""}>${esc(jobbText(i.jobb))}</p>
        ${
          i.jobb.lankar
            ? `<div class="flode-export"><a class="flode-knapp" href="${esc(i.jobb.lankar.pdf)}" download>PDF</a><a class="flode-knapp" href="${esc(i.jobb.lankar.word)}" download>Word</a></div>`
            : ""
        }
      </div>`
    : "";
  return `
    <section class="flode-skapa" aria-labelledby="rp-skapa-rubrik">
      <h2 id="rp-skapa-rubrik" class="flode-liten-rubrik">Skapa rapport</h2>
      <div role="group" aria-label="Ämne" class="flode-chips">${chips}</div>
      ${amne && !amne.kan_skapas ? `<p class="flode-ruta varning" id="rp-forklaring">${esc(amne.forklaring ?? "")}</p>` : ""}
      <fieldset class="flode-fieldset" ${pagar ? "disabled" : ""}>
        <legend>Skede</legend>
        <div class="flode-segment" id="rp-skede">
          <button type="button" data-rp-skede="skiss" aria-pressed="${i.best.skede === "skiss"}">Skiss</button>
          <button type="button" data-rp-skede="planunderlag" aria-pressed="${i.best.skede === "planunderlag"}">Planunderlag</button>
        </div>
      </fieldset>
      <div class="flode-falt"><label for="rp-mottagare">Mottagare</label>
        <select id="rp-mottagare"${pagar ? " disabled" : ""}>${MOTTAGARE.map((m) => `<option value="${m.id}"${m.id === i.best.mottagare ? " selected" : ""}>${esc(m.namn)}</option>`).join("")}</select></div>
      <div class="flode-falt"><label for="rp-syfte">Syfte</label>
        <textarea id="rp-syfte" rows="3"${pagar ? " disabled" : ""}>${esc(i.best.syfte)}</textarea></div>
      ${i.fel ? `<p class="flode-fel" role="alert">${esc(i.fel)}</p>` : ""}
      <p class="flode-liten flode-dampad" id="rp-knapp-not">${esc(lage.text)}</p>
      <button type="button" class="flode-knapp svart stor" id="rp-skapa-knapp" aria-describedby="rp-knapp-not"${lage.aktiv ? "" : " disabled"}>${pagar ? "Skapar …" : "Skapa rapport"}</button>
      ${forlopp}
    </section>`;
}
