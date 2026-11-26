/*
 * Rapportsteget · Skapa rapport: ren logik och HTML för panelen (ingen DOM, så att den går att testa).
 * Panelen visar ämnena (med förklaring när ett ämne saknar rapportmall eller beräkning), eventuellt
 * formulär för extern luftindata (timserie i CSV + bakgrundshalt), beställningen (skede, mottagare,
 * syfte), knappen "Skapa rapport" och jobbets förlopp. Servern gör jobbet (K48, `POST
 * /api/projekt/<id>/rapporter`): skapa, påståenden med belägg, kontroll och export; webben visar förloppet.
 */
import { esc } from "../sidor.ts";
import { formateraHogst } from "../tal.ts";
import type { KorningSlag, LuftRapportIndata, RapportAmne, RapportJobb, RapportVal } from "../types.ts";
import { flodeAdress } from "./adress.ts";
import type { FlodeFraga } from "./status.ts";
import { MOTTAGARE, type Bestallning } from "./tillstand.ts";

/** Rapportämnet → frågornas ämnen i frågeurvalet (dagsljus och sol ligger under Dagsljus). */
const FRAGEAMNE: Record<string, string> = { skyfall: "skyfall", dagsljus: "dagsljus", ekologi: "ekologi", luft: "luft" };
const FRAGE_ID = /^[A-Z]{2}-\d{2,3}$/;

/** Största tillåtna CSV-storlek (5 MB) enligt backendkontraktet för `luft`. */
export const MAX_LUFT_CSV_BYTES = 5 * 1024 * 1024;

export interface LuftFormTillstand {
  /** När en befintlig luft.mkn-körning finns: om användaren valt att skicka en ny timserie i stället. */
  anvandNySerie: boolean;
  /** Timserie före i CSV-format (obligatorisk när formuläret används). */
  serie_fore_csv: string;
  /** Valfri timserie efter i CSV-format. */
  serie_efter_csv: string;
  /** Rå text ur fältet för bakgrundshalt (µg/m³, obligatorisk, minst 0). */
  bakgrund_text: string;
  /** Valfri relativ haltosäkerhet (lämnas tom för serverns förval). */
  osakerhet_halt_rel_text?: string;
  /** Valfri osäkerhet på bakgrundshalt i µg/m³ (lämnas tom för serverns förval). */
  osakerhet_bakgrund_text?: string;
}

export const TOMT_LUFT_FORM: LuftFormTillstand = {
  anvandNySerie: false,
  serie_fore_csv: "",
  serie_efter_csv: "",
  bakgrund_text: "",
};

export interface UnderlagVal {
  /** Det som skickas med när rapporten skapas. */
  val: RapportVal;
  slag: KorningSlag | null;
  /** En mening om vilken körning och vilket förslag rapporten bygger på. */
  korningText: string;
  /** Varning när rapporten inte bygger på användarens eget förslag, annars null. */
  varning: string | null;
  /** Saknas körning: länka till steg 2 (Karta & analys). */
  saknasKorning: boolean;
  /** Valda frågor för ämnet (id och text). */
  fragor: Array<{ id: string; text: string }>;
  fragorText: string;
}

export const SLAGTEXT: Record<KorningSlag, string> = {
  forslag: "ett ritat förslag",
  lage_idag: "Läget idag",
  egen: "en egen körning i projektet",
  demo: "demokörningen som följer med projektet",
  okand: "en körning",
};

function tolkaNumeriskt(raw: string): number {
  const rent = raw.trim().replace(/[\s\u00a0\u202f]/g, "").replace(",", ".");
  if (rent === "") return Number.NaN;
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(rent)) return Number.NaN;
  const n = Number(rent);
  return Number.isFinite(n) ? n : Number.NaN;
}

