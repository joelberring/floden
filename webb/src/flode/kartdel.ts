/*
 * Det som stegen delar om kartan: projektets bakgrundslager (terrängskuggning, vegetation, ortofoto
 * och underlag från andra tjänster) som lager i `Kartmotor`, och byggnaderna med förslagets
 * ändringar. Lagren ligger i `LagerLista` (synlighet, täckning och ordning sparas per projekt i
 * webbläsaren), så att Förslag och Resultat visar samma underlag. Ritlogiken och kartmotorn är de
 * befintliga; här kopplas de bara ihop.
 */
import { hamtaBildMedUtbredning, type ProjektInfo } from "../karta/kartapi.ts";
import { Kartmotor, type RasterLager, type VyByggnad } from "../karta/kartmotor.ts";
import { LagerLista } from "../karta/lager.ts";
import type { PlanDok } from "../karta/plandok.ts";
import { lasUnderlag, type Underlag } from "../karta/wms.ts";
import { BYGGNADER_ID, ORTO_ID, PLAN_ID, rasterAv, SKUGGNING_ID, VEGETATION_ID } from "./rasterlager.ts";

export class BasLager {
  private readonly urler: string[] = [];
  private avslutad = false;
  private readonly hamtade = new Set<string>();

  private projekt = "";
  private registrerad = false;
  readonly lager: LagerLista;

  /** `lager` delas mellan stegen; utan det får kartan en egen lista som inte sparas (projektlistan). */
  constructor(
    private readonly motor: Kartmotor,
    private readonly info: ProjektInfo,
    lager: LagerLista | null = null,
  ) {
    this.lager = lager ?? new LagerLista("", null);
  }

  /** Lagren som finns i alla steg. Sparade val (synlighet, täckning) går före förvalen. */
  registrera(projekt: string): void {
    if (this.registrerad) return;
    this.registrerad = true;
    this.projekt = projekt;
    const l = this.lager;
    const orto = this.info.ortofoto;
    l.registrera({
      id: ORTO_ID,
      grupp: "underlag",
      namn: "Ortofoto",
      synlig: orto.finns,
      opacitet: 1,
      hjalp: orto.finns ? orto.kalla : undefined,
      ejTillgangligt: orto.finns ? undefined : "Inget ortofoto finns lokalt. Det hämtas med floden data lantmateriet (se README).",
      visning: "2d",
    });
    l.registrera({ id: SKUGGNING_ID, grupp: "geometri", namn: "Terräng (skuggning)", synlig: true, opacitet: 0.9, hjalp: "Skuggad terrängmodell ur projektets höjddata.", visning: "2d" });
    l.registrera({ id: VEGETATION_ID, grupp: "geometri", namn: "Vegetation", synlig: false, opacitet: 0.9, hjalp: "Växtlighetens höjd ur projektets data.", visning: "2d" });
    l.registrera({ id: BYGGNADER_ID, grupp: "geometri", namn: "Byggnader", synlig: true, opacitet: 1, hjalp: "Byggnaderna: fotavtryck i kartan, volymer i 3D." });
    l.registrera({ id: PLAN_ID, grupp: "planforslag", namn: "Aktuellt förslag", synlig: true, opacitet: 1, hjalp: "Det du ritar just nu." });
    for (const u of lasUnderlag(this.projekt)) this.registreraUnderlag(u);
  }

  registreraUnderlag(u: Underlag): void {
    this.lager.registrera({
      id: `u:${u.id}`,
      grupp: "underlag",
      namn: u.namn,
      synlig: true,
      opacitet: 1,
      hjalp: u.kalla,
      kanTasBort: true,
      data: { underlag: u },
    });
  }

