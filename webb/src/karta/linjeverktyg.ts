/*
 * Linje- och ytverktyget (R3): rita linje och yta i det valda ritlagret, fäst mot hörn, linjer och kanter
 * (befintliga byggnader, brytlinjer, projektets avgränsning och andras figurer), vinkellås, följ kant,
 * klipp, slå ihop, vänd, förenkla, lägg till och ta bort punkt, flytta punkt och figur. Styrenhet utan
 * webbläsare som Ritverktyg delegerar till.
 *
 * Förebild och logik: O-Tids linjehantering (MIT, © 2026 Joel Berring, joelberring/otid @ b0b952f):
 * map-project-join.ts (klipp, slå ihop, vänd), generalize.ts (förenkla), map-project-draw-assist.ts
 * (följ kant, vinkellås), och interaktionen i map-project-point-edit. De rena delarna ligger i linje.ts och
 * foljkant.ts; här är tillstånd och pekarhändelser. Skillnader: SWEREF-meter i stället för kartans mm,
 * historiken är PlanDok (K50), och en figur har en roll i beräkningen via sitt lager.
 */
import { foljKant, type FoljLinje } from "./foljkant.ts";
import { avstand, begransadRackvidd, sjalvkorsande, yta, type Punkt } from "./geom.ts";
import { flyttaPunkt, forenkla, infogaPunkt, klipp, kopiaP, langd, mittpunkter, narmastPunkt, narmastSida, slaIhop, taBortPunkt, vand, type P3 } from "./linje.ts";
import { forvalEgenskaper } from "./egenskaper.ts";
import type { Figur, PlanDok } from "./plandok.ts";
import { byggFastIndex, fastAt, fastPaStrale, fastRackvidd, lasVinkel, ringExtra, utkastExtra, vinkelRef, type FastIndex, type FastTraff } from "./snap.ts";

export type LinjeVerktyg = "linje" | "yta" | "klipp" | "ihop" | "vand" | "forenkla";
export const LINJEVERKTYG: readonly LinjeVerktyg[] = ["linje", "yta", "klipp", "ihop", "vand", "forenkla"];
export const arLinjeVerktyg = (v: string): v is LinjeVerktyg => (LINJEVERKTYG as readonly string[]).includes(v);

/** Förenklingens tolerans (m) när man trycker Förenkla: avvikelsen från den ursprungliga linjen. */
export const FORENKLA_TOLERANS_M = 0.25;
const TRAFF_PX = 8;
const PUNKT_PX = 10;

export interface LinjePekare {
  pekare: string;
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
}

export interface LinjeMeddelande {
  typ: "fel" | "info";
  text: string;
}

export interface LinjeMiljo {
  meterPerPx(): number;
  meddela(m: LinjeMeddelande | null): void;
  /** En ny figur är ritad (för att öppna egenskapspanelen). */
  figurSkapad?(id: string): void;
}

/** Inställningar som Ritverktyg äger: fäst, rät vinkel och följ kant. */
export interface LinjeInstallning {
  fastPa: boolean;
  ratVinkel: boolean;
  foljKant: boolean;
}

/** Något att fästa mot som kommer utifrån: projektets avgränsning, basens brytlinjer. `id` börjar med `bl:` eller `avgr`. */
export interface ExtraLinje {
  id: string;
  punkter: readonly Punkt[];
  sluten: boolean;
}

export interface BasForm {
  id: string;
  ring: readonly Punkt[];
  hal: ReadonlyArray<readonly Punkt[]>;
}

type Drag = { slag: "punkt"; id: string; index: number; fore: P3[] } | { slag: "figur"; id: string; fore: P3[]; start: Punkt; ankare: number };

