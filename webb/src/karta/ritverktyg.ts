/*
 * Ritverktygen: nytt hus, välj hus, lägg till och ta bort hörn, flytta hörn och hus, fäst, rät
 * vinkel, kortkommandon och ångra/gör om. Styrenhet utan webbläsare: kartan skickar händelser
 * (position i SWEREF-meter, pekartyp, tangenter) och läser tillståndet för att rita. Då går
 * hela interaktionen att prova i test.
 *
 * Interaktionsmönstret och kortkommandona är porterade från O-Tid (MIT, joelberring/otid @
 * 4a446af): verktygsrad med Markera (Q), Rita (W), Lägg till punkt (A), Ta bort punkt (S), Fäst
 * på/av (T), Ångra (Z, Ctrl/Cmd+Z), Gör om (Shift+Z, Ctrl/Cmd+Y), Esc avbryter, Enter avslutar,
 * Backsteg tar bort senaste punkten, Delete tar bort det markerade; Ctrl/Cmd+klick i Markera lägger
 * till eller tar bort en punkt (map-project-shortcuts, map-project-point-edit); vinkellås med
 * Ctrl/Cmd (map-project-draw-assist). Fäst och träff är snap.ts och hit.ts.
 *
 * Kontrollen av det ritade (giltig form, höjd, utbredning) görs av servern med kärnans kod. Här
 * avstår klienten bara från att skapa en uppenbart självkorsande form och visar skissens yta.
 */
import { andraHornVid, flyttaHorn, flyttaRing, infogaHorn, infogaPunkter, narmastHorn } from "./formedit.ts";
import { avstand, btaUppskattning, sjalvkorsande, yta, type Punkt, type Ring } from "./geom.ts";
import { traffAvstand, traffTest, type HusForm } from "./hit.ts";
import type { PlanDok } from "./plandok.ts";
import { formatera } from "../tal.ts";
import { byggFastIndex, fastAt, fastPaStrale, fastRackvidd, lasVinkel, ringExtra, utkastExtra, vinkelRef, type FastIndex, type FastTraff } from "./snap.ts";

export type Verktyg = "valj" | "hus" | "laggtill" | "tabort";

/** Förvald höjd för ett nytt hus (m) tills man anger en annan i höjdpanelen. */
export const FORVALD_HOJD_M = 12;

export interface BasByggnad {
  id: string;
  ring: Ring;
  hal: Ring[];
  hojd_m: number;
}

export interface Val {
  id: string;
  /** Nytt hus (ritat) eller befintlig byggnad. */
  ny: boolean;
}

export interface Pekare {
  pekare: string;
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
}

export interface Meddelande {
  typ: "fel" | "info";
  text: string;
}

export interface Tangent {
  key: string;
  code: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
}

export interface Miljo {
  meterPerPx(): number;
  meddela(m: Meddelande | null): void;
  /** Ett nytt hus har ritats klart (för att öppna höjdpanelen). */
  husSkapat?(id: string): void;
}

/** Tangenterna (som O-Tids förval); visas i verktygsraden. */
export const TANGENTER = {
  valj: "Q",
  hus: "W",
  laggtill: "A",
  tabort: "S",
  fast: "T",
  ratVinkel: "R",
  angra: "Z",
  gora_om: "Shift+Z",
} as const;

const ATT_TRAFFA_PX = 6;
const HORN_PX = 9;

type Drag = { slag: "horn"; id: string; index: number; fore: Ring } | { slag: "hus"; id: string; fore: Ring; start: Punkt; ankare: number };

export class Ritverktyg {
  verktyg: Verktyg = "valj";
  fastPa = true;
  ratVinkel = false;
  val: Val | null = null;
  utkast: Punkt[] = [];
  /** Pekarens punkt efter fäst och vinkellås (för förhandsvisning och nästa klick). */
  mark: Punkt | null = null;
  fast: FastTraff | null = null;
  /** Referenslinje för vinkellåset (från sista punkten i utkastet). */
  lasLinje: [Punkt, Punkt] | null = null;
  /** Form som visas under drag (ännu inte sparad). */
  dragRing: { id: string; ring: Ring } | null = null;
  private drag: Drag | null = null;
  private startFast: FastTraff | undefined;
  private index: FastIndex | undefined;
  private indexNr = -1;
  private readonly dok: PlanDok;
  private readonly bas: Map<string, BasByggnad>;
  private readonly miljo: Miljo;
  private lyssnare: Array<() => void> = [];

  constructor(dok: PlanDok, bas: readonly BasByggnad[], miljo: Miljo) {
    this.dok = dok;
    this.bas = new Map(bas.map((b) => [b.id, b]));
    this.miljo = miljo;
    dok.vidAndring(() => this.andrad());
  }

