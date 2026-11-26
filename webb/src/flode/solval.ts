/*
 * Solinställningar för dagsljus- och solstudier i Sol-fliken: rena typer, förval, validering,
 * serialisering och HTML-mall utan DOM. All solgeometri och skuggberäkning sker på servern.
 */
import type { SolParam } from "../karta/kartapi.ts";
import { esc } from "../sidor.ts";

export type { SolParam };

export type SolDatumForval = "vardagjamning" | "midsommar" | "eget";
export type SolBrukstid = "heladagen" | "09-17";
export type SolLage = "soltimmar" | "skuggmask";

export interface SolVal {
  datumForval: SolDatumForval;
  egetDatumIso: string;
  brukstid: SolBrukstid;
  sammanhangande: boolean;
  inkludera_vsc: boolean;
  /** Klockslag i UTC (6,0–20,0) för ögonblicksskugga; null/utelämnat innebär soltimmar. */
  klockslag_utc?: number | null;
}

export const VARDAGJAMNING_ISO = "2025-03-21";
export const MIDSOMMAR_ISO = "2025-06-21";
export const KLOCKSLAG_MIN_UTC = 6;
export const KLOCKSLAG_MAX_UTC = 20;
export const STANDARD_KLOCKSLAG_UTC = 12;

export const STANDARD_SOLVAL: SolVal = {
  datumForval: "vardagjamning",
  egetDatumIso: VARDAGJAMNING_ISO,
  brukstid: "heladagen",
  sammanhangande: false,
  inkludera_vsc: false,
  klockslag_utc: null,
};

