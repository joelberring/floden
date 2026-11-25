/*
 * Kartmotorn: den enda kartan i webben (MapLibre GL). Allt som ritas ligger i SWEREF 99 18 00
 * (EPSG:3011, meter); varje punkt räknas om exakt med `sweref.ts` (Gauss-Krüger, kontrollerad mot
 * pyproj) innan den ges till MapLibre. Rasterbilder från servern läggs med sina fyra hörn omräknade,
 * så att de hamnar på rätt plats. Ingen kartbakgrund hämtas från något externt: underlag läggs
 * bara till om användaren själv anger en adress.
 *
 * Staplingsordning, nerifrån: underlag, geometri, resultat (rasterlager, i den ordning anroparen
 * ger), därefter byggnader, planförslag och ritlager. Så ligger resultatet alltid över underlaget
 * och ritverktygens handtag överst.
 */
import maplibregl from "maplibre-gl";
import type { Utbredning } from "../fragor-typer";
import { swerefTillWgs84, wgs84TillSweref } from "../sweref";
import type { Punkt, Ring } from "./geom";
import { overfallTillGeoJson, MAT_I_FALT, type OverfallPunkt } from "./overfall";
import type { SkarvFigurer } from "./skarv";
import type { Pekare } from "./ritverktyg";
import type { MatLinjeVy } from "../flode/matverktyg";

export type RasterKalla =
  | { slag: "bild"; url: string; utbredning: Utbredning; skarpa?: boolean }
  | {
      slag: "kakel";
      mall: string;
      kalla: string;
      storlek?: number;
      /** Högsta zoom som plattorna finns på (kartan förstorar därutöver). */
      maxzoom?: number;
      /** Inget hämtas när lagret är släckt: källan och lagret tas bort ur kartan i stället för att bara döljas (OSM:s regler). */
      endastSynlig?: boolean;
    };

export interface RasterLager {
  id: string;
  kalla: RasterKalla;
  opacitet: number;
  synlig: boolean;
}

export interface VyByggnad {
  ring: Array<[number, number]>;
  hal: Array<Array<[number, number]>>;
  status: "oforandrad" | "ny" | "andrad" | "bort";
}

/** Det ritverktygen visar ovanpå kartan. Allt i SWEREF-meter. */
export interface RitVy {
  /** Husen i planförslaget (nya och ändrade) och befintliga byggnader som rivs. */
  hus: Array<{ id: string; ring: Ring; vald: boolean; slag: "ny" | "andrad" | "bort" }>;
  /** Utkastet medan man ritar: punkterna hittills. */
  utkast: Punkt[];
  /** Pekarens (fästa) punkt, ihopbunden med sista utkastpunkten. */
  mark: Punkt | null;
  /** Fästmarkering. */
  fast: { at: Punkt; slag: string } | null;
  lasLinje: [Punkt, Punkt] | null;
  /** Handtag för det markerade nya huset. */
  horn: Punkt[];
  mitt: Punkt[];
  /** Text intill pekaren (t.ex. ytan). */
  etikett: string | null;
  /** Ritade linjer och ytor (R3). `slag` ger färgen efter lagrets roll. */
  figurer?: Array<{ id: string; typ: "linje" | "yta"; punkter: Punkt[]; vald: boolean; roll: string; farg?: string }>;
  /** Följ kant: kantens punkter från senaste punkten till pekaren. */
  foljForhand?: Punkt[];
  /** Det markerade hörnets index på den markerade figuren (visas fyllt), eller null. */
  valdPunkt?: number | null;
  /** Befintliga brytlinjer (visning). */
  brytlinjer?: Punkt[][];
}

export const TOM_RITVY: RitVy = { hus: [], utkast: [], mark: null, fast: null, lasLinje: null, horn: [], mitt: [], etikett: null };

export interface PekarLyssnare {
  ror(p: Punkt, e: Pekare): void;
  klick(p: Punkt, e: Pekare): void;
  dubbelklick(p: Punkt, e: Pekare): void;
  /** Returnerar true om kartan inte ska panorera (ett hörn eller hus dras). */
  ned(p: Punkt, e: Pekare): boolean;
  upp(): void;
  lamnar(): void;
}

export interface Vy {
  lng: number;
  lat: number;
  zoom: number;
}

type Fc = GeoJSON.FeatureCollection;
const TOM: Fc = { type: "FeatureCollection", features: [] };

const ll = (p: Punkt | [number, number]): [number, number] => {
  const [x, y] = Array.isArray(p) ? p : [p.x, p.y];
  return swerefTillWgs84(x, y);
};
const llRing = (r: ReadonlyArray<Punkt | [number, number]>): Array<[number, number]> => {
  const o = r.map(ll);
  if (o.length > 0) o.push(o[0] as [number, number]);
  return o;
};

/** Hörnen i den ordning MapLibres bildkälla vill ha dem: övre vänster, övre höger, nedre höger, nedre vänster. */
export function bildHorn(u: Utbredning): [[number, number], [number, number], [number, number], [number, number]] {
  return [ll([u.x_min, u.y_max]), ll([u.x_max, u.y_max]), ll([u.x_max, u.y_min]), ll([u.x_min, u.y_min])];
}

const BYGGNADER = "byggnader";
const RUTOR = "rutor";
const PROJEKT_POLYGON = "projekt-polygon";
const RIT = "rit";
const OVERFALL = "overfall";
const ANALYS = "analys";
const SKARV = "skarv";
const MAT = "mat";
const SEKTION = "sektion";
const SEKTION_MARKOR = "sektion-markor";
// Lagerordning ovanför rasterlagren, nerifrån.
const OVERLAGER = [
  "byggnader-fyll",
  "byggnader-kant",
  "rutor-linje",
  "analys-yta-fyll",
  "analys-yta-kant",
  "analys-linje",
  "analys-punkt",
  "projekt-polygon-fyll",
  "projekt-polygon-kant",
  "overfall-punkter",
  "skarv-yta-fyll",
  "skarv-yta-linje",
  "skarv-pil",
  "skarv-spets",
  "skarv-punkt",
  "rit-hus-fyll",
  "rit-hus-kant",
  "rit-bort-kant",
  "rit-brytlinjer",
  "rit-fig-fyll",
  "rit-fig-linje",
  "rit-foljkant",
  "rit-las",
  "rit-utkast-fyll",
  "rit-utkast-linje",
  "rit-mitt",
  "rit-horn",
  "rit-fast",
  "rit-punkter",
  "mat-linje-skugga",
  "mat-linje",
  "mat-punkter",
  "sektion-linje-skugga",
  "sektion-linje",
  "sektion-punkter",
  "sektion-markor",
];

