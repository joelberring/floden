import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";

import {
  beraknaRisk,
  hamtaHalsa,
  hamtaOmradeLager,
  hamtaOmraden,
  hamtaRegnKatalog,
  korSimulering,
} from "./api";
import { FlodenKartaKontroll, STANDARD_LAGER, type RitLage } from "./karta";
import type {
  JamforLage,
  KorBegaran,
  KorSvar,
  LagerId,
  LappByggnadPayload,
  LappGataPayload,
  LappMagasinPayload,
  OmradeLagerSvar,
  OsakerhetBlock,
  PercentilNyckel,
  RegnForvalInfo,
  RegnKatalogSvar,
  RegnTyp,
  RiskBegaran,
  RiskSvar,
} from "./types";

interface WebbTillstand {
  omradeId: string;
  omradeLager: OmradeLagerSvar | null;
  regnKatalog: RegnKatalogSvar | null;
  resultat: KorSvar | null;
  riskResultat: RiskSvar | null;
  jamforLage: JamforLage;
  svepProcent: number;
  aktivPercentil: PercentilNyckel;
  landxmlText: string | null;
  landxmlFilnamn: string | null;
  gator: LappGataPayload[];
  byggnader: LappByggnadPayload[];
  magasin: LappMagasinPayload[];
  kallor: string[];
}

const tillstand: WebbTillstand = {
  omradeId: "",
  omradeLager: null,
  regnKatalog: null,
  resultat: null,
  riskResultat: null,
  jamforLage: "svep",
  svepProcent: 50,
  aktivPercentil: "p50",
  landxmlText: null,
  landxmlFilnamn: null,
  gator: [],
  byggnader: [],
  magasin: [],
  kallor: ["©Lantmäteriet", "SGU", "SMHI", "Floden"],
};

function hämtaEl<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) {
    throw new Error(`Saknar DOM-element #${id}`);
  }
  return el as T;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const omradeValjare = hämtaEl<HTMLSelectElement>("omrade-valjare");
const regnForvalSelect = hämtaEl<HTMLSelectElement>("regn-forval");
const regnInfo = hämtaEl<HTMLParagraphElement>("regn-info");
const intensitetInput = hämtaEl<HTMLInputElement>("regn-intensitet");
const varaktighetInput = hämtaEl<HTMLInputElement>("regn-varaktighet");
const regnAterkomsttidSelect = hämtaEl<HTMLSelectElement>("regn-aterkomsttid");
const regnKlimatfaktorSelect = hämtaEl<HTMLSelectElement>("regn-klimatfaktor");
const regnTypSelect = hämtaEl<HTMLSelectElement>("regn-typ");
const lagerLista = hämtaEl<HTMLDivElement>("lager-lista");
const svepKontroll = hämtaEl<HTMLDivElement>("svep-kontroll");
const svepReglage = hämtaEl<HTMLInputElement>("svep-reglage");
const svepProcentSpan = hämtaEl<HTMLElement>("svep-procent");
const lappIdInput = hämtaEl<HTMLInputElement>("lapp-id");
const landxmlFilInput = hämtaEl<HTMLInputElement>("landxml-fil");
const landxmlStatus = hämtaEl<HTMLParagraphElement>("landxml-status");
const gataSektionSelect = hämtaEl<HTMLSelectElement>("gata-sektion");
const byggnadEntreInput = hämtaEl<HTMLInputElement>("byggnad-entre");
const magasinVolymInput = hämtaEl<HTMLInputElement>("magasin-volym");
const ritStatusBox = hämtaEl<HTMLDivElement>("rit-status");
const ritTextSpan = hämtaEl<HTMLSpanElement>("rit-text");
const objektLista = hämtaEl<HTMLUListElement>("lapp-objekt-lista");
const korKnapp = hämtaEl<HTMLButtonElement>("kor-knapp");
const korStatus = hämtaEl<HTMLParagraphElement>("kor-status");
const resultatKort = hämtaEl<HTMLDivElement>("resultat-kort");
const riskGransdjupInput = hämtaEl<HTMLInputElement>("risk-gransdjup");
const riskTidshorisontInput = hämtaEl<HTMLInputElement>("risk-tidshorisont");
const riskMaxAterkomsttidSelect = hämtaEl<HTMLSelectElement>("risk-max-aterkomsttid");
const riskKlimatfaktorSelect = hämtaEl<HTMLSelectElement>("risk-klimatfaktor");
const riskKnapp = hämtaEl<HTMLButtonElement>("risk-knapp");
const riskStatus = hämtaEl<HTMLParagraphElement>("risk-status");
const riskResultatDiv = hämtaEl<HTMLDivElement>("risk-resultat");
const osakerhetVarden = hämtaEl<HTMLDivElement>("osakerhet-varden");
const manifestInfo = hämtaEl<HTMLDivElement>("manifest-info");
const kallorRad = hämtaEl<HTMLParagraphElement>("kallor-rad");
const aktivtOmradeTitel = hämtaEl<HTMLSpanElement>("aktivt-omrade-titel");
const koordIndikator = hämtaEl<HTMLSpanElement>("koord-indikator");
const sidavidBehallare = hämtaEl<HTMLDivElement>("sidavid-behallare");
const sidavidForeImg = hämtaEl<HTMLImageElement>("sidavid-fore-img");
const sidavidEfterImg = hämtaEl<HTMLImageElement>("sidavid-efter-img");
const sidavidDiffImg = hämtaEl<HTMLImageElement>("sidavid-diff-img");

