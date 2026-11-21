import maplibregl from "maplibre-gl";
import type {
  BboxTuple,
  DikeAtgardPayload,
  ExperimentAtgarderPayload,
  GataHojdsattningPayload,
  GeoJsonCollection,
  GrundmodellRattningPayload,
  HojdRattningPayload,
  JamforLage,
  KorSvar,
  LagerId,
  LappByggnadPayload,
  LappGataPayload,
  LappMagasinPayload,
  MarkJusteringPayload,
  OmradeLagerSvar,
  OppnaPassagePayload,
  PercentilNyckel,
  RiskSvar,
  RuttSvar,
  VallAtgardPayload,
} from "./types";

const REF_E_M = 150000.0;
const REF_N_M = 6580000.0;
const REF_LON_DEG = 18.0;
const REF_LAT_DEG = 59.33;
const METERS_PER_DEG_LAT = 111390.0;
const METERS_PER_DEG_LON = 111320.0 * Math.cos((REF_LAT_DEG * Math.PI) / 180.0);

export type RitLage =
  | "ingen"
  | "gata"
  | "byggnad"
  | "magasin"
  | "bro"
  | "rattning_hojd"
  | "dike"
  | "vall"
  | "gata_hojd"
  | "mark_justering";

export function arPolygonRitLage(lage: RitLage): boolean {
  return (
    lage === "byggnad" ||
    lage === "magasin" ||
    lage === "rattning_hojd" ||
    lage === "mark_justering"
  );
}

export interface LagerTillstand {
  id: LagerId;
  namn: string;
  synlig: boolean;
  opacitet: number;
}

export const STANDARD_LAGER: LagerTillstand[] = [
  { id: "hillshade", namn: "Markmodell (skuggning)", synlig: true, opacitet: 1.0 },
  { id: "byggnader", namn: "Byggnader", synlig: true, opacitet: 0.75 },
  { id: "brytlinjer", namn: "Brytlinjer", synlig: true, opacitet: 0.9 },
  { id: "sankor", namn: "Sänkor (noder)", synlig: true, opacitet: 0.9 },
  { id: "flodesvagar", namn: "Flödesvägar", synlig: true, opacitet: 0.8 },
  { id: "maxdjup", namn: "Maxdjup", synlig: true, opacitet: 0.85 },
  { id: "osakerhet", namn: "Osäkerhet (P10/P50/P90)", synlig: false, opacitet: 0.8 },
  { id: "differens", namn: "Differens (efter − före)", synlig: false, opacitet: 0.85 },
  { id: "risk", namn: "Kumulativ risk", synlig: false, opacitet: 0.85 },
  { id: "rutt", namn: "Räddningstjänst (vägrisk)", synlig: false, opacitet: 0.95 },
];

export function wgs84Till3011(lon: number, lat: number): [number, number] {
  const e = REF_E_M + (lon - REF_LON_DEG) * METERS_PER_DEG_LON;
  const n = REF_N_M + (lat - REF_LAT_DEG) * METERS_PER_DEG_LAT;
  return [Number(e.toFixed(2)), Number(n.toFixed(2))];
}

export function e3011TillWgs84(e: number, n: number): [number, number] {
  const lon = REF_LON_DEG + (e - REF_E_M) / METERS_PER_DEG_LON;
  const lat = REF_LAT_DEG + (n - REF_N_M) / METERS_PER_DEG_LAT;
  return [lon, lat];
}

function bboxWgs84TillImageCoordinates(
  bbox: BboxTuple,
): [[number, number], [number, number], [number, number], [number, number]] {
  const [minLon, minLat, maxLon, maxLat] = bbox;
  return [
    [minLon, maxLat],
    [maxLon, maxLat],
    [maxLon, minLat],
    [minLon, minLat],
  ];
}

const TOM_FC: GeoJsonCollection = { type: "FeatureCollection", features: [] };
const TOM_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";
const STANDARD_COORDS: [[number, number], [number, number], [number, number], [number, number]] = [
  [18.0, 59.3302],
  [18.0005, 59.3302],
  [18.0005, 59.33],
  [18.0, 59.33],
];

