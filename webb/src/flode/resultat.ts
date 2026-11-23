/*
 * Steg 4 · Resultat: kartan tar största ytan med en flytande växel (Före, Efter, Skillnad, Svep, 3D)
 * och panelen har flikarna Skyfall, Sol och Ekologi (skiss): en mening med slutsatsen, fyra nyckeltal, en liten
 * stapeljämförelse och en rad om regn och antaganden. Talen kommer ur körningen: mätvärdestabellen
 * och markytorna (`/api/korningar/<id>/marksiffror`, samma funktion som projektsidans tal). Det
 * som saknas visas som "–". Svepet och 3D-vyn är de befintliga.
 */
import { hamtaProjekt, hamtaResultat, lasJson } from "../api.ts";
import type { LagerPost, ResultatSvar } from "../fragor-typer.ts";
import { hamtaProjektInfo, type ProjektInfo } from "../karta/kartapi.ts";
import { Kartmotor, type RasterLager, type VyByggnad } from "../karta/kartmotor.ts";
import { LagerLista } from "../karta/lager.ts";
import { PlanDok } from "../karta/plandok.ts";
import { skapa3dVy, VY3D_ID } from "../kartsida3d.ts";
import { esc } from "../sidor.ts";
import { swerefTillWgs84, wgs84TillSweref } from "../sweref.ts";
import type { ProjektSvar, ProjektVy } from "../types.ts";
import { meterPerPxFranZoom, zoomFranMeterPerPx } from "../vy3d/koord.ts";
import { flodeAdress } from "./adress.ts";
import { BasLager, skapaKarta } from "./kartdel.ts";
import { arPagaende, vidKorning } from "./korning.ts";
import { jamforelse, nyckeltal, regnText, slutsats, type Amne, type EkologiSvar, type Marksiffror, type Underlag } from "./nyckeltal.ts";
import type { Avslutbar } from "./projektlista.ts";
import { tillstandFor } from "./tillstand.ts";

type Lage = "fore" | "efter" | "skillnad" | "svep" | "3d";
const LAGEN: Array<[Lage, string]> = [
  ["fore", "Före"],
  ["efter", "Efter"],
  ["skillnad", "Skillnad"],
  ["svep", "Svep"],
  ["3d", "3D"],
];

interface MarkSvar {
  lager_id: string;
  troskel: number;
  siffror: Marksiffror | null;
}

const AMNEN_ORDNING: Amne[] = ["vatten", "dagsljus", "ekologi"];
const AMNESNAMN: Record<Amne, string> = { vatten: "Skyfall", dagsljus: "Sol", ekologi: "Ekologi" };

const lagerPost = (r: ResultatSvar | undefined, id: string | null): LagerPost | null => (id && r ? (r.lager.find((l) => l.id === id) ?? null) : null);