export class Linjeverktyg {
  utkast: P3[] = [];
  mark: Punkt | null = null;
  fast: FastTraff | null = null;
  lasLinje: [Punkt, Punkt] | null = null;
  /** Förhandsvisning av följ kant: punkterna längs kanten från senaste punkten till pekaren. */
  foljForhand: P3[] | null = null;
  /** Markerad figur och markerat hörn i den. */
  val: string | null = null;
  valdPunkt: number | null = null;
  /** Slå ihop: den första figuren som klickats. */
  ihopForsta: string | null = null;
  /** Figurens form under drag (ännu inte sparad). */
  dragFigur: { id: string; punkter: P3[] } | null = null;
  private drag: Drag | null = null;
  private startFast: FastTraff | undefined;
  private sistaFastId: string | undefined;
  private index: FastIndex | undefined;
  private indexNr = -1;
  private readonly lyssnare: Array<() => void> = [];

  private readonly dok: PlanDok;
  private readonly bas: ReadonlyMap<string, BasForm>;
  private readonly extra: readonly ExtraLinje[];
  private readonly miljo: LinjeMiljo;
  private readonly inst: () => LinjeInstallning;

  constructor(dok: PlanDok, bas: ReadonlyMap<string, BasForm>, extra: readonly ExtraLinje[], miljo: LinjeMiljo, inst: () => LinjeInstallning) {
    this.dok = dok;
    this.bas = bas;
    this.extra = extra;
    this.miljo = miljo;
    this.inst = inst;
    dok.vidAndring(() => {
      if (this.val && !this.dok.figurer.has(this.val)) this.markera(null);
      else this.signalera();
    });
  }

  vidAndring(f: () => void): void {
    this.lyssnare.push(f);
  }

  private signalera(): void {
    for (const f of this.lyssnare) f();
  }

  // ------------------------------------------------------------------ figurer och fäst

  figur(id: string | null): Figur | undefined {
    return id ? this.dok.figurer.get(id) : undefined;
  }

  /** Figurens punkter (under drag den dragna formen). */
  punkterAv(f: Figur): P3[] {
    return this.dragFigur?.id === f.id ? this.dragFigur.punkter : f.punkter;
  }

  /** Synliga figurer (de i dolda lager ritas och träffas inte). */
  synliga(): Figur[] {
    return [...this.dok.figurer.values()].filter((f) => this.dok.arFigurSynlig(f));
  }

  /** Linjen eller ringen med id som fästträffar pekar på. */
  linjeAv(id: string): FoljLinje | undefined {
    if (id.startsWith("b:")) {
      const b = this.bas.get(id.slice(2));
      return b ? { punkter: b.ring, sluten: true } : undefined;
    }
    if (id.startsWith("n:")) {
      const h = this.dok.ny.get(id.slice(2));
      return h ? { punkter: h.ring, sluten: true } : undefined;
    }
    if (id.startsWith("f:")) {
      const f = this.dok.figurer.get(id.slice(2));
      return f ? { punkter: f.punkter, sluten: f.typ === "yta" } : undefined;
    }
    const e = this.extra.find((x) => x.id === id);
    return e ? { punkter: e.punkter, sluten: e.sluten } : undefined;
  }

  /** Fästindex över byggnader, nya hus, figurer och utifrån givna linjer. Byggs om när dokumentet ändrats. */
  fastIndex(): FastIndex {
    if (this.index && this.indexNr === this.dok.andringsNr) return this.index;
    const objekt: Array<{ id: string; ringar: Array<readonly Punkt[]>; oppna?: boolean }> = [];
    for (const b of this.bas.values()) objekt.push({ id: `b:${b.id}`, ringar: [b.ring, ...b.hal] });
    for (const h of this.dok.ny.values()) if (this.dok.arSynligt(h)) objekt.push({ id: `n:${h.id}`, ringar: [h.ring] });
    for (const f of this.synliga()) objekt.push({ id: `f:${f.id}`, ringar: [f.punkter], oppna: f.typ === "linje" });
    for (const e of this.extra) objekt.push({ id: e.id, ringar: [e.punkter], oppna: !e.sluten });
    this.index = byggFastIndex(objekt);
    this.indexNr = this.dok.andringsNr;
    return this.index;
  }

