/*
 * Steg 2-sektion "Skrivbordskontroll (skydd & naturvärden)" under Ekologi & GYF.
 *
 * Ren TypeScript-modul (noll WebGL-/Vite-importer, testbar direkt i Node med `npm test`).
 * Principer:
 * - Inga påhittade värden: saknas ett registerlager visas tydligt "Ej hämtad".
 * - Obligatorisk OSL 20 kap. 1 §-varning visas alltid (skyddsklassade arter ingår aldrig).
 * - Svensk sifferform via `tal.ts` (decimalkomma, smalt hårt mellanslag).
 * - 0 PII: fältlageruppladdning validerar format/storlek och informerar om att bara geometri,
 *   objekttyp, NVI-klass, biotop/trädslag och stamomkrets sparas.
 */
import { esc } from "../sidor.ts";
import { formatera, formateraHogst } from "../tal.ts";

export const EKOLOGI_OSL_VARNING =
  "Sekretessprövade skyddsklassade arter ingår inte — kontrollera alltid via behörig ekolog eller Länsstyrelsen.";

export const MAX_FALTLAGER_BYTES = 10 * 1024 * 1024;
export const TILLATNA_FALTLAGER_FILANDELSER: readonly string[] = [".gpkg", ".geojson", ".json"];

export type TrafikljusStatus = "gron" | "gul" | "rod" | "ej_hamtad";

export type EkologiSkrivbordLagerId =
  | "skyddade_omraden"
  | "skogliga_varden"
  | "vmi"
  | "publika_fynd"
  | "faltlager";

export interface SkyddadeOmradenOversikt {
  antal: number;
  antal_overlappar_karna: number;
  overlapp_karna_m2: number;
  minsta_avstand_m: number | null;
  antal_natura2000?: number;
  per_skyddsform?: Record<string, number>;
  narmaste?: {
    namn: string;
    skyddsform_namn?: string;
    minsta_avstand_m: number;
    overlappar_karna?: boolean;
  } | null;
}

export interface SkogligaVardenOversikt {
  antal: number;
  antal_overlappar_karna: number;
  overlapp_karna_m2: number;
  minsta_avstand_m: number | null;
  antal_nyckelbiotoper: number;
  antal_sumpskogar: number;
  antal_naturvarden?: number;
  antal_biotopskydd?: number;
  antal_naturvardsavtal?: number;
  narmaste?: {
    namn: string;
    kategori_namn?: string;
    minsta_avstand_m: number;
    overlappar_karna?: boolean;
  } | null;
}

export interface VmiOversikt {
  tillganglig: boolean;
  antal_polygoner: number;
  overlapp_karna_m2: number;
  minsta_avstand_m?: number | null;
  alder_text?: string;
}

export interface ArtfyndPublikaOversikt {
  antal_publika_fynd: number;
  antal_unika_arter: number;
  antal_rodlistade_arter: number;
  antal_fridlysta_arter: number;
  antal_skyddsklassade_filtrerade: number;
  period_ar?: [number, number] | null;
  osl_begransning?: boolean;
  osl_varning?: string;
}

export interface EkologiFaltlagerOversikt {
  filnamn: string;
  geojson_sha256?: string;
  antal_objekt: number;
  antal_polygoner: number;
  antal_punkter: number;
  per_nvi_klass: Record<string, number>;
}

export interface EkologiFaltchecklistaRad {
  moment: string;
  prioritet: string;
  underlag: string;
  atgard: string;
}

export interface EkologiSkrivbordSammanfattning {
  skyddad_natur_tillganglig: boolean;
  artfynd_tillganglig: boolean;
  vmi_tillganglig?: boolean;
  tradpunkter_tillganglig?: boolean;
  skyddade_omraden: SkyddadeOmradenOversikt;
  skogliga_varden: SkogligaVardenOversikt;
  vmi: VmiOversikt;
  artfynd_publika: ArtfyndPublikaOversikt | null;
  osl_varning?: string;
}

export interface EkologiSkrivbordIndata {
  sammanfattning: EkologiSkrivbordSammanfattning | null;
  faltlager?: EkologiFaltlagerOversikt | null;
  faltchecklista?: readonly EkologiFaltchecklistaRad[];
  lagerVal?: Partial<Record<EkologiSkrivbordLagerId, boolean>>;
  laddar?: boolean;
  felmeddelande?: string | null;
  projektNamn?: string;
}