/** Pekskärm (grov pekare): större handtag och hållning för att flytta. */
export function arGrovPekare(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
}

/** Hur länge fingret ska hållas stilla mot en figur innan den går att flytta (ms). */
export const HALL_MS = 450;
/** Hur långt fingret får röra sig under hållningen innan det räknas som panorering (px). */
export const HALL_TOLERANS_PX = 10;

/** Analysernas vektorlager (sänkor, flödesvägar, fasadpunkter): färdiga figurer i WGS 84 med `farg`, `bredd` och `beskrivning` som egenskaper, och etiketter att lägga ut. */
export interface AnalysVektor {
  fc: GeoJSON.FeatureCollection;
  etiketter: Array<{ lng: number; lat: number; text: string }>;
}

export class Kartmotor {
  readonly karta: maplibregl.Map;
  private lager: RasterLager[] = [];
  private readonly rasterId = new Set<string>();
  private readonly signatur = new Map<string, string>();
  private klar = false;
  private vantande: Array<() => void> = [];
  private byggnader: VyByggnad[] = [];
  private rutor: Utbredning[] = [];
  private projektPolygon: GeoJSON.Polygon | null = null;
  private rit: RitVy = TOM_RITVY;
  private overfall: OverfallPunkt[] = [];
  private overfallPopup: maplibregl.Popup | null = null;
  private analys: AnalysVektor | null = null;
  private analysPopup: maplibregl.Popup | null = null;
  private analysMarkorer: maplibregl.Marker[] = [];
  private skarv: SkarvFigurer | null = null;
  private skarvForaldrad = false;
  private skarvMarkorer: maplibregl.Marker[] = [];
  private skarvPopup: maplibregl.Popup | null = null;
  private lyssnare: PekarLyssnare | null = null;
  private dragPan = true;
  private readonly slappLyssnare: () => void;
  private notisEl: HTMLElement;
  private etikettEl: HTMLElement;
  private mat: MatLinjeVy | null = null;
  private matEtikettEl: HTMLElement;
  private sektionLinje: Array<[number, number]> | null = null;
  private sektionMarkor: [number, number] | null = null;
  private slut = false;

  /** Anropas med markörens SWEREF-koordinat (E, N), eller null när markören lämnar vyn. */
  onMarkor: ((e: number | null, n: number | null) => void) | null = null;
  /** Klick på kartan (SWEREF-meter), utöver ritverktygen: används för vattenytans mått. */
  onKlickPunkt: ((e: number, n: number) => void) | null = null;
  /** Anropas när användaren själv flyttat eller zoomat vyn. */
  onVyAndrad: (() => void) | null = null;
  /** Anropas när kartskalan (meter per pixel) ändras (även vid load, zoom och anpassa). */
  onSkalaAndrad: ((meterPerPx: number) => void) | null = null;