const lagerKryssrutor = new Map<LagerId, HTMLInputElement>();

const kartaKontroll = new FlodenKartaKontroll("maplibre-karta", {
  onKoordinatHover: (e, n) => {
    koordIndikator.textContent = `EPSG:3011 · E ${e.toFixed(1)} m, N ${n.toFixed(1)} m`;
  },
  onRitPunktAndrad: (antal, lage: RitLage) => {
    if (lage === "ingen") {
      ritStatusBox.hidden = true;
      return;
    }
    ritStatusBox.hidden = false;
    const minPkt = lage === "gata" ? 2 : 3;
    ritTextSpan.textContent = `Ritar ${lage}: ${antal} punkter (minst ${minPkt} krävs).`;
  },
});

function byggLagerLista(): void {
  lagerLista.innerHTML = "";
  lagerKryssrutor.clear();
  for (const lager of STANDARD_LAGER) {
    const rad = document.createElement("div");
    rad.className = "lager-rad";

    const etikett = document.createElement("label");
    etikett.className = "lager-check";
    const kryss = document.createElement("input");
    kryss.type = "checkbox";
    kryss.checked = lager.synlig;
    kryss.dataset["lagerId"] = lager.id;
    kryss.addEventListener("change", () => {
      kartaKontroll.sattLager(lager.id, { synlig: kryss.checked });
    });
    lagerKryssrutor.set(lager.id, kryss);

    const namn = document.createElement("span");
    namn.textContent = lager.namn;
    etikett.append(kryss, namn);

    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "0";
    slider.max = "100";
    slider.value = String(Math.round(lager.opacitet * 100));
    slider.title = `Opacitet för ${lager.namn}`;
    slider.addEventListener("input", () => {
      const andel = Number(slider.value) / 100;
      kartaKontroll.sattLager(lager.id, { opacitet: andel });
    });

    rad.append(etikett, slider);
    lagerLista.append(rad);
  }
}

function synkaLagerKryssruta(id: LagerId, synlig: boolean): void {
  const kryss = lagerKryssrutor.get(id);
  if (kryss) {
    kryss.checked = synlig;
  }
}

function hittaAktivtRegnForval(): RegnForvalInfo | undefined {
  const valtId = regnForvalSelect.value;
  if (!valtId || valtId === "manuellt") {
    return undefined;
  }
  return tillstand.regnKatalog?.forval.find((f) => f.regn_id === valtId);
}

