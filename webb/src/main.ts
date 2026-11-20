import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";

import {
  hamtaHalsa,
  hamtaOmradeLager,
  hamtaOmraden,
  korSimulering,
} from "./api";
import { FlodenKartaKontroll, STANDARD_LAGER, type RitLage } from "./karta";
import type {
  JamforLage,
  KorBegaran,
  KorSvar,
  LappByggnadPayload,
  LappGataPayload,
  LappMagasinPayload,
  OmradeLagerSvar,
  OsakerhetBlock,
  PercentilNyckel,
} from "./types";

interface WebbTillstand {
  omradeId: string;
  omradeLager: OmradeLagerSvar | null;
  resultat: KorSvar | null;
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
  resultat: null,
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

const omradeValjare = hämtaEl<HTMLSelectElement>("omrade-valjare");
const intensitetInput = hämtaEl<HTMLInputElement>("regn-intensitet");
const varaktighetInput = hämtaEl<HTMLInputElement>("regn-varaktighet");
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
const osakerhetVarden = hämtaEl<HTMLDivElement>("osakerhet-varden");
const manifestInfo = hämtaEl<HTMLDivElement>("manifest-info");
const kallorRad = hämtaEl<HTMLParagraphElement>("kallor-rad");
const aktivtOmradeTitel = hämtaEl<HTMLSpanElement>("aktivt-omrade-titel");
const koordIndikator = hämtaEl<HTMLSpanElement>("koord-indikator");
const sidavidBehallare = hämtaEl<HTMLDivElement>("sidavid-behallare");
const sidavidForeImg = hämtaEl<HTMLImageElement>("sidavid-fore-img");
const sidavidEfterImg = hämtaEl<HTMLImageElement>("sidavid-efter-img");
const sidavidDiffImg = hämtaEl<HTMLImageElement>("sidavid-diff-img");

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
  for (const lager of STANDARD_LAGER) {
    const rad = document.createElement("div");
    rad.className = "lager-rad";

    const etikett = document.createElement("label");
    etikett.className = "lager-check";
    const kryss = document.createElement("input");
    kryss.type = "checkbox";
    kryss.checked = lager.synlig;
    kryss.addEventListener("change", () => {
      kartaKontroll.sattLager(lager.id, { synlig: kryss.checked });
    });

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

  resultatKort.innerHTML = `
    <div class="mått-rad"><span>Maxdjup före</span><strong>${res.fore.max_djup_m.toFixed(3)} m</strong></div>
    <div class="mått-rad"><span>Maxdjup efter</span><strong>${res.efter.max_djup_m.toFixed(3)} m</strong></div>
    <div class="mått-rad"><span>Max djupökning granne</span><strong>${res.max_okning_djup_granne_m.toFixed(3)} m</strong></div>
    <div class="mått-rad"><span>Grannförsämring</span><strong>${res.grann_forsamring_volym_m3.toFixed(2)} m³</strong></div>
    <div class="mått-rad"><span>Behövlig fördröjning</span><strong>${res.fordrojningsvolym_m3.toFixed(2)} m³</strong></div>
    <div class="mått-rad"><span>Nya drabbade entréer</span><strong>${res.nya_drabbade_entreer.length} st</strong></div>
    <div class="mått-rad"><span>Körtid</span><strong>${res.kortid_s.toFixed(3)} s</strong></div>
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
  tillstand.kallor = lager.kallor;

  intensitetInput.value = String(lager.standard_regn.intensitet_mm_h_medel);
  varaktighetInput.value = String(lager.standard_regn.varaktighet_s);
  aktivtOmradeTitel.textContent = `${lager.kod}: ${lager.namn} (${lager.bredd_px}×${lager.hojd_px} px @ ${lager.upplosning_m} m)`;

  kartaKontroll.visaOmrade(lager);
  uppdateraSidavidBilder();
  renderaResultatKort();
  renderaOsakerhetOchKallor();
  korStatus.textContent = "Område inläst.";
}

async function starta(): Promise<void> {
  byggLagerLista();
  renderaResultatKort();

  const [halsa, omraden] = await Promise.all([hamtaHalsa(), hamtaOmraden()]);
  tillstand.kallor = halsa.kallor;

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
    intensitet_mm_h: Number(intensitetInput.value),
    varaktighet_s: Number(varaktighetInput.value),
    lapp_id: lappIdInput.value.trim() || "webb-alternativ-a",
    gator: tillstand.gator,
    byggnader: tillstand.byggnader,
    magasin: tillstand.magasin,
  };
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
      korStatus.textContent = `Klar på ${res.kortid_s.toFixed(2)} s.`;
    })
    .catch((err: unknown) => {
      korStatus.textContent = `Fel vid körning: ${err instanceof Error ? err.message : String(err)}`;
    })
    .finally(() => {
      korKnapp.disabled = false;
    });
});

void starta().catch((err: unknown) => {
  korStatus.textContent = `Kunde inte ansluta till API: ${err instanceof Error ? err.message : String(err)}`;
});