  /** Punkten efter fäst, vinkellås och (med följ kant) längs kanten. Uppdaterar `fast`, `lasLinje`, `foljForhand`. */
  private berakna(p: Punkt, e: LinjePekare, utkast: readonly P3[], exkl?: ReadonlySet<string>, extra?: ReturnType<typeof ringExtra>): Punkt {
    const inst = this.inst();
    const rack = fastRackvidd(e.pekare, this.miljo.meterPerPx());
    this.lasLinje = null;
    this.foljForhand = null;
    const sista = utkast[utkast.length - 1];
    if (sista && (inst.foljKant || e.shift) && this.sistaFastId) {
      const linje = this.linjeAv(this.sistaFastId);
      const f = linje ? foljKant(linje, sista, p) : undefined;
      if (f) {
        this.fast = null;
        this.foljForhand = [kopiaP(sista), ...f.punkter];
        return f.slut.at;
      }
    }
    const lasa = e.ctrl ? !inst.ratVinkel : inst.ratVinkel;
    if (lasa && sista) {
      const ref = vinkelRef(utkast, this.startFast);
      const l = lasVinkel(sista, p, ref);
      this.lasLinje = [sista, { x: sista.x + l.dir.x * 1e4, y: sista.y + l.dir.y * 1e4 }];
      if (inst.fastPa) {
        const t = fastPaStrale(this.fastIndex(), { origin: sista, dir: l.dir, nara: l.at, rackvidd: rack, extra: utkastExtra([...utkast, l.at]) });
        if (t) {
          this.fast = t;
          return t.at;
        }
      }
      this.fast = null;
      return l.at;
    }
    if (!inst.fastPa || e.alt) {
      this.fast = null;
      return p;
    }
    const t = fastAt(this.fastIndex(), {
      punkt: p,
      rackvidd: rack,
      ...(exkl ? { exkludera: exkl } : {}),
      extra: extra ?? utkastExtra([...utkast, p]),
    } as Parameters<typeof fastAt>[1]);
    this.fast = t ?? null;
    return t ? t.at : p;
  }

  // ------------------------------------------------------------------ pekarhändelser

  ror(verktyg: string, p: Punkt, e: LinjePekare): void {
    if (this.drag) {
      this.dra(p, e);
      return;
    }
    if (verktyg === "linje" || verktyg === "yta") {
      this.mark = this.berakna(p, e, this.utkast);
    } else {
      this.mark = null;
      this.fast = null;
      this.lasLinje = null;
      this.foljForhand = null;
    }
    this.signalera();
  }

  klick(verktyg: LinjeVerktyg, p: Punkt, e: LinjePekare): void {
    if (verktyg === "linje" || verktyg === "yta") return this.klickaRita(verktyg, p, e);
    const tol = TRAFF_PX * this.miljo.meterPerPx();
    const id = this.traffa(p, tol);
    if (verktyg === "klipp") return this.klippVid(p, tol);
    if (verktyg === "ihop") return this.ihopVid(id);
    if (!id) return this.miljo.meddela({ typ: "info", text: "Klicka på en linje eller yta." });
    this.markera(id);
    if (verktyg === "vand") this.vandVal();
    else this.forenklaVal();
  }

  private klickaRita(verktyg: "linje" | "yta", p: Punkt, e: LinjePekare): void {
    if (this.utkast.length === 0 && !this.lagerKontroll(verktyg)) return;
    const rack = fastRackvidd(e.pekare, this.miljo.meterPerPx());
    const forra = this.utkast[this.utkast.length - 1];
    const q = this.berakna(p, e, this.utkast);
    const foljde = this.foljForhand;
    const forsta = this.utkast[0];
    if (verktyg === "yta" && forsta && this.utkast.length >= 3 && avstand(forsta, q) <= rack) {
      this.avsluta(verktyg);
      return;
    }
    if (forra && avstand(forra, q) < Math.max(1e-6, rack * 0.3)) return; // andra klicket i ett dubbelklick
    if (this.utkast.length === 0) this.startFast = this.fast ?? undefined;
    if (foljde && foljde.length > 1) {
      this.utkast.push(...foljde.slice(1).map(kopiaP)); // följ kant: kantens egna hörn, exakt samma
    } else {
      this.utkast.push(q);
      this.sistaFastId = this.fast?.mal.id;
    }
    this.mark = this.utkast[this.utkast.length - 1] ?? null;
    this.foljForhand = null;
    this.miljo.meddela(null);
    this.signalera();
  }

