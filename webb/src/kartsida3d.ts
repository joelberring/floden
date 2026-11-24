/*
 * 3D-vyn i Resultat (steg 4): ett lager (egen vy) i samma lagerlista som kartan. Den här filen är liten och
 * laddas med flödet; själva 3D-koden och Three.js hämtas först när användaren öppnar 3D.
 *
 * Här kopplas stegets tillstånd (valt resultat, läge, lager i panelen, ritat förslag) till
 * 3D-scenen, och kameran synkas med kartan vid växling. Inget här räknar ett värde: scenen visar
 * serverns buffertar, och värden på marken läses av servern som i 2D.
 */
import type { LagerPost, ResultatSvar } from "./fragor-typer";
import { LAGEN, type Lage } from "./kartforklaring";
import type { ProjektInfo } from "./karta/kartapi";
import type { EgenVy, LagerDef, LagerLista } from "./karta/lager";
import type { PlanDok } from "./karta/plandok";
import { avstandFranMpp, mppFranKamera, SYNFALT_GRADER } from "./vy3d/koord";
import { valjSidor, type Lage as Lage3, type LagerUrval } from "./vy3d/lagerval";
import { VSC_TECKENFORKLARING } from "./vy3d/farger";
import type { Hover, Stil, Tillstand, Vy3d, Hamtare, Vy3dInfo } from "./vy3d/index";
import { planHus, planInnehall, planMark, planMurar } from "./vy3d/planinnehall.ts";
import type { ProjektVy } from "./types";

export { planHus, planInnehall, planMark, planMurar };

export const VY3D_ID = "vy:3d";

export interface Kontext3d {
  projekt: string;
  lager: LagerLista;
  info: ProjektInfo;
  dok: PlanDok;
  vy(): ProjektVy | null;
  resultat(): ResultatSvar | undefined;
  lage(): Lage;
  svep(): number;
  /** Takform per nytt hus (id -> K9-takform), bara avvikelser från förvalet (brutet tak). Utan: alla hus brutet tak. */
  takform?(): ReadonlyMap<string, string>;
  /** Visar resultatet av ett ritat förslag (då ritas förslaget inte om en gång till som överlägg). */
  arRitatResultat(): boolean;
  /** Kartans mitt (SWEREF), meter per pixel och ytans höjd i pixlar. */
  kartvy(): { e: number; n: number; mpp: number; hojdPx: number };
  /** Sätter kartans mitt och skala (när man går tillbaka till 2D). */
  settKartvy(e: number, n: number, mpp: number): void;
  /** Markens värden på en punkt (som när pekaren är över kartan); null när pekaren lämnar vyn. */
  avlasning(e: number | null, n: number | null): void;
  /** Text under vyn (fasadpunkt eller byggnad). */
  text(text: string | null): void;
  fel(text: string): void;
}

const lagerPost = (r: ResultatSvar | undefined, id: string | null): LagerPost | null => (id && r ? (r.lager.find((l) => l.id === id) ?? null) : null);

/** Lagren som är 3D-bara. Fasadlagret är tillgängligt först när körningen har fasadresultat. */
export function registrera3dLager(lager: LagerLista, fasad: { finns: boolean; skal: string }, luft: string, buller: string): void {
  // Panelen ritas om vid varje registrering: registrera bara när något ändrats, annars blir det en loop.
  const tand = (id: string, namn: string, hjalp: string, extra: Partial<LagerDef> = {}): void => {
    const g = lager.hamta(id);
    if (g && g.hjalp === hjalp && g.ejTillgangligt === extra.ejTillgangligt) return;
    lager.registrera({ id, grupp: "resultat", namn, synlig: true, opacitet: 1, hjalp, visning: "3d", ...extra });
  };
  if (!lager.hamta("3d:terrang")) lager.registrera({ id: "3d:terrang", grupp: "geometri", namn: "Terräng", synlig: true, opacitet: 1, hjalp: "Marken som höjdfält, rutvis och med olika detaljnivå efter avstånd.", visning: "3d" });
  tand("3d:fasad", "Dagsljus på fasaderna (VSC)", "Färgade punkter på fasaderna, en per våning (K11-fasadpunkter).", fasad.finns ? { opacitet: 1 } : { ejTillgangligt: fasad.skal, synlig: false });
  tand("3d:luft", "Luft: halter och vind", luft, { ejTillgangligt: luft, synlig: false });
  tand("3d:buller", "Buller på fasaderna", buller, { ejTillgangligt: buller, synlig: false });
}

