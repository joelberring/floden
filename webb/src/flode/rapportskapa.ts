/*
 * Steg 5 · Skapa rapport: ren logik och HTML för panelen (ingen DOM, så att den går att testa). Panelen visar
 * ämnena (med förklaring när ett ämne saknar rapportmall eller beräkning), beställningen från Frågor (skede,
 * mottagare, syfte), knappen "Skapa rapport" och jobbets förlopp. Servern gör jobbet (K48, `POST
 * /api/projekt/<id>/rapporter`): skapa, påståenden med belägg, kontroll och export; webben visar förloppet.
 */
import { esc } from "../sidor.ts";
import { formateraHogst } from "../tal.ts";
import type { KorningSlag, RapportAmne, RapportJobb, RapportVal } from "../types.ts";
import { flodeAdress } from "./adress.ts";
import type { FlodeFraga } from "./status.ts";
import { MOTTAGARE, type Bestallning } from "./tillstand.ts";

/** Rapportämnet → frågornas ämnen i steg 3 (dagsljus och sol ligger under Dagsljus). */
const FRAGEAMNE: Record<string, string> = { skyfall: "skyfall", dagsljus: "dagsljus", ekologi: "ekologi", luft: "luft" };
const FRAGE_ID = /^[A-Z]{2}-\d{2,3}$/;

export interface UnderlagVal {
  /** Det som skickas med när rapporten skapas. */
  val: RapportVal;
  slag: KorningSlag | null;
  /** En mening om vilken körning och vilket förslag rapporten bygger på. */
  korningText: string;
  /** Varning när rapporten inte bygger på användarens eget förslag, annars null. */
  varning: string | null;
  /** Saknas körning: länka till steg 3. */
  saknasKorning: boolean;
  /** Frågorna från steg 3 som skickas med (id och text). */
  fragor: Array<{ id: string; text: string }>;
  fragorText: string;
}

const SLAGTEXT: Record<KorningSlag, string> = {
  forslag: "ett ritat förslag",
  egen: "en egen körning i projektet",
  demo: "demokörningen som följer med projektet",
  okand: "en körning",
};

/**
 * Vilken körning och vilka frågor rapporten bygger på. Körningen är den senaste för det ritade förslaget
 * (steg 2–3); finns ingen faller den tillbaka på servern förval (egen körning, sist demokörningen) och säger det.
 * Frågorna är de som valts i steg 3 för ämnet (id ur huvudkatalogen); har inga valts tar rapporten alla.
 */
export function valjUnderlag(
  amne: RapportAmne | undefined,
  forslag: { slug: string; namn: string } | null,
  valda: ReadonlySet<string> | null,
  katalog: ReadonlyArray<FlodeFraga> | null,
): UnderlagVal {
  const fragor = fragorForAmne(amne?.id ?? "", valda, katalog);
  const antal = formateraHogst(fragor.length, 0);
  const fragorText =
    fragor.length > 0
      ? `${antal} ${fragor.length === 1 ? "fråga" : "frågor"} som du valde i steg 3.`
      : "Inga frågor för ämnet är valda i steg 3 (eller så har steget inte öppnats), så rapporten tar alla frågor för ämnet.";
  const bas: UnderlagVal = {
    val: fragor.length > 0 ? { fragor: fragor.map((f) => f.id) } : {},
    slag: null,
    korningText: "",
    varning: null,
    saknasKorning: false,
    fragor,
    fragorText,
  };
  if (!amne || !amne.mall) return bas;
  const kor = amne.korningar ?? [];
  if (kor.length === 0 || !amne.forval_korning) return { ...bas, saknasKorning: true, korningText: "Det finns ingen klar beräkning att bygga rapporten på." };
  const egen = forslag ? kor.find((k) => k.scenario?.slug === forslag.slug) : undefined;
  if (egen) {
    return {
      ...bas,
      val: { ...bas.val, korning_id: egen.korning_id, scenario: forslag?.slug },
      slag: "forslag",
      korningText: `Ditt förslag ”${forslag?.namn ?? ""}”, körning ${egen.korning_id}.`,
    };
  }
  const f = kor.find((k) => k.korning_id === amne.forval_korning) ?? kor[0];
  const slag: KorningSlag = f?.slag ?? amne.forval_slag ?? "okand";
  const id = f?.korning_id ?? amne.forval_korning;
  const forslagText = forslag ? `Förslaget ”${forslag.namn}” är inte beräknat än.` : "Inget förslag är ritat och beräknat.";
  const slut = slag === "demo" ? " Det står i rapporten att den bygger på demokörningen." : "";
  return {
    ...bas,
    val: { ...bas.val, korning_id: id },
    slag,
    korningText: `Körning ${id}: ${SLAGTEXT[slag]}${slag === "forslag" && f?.scenario ? ` (”${f.scenario.namn}”)` : ""}.`,
    varning: slag === "forslag" && !forslag ? null : `${forslagText} Rapporten skulle bygga på ${SLAGTEXT[slag]}, inte på ditt förslag.${slut}`,
  };
}

