/*
 * Enkel hash-router: #/ · #/projekt/<id> · #/fragor · #/om · #/matematik · #/forvaltning
 * (bara med inloggning och kontodata) · #/nycklar (inloggade) · #/administration (admin) ·
 * #/arbetsbank/<omrade> (kontrollfallens arbetsbänk).
 * Arbetsbänken är den äldre kartan i index.html (main.ts) och göms i stället för att ritas om,
 * så att kartan finns kvar mellan sidbyten. Den finns inte i menyn eller på någon sida: den nås
 * bara med adressen och visas bara för superadmin, eller alltid när inloggning är av (lokalt).
 */
import {
  adminTillaten,
  arbetsbankTillaten,
  forvaltningSynlig,
  inloggningsAdress,
  kontoLage,
  laddaKonto,
  vidKontoandring,
} from "./konto";
import { hamtaModuler, hamtaOmraden, hamtaProjektLista } from "./api";
import { avslutaFragor, visaFragor } from "./fragor";
import { avslutaFlode, projektLaddat, tolkaFlodeAdress, visaFlode } from "./flode/index";
import { visaMatematik } from "./matematik/sida";
import { avslutaProv, visaProvWebgpu } from "./prov/webgpuProv";
import { visaAdministration, visaNycklar } from "./kontosidor";
import {
  PROJEKT_ID,
  sidaForvaltning,
  sidaInloggning,
  sidaOm,
  sidaProjekt,
  sidaProjektUtanData,
  sidaStart,
  sidhuvud,
  type SidId,
} from "./sidor";
import { hamtaProjekt } from "./api";
import { kartaOmdirigering, SIDTITLAR, tolkaHash, type RuttSida } from "./rutter";
import type { ModulInfo, OmradeSammanfattning, ProjektListaPost, ProjektSvar } from "./types";

export interface RouterKrokar {
  /** Arbetsbänken visas; `omradeId` är null när adressen inte anger något område. */
  visaArbetsbank(omradeId: string | null): void;
}

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
  let projektLista: ProjektListaPost[] = [];
  let projektListaKlar = false;
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
        projektLista = s.projekt;
        // Demokvarteret har egen sida med siffror; projekt ur registret saknar beräkningar än.
        harProjekt = s.projekt.some((p) => p.id === PROJEKT_ID && p.finns !== false);
        if (harProjekt) projekt = await hamtaProjekt(PROJEKT_ID);
      })
      .catch(() => undefined)
      .finally(() => {
        projektListaKlar = true;
      }),
  ]);

  const sidId = (s: RuttSida): SidId => (s === "arbetsbank" || s === "prov" ? "om" : s);

  const rita = (): void => {
    // Den gamla kartsidan finns inte kvar: #/karta leder till motsvarande steg i flödet.
    const karta = kartaOmdirigering(window.location.hash);
    if (karta) {
      window.location.replace(karta);
      return;
    }
    const rutt = tolkaHash(window.location.hash);
    document.title = SIDTITLAR[rutt.sida];
    document.body.dataset.sida = rutt.sida;

    // Utloggad på en server med inloggning: bara inloggningssidan, oavsett adress.
    if (kontoLage().lage === "ut") {
      avslutaFragor();
      document.title = "Floden";
      document.body.dataset.sida = "inloggning";
      sidhuvudEl.innerHTML = "";
      vyArbetsbank.hidden = true;
      vySida.hidden = false;
      vySida.innerHTML = sidaInloggning(inloggningsAdress("/"));
      return;
    }
    // Flödet (#/ och #/p/<projekt>/<steg>) har eget sidhuvud och egna steg.
    const flode = tolkaFlodeAdress(window.location.hash);
    if (flode) {
      avslutaFragor();
      document.body.dataset.sida = "flode";
      vyArbetsbank.hidden = true;
      vySida.hidden = false;
      visaFlode(sidhuvudEl, vySida, flode);
      return;
    }
    avslutaFlode();
    if (rutt.sida !== "fragor") avslutaFragor();
    if (rutt.sida !== "prov") avslutaProv();

    // Matematik och forskning: flödets sidhuvud och stil, men inget steg är aktivt.
    if (rutt.sida === "matematik") {
      document.body.dataset.sida = "flode";
      vyArbetsbank.hidden = true;
      vySida.hidden = false;
      visaMatematik(sidhuvudEl, vySida);
      window.scrollTo(0, 0);
      return;
    }

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
    // Sidor med behörighetskrav väntar på /konto/jag och går annars tillbaka till startsidan.
    const lage = kontoLage();
    const kravs: Partial<Record<RuttSida, () => boolean>> = {
      arbetsbank: arbetsbankTillaten,
      administration: adminTillaten,
    };
    const krav = kravs[rutt.sida];
    if (krav && lage.lage !== "okand" && !krav()) {
      window.location.replace("#/");
      return;
    }
    sidhuvudEl.innerHTML = sidhuvud(sidId(rutt.sida));
    if (krav && lage.lage === "okand") {
      vyArbetsbank.hidden = true;
      vySida.hidden = false;
      vySida.innerHTML = "";
      return;
    }

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
    if (rutt.sida === "prov") {
      // Provsidan håller sitt eget tillstånd; den ritas inte om vid andra sidbyten.
      void visaProvWebgpu(vySida);
      window.scrollTo(0, 0);
      fokuseraRubrik();
      return;
    }
    switch (rutt.sida) {
      case "start":
        vySida.innerHTML = sidaStart(moduler, harProjekt, projektLista);
        break;
      case "projekt":
        if (rutt.id && rutt.id !== PROJEKT_ID) {
          const p = projektLista.find((x) => x.id === rutt.id) ?? null;
          vySida.innerHTML = p || projektListaKlar ? sidaProjektUtanData(p) : "";
        } else {
          vySida.innerHTML = sidaProjekt(projekt, moduler);
        }
        break;
      case "nycklar":
        void visaNycklar(vySida);
        break;
      case "administration":
        void visaAdministration(vySida);
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
    const flode = tolkaFlodeAdress(window.location.hash);
    if (flode && kontoLage().lage !== "ut") {
      projektLaddat(sidhuvudEl, flode);
      return;
    }
    const sida = tolkaHash(window.location.hash).sida;
    if (kontoLage().lage === "ut" || (sida !== "fragor" && sida !== "arbetsbank" && sida !== "prov")) {
      rita();
    } else sidhuvudEl.innerHTML = sidhuvud(sidId(sida));
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
  // Sidorna ritas om när moduler, projekt och områden har kommit (frågorna och kontosidorna ritas inte om).
  void data.then(() => {
    if (tolkaFlodeAdress(window.location.hash)) return;
    const sida = tolkaHash(window.location.hash).sida;
    if (sida !== "fragor" && sida !== "arbetsbank" && sida !== "prov") {
      rita();
    }
  });
}