export interface EkologiSkrivbordKallaRad {
  id: EkologiSkrivbordLagerId;
  namn: string;
  kalla: string;
  status: TrafikljusStatus;
  statusEtikett: string;
  detalj: string;
  tillganglig: boolean;
  antal: number | null;
}

export interface FaltlagerFilValidering {
  ok: boolean;
  fel: string | null;
}

const STATUS_ETIKETT: Record<TrafikljusStatus, string> = {
  gron: "Inga träffar",
  gul: "Inom buffert",
  rod: "Träff / hög prioritet",
  ej_hamtad: "Ej hämtad",
};

function fmtYta(m2: number): string {
  if (!Number.isFinite(m2)) return "–";
  if (Math.abs(m2) >= 100) return `${formatera(Math.round(m2), 0)}\u00a0m²`;
  return `${formateraHogst(Math.round(m2 * 10) / 10, 1)}\u00a0m²`;
}

function fmtAvstand(m: number | null | undefined): string {
  if (typeof m !== "number" || !Number.isFinite(m)) return "–";
  if (m >= 1000) return `${formateraHogst(Math.round((m / 1000) * 10) / 10, 1)}\u00a0km`;
  return `${formatera(Math.round(m), 0)}\u00a0m`;
}

/** Standardval för kartlagrens kryssrutor i skrivbordskontrollen. */
export function standardSkrivbordLagerVal(
  sam: EkologiSkrivbordSammanfattning | null,
  faltlager?: EkologiFaltlagerOversikt | null,
): Record<EkologiSkrivbordLagerId, boolean> {
  return {
    skyddade_omraden: Boolean(sam?.skyddad_natur_tillganglig && sam.skyddade_omraden.antal > 0),
    skogliga_varden: Boolean(sam?.skyddad_natur_tillganglig && sam.skogliga_varden.antal > 0),
    vmi: Boolean(sam?.vmi?.tillganglig && sam.vmi.antal_polygoner > 0),
    publika_fynd: Boolean(sam?.artfynd_tillganglig && (sam.artfynd_publika?.antal_publika_fynd ?? 0) > 0),
    faltlager: Boolean(faltlager && faltlager.antal_objekt > 0),
  };
}

