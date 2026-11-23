/*
 * Det som stegen delar om kartan: projektets byggnader, terrängskuggning och ortofoto som lager i
 * `Kartmotor`, och byggnaderna med förslagets ändringar. Ritlogiken och kartmotorn är de
 * befintliga; här kopplas de bara ihop.
 */
import { hamtaBildMedUtbredning, type ProjektInfo } from "../karta/kartapi.ts";
import type { PlanDok } from "../karta/plandok.ts";
import { Kartmotor, type RasterLager, type VyByggnad } from "../karta/kartmotor.ts";

export class BasLager {
  private readonly urler: string[] = [];
  private lager: RasterLager[] = [];
  private avslutad = false;

  constructor(
    private readonly motor: Kartmotor,
    private readonly info: ProjektInfo,
    private readonly extra: () => RasterLager[] = () => [],
  ) {}

  /** Hämtar skuggning (och ortofoto om det finns) och lägger dem under resten. */
  async ladda(projekt: string): Promise<void> {
    const jobb: Array<[string, string]> = [["skuggning", `/api/kartverktyg/${encodeURIComponent(projekt)}/geometri/skuggning.png`]];
    if (this.info.ortofoto.finns) jobb.unshift(["orto", this.info.ortofoto.png]);
    for (const [id, url] of jobb) {
      try {
        const b = await hamtaBildMedUtbredning(url);
        if (this.avslutad) {
          URL.revokeObjectURL(b.url);
          return;
        }
        this.urler.push(b.url);
        this.lager.push({ id: `bas:${id}`, opacitet: id === "skuggning" ? 0.9 : 1, synlig: true, kalla: { slag: "bild", url: b.url, utbredning: b.utbredning } });
      } catch {
        /* Underlaget är valfritt: kartan fungerar med bara byggnaderna. */
      }
    }
    this.synka();
  }

  /** Bakgrundslagren (skuggning, ortofoto) som kartan ska visa. */
  lagerListan(): RasterLager[] {
    return [...this.lager];
  }

  synka = (): void => {
    if (this.avslutad) return;
    this.motor.settLager([...this.lager, ...this.extra()]);
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