  vidAndring(f: () => void): void {
    this.lyssnare.push(f);
  }

  private andrad(): void {
    if (this.val && !this.finns(this.val)) this.val = null;
    this.signalera();
  }

  private signalera(): void {
    for (const f of this.lyssnare) f();
  }

  private finns(v: Val): boolean {
    if (!v.ny) return this.bas.has(v.id);
    const h = this.dok.ny.get(v.id);
    return h !== undefined && this.dok.arRedigerbart(h); // ett hus i ett dolt eller låst lager går inte att ha markerat
  }

  // ------------------------------------------------------------------ tillstånd

  sattVerktyg(v: Verktyg): void {
    if (v === this.verktyg) return;
    this.avbrytUtkast();
    this.drag = null;
    this.dragRing = null;
    this.verktyg = v;
    this.fast = null;
    this.mark = null;
    this.miljo.meddela(null);
    this.signalera();
  }

  vaxlaFast(): void {
    this.fastPa = !this.fastPa;
    if (!this.fastPa) this.fast = null;
    this.signalera();
  }

  vaxlaRatVinkel(): void {
    this.ratVinkel = !this.ratVinkel;
    this.signalera();
  }

  markera(v: Val | null): void {
    this.val = v;
    this.signalera();
  }

  /** Husets nuvarande ring (under drag den dragna formen). */
  ringAv(v: Val): Ring | null {
    if (this.dragRing && this.dragRing.id === v.id && v.ny) return this.dragRing.ring;
    if (v.ny) return this.dok.ny.get(v.id)?.ring ?? null;
    return this.bas.get(v.id)?.ring ?? null;
  }

  /** Husets nuvarande höjd (m): ändrad, annars basens; nya hus har sin egen. */
  hojdAv(v: Val): number | null {
    if (v.ny) return this.dok.ny.get(v.id)?.hojd_m ?? null;
    return this.dok.hojd.get(v.id) ?? this.bas.get(v.id)?.hojd_m ?? null;
  }

  /** Skissens yta och BTA-uppskattning för ett nytt hus (inget lösarresultat). */
  mattAv(ring: readonly Punkt[], hojd: number): { yta_m2: number; bta_m2: number } {
    const a = yta(ring);
    return { yta_m2: a, bta_m2: btaUppskattning(a, hojd) };
  }

  /** Text vid pekaren medan man ritar: ytan med den här punkten som sista hörn. */
  ritText(): string | null {
    if (this.verktyg !== "hus" || this.utkast.length < 2 || !this.mark) return null;
    const ring = [...this.utkast, this.mark];
    const a = yta(ring);
    if (a < 0.5) return null;
    const m = this.mattAv(ring, FORVALD_HOJD_M);
    return `Yta ${fmt(m.yta_m2)} m²`;
  }

  /** Befintliga byggnader och nya hus som träffbara former, nya sist (överst). */
  private husFormer(): HusForm[] {
    const ut: HusForm[] = [];
    for (const b of this.bas.values()) ut.push({ id: `b:${b.id}`, ringar: [b.ring, ...b.hal] });
    for (const h of this.dok.ny.values()) if (this.dok.arRedigerbart(h)) ut.push({ id: `n:${h.id}`, ringar: [h.ring] });
    return ut;
  }

  private fastIndex(): FastIndex {
    if (this.index && this.indexNr === this.dok.andringsNr) return this.index;
    const objekt: Array<{ id: string; ringar: Ring[] }> = [];
    for (const b of this.bas.values()) objekt.push({ id: `b:${b.id}`, ringar: [b.ring, ...b.hal] });
    for (const h of this.dok.ny.values()) if (this.dok.arSynligt(h)) objekt.push({ id: `n:${h.id}`, ringar: [h.ring] }); // låsta lager går att fästa mot, dolda inte
    this.index = byggFastIndex(objekt);
    this.indexNr = this.dok.andringsNr;
    return this.index;
  }

  // ------------------------------------------------------------------ fäst och vinkel