/** Deterministisk trafikljusstatus per datakälla i skrivbordskontrollen. */
export function beraknaSkrivbordStatusRader(
  sam: EkologiSkrivbordSammanfattning | null,
  faltlager?: EkologiFaltlagerOversikt | null,
): EkologiSkrivbordKallaRad[] {
  const rader: EkologiSkrivbordKallaRad[] = [];

  // 1. Skyddad natur & Natura 2000
  if (!sam || !sam.skyddad_natur_tillganglig) {
    rader.push({
      id: "skyddade_omraden",
      namn: "Skyddad natur & Natura 2000",
      kalla: "Naturvårdsverket WFS (CC0)",
      status: "ej_hamtad",
      statusEtikett: STATUS_ETIKETT.ej_hamtad,
      detalj: "Ej mappad i geografin",
      tillganglig: false,
      antal: null,
    });
  } else {
    const sk = sam.skyddade_omraden;
    const status: TrafikljusStatus =
      sk.antal_overlappar_karna > 0 ? "rod" : sk.antal > 0 ? "gul" : "gron";
    const detalj =
      sk.antal_overlappar_karna > 0
        ? `${formatera(sk.antal_overlappar_karna, 0)} överlappar kärnan (${fmtYta(sk.overlapp_karna_m2)}), totalt ${formatera(sk.antal, 0)} i buffert`
        : sk.antal > 0
          ? `${formatera(sk.antal, 0)} inom buffert · närmaste ${fmtAvstand(sk.minsta_avstand_m)}`
          : "0 registrerade områden inom buffert";
    rader.push({
      id: "skyddade_omraden",
      namn: "Skyddad natur & Natura 2000",
      kalla: "Naturvårdsverket WFS (CC0)",
      status,
      statusEtikett: status === "rod" ? "Överlappar kärnan" : STATUS_ETIKETT[status],
      detalj,
      tillganglig: true,
      antal: sk.antal,
    });
  }

  // 2. Skogsstyrelsen (nyckelbiotoper, sumpskogar, biotopskydd, naturvårdsavtal)
  if (!sam || !sam.skyddad_natur_tillganglig) {
    rader.push({
      id: "skogliga_varden",
      namn: "Skogsstyrelsen (nyckelbiotop & sumpskog)",
      kalla: "Skogsstyrelsen REST (CC0)",
      status: "ej_hamtad",
      statusEtikett: STATUS_ETIKETT.ej_hamtad,
      detalj: "Ej mappad i geografin",
      tillganglig: false,
      antal: null,
    });
  } else {
    const sg = sam.skogliga_varden;
    const status: TrafikljusStatus =
      sg.antal_overlappar_karna > 0 ? "rod" : sg.antal > 0 ? "gul" : "gron";
    const detalj =
      sg.antal > 0
        ? `${formatera(sg.antal, 0)} objekt (${formatera(sg.antal_nyckelbiotoper, 0)} nyckelbiotoper, ${formatera(sg.antal_sumpskogar, 0)} sumpskogar) · avstånd ${fmtAvstand(sg.minsta_avstand_m)}`
        : "0 registrerade skogliga naturvärden inom buffert";
    rader.push({
      id: "skogliga_varden",
      namn: "Skogsstyrelsen (nyckelbiotop & sumpskog)",
      kalla: "Skogsstyrelsen REST (CC0)",
      status,
      statusEtikett: status === "rod" ? "Överlappar kärnan" : STATUS_ETIKETT[status],
      detalj,
      tillganglig: true,
      antal: sg.antal,
    });
  }

  // 3. VMI våtmark (1981–2005)
  if (!sam || !sam.vmi?.tillganglig) {
    rader.push({
      id: "vmi",
      namn: "VMI våtmark (1981–2005)",
      kalla: "Naturvårdsverket VMI (CC0)",
      status: "ej_hamtad",
      statusEtikett: STATUS_ETIKETT.ej_hamtad,
      detalj: "VMI-lager saknas i geografin",
      tillganglig: false,
      antal: null,
    });
  } else {
    const vmi = sam.vmi;
    const status: TrafikljusStatus =
      vmi.overlapp_karna_m2 > 0 ? "rod" : vmi.antal_polygoner > 0 ? "gul" : "gron";
    const detalj =
      vmi.overlapp_karna_m2 > 0
        ? `Överlapp ${fmtYta(vmi.overlapp_karna_m2)} (${formatera(vmi.antal_polygoner, 0)} polygoner, äldre inventering)`
        : vmi.antal_polygoner > 0
          ? `${formatera(vmi.antal_polygoner, 0)} polygoner i utsnittet (äldre inventering)`
          : "0 registrerade VMI-våtmarker i utsnittet";
    rader.push({
      id: "vmi",
      namn: "VMI våtmark (1981–2005)",
      kalla: "Naturvårdsverket VMI (CC0)",
      status,
      statusEtikett: status === "rod" ? "Överlappar kärnan" : STATUS_ETIKETT[status],
      detalj,
      tillganglig: true,
      antal: vmi.antal_polygoner,
    });
  }

  // 4. Publika rödlistefynd (SLU Artdatabanken SOS)
  if (!sam || !sam.artfynd_tillganglig || !sam.artfynd_publika) {
    rader.push({
      id: "publika_fynd",
      namn: "Publika rödlistade & fridlysta fynd",
      kalla: "SLU Artdatabanken SOS (publika fynd)",
      status: "ej_hamtad",
      statusEtikett: STATUS_ETIKETT.ej_hamtad,
      detalj: "Ej hämtad (API-nyckel saknas eller lager ej mappat)",
      tillganglig: false,
      antal: null,
    });
  } else {
    const art = sam.artfynd_publika;
    const status: TrafikljusStatus =
      art.antal_skyddsklassade_filtrerade > 0
        ? "rod"
        : art.antal_unika_arter > 0
          ? "gul"
          : "gron";
    const skyddTxt =
      art.antal_skyddsklassade_filtrerade > 0
        ? ` · ${formatera(art.antal_skyddsklassade_filtrerade, 0)} skyddsklassade filtrerade (0 koord)`
        : "";
    const detalj = `${formatera(art.antal_unika_arter, 0)} arter (${formatera(art.antal_publika_fynd, 0)} publika fynd)${skyddTxt}`;
    rader.push({
      id: "publika_fynd",
      namn: "Publika rödlistade & fridlysta fynd",
      kalla: "SLU Artdatabanken SOS (publika fynd)",
      status,
      statusEtikett:
        art.antal_skyddsklassade_filtrerade > 0
          ? "OSL-filtrerade finns"
          : art.antal_unika_arter > 0
            ? "Publika fynd"
            : STATUS_ETIKETT.gron,
      detalj,
      tillganglig: true,
      antal: art.antal_publika_fynd,
    });
  }

  // 5. Fältlager (NVI / skyddsvärda träd)
  if (!faltlager || faltlager.antal_objekt <= 0) {
    rader.push({
      id: "faltlager",
      namn: "Fältlager (NVI & skyddsvärda träd)",
      kalla: "Projektets egna fältdata (0 PII)",
      status: "ej_hamtad",
      statusEtikett: "Ej importerat",
      detalj: "Ladda upp GeoPackage eller GeoJSON från fältinventering",
      tillganglig: false,
      antal: 0,
    });
  } else {
    const k12 = (faltlager.per_nvi_klass["1"] ?? 0) + (faltlager.per_nvi_klass["2"] ?? 0);
    const status: TrafikljusStatus = k12 > 0 ? "rod" : "gul";
    const detalj = `${formatera(faltlager.antal_objekt, 0)} objekt (${formatera(faltlager.antal_polygoner, 0)} ytor, ${formatera(faltlager.antal_punkter, 0)} träd)${k12 > 0 ? ` · ${formatera(k12, 0)} i NVI klass 1–2` : ""}`;
    rader.push({
      id: "faltlager",
      namn: "Fältlager (NVI & skyddsvärda träd)",
      kalla: faltlager.filnamn,
      status,
      statusEtikett: k12 > 0 ? "NVI klass 1–2" : "Importerat",
      detalj,
      tillganglig: true,
      antal: faltlager.antal_objekt,
    });
  }

  return rader;
}