function uppdateraRegnKontroller(): void {
  const forval = hittaAktivtRegnForval();
  if (forval) {
    intensitetInput.value = String(Math.round(forval.max_intensitet_mm_h));
    varaktighetInput.value = String(Math.round(forval.varaktighet_min * 60));
    intensitetInput.disabled = true;
    varaktighetInput.disabled = true;
    regnAterkomsttidSelect.disabled = true;
    regnKlimatfaktorSelect.disabled = true;
    regnTypSelect.disabled = true;

    if (
      Array.from(regnAterkomsttidSelect.options).some(
        (o) => o.value === String(forval.aterkomsttid_ar),
      )
    ) {
      regnAterkomsttidSelect.value = String(forval.aterkomsttid_ar);
    }
    if (
      Array.from(regnKlimatfaktorSelect.options).some(
        (o) => Number(o.value) === forval.klimatfaktor,
      )
    ) {
      const match = Array.from(regnKlimatfaktorSelect.options).find(
        (o) => Number(o.value) === forval.klimatfaktor,
      );
      if (match) {
        regnKlimatfaktorSelect.value = match.value;
      }
    }
    if (forval.typ === "cds" || forval.typ === "block") {
      regnTypSelect.value = forval.typ;
    }

    regnInfo.textContent =
      `${forval.namn}: ${forval.total_djup_mm.toFixed(1)} mm totalt · ` +
      `max ${forval.max_intensitet_mm_h.toFixed(1)} mm/h · ` +
      `${forval.varaktighet_min} min · kf ${forval.klimatfaktor.toFixed(2)} · ` +
      `${forval.statistikkalla}`;
    return;
  }

  const aterkomstVal = regnAterkomsttidSelect.value;
  const harAterkomsttid = aterkomstVal !== "";
  intensitetInput.disabled = harAterkomsttid;
  varaktighetInput.disabled = false;
  regnAterkomsttidSelect.disabled = false;
  regnKlimatfaktorSelect.disabled = !harAterkomsttid;
  regnTypSelect.disabled = !harAterkomsttid;

  if (harAterkomsttid) {
    const kf = Number(regnKlimatfaktorSelect.value);
    const varMin = Math.max(5, Math.round((Number(varaktighetInput.value) || 3600) / 60));
    const typ = regnTypSelect.value === "block" ? "blockregn" : "CDS-regn";
    regnInfo.textContent =
      `Dimensionerande ${typ}: ${aterkomstVal}-årsregn, varaktighet ${varMin} min, klimatfaktor ${kf.toFixed(2)}.`;
  } else {
    regnInfo.textContent =
      "Manuellt blockregn: ange intensitet (mm/h) och varaktighet (s), eller välj ett regnförval ovan.";
  }
}

function fyllRegnKatalog(katalog: RegnKatalogSvar): void {
  tillstand.regnKatalog = katalog;

  regnForvalSelect.innerHTML = "";
  const manOpt = document.createElement("option");
  manOpt.value = "manuellt";
  manOpt.textContent = "Manuellt blockregn / områdets standard";
  regnForvalSelect.append(manOpt);

  const grupper = new Map<string, RegnForvalInfo[]>();
  for (const post of katalog.forval) {
    const lista = grupper.get(post.region) ?? [];
    lista.push(post);
    grupper.set(post.region, lista);
  }

  for (const [region, poster] of grupper.entries()) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = region;
    for (const post of poster) {
      const opt = document.createElement("option");
      opt.value = post.regn_id;
      opt.textContent = `${post.namn} – ${post.total_djup_mm.toFixed(0)} mm`;
      optgroup.append(opt);
    }
    regnForvalSelect.append(optgroup);
  }

  if (katalog.standard_aterkomsttider_ar.length > 0) {
    const tidigareRegnVal = regnAterkomsttidSelect.value;
    regnAterkomsttidSelect.innerHTML = "";
    const valfriOpt = document.createElement("option");
    valfriOpt.value = "";
    valfriOpt.textContent = "Manuellt (mm/h)";
    regnAterkomsttidSelect.append(valfriOpt);
    for (const t of katalog.standard_aterkomsttider_ar) {
      const opt = document.createElement("option");
      opt.value = String(t);
      opt.textContent = `${t} år`;
      regnAterkomsttidSelect.append(opt);
    }
    if (tidigareRegnVal) {
      regnAterkomsttidSelect.value = tidigareRegnVal;
    }

    const tidigareRiskMax = riskMaxAterkomsttidSelect.value || "100";
    const riskTider = katalog.standard_aterkomsttider_ar.filter((t) => t >= 20);
    if (riskTider.length > 0) {
      riskMaxAterkomsttidSelect.innerHTML = "";
      for (const t of riskTider) {
        const opt = document.createElement("option");
        opt.value = String(t);
        opt.textContent = `${t} år`;
        riskMaxAterkomsttidSelect.append(opt);
      }
      if (riskTider.some((t) => String(t) === tidigareRiskMax)) {
        riskMaxAterkomsttidSelect.value = tidigareRiskMax;
      } else {
        const sista = riskTider[riskTider.length - 1];
        if (sista !== undefined) {
          riskMaxAterkomsttidSelect.value = String(sista);
        }
      }
    }
  }

  if (katalog.standard_klimatfaktorer.length > 0) {
    const tidigareRegnKf = regnKlimatfaktorSelect.value || "1.24";
    const tidigareRiskKf = riskKlimatfaktorSelect.value || "1.24";

    for (const sel of [regnKlimatfaktorSelect, riskKlimatfaktorSelect]) {
      sel.innerHTML = "";
      for (const kf of katalog.standard_klimatfaktorer) {
        const opt = document.createElement("option");
        opt.value = String(kf);
        opt.textContent = kf.toFixed(2);
        sel.append(opt);
      }
    }

    if (katalog.standard_klimatfaktorer.some((k) => String(k) === tidigareRegnKf)) {
      regnKlimatfaktorSelect.value = tidigareRegnKf;
    }
    if (katalog.standard_klimatfaktorer.some((k) => String(k) === tidigareRiskKf)) {
      riskKlimatfaktorSelect.value = tidigareRiskKf;
    }
  }

  uppdateraRegnKontroller();
}