  constructor(behallare: HTMLElement, startVy?: Vy) {
    behallare.classList.add("kartmotor");
    this.karta = new maplibregl.Map({
      container: behallare,
      style: {
        version: 8,
        sources: {},
        layers: [{ id: "bakgrund", type: "background", paint: { "background-color": "#eae6da" } }],
      },
      center: startVy ? [startVy.lng, startVy.lat] : [18.0, 59.33],
      zoom: startVy?.zoom ?? 15,
      maxZoom: 22,
      minZoom: 3,
      renderWorldCopies: false,
      attributionControl: false,
      dragRotate: false,
      boxZoom: false, // Skift+klick är följ kant i ritverktygen; kartans rutzoom åt Skift-klicket
      pitchWithRotate: false,
      doubleClickZoom: false,
      fadeDuration: 0,
      preserveDrawingBuffer: true,
    });
    this.karta.touchZoomRotate.disableRotation();
    // Valfritt provuttag: bara när sidan själv satt `__FLODEN_PROV__` (tools/ritverktyg_prov.py) lämnas motorn ut, så att provet kan
    // räkna om SWEREF-punkter till skärmpixlar. Ritandet i provet sker ändå med riktiga musklick och tangenter.
    if ((window as unknown as { __FLODEN_PROV__?: boolean }).__FLODEN_PROV__) {
      (window as unknown as { __floden_kartmotor?: Kartmotor }).__floden_kartmotor = this;
    }
    this.byttKallor([]);
    this.karta.addControl(new maplibregl.ScaleControl({ unit: "metric", maxWidth: 110 }), "bottom-left");
    this.notisEl = document.createElement("div");
    this.notisEl.className = "kartnotis";
    this.notisEl.hidden = true;
    behallare.appendChild(this.notisEl);
    this.etikettEl = document.createElement("div");
    this.etikettEl.className = "kartetikett";
    this.etikettEl.hidden = true;
    behallare.appendChild(this.etikettEl);
    this.matEtikettEl = document.createElement("div");
    this.matEtikettEl.className = "kartetikett ar-matetikett";
    this.matEtikettEl.hidden = true;
    behallare.appendChild(this.matEtikettEl);
    this.karta.getCanvas().setAttribute("aria-label", "Karta i SWEREF 99 18 00. Dra för att panorera, rulla för att zooma.");

    const meddelaSkala = (): void => {
      if (!this.slut && this.onSkalaAndrad) this.onSkalaAndrad(this.meterPerPx());
    };
    this.karta.on("load", () => {
      this.byggOverlager();
      this.kopplaOverfall();
      this.kopplaAnalys();
      this.klar = true;
      for (const f of this.vantande.splice(0)) f();
      this.synkaRaster();
      this.synkaOverlager();
      meddelaSkala();
    });
    const sweref = (e: maplibregl.MapMouseEvent): Punkt => {
      const [x, y] = wgs84TillSweref(e.lngLat.lng, e.lngLat.lat);
      return { x, y };
    };
    const pekare = (e: maplibregl.MapMouseEvent): Pekare => {
      const o = e.originalEvent as MouseEvent & { pointerType?: string };
      return { pekare: o.pointerType ?? "mouse", ctrl: o.ctrlKey || o.metaKey, shift: o.shiftKey, alt: o.altKey };
    };
    this.karta.on("mousemove", (e) => {
      const p = sweref(e);
      this.onMarkor?.(p.x, p.y);
      this.lyssnare?.ror(p, pekare(e));
    });
    this.karta.on("mouseout", () => {
      this.onMarkor?.(null, null);
      this.lyssnare?.lamnar();
    });
    this.karta.on("click", (e) => {
      this.lyssnare?.klick(sweref(e), pekare(e));
      const p = sweref(e);
      this.onKlickPunkt?.(p.x, p.y);
    });
    this.karta.on("dblclick", (e) => this.lyssnare?.dubbelklick(sweref(e), pekare(e)));
    this.karta.on("mousedown", (e) => {
      if (!this.lyssnare) return;
      if (this.lyssnare.ned(sweref(e), pekare(e))) {
        this.karta.dragPan.disable();
        this.dragPan = false;
      }
    });
    const slapp = (): void => {
      if (!this.dragPan) {
        this.dragPan = true;
        this.karta.dragPan.enable();
      }
      this.lyssnare?.upp();
    };
    this.slappLyssnare = slapp;
    this.karta.on("mouseup", slapp);
    // Håll för att flytta (R7): ett finger som hålls stilla mot en markerad figur tar tag i den (hörn, mittpunkt eller hela figuren);
    // därefter flyttas den med fingret i stället för att kartan panoreras. Rör fingret sig före hållningen panorerar kartan som vanligt.
    let hallTimer: number | null = null;
    let hallStart: { x: number; y: number } | null = null;
    let hallDrar = false;
    const touchPekare = (): Pekare => ({ pekare: "touch", ctrl: false, shift: false, alt: false });
    const rensaHall = (): void => {
      if (hallTimer !== null) window.clearTimeout(hallTimer);
      hallTimer = null;
      hallStart = null;
    };
    this.karta.on("touchstart", (e) => {
      rensaHall();
      hallDrar = false;
      if (e.points.length !== 1 || !this.lyssnare) return;
      const p = sweref(e as unknown as maplibregl.MapMouseEvent);
      hallStart = { x: e.point.x, y: e.point.y };
      hallTimer = window.setTimeout(() => {
        hallTimer = null;
        if (this.lyssnare?.ned(p, touchPekare())) {
          hallDrar = true;
          this.karta.dragPan.disable();
          this.dragPan = false;
          navigator.vibrate?.(15);
          this.lyssnare.ror(p, touchPekare());
        }
      }, HALL_MS);
    });
    this.karta.on("touchmove", (e) => {
      if (hallDrar) {
        e.preventDefault();
        this.lyssnare?.ror(sweref(e as unknown as maplibregl.MapMouseEvent), touchPekare());
        return;
      }
      if (hallTimer !== null && hallStart && Math.hypot(e.point.x - hallStart.x, e.point.y - hallStart.y) > HALL_TOLERANS_PX) rensaHall();
    });
    const touchSlut = (): void => {
      rensaHall();
      if (hallDrar) {
        hallDrar = false;
        slapp();
      }
    };
    this.karta.on("touchend", touchSlut);
    this.karta.on("touchcancel", touchSlut);
    window.addEventListener("mouseup", slapp);
    window.addEventListener("blur", slapp);
    this.karta.on("moveend", (e) => {
      if ((e as { originalEvent?: unknown }).originalEvent) this.onVyAndrad?.();
      meddelaSkala();
    });
    this.karta.on("move", (e) => {
      if ((e as { originalEvent?: unknown }).originalEvent) this.onVyAndrad?.();
      meddelaSkala();
    });
    this.karta.on("zoom", meddelaSkala);
    this.karta.on("resize", meddelaSkala);
    this.karta.on("render", () => {
      this.placeraEtikett();
      this.placeraMatEtikett();
    });
  }

  avsluta(): void {
    this.slut = true;
    window.removeEventListener("mouseup", this.slappLyssnare);
    window.removeEventListener("blur", this.slappLyssnare);
    this.karta.remove();
    this.overfallPopup?.remove();
    this.analysPopup?.remove();
    for (const m of this.analysMarkorer) m.remove();
    this.skarvPopup?.remove();
    for (const m of this.skarvMarkorer) m.remove();
    this.notisEl.remove();
    this.etikettEl.remove();
    this.matEtikettEl.remove();
  }

  private nar(f: () => void): void {
    if (this.klar) f();
    else this.vantande.push(f);
  }

  // ------------------------------------------------------------------ rasterlager

  /** Rasterlagren nerifrån och upp. Källor och lager läggs till, uppdateras och tas bort efter behov. */
  settLager(lager: RasterLager[]): void {
    this.lager = lager;
    this.nar(() => this.synkaRaster());
  }