/** Validerar fältlagerfil (filändelse och max 10 MB) innan uppladdning. */
export function valideraFaltlagerFil(filnamn: string, storlekBytes: number): FaltlagerFilValidering {
  const namn = filnamn.trim();
  if (!namn) {
    return { ok: false, fel: "Välj en GeoPackage- eller GeoJSON-fil innan uppladdning." };
  }
  const low = namn.toLowerCase();
  if (!TILLATNA_FALTLAGER_FILANDELSER.some((ext) => low.endsWith(ext))) {
    return {
      ok: false,
      fel: "Filformatet stöds inte. Använd GeoPackage (.gpkg) eller GeoJSON (.geojson/.json).",
    };
  }
  if (!Number.isFinite(storlekBytes) || storlekBytes <= 0) {
    return { ok: false, fel: "Filen är tom och kan inte importeras." };
  }
  if (storlekBytes > MAX_FALTLAGER_BYTES) {
    return {
      ok: false,
      fel: "Filen överstiger maxgränsen på 10 MB för fältlager.",
    };
  }
  return { ok: true, fel: null };
}

/** Exporterar fältchecklistan (NVI) som ren Markdown med obligatorisk OSL-varning och 0 PII. */
export function exporteraFaltchecklistaMarkdown(
  rader: readonly EkologiFaltchecklistaRad[],
  projektNamn = "Planprojekt",
): string {
  const linjer: string[] = [
    `# Checklista inför fältinventering (NVI) – ${projektNamn}`,
    "",
    `> **Viktigt (OSL 20 kap. 1 §):** ${EKOLOGI_OSL_VARNING}`,
    "",
    "| Moment | Prioritet | Underlag i skrivbordskontroll | Åtgärd inför fältbesök |",
    "| --- | --- | --- | --- |",
  ];
  const san = (s: string): string => s.replace(/\|/g, "/").replace(/\r?\n/g, " ").trim();
  for (const r of rader) {
    linjer.push(`| ${san(r.moment)} | ${san(r.prioritet)} | ${san(r.underlag)} | ${san(r.atgard)} |`);
  }
  return linjer.join("\n") + "\n";
}