function renderaObjektLista(): void {
  objektLista.innerHTML = "";
  const poster: string[] = [];
  for (const g of tillstand.gator) {
    poster.push(`Gata ${g.gata_id} (${g.sektion}, ${g.mittlinje.length} pkt)`);
  }
  for (const b of tillstand.byggnader) {
    poster.push(`Byggnad ${b.byggnad_id} (entré ${b.entre_rh2000.toFixed(2)} m)`);
  }
  for (const m of tillstand.magasin) {
    poster.push(`Magasin ${m.magasin_id} (${m.volym_m3.toFixed(1)} m³)`);
  }
  for (const text of poster) {
    const li = document.createElement("li");
    li.textContent = text;
    objektLista.append(li);
  }
  kartaKontroll.ritaLappObjekt(tillstand.gator, tillstand.byggnader, tillstand.magasin);
}

function renderaOsakerhetOchKallor(): void {
  const osak: OsakerhetBlock | undefined =
    tillstand.resultat?.osakerhet ?? tillstand.omradeLager?.osakerhet;
  const pctData = osak?.[tillstand.aktivPercentil];

  if (osak && pctData) {
    osakerhetVarden.innerHTML = `
      <div class="mått-rad"><span>P10 maxdjup</span><strong>${osak.p10.max_djup_m.toFixed(3)} m</strong></div>
      <div class="mått-rad"><span>P50 maxdjup (median)</span><strong>${osak.p50.max_djup_m.toFixed(3)} m</strong></div>
      <div class="mått-rad"><span>P90 maxdjup</span><strong>${osak.p90.max_djup_m.toFixed(3)} m</strong></div>
      <div class="mått-rad"><span>Översvämmad yta (${tillstand.aktivPercentil.toUpperCase()})</span><strong>${pctData.oversvammad_area_m2.toFixed(1)} m²</strong></div>
      <div class="mått-rad"><span>Ensemblescenarier</span><strong>${osak.antal_scenarier} st</strong></div>
    `;
  } else {
    osakerhetVarden.textContent = "Laddar osäkerhetsstatistik…";
  }

  if (tillstand.resultat) {
    const m = tillstand.resultat.manifest;
    manifestInfo.textContent = `Manifest ${m.kontrakt} · modul: ${m.modul} · kod: ${m.kodversion}`;
  } else if (tillstand.riskResultat) {
    const m = tillstand.riskResultat.manifest;
    manifestInfo.textContent = `Manifest ${m.kontrakt} · modul: ${m.modul} · kod: ${m.kodversion}`;
  } else if (tillstand.omradeLager) {
    manifestInfo.textContent = `Basrecept: ${tillstand.omradeLager.bas_recept_sha256.slice(0, 12)}…`;
  }

  kallorRad.textContent = `Källor: ${tillstand.kallor.join(" · ")}`;
}

