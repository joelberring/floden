/*
 * Enkel hash-router: #/ · #/projekt/<id> · #/karta/<id> · #/fragor · #/om · #/forvaltning
 * (bara med inloggning och kontodata) · #/arbetsbank/<omrade> (kontrollfallens arbetsbänk).
 * Arbetsbänken är den äldre kartan i index.html (main.ts) och göms i stället för att ritas om,
 * så att kartan finns kvar mellan sidbyten.
 */
import { forvaltningSynlig, kontoLage, laddaKonto, vidKontoandring } from "./konto";
import { hamtaModuler, hamtaOmraden, hamtaProjektLista } from "./api";
import { avslutaFragor, visaFragor } from "./fragor";
import { avslutaKarta, visaKarta } from "./kartsida";
import {
  PROJEKT_ID,
  sidaForvaltning,
  sidaOm,
  sidaProjekt,
  sidaStart,
  sidhuvud,
  type SidId,
} from "./sidor";
import { hamtaProjekt } from "./api";
import type { ModulInfo, OmradeSammanfattning, ProjektSvar } from "./types";

export type RuttSida = SidId | "arbetsbank";

export interface Rutt {
  sida: RuttSida;
  id: string | null;
  parametrar: URLSearchParams;
}

export interface RouterKrokar {
  /** Arbetsbänken visas; `omradeId` är null när adressen inte anger något område. */
  visaArbetsbank(omradeId: string | null): void;
}