function csvCell(s: string): string {
  const ren = s.replace(/\r?\n/g, " ");
  if (/[",;\n]/.test(ren)) {
    return `"${ren.replace(/"/g, '""')}"`;
  }
  return ren;
}

/** Exporterar fältchecklistan (NVI) som semikolonseparerad svensk CSV med obligatorisk OSL-rad. */
export function exporteraFaltchecklistaCsv(rader: readonly EkologiFaltchecklistaRad[]): string {
  const linjer: string[] = [
    ["Moment", "Prioritet", "Underlag", "Åtgärd"].map(csvCell).join(";"),
  ];
  for (const r of rader) {
    linjer.push([r.moment, r.prioritet, r.underlag, r.atgard].map(csvCell).join(";"));
  }
  linjer.push(["OSL-begränsning", "Obligatorisk", "OSL 20 kap. 1 §", EKOLOGI_OSL_VARNING].map(csvCell).join(";"));
  return linjer.join("\n") + "\n";
}

/** Renderar hela Steg 2-sektionen "Skrivbordskontroll (skydd & naturvärden)" (ren HTML, 100 % esc-skyddad). */
export function ekologiSkrivbordPanelHtml(indata: EkologiSkrivbordIndata): string {
  const rader = beraknaSkrivbordStatusRader(indata.sammanfattning, indata.faltlager);
  const grundVal = standardSkrivbordLagerVal(indata.sammanfattning, indata.faltlager);
  const lagerVal: Record<EkologiSkrivbordLagerId, boolean> = {
    ...grundVal,
    ...(indata.lagerVal ?? {}),
  };
  const checklista = indata.faltchecklista ?? [];
  const oslText = indata.sammanfattning?.osl_varning || EKOLOGI_OSL_VARNING;

  const statusHtml = rader
    .map((rad) => {
      const checked = lagerVal[rad.id] && rad.tillganglig;
      const disabled = !rad.tillganglig;
      return `
        <li class="ar-skrivbord-rad ar-skrivbord-${rad.status}" data-skrivbord-kalla="${esc(rad.id)}">
          <div class="ar-skrivbord-topp">
            <label class="ar-skrivbord-lagerval">
              <input type="checkbox" data-ekologi-skrivbord-lager="${esc(rad.id)}"${checked ? " checked" : ""}${disabled ? " disabled" : ""} />
              <strong>${esc(rad.namn)}</strong>
            </label>
            <span class="ar-ljus-bricka ar-ljus-${rad.status}" data-trafikljus="${esc(rad.status)}">${esc(rad.statusEtikett)}</span>
          </div>
          <div class="flode-liten flode-dampad">${esc(rad.detalj)}</div>
        </li>`;
    })
    .join("");

  const checklistaHtml =
    checklista.length > 0
      ? `<div class="ar-skrivbord-checklista">
          <div class="ar-skrivbord-check-rad">
            <span class="flode-liten"><strong>Checklista inför fältinventering (NVI):</strong> ${formatera(checklista.length, 0)} kontrollpunkter</span>
            <span class="ar-skrivbord-export-grupp">
              <button type="button" class="flode-knapp-liten" data-ekologi-checklista-export="md">Exportera Markdown</button>
              <button type="button" class="flode-knapp-liten" data-ekologi-checklista-export="csv">Exportera CSV</button>
            </span>
          </div>
        </div>`
      : "";

  const felHtml = indata.felmeddelande
    ? `<p class="flode-fel" role="alert">${esc(indata.felmeddelande)}</p>`
    : "";

  return `
    <section class="ar-ekologi-skrivbord" aria-labelledby="ar-ekologi-skrivbord-rubrik">
      <div class="ar-doman-huvud">
        <h2 class="flode-h2" id="ar-ekologi-skrivbord-rubrik">Skrivbordskontroll (skydd &amp; naturvärden)</h2>
        <span class="ar-skiss-badge" title="Skrivbordsunderlag före fältinventering">SKISS</span>
      </div>
      ${indata.laddar ? `<p class="flode-liten flode-dampad">Läser verifierat skrivbordsunderlag…</p>` : ""}
      ${felHtml}
      <ul class="ar-skrivbord-lista">
        ${statusHtml}
      </ul>
      <div class="flode-ruta ar-osl-varning" role="note" data-osl-varning="true">
        <strong>Begränsning (OSL 20 kap. 1 §):</strong> ${esc(oslText)}
      </div>
      ${checklistaHtml}
      <div class="ar-faltlager-import">
        <label class="flode-liten" for="ar-ekologi-faltlager-input">
          <strong>Importera eget fältlager (NVI / skyddsvärda träd)</strong>
        </label>
        <input id="ar-ekologi-faltlager-input" type="file" accept=".gpkg,.geojson,.json" data-ekologi-faltlager-fil />
        <p class="flode-liten flode-dampad">GeoPackage eller GeoJSON i känd CRS, max 10 MB. Endast geometri, objekttyp, NVI-klass, biotop/trädslag och stamomkrets sparas (0 PII).</p>
      </div>
    </section>`;
}