  private synkaRaster(): void {
    if (!this.klar || this.slut) return;
    const k = this.karta;
    const behovs = (l: RasterLager): boolean => l.synlig || !(l.kalla.slag === "kakel" && l.kalla.endastSynlig);
    const onskade = new Set(this.lager.filter(behovs).map((l) => l.id));
    for (const id of [...this.rasterId]) {
      if (onskade.has(id)) continue;
      if (k.getLayer(`r-${id}`)) k.removeLayer(`r-${id}`);
      if (k.getSource(`r-${id}`)) k.removeSource(`r-${id}`);
      this.rasterId.delete(id);
      this.signatur.delete(id);
    }
    for (const l of this.lager.filter(behovs)) {
      const sid = `r-${l.id}`;
      const kalla = l.kalla;
      const befintlig = k.getSource(sid);
      if (kalla.slag === "bild") {
        const coords = bildHorn(kalla.utbredning);
        const sig = `${kalla.url}|${JSON.stringify(coords)}`;
        if (!befintlig) {
          k.addSource(sid, { type: "image", url: kalla.url, coordinates: coords });
          this.signatur.set(l.id, sig);
        } else if (this.signatur.get(l.id) !== sig) {
          // Bara när bilden eller hörnen ändrats: ett nytt anrop avbryter en bild som håller på att hämtas.
          this.signatur.set(l.id, sig);
          void Promise.resolve((befintlig as maplibregl.ImageSource).updateImage({ url: kalla.url, coordinates: coords })).catch(() => undefined);
        }
      } else if (!befintlig) {
        k.addSource(sid, { type: "raster", tiles: [kalla.mall], tileSize: kalla.storlek ?? 256, ...(kalla.maxzoom ? { maxzoom: kalla.maxzoom } : {}) });
      }
      if (!k.getLayer(sid)) {
        k.addLayer({ id: sid, type: "raster", source: sid, paint: {} }, OVERLAGER[0]);
      }
      k.setLayoutProperty(sid, "visibility", l.synlig ? "visible" : "none");
      k.setPaintProperty(sid, "raster-opacity", l.opacitet);
      k.setPaintProperty(sid, "raster-resampling", kalla.slag === "bild" && kalla.skarpa ? "nearest" : "linear");
      this.rasterId.add(l.id);
    }
    // Källor (attribution) för de kakellager som syns: synliga hela tiden, inte hopfällda; försvinner när lagret släcks.
    this.byttKallor(this.lager.filter((l) => l.synlig && l.kalla.slag === "kakel" && l.kalla.kalla !== "").map((l) => (l.kalla as { kalla: string }).kalla));
    // Ordning: första lagret längst ner (precis över bakgrunden), sedan uppåt.
    for (const l of this.lager.filter(behovs)) k.moveLayer(`r-${l.id}`, OVERLAGER[0]);
  }

  private kallControl: maplibregl.AttributionControl | null = null;
  private kallor: string | null = null;

  /**
   * Källhänvisningen i kartans hörn. Skrivs ut öppen (inte hopfälld) eftersom OpenStreetMaps villkor kräver en synlig
   * attribution, och byts ut när de synliga underlagen ändras (MapLibre uppdaterar annars inte texten när en källa tas bort).
   */
  private byttKallor(texter: string[]): void {
    const nyckel = JSON.stringify(texter);
    if (nyckel === this.kallor) return;
    this.kallor = nyckel;
    if (this.kallControl) this.karta.removeControl(this.kallControl);
    this.kallControl = new maplibregl.AttributionControl({ compact: false, customAttribution: texter });
    this.karta.addControl(this.kallControl, "bottom-right");
  }

  // ------------------------------------------------------------------ vektorlager