function renderaResultatKort(): void {
  const res = tillstand.resultat;
  if (!res) {
    resultatKort.innerHTML = `<p class="hjalptext">Tryck på "Kör simulering" för att beräkna före/efter och osäkerhet.</p>`;
    return;
  }

  const regnRader =
    res.regn.total_djup_mm !== undefined
      ? `
      <div class="mått-rad"><span>Regn</span><strong>${escapeHtml(res.regn.namn ?? "Valt regn")}</strong></div>
      <div class="mått-rad"><span>Regnmängd / max</span><strong>${res.regn.total_djup_mm.toFixed(1)} mm · ${(res.regn.max_intensitet_mm_h ?? 0).toFixed(1)} mm/h</strong></div>
    `
      : "";

  resultatKort.innerHTML = `
    ${regnRader}
    <div class="mått-rad"><span>Maxdjup före</span><strong>${res.fore.max_djup_m.toFixed(3)} m</strong></div>
    <div class="mått-rad"><span>Maxdjup efter</span><strong>${res.efter.max_djup_m.toFixed(3)} m</strong></div>
    <div class="mått-rad"><span>Max djupökning granne</span><strong>${res.max_okning_djup_granne_m.toFixed(3)} m</strong></div>
    <div class="mått-rad"><span>Grannförsämring</span><strong>${res.grann_forsamring_volym_m3.toFixed(2)} m³</strong></div>
    <div class="mått-rad"><span>Behövlig fördröjning</span><strong>${res.fordrojningsvolym_m3.toFixed(2)} m³</strong></div>
    <div class="mått-rad"><span>Nya drabbade entréer</span><strong>${res.nya_drabbade_entreer.length} st</strong></div>
    <div class="mått-rad"><span>Körtid</span><strong>${res.kortid_s.toFixed(3)} s</strong></div>
  `;
}

function renderaRiskResultat(): void {
  const risk = tillstand.riskResultat;
  if (!risk) {
    riskResultatDiv.innerHTML = `<p class="hjalptext">Tryck på "Beräkna kumulativ risk" för att integrera överskridandesannolikhet över återkomsttider.</p>`;
    return;
  }

  const s = risk.sammanfattning;
  const kritiskText =
    s.min_kritisk_aterkomsttid_ar !== null
      ? `${s.min_kritisk_aterkomsttid_ar.toFixed(1)} år`
      : `Ej nådd (>${risk.max_aterkomsttid_ar} år)`;

  const stegRader = risk.steg
    .map(
      (st) => `
      <tr>
        <td>${st.aterkomsttid_ar} år</td>
        <td>${st.total_djup_mm.toFixed(1)} mm</td>
        <td>${st.max_djup_m.toFixed(3)} m</td>
        <td>${st.area_over_grans_m2.toFixed(1)} m²</td>
      </tr>
    `,
    )
    .join("");

  riskResultatDiv.innerHTML = `
    <div class="mått-rad"><span>Yta över gränsdjup (${risk.gransdjup_m.toFixed(2)} m)</span><strong>${s.area_over_grans_m2.toFixed(1)} m² (${(s.andel_over_grans * 100).toFixed(1)} %)</strong></div>
    <div class="mått-rad"><span>Max årlig sannolikhet</span><strong>${(s.max_arlig_sannolikhet * 100).toFixed(2)} %/år</strong></div>
    <div class="mått-rad"><span>Max kumulativ sannolikhet (${risk.tidshorisont_ar} år)</span><strong>${(s.max_kumulativ_sannolikhet * 100).toFixed(1)} %</strong></div>
    <div class="mått-rad"><span>Lägsta kritiska återkomsttid</span><strong>${escapeHtml(kritiskText)}</strong></div>
    <div class="mått-rad"><span>Max förväntat årligt djup</span><strong>${s.max_forvantat_arligt_djup_m.toFixed(3)} m/år</strong></div>
    <p class="hjalptext">${escapeHtml(risk.regntyp.toUpperCase())}, ${risk.varaktighet_min} min, kf ${risk.klimatfaktor.toFixed(2)} · ${escapeHtml(risk.statistikkalla)}</p>
    <table class="risk-tabell" aria-label="Steg per återkomsttid">
      <thead>
        <tr>
          <th>Återkomsttid</th>
          <th>Regndjup</th>
          <th>Maxdjup</th>
          <th>Yta &gt; gräns</th>
        </tr>
      </thead>
      <tbody>
        ${stegRader}
      </tbody>
    </table>
  `;
}

