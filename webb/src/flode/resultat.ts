/*
 * Steg 4 · Resultat: kartan tar största ytan med en flytande växel (Före, Efter, Skillnad, Svep, 3D)
 * och panelen har flikarna Skyfall, Sol och Ekologi (skiss): en mening med slutsatsen, fyra nyckeltal, en liten
 * stapeljämförelse och en rad om regn och antaganden. Talen kommer ur körningen: mätvärdestabellen
 * och markytorna (`/api/korningar/<id>/marksiffror`, samma funktion som projektsidans tal). Det
 * som saknas visas som "–". Svepet och 3D-vyn är de befintliga.
 *
 * Knappen "Visa osäkerhet" (Skyfall och Sol) hämtar intervallen för vatten (K40) och SVF (K41) och
 * de känsliga överfallen (bifurkationer) och lägger dem som nyckeltal med [min; max] och som lagret
 * "Känsliga överfall" på kartan. Värdet under markören läses ur rasterfilen på servern, och
 * "Visa värden även på tak" och "Så läser du kartan" kommer från den gamla kartsidan.
 */
import { hamtaAvlasning, hamtaMarkhojd, hamtaProjekt, hamtaResultat, lasJson } from "../api.ts";
import { ApiFel } from "../apifel.ts";
import { markhojdAvlasning } from "./markhojd.ts";
import { monteraOsmKryss } from "./osmval.ts";
import type { AvlasningSvar, LagerPost, ResultatSvar } from "../fragor-typer.ts";
import { hamtaBifurkationer, hamtaProjektInfo, hamtaSvfIntervall, hamtaVattenIntervall, type BifurkationsPunktPost, type ProjektInfo } from "../karta/kartapi.ts";
import { Kartmotor, type RasterLager, type VyByggnad } from "../karta/kartmotor.ts";
import { LagerLista } from "../karta/lager.ts";
import { OVERFALL_LAGER_ID, overfallLager, ritadeOverfall } from "../karta/overfall.ts";
import { PlanDok } from "../karta/plandok.ts";
import { LAGEN, lasKartan, type Lage } from "../kartforklaring.ts";
import { fasadForklaringHtml, skapa3dVy, VY3D_ID } from "../kartsida3d.ts";
import { esc } from "../sidor.ts";
import { felBeskedHtml, kopplaLaddaOm, sidFel } from "./felhandling.ts";
import { kontoLage } from "../konto.ts";
import { swerefTillWgs84, wgs84TillSweref } from "../sweref.ts";
import { formatera, formateraHogst } from "../tal.ts";
import type { ProjektSvar, ProjektVy } from "../types.ts";
import { meterPerPxFranZoom, zoomFranMeterPerPx } from "../vy3d/koord.ts";
import { flodeAdress } from "./adress.ts";
import { extraAnalyserHtml, kopplaExtraAnalyser } from "./extranalyser.ts";
import { BasLager, skapaKarta } from "./kartdel.ts";
import { arPagaende, vidKorning } from "./korning.ts";
import { antagandeRader, intervallForklaring, jamforelse, kallRad, nyckeltal, regnText, resultatEtikett, slutsats, type Amne, type EkologiSvar, type Marksiffror, type Underlag } from "./nyckeltal.ts";
import { osakerhetTal, overfallRader, type OsakerhetSvar } from "./osakerhet.ts";
import { kroppHtml, type OsakerhetVisning } from "./panelhtml.ts";
import type { Avslutbar } from "./projektlista.ts";
import { tillstandFor } from "./tillstand.ts";

interface MarkSvar {
  lager_id: string;
  troskel: number;
  siffror: Marksiffror | null;
}

type Kalla = "ritat" | "projekt" | "lage";

const AMNEN_ORDNING: Amne[] = ["vatten", "dagsljus", "ekologi"];
const AMNESNAMN: Record<Amne, string> = { vatten: "Skyfall", dagsljus: "Sol", ekologi: "Ekologi" };

const lagerPost = (r: ResultatSvar | undefined, id: string | null): LagerPost | null => (id && r ? (r.lager.find((l) => l.id === id) ?? null) : null);

/** Teckenförklaring som i skissen: rubrik, band och ändpunkter. */
export function teckenHtml(l: LagerPost, rubrik: string): string {
  const tf = l.teckenforklaring;
  const f = (v: number): string => formateraHogst(v, 2);
  const enhet = l.enhet ? ` ${esc(l.enhet)}` : "";
  let band = "";
  if (tf.typ === "divergerande") {
    band = `<span class="flode-band" style="background:linear-gradient(90deg,${tf.farg_minskning},#f6f5f0,${tf.farg_okning})"></span>
      <span class="flode-bandtext"><span>−${f(tf.max)}${enhet}</span><span>0</span><span>+${f(tf.max)}${enhet}</span></span>`;
  } else if (tf.typ === "kontinuerlig") {
    band = `<span class="flode-band" style="background:linear-gradient(90deg,${tf.stopp.join(",")})"></span>
      <span class="flode-bandtext"><span>${f(tf.min)}</span><span>${f(tf.max)}${enhet}</span></span>`;
  } else {
    band = `<span class="flode-klasser">${tf.klasser.map((k) => `<span><i style="background:${k.farg}"></i>från ${f(k.fran)}${enhet}</span>`).join("")}</span>`;
  }
  return `<span class="flode-kort-rubrik">${esc(rubrik)}</span>${band}`;
}