  /** Hämtar skuggning, vegetation och ortofoto (om det finns) som bilder med utbredning. */
  async ladda(projekt: string): Promise<void> {
    this.registrera(projekt);
    const jobb: Array<[string, string, string]> = [[SKUGGNING_ID, this.skuggningsUrl(), "Terräng (skuggning)"]];
    if (this.info.ortofoto.finns) jobb.unshift([ORTO_ID, this.info.ortofoto.png, "Ortofoto"]);
    await Promise.all([...jobb.map(([id, url, namn]) => this.hamta(id, url, namn)), this.hamtaVegetation()]);
    this.synka();
  }

  private skuggningsUrl(): string {
    return `/api/kartverktyg/${encodeURIComponent(this.projekt)}/geometri/skuggning.png`;
  }

  /** Vegetationen hämtas först när lagret tänds. */
  private async hamtaVegetation(): Promise<void> {
    if (!this.lager.hamta(VEGETATION_ID)?.synlig) return;
    await this.hamta(VEGETATION_ID, `/api/kartverktyg/${encodeURIComponent(this.projekt)}/geometri/vegetation.png`, "Vegetation");
  }

  private async hamta(id: string, url: string, namn: string): Promise<void> {
    if (this.hamtade.has(id)) return;
    this.hamtade.add(id);
    try {
      const b = await hamtaBildMedUtbredning(url);
      if (this.avslutad) {
        URL.revokeObjectURL(b.url);
        return;
      }
      this.urler.push(b.url);
      const g = this.lager.hamta(id);
      if (g) g.data = { url: b.url, utbredning: b.utbredning };
    } catch (e) {
      if (this.avslutad) return;
      // Underlaget är valfritt: kartan fungerar med bara byggnaderna. Skälet visas i lagerpanelen.
      const g = this.lager.hamta(id);
      this.lager.registrera({ ...(g ?? { id, grupp: "geometri", namn, synlig: false, opacitet: 1 }), ejTillgangligt: `Kunde inte hämtas: ${e instanceof Error ? e.message : String(e)}` });
    }
  }

  /** Bakgrundslagren i den ordning kartan staplar dem (nederst först): underlag, sedan geometri. */
  lagerListan(): RasterLager[] {
    const ut: RasterLager[] = [];
    for (const g of ["underlag", "geometri"] as const) {
      // Panelen visar översta lagret först; kartan staplas nerifrån.
      for (const l of [...this.lager.iGrupp(g)].reverse()) {
        const r = rasterAv(l);
        if (r) ut.push(r);
      }
    }
    return ut;
  }

  /** Byggnadslagrets och förslagslagrets stil (tänt/släckt, täckning) ur lagerlistan. */
  byggnadStil(): { synlig: boolean; opacitet: number } {
    const b = this.lager.hamta(BYGGNADER_ID);
    return { synlig: Boolean(b?.synlig), opacitet: b?.opacitet ?? 1 };
  }

  planStil(): { synlig: boolean; opacitet: number } {
    const p = this.lager.hamta(PLAN_ID);
    return { synlig: Boolean(p?.synlig), opacitet: p?.opacitet ?? 1 };
  }

  synka = (): void => {
    if (this.avslutad) return;
    if (this.registrerad && this.lager.hamta(VEGETATION_ID)?.synlig && !this.hamtade.has(VEGETATION_ID)) {
      void this.hamtaVegetation().then(() => this.synka());
    }
    this.motor.settLager(this.lagerListan());
  };

  avsluta(): void {
    this.avslutad = true;
    for (const u of this.urler) URL.revokeObjectURL(u);
  }
}

/** Byggnaderna med förslagets ändringar (ändrad höjd, riven). */
export function byggnaderMedForslag(info: ProjektInfo, dok: PlanDok | null): VyByggnad[] {
  return info.byggnader.map((b) => ({
    ring: b.ring,
    hal: b.hal,
    status: dok?.bort.has(b.id) ? "bort" : dok?.hojd.has(b.id) ? "andrad" : "oforandrad",
  }));
}

export function skapaKarta(yta: HTMLElement, info: ProjektInfo): Kartmotor {
  const m = new Kartmotor(yta);
  m.settRutor([]);
  m.anpassa(info.utbredning);
  return m;
}