  /** Pekaren lämnar kartan: förhandsvisningen (punkt, fäst, följ kant, hjälplinje) tas bort; utkastet och pågående drag behålls. */
  lamnar(): void {
    if (this.drag) return;
    this.mark = null;
    this.fast = null;
    this.lasLinje = null;
    this.foljForhand = null;
    this.signalera();
  }

  dubbelklick(verktyg: string): void {
    if (verktyg === "linje" || verktyg === "yta") this.avsluta(verktyg);
  }

  /** Lagret en ny linje eller yta hamnar i, eller ett besked om varför det inte går. */
  private lagerKontroll(verktyg: "linje" | "yta"): boolean {
    if (this.dok.lagerForRitning(verktyg === "yta" ? "polygon" : "linje")) return true;
    this.miljo.meddela({
      typ: "fel",
      text: `Det finns inget tänt och olåst lager för ${verktyg === "yta" ? "ytor" : "linjer"}. Lägg till eller tänd ett lager med rätt roll i lagerpanelen (mur och kant och passage är linjer; mark, hårdgjort, vegetation och vatten är ytor).`,
    });
    return false;
  }

  /** Avslutar utkastet: en linje behöver två punkter, en yta tre och inga korsande kanter. */
  avsluta(verktyg: "linje" | "yta"): boolean {
    const punkter = this.utkast.filter((p, i) => i === 0 || avstand(p, this.utkast[i - 1] as Punkt) > 1e-6);
    if (verktyg === "linje") {
      if (punkter.length < 2 || langd(punkter) < 0.05) {
        this.miljo.meddela({ typ: "fel", text: "En linje behöver minst två punkter. Klicka fler punkter, eller tryck Esc för att avbryta." });
        return false;
      }
    } else {
      const ring = punkter.length > 1 && avstand(punkter[0] as Punkt, punkter[punkter.length - 1] as Punkt) < 1e-6 ? punkter.slice(0, -1) : punkter;
      if (ring.length < 3) {
        this.miljo.meddela({ typ: "fel", text: "En yta behöver minst tre hörn. Klicka fler punkter, eller tryck Esc för att avbryta." });
        return false;
      }
      if (sjalvkorsande(ring)) {
        this.miljo.meddela({ typ: "fel", text: "Ytans kanter korsar varandra. Ta bort senaste punkten med Backsteg och rita om." });
        return false;
      }
      if (yta(ring) < 0.5) {
        this.miljo.meddela({ typ: "fel", text: "Ytan är för liten (under 0,5 m²)." });
        return false;
      }
      const slutring = [...ring]; // ring kan vara samma lista som punkter: töm den inte innan den kopierats
      punkter.length = 0;
      punkter.push(...slutring);
    }
    const lager = this.dok.lagerForRitning(verktyg === "yta" ? "polygon" : "linje");
    if (!lager) return this.lagerKontroll(verktyg);
    const f = this.dok.laggTillFigur(verktyg, punkter, lager.lager_id, forvalEgenskaper(lager.roll, lager.egenskaper_forval));
    this.rensaUtkast();
    this.val = f.id;
    this.valdPunkt = null;
    this.miljo.meddela({ typ: "info", text: "Ange egenskaperna i panelen." });
    this.miljo.figurSkapad?.(f.id);
    this.signalera();
    return true;
  }

  private rensaUtkast(): void {
    this.utkast = [];
    this.mark = null;
    this.lasLinje = null;
    this.foljForhand = null;
    this.startFast = undefined;
    this.sistaFastId = undefined;
  }

  avbryt(): void {
    this.ihopForsta = null;
    if (this.utkast.length === 0) return this.signalera();
    this.rensaUtkast();
    this.signalera();
  }