function stilFran(lager: LagerLista, id: string): { synlig: boolean; opacitet: number } {
  const l = lager.hamta(id);
  return { synlig: Boolean(l?.synlig && !l.ejTillgangligt), opacitet: l?.opacitet ?? 1 };
}

/** 3D-vyn som egen vy i lagerpanelen. */
export function skapa3dVy(ktx: Kontext3d): EgenVy {
  return {
    id: "3d",
    namn: "3D-vy",
    knapp: { av: "Visa 3D", pa: "Visa kartan" },
    montera: (yta) => montera(ktx, yta),
  };
}

function montera(ktx: Kontext3d, yta: HTMLElement): { avmontera(): void; uppdatera(): void; uppdateraPlan(): void } {
  let vy: Vy3d | null = null;
  let hamtare: Hamtare | null = null;
  let info: Vy3dInfo | null = null;
  let avmonterad = false;
  let fasadAnalys: string | null = null;
  let tid = 0;
  let startHojd = 600;
  yta.innerHTML = `<div class="vy3d-laddar dampad" role="status">Laddar 3D-vyn …</div>`;

  const bygg = async (): Promise<Tillstand> => {
    const v = ktx.vy();
    const r = ktx.resultat();
    const urval: LagerUrval = { fore: lagerPost(r, v?.fore_lager ?? null), efter: lagerPost(r, v?.efter_lager ?? null), skillnad: lagerPost(r, v?.skillnad_lager ?? null) };
    let fasadScen: string[] = [];
    let kor = null;
    if (v && hamtare) {
      try {
        kor = await hamtare.korning(v.korning_id);
      } catch {
        kor = null;
      }
    }
    fasadAnalys = kor ? (Object.keys(kor.fasad)[0] ?? null) : null;
    if (kor && fasadAnalys) fasadScen = kor.fasad[fasadAnalys] ?? [];
    registrera3dLager(
      ktx.lager,
      {
        finns: fasadScen.length > 0,
        skal: v ? "Den här beräkningen har inga fasadresultat. Dagsljus på fasader (VSC) finns i vyerna för dagsljus." : "Välj ett resultat för att se fasadresultat.",
      },
      luftText(kor?.doman ?? {}),
      "Buller är under utveckling (metodkontroll pågår) och har inga fasadresultat att visa än.",
    );
    const stil: Stil = {
      terrang: stilFran(ktx.lager, "3d:terrang"),
      byggnader: stilFran(ktx.lager, "geo:byggnader"),
      resultat: resultatStil(ktx.lager),
      fasad: stilFran(ktx.lager, "3d:fasad"),
      plan: stilFran(ktx.lager, "plan:aktuellt"),
    };
    return {
      korning: v?.korning_id ?? null,
      uppdelning: valjSidor(ktx.lage() as Lage3, urval, fasadScen),
      stil,
      svep: ktx.svep(),
      plan: planHus(ktx),
      planMark: planMark(ktx),
      planMurar: planMurar(ktx),
      fasadAnalys,
      vattenTroskel: 0.02,
    };
  };

  const uppdatera = async (): Promise<void> => {
    if (!vy) return;
    const nr = ++tid;
    const t = await bygg();
    if (nr !== tid || avmonterad || !vy) return;
    vy.satt(t);
    visaKontroller(yta, ktx, t);
  };

  void (async () => {
    try {
      const modul = await import("./vy3d/index");
      const k = ktx.kartvy();
      startHojd = k.hojdPx;
      const { hamtare: h, info: i, skapa } = await modul.skapaVy3d(yta, ktx.projekt);
      if (avmonterad) return;
      hamtare = h;
      info = i;
      yta.querySelector(".vy3d-laddar")?.remove();
      vy = skapa({ e: k.e, n: k.n, mpp: k.mpp, hojdPx: k.hojdPx });
      vy.onFel = (text) => ktx.fel(text);
      // Mäthandtag för tools/webb_skarmbild.py --3d (bildfrekvens, rutstatus); används inte av sidan.
      (window as unknown as { __floden3d?: unknown }).__floden3d = vy;
      vy.onHover = (hov: Hover) => {
        if (hov === null) {
          ktx.avlasning(null, null);
          ktx.text(null);
        } else if (hov.slag === "mark") {
          ktx.text(null);
          ktx.avlasning(hov.e, hov.n);
        } else {
          ktx.avlasning(null, null);
          ktx.text(hov.text);
        }
      };
      monteraKnappar(yta, ktx, () => vy, () => info);
      await uppdatera();
      // Snett från början så att höjder syns; synken till kartan är exakt i startögonblicket.
      window.setTimeout(() => {
        if (vy && !avmonterad) lutaTill(vy, 52, 650);
      }, 350);
    } catch (e) {
      yta.innerHTML = `<p class="fel-text" role="alert">3D-vyn kunde inte startas: ${esc(e instanceof Error ? e.message : String(e))}. Kartan fungerar som vanligt.</p>`;
      ktx.fel(e instanceof Error ? e.message : String(e));
    }
  })();

  return {
    avmontera: () => {
      avmonterad = true;
      if (vy) {
        // Samma ruta i kartan som i 3D: måltavlan och avståndet ger kartans mitt och skala.
        const k = vy.hamtaKamera();
        ktx.settKartvy(k.mal.e, k.mal.n, mppFranKamera(k, yta.clientHeight || startHojd, SYNFALT_GRADER));
        vy.avmontera();
      }
      vy = null;
      yta.innerHTML = "";
    },
    uppdatera: () => void uppdatera(),
    // Bara det ritade förslaget byts (live medan man ritar); terräng och byggnader byggs inte om.
    uppdateraPlan: () => vy?.settPlan(planInnehall(ktx)),
  };
}

