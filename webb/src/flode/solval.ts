/*
 * Solinställningar för dagsljus- och solstudier i Sol-fliken: rena typer, förval, validering,
 * serialisering och HTML-mall utan DOM. All solgeometri och skuggberäkning sker på servern.
 */
import type { SolParam } from "../karta/kartapi.ts";
import { esc } from "../sidor.ts";

export type { SolParam };

export type SolDatumForval = "vardagjamning" | "midsommar" | "eget";
export type SolBrukstid = "heladagen" | "09-17";

export interface SolVal {
  datumForval: SolDatumForval;
  egetDatumIso: string;
  brukstid: SolBrukstid;
  sammanhangande: boolean;
  inkludera_vsc: boolean;
}

export const VARDAGJAMNING_ISO = "2025-03-21";
export const MIDSOMMAR_ISO = "2025-06-21";

export const STANDARD_SOLVAL: SolVal = {
  datumForval: "vardagjamning",
  egetDatumIso: VARDAGJAMNING_ISO,
  brukstid: "heladagen",
  sammanhangande: false,
  inkludera_vsc: false,
};

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
  const arStandard =
    val.datumForval === "vardagjamning" &&
    val.brukstid === "heladagen" &&
    !val.sammanhangande &&
    !val.inkludera_vsc;
  if (arStandard) return null;
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
  return {
    datumForval,
    egetDatumIso,
    brukstid,
    sammanhangande: Boolean(o.sammanhangande),
    inkludera_vsc: Boolean(o.inkludera_vsc),
  };
}

/** Kompakt svartvitt inställningsblock för Sol-fliken. */
export function solValHtml(val: SolVal, fel: string | null = null): string {
  const egetInput =
    val.datumForval === "eget"
      ? `<div class="flode-falt ar-sol-eget"><label for="ar-sol-datum">Datum</label><input id="ar-sol-datum" type="date" data-sol-falt="egetDatumIso" value="${esc(val.egetDatumIso)}" /></div>`
      : "";
  const felHtml = fel ? `<p class="flode-fel flode-liten" role="alert">${esc(fel)}</p>` : "";
  return `<section class="ar-solval" aria-label="Solinställningar">
    <div class="ar-sektion-rad">
      <span class="ar-sektion-rubrik">Solstudie</span>
      <span class="flode-liten flode-dampad">${esc(effektivtSolDatum(val))}${val.brukstid === "09-17" ? " · 09–17" : " · Hela dagen"}</span>
    </div>
    <div class="flode-segment liten" role="group" aria-label="Datum för solstudie">
      <button type="button" data-sol-datum="vardagjamning" aria-pressed="${val.datumForval === "vardagjamning"}">Vårdagjämning 21/3</button>
      <button type="button" data-sol-datum="midsommar" aria-pressed="${val.datumForval === "midsommar"}">Midsommar 21/6</button>
      <button type="button" data-sol-datum="eget" aria-pressed="${val.datumForval === "eget"}">Eget</button>
    </div>
    ${egetInput}
    <div class="flode-segment liten" role="group" aria-label="Brukstid">
      <button type="button" data-sol-brukstid="heladagen" aria-pressed="${val.brukstid === "heladagen"}">Hela dagen</button>
      <button type="button" data-sol-brukstid="09-17" aria-pressed="${val.brukstid === "09-17"}">09–17</button>
    </div>
    <div class="ar-sol-kryss">
      <label class="flode-kryss"><input type="checkbox" data-sol-kryss="sammanhangande"${val.sammanhangande ? " checked" : ""} /> Sammanhängande soltimmar</label>
      <label class="flode-kryss"><input type="checkbox" data-sol-kryss="inkludera_vsc"${val.inkludera_vsc ? " checked" : ""} /> Fasadpunkter (VSC)</label>
    </div>
    ${felHtml}
  </section>`;
}