export class FlodenKartaKontroll {
  private readonly karta: maplibregl.Map;
  private laddad = false;
  private ritLage: RitLage = "ingen";
  private ritPunkter3011: Array<[number, number]> = [];
  private lagerStatus: Map<LagerId, LagerTillstand> = new Map();
  private aktuelltOmrade: OmradeLagerSvar | null = null;
  private aktuelltResultat: KorSvar | null = null;
  private aktuellRisk: RiskSvar | null = null;
  private aktuellRutt: RuttSvar | null = null;
  private jamforLage: JamforLage = "svep";
  private svepAndel = 0.5;
  private aktivPercentil: PercentilNyckel = "p50";

  private onKoordinatHover?: (e: number, n: number) => void;
  private onRitPunktAndrad?: (antal: number, lage: RitLage) => void;

  constructor(
    containerId: string,
    alternativ?: {
      onKoordinatHover?: (e: number, n: number) => void;
      onRitPunktAndrad?: (antal: number, lage: RitLage) => void;
    },
  ) {
    this.onKoordinatHover = alternativ?.onKoordinatHover;
    this.onRitPunktAndrad = alternativ?.onRitPunktAndrad;

    for (const l of STANDARD_LAGER) {
      this.lagerStatus.set(l.id, { ...l });
    }

    this.karta = new maplibregl.Map({
      container: containerId,
      style: {
        version: 8,
        sources: {},
        layers: [
          {
            id: "bakgrund",
            type: "background",
            paint: { "background-color": "#efefef" },
          },
        ],
      },
      center: [REF_LON_DEG, REF_LAT_DEG],
      zoom: 17,
      attributionControl: false,
    });

    this.karta.addControl(new maplibregl.NavigationControl(), "top-right");

    this.karta.on("load", () => {
      this.initieraKallorOchLager();
      this.laddad = true;
      if (this.aktuelltOmrade) {
        this.uppdateraOmradeData(this.aktuelltOmrade);
      }
      if (this.aktuelltResultat) {
        this.uppdateraResultatData(this.aktuelltResultat);
      }
      if (this.aktuellRisk) {
        this.uppdateraRiskData(this.aktuellRisk);
      }
      if (this.aktuellRutt) {
        this.uppdateraRuttData(this.aktuellRutt);
      }
      this.appliceraSynlighetOchOpacitet();
    });

    this.karta.on("mousemove", (ev) => {
      const [e, n] = wgs84Till3011(ev.lngLat.lng, ev.lngLat.lat);
      this.onKoordinatHover?.(e, n);
    });

    this.karta.on("click", (ev) => {
      if (this.ritLage === "ingen") {
        return;
      }
      const punkt = wgs84Till3011(ev.lngLat.lng, ev.lngLat.lat);
      this.ritPunkter3011.push(punkt);
      this.uppdateraRitSkiss();
      this.onRitPunktAndrad?.(this.ritPunkter3011.length, this.ritLage);
    });
  }