/** Frågorna från steg 3 som hör till ämnet och är id:n ur huvudkatalogen. */
export function fragorForAmne(
  amneId: string,
  valda: ReadonlySet<string> | null,
  katalog: ReadonlyArray<FlodeFraga> | null,
): Array<{ id: string; text: string }> {
  if (!valda || !katalog) return [];
  const amne = FRAGEAMNE[amneId];
  return katalog.filter((f) => f.amne === amne && valda.has(f.id) && FRAGE_ID.test(f.id)).map((f) => ({ id: f.id, text: f.text }));
}

export function underlagHtml(projekt: string, u: UnderlagVal): string {
  const lista =
    u.fragor.length > 0
      ? `<details class="flode-detalj"><summary>Visa frågorna</summary><ul class="flode-liten">${u.fragor.map((f) => `<li>${esc(f.id)} ${esc(f.text)}</li>`).join("")}</ul></details>`
      : "";
  const saknas = u.saknasKorning
    ? `<p class="flode-ruta varning" id="rp-saknas-korning" role="alert">${esc(u.korningText)} <a href="${flodeAdress(projekt, "fragor")}">Kör beräkningarna i steg 3</a> (rita förslaget i <a href="${flodeAdress(projekt, "forslag")}">steg 2</a> först).</p>`
    : "";
  return `
    <div class="flode-underlag" id="rp-underlag" aria-labelledby="rp-underlag-rubrik">
      <h3 id="rp-underlag-rubrik" class="flode-liten-rubrik">Rapporten bygger på</h3>
      ${u.saknasKorning ? saknas : `<p class="flode-liten" id="rp-korning">${esc(u.korningText)}</p>`}
      ${u.varning ? `<p class="flode-ruta varning" id="rp-varning" role="status">${esc(u.varning)} <a href="${flodeAdress(projekt, "fragor")}">Kör beräkningarna i steg 3</a>.</p>` : ""}
      <p class="flode-liten" id="rp-fragor">${esc(u.fragorText)}</p>${lista}
    </div>`;
}

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
      return `Rapporten är skapad men kontrollen hittade ${j.kontroll.antal_fel} fel, så PDF och Word är inte exporterade. ${(j.kontroll.fel ?? []).join(" ")}`.trim();
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
export function knappLage(amne: RapportAmne | undefined, best: Bestallning, pagar: boolean, saknasKorning = false): KnappLage {
  if (pagar) return { aktiv: false, text: "Rapporten skapas …" };
  if (!amne) return { aktiv: false, text: "Välj ett ämne." };
  if (!amne.kan_skapas) return { aktiv: false, text: amne.forklaring ?? "Rapporten kan inte skapas för det här ämnet än." };
  if (saknasKorning) return { aktiv: false, text: "Det finns ingen beräkning att bygga rapporten på. Kör beräkningarna i steg 3 först." };
  if (best.skede !== "skiss") return { aktiv: false, text: "Rapporten för planunderlag finns inte än. Välj skedet skiss." };
  return { aktiv: true, text: "Skapar rapporten, kontrollerar den och exporterar PDF och Word. Talen hämtas ur beräkningarna." };
}

export interface SkapaPanelIn {
  projekt?: string;
  underlag?: UnderlagVal;
  amnen: ReadonlyArray<RapportAmne>;
  valt: string;
  best: Bestallning;
  jobb: RapportJobb | null;
  fel: string | null;
}

export function skapaPanelHtml(i: SkapaPanelIn): string {
  const amne = i.amnen.find((a) => a.id === i.valt);
  const pagar = arPagaende(i.jobb);
  const lage = knappLage(amne, i.best, pagar, i.underlag?.saknasKorning ?? false);
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
      ${amne && !amne.kan_skapas && !i.underlag?.saknasKorning ? `<p class="flode-ruta varning" id="rp-forklaring">${esc(amne.forklaring ?? "")}</p>` : ""}
      ${i.underlag && i.projekt && amne?.mall ? underlagHtml(i.projekt, i.underlag) : ""}
      <fieldset class="flode-fieldset" ${pagar ? "disabled" : ""}>
        <legend>Skede</legend>
        <div class="flode-segment" id="rp-skede">
          <button type="button" data-rp-skede="skiss" aria-pressed="${i.best.skede === "skiss"}">Skiss</button>
          <button type="button" data-rp-skede="planunderlag" aria-pressed="${i.best.skede === "planunderlag"}">Planunderlag (inte än)</button>
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