function luftText(doman: Record<string, string[]>): string {
  if (doman.luft) return "Luftresultatet visas som ett vanligt resultatlager (färg på marken). Vindfält i 3D finns inte i resultatet.";
  return "Luft är inte förkörd i demon: en beräkning tar mer än tio minuter. När ett luftresultat finns visas halter och vindkomfort här som en färgad yta på gånghöjd. Resultatet har en höjd, inget vindfält.";
}

function resultatStil(lager: LagerLista): { synlig: boolean; opacitet: number } {
  const tand = lager.iGrupp("resultat").filter((l) => l.id.startsWith("res:") && l.synlig);
  const forsta = tand[0];
  return { synlig: tand.length > 0, opacitet: forsta?.opacitet ?? 0.85 };
}

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

/** Lutar kameran mjukt från nuvarande lutning till `mal` grader (synken är exakt i starten, sedan lutas den). */
function lutaTill(vy: Vy3d, mal: number, ms: number): void {
  const start = vy.hamtaKamera();
  const t0 = performance.now();
  const steg = (): void => {
    const f = Math.min(1, (performance.now() - t0) / ms);
    const e = f * f * (3 - 2 * f);
    vy.settKamera({ ...vy.hamtaKamera(), lutning: start.lutning + (mal - start.lutning) * e });
    if (f < 1) requestAnimationFrame(steg);
  };
  requestAnimationFrame(steg);
}

function fyllUtbredning(vy: Vy3d, info: Vy3dInfo, ktx: Kontext3d): void {
  const u = info.utbredning;
  const mitt = { e: (u.x_min + u.x_max) / 2, n: (u.y_min + u.y_max) / 2, z: vy.markHojd((u.x_min + u.x_max) / 2, (u.y_min + u.y_max) / 2) };
  const bredd = Math.max(u.x_max - u.x_min, u.y_max - u.y_min);
  const hojdPx = Math.max(200, ktx.kartvy().hojdPx);
  vy.settKamera({ mal: mitt, avstand: avstandFranMpp((bredd * 1.15) / hojdPx, hojdPx, SYNFALT_GRADER), azimut: vy.hamtaKamera().azimut, lutning: 52 });
}