  private initieraKallorOchLager(): void {
    this.karta.addSource("src-hillshade", {
      type: "image",
      url: TOM_PNG,
      coordinates: STANDARD_COORDS,
    });
    this.karta.addSource("src-djup-fore", {
      type: "image",
      url: TOM_PNG,
      coordinates: STANDARD_COORDS,
    });
    this.karta.addSource("src-djup-efter", {
      type: "image",
      url: TOM_PNG,
      coordinates: STANDARD_COORDS,
    });
    this.karta.addSource("src-diff", {
      type: "image",
      url: TOM_PNG,
      coordinates: STANDARD_COORDS,
    });
    this.karta.addSource("src-osakerhet", {
      type: "image",
      url: TOM_PNG,
      coordinates: STANDARD_COORDS,
    });
    this.karta.addSource("src-risk", {
      type: "image",
      url: TOM_PNG,
      coordinates: STANDARD_COORDS,
    });

    this.karta.addSource("src-byggnader", { type: "geojson", data: TOM_FC as never });
    this.karta.addSource("src-brytlinjer", { type: "geojson", data: TOM_FC as never });
    this.karta.addSource("src-flodesvagar", { type: "geojson", data: TOM_FC as never });
    this.karta.addSource("src-sankor", { type: "geojson", data: TOM_FC as never });
    this.karta.addSource("src-rutt", { type: "geojson", data: TOM_FC as never });
    this.karta.addSource("src-lapp-skiss", { type: "geojson", data: TOM_FC as never });

    this.karta.addLayer({
      id: "lyr-hillshade",
      type: "raster",
      source: "src-hillshade",
      paint: { "raster-resampling": "nearest", "raster-opacity": 1.0 },
    });
    this.karta.addLayer({
      id: "lyr-djup-fore",
      type: "raster",
      source: "src-djup-fore",
      paint: { "raster-resampling": "nearest", "raster-opacity": 0.85 },
    });
    this.karta.addLayer({
      id: "lyr-djup-efter",
      type: "raster",
      source: "src-djup-efter",
      paint: { "raster-resampling": "nearest", "raster-opacity": 0.85 },
    });
    this.karta.addLayer({
      id: "lyr-osakerhet",
      type: "raster",
      source: "src-osakerhet",
      paint: { "raster-resampling": "nearest", "raster-opacity": 0.8 },
    });
    this.karta.addLayer({
      id: "lyr-diff",
      type: "raster",
      source: "src-diff",
      paint: { "raster-resampling": "nearest", "raster-opacity": 0.85 },
    });
    this.karta.addLayer({
      id: "lyr-risk",
      type: "raster",
      source: "src-risk",
      paint: { "raster-resampling": "nearest", "raster-opacity": 0.85 },
    });
    this.karta.addLayer({
      id: "lyr-byggnader",
      type: "fill",
      source: "src-byggnader",
      paint: {
        "fill-color": "#c8c8c8",
        "fill-opacity": 0.9,
        "fill-outline-color": "#111111",
      },
    });
    this.karta.addLayer({
      id: "lyr-flodesvagar",
      type: "line",
      source: "src-flodesvagar",
      paint: {
        "line-color": "#2b5d8c",
        "line-width": 1.4,
        "line-opacity": 0.8,
      },
    });
    this.karta.addLayer({
      id: "lyr-brytlinjer",
      type: "line",
      source: "src-brytlinjer",
      paint: {
        "line-color": "#111111",
        "line-width": 1.6,
        "line-opacity": 0.9,
      },
    });
    this.karta.addLayer({
      id: "lyr-rutt",
      type: "line",
      source: "src-rutt",
      paint: {
        "line-color": [
          "case",
          [">=", ["coalesce", ["get", "kumulativ_sannolikhet"], 0], 0.25],
          "#b4462b",
          [">", ["coalesce", ["get", "kumulativ_sannolikhet"], 0], 0],
          "#d9895f",
          "#6b6b6b",
        ],
        "line-width": 4,
        "line-opacity": 0.95,
      },
    });
    this.karta.addLayer({
      id: "lyr-sankor",
      type: "circle",
      source: "src-sankor",
      paint: {
        "circle-radius": 5,
        "circle-color": "#2b5d8c",
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1.5,
        "circle-opacity": 0.9,
      },
    });
    this.karta.addLayer({
      id: "lyr-lapp-skiss",
      type: "line",
      source: "src-lapp-skiss",
      paint: {
        "line-color": "#111111",
        "line-width": 2,
        "line-dasharray": [2, 1],
      },
    });
  }

  public visaOmrade(omrade: OmradeLagerSvar): void {
    this.aktuelltOmrade = omrade;
    this.aktuelltResultat = null;
    this.aktuellRisk = null;
    this.aktuellRutt = null;
    const riskStatus = this.lagerStatus.get("risk");
    if (riskStatus) {
      this.lagerStatus.set("risk", { ...riskStatus, synlig: false });
    }
    const ruttStatus = this.lagerStatus.get("rutt");
    if (ruttStatus) {
      this.lagerStatus.set("rutt", { ...ruttStatus, synlig: false });
    }
    if (this.laddad) {
      this.uppdateraOmradeData(omrade);
      this.appliceraSynlighetOchOpacitet();
    }
  }

  /** Mäter om kartan och zoomar till området, t.ex. när kartsidan blir synlig första gången. */
  public anpassaVy(): void {
    this.karta.resize();
    if (!this.aktuelltOmrade) {
      return;
    }
    const [minLon, minLat, maxLon, maxLat] = this.aktuelltOmrade.bbox_wgs84;
    this.karta.fitBounds(
      [
        [minLon, minLat],
        [maxLon, maxLat],
      ],
      { padding: 36, duration: 0 },
    );
  }

  public visaResultat(resultat: KorSvar): void {
    this.aktuelltResultat = resultat;
    if (this.laddad) {
      this.uppdateraResultatData(resultat);
      this.appliceraSynlighetOchOpacitet();
    }
  }