  /** Tar bort utkastets senaste punkt (Backsteg). */
  slappSenastePunkt(): void {
    if (this.utkast.length === 0) return;
    this.utkast.pop();
    if (this.utkast.length === 0) this.rensaUtkast();
    this.signalera();
  }

  // ------------------------------------------------------------------ träff och markering

  /** Figuren under punkten (senast ritade överst), eller undefined. Ytor träffas på kanten och inuti. */
  traffa(p: Punkt, tolerans: number): string | null {
    const lista = this.synliga().filter((f) => this.dok.arFigurRedigerbar(f));
    for (let i = lista.length - 1; i >= 0; i--) {
      const f = lista[i] as Figur;
      const s = narmastSida(f.punkter, p, tolerans, f.typ === "yta");
      if (s) return f.id;
    }
    for (let i = lista.length - 1; i >= 0; i--) {
      const f = lista[i] as Figur;
      if (f.typ === "yta" && inuti(p, f.punkter)) return f.id;
    }
    return null;
  }

  markera(id: string | null, punkt: number | null = null): void {
    this.val = id;
    this.valdPunkt = id ? punkt : null;
    this.ihopForsta = null;
    this.signalera();
  }

  /** Markerar figuren under punkten, och hörnet om man klickade på ett. Returnerar vad som markerades. */
  valjVid(p: Punkt, tolerans: number): string | null {
    const id = this.traffa(p, tolerans);
    const f = this.figur(id);
    const punkt = f ? (narmastPunkt(f.punkter, p, tolerans * 1.2) ?? null) : null;
    this.markera(id, punkt);
    return id;
  }

  // ------------------------------------------------------------------ klipp, slå ihop, vänd, förenkla

  private klippVid(p: Punkt, tol: number): void {
    const f = this.figur(this.traffa(p, tol));
    if (!f) return this.miljo.meddela({ typ: "info", text: "Klicka på en linje där den ska klippas." });
    if (f.typ !== "linje") return this.miljo.meddela({ typ: "info", text: "Bara linjer går att klippa. Rita ytan som en linje om du vill dela den." });
    const delar = klipp(f.punkter, p, tol);
    if (!delar) return this.miljo.meddela({ typ: "info", text: "Klicka inne på linjen, inte på en ändpunkt." });
    const ny = this.dok.delaFigur(f.id, delar[0], delar[1]);
    this.val = ny?.id ?? f.id;
    this.miljo.meddela({ typ: "info", text: "Linjen klipptes i två." });
    this.signalera();
  }

  private ihopVid(id: string | null): void {
    const f = this.figur(id);
    if (!f || f.typ !== "linje") return this.miljo.meddela({ typ: "info", text: "Klicka på två linjer i samma lager för att slå ihop dem." });
    const forsta = this.figur(this.ihopForsta);
    if (!forsta || forsta.id === f.id) {
      this.ihopForsta = f.id;
      this.val = f.id;
      this.miljo.meddela({ typ: "info", text: "Klicka på linjen att slå ihop med." });
      return this.signalera();
    }
    if (forsta.lager !== f.lager) {
      return this.miljo.meddela({ typ: "fel", text: "Linjerna ligger i olika lager och kan inte slås ihop." });
    }
    const ihop = slaIhop(forsta.punkter, f.punkter, 0.05, true);
    if (!ihop) return this.miljo.meddela({ typ: "fel", text: "Linjerna gick inte att slå ihop." });
    this.dok.slaIhopFigurer(forsta.id, f.id, ihop);
    this.ihopForsta = null;
    this.val = forsta.id;
    const glapp = avstand(forsta.punkter[forsta.punkter.length - 1] as Punkt, f.punkter[0] as Punkt);
    this.miljo.meddela({ typ: "info", text: glapp > 0.05 ? "Linjerna slogs ihop; ändarna sammanfaller inte, så en rak sträcka binder dem." : "Linjerna slogs ihop." });
    this.signalera();
  }

  vandVal(): void {
    const f = this.figur(this.val);
    if (!f) return this.miljo.meddela({ typ: "info", text: "Markera en linje eller yta först." });
    this.dok.sattFigurPunkter(f.id, vand(f.punkter));
    this.miljo.meddela({ typ: "info", text: "Riktningen vändes." });
  }