  /** Punkten efter fäst och vinkellås. Uppdaterar `fast` och `lasLinje`. */
  private beräkna(p: Punkt, e: Pekare, exkl?: ReadonlySet<string>, extra?: ReturnType<typeof ringExtra>): Punkt {
    const rack = fastRackvidd(e.pekare, this.miljo.meterPerPx());
    this.lasLinje = null;
    const last = this.verktyg === "hus" ? this.utkast[this.utkast.length - 1] : undefined;
    const lasa = this.verktyg === "hus" && (e.ctrl ? !this.ratVinkel : this.ratVinkel);
    if (lasa && last) {
      const ref = vinkelRef(this.utkast, this.startFast);
      const l = lasVinkel(last, p, ref);
      this.lasLinje = [last, { x: last.x + l.dir.x * 1e4, y: last.y + l.dir.y * 1e4 }];
      if (this.fastPa) {
        const t = fastPaStrale(this.fastIndex(), { origin: last, dir: l.dir, nara: l.at, rackvidd: rack, extra: utkastExtra([...this.utkast, l.at]) });
        if (t) {
          this.fast = t;
          return t.at;
        }
      }
      this.fast = null;
      return l.at;
    }
    if (!this.fastPa || e.alt) {
      this.fast = null;
      return p;
    }
    const t = fastAt(this.fastIndex(), {
      punkt: p,
      rackvidd: rack,
      ...(exkl ? { exkludera: exkl } : {}),
      extra: extra ?? (this.verktyg === "hus" ? utkastExtra([...this.utkast, p]) : undefined),
    } as Parameters<typeof fastAt>[1]);
    this.fast = t ?? null;
    return t ? t.at : p;
  }

  // ------------------------------------------------------------------ pekarhändelser

  ror(p: Punkt, e: Pekare): void {
    if (this.drag) {
      this.dra(p, e);
      return;
    }
    if (this.verktyg === "hus") {
      this.mark = this.beräkna(p, e);
    } else {
      this.mark = null;
      this.fast = null;
      this.lasLinje = null;
    }
    this.signalera();
  }

  klick(p: Punkt, e: Pekare): void {
    if (this.verktyg === "hus") {
      this.klickaHus(p, e);
      return;
    }
    const tol = ATT_TRAFFA_PX * this.miljo.meterPerPx();
    const mpp = this.miljo.meterPerPx();
    const valdRing = this.val && this.val.ny ? this.ringAv(this.val) : null;
    if (this.verktyg === "laggtill" || this.verktyg === "tabort" || (this.verktyg === "valj" && e.ctrl)) {
      if (this.val?.ny && valdRing) {
        const lage = this.verktyg === "laggtill" ? "LAGG_TILL" : this.verktyg === "tabort" ? "TA_BORT" : "ENDERA";
        const r = andraHornVid(valdRing, p, HORN_PX * mpp, lage);
        if (r) {
          if (sjalvkorsande(r.ring)) this.miljo.meddela({ typ: "fel", text: "Husets kanter skulle korsa varandra. Hörnet ändrades inte." });
          else {
            this.dok.sattRing(this.val.id, r.ring);
            this.miljo.meddela(null);
          }
          return;
        }
        if (this.verktyg === "tabort" && narmastHorn(valdRing, p, HORN_PX * mpp)) {
          this.miljo.meddela({ typ: "info", text: "Ett hus behöver minst tre hörn. Ta bort hela huset i stället." });
          return;
        }
      }
      if (this.verktyg !== "valj" && !this.val?.ny) {
        this.miljo.meddela({ typ: "info", text: "Markera först ett nytt hus du ritat." });
      }
    }
    this.valjVid(p, tol);
  }

  /** Markerar huset under punkten (nya hus före befintliga). Returnerar vad som markerades. */
  valjVid(p: Punkt, tolerans: number): Val | null {
    const traffar = traffTest(this.husFormer(), p, tolerans);
    const forsta = traffar[0];
    if (!forsta) {
      this.val = null;
      this.signalera();
      return null;
    }
    const ny = forsta.id.startsWith("n:");
    this.val = { id: forsta.id.slice(2), ny };
    this.miljo.meddela(null);
    this.signalera();
    return this.val;
  }

  /** Ritlagret ett nytt hus hamnar i, eller ett besked om varför det inte går (inget tänt och olåst byggnadslager valt). */
  private husLagerKontroll(): boolean {
    if (this.dok.aktivtHusLager() || this.dok.lager.length === 0) return true;
    const l = this.dok.lagerAv(this.dok.aktivtLager);
    const skal = !l
      ? "Det finns inget byggnadslager. Lägg till ett lager med rollen Byggnad i lagerpanelen."
      : l.roll !== "byggnad"
        ? `Lagret ${l.namn} har rollen ${l.roll}. Hus ritas i ett lager med rollen Byggnad; välj ett sådant lager i lagerpanelen. Ritverktyg för linjer och ytor kommer i ett senare steg.`
        : `Lagret ${l.namn} är ${l.last ? "låst" : "släckt"}. Lås upp eller tänd det i lagerpanelen, eller välj ett annat lager.`;
    this.miljo.meddela({ typ: "fel", text: skal });
    return false;
  }