  public visaRisk(risk: RiskSvar): void {
    this.aktuellRisk = risk;
    const nu = this.lagerStatus.get("risk");
    if (nu) {
      this.lagerStatus.set("risk", { ...nu, synlig: true });
    }
    if (this.laddad) {
      this.uppdateraRiskData(risk);
      this.appliceraSynlighetOchOpacitet();
    }
  }

  public visaRutt(rutt: RuttSvar): void {
    this.aktuellRutt = rutt;
    const nu = this.lagerStatus.get("rutt");
    if (nu) {
      this.lagerStatus.set("rutt", { ...nu, synlig: true });
    }
    if (this.laddad) {
      this.uppdateraRuttData(rutt);
      this.appliceraSynlighetOchOpacitet();
    }
  }

  private uppdateraOmradeData(omrade: OmradeLagerSvar): void {
    const coords = bboxWgs84TillImageCoordinates(omrade.bbox_wgs84);
    this.uppdateraBildKalla("src-hillshade", omrade.hillshade.data_uri, coords);
    this.uppdateraBildKalla("src-djup-fore", omrade.bas_max_djup.data_uri, coords);
    this.uppdateraBildKalla("src-djup-efter", omrade.bas_max_djup.data_uri, coords);
    this.uppdateraBildKalla("src-diff", TOM_PNG, coords);
    this.uppdateraBildKalla("src-risk", TOM_PNG, coords);
    this.uppdateraBildKalla(
      "src-osakerhet",
      omrade.osakerhet[this.aktivPercentil].data_uri,
      coords,
    );

    this.uppdateraGeoJsonKalla("src-byggnader", omrade.byggnader);
    this.uppdateraGeoJsonKalla("src-brytlinjer", omrade.brytlinjer);
    this.uppdateraGeoJsonKalla("src-flodesvagar", omrade.flodesvagar);
    this.uppdateraGeoJsonKalla("src-sankor", omrade.sankor);
    this.uppdateraGeoJsonKalla("src-rutt", TOM_FC);

    const [minLon, minLat, maxLon, maxLat] = omrade.bbox_wgs84;
    this.karta.fitBounds(
      [
        [minLon, minLat],
        [maxLon, maxLat],
      ],
      { padding: 36, duration: 250 },
    );
  }

  private uppdateraResultatData(res: KorSvar): void {
    const coords = bboxWgs84TillImageCoordinates(res.bbox_wgs84);
    this.uppdateraBildKalla("src-hillshade", res.efter_hillshade.data_uri, coords);
    this.uppdateraBildKalla("src-djup-fore", res.fore.data_uri, coords);
    this.uppdateraBildKalla("src-djup-efter", res.efter.data_uri, coords);
    this.uppdateraBildKalla("src-diff", res.differens.data_uri, coords);
    this.uppdateraBildKalla(
      "src-osakerhet",
      res.osakerhet[this.aktivPercentil].data_uri,
      coords,
    );

    this.uppdateraGeoJsonKalla("src-byggnader", res.efter_lager.byggnader);
    this.uppdateraGeoJsonKalla("src-brytlinjer", res.efter_lager.brytlinjer);
    this.uppdateraGeoJsonKalla("src-flodesvagar", res.efter_lager.flodesvagar);
    this.uppdateraGeoJsonKalla("src-sankor", res.efter_lager.sankor);
  }

  private uppdateraRiskData(risk: RiskSvar): void {
    const coords = bboxWgs84TillImageCoordinates(risk.bbox_wgs84);
    this.uppdateraBildKalla("src-risk", risk.kumulativ_risk_karta.data_uri, coords);
  }

  private uppdateraRuttData(rutt: RuttSvar): void {
    this.uppdateraGeoJsonKalla("src-rutt", rutt.vagnat_geojson);
  }

  private uppdateraBildKalla(
    id: string,
    url: string,
    coordinates: [[number, number], [number, number], [number, number], [number, number]],
  ): void {
    const src = this.karta.getSource(id) as maplibregl.ImageSource | undefined;
    src?.updateImage({ url, coordinates });
  }

  private uppdateraGeoJsonKalla(id: string, fc: GeoJsonCollection): void {
    const src = this.karta.getSource(id) as maplibregl.GeoJSONSource | undefined;
    src?.setData(fc as never);
  }

  public sattLager(id: LagerId, andring: Partial<Pick<LagerTillstand, "synlig" | "opacitet">>): void {
    const nu = this.lagerStatus.get(id);
    if (!nu) {
      return;
    }
    this.lagerStatus.set(id, { ...nu, ...andring });
    if (this.laddad) {
      this.appliceraSynlighetOchOpacitet();
    }
  }