/** Teckenförklaring som i skissen: rubrik, band och ändpunkter. */
export function teckenHtml(l: LagerPost, rubrik: string): string {
  const tf = l.teckenforklaring;
  const f = (v: number): string => new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(v);
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
  let kalla: "ritat" | "projekt" = "projekt";
  let vy: ProjektVy | null = null;
  let r: ResultatSvar | undefined;
  let mark: MarkSvar | null = null;
  let ekologi: EkologiSvar | null = null;
  let ekologiFel: string | null = null;
  let lage: Lage = "skillnad";
  let svep = 0.5;
  let a: Kartmotor | null = null;
  let b: Kartmotor | null = null;
  let bas: BasLager | null = null;
  let vy3d: { avmontera(): void; uppdatera?(): void } | null = null;
  let seq = 0;
  const cache = new Map<string, ResultatSvar>();

  rot.innerHTML = `
    <div class="flode-sida">
      <section class="flode-karta" aria-label="Karta">
        <div class="flode-karta-yta" id="rs-a"></div>
        <div class="flode-karta-yta flode-b" id="rs-b" hidden></div>
        <div class="flode-karta-yta" id="rs-3d" hidden></div>
        <div class="flode-vaxel" role="group" aria-label="Visning" id="rs-vaxel">${LAGEN.map(([id, n]) => `<button type="button" data-lage="${id}" aria-pressed="${id === lage}">${n}</button>`).join("")}</div>
        <div class="flode-svep" id="rs-svep-rad" hidden>
          <span>Före</span><input id="rs-svep" type="range" min="0" max="100" value="50" aria-label="Svepgräns: till vänster före, till höger efter" /><span>Efter</span>
        </div>
        <div class="flode-kartkort flode-forklaring" id="rs-tecken" hidden></div>
      </section>
      <aside class="flode-panel" aria-label="Resultat">
        <div role="tablist" aria-label="Ämne" class="flode-flikar" id="rs-flikar">
          <button type="button" role="tab" data-amne="vatten" aria-selected="true">Skyfall</button>
          <button type="button" role="tab" data-amne="dagsljus" aria-selected="false">Sol</button>
          <button type="button" role="tab" data-amne="ekologi" aria-selected="false">Ekologi</button>
        </div>
        <div id="rs-innehall" class="flode-panelinnehall"><p class="flode-dampad">Hämtar resultatet …</p></div>
        <div class="flode-rad-slut"><a class="flode-knapp svart stor" href="${flodeAdress(projekt, "rapport")}">Skapa rapport</a></div>
      </aside>
    </div>`;
  const lyssna = (m: HTMLElement, typ: string, f: (h: Event) => void): void => m.addEventListener(typ, f, { signal: ac.signal });
  const el = <T extends HTMLElement>(id: string): T => rot.querySelector(`#${id}`) as T;

  const vyerFor = (k: "ritat" | "projekt"): ProjektVy[] => (k === "ritat" ? t.ritVyer : (projektSvar?.vyer ?? [])).filter((v) => v.avsnitt === amne);

  // ---------- Karta ----------
  const rasterAv = (id: string, lp: LagerPost | null, synlig: boolean): RasterLager[] =>
    lp ? [{ id, opacitet: 0.85, synlig, kalla: { slag: "bild", url: lp.png, utbredning: lp.utbredning, skarpa: true } }] : [];

  const byggnader = (lp: LagerPost | null): VyByggnad[] => {
    if (r?.byggnader && lp) {
      const sid = lp.typ === "skillnad" ? (lp.efter ?? lp.scenario) : lp.scenario;
      const p = r.byggnader.scenarier[sid];
      if (p) return p.map((x) => ({ ring: x.ring, hal: x.hal, status: x.status }));
    }
    return (info?.byggnader ?? []).map((x) => ({ ring: x.ring, hal: x.hal, status: "oforandrad" as const }));
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
    a.settByggnadStil({ synlig: true, opacitet: 1 });
    a.settByggnader(byggnader(lage === "svep" ? fore : visad));
    el("rs-b").hidden = !delat;
    el("rs-svep-rad").hidden = !delat;
    if (delat) {
      if (!b) {
        b = new Kartmotor(el("rs-b"), a.hamtaVy());
        b.onVyAndrad = () => b && a?.settVy(b.hamtaVy());
      }
      b.settLager([...baslager(), ...rasterAv("res:efter", efter, true)]);
      b.settByggnadStil({ synlig: true, opacitet: 1 });
      b.settByggnader(byggnader(efter));
      el("rs-b").style.clipPath = `inset(0 0 0 ${Math.round(svep * 1000) / 10}%)`;
    } else if (b) {
      b.avsluta();
      b = null;
    }
    const tl = lage === "fore" || lage === "svep" ? fore : lage === "efter" ? efter : skill;
    const tecken = el("rs-tecken");
    tecken.hidden = !tl || !vy;
    if (tl && vy) {
      const namn = lage === "skillnad" ? `Skillnad i ${tl.etikett.toLowerCase()}` : tl.etikett;
      tecken.innerHTML = teckenHtml(tl, namn);
    }
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

  const ritaPanel = (): void => {
    if (avslutad) return;
    const inne = el("rs-innehall");
    const harRitat = t.ritVyer.length > 0;
    const vyer = vyerFor(kalla);
    const pagar = arPagaende(t);
    for (const k of Array.from(rot.querySelectorAll<HTMLButtonElement>("[data-amne]"))) {
      k.setAttribute("aria-selected", String(k.dataset.amne === amne));
      k.tabIndex = k.dataset.amne === amne ? 0 : -1;
    }
    if (!vy || !r) {
      inne.innerHTML = pagar
        ? `<h1 class="flode-h" id="rs-rubrik">Beräknar …</h1><p class="flode-dampad" role="status">${esc(t.korning?.text ?? "")}</p>`
        : `<h1 class="flode-h" id="rs-rubrik">Inget resultat än</h1><p class="flode-dampad">${AMNESNAMN[amne]} är inte beräknat för ${kalla === "ritat" ? "ditt förslag" : "projektet"}. Välj frågor och kör beräkningarna i <a href="${flodeAdress(projekt, "fragor")}">steg 3</a>.</p>`;
      return;
    }
    const u = underlag();
    const tal = nyckeltal(amne, u);
    const jfr = jamforelse(amne, u);
    const regn = amne === "vatten" ? regnText(r.antaganden.regn[0]) : null;
    const vattenAnt = amne === "vatten" ? [...new Set((r.antaganden.vatten ?? []).flatMap((x) => [x.takvatten_text, x.avtappning_text]).filter((x): x is string => Boolean(x)))] : [];
    const ingress = amne === "dagsljus" || amne === "ekologi" ? projektSvar?.avsnitt.find((x) => x.id === amne)?.ingress : null;
    const ekoVarning = amne === "ekologi" ? [...(ekologi?.varningar ?? []), ...(ekologiFel ? [`Ekologisvaret gick inte att hämta: ${ekologiFel}`] : [])] : [];
    const raderRegn = [regn ? `Regn: ${regn}.` : "", ...vattenAnt.map((x) => `${x}.`), ingress ?? "", ...ekoVarning, `Alla tal kommer ur körningen ${vy.korning_id}.`].filter(Boolean).join(" ");
    const kallVal = harRitat && (projektSvar?.vyer.length ?? 0) > 0
      ? `<div class="flode-segment liten" role="group" aria-label="Vilket resultat">
           <button type="button" data-kalla="ritat" aria-pressed="${kalla === "ritat"}">Ditt förslag</button>
           <button type="button" data-kalla="projekt" aria-pressed="${kalla === "projekt"}">Projektets exempel</button></div>`
      : "";
    const vyVal = vyer.length > 1
      ? `<div class="flode-chips liten" role="group" aria-label="Förändring">${vyer.map((v) => `<button type="button" data-vy="${esc(v.id)}" aria-pressed="${v.id === vy?.id}">${esc(v.efter_namn)}</button>`).join("")}</div>`
      : "";
    inne.innerHTML = `
      ${kallVal}${vyVal}
      <h1 class="flode-slutsats" id="rs-rubrik">${esc(slutsats(amne, u))}</h1>
      <dl class="flode-tal">${tal.map((x) => `<div><dt>${esc(x.etikett)}</dt><dd>${esc(x.varde)}</dd></div>`).join("")}</dl>
      <div class="flode-staplar"><span class="flode-liten flode-dampad">${esc(jfr.rubrik)}</span>
        ${jfr.staplar.map((s) => `<div class="flode-stapel"><span class="flode-dampad">${esc(s.etikett)}</span><span class="flode-stapel-fyll${s.etikett === "Före" || s.etikett === "Mindre sol" ? " ljus" : ""}" style="width:${s.andel === null ? 0 : Math.max(2, Math.round(s.andel * 100))}%"></span><span>${esc(s.varde)}</span></div>`).join("")}
      </div>
      <p class="flode-liten flode-dampad">${esc(raderRegn)}</p>`;
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
        const m = amne === "ekologi" ? null : await lasJson<MarkSvar>(`/api/korningar/${encodeURIComponent(ny.korning_id)}/marksiffror?lager=${encodeURIComponent(ny.skillnad_lager)}`).catch(() => null);
        let eko: EkologiSvar | null = null;
        let ekoFel: string | null = null;
        if (amne === "ekologi") {
          // Talen räknas på servern (K45); webben formaterar bara. Det ritade förslaget går som scenario.
          const begaran: Record<string, unknown> = { omrade_id: projekt };
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
    if (vyerFor(kalla).length === 0) kalla = kalla === "ritat" ? "projekt" : t.ritVyer.some((v) => v.avsnitt === ny) ? "ritat" : "projekt";
    await valjVy(vyerFor(kalla)[0] ?? null);
  };

  lyssna(rot, "click", (h) => {
    const m = (h.target as Element).closest<HTMLElement>("[data-lage],[data-amne],[data-kalla],[data-vy]");
    if (!m) return;
    if (m.dataset.lage) stallLage(m.dataset.lage as Lage);
    else if (m.dataset.amne) void valjAmne(m.dataset.amne as Amne);
    else if (m.dataset.kalla) {
      kalla = m.dataset.kalla as "ritat" | "projekt";
      void valjVy(vyerFor(kalla)[0] ?? null);
    } else if (m.dataset.vy) void valjVy(vyerFor(kalla).find((v) => v.id === m.dataset.vy) ?? null);
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
    if (t.korning?.klar && !t.korning.fel && t.ritVyer.length > 0) {
      kalla = "ritat";
      void valjAmne(amne);
    } else ritaPanel();
  });

  const klar = (async () => {
    try {
      [projektSvar, info] = await Promise.all([hamtaProjekt(projekt), hamtaProjektInfo(projekt)]);
    } catch (e) {
      el("rs-innehall").innerHTML = `<h1 class="flode-h" id="rs-rubrik">Resultatet gick inte att hämta</h1><p class="flode-fel" role="alert">${esc(e instanceof Error ? e.message : String(e))}</p>`;
      return;
    }
    if (avslutad) return;
    a = skapaKarta(el("rs-a"), info);
    a.onVyAndrad = () => b && a && b.settVy(a.hamtaVy());
    bas = new BasLager(a, info);
    // BasLager äger bakgrundsbilderna; resultatlagret läggs ovanpå i synkaKarta.
    const orig = bas;
    void orig.ladda(projekt).then(() => {
      basLagerListan = orig.lagerListan();
      synkaKarta();
    });
    kalla = t.ritVyer.length > 0 ? "ritat" : "projekt";
    if (vyerFor(kalla).length === 0) {
      const forsta = kalla === "ritat" ? t.ritVyer[0]?.avsnitt : undefined;
      amne = forsta === "dagsljus" || forsta === "ekologi" ? forsta : "vatten";
    }
    await valjAmne(amne);
    window.requestAnimationFrame(() => a?.matOm());
  })();

  return {
    klar,
    avsluta: () => {
      avslutad = true;
      ac.abort();
      avlyssna();
      vy3d?.avmontera();
      b?.avsluta();
      a?.avsluta();
      bas?.avsluta();
    },
  };
}