export function visaResultat(rot: HTMLElement, projekt: string): Avslutbar {
  const t = tillstandFor(projekt);
  let avslutad = false;
  const ac = new AbortController();
  let projektSvar: ProjektSvar | null = null;
  let info: ProjektInfo | null = null;
  let amne: Amne = "vatten";
  let kalla: Kalla = "projekt";
  let vy: ProjektVy | null = null;
  let r: ResultatSvar | undefined;
  let mark: MarkSvar | null = null;
  let ekologi: EkologiSvar | null = null;
  let ekologiFel: string | null = null;
  let natverkLista: Array<{ natverk_id: string; visningsnamn: string; korbar: boolean; lokalt: boolean }> = [];
  let valtNatverk: string | null = null;
  let lage: Lage = "skillnad";
  let svep = 0.5;
  let a: Kartmotor | null = null;
  let b: Kartmotor | null = null;
  let bas: BasLager | null = null;
  let vy3d: { avmontera(): void; uppdatera?(): void } | null = null;
  let seq = 0;
  let visaTak = false;
  let avlSeq = 0;
  let avlTimer: number | null = null;
  // Osäkerheten gäller projektets markmodell och hämtas per ämne när man trycker på knappen.
  const osak: OsakerhetSvar = { vatten: null, svf: null, bifurkation: null, fel: [] };
  const osakPa = new Set<Amne>();
  let osakLaddar = false;
  let onskadVy: string | null = null;
  let overfall: BifurkationsPunktPost[] = [];
  const kartLager = new LagerLista(projekt);
  const cache = new Map<string, ResultatSvar>();

  rot.innerHTML = `
    <div class="flode-sida flode-fast">
      <section class="flode-karta" aria-label="Karta">
        <div class="flode-karta-yta" id="rs-a"></div>
        <div class="flode-karta-yta flode-b" id="rs-b" hidden></div>
        <div class="flode-karta-yta" id="rs-3d" hidden></div>
        <div class="flode-vaxel" role="group" aria-label="Visning" id="rs-vaxel">${LAGEN.map(([id, n]) => `<button type="button" data-lage="${id}" aria-pressed="${id === lage}">${n}</button>`).join("")}</div>
        <div class="flode-svep" id="rs-svep-rad" hidden>
          <span>Före</span><input id="rs-svep" type="range" min="0" max="100" value="50" aria-label="Svepgräns: till vänster före, till höger efter" /><span>Efter</span>
        </div>
        <div class="flode-kartkort flode-forklaring" id="rs-tecken" hidden></div>
        <div class="flode-kartkort flode-kartkort-hoger flode-avlasning" id="rs-avl" role="status" hidden></div>
        <div class="flode-kartkort flode-osm" id="rs-osm" hidden></div>
      </section>
      <aside class="flode-panel" aria-label="Resultat">
        <div role="tablist" aria-label="Ämne" class="flode-flikar" id="rs-flikar">
          <button type="button" role="tab" data-amne="vatten" aria-selected="true">Skyfall</button>
          <button type="button" role="tab" data-amne="dagsljus" aria-selected="false">Sol</button>
          <button type="button" role="tab" data-amne="ekologi" aria-selected="false">Ekologi</button>
        </div>
        <div id="rs-innehall" class="flode-panelinnehall"><p class="flode-dampad">Hämtar resultatet …</p></div>
        ${extraAnalyserHtml()}
        <div class="flode-rad-slut"><a class="flode-knapp svart stor" href="${flodeAdress(projekt, "rapport")}">Gå vidare till rapport</a></div>
      </aside>
    </div>`;
  const lyssna = (m: HTMLElement, typ: string, f: (h: Event) => void): void => m.addEventListener(typ, f, { signal: ac.signal });
  const el = <T extends HTMLElement>(id: string): T => rot.querySelector(`#${id}`) as T;
  kopplaExtraAnalyser(rot, ac.signal);

  const vyerFor = (k: Kalla): ProjektVy[] => (k === "ritat" ? t.ritVyer : k === "lage" ? t.lageVyer : (projektSvar?.vyer ?? [])).filter((v) => v.avsnitt === amne);

  // ---------- Karta ----------
  const rasterAv = (id: string, lp: LagerPost | null, synlig: boolean): RasterLager[] =>
    lp ? [{ id, opacitet: 0.85, synlig, kalla: { slag: "bild", url: visaTak && lp.png_med_tak ? lp.png_med_tak : lp.png, utbredning: lp.utbredning, skarpa: true } }] : [];

  const byggnader = (lp: LagerPost | null): VyByggnad[] => {
    if (r?.byggnader && lp) {
      const sid = lp.typ === "skillnad" ? (lp.efter ?? lp.scenario) : lp.scenario;
      const p = r.byggnader.scenarier[sid];
      if (p) return p.map((x) => ({ ring: x.ring, hal: x.hal, status: x.status }));
    }
    return (info?.byggnader ?? []).map((x) => ({ ring: x.ring, hal: x.hal, status: "oforandrad" as const }));
  };

  /** Teckenförklaringen (färgskala, "Visa värden även på tak") och, i 3D med fasadlagret tänt, fasadskalan. */
  const ritaTecken = (): void => {
    const fore = lagerPost(r, vy?.fore_lager ?? null);
    const efter = lagerPost(r, vy?.efter_lager ?? null);
    const skill = lagerPost(r, vy?.skillnad_lager ?? null);
    const tl = lage === "fore" || lage === "svep" ? fore : lage === "efter" ? efter : skill;
    const fasad = lagerLista.hamta("3d:fasad");
    const fasadTand = lage === "3d" && Boolean(fasad?.synlig) && !fasad?.ejTillgangligt;
    const tecken = el("rs-tecken");
    tecken.hidden = (!tl || !vy) && !fasadTand;
    let html = "";
    if (tl && vy) {
      const namn = lage === "skillnad" ? `Skillnad i ${tl.etikett.toLowerCase()}` : tl.etikett;
      const takKryss = lage !== "3d" && tl.tak_maskat ? `<label class="flode-kryss"><input type="checkbox" id="rs-tak" ${visaTak ? "checked" : ""}/> Visa värden även på tak</label>` : "";
      html = teckenHtml(tl, namn) + takKryss;
    }
    if (fasadTand) html += fasadForklaringHtml("skillnad", Boolean(skill));
    tecken.innerHTML = html;
  };

  const synkaKarta = (): void => {
    if (avslutad || !a || !info || !bas) return;
    const fore = lagerPost(r, vy?.fore_lager ?? null);
    const efter = lagerPost(r, vy?.efter_lager ?? null);
    const skill = lagerPost(r, vy?.skillnad_lager ?? null);
    const delat = lage === "svep" && Boolean(fore && efter);
    const visad = lage === "fore" ? fore : lage === "efter" ? efter : lage === "skillnad" ? skill : fore;
    const ovan = lage === "3d" ? [] : rasterAv("res:visad", visad, true);
    bas.synka = () => a?.settLager([...baslager(), ...ovan]);
    bas.synka();
    const medVarden = lage !== "3d" && visaTak && Boolean(visad?.png_med_tak);
    const byggStil = bas.byggnadStil();
    // Med värden på tak tonas byggnadsfotavtrycken ned så att värdena syns.
    const bStil = { synlig: byggStil.synlig, opacitet: medVarden ? byggStil.opacitet * 0.25 : byggStil.opacitet };
    const notis = lage !== "3d" && !visaTak && visad?.tak_maskat ? "Ljusgrå yta: tak, inte mark" : null;
    a.settByggnadStil(bStil);
    a.settNotis(notis);
    a.settOverfall(overfall);
    a.settOverfallStil(overfallStil());
    a.settByggnader(byggnader(lage === "svep" ? fore : visad));
    el("rs-b").hidden = !delat;
    el("rs-svep-rad").hidden = !delat;
    if (delat) {
      if (!b) {
        b = new Kartmotor(el("rs-b"), a.hamtaVy());
        b.onVyAndrad = () => b && a?.settVy(b.hamtaVy());
        b.onMarkor = planeraAvlasning;
      }
      b.settLager([...baslager(), ...rasterAv("res:efter", efter, true)]);
      b.settByggnadStil(bStil);
      b.settOverfall(overfall);
      b.settOverfallStil(overfallStil());
      b.onMarkor = planeraAvlasning;
      b.settByggnader(byggnader(efter));
      el("rs-b").style.clipPath = `inset(0 0 0 ${Math.round(svep * 1000) / 10}%)`;
    } else if (b) {
      b.avsluta();
      b = null;
    }
    ritaTecken();
    uppdateraAvlasning(null);
    vy3d?.uppdatera?.();
  };
  const baslager = (): RasterLager[] => basLagerListan;
  let basLagerListan: RasterLager[] = [];

  const kartvy3d = (): { e: number; n: number; mpp: number; hojdPx: number } => {
    const v = (a as Kartmotor).hamtaVy();
    const [e, n] = wgs84TillSweref(v.lng, v.lat);
    return { e, n, mpp: meterPerPxFranZoom(v.zoom, v.lat), hojdPx: el("rs-a").clientHeight || 600 };
  };
  const lagerLista = new LagerLista(projekt, null);
  lagerLista.vidAndring(() => ritaTecken());
  lagerLista.registrera({ id: "geo:byggnader", grupp: "geometri", namn: "Byggnader", synlig: true, opacitet: 1, hjalp: "Byggnaderna: fotavtryck i kartan, volymer i 3D." });
  lagerLista.registrera({ id: "plan:aktuellt", grupp: "planforslag", namn: "Aktuellt förslag", synlig: true, opacitet: 1, hjalp: "Det du ritar just nu." });

  const stallLage = (nytt: Lage): void => {
    const till3d = nytt === "3d";
    lage = nytt;
    for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-lage]"))) k.setAttribute("aria-pressed", String(k.dataset.lage === lage));
    if (till3d && !vy3d && a && info && vy) {
      for (const id of ["fore", "efter", "skillnad"]) lagerLista.sattSynlig(`res:${id}`, id === "skillnad");
      lagerLista.sattSynlig(VY3D_ID, true);
      el("rs-3d").hidden = false;
      el("rs-a").style.visibility = "hidden";
      vy3d = skapa3dVy({
        projekt,
        lager: lagerLista,
        info,
        dok: new PlanDok(),
        vy: () => vy,
        resultat: () => r,
        lage: () => "skillnad",
        svep: () => svep,
        arRitatResultat: () => false,
        kartvy: kartvy3d,
        settKartvy: (e, n, mpp) => {
          const [lng, lat] = swerefTillWgs84(e, n);
          a?.settVy({ lng, lat, zoom: zoomFranMeterPerPx(mpp, lat) });
        },
        avlasning: () => undefined,
        text: () => undefined,
        fel: () => undefined,
      }).montera(el("rs-3d"));
    } else if (!till3d && vy3d) {
      vy3d.avmontera();
      vy3d = null;
      el("rs-3d").hidden = true;
      el("rs-a").style.visibility = "";
      window.requestAnimationFrame(() => a?.matOm());
    }
    synkaKarta();
  };

  // ---------- Panel ----------
  const underlag = (): Underlag => {
    const fore = lagerPost(r, vy?.fore_lager ?? null);
    const efter = lagerPost(r, vy?.efter_lager ?? null);
    const tab = r?.tabeller.find((x) => x.analys === (amne === "vatten" ? "skyfall" : "sol"));
    const rad = (sc: string | undefined): Record<string, unknown> | undefined => tab?.rader.find((x) => x.scenario === sc)?.varden;
    const num = (o: Record<string, unknown> | undefined, k: string): number | null => (typeof o?.[k] === "number" ? (o[k] as number) : null);
    const rf = rad(fore?.scenario);
    const re = rad(efter?.scenario);
    return {
      troskel: mark?.troskel ?? null,
      siffror: mark?.siffror ?? null,
      ekologi,
      foreDjup_m: num(rf, "max_djup_m"),
      efterDjup_m: num(re, "max_djup_m"),
      foreLagrat_m3: num(rf, "lagrat_m3"),
      efterLagrat_m3: num(re, "lagrat_m3"),
    };
  };

  const overfallStil = (): { synlig: boolean; opacitet: number } => {
    const l = kartLager.hamta(OVERFALL_LAGER_ID);
    return { synlig: amne === "vatten" && osakPa.has("vatten") && Boolean(l?.synlig), opacitet: l?.opacitet ?? 1 };
  };

  const osakerhetVisning = (): OsakerhetVisning => ({
    pa: osakPa.has(amne),
    laddar: osakLaddar,
    tal: osakerhetTal(amne, osak),
    rader: amne === "vatten" ? overfallRader(osak.bifurkation) : [],
    fel: osak.fel,
    overfallLager: amne === "vatten" && osak.bifurkation ? { synlig: Boolean(kartLager.hamta(OVERFALL_LAGER_ID)?.synlig), antal: overfall.length } : null,
  });

  /** Hämtar intervallen och överfallen för ämnet. Allt räknas på servern; här sätts bara svaren in. */
  const laddaOsakerhet = async (ny: Amne): Promise<void> => {
    if (ny === "ekologi") return;
    osakPa.add(ny);
    osak.fel = [];
    const behovs = ny === "vatten" ? !osak.vatten || !osak.bifurkation : !osak.svf;
    if (!behovs) {
      ritaPanel();
      synkaKarta();
      return;
    }
    osakLaddar = true;
    ritaPanel();
    const meddelande = (e: unknown): string => (e instanceof Error ? e.message : String(e));
    if (ny === "vatten") {
      const [v, b] = await Promise.allSettled([hamtaVattenIntervall(projekt), hamtaBifurkationer(projekt)]);
      if (v.status === "fulfilled") osak.vatten = v.value;
      else osak.fel.push(`Skyfallsintervallet gick inte att hämta: ${meddelande(v.reason)}`);
      if (b.status === "fulfilled") {
        osak.bifurkation = b.value;
        overfall = ritadeOverfall(b.value.punkter);
        kartLager.registrera(overfallLager(overfall.length));
      } else osak.fel.push(`Överfallen gick inte att hämta: ${meddelande(b.reason)}`);
    } else {
      try {
        osak.svf = await hamtaSvfIntervall(projekt);
      } catch (e) {
        osak.fel.push(`Soluppskattningen gick inte att hämta: ${meddelande(e)}`);
      }
    }
    osakLaddar = false;
    if (avslutad) return;
    ritaPanel();
    synkaKarta();
  };

  // ---------- Värden under markören ----------
  // Markhöjden under pekaren (ur projektets markmodell) visas i samma kort som resultatvärdena.
  let hojdText: string | null = null;
  let senasteAvl: AvlasningSvar | null = null;
  const markhojd = markhojdAvlasning(
    (e, n) => hamtaMarkhojd(projekt, e, n),
    (text) => {
      hojdText = text;
      ritaAvlasning();
    },
  );
  const uppdateraAvlasning = (svar: AvlasningSvar | null): void => {
    senasteAvl = svar;
    ritaAvlasning();
  };
  const ritaAvlasning = (): void => {
    const ruta = el("rs-avl");
    const svar = senasteAvl;
    const delar: string[] = [];
    if (svar && vy && lage !== "3d" && !svar.varden.every((x) => !x.inom)) {
      const rader: Array<[string, string | null]> = vy.lage_idag
        ? [["Läget idag", vy.fore_lager]]
        : [
            ["Före", vy.fore_lager],
            [`Efter: ${vy.efter_namn.toLowerCase()}`, vy.efter_lager],
            ["Skillnad", vy.skillnad_lager],
          ];
      for (const [namn, id] of rader) {
        const x = svar.varden.find((v) => v.lager_id === id && v.inom);
        if (!x) continue;
        let text: string;
        if (x.tak && !visaTak) text = "tak, inte mark";
        else if (x.varde === null) text = "inget värde";
        else text = `${x.typ === "skillnad" && x.varde > 0 ? "+" : ""}${formatera(x.varde, 2)}${x.enhet ? ` ${x.enhet}` : ""}${x.tak ? " (tak)" : ""}`;
        delar.push(`<span><span class="flode-dampad">${esc(namn)}</span> <strong>${esc(text)}</strong></span>`);
      }
    }
    if (hojdText && lage !== "3d") delar.push(`<span class="flode-dampad" id="rs-hojd">${esc(hojdText)}</span>`);
    ruta.hidden = delar.length === 0;
    ruta.innerHTML = delar.join("");
  };

  const planeraAvlasning = (e: number | null, n: number | null): void => {
    markhojd.ror(e, n);
    if (avlTimer !== null) window.clearTimeout(avlTimer);
    avlTimer = null;
    const v = vy;
    if (e === null || n === null || !v || !r || lage === "3d") {
      avlSeq += 1;
      uppdateraAvlasning(null);
      return;
    }
    avlTimer = window.setTimeout(() => {
      const nr = ++avlSeq;
      hamtaAvlasning(v.korning_id, v.skillnad_lager, e, n).then(
        (svar) => {
          if (nr === avlSeq && !avslutad) uppdateraAvlasning(svar);
        },
        () => {
          if (nr === avlSeq && !avslutad) uppdateraAvlasning(null);
        },
      );
    }, 90);
  };

  const ritaPanel = (): void => {
    if (avslutad) return;
    const inne = el("rs-innehall");
    const harRitat = t.ritVyer.length > 0;
    const harLage = t.lageVyer.length > 0;
    const vyer = vyerFor(kalla);
    // Läget idag har bara Före: övriga lägen i växeln döljs (U5a).
    for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-lage]"))) k.hidden = kalla === "lage" && k.dataset.lage !== "fore";
    const pagar = arPagaende(t);
    for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-amne]"))) {
      k.setAttribute("aria-selected", String(k.dataset.amne === amne));
      k.tabIndex = k.dataset.amne === amne ? 0 : -1;
    }
    if (!vy || !r) {
      inne.innerHTML = pagar
        ? `<h1 class="flode-h" id="rs-rubrik">Beräknar …</h1><p class="flode-dampad" role="status">${esc(t.korning?.text ?? "")}</p>`
        : `<h1 class="flode-h" id="rs-rubrik">Inget resultat än</h1><p class="flode-dampad">${AMNESNAMN[amne]} är inte beräknat för ${kalla === "ritat" ? "ditt förslag" : kalla === "lage" ? "läget idag" : "projektet"}. Välj frågor och kör beräkningarna i <a href="${flodeAdress(projekt, "fragor")}">steg 3</a>.</p>`;
      return;
    }
    const u = underlag();
    const tal = nyckeltal(amne, u);
    const jfr = jamforelse(amne, u);
    const regn = amne === "vatten" ? regnText(r.antaganden.regn[0]) : null;
    const vattenAnt = amne === "vatten" ? [...new Set((r.antaganden.vatten ?? []).flatMap((x) => [x.takvatten_text, x.avtappning_text]).filter((x): x is string => Boolean(x)))] : [];
    const ingress = amne === "dagsljus" || amne === "ekologi" ? projektSvar?.avsnitt?.find((x) => x.id === amne)?.ingress : null;
    const natverkVal = amne === "ekologi" && natverkLista.length > 0
      ? `<label class="flode-liten">Habitatnätverk <select id="rs-natverk" aria-label="Habitatnätverk">${natverkLista.map((n) => `<option value="${esc(n.natverk_id)}"${n.natverk_id === (valtNatverk ?? ekologi?.artprofil_id) ? " selected" : ""}>${esc(n.visningsnamn)}</option>`).join("")}</select></label>`
      : "";
    const ekoKalla = amne === "ekologi" ? [resultatEtikett(ekologi), kallRad(ekologi), ...antagandeRader(ekologi), intervallForklaring(ekologi)].filter(Boolean) : [];
    const ekoVarning = amne === "ekologi" ? [...(ekologi?.varningar ?? []), ...(ekologiFel ? [`Ekologisvaret gick inte att hämta: ${ekologiFel}`] : [])] : [];
    const raderRegn = [regn ? `Regn: ${regn}.` : "", ...vattenAnt.map((x) => `${x}.`), ingress ?? "", ...ekoVarning, `Alla tal kommer ur körningen ${vy.korning_id}.`].filter(Boolean).join(" ");
    const kallKnappar = [
      harRitat ? `<button type="button" data-kalla="ritat" aria-pressed="${kalla === "ritat"}">Ditt förslag</button>` : "",
      harLage ? `<button type="button" data-kalla="lage" aria-pressed="${kalla === "lage"}">Läget idag</button>` : "",
      (projektSvar?.vyer?.length ?? 0) > 0 ? `<button type="button" data-kalla="projekt" aria-pressed="${kalla === "projekt"}">Projektets exempel</button>` : "",
    ].filter(Boolean);
    const kallVal = kallKnappar.length > 1 ? `<div class="flode-segment liten" role="group" aria-label="Vilket resultat">${kallKnappar.join("")}</div>` : "";
    if (kalla === "lage") {
      inne.innerHTML = `${kallVal}${vyer.length > 1 ? `<div class="flode-chips liten" role="group" aria-label="Analys">${vyer.map((v) => `<button type="button" data-vy="${esc(v.id)}" aria-pressed="${v.id === vy?.id}">${esc(v.mattnamn)}</button>`).join("")}</div>` : ""}
        <h1 class="flode-h" id="rs-rubrik">Läget idag: ${esc(AMNESNAMN[amne].toLowerCase())}</h1>
        <p class="flode-dampad" id="rs-lagenot">Projektets befintliga läge, beräknat utan ritat förslag. Det finns ingen jämförelse före och efter, så inga skillnader visas. Håll markören över kartan för värdet i en cell. Rita ett förslag i <a href="${flodeAdress(projekt, "forslag")}">steg 2</a> och kör det i <a href="${flodeAdress(projekt, "fragor")}">steg 3</a> för att se vad det ändrar.</p>
        <p class="flode-liten flode-dampad">Alla tal kommer ur körningen ${esc(vy.korning_id)}. Rapporten bygger på en jämförelse före och efter och kan inte skapas på enbart läget idag.</p>`;
      return;
    }
    const kallNot = !harRitat
      ? `<p class="flode-liten flode-dampad" id="rs-kallnot">Du ser projektets färdiga beräkningar, inte ett eget förslag. Rita ett förslag i <a href="${flodeAdress(projekt, "forslag")}">steg 2</a> och kör det i <a href="${flodeAdress(projekt, "fragor")}">steg 3</a> för att se ditt eget.</p>`
      : "";
    const vyVal = vyer.length > 1
      ? `<div class="flode-chips liten" role="group" aria-label="Förändring">${vyer.map((v) => `<button type="button" data-vy="${esc(v.id)}" aria-pressed="${v.id === vy?.id}">${esc(v.efter_namn)}</button>`).join("")}</div>`
      : "";
    inne.innerHTML = `
      ${kallVal}${kallNot}${vyVal}${natverkVal}
      ${kroppHtml({
        slutsats: slutsats(amne, u),
        tal,
        jamforelse: jfr,
        kalla: ekoKalla,
        anmarkning: raderRegn,
        osakerhet: amne === "ekologi" ? null : osakerhetVisning(),
        las: lasKartan(vy, lage, info?.paahittat !== false),
      })}`;
  };

  // ---------- Laddning ----------
  const valjVy = async (ny: ProjektVy | null): Promise<void> => {
    const nr = ++seq;
    vy = ny;
    r = undefined;
    mark = null;
    ekologi = null;
    ekologiFel = null;
    if (ny) {
      try {
        const res = cache.get(ny.korning_id) ?? (await hamtaResultat(ny.korning_id));
        cache.set(ny.korning_id, res);
        const m = amne === "ekologi" || kalla === "lage" ? null : await lasJson<MarkSvar>(`/api/korningar/${encodeURIComponent(ny.korning_id)}/marksiffror?lager=${encodeURIComponent(ny.skillnad_lager)}`).catch(() => null);
        let eko: EkologiSvar | null = null;
        let ekoFel: string | null = null;
        if (amne === "ekologi" && kalla !== "lage") {
          // Talen räknas på servern (K45); webben formaterar bara. Det ritade förslaget går som scenario.
          const begaran: Record<string, unknown> = { omrade_id: projekt };
          if (valtNatverk) begaran.natverk = valtNatverk;
          if (natverkLista.length === 0) {
            natverkLista = await lasJson<{ natverk: typeof natverkLista }>("/api/ekologi/natverk")
              .then((s) => s.natverk.filter((n) => n.korbar && !n.lokalt))
              .catch(() => []);
          }
          if (kalla === "ritat" && t.scenario) begaran.scenario = t.scenario.slug;
          try {
            eko = await lasJson<EkologiSvar>("/api/analys/ekologi", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(begaran),
            });
          } catch (e) {
            ekoFel = e instanceof Error ? e.message : String(e);
          }
        }
        if (nr !== seq || avslutad) return;
        r = res;
        mark = m;
        ekologi = eko;
        ekologiFel = ekoFel;
      } catch {
        if (nr !== seq) return;
        r = undefined;
      }
    }
    ritaPanel();
    if (vy3d) {
      stallLage("skillnad");
    } else synkaKarta();
    if (lage === "3d" && !vy3d) stallLage("3d");
    if (a && info && r) {
      const lp = lagerPost(r, ny?.fore_lager ?? null) ?? lagerPost(r, ny?.skillnad_lager ?? null);
      a.anpassa(lp?.utbredning ?? info.utbredning);
    }
  };

  const valjAmne = async (ny: Amne): Promise<void> => {
    amne = ny;
    if (vyerFor(kalla).length === 0) kalla = kalla === "projekt" ? (t.ritVyer.some((v) => v.avsnitt === ny) ? "ritat" : t.lageVyer.some((v) => v.avsnitt === ny) ? "lage" : "projekt") : t.ritVyer.some((v) => v.avsnitt === ny) && kalla !== "ritat" ? "ritat" : t.lageVyer.some((v) => v.avsnitt === ny) && kalla !== "lage" ? "lage" : "projekt";
    const onskad = onskadVy ? vyerFor(kalla).find((v) => v.id === onskadVy) : undefined;
    onskadVy = null;
    await valjVy(onskad ?? vyerFor(kalla)[0] ?? null);
  };

  lyssna(rot, "click", (h) => {
    const m = (h.target as Element).closest<HTMLElement>("[data-lage],[data-amne],[data-kalla],[data-vy],#rs-osakerhet");
    if (!m) return;
    if (m.id === "rs-osakerhet") {
      if (osakPa.has(amne)) {
        osakPa.delete(amne);
        ritaPanel();
        synkaKarta();
      } else void laddaOsakerhet(amne);
    } else if (m.dataset.lage) stallLage(m.dataset.lage as Lage);
    else if (m.dataset.amne) void valjAmne(m.dataset.amne as Amne);
    else if (m.dataset.kalla) {
      kalla = m.dataset.kalla as Kalla;
      if (kalla === "lage" && lage !== "fore") stallLage("fore");
      void valjVy(vyerFor(kalla)[0] ?? null);
    } else if (m.dataset.vy) void valjVy(vyerFor(kalla).find((v) => v.id === m.dataset.vy) ?? null);
  });
  lyssna(rot, "change", (h) => {
    const s = h.target as HTMLSelectElement;
    if (s.id === "rs-tak") {
      visaTak = (s as unknown as HTMLInputElement).checked;
      synkaKarta();
      return;
    }
    if (s.id === "rs-overfall") {
      kartLager.sattSynlig(OVERFALL_LAGER_ID, (s as unknown as HTMLInputElement).checked);
      synkaKarta();
      return;
    }
    if (s.id !== "rs-natverk") return;
    valtNatverk = s.value;
    void valjVy(vy);
  });
  rot.querySelector("#rs-flikar")?.addEventListener("keydown", (h) => {
    const k = (h as KeyboardEvent).key;
    if (k === "ArrowRight" || k === "ArrowLeft") {
      const n = AMNEN_ORDNING.length;
      const nasta = AMNEN_ORDNING[(AMNEN_ORDNING.indexOf(amne) + (k === "ArrowRight" ? 1 : n - 1)) % n] ?? amne;
      void valjAmne(nasta).then(() => rot.querySelector<HTMLElement>(`[data-amne="${amne}"]`)?.focus());
    }
  });
  el<HTMLInputElement>("rs-svep").addEventListener("input", (h) => {
    svep = Number((h.target as HTMLInputElement).value) / 100;
    el("rs-b").style.clipPath = `inset(0 0 0 ${Math.round(svep * 1000) / 10}%)`;
    vy3d?.uppdatera?.();
  });
  const avlyssna = vidKorning(() => {
    if (avslutad) return;
    if (t.korning?.klar && !t.korning.fel && t.korning.lage && t.lageVyer.length > 0) {
      kalla = "lage";
      if (lage !== "fore") stallLage("fore");
      void valjAmne(amne);
    } else if (t.korning?.klar && !t.korning.fel && !t.korning.lage && t.ritVyer.length > 0) {
      kalla = "ritat";
      void valjAmne(amne);
    } else ritaPanel();
  });

  const klar = (async () => {
    try {
      // Projektets färdiga exempel (`/api/projekt/<id>`) finns bara för demokvarteret: ett projekt i en geografi svarar 404
      // där och har bara sina egna körningar. Det är inget fel; då visas inga "Projektets exempel".
      [projektSvar, info] = await Promise.all([
        hamtaProjekt(projekt).catch((e: unknown) => {
          if (e instanceof ApiFel && e.status === 404) return null;
          throw e;
        }),
        hamtaProjektInfo(projekt),
      ]);
    } catch (e) {
      el("rs-innehall").innerHTML = `<h1 class="flode-h" id="rs-rubrik">Resultatet</h1>${felBeskedHtml(sidFel(e, "Resultatet", kontoLage(), `/#/p/${encodeURIComponent(projekt)}/resultat`), esc)}`;
      kopplaLaddaOm(el("rs-innehall"));
      return;
    }
    if (avslutad) return;
    a = skapaKarta(el("rs-a"), info);
    a.onVyAndrad = () => b && a && b.settVy(a.hamtaVy());
    a.onMarkor = planeraAvlasning;
    bas = new BasLager(a, info, kartLager);
    bas.registrera(projekt);
    // OpenStreetMap-bakgrunden (samma val som i steg 2, sparat per projekt).
    monteraOsmKryss(el("rs-osm"), kartLager, () => {
      if (!bas) return;
      basLagerListan = bas.lagerListan();
      synkaKarta();
    }, ac.signal);
    // BasLager äger bakgrundsbilderna; resultatlagret läggs ovanpå i synkaKarta.
    const orig = bas;
    void orig.ladda(projekt).then(() => {
      basLagerListan = orig.lagerListan();
      synkaKarta();
    });
    // Länkar från de äldre sidorna kan ange en vy: #/p/<projekt>/resultat?vy=<vy>.
    const fraga = window.location.hash.split("?")[1] ?? "";
    const vyUtanLank = new URLSearchParams(fraga).get("vy");
    const vyAvLank = vyUtanLank ? projektSvar?.vyer.find((v) => v.id === vyUtanLank) : undefined;
    kalla = vyAvLank ? "projekt" : t.korning?.lage && t.lageVyer.length > 0 ? "lage" : t.ritVyer.length > 0 ? "ritat" : t.lageVyer.length > 0 ? "lage" : "projekt";
    if (kalla === "lage") lage = "fore";
    if (vyAvLank) {
      amne = vyAvLank.avsnitt as Amne;
      onskadVy = vyAvLank.id;
    }
    if (!vyAvLank && vyerFor(kalla).length === 0) {
      const forsta = kalla === "ritat" ? t.ritVyer[0]?.avsnitt : kalla === "lage" ? t.lageVyer[0]?.avsnitt : undefined;
      amne = forsta === "dagsljus" || forsta === "ekologi" ? forsta : "vatten";
    }
    await valjAmne(amne);
    window.requestAnimationFrame(() => a?.matOm());
  })();

  return {
    klar,
    avsluta: () => {
      avslutad = true;
      markhojd.avsluta();
      ac.abort();
      avlyssna();
      vy3d?.avmontera();
      if (avlTimer !== null) window.clearTimeout(avlTimer);
      b?.avsluta();
      a?.avsluta();
      bas?.avsluta();
    },
  };
}