  public sattJamforLage(lage: JamforLage, svepProcent: number): void {
    this.jamforLage = lage;
    this.svepAndel = Math.max(0, Math.min(1, svepProcent / 100));
    if (this.laddad) {
      this.appliceraSynlighetOchOpacitet();
    }
  }

  public sattPercentil(pct: PercentilNyckel): void {
    this.aktivPercentil = pct;
    const aktivKalla = this.aktuelltResultat ?? this.aktuelltOmrade;
    if (this.laddad && aktivKalla) {
      const coords = bboxWgs84TillImageCoordinates(aktivKalla.bbox_wgs84);
      this.uppdateraBildKalla("src-osakerhet", aktivKalla.osakerhet[pct].data_uri, coords);
    }
  }

  private appliceraSynlighetOchOpacitet(): void {
    const get = (id: LagerId): LagerTillstand =>
      this.lagerStatus.get(id) ?? { id, namn: id, synlig: false, opacitet: 1 };

    const hs = get("hillshade");
    this.setLayerVis("lyr-hillshade", hs.synlig);
    this.karta.setPaintProperty("lyr-hillshade", "raster-opacity", hs.opacitet);

    const bygg = get("byggnader");
    this.setLayerVis("lyr-byggnader", bygg.synlig);
    this.karta.setPaintProperty("lyr-byggnader", "fill-opacity", bygg.opacitet);

    const bryt = get("brytlinjer");
    this.setLayerVis("lyr-brytlinjer", bryt.synlig);
    this.karta.setPaintProperty("lyr-brytlinjer", "line-opacity", bryt.opacitet);

    const flode = get("flodesvagar");
    this.setLayerVis("lyr-flodesvagar", flode.synlig);
    this.karta.setPaintProperty("lyr-flodesvagar", "line-opacity", flode.opacitet);

    const sank = get("sankor");
    this.setLayerVis("lyr-sankor", sank.synlig);
    this.karta.setPaintProperty("lyr-sankor", "circle-opacity", sank.opacitet);

    const osak = get("osakerhet");
    this.setLayerVis("lyr-osakerhet", osak.synlig);
    this.karta.setPaintProperty("lyr-osakerhet", "raster-opacity", osak.opacitet);

    const risk = get("risk");
    this.setLayerVis("lyr-risk", risk.synlig);
    this.karta.setPaintProperty("lyr-risk", "raster-opacity", risk.opacitet);

    const rutt = get("rutt");
    this.setLayerVis("lyr-rutt", rutt.synlig);
    this.karta.setPaintProperty("lyr-rutt", "line-opacity", rutt.opacitet);

    const djup = get("maxdjup");
    const diff = get("differens");

    if (this.jamforLage === "fore") {
      this.setLayerVis("lyr-djup-fore", djup.synlig);
      this.setLayerVis("lyr-djup-efter", false);
      this.setLayerVis("lyr-diff", diff.synlig);
      this.karta.setPaintProperty("lyr-djup-fore", "raster-opacity", djup.opacitet);
    } else if (this.jamforLage === "efter") {
      this.setLayerVis("lyr-djup-fore", false);
      this.setLayerVis("lyr-djup-efter", djup.synlig);
      this.setLayerVis("lyr-diff", diff.synlig);
      this.karta.setPaintProperty("lyr-djup-efter", "raster-opacity", djup.opacitet);
    } else if (this.jamforLage === "differens") {
      this.setLayerVis("lyr-djup-fore", false);
      this.setLayerVis("lyr-djup-efter", false);
      this.setLayerVis("lyr-diff", true);
      this.karta.setPaintProperty("lyr-diff", "raster-opacity", diff.opacitet);
    } else {
      // Svepläge: blanda opacitet mellan före och efter så reglaget fungerar direkt i MapLibre
      this.setLayerVis("lyr-djup-fore", djup.synlig);
      this.setLayerVis("lyr-djup-efter", djup.synlig);
      this.setLayerVis("lyr-diff", diff.synlig);
      this.karta.setPaintProperty(
        "lyr-djup-fore",
        "raster-opacity",
        djup.opacitet * (1 - this.svepAndel),
      );
      this.karta.setPaintProperty(
        "lyr-djup-efter",
        "raster-opacity",
        djup.opacitet * this.svepAndel,
      );
    }
    this.karta.setPaintProperty("lyr-diff", "raster-opacity", diff.opacitet);
  }