/** Formaterar decimaltimmar (t.ex. 12,5 -> "12:30") som HH:MM. */
export function formateraTimme(timme: number): string {
  const minuter = Math.round(timme * 60);
  const h = Math.floor(minuter / 60) % 24;
  const m = minuter % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** Tydlig svensk klockslagsetikett med UTC samt svensk normaltid (CET) / sommartid (CEST). */
export function formateraKlockslagUtc(timmeUtc: number): string {
  const utc = formateraTimme(timmeUtc);
  const cet = formateraTimme(timmeUtc + 1);
  const cest = formateraTimme(timmeUtc + 2);
  return `kl. ${utc} UTC (${cet} normaltid / ${cest} sommartid)`;
}

/** Sant om solvalet står i ögonblicksskugga-läge (klockslag_utc satt). */
export function arOgonblicksskugga(val: SolVal): boolean {
  return typeof val.klockslag_utc === "number" && Number.isFinite(val.klockslag_utc);
}

const ISO_DATUM_RE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;

/** Kontrollerar att ett datum är ett giltigt ISO-datum (`YYYY-MM-DD`). */
export function arGiltigtDatumIso(text: string): boolean {
  if (!ISO_DATUM_RE.test(text)) return false;
  const [y, m, d] = text.split("-").map(Number) as [number, number, number];
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

/** Validerar solinställningarna. Returnerar svensk feltext eller null om allt är giltigt. */
export function valideraSolVal(val: SolVal): string | null {
  if (val.datumForval === "eget" && !arGiltigtDatumIso(val.egetDatumIso.trim())) {
    return "Ange ett giltigt datum på formen ÅÅÅÅ-MM-DD (t.ex. 2025-03-21).";
  }
  if (val.klockslag_utc !== undefined && val.klockslag_utc !== null) {
    if (
      typeof val.klockslag_utc !== "number" ||
      !Number.isFinite(val.klockslag_utc) ||
      val.klockslag_utc < KLOCKSLAG_MIN_UTC ||
      val.klockslag_utc > KLOCKSLAG_MAX_UTC
    ) {
      return "Välj ett klockslag mellan 06:00 och 20:00 UTC.";
    }
  }
  return null;
}

/** Effektivt ISO-datum för valt solval. */
export function effektivtSolDatum(val: SolVal): string {
  if (val.datumForval === "midsommar") return MIDSOMMAR_ISO;
  if (val.datumForval === "eget") return val.egetDatumIso.trim() || VARDAGJAMNING_ISO;
  return VARDAGJAMNING_ISO;
}

/**
 * Omvandlar solinställningarna till JSON-kroppen `sol` för `POST .../plan` och `POST .../kor`.
 * Returnerar `null` när allt står på projektets standard (vårdagjämning, hela dagen, inga tillval).
 */
export function tillSolParam(val: SolVal): SolParam | null {
  const harKlockslag = arOgonblicksskugga(val);
  const arStandard =
    val.datumForval === "vardagjamning" &&
    val.brukstid === "heladagen" &&
    !val.sammanhangande &&
    !val.inkludera_vsc &&
    !harKlockslag;
  if (arStandard) return null;
  if (harKlockslag) {
    return {
      datum_iso: effektivtSolDatum(val),
      start_timme: null,
      slut_timme: null,
      sammanhangande: null,
      inkludera_vsc: val.inkludera_vsc,
      klockslag_utc: val.klockslag_utc as number,
    };
  }
  return {
    datum_iso: effektivtSolDatum(val),
    start_timme: val.brukstid === "09-17" ? 9 : null,
    slut_timme: val.brukstid === "09-17" ? 17 : null,
    sammanhangande: val.sammanhangande,
    inkludera_vsc: val.inkludera_vsc,
  };
}

/** Deterministisk signatur för valda solinställningar. */
export function solSignatur(val: SolVal): string {
  return JSON.stringify(tillSolParam(val));
}

/** Läser sparat SolVal från okänd JSON (t.ex. sessionStorage) med säkra förval. */
export function lasSolVal(ra: unknown): SolVal {
  if (!ra || typeof ra !== "object") return { ...STANDARD_SOLVAL };
  const o = ra as Record<string, unknown>;
  const datumForval: SolDatumForval =
    o.datumForval === "midsommar" || o.datumForval === "eget" ? o.datumForval : "vardagjamning";
  const egetDatumIso =
    typeof o.egetDatumIso === "string" && arGiltigtDatumIso(o.egetDatumIso.trim())
      ? o.egetDatumIso.trim()
      : VARDAGJAMNING_ISO;
  const brukstid: SolBrukstid = o.brukstid === "09-17" ? "09-17" : "heladagen";
  const klockslag_utc =
    typeof o.klockslag_utc === "number" &&
    Number.isFinite(o.klockslag_utc) &&
    o.klockslag_utc >= KLOCKSLAG_MIN_UTC &&
    o.klockslag_utc <= KLOCKSLAG_MAX_UTC
      ? o.klockslag_utc
      : null;
  return {
    datumForval,
    egetDatumIso,
    brukstid,
    sammanhangande: Boolean(o.sammanhangande),
    inkludera_vsc: Boolean(o.inkludera_vsc),
    klockslag_utc,
  };
}

/** Kompakt svartvitt inställningsblock för Sol-fliken. */
export function solValHtml(val: SolVal, fel: string | null = null): string {
  const arSkugg = arOgonblicksskugga(val);
  const klockslag = arSkugg ? (val.klockslag_utc as number) : STANDARD_KLOCKSLAG_UTC;
  const egetInput =
    val.datumForval === "eget"
      ? `<div class="flode-falt ar-sol-eget"><label for="ar-sol-datum">Datum</label><input id="ar-sol-datum" type="date" data-sol-falt="egetDatumIso" value="${esc(val.egetDatumIso)}" /></div>`
      : "";
  const felHtml = fel ? `<p class="flode-fel flode-liten" role="alert">${esc(fel)}</p>` : "";
  const sammanfattningTid = arSkugg
    ? ` · kl. ${formateraTimme(klockslag)} UTC`
    : val.brukstid === "09-17"
      ? " · 09–17"
      : " · Hela dagen";
  const lageKontroll = arSkugg
    ? `<div class="flode-falt ar-sol-klockslag">
        <label class="flode-liten" for="ar-sol-klockslag">Klockslag <output data-sol-klockslag-ut>${esc(formateraKlockslagUtc(klockslag))}</output></label>
        <input id="ar-sol-klockslag" type="range" min="${KLOCKSLAG_MIN_UTC}" max="${KLOCKSLAG_MAX_UTC}" step="0.5" value="${klockslag}" data-sol-klockslag aria-label="Klockslag i UTC för ögonblicksskugga" />
      </div>`
    : `<div class="flode-segment liten" role="group" aria-label="Brukstid">
        <button type="button" data-sol-brukstid="heladagen" aria-pressed="${val.brukstid === "heladagen"}">Hela dagen</button>
        <button type="button" data-sol-brukstid="09-17" aria-pressed="${val.brukstid === "09-17"}">09–17</button>
      </div>`;
  const sammanhangandeKryss = arSkugg
    ? ""
    : `<label class="flode-kryss"><input type="checkbox" data-sol-kryss="sammanhangande"${val.sammanhangande ? " checked" : ""} /> Sammanhängande soltimmar</label>`;
  return `<section class="ar-solval" aria-label="Solinställningar">
    <div class="ar-sektion-rad">
      <span class="ar-sektion-rubrik">Solstudie</span>
      <span class="flode-liten flode-dampad">${esc(effektivtSolDatum(val))}${esc(sammanfattningTid)}</span>
    </div>
    <div class="flode-segment liten" role="group" aria-label="Solstudieläge">
      <button type="button" data-sol-lage="soltimmar" aria-pressed="${!arSkugg}">Soltimmar</button>
      <button type="button" data-sol-lage="skuggmask" aria-pressed="${arSkugg}">Ögonblicksskugga</button>
    </div>
    <div class="flode-segment liten" role="group" aria-label="Datum för solstudie">
      <button type="button" data-sol-datum="vardagjamning" aria-pressed="${val.datumForval === "vardagjamning"}">Vårdagjämning 21/3</button>
      <button type="button" data-sol-datum="midsommar" aria-pressed="${val.datumForval === "midsommar"}">Midsommar 21/6</button>
      <button type="button" data-sol-datum="eget" aria-pressed="${val.datumForval === "eget"}">Eget</button>
    </div>
    ${egetInput}
    ${lageKontroll}
    <div class="ar-sol-kryss">
      ${sammanhangandeKryss}
      <label class="flode-kryss"><input type="checkbox" data-sol-kryss="inkludera_vsc"${val.inkludera_vsc ? " checked" : ""} /> Fasadpunkter (VSC)</label>
    </div>
    ${felHtml}
  </section>`;
}