function uppdateraSidavidBilder(): void {
  const res = tillstand.resultat;
  const omrade = tillstand.omradeLager;
  if (res) {
    sidavidForeImg.src = res.fore.data_uri;
    sidavidEfterImg.src = res.efter.data_uri;
    sidavidDiffImg.src = res.differens.data_uri;
  } else if (omrade) {
    sidavidForeImg.src = omrade.bas_max_djup.data_uri;
    sidavidEfterImg.src = omrade.bas_max_djup.data_uri;
    sidavidDiffImg.src = omrade.hillshade.data_uri;
  }
}

async function laddaOmrade(omradeId: string): Promise<void> {
  tillstand.omradeId = omradeId;
  korStatus.textContent = "Laddar markmodell och sänkgraf…";
  const lager = await hamtaOmradeLager(omradeId);
  tillstand.omradeLager = lager;
  tillstand.resultat = null;
  tillstand.riskResultat = null;
  tillstand.kallor = lager.kallor;

  if (regnForvalSelect.value === "manuellt") {
    intensitetInput.value = String(lager.standard_regn.intensitet_mm_h_medel);
    varaktighetInput.value = String(lager.standard_regn.varaktighet_s);
  }
  uppdateraRegnKontroller();

  aktivtOmradeTitel.textContent = `${lager.kod}: ${lager.namn} (${lager.bredd_px}×${lager.hojd_px} px @ ${lager.upplosning_m} m)`;

  kartaKontroll.visaOmrade(lager);
  synkaLagerKryssruta("risk", false);
  uppdateraSidavidBilder();
  renderaResultatKort();
  renderaRiskResultat();
  riskStatus.textContent = "";
  renderaOsakerhetOchKallor();
  korStatus.textContent = "Område inläst.";
}

async function starta(): Promise<void> {
  byggLagerLista();
  renderaResultatKort();
  renderaRiskResultat();

  const [halsa, omraden, regnKatalog] = await Promise.all([
    hamtaHalsa(),
    hamtaOmraden(),
    hamtaRegnKatalog().catch((): RegnKatalogSvar | null => null),
  ]);
  tillstand.kallor = halsa.kallor;

  if (regnKatalog) {
    fyllRegnKatalog(regnKatalog);
  } else {
    uppdateraRegnKontroller();
  }

  gataSektionSelect.innerHTML = "";
  for (const sek of halsa.gatusektioner) {
    const opt = document.createElement("option");
    opt.value = sek.namn;
    opt.textContent = `${sek.namn} (${sek.total_bredd_m} m)`;
    gataSektionSelect.append(opt);
  }
  if (!gataSektionSelect.value) {
    const opt = document.createElement("option");
    opt.value = "standard-12m";
    opt.textContent = "standard-12m";
    gataSektionSelect.append(opt);
  }

  omradeValjare.innerHTML = "";
  for (const o of omraden.omraden) {
    const opt = document.createElement("option");
    opt.value = o.omrade_id;
    opt.textContent = `${o.kod} – ${o.namn}`;
    omradeValjare.append(opt);
  }

  const initial =
    omraden.omraden.find((o) => o.kod === "A3")?.omrade_id ??
    omraden.omraden[0]?.omrade_id;
  if (initial) {
    omradeValjare.value = initial;
    await laddaOmrade(initial);
  }
}

omradeValjare.addEventListener("change", () => {
  void laddaOmrade(omradeValjare.value).catch((err: unknown) => {
    korStatus.textContent = `Fel: ${err instanceof Error ? err.message : String(err)}`;
  });
});

regnForvalSelect.addEventListener("change", () => {
  if (regnForvalSelect.value === "manuellt") {
    regnAterkomsttidSelect.value = "";
    if (tillstand.omradeLager) {
      intensitetInput.value = String(tillstand.omradeLager.standard_regn.intensitet_mm_h_medel);
      varaktighetInput.value = String(tillstand.omradeLager.standard_regn.varaktighet_s);
    }
  }
  uppdateraRegnKontroller();
});

regnAterkomsttidSelect.addEventListener("change", () => {
  uppdateraRegnKontroller();
});

regnKlimatfaktorSelect.addEventListener("change", () => {
  uppdateraRegnKontroller();
});

regnTypSelect.addEventListener("change", () => {
  uppdateraRegnKontroller();
});