  private byggOverlager(): void {
    const k = this.karta;
    const grov = arGrovPekare(); // pekskärm: större handtag
    for (const s of [BYGGNADER, RUTOR, PROJEKT_POLYGON, ANALYS, OVERFALL, `${SKARV}-yta`, `${SKARV}-pil`, `${SKARV}-spets`, `${SKARV}-punkt`, `${RIT}-hus`, `${RIT}-fig`, `${RIT}-bryt`, `${RIT}-folj`, `${RIT}-utkast`, `${RIT}-las`, `${RIT}-mitt`, `${RIT}-horn`, `${RIT}-fast`, `${RIT}-punkter`, MAT, SEKTION, SEKTION_MARKOR]) {
      k.addSource(s, { type: "geojson", data: TOM });
    }
    k.addLayer({
      id: "byggnader-fyll",
      type: "fill",
      source: BYGGNADER,
      paint: {
        "fill-color": ["match", ["get", "status"], "oforandrad", "#d2d2d2", "bort", "#ffffff", "#b4b4b4"],
        "fill-opacity": ["match", ["get", "status"], "bort", 0.55, 0.8],
      },
    });
    k.addLayer({
      id: "byggnader-kant",
      type: "line",
      source: BYGGNADER,
      paint: {
        "line-color": ["match", ["get", "status"], "oforandrad", "#757575", "#111111"],
        "line-width": ["match", ["get", "status"], "oforandrad", 1, 2],
      },
    });
    k.addLayer({ id: "rutor-linje", type: "line", source: RUTOR, paint: { "line-color": "#111111", "line-opacity": 0.5, "line-width": 1, "line-dasharray": [4, 3] } });
    k.addLayer({ id: "analys-yta-fyll", type: "fill", source: ANALYS, filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": ["get", "farg"], "fill-opacity": 0.5 } });
    k.addLayer({ id: "analys-yta-kant", type: "line", source: ANALYS, filter: ["==", ["geometry-type"], "Polygon"], paint: { "line-color": ["get", "farg"], "line-width": 2 } });
    k.addLayer({ id: "analys-linje", type: "line", source: ANALYS, filter: ["==", ["geometry-type"], "LineString"], layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": ["get", "farg"], "line-width": ["get", "bredd"], "line-opacity": 0.9 } });
    k.addLayer({ id: "analys-punkt", type: "circle", source: ANALYS, filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 4, "circle-color": ["get", "farg"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 1 } });
    k.addLayer({ id: "projekt-polygon-fyll", type: "fill", source: PROJEKT_POLYGON, paint: { "fill-color": "#2f4a2c", "fill-opacity": 0.04 } });
    k.addLayer({ id: "projekt-polygon-kant", type: "line", source: PROJEKT_POLYGON, paint: { "line-color": "#2f4a2c", "line-width": 2.5, "line-opacity": 0.95 } });
    k.addLayer({
      id: "overfall-punkter",
      type: "circle",
      source: OVERFALL,
      paint: { "circle-radius": 7, "circle-color": "#c2410c", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 },
    });
    k.addLayer({ id: "skarv-yta-fyll", type: "fill", source: `${SKARV}-yta`, paint: { "fill-color": "#1565c0", "fill-opacity": 0.38 } });
    k.addLayer({ id: "skarv-yta-linje", type: "line", source: `${SKARV}-yta`, paint: { "line-color": "#0d47a1", "line-width": 2 } });
    k.addLayer({ id: "skarv-pil", type: "line", source: `${SKARV}-pil`, paint: { "line-color": "#c2410c", "line-width": 3 } });
    k.addLayer({ id: "skarv-spets", type: "fill", source: `${SKARV}-spets`, paint: { "fill-color": "#c2410c", "fill-opacity": 1 } });
    k.addLayer({
      id: "skarv-punkt",
      type: "circle",
      source: `${SKARV}-punkt`,
      paint: { "circle-radius": 5, "circle-color": "#0d47a1", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 },
    });
    k.addLayer({
      id: "rit-hus-fyll",
      type: "fill",
      source: `${RIT}-hus`,
      paint: {
        "fill-color": ["match", ["get", "slag"], "bort", "#ffffff", "#2b2b2b"],
        "fill-opacity": ["case", ["get", "vald"], 0.4, 0.28],
      },
    });
    k.addLayer({
      id: "rit-hus-kant",
      type: "line",
      source: `${RIT}-hus`,
      paint: {
        "line-color": "#111111",
        "line-width": ["case", ["get", "vald"], 3, 2],
      },
      filter: ["!=", ["get", "slag"], "bort"],
    });
    k.addLayer({
      id: "rit-bort-kant",
      type: "line",
      source: `${RIT}-hus`,
      paint: { "line-color": "#111111", "line-width": 2, "line-dasharray": [2, 2] },
      filter: ["==", ["get", "slag"], "bort"],
    });
    k.addLayer({ id: "rit-brytlinjer", type: "line", source: `${RIT}-bryt`, paint: { "line-color": "#6b6b6b", "line-width": 1.5, "line-opacity": 0.7 } });
    k.addLayer({
      id: "rit-fig-fyll",
      type: "fill",
      source: `${RIT}-fig`,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": ["get", "farg"], "fill-opacity": ["case", ["get", "vald"], 0.4, 0.25] },
    });
    k.addLayer({
      id: "rit-fig-linje",
      type: "line",
      source: `${RIT}-fig`,
      paint: { "line-color": ["get", "farg"], "line-width": ["case", ["get", "vald"], 4, 3] },
    });
    k.addLayer({ id: "rit-foljkant", type: "line", source: `${RIT}-folj`, paint: { "line-color": "#c2410c", "line-width": 4, "line-opacity": 0.8 } });
    k.addLayer({ id: "rit-las", type: "line", source: `${RIT}-las`, paint: { "line-color": "#6b6b6b", "line-width": 1, "line-dasharray": [3, 3] } });
    k.addLayer({ id: "rit-utkast-fyll", type: "fill", source: `${RIT}-utkast`, filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": "#111111", "fill-opacity": 0.12 } });
    k.addLayer({ id: "rit-utkast-linje", type: "line", source: `${RIT}-utkast`, filter: ["==", ["geometry-type"], "LineString"], paint: { "line-color": "#111111", "line-width": 2, "line-dasharray": [2, 1] } });
    k.addLayer({ id: "rit-mitt", type: "circle", source: `${RIT}-mitt`, paint: { "circle-radius": grov ? 7 : 4, "circle-color": "#ffffff", "circle-stroke-color": "#111111", "circle-stroke-width": 1, "circle-opacity": 0.8 } });
    k.addLayer({ id: "rit-horn", type: "circle", source: `${RIT}-horn`, paint: { "circle-radius": grov ? 10 : 6, "circle-color": "#ffffff", "circle-stroke-color": "#111111", "circle-stroke-width": 2 } });
    k.addLayer({
      id: "rit-fast",
      type: "circle",
      source: `${RIT}-fast`,
      paint: { "circle-radius": grov ? 16 : 9, "circle-color": "rgba(255,255,255,0)", "circle-stroke-color": "#c2410c", "circle-stroke-width": 3 },
    });
    k.addLayer({ id: "rit-punkter", type: "circle", source: `${RIT}-punkter`, paint: { "circle-radius": grov ? 7 : 4, "circle-color": "#111111", "circle-stroke-color": "#ffffff", "circle-stroke-width": 1 } });
    k.addLayer({ id: "mat-linje-skugga", type: "line", source: MAT, filter: ["==", ["geometry-type"], "LineString"], paint: { "line-color": "#ffffff", "line-width": 5, "line-opacity": 0.95 } });
    k.addLayer({ id: "mat-linje", type: "line", source: MAT, filter: ["==", ["geometry-type"], "LineString"], paint: { "line-color": "#111111", "line-width": 2.5 } });
    k.addLayer({ id: "mat-punkter", type: "circle", source: MAT, filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": grov ? 8 : 5, "circle-color": "#111111", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
    k.addLayer({ id: "sektion-linje-skugga", type: "line", source: SEKTION, filter: ["==", ["geometry-type"], "LineString"], paint: { "line-color": "#ffffff", "line-width": 5.5, "line-opacity": 0.95 } });
    k.addLayer({ id: "sektion-linje", type: "line", source: SEKTION, filter: ["==", ["geometry-type"], "LineString"], paint: { "line-color": "#111111", "line-width": 2.5, "line-dasharray": [4, 2] } });
    k.addLayer({ id: "sektion-punkter", type: "circle", source: SEKTION, filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": grov ? 8 : 5, "circle-color": "#111111", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
    k.addLayer({ id: "sektion-markor", type: "circle", source: SEKTION_MARKOR, paint: { "circle-radius": grov ? 9 : 6.5, "circle-color": "#ffffff", "circle-stroke-color": "#111111", "circle-stroke-width": 2.5 } });
  }

  private satt(id: string, data: Fc): void {
    (this.karta.getSource(id) as maplibregl.GeoJSONSource | undefined)?.setData(data);
  }

  settByggnader(b: VyByggnad[]): void {
    this.byggnader = b;
    this.nar(() => this.synkaOverlager());
  }

  /** Byggnadernas fyllning: 0 döljer den (t.ex. när värden ska synas även på tak). */
  settByggnadStil(stil: { synlig: boolean; opacitet: number }): void {
    this.nar(() => {
      const k = this.karta;
      for (const id of ["byggnader-fyll", "byggnader-kant"]) k.setLayoutProperty(id, "visibility", stil.synlig ? "visible" : "none");
      k.setPaintProperty("byggnader-fyll", "fill-opacity", ["match", ["get", "status"], "bort", 0.55 * stil.opacitet, 0.85 * stil.opacitet]);
      k.setPaintProperty("byggnader-kant", "line-opacity", stil.opacitet);
    });
  }

  /** Planförslagets ritade hus (lagret "Planförslag"). */
  settPlanStil(stil: { synlig: boolean; opacitet: number }): void {
    this.nar(() => {
      const k = this.karta;
      for (const id of ["rit-hus-fyll", "rit-hus-kant", "rit-bort-kant"]) k.setLayoutProperty(id, "visibility", stil.synlig ? "visible" : "none");
      k.setPaintProperty("rit-hus-fyll", "fill-opacity", ["case", ["get", "vald"], 0.4 * stil.opacitet, 0.28 * stil.opacitet]);
      k.setPaintProperty("rit-hus-kant", "line-opacity", stil.opacitet);
    });
  }

  /** Analysernas vektorlager (sänkor, flödesvägar, VSC-punkter) eller null för inga. Figurerna är färdigfärgade; etiketterna läggs som HTML-markörer. */
  settAnalysVektor(v: AnalysVektor | null): void {
    this.analys = v;
    this.nar(() => this.synkaOverlager());
  }

  /** Hovring och klick på en analysfigur visar dess värden (egenskapen `beskrivning`). */
  private kopplaAnalys(): void {
    const k = this.karta;
    const lager = ["analys-yta-fyll", "analys-linje", "analys-punkt"];
    const visa = (e: maplibregl.MapLayerMouseEvent): void => {
      const text = e.features?.[0]?.properties?.beskrivning;
      if (typeof text !== "string") return;
      const ruta = document.createElement("div");
      ruta.textContent = text;
      this.analysPopup?.remove();
      this.analysPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8, className: "overfall-popup" }).setLngLat(e.lngLat).setDOMContent(ruta).addTo(k);
    };
    for (const id of lager) {
      k.on("mousemove", id, visa);
      k.on("click", id, visa);
      k.on("mouseleave", id, () => this.analysPopup?.remove());
    }
  }

  private synkaAnalys(): void {
    this.satt(ANALYS, (this.analys?.fc ?? TOM) as Fc);
    for (const m of this.analysMarkorer.splice(0)) m.remove();
    for (const e of this.analys?.etiketter ?? []) {
      const el = document.createElement("div");
      el.className = "analys-etikett";
      el.textContent = e.text;
      this.analysMarkorer.push(new maplibregl.Marker({ element: el }).setLngLat([e.lng, e.lat]).addTo(this.karta));
    }
  }

  /** Överfallspunkterna (SWEREF-meter, ur bifurkationsanalysen) som eget lager. */
  settOverfall(p: OverfallPunkt[]): void {
    this.overfall = p;
    this.nar(() => this.synkaOverlager());
  }

  /** Skarvkontrollens fynd (R5): sänkans yta, pil till överfallet och en numrerad punkt per fynd. `foraldrad` tonar ner dem medan kontrollen räknas om. */
  settSkarv(f: SkarvFigurer | null, foraldrad = false): void {
    this.skarv = f;
    this.skarvForaldrad = foraldrad;
    this.nar(() => this.synkaSkarv());
  }

  private synkaSkarv(): void {
    if (!this.klar || this.slut) return;
    const k = this.karta;
    const f = this.skarv;
    const fc = (features: Fc["features"]): Fc => ({ type: "FeatureCollection", features });
    this.satt(`${SKARV}-yta`, fc((f?.ytor ?? []).map((y) => ({ type: "Feature", properties: { nr: y.nr }, geometry: { type: "Polygon", coordinates: [llRing(y.ring)] } }))));
    this.satt(`${SKARV}-pil`, fc((f?.pilar ?? []).map((p) => ({ type: "Feature", properties: { nr: p.nr }, geometry: { type: "LineString", coordinates: p.linje.map(ll) } }))));
    this.satt(`${SKARV}-spets`, fc((f?.pilar ?? []).filter((p) => p.spets.length === 3).map((p) => ({ type: "Feature", properties: { nr: p.nr }, geometry: { type: "Polygon", coordinates: [llRing(p.spets)] } }))));
    this.satt(`${SKARV}-punkt`, fc((f?.punkter ?? []).map((p) => ({ type: "Feature", properties: { nr: p.nr, text: p.text }, geometry: { type: "Point", coordinates: ll(p.p) } }))));
    const ton = this.skarvForaldrad ? 0.35 : 1;
    k.setPaintProperty("skarv-yta-fyll", "fill-opacity", 0.38 * ton);
    k.setPaintProperty("skarv-yta-linje", "line-opacity", ton);
    k.setPaintProperty("skarv-pil", "line-opacity", ton);
    k.setPaintProperty("skarv-spets", "fill-opacity", ton);
    k.setPaintProperty("skarv-punkt", "circle-opacity", ton);
    // Numren är DOM-markörer (kartan har inga teckensnitt för text i lagren); de byts bara när fynden ändras.
    const nyckel = JSON.stringify((f?.punkter ?? []).map((p) => [p.nr, p.p.x, p.p.y]));
    if (nyckel !== this.skarvMarkorNyckel) {
      this.skarvMarkorNyckel = nyckel;
      for (const m of this.skarvMarkorer) m.remove();
      this.skarvMarkorer = (f?.punkter ?? []).map((p) => {
        const el = document.createElement("div");
        el.className = "skarv-markor";
        el.textContent = String(p.nr);
        el.title = p.text;
        el.addEventListener("click", (e) => {
          e.stopPropagation();
          this.skarvPopup?.remove();
          const ruta = document.createElement("div");
          ruta.textContent = p.text;
          this.skarvPopup = new maplibregl.Popup({ closeButton: true, closeOnClick: true, offset: 14, className: "overfall-popup" }).setLngLat(ll(p.p)).setDOMContent(ruta).addTo(this.karta);
        });
        return new maplibregl.Marker({ element: el, offset: [12, -12] }).setLngLat(ll(p.p)).addTo(k);
      });
    }
    for (const m of this.skarvMarkorer) m.getElement().style.opacity = String(ton);
  }
  private skarvMarkorNyckel = "";

  /** Tänder eller släcker överfallslagret (lagret "Känsliga överfall" i lagerpanelen). */
  settOverfallStil(stil: { synlig: boolean; opacitet: number }): void {
    this.nar(() => {
      const k = this.karta;
      k.setLayoutProperty("overfall-punkter", "visibility", stil.synlig ? "visible" : "none");
      k.setPaintProperty("overfall-punkter", "circle-opacity", stil.opacitet);
      k.setPaintProperty("overfall-punkter", "circle-stroke-opacity", stil.opacitet);
      if (!stil.synlig) this.overfallPopup?.remove();
    });
  }

  /** Hovring och klick på en överfallspunkt visar punktens värden (samma text som panelen). */
  private kopplaOverfall(): void {
    const k = this.karta;
    const visa = (e: maplibregl.MapLayerMouseEvent): void => {
      const f = e.features?.[0];
      const text = f?.properties?.beskrivning;
      if (!f || typeof text !== "string") return;
      const ruta = document.createElement("div");
      const rad = document.createElement("div");
      rad.textContent = text;
      const not = document.createElement("strong");
      not.textContent = MAT_I_FALT.charAt(0).toUpperCase() + MAT_I_FALT.slice(1);
      ruta.append(rad, not);
      this.overfallPopup?.remove();
      this.overfallPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10, className: "overfall-popup" })
        .setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number])
        .setDOMContent(ruta)
        .addTo(k);
    };
    k.on("mousemove", "overfall-punkter", visa);
    k.on("click", "overfall-punkter", visa);
    k.on("mouseleave", "overfall-punkter", () => this.overfallPopup?.remove());
  }

  settRutor(r: Utbredning[]): void {
    this.rutor = r;
    this.nar(() => this.synkaOverlager());
  }

  /** Projektets avgränsningspolygon (SWEREF 99 18 00) som tydlig skogsgrön kontur i steg 2 och 4. */
  settProjektPolygon(polygon: GeoJSON.Polygon | null): void {
    this.projektPolygon = polygon;
    this.nar(() => this.synkaOverlager());
  }

  settRit(r: RitVy): void {
    this.rit = r;
    this.nar(() => this.synkaOverlager());
    this.placeraEtikett();
  }

  /** Ritar mätlinjen (breddmätning i EPSG:3011) ovanpå alla andra lager, eller null för att rensa. */
  settMatLinje(linje: MatLinjeVy | null): void {
    this.mat = linje;
    this.nar(() => this.synkaMat());
    this.placeraMatEtikett();
  }

  private synkaMat(): void {
    if (!this.klar || this.slut) return;
    const m = this.mat;
    if (!m) {
      this.satt(MAT, TOM);
      return;
    }
    const a = ll(m.a);
    const b = ll(m.b);
    this.satt(MAT, {
      type: "FeatureCollection",
      features: [
        { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [a, b] } },
        { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: a } },
        { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: b } },
      ],
    });
  }

  private placeraMatEtikett(): void {
    const m = this.mat;
    if (!m || this.slut) {
      this.matEtikettEl.hidden = true;
      return;
    }
    const mitt: Punkt = { x: (m.a.x + m.b.x) / 2, y: (m.a.y + m.b.y) / 2 };
    const s = this.karta.project(ll(mitt));
    this.matEtikettEl.textContent = m.text;
    this.matEtikettEl.style.left = `${Math.round(s.x)}px`;
    this.matEtikettEl.style.top = `${Math.round(s.y - 14)}px`;
    this.matEtikettEl.hidden = false;
  }

  /** Ritar sektionslinjen (EPSG:3011) ovanpå alla andra lager, eller null för att rensa. */
  settSektionLinje(punkter: Array<[number, number]> | null): void {
    this.sektionLinje = punkter && punkter.length > 0 ? punkter.map(([x, y]) => [x, y]) : null;
    this.nar(() => this.synkaSektion());
  }

  /** Ritar en synkad cirkelmarkör längs sektionen vid hovring i profil-SVG:n, eller null. */
  settSektionMarkor(punkt: [number, number] | null): void {
    this.sektionMarkor = punkt ? [punkt[0], punkt[1]] : null;
    this.nar(() => this.synkaSektionMarkor());
  }

  private synkaSektion(): void {
    if (!this.klar || this.slut) return;
    const pts = this.sektionLinje;
    if (!pts || pts.length === 0) {
      this.satt(SEKTION, TOM);
      return;
    }
    const coords = pts.map(ll);
    const features: Fc["features"] = [];
    if (coords.length >= 2) {
      features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } });
    }
    for (const c of coords) {
      features.push({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: c } });
    }
    this.satt(SEKTION, { type: "FeatureCollection", features });
  }

  private synkaSektionMarkor(): void {
    if (!this.klar || this.slut) return;
    const p = this.sektionMarkor;
    if (!p) {
      this.satt(SEKTION_MARKOR, TOM);
      return;
    }
    this.satt(SEKTION_MARKOR, {
      type: "FeatureCollection",
      features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: ll(p) } }],
    });
  }

  private synkaOverlager(): void {
    if (!this.klar || this.slut) return;
    this.satt(BYGGNADER, {
      type: "FeatureCollection",
      features: this.byggnader.map((b) => ({
        type: "Feature",
        properties: { status: b.status },
        geometry: { type: "Polygon", coordinates: [b.ring, ...b.hal].map(llRing) },
      })),
    });
    this.satt(RUTOR, {
      type: "FeatureCollection",
      features: this.rutor.map((u) => ({
        type: "Feature",
        properties: {},
        geometry: { type: "Polygon", coordinates: [llRing([[u.x_min, u.y_min], [u.x_max, u.y_min], [u.x_max, u.y_max], [u.x_min, u.y_max]])] },
      })),
    });
    this.satt(
      PROJEKT_POLYGON,
      this.projektPolygon && this.projektPolygon.coordinates.length > 0
        ? {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                properties: {},
                geometry: {
                  type: "Polygon",
                  coordinates: this.projektPolygon.coordinates.map((ring) => ring.map((pt) => ll([pt[0] as number, pt[1] as number]))),
                },
              },
            ],
          }
        : TOM,
    );
    this.satt(OVERFALL, overfallTillGeoJson(this.overfall));
    this.synkaAnalys();
    this.synkaSkarv();
    const r = this.rit;
    this.satt(`${RIT}-hus`, {
      type: "FeatureCollection",
      features: r.hus.map((h) => ({
        type: "Feature",
        properties: { id: h.id, vald: h.vald, slag: h.slag },
        geometry: { type: "Polygon", coordinates: [llRing(h.ring)] },
      })),
    });
    this.satt(`${RIT}-fig`, {
      type: "FeatureCollection",
      features: (r.figurer ?? []).map((g) => ({
        type: "Feature",
        properties: { id: g.id, vald: g.vald, roll: g.roll, farg: g.farg ?? "#111111" },
        geometry: g.typ === "yta" ? { type: "Polygon", coordinates: [llRing(g.punkter)] } : { type: "LineString", coordinates: g.punkter.map(ll) },
      })),
    });
    this.satt(`${RIT}-bryt`, {
      type: "FeatureCollection",
      features: (r.brytlinjer ?? []).map((l) => ({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: l.map(ll) } })),
    });
    this.satt(`${RIT}-folj`, {
      type: "FeatureCollection",
      features: r.foljForhand && r.foljForhand.length >= 2 ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: r.foljForhand.map(ll) } }] : [],
    });
    const utkast = r.mark ? [...r.utkast, r.mark] : r.utkast;
    const f: Fc["features"] = [];
    if (utkast.length >= 2) f.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: utkast.map(ll) } });
    if (utkast.length >= 3) f.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [llRing(utkast)] } });
    this.satt(`${RIT}-utkast`, { type: "FeatureCollection", features: f });
    this.satt(`${RIT}-las`, {
      type: "FeatureCollection",
      features: r.lasLinje ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: r.lasLinje.map(ll) } }] : [],
    });
    const punkter = (lista: Punkt[]): Fc => ({
      type: "FeatureCollection",
      features: lista.map((p) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: ll(p) } })),
    });
    this.satt(`${RIT}-mitt`, punkter(r.mitt));
    this.satt(`${RIT}-horn`, punkter(r.horn));
    this.satt(`${RIT}-fast`, punkter(r.fast ? [r.fast.at] : []));
    this.satt(`${RIT}-punkter`, punkter(r.utkast));
    this.synkaMat();
    this.synkaSektion();
    this.synkaSektionMarkor();
  }

  // ------------------------------------------------------------------ DOM-lager

  /** Kort förklaring nere till höger i vyn (t.ex. "Ljusgrå yta: tak, inte mark"), eller null. */
  settNotis(notis: string | null): void {
    this.notisEl.textContent = notis ?? "";
    this.notisEl.hidden = !notis;
  }

  private placeraEtikett(): void {
    const r = this.rit;
    const p = r.mark ?? r.fast?.at;
    if (!r.etikett || !p || this.slut) {
      this.etikettEl.hidden = true;
      return;
    }
    const [lng, lat] = ll(p);
    const s = this.karta.project([lng, lat]);
    this.etikettEl.textContent = r.etikett + (r.fast ? ` · ${r.fast.slag}` : "");
    this.etikettEl.style.left = `${Math.round(s.x + 14)}px`;
    this.etikettEl.style.top = `${Math.round(s.y + 14)}px`;
    this.etikettEl.hidden = false;
  }

  // ------------------------------------------------------------------ pekare och vy

  /** Pekarhändelser (för ritverktygen). Null stänger av. */
  settPekare(l: PekarLyssnare | null): void {
    this.lyssnare = l;
  }

  settMarkorstil(stil: string): void {
    this.karta.getCanvas().style.cursor = stil;
  }

  /** Meter per skärmpixel vid kartans mitt, räknat i SWEREF (exakt, via omprojicering av två skärmpunkter). */
  meterPerPx(): number {
    const c = this.karta.getCanvas();
    const y = c.clientHeight / 2;
    const x = c.clientWidth / 2;
    const a = this.karta.unproject([x - 50, y]);
    const b = this.karta.unproject([x + 50, y]);
    const [e1, n1] = wgs84TillSweref(a.lng, a.lat);
    const [e2, n2] = wgs84TillSweref(b.lng, b.lat);
    return Math.hypot(e2 - e1, n2 - n1) / 100;
  }

  hamtaVy(): Vy {
    const c = this.karta.getCenter();
    return { lng: c.lng, lat: c.lat, zoom: this.karta.getZoom() };
  }

  /** Sätter vyn utan att tala om det (för att följa en annan vy). */
  settVy(v: Vy): void {
    this.karta.jumpTo({ center: [v.lng, v.lat], zoom: v.zoom });
  }

  /** Anpassar vyn så att utbredningen syns med marginal (minst 40 m). */
  anpassa(u: Utbredning): void {
    const bredd = Math.max(u.x_max - u.x_min, 40);
    const hojd = Math.max(u.y_max - u.y_min, 40);
    const cx = (u.x_min + u.x_max) / 2;
    const cy = (u.y_min + u.y_max) / 2;
    const [a, b] = [ll([cx - bredd / 2, cy - hojd / 2]), ll([cx + bredd / 2, cy + hojd / 2])];
    this.karta.fitBounds([a, b], { padding: 36, duration: 0, maxZoom: 21 });
  }

  zooma(faktor: number): void {
    this.karta.zoomTo(this.karta.getZoom() - Math.log2(faktor), { duration: 0 });
    this.onVyAndrad?.();
  }

  /** Överför en SWEREF-punkt till skärmpixlar (för tester och etiketter). */
  tillSkarm(p: Punkt): { x: number; y: number } {
    const s = this.karta.project(ll(p));
    return { x: s.x, y: s.y };
  }

  /** Behöver anropas när behållaren ändrat storlek utan att fönstret ändrats (t.ex. paneler som fälls). */
  matOm(): void {
    this.karta.resize();
  }

  /** Alias för äldre anrop. */
  rita(): void {
    this.karta.triggerRepaint();
  }
}