export function tolkaHash(hash: string): Rutt {
  const utan = hash.replace(/^#/, "");
  const [sokvag = "", fraga = ""] = utan.split("?", 2);
  const delar = sokvag.split("/").filter((d) => d.length > 0);
  const parametrar = new URLSearchParams(fraga);
  const sidor: Record<string, RuttSida> = {
    projekt: "projekt",
    oversikt: "projekt", // den gamla översikten är sammanslagen med projektet
    karta: "karta",
    fragor: "fragor",
    om: "om",
    forvaltning: "forvaltning",
    arbetsbank: "arbetsbank",
  };
  const sida = delar[0] ? sidor[delar[0]] : undefined;
  if (!sida) {
    return { sida: "start", id: null, parametrar };
  }
  return { sida, id: delar[1] ? decodeURIComponent(delar[1]) : null, parametrar };
}

const SIDTITLAR: Record<RuttSida, string> = {
  start: "Floden – se hur ett nytt hus påverkar sol och skyfall",
  projekt: "Projekt – Floden",
  karta: "Karta – Floden",
  fragor: "Utredningsfrågor – Floden",
  om: "Om beräkningarna – Floden",
  forvaltning: "Förvaltning – Floden",
  arbetsbank: "Arbetsbänk – Floden",
};

export function startaRouter(krokar: RouterKrokar): void {
  const sidhuvudEl = document.getElementById("sidhuvud");
  const vySida = document.getElementById("vy-sida");
  const vyArbetsbank = document.getElementById("vy-karta");
  if (!sidhuvudEl || !vySida || !vyArbetsbank) {
    throw new Error("Sidskalet saknas i index.html (sidhuvud, vy-sida, vy-karta).");
  }

  let moduler: ModulInfo[] = [];
  let omraden: OmradeSammanfattning[] = [];
  let projekt: ProjektSvar | null = null;
  let harProjekt = false;
  const data = Promise.all([
    hamtaModuler()
      .then((s) => {
        moduler = s.moduler;
      })
      .catch(() => undefined),
    hamtaOmraden()
      .then((s) => {
        omraden = s.omraden;
      })
      .catch(() => undefined),
    hamtaProjektLista()
      .then(async (s) => {
        harProjekt = s.projekt.length > 0;
        if (harProjekt) projekt = await hamtaProjekt(PROJEKT_ID);
      })
      .catch(() => undefined),
  ]);

  const sidId = (s: RuttSida): SidId => (s === "arbetsbank" ? "om" : s);

  const rita = (): void => {
    const rutt = tolkaHash(window.location.hash);
    document.title = SIDTITLAR[rutt.sida];
    document.body.dataset.sida = rutt.sida;

    if (rutt.sida !== "karta") avslutaKarta();
    if (rutt.sida !== "fragor") avslutaFragor();

    const fokuseraRubrik = (): void => {
      window.requestAnimationFrame(() => {
        const h1 = vySida.querySelector<HTMLElement>("h1");
        if (h1) {
          if (!h1.hasAttribute("tabindex")) h1.setAttribute("tabindex", "-1");
          h1.focus({ preventScroll: true });
        }
      });
    };

    if (rutt.sida === "forvaltning" && kontoLage().lage !== "okand" && !forvaltningSynlig()) {
      window.location.replace("#/");
      return;
    }
    sidhuvudEl.innerHTML = sidhuvud(sidId(rutt.sida));

    if (rutt.sida === "arbetsbank") {
      vySida.hidden = true;
      vySida.innerHTML = "";
      vyArbetsbank.hidden = false;
      krokar.visaArbetsbank(rutt.id);
      // MapLibre behöver mäta om sin behållare när den blir synlig.
      window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
      return;
    }

    vyArbetsbank.hidden = true;
    vySida.hidden = false;
    if (rutt.sida === "fragor") {
      // Frågesidan håller sitt eget tillstånd och ritas inte om vid adressbyte.
      visaFragor(vySida, rutt.id);
      window.scrollTo(0, 0);
      fokuseraRubrik();
      return;
    }
    if (rutt.sida === "karta") {
      void visaKarta(vySida, rutt.parametrar.get("vy"));
      window.scrollTo(0, 0);
      fokuseraRubrik();
      return;
    }
    switch (rutt.sida) {
      case "start":
        vySida.innerHTML = sidaStart(moduler, harProjekt);
        break;
      case "projekt":
        vySida.innerHTML = sidaProjekt(projekt, moduler);
        break;
      case "om":
        vySida.innerHTML = sidaOm(moduler, omraden);
        break;
      case "forvaltning":
        vySida.innerHTML = kontoLage().lage === "okand" ? "" : sidaForvaltning();
        break;
    }
    window.scrollTo(0, 0);
    fokuseraRubrik();
  };

  // Inloggningsläget kommer från /konto/jag och kan ändra meny, hälsning och förvaltningssidan.
  vidKontoandring(() => {
    const sida = tolkaHash(window.location.hash).sida;
    if (sida !== "karta" && sida !== "fragor" && sida !== "arbetsbank") rita();
    else sidhuvudEl.innerHTML = sidhuvud(sidId(sida));
  });
  void laddaKonto();

  document.addEventListener("click", (h) => {
    const mal = h.target instanceof Element ? h.target : null;
    if (!mal) return;
    const knapp = mal.closest(".meny-knapp");
    if (knapp instanceof HTMLButtonElement) {
      const oppen = sidhuvudEl.classList.toggle("oppen");
      knapp.setAttribute("aria-expanded", String(oppen));
      return;
    }
    const lank = mal.closest("a");
    if (!lank) return;
    if (lank.classList.contains("hoppa-till")) {
      h.preventDefault();
      const innehall = vyArbetsbank.hidden ? vySida : document.getElementById("karta-huvudinnehall");
      innehall?.focus();
    }
    if (sidhuvudEl.contains(lank)) sidhuvudEl.classList.remove("oppen");
  });

  window.addEventListener("hashchange", rita);
  rita();
  // Sidorna ritas om när moduler, projekt och områden har kommit (kartan och frågorna ritas inte om).
  void data.then(() => {
    const sida = tolkaHash(window.location.hash).sida;
    if (sida !== "karta" && sida !== "fragor" && sida !== "arbetsbank") {
      rita();
    }
  });
}
