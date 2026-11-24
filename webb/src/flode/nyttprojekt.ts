/*
 * Steg 1 · Nytt projekt (`#/nytt`): välj geografi, rita avgränsningen på kartan (rektangel med två klick eller
 * polygon med flera klick och dubbelklick), ange namn och om projektet delas med organisationen, och skapa.
 * Geografins giltiga kärna (grönt) och yttre gräns (streckat) ritas så att man ser var data finns. Servern
 * kontrollerar allt (`POST /api/projekt`, K48). En polygon är projektets riktiga avgränsning: den ritas som den är,
 * ytan räknas på den, och en polygon som korsar sig själv nekas med ett svenskt besked redan här. Det som räknas
 * till projektet (byggnader, celler) är det som ligger inne i polygonen; beräkningsrutnätet och bufferten är
 * rektangeln runt den, och det sägs. Efter skapande visas varningarna och "Öppna projektet" leder till steg 2.
 *
 * Bakgrund: OpenStreetMap-plattor (`karta/osm.ts`, avstängbart, valet sparas) under kärnan och avgränsningen, så att man
 * ser var på jorden geografin ligger. Geografins och projektets varningar visas som en rad ("32 varningar vid mappningen")
 * med en hopfälld lista per slag (`varningar.ts`), inte som en lång text i vänsterspalten. En rad förklarar varför rutorna
 * ser vridna ut långt från SWEREF 99 18 00:s mittmeridian (Göteborg).
 */
import { ApiFel, hamtaGeografier, skapaProjekt } from "../api.ts";
import { kontoLage, laddaKonto } from "../konto.ts";
import { Kartmotor, type PekarLyssnare, type RasterLager } from "../karta/kartmotor.ts";
import { lasOsmVal, OSM_ID, OSM_KALLA, sparaOsmVal, webbLagringOsm } from "../karta/osm.ts";
import { esc } from "../sidor.ts";
import { swerefTillWgs84 } from "../sweref.ts";
import type { GeografiPost, GeografierSvar, Rektangel3011, SkapaProjektSvar } from "../types.ts";
import { flodeAdress } from "./adress.ts";
import { felBeskedHtml, ingenGeografi, kopplaLaddaOm, nyttProjektFel, type FelBesked } from "./felhandling.ts";
import {
  arealText,
  formularFel,
  omslutande,
  placering,
  placeringText,
  polygonFel,
  punkterPolygon,
  rektangelPolygon,
  skapaBegaran,
  vridningText,
  type Pkt,
} from "./nyttprojekt-logik.ts";
import { varningarHtml } from "./varningar.ts";
import type { Avslutbar } from "./projektlista.ts";

type Slag = "rektangel" | "polygon";
type Fc = GeoJSON.FeatureCollection;

const ll = (p: Pkt | [number, number]): [number, number] => (Array.isArray(p) ? swerefTillWgs84(p[0], p[1]) : swerefTillWgs84(p.x, p.y));
const ring = (r: Rektangel3011): Array<[number, number]> =>
  [[r[0], r[1]], [r[2], r[1]], [r[2], r[3]], [r[0], r[3]], [r[0], r[1]]].map((p) => ll(p as [number, number]));
const TOM: Fc = { type: "FeatureCollection", features: [] };