varaktighetInput.addEventListener("input", () => {
  if (regnForvalSelect.value === "manuellt" && regnAterkomsttidSelect.value !== "") {
    uppdateraRegnKontroller();
  }
});

document.querySelectorAll<HTMLButtonElement>("[data-jamfor]").forEach((knapp) => {
  knapp.addEventListener("click", () => {
    const lage = (knapp.dataset["jamfor"] ?? "svep") as JamforLage;
    tillstand.jamforLage = lage;
    document.querySelectorAll("[data-jamfor]").forEach((k) => k.classList.remove("aktiv"));
    knapp.classList.add("aktiv");

    svepKontroll.hidden = lage !== "svep";
    sidavidBehallare.hidden = lage !== "sidavid";
    kartaKontroll.sattJamforLage(lage, tillstand.svepProcent);
    uppdateraSidavidBilder();
  });
});

svepReglage.addEventListener("input", () => {
  tillstand.svepProcent = Number(svepReglage.value);
  svepProcentSpan.textContent = `${tillstand.svepProcent} %`;
  kartaKontroll.sattJamforLage(tillstand.jamforLage, tillstand.svepProcent);
});

document.querySelectorAll<HTMLButtonElement>("[data-pct]").forEach((knapp) => {
  knapp.addEventListener("click", () => {
    const pct = (knapp.dataset["pct"] ?? "p50") as PercentilNyckel;
    tillstand.aktivPercentil = pct;
    document.querySelectorAll("[data-pct]").forEach((k) => k.classList.remove("aktiv"));
    knapp.classList.add("aktiv");
    kartaKontroll.sattPercentil(pct);
    renderaOsakerhetOchKallor();
  });
});

landxmlFilInput.addEventListener("change", () => {
  const fil = landxmlFilInput.files?.[0];
  if (!fil) {
    tillstand.landxmlText = null;
    tillstand.landxmlFilnamn = null;
    landxmlStatus.textContent = "Ingen LandXML-fil laddad.";
    return;
  }
  void fil.text().then((text) => {
    tillstand.landxmlText = text;
    tillstand.landxmlFilnamn = fil.name;
    landxmlStatus.textContent = `Laddad: ${fil.name} (${text.length} tecken)`;
  });
});

hämtaEl<HTMLButtonElement>("rita-gata-knapp").addEventListener("click", () => {
  kartaKontroll.startaRitning("gata");
});
hämtaEl<HTMLButtonElement>("rita-byggnad-knapp").addEventListener("click", () => {
  kartaKontroll.startaRitning("byggnad");
});
hämtaEl<HTMLButtonElement>("rita-magasin-knapp").addEventListener("click", () => {
  kartaKontroll.startaRitning("magasin");
});

hämtaEl<HTMLButtonElement>("sluta-rita-knapp").addEventListener("click", () => {
  const ritad = kartaKontroll.slutforRitning({
    sektion: gataSektionSelect.value || "standard-12m",
    entreRh2000: Number(byggnadEntreInput.value) || 10.4,
    magasinVolymM3: Number(magasinVolymInput.value) || 8.0,
    index: tillstand.gator.length + tillstand.byggnader.length + tillstand.magasin.length + 1,
  });
  if (!ritad) {
    korStatus.textContent = "För få punkter ritade för att skapa lappobjekt.";
    return;
  }
  if (ritad.typ === "gata") {
    tillstand.gator.push(ritad.data);
  } else if (ritad.typ === "byggnad") {
    tillstand.byggnader.push(ritad.data);
  } else {
    tillstand.magasin.push(ritad.data);
  }
  renderaObjektLista();
});

hämtaEl<HTMLButtonElement>("avbryt-rita-knapp").addEventListener("click", () => {
  kartaKontroll.avbrytRitning();
});

hämtaEl<HTMLButtonElement>("rensa-lapp-knapp").addEventListener("click", () => {
  tillstand.gator = [];
  tillstand.byggnader = [];
  tillstand.magasin = [];
  tillstand.landxmlText = null;
  tillstand.landxmlFilnamn = null;
  landxmlFilInput.value = "";
  landxmlStatus.textContent = "Ingen LandXML-fil laddad.";
  renderaObjektLista();
});

