/*
 * Steg 2-panel för CAD/BIM import & export (Uppdrag 17):
 * - Import av `.gpkg`, `.dxf`, `.ifc` och `.xml` (LandXML 1.2) via `POST /api/projekt/<id>/cad/importera`
 *   in i planförslagets K50-ritlager (`PlanDok`), med full visning av importerade objekt, CRS,
 *   ohämtade entiteter och varningar (inga tysta fel).
 * - Export av projektets K1 v2-geometri + valfritt sparat förslag i alla 5 format:
 *   `.gpkg`, `.dxf`, `.xml` (LandXML 1.2), `.obj` och `.glb` (3D glTF 2.0).
 *
 * Noll WebGL-/Vite-specifika statiska importer så hela modulen kan testas direkt i Node (`npm test`).
 */
import { franGeoRing } from "../karta/geom.ts";
import type { P3 } from "../karta/linje.ts";
import type { PlanDok } from "../karta/plandok.ts";
import { nyttLager, type Roll, type RollInfo } from "../karta/ritlager.ts";
import { esc } from "../sidor.ts";
import { formatera } from "../tal.ts";

export type CadExportFormat = "gpkg" | "dxf" | "landxml" | "obj" | "glb";

export interface CadExportFormatInfo {
  id: CadExportFormat;
  namn: string;
  andelse: string;
  beskrivning: string;
}

export const CAD_EXPORT_FORMAT: ReadonlyArray<CadExportFormatInfo> = [
  { id: "gpkg", namn: "GeoPackage", andelse: ".gpkg", beskrivning: "Byggnader, markytor & brytlinjer (EPSG:3011)" },
  { id: "dxf", namn: "DXF", andelse: ".dxf", beskrivning: "2D/3D CAD-lager (EPSG:3011 / RH 2000)" },
  { id: "landxml", namn: "LandXML 1.2", andelse: ".xml", beskrivning: "TIN-markmodell & brytlinjer" },
  { id: "obj", namn: "Wavefront OBJ", andelse: ".obj", beskrivning: "3D terräng + byggnader (meter)" },
  { id: "glb", namn: "glTF 2.0 (GLB)", andelse: ".glb", beskrivning: "3D binär mesh för Revit/Rhino/Blender" },
];

export const CAD_IMPORT_ACCEPT = ".gpkg,.dxf,.ifc,.xml";
export const STANDARD_IMPORT_BYGGNADSHOJD_M = 9.0;

export interface CadImportFeature {
  type: "Feature";
  id?: string;
  geometry: {
    type: "Polygon" | "LineString" | "Point" | string;
    coordinates: unknown;
  };
  properties: Record<string, unknown>;
}

export interface CadImportSvar {
  format: string;
  projekt_id: string;
  filnamn: string;
  filtyp: "gpkg" | "dxf" | "ifc" | "xml" | string;
  koordinatsystem: string;
  hojdsystem: string;
  kalla_crs: string;
  antal_objekt: number;
  antal_per_typ: {
    byggnader: number;
    markytor: number;
    brytlinjer: number;
    trad: number;
  };
  geojson: {
    type: "FeatureCollection";
    features: CadImportFeature[];
  };
  features: CadImportFeature[];
  varningar: string[];
  ohamtade_entiteter: Record<string, number>;
  metadata?: Record<string, unknown>;
}

export interface CadImportUtfall {
  antalHus: number;
  antalYtor: number;
  antalLinjer: number;
  antalTrad: number;
}

export interface CadBimPanelTillstand {
  laddar: boolean;
  fel: string | null;
  svar: CadImportSvar | null;
  inlagt: CadImportUtfall | null;
  scenario: string | null;
  ursprungE: string;
  ursprungN: string;
  ursprungZ: string;
}

/** URL för CAD/BIM-import (`POST /api/projekt/<id>/cad/importera`). */
export function cadImportUrl(projekt: string): string {
  return `/api/projekt/${encodeURIComponent(projekt)}/cad/importera`;
}