  forenklaVal(tolerans = FORENKLA_TOLERANS_M): void {
    const f = this.figur(this.val);
    if (!f) return this.miljo.meddela({ typ: "info", text: "Markera en linje eller yta först." });
    const ny = forenkla(f.punkter, tolerans, f.typ === "yta");
    if (!ny) return this.miljo.meddela({ typ: "info", text: "Det finns inga punkter att ta bort inom toleransen." });
    this.dok.sattFigurPunkter(f.id, ny);
    this.miljo.meddela({ typ: "info", text: `Förenklade från ${f.punkter.length} till ${ny.length} punkter.` });
  }

  /** Tar bort den markerade figuren (eller det markerade hörnet i Ta bort punkt). */
  taBortVal(): void {
    if (!this.val) return;
    this.dok.taBortFigur(this.val);
    this.markera(null);
  }

  /** Lägg till (`laggtill`) eller ta bort (`tabort`) en punkt på den markerade figuren. Returnerar true om något hände. */
  punktAtgard(lage: "laggtill" | "tabort", p: Punkt): boolean {
    const f = this.figur(this.val);
    if (!f) return false;
    const mpp = this.miljo.meterPerPx();
    const sluten = f.typ === "yta";
    if (lage === "laggtill") {
      const s = narmastSida(f.punkter, p, PUNKT_PX * mpp, sluten);
      if (!s) return false;
      const ny = infogaPunkt(f.punkter, s.index, s.t, sluten);
      if (ny) this.dok.sattFigurPunkter(f.id, ny);
      return ny !== undefined;
    }
    const i = narmastPunkt(f.punkter, p, PUNKT_PX * mpp);
    if (i === undefined) return false;
    const ny = taBortPunkt(f.punkter, i, sluten);
    if (!ny) {
      this.miljo.meddela({ typ: "info", text: sluten ? "En yta behöver minst tre hörn. Ta bort hela ytan i stället." : "En linje behöver minst två punkter. Ta bort hela linjen i stället." });
      return true;
    }
    if (sluten && sjalvkorsande(ny)) {
      this.miljo.meddela({ typ: "fel", text: "Ytans kanter skulle korsa varandra. Punkten togs inte bort." });
      return true;
    }
    this.dok.sattFigurPunkter(f.id, ny);
    return true;
  }

  /** Höjd (m RH 2000) på det markerade hörnet; tomt tar bort den. */
  sattPunktHojd(z: number | undefined): void {
    const f = this.figur(this.val);
    if (!f || this.valdPunkt === null) return;
    const ny = f.punkter.map((q, i) => {
      if (i !== this.valdPunkt) return kopiaP(q);
      const r: P3 = { x: q.x, y: q.y };
      if (z !== undefined) r.z = z;
      return r;
    });
    this.dok.sattFigurPunkter(f.id, ny);
  }

  // ------------------------------------------------------------------ drag

  /** Börjar dra ett hörn, en mittpunkt (blir ett nytt hörn) eller hela figuren. Returnerar true om kartan inte ska panorera. */
  ned(p: Punkt, e: LinjePekare): boolean {
    const f = this.figur(this.val);
    if (!f || e.ctrl || !this.dok.arFigurRedigerbar(f)) return false;
    const mpp = this.miljo.meterPerPx();
    const sluten = f.typ === "yta";
    const rad = sluten && inuti(p, f.punkter) ? begransadRackvidd(fastRackvidd(e.pekare, mpp), f.punkter) : fastRackvidd(e.pekare, mpp);
    const i = narmastPunkt(f.punkter, p, rad);
    if (i !== undefined) {
      this.markera(f.id, i);
      this.drag = { slag: "punkt", id: f.id, index: i, fore: f.punkter };
      return true;
    }
    const mitt = mittpunkter(f.punkter, sluten).find((m) => avstand(m.at, p) <= rad);
    if (mitt) {
      const ny = infogaPunkt(f.punkter, mitt.index, 0.5, sluten);
      if (ny) {
        this.drag = { slag: "punkt", id: f.id, index: mitt.index + 1, fore: f.punkter };
        this.dragFigur = { id: f.id, punkter: ny };
        this.valdPunkt = mitt.index + 1;
        this.signalera();
        return true;
      }
    }
    if (narmastSida(f.punkter, p, rad, sluten) || (sluten && inuti(p, f.punkter))) {
      let ankare = 0;
      let d = Infinity;
      f.punkter.forEach((q, k) => {
        const x = avstand(q, p);
        if (x < d) {
          d = x;
          ankare = k;
        }
      });
      this.drag = { slag: "figur", id: f.id, fore: f.punkter, start: p, ankare };
      return true;
    }
    return false;
  }