function byteLangd(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** Sant när en befintlig klar luft.mkn-körning finns att välja i projektet. */
export function harBefintligLuftKorning(amne: RapportAmne | undefined): boolean {
  return Boolean(amne && amne.id === "luft" && (amne.korningar?.length ?? 0) > 0 && amne.forval_korning);
}

/** Sant när luftformuläret (CSV före/efter + bakgrundshalt) ska visas och valideras före submit. */
export function behoverLuftForm(amne: RapportAmne | undefined, luftForm?: LuftFormTillstand | null): boolean {
  if (!amne || amne.id !== "luft" || !amne.kan_skapas) return false;
  return !harBefintligLuftKorning(amne) || Boolean(luftForm?.anvandNySerie);
}

/**
 * Validerar extern luftindata (CSV före/efter + explicit bakgrundshalt >= 0) och bygger exakt
 * backendkontrakt `LuftRapportIndata`. Floden gissar aldrig någon bakgrundshalt.
 */
export function byggLuftIndata(
  form: LuftFormTillstand,
): { ok: true; data: LuftRapportIndata } | { ok: false; fel: string } {
  const fore = form.serie_fore_csv.trim();
  if (!fore) {
    return { ok: false, fel: "Ange en timserie före (CSV) med lokalt haltbidrag." };
  }
  if (byteLangd(fore) > MAX_LUFT_CSV_BYTES) {
    return { ok: false, fel: "Timserie före (CSV) är för stor (högst 5 MB)." };
  }
  const efter = form.serie_efter_csv.trim();
  if (efter && byteLangd(efter) > MAX_LUFT_CSV_BYTES) {
    return { ok: false, fel: "Timserie efter (CSV) är för stor (högst 5 MB)." };
  }
  const bakgText = form.bakgrund_text.trim();
  if (!bakgText) {
    return { ok: false, fel: "Ange bakgrundshalt (µg/m³, minst 0). Bakgrundshalt antas aldrig automatiskt." };
  }
  const bakg = tolkaNumeriskt(bakgText);
  if (!Number.isFinite(bakg)) {
    return { ok: false, fel: "Bakgrundshalten måste vara ett giltigt tal i µg/m³." };
  }
  if (bakg < 0) {
    return { ok: false, fel: "Bakgrundshalten får inte vara negativ (minst 0 µg/m³)." };
  }
  const data: LuftRapportIndata = {
    serie_fore_csv: fore,
    serie_efter_csv: efter ? efter : null,
    bakgrund_ug_m3: bakg,
  };
  const haltRelText = (form.osakerhet_halt_rel_text ?? "").trim();
  if (haltRelText) {
    const haltRel = tolkaNumeriskt(haltRelText);
    if (!Number.isFinite(haltRel) || haltRel < 0) {
      return { ok: false, fel: "Relativ haltosäkerhet måste vara ett tal minst 0." };
    }
    data.osakerhet_halt_rel = haltRel;
  }
  const bakgOsakText = (form.osakerhet_bakgrund_text ?? "").trim();
  if (bakgOsakText) {
    const bakgOsak = tolkaNumeriskt(bakgOsakText);
    if (!Number.isFinite(bakgOsak) || bakgOsak < 0) {
      return { ok: false, fel: "Bakgrundsosäkerhet (µg/m³) måste vara ett tal minst 0." };
    }
    data.osakerhet_bakgrund_ug_m3 = bakgOsak;
  }
  return { ok: true, data };
}

/** Returnerar feltext för luftformuläret eller null när all obligatorisk indata är giltig. */
export function valideraLuftIndata(form: LuftFormTillstand): string | null {
  const res = byggLuftIndata(form);
  return res.ok ? null : res.fel;
}

/**
 * Bygger `RapportVal` för `POST /api/projekt/<id>/rapporter`. För `luft` med extern CSV valideras
 * formuläret strikt och `korning_id`/`scenario` utelämnas så servern kör den nya timserien.
 */
export function byggRapportValForSkapa(
  amne: RapportAmne | undefined,
  underlag: UnderlagVal,
  luftForm: LuftFormTillstand = TOMT_LUFT_FORM,
): { ok: true; val: RapportVal } | { ok: false; fel: string } {
  if (!behoverLuftForm(amne, luftForm)) {
    return { ok: true, val: { ...underlag.val } };
  }
  const res = byggLuftIndata(luftForm);
  if (!res.ok) return res;
  const val: RapportVal = {
    ...(underlag.val.fragor && underlag.val.fragor.length > 0 ? { fragor: underlag.val.fragor } : {}),
    luft: res.data,
  };
  return { ok: true, val };
}

/**
 * Vilken körning och vilka frågor rapporten bygger på. Körningen är den senaste för det ritade förslaget
 * (steg 2 · Karta & analys); finns ingen faller den tillbaka på serverns förval (egen körning, läget idag,
 * sist demokörningen) och säger det. Frågorna är de som valts i frågeurvalet (id ur huvudkatalogen); har
 * inga valts tar rapporten alla.
 */
export function valjUnderlag(
  amne: RapportAmne | undefined,
  forslag: { slug: string; namn: string } | null,
  valda: ReadonlySet<string> | null,
  katalog: ReadonlyArray<FlodeFraga> | null,
  luftNySerie = false,
): UnderlagVal {
  const fragor = fragorForAmne(amne?.id ?? "", valda, katalog);
  const antal = formateraHogst(fragor.length, 0);
  const fragorText =
    fragor.length > 0
      ? `${antal} ${fragor.length === 1 ? "fråga vald" : "frågor valda"} i frågeurvalet.`
      : "Inga enskilda frågor är valda för ämnet, så rapporten tar alla frågor för ämnet.";
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
  if (amne.id === "luft" && amne.kan_skapas && (kor.length === 0 || !amne.forval_korning || luftNySerie)) {
    return {
      ...bas,
      saknasKorning: false,
      korningText:
        kor.length === 0
          ? "Ingen sparad luft.mkn-körning finns i projektet. Ange extern timserie (CSV) och bakgrundshalt nedan."
          : "Ny extern timserie (CSV) och bakgrundshalt används i stället för befintlig luftkörning.",
    };
  }
  if (kor.length === 0 || !amne.forval_korning) {
    return { ...bas, saknasKorning: true, korningText: "Det finns ingen klar beräkning att bygga rapporten på." };
  }
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
  const slagText = SLAGTEXT[slag] ?? SLAGTEXT.okand;
  const id = f?.korning_id ?? amne.forval_korning;
  const forslagText = forslag ? `Förslaget ”${forslag.namn}” är inte beräknat än.` : "Inget förslag är ritat och beräknat.";
  const slut = slag === "demo" ? " Det står i rapporten att den bygger på demokörningen." : "";
  return {
    ...bas,
    val: { ...bas.val, korning_id: id },
    slag,
    korningText: `Körning ${id}: ${slagText}${slag === "forslag" && f?.scenario ? ` (”${f.scenario.namn}”)` : ""}.`,
    varning: slag === "forslag" && !forslag ? null : `${forslagText} Rapporten skulle bygga på ${slagText}, inte på ditt förslag.${slut}`,
  };
}

/** Valda frågor ur huvudkatalogen som hör till ämnet. */
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
    ? `<p class="flode-ruta varning" id="rp-saknas-korning" role="alert">${esc(u.korningText)} <a href="${flodeAdress(projekt, "karta")}">Kör beräkningarna i steg 2 (Karta &amp; analys)</a>.</p>`
    : "";
  return `
    <div class="flode-underlag" id="rp-underlag" aria-labelledby="rp-underlag-rubrik">
      <h3 id="rp-underlag-rubrik" class="flode-liten-rubrik">Rapporten bygger på</h3>
      ${u.saknasKorning ? saknas : `<p class="flode-liten" id="rp-korning">${esc(u.korningText)}</p>`}
      ${u.varning ? `<p class="flode-ruta varning" id="rp-varning" role="status">${esc(u.varning)} <a href="${flodeAdress(projekt, "karta")}">Kör beräkningarna i steg 2 (Karta &amp; analys)</a>.</p>` : ""}
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
export function knappLage(
  amne: RapportAmne | undefined,
  best: Bestallning,
  pagar: boolean,
  saknasKorning = false,
  luftForm?: LuftFormTillstand | null,
): KnappLage {
  if (pagar) return { aktiv: false, text: "Rapporten skapas …" };
  if (!amne) return { aktiv: false, text: "Välj ett ämne." };
  if (!amne.kan_skapas) return { aktiv: false, text: amne.forklaring ?? "Rapporten kan inte skapas för det här ämnet än." };
  if (saknasKorning) {
    return { aktiv: false, text: "Det finns ingen beräkning att bygga rapporten på. Kör beräkningarna i steg 2 (Karta & analys) först." };
  }
  if (behoverLuftForm(amne, luftForm)) {
    const luftFel = valideraLuftIndata(luftForm ?? TOMT_LUFT_FORM);
    if (luftFel) return { aktiv: false, text: luftFel };
  }
  if (best.skede !== "skiss") return { aktiv: false, text: "Rapporten för planunderlag finns inte än. Välj skedet skiss." };
  return { aktiv: true, text: "Skapar rapporten, kontrollerar den och exporterar PDF och Word. Talen hämtas ur beräkningarna." };
}

/** Stramt svartvitt formulär för extern luftindata (timserie före/efter i CSV + bakgrundshalt µg/m³). */
export function luftFormHtml(
  amne: RapportAmne | undefined,
  luftForm: LuftFormTillstand,
  pagar: boolean,
): string {
  if (!amne || amne.id !== "luft" || !amne.kan_skapas) return "";
  const harBefintlig = harBefintligLuftKorning(amne);
  const visaForm = behoverLuftForm(amne, luftForm);
  const luftFel = visaForm ? valideraLuftIndata(luftForm) : null;
  const visaFel = Boolean(luftFel && (luftForm.serie_fore_csv.trim() !== "" || luftForm.bakgrund_text.trim() !== ""));
  const skissNotis = `<p class="flode-ruta" id="rp-luft-skiss"><span class="flode-skiss-marke">Skiss</span> Metodstatus: skiss. Luftunderlaget bygger på extern indata (timserie i CSV-format och uttryckligt angiven bakgrundshalt i µg/m³). Floden gissar aldrig någon bakgrundshalt.</p>`;
  const lageVal = harBefintlig
    ? `<fieldset class="flode-fieldset"${pagar ? " disabled" : ""}>
        <legend>Källa för lufttimserie</legend>
        <div class="flode-segment liten" id="rp-luft-lage" role="group" aria-label="Källa för lufttimserie">
          <button type="button" data-rp-luft-lage="befintlig" aria-pressed="${!luftForm.anvandNySerie}">Befintlig körning</button>
          <button type="button" data-rp-luft-lage="ny" aria-pressed="${luftForm.anvandNySerie}">Ny timserie (CSV)</button>
        </div>
      </fieldset>`
    : "";
  if (!visaForm) return `${skissNotis}${lageVal}`;
  return `${skissNotis}${lageVal}
    <fieldset class="flode-fieldset" id="rp-luft-form"${pagar ? " disabled" : ""}>
      <legend>Extern timserie och bakgrundshalt</legend>
      <div class="flode-falt">
        <label for="rp-luft-fore-fil">Timserie före – CSV-fil (obligatorisk)</label>
        <input id="rp-luft-fore-fil" type="file" accept=".csv,.txt,text/csv,text/plain" data-rp-luft-fil="fore"${pagar ? " disabled" : ""} />
        <label for="rp-luft-fore-csv">Timserie före – CSV-innehåll (obligatorisk)</label>
        <textarea id="rp-luft-fore-csv" rows="4" class="flode-inmatning" data-rp-luft-falt="serie_fore_csv" placeholder="tid_s,halt_ug_m3&#10;0,18.4&#10;3600,21.2"${pagar ? " disabled" : ""}>${esc(luftForm.serie_fore_csv)}</textarea>
        <p class="flode-liten flode-dampad">CSV med kolumnen <code>halt_ug_m3</code>, <code>lokal_ug_m3</code> eller <code>halt_bidrag_ug_m3</code> (minst 24 timrader, högst 5 MB).</p>
      </div>
      <div class="flode-falt">
        <label for="rp-luft-efter-fil">Timserie efter – CSV-fil (valfri)</label>
        <input id="rp-luft-efter-fil" type="file" accept=".csv,.txt,text/csv,text/plain" data-rp-luft-fil="efter"${pagar ? " disabled" : ""} />
        <label for="rp-luft-efter-csv">Timserie efter – CSV-innehåll (valfri)</label>
        <textarea id="rp-luft-efter-csv" rows="3" class="flode-inmatning" data-rp-luft-falt="serie_efter_csv" placeholder="Valfri timserie efter förslag (samma CSV-format)"${pagar ? " disabled" : ""}>${esc(luftForm.serie_efter_csv)}</textarea>
      </div>
      <div class="flode-falt">
        <label for="rp-luft-bakgrund">Bakgrundshalt (µg/m³, obligatorisk, minst 0)</label>
        <input id="rp-luft-bakgrund" type="number" step="0.1" min="0" inputmode="decimal" class="flode-inmatning" data-rp-luft-falt="bakgrund_text" value="${esc(luftForm.bakgrund_text)}" placeholder="t.ex. 12,0" required${pagar ? " disabled" : ""} />
      </div>
      <p class="flode-fel flode-liten" id="rp-luft-fel" role="alert"${visaFel ? "" : " hidden"}>${esc(visaFel && luftFel ? luftFel : "")}</p>
    </fieldset>`;
}

export interface SkapaPanelIn {
  projekt?: string;
  underlag?: UnderlagVal;
  amnen: ReadonlyArray<RapportAmne>;
  valt: string;
  best: Bestallning;
  jobb: RapportJobb | null;
  fel: string | null;
  luftForm?: LuftFormTillstand;
}

export function skapaPanelHtml(i: SkapaPanelIn): string {
  const amne = i.amnen.find((a) => a.id === i.valt);
  const pagar = arPagaende(i.jobb);
  const luftForm = i.luftForm ?? TOMT_LUFT_FORM;
  const lage = knappLage(amne, i.best, pagar, i.underlag?.saknasKorning ?? false, luftForm);
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
      ${luftFormHtml(amne, luftForm, pagar)}
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
      <div class="flode-skapa-rad">
        <p class="flode-liten flode-dampad" id="rp-knapp-not" role="status">${esc(lage.text)}</p>
        <button type="button" class="flode-knapp svart stor" id="rp-skapa-knapp" aria-describedby="rp-knapp-not"${lage.aktiv ? "" : " disabled"}>${pagar ? "Skapar …" : "Skapa rapport"}</button>
      </div>
      ${forlopp}
    </section>`;
}