/** URL för CAD/BIM-export (`GET /api/projekt/<id>/cad/exportera?format=...&scenario=...`). */
export function cadExportUrl(
  projekt: string,
  format: CadExportFormat,
  scenario?: string | null,
): string {
  const q = new URLSearchParams();
  q.set("format", format);
  if (scenario) q.set("scenario", scenario);
  return `/api/projekt/${encodeURIComponent(projekt)}/cad/exportera?${q.toString()}`;
}

/** Kodar råa byte deterministiskt till base64 (fungerar i både webbläsare och Node). */
export function bytesTillBase64(bytes: Uint8Array): string {
  if (typeof Buffer !== "undefined") {
    return Buffer.from(bytes).toString("base64");
  }
  const chunk = 0x8000;
  let binar = "";
  for (let i = 0; i < bytes.length; i += chunk) {
    const del = bytes.subarray(i, i + chunk);
    binar += String.fromCharCode(...del);
  }
  return btoa(binar);
}

/** Tolkar valfritt decimaltal med svenskt komma eller punkt. */
export function tolkaValfrittTal(text: string): number | null {
  const ren = text.trim().replace(/\s+/g, "").replace(",", ".");
  if (!ren) return null;
  const v = Number(ren);
  return Number.isFinite(v) ? v : null;
}

function sakerstallLager(dok: PlanDok, roll: Roll, roller: readonly RollInfo[]): string {
  const bef = dok.lager.find((l) => l.roll === roll && l.synlig && !l.last);
  if (bef) return bef.lager_id;
  const dolt = dok.lager.find((l) => l.roll === roll);
  if (dolt) {
    dok.andraLager(dolt.lager_id, (l) => {
      l.synlig = true;
      l.last = false;
    });
    return dolt.lager_id;
  }
  const standardNamn: Record<Roll, string> = {
    byggnad: "Importerade hus",
    mark: "Importerad mark",
    hardgjort: "Hårdgjord yta",
    vegetation: "Vegetation",
    vatten: "Vatten",
    brytlinje: "Importerade brytlinjer",
    oppning: "Öppningar",
  };
  const nytt = nyttLager(dok.lager, roll, standardNamn[roll], roller);
  nytt.kalla = "fil:cad_bim";
  if (roll === "brytlinje") {
    nytt.geometri = "linje";
    nytt.egenskaper_forval = { typ: "kantsten" };
  } else if (roll !== "oppning") {
    nytt.geometri = "polygon";
  }
  dok.laggTillLager(nytt);
  return nytt.lager_id;
}

const MARK_ROLLER: ReadonlySet<string> = new Set(["mark", "hardgjort", "vegetation", "vatten"]);