  private setLayerVis(layerId: string, synlig: boolean): void {
    this.karta.setLayoutProperty(layerId, "visibility", synlig ? "visible" : "none");
  }

  public startaRitning(lage: RitLage): void {
    this.ritLage = lage;
    this.ritPunkter3011 = [];
    this.karta.getCanvas().style.cursor = lage === "ingen" ? "" : "crosshair";
    this.uppdateraRitSkiss();
    this.onRitPunktAndrad?.(0, lage);
  }

  public avbrytRitning(): void {
    this.startaRitning("ingen");
  }

  public slutforRitning(alternativ: {
    sektion: string;
    entreRh2000: number;
    magasinVolymM3: number;
    broBreddM: number;
    expHojdDeltaM: number;
    expBreddM: number;
    index: number;
  }):
    | { typ: "gata"; data: LappGataPayload }
    | { typ: "byggnad"; data: LappByggnadPayload }
    | { typ: "magasin"; data: LappMagasinPayload }
    | { typ: "bro"; data: OppnaPassagePayload }
    | { typ: "rattning_hojd"; data: HojdRattningPayload }
    | { typ: "dike"; data: DikeAtgardPayload }
    | { typ: "vall"; data: VallAtgardPayload }
    | { typ: "gata_hojd"; data: GataHojdsattningPayload }
    | { typ: "mark_justering"; data: MarkJusteringPayload }
    | null {
    const punkter = [...this.ritPunkter3011];
    const lage = this.ritLage;
    this.avbrytRitning();

    if (lage === "gata") {
      if (punkter.length < 2) {
        return null;
      }
      return {
        typ: "gata",
        data: {
          gata_id: `gata-${alternativ.index}`,
          mittlinje: punkter,
          sektion: alternativ.sektion,
        },
      };
    }

    if (lage === "byggnad") {
      if (punkter.length < 3) {
        return null;
      }
      const meanE = punkter.reduce((acc, p) => acc + p[0], 0) / punkter.length;
      const meanN = punkter.reduce((acc, p) => acc + p[1], 0) / punkter.length;
      return {
        typ: "byggnad",
        data: {
          byggnad_id: `byggnad-${alternativ.index}`,
          polygon: punkter,
          entre_rh2000: alternativ.entreRh2000,
          entre_punkt: [Number(meanE.toFixed(2)), Number(meanN.toFixed(2))],
        },
      };
    }

    if (lage === "magasin") {
      if (punkter.length < 3) {
        return null;
      }
      return {
        typ: "magasin",
        data: {
          magasin_id: `magasin-${alternativ.index}`,
          polygon: punkter,
          volym_m3: alternativ.magasinVolymM3,
        },
      };
    }

    if (lage === "bro") {
      if (punkter.length < 2) {
        return null;
      }
      return {
        typ: "bro",
        data: {
          passage_id: `bro-${alternativ.index}`,
          typ: "bro",
          linje: punkter,
          bredd_m: Math.max(0.5, alternativ.broBreddM),
          aktiv: true,
        },
      };
    }

    if (lage === "rattning_hojd") {
      if (punkter.length < 3) {
        return null;
      }
      return {
        typ: "rattning_hojd",
        data: {
          rattning_id: `rattning-${alternativ.index}`,
          polygon: punkter,
          utjamna_fran_rand: true,
          ta_bort_byggnad: true,
          aktiv: true,
        },
      };
    }

    if (lage === "dike") {
      if (punkter.length < 2) {
        return null;
      }
      return {
        typ: "dike",
        data: {
          atgard_id: `dike-${alternativ.index}`,
          linje: punkter,
          botten_bredd_m: Math.max(0.5, alternativ.expBreddM),
          slantslutning: 2.0,
          djup_m: Math.max(0.05, Math.abs(alternativ.expHojdDeltaM)),
          aktiv: true,
        },
      };
    }

    if (lage === "vall") {
      if (punkter.length < 2) {
        return null;
      }
      return {
        typ: "vall",
        data: {
          atgard_id: `vall-${alternativ.index}`,
          linje: punkter,
          kron_bredd_m: Math.max(0.5, alternativ.expBreddM),
          slantslutning: 2.0,
          hojd_over_mark_m: Math.max(0.05, Math.abs(alternativ.expHojdDeltaM)),
          aktiv: true,
        },
      };
    }

    if (lage === "gata_hojd") {
      if (punkter.length < 2) {
        return null;
      }
      return {
        typ: "gata_hojd",
        data: {
          atgard_id: `gata-hojd-${alternativ.index}`,
          linje: punkter,
          bredd_m: Math.max(1.0, alternativ.expBreddM),
          delta_z_m: alternativ.expHojdDeltaM,
          tvarfall: 0.025,
          kantsten_hojd_m: 0.12,
          aktiv: true,
        },
      };
    }

    if (lage === "mark_justering") {
      if (punkter.length < 3) {
        return null;
      }
      return {
        typ: "mark_justering",
        data: {
          atgard_id: `mark-${alternativ.index}`,
          polygon: punkter,
          delta_z_m: alternativ.expHojdDeltaM,
          aktiv: true,
        },
      };
    }

    return null;
  }