  private klickaHus(p: Punkt, e: Pekare): void {
    if (this.utkast.length === 0 && !this.husLagerKontroll()) return;
    const rack = fastRackvidd(e.pekare, this.miljo.meterPerPx());
    const q = this.beräkna(p, e);
    const forsta = this.utkast[0];
    if (forsta && this.utkast.length >= 3 && avstand(forsta, q) <= rack) {
      this.avsluta();
      return;
    }
    const sista = this.utkast[this.utkast.length - 1];
    if (sista && avstand(sista, q) < Math.max(1e-6, rack * 0.3)) return; // andra klicket i ett dubbelklick
    if (this.utkast.length === 0) this.startFast = this.fast ?? undefined;
    this.utkast.push(q);
    this.mark = q;
    this.miljo.meddela(null);
    this.signalera();
  }

  dubbelklick(): void {
    if (this.verktyg === "hus") this.avsluta();
  }

  /** Avslutar utkastet: minst tre hörn och ingen korsning, annars ett tydligt besked och man ritar vidare. */
  avsluta(): boolean {
    const ring = this.utkast.filter((p, i) => i === 0 || avstand(p, this.utkast[i - 1] as Punkt) > 1e-6);
    if (ring.length < 3) {
      this.miljo.meddela({ typ: "fel", text: "Ett hus behöver minst tre hörn. Klicka fler punkter, eller tryck Esc för att avbryta." });
      return false;
    }
    if (sjalvkorsande(ring)) {
      this.miljo.meddela({ typ: "fel", text: "Husets kanter korsar varandra. Ta bort senaste punkten med Backsteg och rita om." });
      return false;
    }
    if (yta(ring) < 1) {
      this.miljo.meddela({ typ: "fel", text: "Huset är för litet (under 1 m²)." });
      return false;
    }
    if (!this.husLagerKontroll()) return false;
    const h = this.dok.laggTillHus(ring, FORVALD_HOJD_M);
    this.utkast = [];
    this.mark = null;
    this.lasLinje = null;
    this.startFast = undefined;
    this.verktyg = "valj";
    this.val = { id: h.id, ny: true };
    this.miljo.meddela({ typ: "info", text: "Ange husets höjd i panelen." });
    this.miljo.husSkapat?.(h.id);
    this.signalera();
    return true;
  }

  avbrytUtkast(): void {
    if (this.utkast.length === 0) return;
    this.utkast = [];
    this.mark = null;
    this.lasLinje = null;
    this.startFast = undefined;
    this.signalera();
  }

  /** Tar bort utkastets senaste punkt (Backsteg). */
  slappSenastePunkt(): void {
    if (this.utkast.length === 0) return;
    this.utkast.pop();
    if (this.utkast.length === 0) this.startFast = undefined;
    this.signalera();
  }

  // ------------------------------------------------------------------ drag

  /** Börjar dra ett hörn eller huset. Returnerar true om kartan inte ska panorera. */
  ned(p: Punkt, e: Pekare): boolean {
    if (this.verktyg !== "valj" || !this.val?.ny || e.ctrl) return false;
    const ring = this.ringAv(this.val);
    if (!ring) return false;
    const mpp = this.miljo.meterPerPx();
    const horn = narmastHorn(ring, p, HORN_PX * mpp);
    if (horn) {
      this.drag = { slag: "horn", id: this.val.id, index: horn.index, fore: ring };
      return true;
    }
    const mitt = infogaPunkter(ring).find((m) => avstand(m.at, p) <= HORN_PX * mpp);
    if (mitt) {
      const ny = infogaHorn(ring, mitt.index, mitt.at);
      if (ny) {
        this.drag = { slag: "horn", id: this.val.id, index: mitt.index + 1, fore: ring };
        this.dragRing = { id: this.val.id, ring: ny };
        this.signalera();
        return true;
      }
    }
    if (traffAvstand({ id: this.val.id, ringar: [ring] }, p, 0)) {
      let ankare = 0;
      let d = Infinity;
      ring.forEach((q, i) => {
        const x = avstand(q, p);
        if (x < d) {
          d = x;
          ankare = i;
        }
      });
      this.drag = { slag: "hus", id: this.val.id, fore: ring, start: p, ankare };
      return true;
    }
    return false;
  }