function tal(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * Lägger in K50-kompatibla GeoJSON-features från serverns CAD/BIM-import i `PlanDok`
 * som en samlad ångringsbar grupp (`cad-import`).
 */
export function laggInCadImportIDok(
  dok: PlanDok,
  svar: Pick<CadImportSvar, "features" | "geojson">,
  roller: readonly RollInfo[] = [],
): CadImportUtfall {
  const lista = svar.features?.length ? svar.features : (svar.geojson?.features ?? []);
  let antalHus = 0;
  let antalYtor = 0;
  let antalLinjer = 0;
  let antalTrad = 0;

  dok.grupp("cad-import", () => {
    for (const f of lista) {
      const g = f.geometry;
      const p = f.properties ?? {};
      const roll = typeof p.roll === "string" ? p.roll.trim().toLowerCase() : "";

      if (g.type === "Polygon") {
        const ringar = g.coordinates as number[][][];
        const yttre = ringar?.[0];
        if (!Array.isArray(yttre) || yttre.length < 3) continue;
        const ring = franGeoRing(yttre);
        if (ring.length < 3) continue;

        if (roll === "byggnad") {
          const lid = sakerstallLager(dok, "byggnad", roller);
          const hojdRaw = tal(p.byggnadshojd_m) ?? tal(p.hojd_m);
          const hojd = hojdRaw !== null && hojdRaw > 0 ? hojdRaw : STANDARD_IMPORT_BYGGNADSHOJD_M;
          dok.laggTillHus(ring, hojd, dok.nyttId(), lid);
          antalHus += 1;
        } else {
          const ytaRoll: Roll = MARK_ROLLER.has(roll) ? (roll as Roll) : "mark";
          const lid = sakerstallLager(dok, ytaRoll, roller);
          const eg: Record<string, unknown> = {};
          const niva = tal(p.niva_rh2000) ?? tal(p.niva_rh2000_m);
          if (niva !== null) eg.niva_rh2000 = niva;
          const markslag = tal(p.markslag);
          if (markslag !== null) eg.markslag = Math.round(markslag);
          dok.laggTillFigur("yta", ring, lid, eg);
          antalYtor += 1;
        }
      } else if (g.type === "LineString") {
        const koord = g.coordinates as number[][];
        if (!Array.isArray(koord) || koord.length < 2) continue;
        const punkter: P3[] = [];
        for (const pt of koord) {
          if (!Array.isArray(pt) || pt.length < 2) continue;
          const x = pt[0];
          const y = pt[1];
          const z = pt[2];
          if (typeof x !== "number" || typeof y !== "number" || !Number.isFinite(x) || !Number.isFinite(y)) continue;
          const p3: P3 = { x, y };
          if (typeof z === "number" && Number.isFinite(z)) p3.z = z;
          punkter.push(p3);
        }
        if (punkter.length < 2) continue;
        const lid = sakerstallLager(dok, "brytlinje", roller);
        const eg: Record<string, unknown> = {};
        if (typeof p.typ === "string" && p.typ.trim()) eg.typ = p.typ.trim();
        const hojd = tal(p.hojd_m);
        if (hojd !== null && hojd > 0) eg.hojd_m = hojd;
        dok.laggTillFigur("linje", punkter, lid, eg);
        antalLinjer += 1;
      } else if (g.type === "Point") {
        antalTrad += 1;
      }
    }
  });

  return { antalHus, antalYtor, antalLinjer, antalTrad };
}

/** Renderar CAD/BIM-panelen (Steg 2) för import till ritlagret och export i alla 5 format. */
export function cadBimPanelHtml(t: CadBimPanelTillstand, projekt: string): string {
  const s = t.svar;
  let importUtHtml = `<p class="flode-liten flode-dampad">Läs in <code>.gpkg</code>, <code>.dxf</code>, <code>.ifc</code> eller <code>.xml</code> (LandXML 1.2) direkt i förslagets ritlager i SWEREF 99 18 00 (EPSG:3011).</p>`;

  if (t.laddar) {
    importUtHtml = `<p class="flode-liten flode-dampad" role="status">Importerar och validerar CAD/BIM-fil …</p>`;
  } else if (t.fel) {
    importUtHtml = `<p class="flode-liten flode-fel" role="alert">${esc(t.fel)}</p>`;
  } else if (s) {
    const ohamtade = Object.entries(s.ohamtade_entiteter ?? {});
    const ohamtadeHtml =
      ohamtade.length > 0
        ? `<p class="flode-ruta varning flode-liten">Ej stödda entiteter hoppades över: ${esc(
            ohamtade.map(([k, v]) => `${k} (${formatera(v, 0)})`).join(", "),
          )}.</p>`
        : "";
    const varningHtml =
      s.varningar.length > 0
        ? `<ul class="flode-liten flode-dampad ar-gyf-varningar">${s.varningar.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>`
        : "";
    const inlagtRad = t.inlagt
      ? `<p class="flode-liten">Tillagt i ritlagret: <strong>${esc(formatera(t.inlagt.antalHus, 0))} hus</strong>, <strong>${esc(formatera(t.inlagt.antalYtor, 0))} ytor</strong>, <strong>${esc(formatera(t.inlagt.antalLinjer, 0))} linjer</strong>${t.inlagt.antalTrad > 0 ? ` (${esc(formatera(t.inlagt.antalTrad, 0))} punkter/träd ej i K50-ritlager)` : ""}.</p>`
      : "";

    importUtHtml = `
      <dl class="flode-tal ar-cadbim-tal">
        <div><dt>Fil</dt><dd><strong>${esc(s.filnamn)}</strong> <span class="flode-dampad">(${esc(s.kalla_crs)} → ${esc(s.koordinatsystem)})</span></dd></div>
        <div><dt>Inlästa objekt</dt><dd><strong>${esc(formatera(s.antal_objekt, 0))}</strong> <span class="flode-dampad">(${esc(formatera(s.antal_per_typ.byggnader, 0))} hus, ${esc(formatera(s.antal_per_typ.markytor, 0))} ytor, ${esc(formatera(s.antal_per_typ.brytlinjer, 0))} linjer, ${esc(formatera(s.antal_per_typ.trad, 0))} punkter)</span></dd></div>
      </dl>
      ${inlagtRad}
      ${ohamtadeHtml}
      ${varningHtml}`;
  }

  const exportKnappar = CAD_EXPORT_FORMAT.map(
    (f) =>
      `<a class="flode-knapp liten ar-cadbim-export-lank" data-cad-export="${f.id}" href="${esc(
        cadExportUrl(projekt, f.id, t.scenario),
      )}" download title="${esc(f.beskrivning)}">${esc(f.namn)} <span>${esc(f.andelse)}</span></a>`,
  ).join("");

  return `
    <details class="flode-detalj ar-cadbim-panel" id="ar-cadbim-panel">
      <summary class="ar-utci-gyf-summary">
        <span>CAD / BIM (import &amp; export)</span>
        <span class="ar-skiss-badge" title="SWEREF 99 18 00 (EPSG:3011) och RH 2000">EPSG:3011</span>
      </summary>
      <div class="ar-utci-gyf-kropp">
        <div class="ar-cadbim-sektion">
          <span class="ar-sektion-rubrik">Importera till ritlager</span>
          <div class="ar-cadbim-importrad">
            <input type="file" id="ar-cadbim-fil" accept="${CAD_IMPORT_ACCEPT}" aria-label="Välj CAD/BIM-fil (.gpkg, .dxf, .ifc, .xml)" hidden />
            <button type="button" class="flode-knapp liten" id="ar-cadbim-valj"${t.laddar ? " disabled" : ""}>Välj fil (.gpkg / .dxf / .ifc / .xml)</button>
          </div>
          <details class="flode-detalj inre ar-cadbim-ifc-ursprung">
            <summary class="flode-liten">Lokalt IFC-ursprung (valfritt, EPSG:3011 / RH 2000)</summary>
            <div class="ar-cadbim-ursprung-grid">
              <label class="flode-liten">E (m)
                <input id="ar-cadbim-e0" type="text" inputmode="decimal" value="${esc(t.ursprungE)}" placeholder="0" />
              </label>
              <label class="flode-liten">N (m)
                <input id="ar-cadbim-n0" type="text" inputmode="decimal" value="${esc(t.ursprungN)}" placeholder="0" />
              </label>
              <label class="flode-liten">Z (m)
                <input id="ar-cadbim-z0" type="text" inputmode="decimal" value="${esc(t.ursprungZ)}" placeholder="0" />
              </label>
            </div>
          </details>
          <div id="ar-cadbim-ut">${importUtHtml}</div>
        </div>

        <div class="ar-cadbim-sektion">
          <span class="ar-sektion-rubrik">Exportera ${t.scenario ? `förslag (${esc(t.scenario)})` : "projektets geometri"}</span>
          <div class="ar-cadbim-export-grid" role="group" aria-label="Exportera CAD/BIM-format">
            ${exportKnappar}
          </div>
        </div>
      </div>
    </details>`;
}

export interface CadBimKopplingOpts {
  projekt: string;
  hamtaScenario(): string | null;
  onImporterat(svar: CadImportSvar): CadImportUtfall;
  signal: AbortSignal;
}

export interface CadBimKontroller {
  uppdateraKontext(): void;
}

/** Kopplar CAD/BIM-panelen i Steg 2 mot `/api/projekt/<id>/cad/importera` och `.../exportera`. */
export function kopplaCadBimPanel(
  behallare: HTMLElement,
  opts: CadBimKopplingOpts,
): CadBimKontroller {
  const state: CadBimPanelTillstand = {
    laddar: false,
    fel: null,
    svar: null,
    inlagt: null,
    scenario: opts.hamtaScenario(),
    ursprungE: "",
    ursprungN: "",
    ursprungZ: "",
  };

  let oppen = false;
  let seq = 0;

  const rita = (): void => {
    const nu = behallare.querySelector<HTMLDetailsElement>("#ar-cadbim-panel");
    if (nu) oppen = nu.open;
    behallare.innerHTML = cadBimPanelHtml(state, opts.projekt);
    const ny = behallare.querySelector<HTMLDetailsElement>("#ar-cadbim-panel");
    if (ny) ny.open = oppen;
  };

  const importeraFil = async (fil: File): Promise<void> => {
    const nr = ++seq;
    oppen = true;
    state.laddar = true;
    state.fel = null;
    rita();
    try {
      const buf = new Uint8Array(await fil.arrayBuffer());
      const e0 = tolkaValfrittTal(state.ursprungE);
      const n0 = tolkaValfrittTal(state.ursprungN);
      const z0 = tolkaValfrittTal(state.ursprungZ);
      const kropp: Record<string, unknown> = {
        filnamn: fil.name,
        innehall_base64: bytesTillBase64(buf),
      };
      if (e0 !== null) kropp.ursprung_e_m = e0;
      if (n0 !== null) kropp.ursprung_n_m = n0;
      if (z0 !== null) kropp.ursprung_z_m = z0;

      const { lasJson } = await import("../api.ts");
      const svar = await lasJson<CadImportSvar>(cadImportUrl(opts.projekt), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(kropp),
      });
      if (nr !== seq) return;
      state.svar = svar;
      state.inlagt = opts.onImporterat(svar);
      state.fel = null;
    } catch (e) {
      if (nr !== seq) return;
      state.fel = e instanceof Error ? e.message : String(e);
    } finally {
      if (nr === seq) {
        state.laddar = false;
        rita();
      }
    }
  };

  behallare.addEventListener(
    "click",
    (h) => {
      const m = (h.target as Element).closest<HTMLElement>("#ar-cadbim-valj");
      if (!m) return;
      behallare.querySelector<HTMLInputElement>("#ar-cadbim-fil")?.click();
    },
    { signal: opts.signal },
  );

  behallare.addEventListener(
    "input",
    (h) => {
      const el = h.target as HTMLInputElement;
      if (el.id === "ar-cadbim-e0") state.ursprungE = el.value;
      else if (el.id === "ar-cadbim-n0") state.ursprungN = el.value;
      else if (el.id === "ar-cadbim-z0") state.ursprungZ = el.value;
    },
    { signal: opts.signal },
  );

  behallare.addEventListener(
    "change",
    (h) => {
      const el = h.target as HTMLInputElement;
      if (el.id !== "ar-cadbim-fil") return;
      const fil = el.files?.[0];
      el.value = "";
      if (fil) void importeraFil(fil);
    },
    { signal: opts.signal },
  );

  rita();

  return {
    uppdateraKontext: () => {
      const nySc = opts.hamtaScenario();
      if (nySc !== state.scenario) {
        state.scenario = nySc;
        rita();
      }
    },
  };
}