/** Knappar för kameran, i vyns hörn: bara kamerarörelser, inga värden. */
function monteraKnappar(yta: HTMLElement, ktx: Kontext3d, vy: () => Vy3d | null, info: () => Vy3dInfo | null): void {
  const rad = document.createElement("div");
  rad.className = "vy3d-knappar";
  rad.innerHTML = `
    <button type="button" class="knapp knapp-liten" data-kam="upp">Rakt uppifrån</button>
    <button type="button" class="knapp knapp-liten" data-kam="snett">Snett</button>
    <button type="button" class="knapp knapp-liten" data-kam="norr">Norr uppåt</button>
    <button type="button" class="knapp knapp-liten" data-kam="hel">Hela kvarteret</button>`;
  rad.addEventListener("click", (h) => {
    const v = vy();
    const i = info();
    const knapp = (h.target as HTMLElement).closest<HTMLElement>("[data-kam]");
    if (!v || !i || !knapp) return;
    const k = v.hamtaKamera();
    switch (knapp.dataset.kam) {
      case "upp":
        v.settKamera({ ...k, lutning: 0 });
        break;
      case "snett":
        v.settKamera({ ...k, lutning: 52 });
        break;
      case "norr":
        v.settKamera({ ...k, azimut: 0 });
        break;
      default:
        fyllUtbredning(v, i, ktx);
    }
  });
  yta.appendChild(rad);
  const hjalp = document.createElement("p");
  hjalp.className = "vy3d-hjalp dampad";
  hjalp.textContent = "Dra för att rotera, högerknapp för att flytta, rulla för att zooma.";
  yta.appendChild(hjalp);
  const delare = document.createElement("div");
  delare.className = "vy3d-delare";
  delare.hidden = true;
  yta.appendChild(delare);
}

function visaKontroller(yta: HTMLElement, ktx: Kontext3d, t: Tillstand): void {
  const delare = yta.querySelector<HTMLElement>(".vy3d-delare");
  if (delare) {
    delare.hidden = t.uppdelning.delning === "ingen";
    delare.style.left = t.uppdelning.delning === "svep" ? `${t.svep * 100}%` : "50%";
  }
  void ktx;
}

/** Teckenförklaring för fasadlagret (VSC) när det visas. */
export function fasadForklaringHtml(lage: Lage, harSkillnad: boolean): string {
  const namn = LAGEN.find(([id]) => id === lage)?.[1] ?? "";
  if (lage === "skillnad" && harSkillnad) {
    return `<div class="teckenforklaring"><h3 class="etikett-liten">Dagsljus på fasader, skillnad</h3>
      <div class="skala-rad"><span class="mono">minskning</span><span class="skala-band" style="background:linear-gradient(to right,#2b5d8c,#f5f5f5,#b4462b)"></span><span class="mono">ökning</span></div>
      <p class="dampad liten-text">Punktens VSC efter minus före, i procentenheter. Grå punkt: ingen jämförelse (ny våning eller nytt hus).</p></div>`;
  }
  const tf = VSC_TECKENFORKLARING;
  if (tf.typ !== "kontinuerlig") return "";
  return `<div class="teckenforklaring"><h3 class="etikett-liten">Dagsljus på fasader (VSC)${namn ? ` · ${esc(namn.toLowerCase())}` : ""}</h3>
    <div class="skala-rad"><span class="mono">0</span><span class="skala-band" style="background:linear-gradient(to right,${tf.stopp.join(",")})"></span><span class="mono">40 %</span></div>
    <p class="dampad liten-text">VSC är andelen himmel som syns från punkten, mätt som dagsljus. 40 % är det mesta en lodrät fasad kan få.</p></div>`;
}