  private dra(p: Punkt, e: LinjePekare): void {
    const d = this.drag;
    if (!d) return;
    const self = new Set([`f:${d.id}`]);
    if (d.slag === "punkt") {
      const bas = this.dragFigur?.id === d.id ? this.dragFigur.punkter : d.fore;
      const utan = bas[d.index] as Punkt;
      const q = this.berakna(p, { ...e, ctrl: false, shift: false }, [], self, ringExtra(bas, utan));
      this.dragFigur = { id: d.id, punkter: flyttaPunkt(bas, d.index, q) };
    } else {
      const dx = p.x - d.start.x;
      const dy = p.y - d.start.y;
      let flyttad = d.fore.map((q) => ({ ...kopiaP(q), x: q.x + dx, y: q.y + dy }));
      const ankare = flyttad[d.ankare] as Punkt;
      const inst = this.inst();
      const t = inst.fastPa && !e.alt ? fastAt(this.fastIndex(), { punkt: ankare, rackvidd: fastRackvidd(e.pekare, this.miljo.meterPerPx()), exkludera: self }) : undefined;
      this.fast = t ?? null;
      if (t) {
        const ox = t.at.x - ankare.x;
        const oy = t.at.y - ankare.y;
        flyttad = flyttad.map((q) => ({ ...q, x: q.x + ox, y: q.y + oy }));
      }
      this.dragFigur = { id: d.id, punkter: flyttad };
    }
    this.signalera();
  }

  upp(): void {
    const d = this.drag;
    this.drag = null;
    const r = this.dragFigur;
    this.dragFigur = null;
    this.fast = null;
    if (d && r) {
      const orort = r.punkter.length === d.fore.length && r.punkter.every((q, i) => avstand(q, d.fore[i] as Punkt) < 1e-9);
      const f = this.figur(d.id);
      if (!orort && f) {
        if (f.typ === "yta" && sjalvkorsande(r.punkter)) this.miljo.meddela({ typ: "fel", text: "Ytans kanter skulle korsa varandra. Formen ändrades inte." });
        else {
          this.dok.sattFigurPunkter(d.id, r.punkter);
          this.miljo.meddela(null);
        }
      }
    }
    this.signalera();
  }

  get drar(): boolean {
    return this.drag !== null;
  }

  avbrytDrag(): void {
    this.drag = null;
    this.dragFigur = null;
    this.signalera();
  }

  /** Text vid pekaren medan man ritar: längd eller yta. */
  ritText(verktyg: string, format: (v: number) => string): string | null {
    if (!this.mark || this.utkast.length === 0) return null;
    const punkter = [...this.utkast, this.mark];
    if (verktyg === "linje") return `Längd ${format(langd(punkter))} m`;
    if (verktyg === "yta" && punkter.length >= 3) {
      const a = yta(punkter);
      return a >= 0.5 ? `Yta ${format(a)} m²` : null;
    }
    return null;
  }
}

/** Punkt i ring (jämn-udda-regeln). */
function inuti(p: Punkt, ring: readonly Punkt[]): boolean {
  let inne = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i] as Punkt;
    const b = ring[j] as Punkt;
    if (a.y > p.y !== b.y > p.y && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) inne = !inne;
  }
  return inne;
}