  public ritaLappObjekt(
    gator: LappGataPayload[],
    byggnader: LappByggnadPayload[],
    magasin: LappMagasinPayload[],
    grundmodell?: GrundmodellRattningPayload,
    experiment?: ExperimentAtgarderPayload,
  ): void {
    const features: GeoJsonCollection["features"] = [];
    for (const g of gator) {
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: g.mittlinje.map(([e, n]) => e3011TillWgs84(e, n)),
        },
        properties: { id: g.gata_id },
      });
    }
    for (const b of byggnader) {
      const ring = [...b.polygon, b.polygon[0] ?? [0, 0]].map(([e, n]) =>
        e3011TillWgs84(e, n),
      );
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: ring,
        },
        properties: { id: b.byggnad_id },
      });
    }
    for (const m of magasin) {
      const ring = [...m.polygon, m.polygon[0] ?? [0, 0]].map(([e, n]) =>
        e3011TillWgs84(e, n),
      );
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: ring,
        },
        properties: { id: m.magasin_id },
      });
    }

    for (const p of grundmodell?.passager ?? []) {
      if (!p.aktiv) {
        continue;
      }
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: p.linje.map(([e, n]) => e3011TillWgs84(e, n)),
        },
        properties: { id: p.passage_id },
      });
    }
    for (const h of grundmodell?.hojd_rattningar ?? []) {
      if (!h.aktiv) {
        continue;
      }
      const ring = [...h.polygon, h.polygon[0] ?? [0, 0]].map(([e, n]) =>
        e3011TillWgs84(e, n),
      );
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: ring,
        },
        properties: { id: h.rattning_id },
      });
    }

    for (const d of experiment?.diken ?? []) {
      if (!d.aktiv) {
        continue;
      }
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: d.linje.map(([e, n]) => e3011TillWgs84(e, n)),
        },
        properties: { id: d.atgard_id },
      });
    }
    for (const v of experiment?.vallar ?? []) {
      if (!v.aktiv) {
        continue;
      }
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: v.linje.map(([e, n]) => e3011TillWgs84(e, n)),
        },
        properties: { id: v.atgard_id },
      });
    }
    for (const g of experiment?.gator ?? []) {
      if (!g.aktiv) {
        continue;
      }
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: g.linje.map(([e, n]) => e3011TillWgs84(e, n)),
        },
        properties: { id: g.atgard_id },
      });
    }
    for (const m of experiment?.mark_justeringar ?? []) {
      if (!m.aktiv) {
        continue;
      }
      const ring = [...m.polygon, m.polygon[0] ?? [0, 0]].map(([e, n]) =>
        e3011TillWgs84(e, n),
      );
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: ring,
        },
        properties: { id: m.atgard_id },
      });
    }

    if (this.laddad) {
      this.uppdateraGeoJsonKalla("src-lapp-skiss", {
        type: "FeatureCollection",
        features,
      });
    }
  }

  private uppdateraRitSkiss(): void {
    if (!this.laddad) {
      return;
    }
    if (this.ritPunkter3011.length < 2) {
      this.uppdateraGeoJsonKalla("src-lapp-skiss", TOM_FC);
      return;
    }
    const coords = this.ritPunkter3011.map(([e, n]) => e3011TillWgs84(e, n));
    if (arPolygonRitLage(this.ritLage) && coords.length >= 3) {
      const first = coords[0];
      if (first) {
        coords.push(first);
      }
    }
    this.uppdateraGeoJsonKalla("src-lapp-skiss", {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "LineString",
            coordinates: coords,
          },
          properties: {},
        },
      ],
    });
  }
}