korKnapp.addEventListener("click", () => {
  if (!tillstand.omradeId) {
    return;
  }
  korKnapp.disabled = true;
  korStatus.textContent = "Kör före/efter och osäkerhetsensemble…";

  const begaran: KorBegaran = {
    omrade_id: tillstand.omradeId,
    lapp_id: lappIdInput.value.trim() || "webb-alternativ-a",
    gator: tillstand.gator,
    byggnader: tillstand.byggnader,
    magasin: tillstand.magasin,
  };

  const valtForval = regnForvalSelect.value;
  if (valtForval && valtForval !== "manuellt") {
    begaran.regn_id = valtForval;
  } else if (regnAterkomsttidSelect.value !== "") {
    begaran.aterkomsttid_ar = Number(regnAterkomsttidSelect.value);
    begaran.klimatfaktor = Number(regnKlimatfaktorSelect.value);
    begaran.varaktighet_min = Math.max(
      5,
      Math.round((Number(varaktighetInput.value) || 3600) / 60),
    );
    begaran.regntyp = (regnTypSelect.value === "block" ? "block" : "cds") as RegnTyp;
  } else {
    begaran.intensitet_mm_h = Number(intensitetInput.value);
    begaran.varaktighet_s = Number(varaktighetInput.value);
  }

  if (tillstand.landxmlText) {
    begaran.landxml = tillstand.landxmlText;
  }

  void korSimulering(begaran)
    .then((res) => {
      tillstand.resultat = res;
      tillstand.kallor = res.kallor;
      kartaKontroll.visaResultat(res);
      uppdateraSidavidBilder();
      renderaResultatKort();
      renderaOsakerhetOchKallor();
      if (res.regn.total_djup_mm !== undefined) {
        const regnNamn = res.regn.namn ?? "Valt regn";
        const kallaText = res.regn.statistikkalla ? ` · ${res.regn.statistikkalla}` : "";
        regnInfo.textContent =
          `${regnNamn}: ${res.regn.total_djup_mm.toFixed(1)} mm totalt · ` +
          `max ${(res.regn.max_intensitet_mm_h ?? 0).toFixed(1)} mm/h${kallaText}`;
      }
      korStatus.textContent = `Klar på ${res.kortid_s.toFixed(2)} s.`;
    })
    .catch((err: unknown) => {
      korStatus.textContent = `Fel vid körning: ${err instanceof Error ? err.message : String(err)}`;
    })
    .finally(() => {
      korKnapp.disabled = false;
    });
});

riskKnapp.addEventListener("click", () => {
  if (!tillstand.omradeId) {
    return;
  }

  const gransdjup = Number(riskGransdjupInput.value);
  const tidshorisont = Number(riskTidshorisontInput.value);
  const maxAterkomsttid = Number(riskMaxAterkomsttidSelect.value);
  const klimatfaktor = Number(riskKlimatfaktorSelect.value);

  if (!Number.isFinite(gransdjup) || gransdjup <= 0) {
    riskStatus.textContent = "Ange ett giltigt tröskeldjup (> 0 m).";
    return;
  }
  if (!Number.isFinite(tidshorisont) || tidshorisont < 1) {
    riskStatus.textContent = "Ange en giltig tidshorisont (>= 1 år).";
    return;
  }

  riskKnapp.disabled = true;
  riskStatus.textContent = "Beräknar kumulativ översvämningsrisk över återkomsttider…";

  const begaran: RiskBegaran = {
    omrade_id: tillstand.omradeId,
    gransdjup_m: gransdjup,
    max_aterkomsttid_ar: maxAterkomsttid,
    klimatfaktor,
    tidshorisont_ar: Math.round(tidshorisont),
  };

  void beraknaRisk(begaran)
    .then((risk) => {
      tillstand.riskResultat = risk;
      tillstand.kallor = risk.kallor;
      kartaKontroll.visaRisk(risk);
      synkaLagerKryssruta("risk", true);
      renderaRiskResultat();
      renderaOsakerhetOchKallor();
      riskStatus.textContent = `Riskberäkning klar på ${risk.kortid_s.toFixed(2)} s.`;
    })
    .catch((err: unknown) => {
      riskStatus.textContent = `Fel vid riskberäkning: ${err instanceof Error ? err.message : String(err)}`;
    })
    .finally(() => {
      riskKnapp.disabled = false;
    });
});

void starta().catch((err: unknown) => {
  korStatus.textContent = `Kunde inte ansluta till API: ${err instanceof Error ? err.message : String(err)}`;
});