export function visaNyttProjekt(rot: HTMLElement): Avslutbar {
  let avslutad = false;
  let motor: Kartmotor | null = null;
  let svar: GeografierSvar | null = null;
  let geo: GeografiPost | null = null;
  let slag: Slag = "rektangel";
  let pkt: Pkt[] = []; // klickade punkter (rektangel: 1-2, polygon: fler)
  let mus: Pkt | null = null;
  let klarForm = false; // formen är färdigritad
  let skapar = false;
  let fel: string | null = null;
  let resultat: SkapaProjektSvar | null = null;
  let osmPa = lasOsmVal(webbLagringOsm()) ?? true; // förvalt tänt: utan bakgrund syns inte var geografin ligger
  const ac = new AbortController();

  rot.innerHTML = `
    <div class="flode-sida flode-nytt">
      <aside class="flode-panel" aria-labelledby="nytt-rubrik">
        <h1 id="nytt-rubrik" class="flode-h">Nytt projekt</h1>
        <p class="flode-liten flode-dampad"><a href="#/">Till projektlistan</a></p>
        <div id="np-innehall"><p class="flode-dampad">Hämtar geografierna …</p></div>
      </aside>
      <section class="flode-karta" aria-label="Karta: rita projektets avgränsning">
        <div class="flode-karta-yta" id="np-karta"></div>
        <div class="flode-kartkort" id="np-kort" hidden></div>
        <div class="flode-kartkort flode-osm" id="np-osm"><label class="flode-kryss"><input type="checkbox" id="np-osm-kryss" /> OpenStreetMap-bakgrund</label></div>
      </section>
    </div>`;
  const el = <T extends HTMLElement>(id: string): T => rot.querySelector(`#${id}`) as T;

  // ---- avgränsningen som rektangel/polygon ur klicken
  const form = (): { avgr: Rektangel3011 | null; polygon: GeoJSON.Polygon | null; pkt: Pkt[] } => {
    if (slag === "rektangel") {
      const [a, b] = [pkt[0], pkt[1] ?? (klarForm ? undefined : mus)];
      if (!a || !b) return { avgr: null, polygon: null, pkt: pkt.slice() };
      const r = omslutande([a, b]);
      return { avgr: r, polygon: r ? rektangelPolygon(r) : null, pkt: [a, b] };
    }
    const lista = !klarForm && mus ? [...pkt, mus] : pkt;
    return { avgr: omslutande(lista.length >= 3 ? lista : []), polygon: punkterPolygon(lista), pkt: lista };
  };
  const fast = (): { avgr: Rektangel3011 | null; polygon: GeoJSON.Polygon | null; horn: Pkt[] | null } => {
    if (!klarForm) return { avgr: null, polygon: null, horn: null };
    const f = form();
    return {
      avgr: f.avgr,
      polygon: slag === "rektangel" ? (f.avgr ? rektangelPolygon(f.avgr) : null) : f.polygon,
      horn: slag === "polygon" ? f.pkt : null,
    };
  };
  /** Polygonen bara när den ritats som polygon (en rektangel har ingen polygon utöver rektangeln). */
  const riktigPolygon = (f: ReturnType<typeof fast>): GeoJSON.Polygon | null => (slag === "polygon" ? f.polygon : null);

  // ---- kartlager (egna källor ovanpå Kartmotorn)
  const satt = (id: string, data: Fc): void => {
    const k = motor?.karta;
    if (!k) return;
    const kalla = k.getSource(id) as { setData(d: Fc): void } | undefined;
    if (kalla) kalla.setData(data);
  };
  const byggLager = (): void => {
    const k = motor?.karta;
    if (!k) return;
    const lagg = (id: string, typ: "fill" | "line", paint: Record<string, unknown>): void => {
      if (!k.getSource(id)) k.addSource(id, { type: "geojson", data: TOM });
      if (!k.getLayer(`${id}-${typ}`)) k.addLayer({ id: `${id}-${typ}`, type: typ, source: id, paint } as never);
    };
    lagg("np-gransen", "line", { "line-color": "#6b6e63", "line-width": 1.5, "line-dasharray": [3, 2] });
    lagg("np-karna", "fill", { "fill-color": "#4c8a3f", "fill-opacity": 0.14 });
    lagg("np-karna", "line", { "line-color": "#3b7a30", "line-width": 2 });
    lagg("np-avgr", "fill", { "fill-color": "#1a1a1a", "fill-opacity": 0.1 });
    lagg("np-avgr", "line", { "line-color": "#1a1a1a", "line-width": 2.5 });
    lagg("np-utkast", "line", { "line-color": "#1a1a1a", "line-width": 2, "line-dasharray": [2, 1.5] });
    synkaKarta();
  };
  const synkaOsm = (): void => {
    const l: RasterLager = { id: OSM_ID, kalla: OSM_KALLA, opacitet: 1, synlig: osmPa };
    motor?.settLager([l]);
    const k = rot.querySelector<HTMLInputElement>("#np-osm-kryss");
    if (k) k.checked = osmPa;
  };
  const synkaKarta = (): void => {
    if (!motor) return;
    satt("np-gransen", geo ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring(geo.gransen_3011)] } }] } : TOM);
    satt("np-karna", geo ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring(geo.karna_3011)] } }] } : TOM);
    const f = form();
    const fin = klarForm && f.polygon;
    satt("np-avgr", fin ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [f.polygon?.coordinates[0]?.map((p) => ll(p as [number, number])) ?? []] } }] } : TOM);
    const ut = klarForm ? [] : f.pkt;
    const linje: Fc =
      ut.length >= 2
        ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: (slag === "rektangel" && f.avgr ? ring(f.avgr) : [...ut.map(ll), ll(ut[0] as Pkt)]) } }] }
        : TOM;
    satt("np-utkast", linje);
  };

  // ---- pekare
  const lyssnare: PekarLyssnare = {
    ror: (p) => {
      if (klarForm || pkt.length === 0) return;
      mus = p;
      synkaKarta();
      rit();
    },
    klick: (p) => {
      if (!geo || resultat) return;
      if (klarForm) return;
      mus = null;
      if (slag === "rektangel") {
        pkt.push(p);
        if (pkt.length >= 2) klarForm = true;
      } else {
        pkt.push(p);
      }
      uppdatera();
    },
    dubbelklick: () => {
      if (slag === "polygon" && pkt.length >= 3 && !klarForm) {
        // Dubbelklicket ger också två enkla klick: ta bort den sista dubbletten.
        const sist = pkt[pkt.length - 1];
        const fore = pkt[pkt.length - 2];
        if (sist && fore && Math.hypot(sist.x - fore.x, sist.y - fore.y) < 0.5) pkt.pop();
        if (pkt.length >= 3) {
          klarForm = true;
          uppdatera();
        }
      }
    },
    ned: () => false,
    upp: () => undefined,
    lamnar: () => {
      mus = null;
      synkaKarta();
    },
  };

  const nollstall = (): void => {
    pkt = [];
    mus = null;
    klarForm = false;
    fel = null;
  };

  const uppdatera = (): void => {
    synkaKarta();
    rit();
  };

  // ---- panelen
  const kort = (): void => {
    const k = el("np-kort");
    const f = fast();
    const { avgr } = f;
    if (!geo) {
      k.hidden = true;
      return;
    }
    k.hidden = false;
    k.innerHTML = `<strong>${esc(geo.namn)} (${esc(geo.version)})</strong><span class="flode-dampad">Grönt: giltig kärna. Streckat: yttre gräns.${avgr ? ` Avgränsning ${esc(arealText(avgr, riktigPolygon(f)))}.` : ""}</span>`;
  };

  const visaFel = (b: FelBesked): string => felBeskedHtml(b, esc, "np-felbesked");
  const rit = (): void => {
    const inn = el("np-innehall");
    if (!svar) return;
    const aktiv = inn.contains(document.activeElement) ? (document.activeElement as HTMLElement).id : "";
    const namnVarde = (inn.querySelector<HTMLInputElement>("#np-namn")?.value ?? "");
    const synligVarde = inn.querySelector<HTMLInputElement>("#np-synlig")?.checked ?? false;
    const orgVarde = inn.querySelector<HTMLSelectElement>("#np-org")?.value ?? "";
    if (resultat) {
      const ar = resultat;
      inn.innerHTML = `
        <div class="flode-ruta ok" role="status"><p><strong>Projektet är skapat.</strong> Du är utredare i det.</p></div>
        ${ar.varningar.length > 0 ? `<h2 class="flode-liten-rubrik">Varningar</h2>${varningarHtml(ar.varningar, "när projektet skapades", "np-varningar")}` : ""}
        ${(ar.info ?? []).length > 0 ? `<h2 class="flode-liten-rubrik">Så räknas projektet</h2><ul class="flode-liten" id="np-info">${(ar.info ?? []).map((v) => `<li>${esc(v)}</li>`).join("")}</ul>` : ""}
        <div class="flode-rad-slut"><a class="flode-knapp svart stor" id="np-oppna" href="${flodeAdress(ar.projekt_id, "forslag")}">Öppna projektet (steg 2)</a></div>`;
      inn.querySelector<HTMLElement>("#np-oppna")?.focus();
      return;
    }
    if (!svar.far_skapa_projekt) {
      inn.innerHTML = `${visaFel(nyttProjektFel(new ApiFel("Saknar rätt", 403), kontoLage()))}<ul class="flode-liten flode-dampad" id="np-geolista" aria-label="Mappade geografier">${svar.geografier.map((g) => `<li>${esc(g.namn)} (${esc(g.ref)})</li>`).join("")}</ul>`;
      return;
    }
    const flera = svar.organisationer.length > 1;
    const f = fast();
    const { avgr } = f;
    const poly = riktigPolygon(f);
    const pl = geo && avgr ? placeringText(placering(avgr, geo, poly), geo.gransen_3011) : null;
    const hinder = formularFel({ geografi: geo, namn: namnVarde, avgransning: avgr, polygon: poly, horn: f.horn, organisation: orgVarde || null, flera });
    // Självkorsning syns direkt under ritandet, och polygonens övriga fel när formen är klar.
    const polFel = slag === "polygon" && (klarForm || pkt.length >= 4) ? polygonFel(pkt) : null;
    const polygonRuta = polFel && (klarForm || polFel.startsWith("Polygonen korsar")) ? polFel : null;
    inn.innerHTML = `
      <div class="flode-falt"><label for="np-geo">Geografi</label>
        <select id="np-geo">${svar.geografier.map((g) => `<option value="${esc(g.ref)}"${g.ref === geo?.ref ? " selected" : ""}>${esc(g.namn)} (${esc(g.version)})</option>`).join("")}</select></div>
      <ul class="flode-liten flode-dampad" id="np-geolista" aria-label="Mappade geografier">${svar.geografier.map((g) => `<li>${esc(g.namn)} (${esc(g.ref)})${g.kommun ? `, ${esc(g.kommun)}` : ""}: kärna ${Math.round(g.karna_3011[2] - g.karna_3011[0])} x ${Math.round(g.karna_3011[3] - g.karna_3011[1])} m${g.buffert_m != null ? `, buffert ${Math.round(g.buffert_m)} m` : ""}</li>`).join("")}</ul>
      ${geo && geo.varningar.length > 0 ? `<div class="flode-liten flode-dampad">${varningarHtml(geo.varningar, "vid mappningen", "np-geovarningar")}</div>` : ""}
      ${geo && vridningText(geo.gransen_3011) ? `<p class="flode-liten flode-dampad" id="np-vridning">${esc(vridningText(geo.gransen_3011) ?? "")}</p>` : ""}
      <fieldset class="flode-fieldset"><legend>Rita avgränsningen</legend>
        <div class="flode-segment" id="np-slag">
          <button type="button" data-slag="rektangel" aria-pressed="${slag === "rektangel"}">Rektangel</button>
          <button type="button" data-slag="polygon" aria-pressed="${slag === "polygon"}">Polygon</button>
        </div>
        <p class="flode-liten flode-dampad" id="np-hjalp">${
          slag === "rektangel"
            ? "Klicka på två motstående hörn."
            : "Klicka för varje hörn och dubbelklicka (eller tryck Klar) för att avsluta. Byggnader och celler inne i polygonen räknas till projektet; beräkningsrutan är rektangeln runt den."
        }</p>
        <div class="flode-rad-slut">
          ${slag === "polygon" && !klarForm && pkt.length >= 3 ? `<button type="button" class="flode-knapp liten" id="np-klar">Klar</button>` : ""}
          <button type="button" class="flode-knapp liten" id="np-rensa"${pkt.length === 0 ? " disabled" : ""}>Rensa</button>
        </div>
      </fieldset>
      ${avgr && klarForm ? `<p class="flode-liten" id="np-yta">Avgränsning: ${esc(arealText(avgr, poly))}</p>` : ""}
      ${polygonRuta ? `<p class="flode-fel" role="alert" id="np-polygonfel">${esc(polygonRuta)}</p>` : ""}
      ${pl ? `<div class="flode-ruta ${pl.nivå === "ok" ? "ok" : pl.nivå === "varning" ? "varning" : ""}" id="np-placering" role="status"><p>${esc(pl.text)}</p></div>` : ""}
      <div class="flode-falt"><label for="np-namn">Namn</label>
        <input type="text" id="np-namn" maxlength="120" autocomplete="off" value="${esc(namnVarde)}" /></div>
      ${flera ? `<div class="flode-falt"><label for="np-org">Organisation</label><select id="np-org"><option value="">Välj …</option>${svar.organisationer.map((o) => `<option value="${esc(o.id)}"${o.id === orgVarde ? " selected" : ""}>${esc(o.namn)}</option>`).join("")}</select></div>` : ""}
      <label class="flode-fraga"><input type="checkbox" id="np-synlig"${synligVarde ? " checked" : ""} /> <span class="flode-fraga-text">Synligt för organisationen (admin och ägare får utredarrätt, ledare läsrätt)</span></label>
      ${fel ? `<p class="flode-fel" role="alert" id="np-fel">${esc(fel)}</p>` : ""}
      <div class="flode-rad-slut">
        <p class="flode-liten flode-dampad" id="np-hinder">${klarForm ? esc(hinder.join(" ")) : esc(hinder.filter((h) => !h.startsWith("Rita")).join(" "))}</p>
        <button type="button" class="flode-knapp svart stor" id="np-skapa"${hinder.length > 0 || skapar ? " disabled" : ""}>${skapar ? "Skapar …" : "Skapa"}</button>
      </div>`;
    if (aktiv) inn.querySelector<HTMLElement>(`#${CSS.escape(aktiv)}`)?.focus();
    kort();
  };

  kopplaLaddaOm(rot, ac.signal);
  const lyssna = (typ: string, f: (h: Event) => void): void => el("np-innehall").addEventListener(typ, f, { signal: ac.signal });
  lyssna("click", (h) => {
    const m = (h.target as Element).closest<HTMLElement>("[data-slag],#np-rensa,#np-klar,#np-skapa");
    if (!m) return;
    if (m.dataset.slag) {
      slag = m.dataset.slag as Slag;
      nollstall();
      uppdatera();
    } else if (m.id === "np-rensa") {
      nollstall();
      uppdatera();
    } else if (m.id === "np-klar") {
      klarForm = pkt.length >= 3;
      mus = null;
      uppdatera();
    } else if (m.id === "np-skapa") void skapa();
  });
  lyssna("change", (h) => {
    const m = h.target as HTMLInputElement;
    if (m.id === "np-geo") {
      geo = svar?.geografier.find((g) => g.ref === m.value) ?? geo;
      nollstall();
      if (geo && motor) motor.anpassa({ x_min: geo.gransen_3011[0], y_min: geo.gransen_3011[1], x_max: geo.gransen_3011[2], y_max: geo.gransen_3011[3] });
      uppdatera();
    } else rit();
  });
  lyssna("input", (h) => {
    if ((h.target as HTMLElement).id === "np-namn") {
      // Bara knappen och hindertexten uppdateras, så att markören stannar i fältet.
      const f = fast();
      const { avgr } = f;
      const flera = (svar?.organisationer.length ?? 0) > 1;
      const hinder = formularFel({
        geografi: geo,
        namn: (h.target as HTMLInputElement).value,
        avgransning: avgr,
        polygon: riktigPolygon(f),
        horn: f.horn,
        organisation: el<HTMLSelectElement>("np-innehall").querySelector<HTMLSelectElement>("#np-org")?.value || null,
        flera,
      });
      const knapp = el<HTMLButtonElement>("np-skapa");
      if (knapp) knapp.disabled = hinder.length > 0 || skapar;
      const t = el("np-hinder");
      if (t) t.textContent = hinder.filter((x) => klarForm || !x.startsWith("Rita")).join(" ");
    }
  });

  const skapa = async (): Promise<void> => {
    const { avgr, polygon } = fast();
    if (!geo || !avgr || !polygon) return;
    const namn = el<HTMLInputElement>("np-namn").value;
    const flera = (svar?.organisationer.length ?? 0) > 1;
    const org = flera ? el<HTMLSelectElement>("np-org").value : null;
    skapar = true;
    fel = null;
    rit();
    try {
      const b = skapaBegaran(geo, namn, slag === "rektangel" ? avgr : polygon, el<HTMLInputElement>("np-synlig").checked, org);
      const r = await skapaProjekt(b);
      if (avslutad) return;
      resultat = r;
    } catch (e) {
      fel = e instanceof Error ? e.message : String(e);
    }
    skapar = false;
    if (!avslutad) rit();
  };

  rot.querySelector("#np-osm")?.addEventListener(
    "change",
    (h) => {
      osmPa = (h.target as HTMLInputElement).checked;
      sparaOsmVal(webbLagringOsm(), osmPa);
      synkaOsm();
    },
    { signal: ac.signal },
  );
  const klar = (async () => {
    try {
      svar = await hamtaGeografier();
    } catch (e) {
      // Kontoläget avgör vilken förklaring som är rätt (lokalt eller inloggad): vänta in det om det inte hunnit hämtas.
      if (kontoLage().lage === "okand") await laddaKonto().catch(() => undefined);
      if (!avslutad) {
        el("np-innehall").innerHTML = visaFel(nyttProjektFel(e, kontoLage()));
        el("np-innehall").querySelector<HTMLElement>("#np-felbesked")?.focus();
      }
      return;
    }
    if (avslutad) return;
    if (svar.geografier.length === 0) {
      const k = kontoLage();
      el("np-innehall").innerHTML = visaFel(ingenGeografi(k.lage === "in" && k.superadmin === true));
      return;
    }
    geo = svar.geografier[svar.geografier.length - 1] ?? null;
    motor = new Kartmotor(el("np-karta"));
    motor.settRutor([]);
    synkaOsm();
    motor.settPekare(lyssnare);
    motor.settMarkorstil("crosshair");
    if (geo) motor.anpassa({ x_min: geo.gransen_3011[0], y_min: geo.gransen_3011[1], x_max: geo.gransen_3011[2], y_max: geo.gransen_3011[3] });
    if (motor.karta.isStyleLoaded()) byggLager();
    else motor.karta.once("load", byggLager);
    rit();
  })();

  return {
    klar,
    avsluta: () => {
      avslutad = true;
      ac.abort();
      motor?.settPekare(null);
      motor?.avsluta();
    },
  };
}