  private dra(p: Punkt, e: Pekare): void {
    const d = this.drag;
    if (!d) return;
    const self = new Set([`n:${d.id}`]);
    if (d.slag === "horn") {
      const bas = this.dragRing?.id === d.id ? this.dragRing.ring : d.fore;
      const utan = bas[d.index] as Punkt;
      const q = this.beräkna(p, { ...e, ctrl: false }, self, ringExtra(bas, utan));
      this.dragRing = { id: d.id, ring: flyttaHorn(bas, d.index, q) };
    } else {
      const dx = p.x - d.start.x;
      const dy = p.y - d.start.y;
      let flyttad = flyttaRing(d.fore, dx, dy);
      const ankare = flyttad[d.ankare] as Punkt;
      const t = this.fastPa && !e.alt ? fastAt(this.fastIndex(), { punkt: ankare, rackvidd: fastRackvidd(e.pekare, this.miljo.meterPerPx()), exkludera: self }) : undefined;
      this.fast = t ?? null;
      if (t) flyttad = flyttaRing(flyttad, t.at.x - ankare.x, t.at.y - ankare.y);
      this.dragRing = { id: d.id, ring: flyttad };
    }
    this.signalera();
  }

  upp(): void {
    const d = this.drag;
    this.drag = null;
    const r = this.dragRing;
    this.dragRing = null;
    this.fast = null;
    if (!d || !r) {
      this.signalera();
      return;
    }
    const orort = r.ring.length === d.fore.length && r.ring.every((q, i) => avstand(q, d.fore[i] as Punkt) < 1e-9);
    if (!orort) {
      if (sjalvkorsande(r.ring)) this.miljo.meddela({ typ: "fel", text: "Husets kanter skulle korsa varandra. Formen ändrades inte." });
      else {
        this.dok.sattRing(d.id, r.ring);
        this.miljo.meddela(null);
      }
    }
    this.signalera();
  }

  /** Pågår ett drag av ett hörn eller hus? */
  get drar(): boolean {
    return this.drag !== null;
  }

  // ------------------------------------------------------------------ åtgärder

  /** Tar bort det markerade: ett nytt hus tas bort, en befintlig byggnad rivs (eller rivningen ångras). */
  taBortVal(): void {
    const v = this.val;
    if (!v) return;
    if (v.ny) {
      this.dok.taBortNyttHus(v.id);
      this.val = null;
    } else {
      this.dok.sattBort(v.id, !this.dok.bort.has(v.id));
    }
    this.signalera();
  }

  /** Ändrar höjden på det markerade huset (m). Servern kontrollerar gränserna. */
  sattHojd(hojd: number): void {
    if (this.val) this.dok.sattHojd(this.val.id, hojd, this.val.ny);
  }

  angra(): void {
    this.dok.angra();
  }

  goraOm(): void {
    this.dok.goraOm();
  }

  // ------------------------------------------------------------------ tangenter

  /** Hanterar en tangent. Returnerar true om den användes (anroparen gör preventDefault). */
  tangent(t: Tangent): boolean {
    const kommando = t.ctrlKey || t.metaKey;
    const k = t.key.toLowerCase();
    if (kommando && k === "z") {
      if (t.shiftKey) this.goraOm();
      else this.angra();
      return true;
    }
    if (kommando && k === "y") {
      this.goraOm();
      return true;
    }
    if (kommando || t.altKey) return false;
    if (t.key === "Escape") {
      if (this.utkast.length > 0) this.avbrytUtkast();
      else if (this.drag) {
        this.drag = null;
        this.dragRing = null;
        this.signalera();
      } else if (this.verktyg !== "valj") this.sattVerktyg("valj");
      else this.markera(null);
      return true;
    }
    if (t.key === "Enter" && this.utkast.length > 0) {
      this.avsluta();
      return true;
    }
    if (t.key === "Backspace" && this.utkast.length > 0) {
      this.slappSenastePunkt();
      return true;
    }
    if ((t.key === "Delete" || t.key === "Backspace") && this.val && this.utkast.length === 0) {
      this.taBortVal();
      return true;
    }
    const nyckel = t.shiftKey ? `Shift+${k.toUpperCase()}` : k.toUpperCase();
    switch (nyckel) {
      case "Q":
        this.sattVerktyg("valj");
        return true;
      case "W":
        this.sattVerktyg("hus");
        return true;
      case "A":
        this.sattVerktyg("laggtill");
        return true;
      case "S":
        this.sattVerktyg("tabort");
        return true;
      case "T":
        this.vaxlaFast();
        return true;
      case "R":
        this.vaxlaRatVinkel();
        return true;
      case "Z":
        this.angra();
        return true;
      case "Shift+Z":
        this.goraOm();
        return true;
      default:
        return false;
    }
  }
}

function fmt(v: number): string {
  return formatera(Math.round(v), 0);
}
