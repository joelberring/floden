/*
 * Formredigering av ett hus: flytta, infoga och ta bort hörn, flytta hela huset.
 *
 * Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af, packages/map-revision/src/edit.ts
 * (moveVertex, insertVertex, deleteVertex, insertPoints) samt apps/web/src/lib/map-project-point-edit.ts
 * (editPointAt: ADD, REMOVE, EITHER) och map-project-draw.ts (nearestVertex, translateObject)).
 * O-Tid har linjer, kurvor och ringar i kartans mm; här finns bara raka ringar i SWEREF-meter,
 * så kurvgrenarna är borta. Minsta antal hörn är tre. Rena funktioner som ger nya ringar.
 */
import { avstand, projicera, type Punkt, type Ring } from "./geom.ts";

export interface HornAdress {
  index: number;
}

/** Hörnet `index` flyttat till `till`. */
export function flyttaHorn(ring: Ring, index: number, till: Punkt): Ring {
  if (index < 0 || index >= ring.length) return ring;
  return ring.map((p, i) => (i === index ? { x: till.x, y: till.y } : p));
}

/** Ett nytt hörn efter hörnet `index` (på sträckan till nästa). */
export function infogaHorn(ring: Ring, index: number, at: Punkt): Ring | undefined {
  if (index < 0 || index >= ring.length) return undefined;
  return [...ring.slice(0, index + 1), { x: at.x, y: at.y }, ...ring.slice(index + 1)];
}

/** Hörnet borttaget. Ett hus med tre hörn går inte att minska (ta bort huset i stället). */
export function taBortHorn(ring: Ring, index: number): Ring | undefined {
  if (ring.length <= 3 || index < 0 || index >= ring.length) return undefined;
  return ring.filter((_, i) => i !== index);
}

/** Hela ringen flyttad `dx`, `dy` meter. */
export function flyttaRing(ring: Ring, dx: number, dy: number): Ring {
  return ring.map((p) => ({ x: p.x + dx, y: p.y + dy }));
}

/** Sträckornas mitt, där ett nytt hörn kan läggas in (med hörnet före). */
export function infogaPunkter(ring: Ring): Array<{ index: number; at: Punkt }> {
  return ring.map((a, i) => {
    const b = ring[(i + 1) % ring.length] as Punkt;
    return { index: i, at: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
  });
}

/** Närmaste hörn inom `tolerans` (m), eller undefined. */
export function narmastHorn(ring: Ring, p: Punkt, tolerans: number): HornAdress | undefined {
  let basta: HornAdress | undefined;
  let d = tolerans;
  ring.forEach((q, i) => {
    const x = avstand(q, p);
    if (x <= d) {
      basta = { index: i };
      d = x;
    }
  });
  return basta;
}

/** Närmaste sträcka inom `tolerans` (m): sträckans början, läget (0-1) och punkten på den. */
export function narmastStracka(ring: Ring, p: Punkt, tolerans: number): { index: number; t: number; at: Punkt } | undefined {
  let basta: { index: number; t: number; at: Punkt } | undefined;
  let d = tolerans;
  ring.forEach((a, i) => {
    const b = ring[(i + 1) % ring.length] as Punkt;
    const pr = projicera(p, a, b);
    if (pr.distance <= d) {
      basta = { index: i, t: pr.t, at: pr.at };
      d = pr.distance;
    }
  });
  return basta;
}

/**
 * Ett klick som ändrar hörnen: `TA_BORT` tar bort hörnet under pekaren, `LAGG_TILL` lägger en
 * ny punkt på sträckan under pekaren, `ENDERA` (Ctrl/Cmd-klick) tar bort om pekaren står på ett
 * hörn och lägger annars till. Svaret är den nya ringen och hörnets index, eller undefined
 * när klicket inte träffade.
 */
export function andraHornVid(ring: Ring, p: Punkt, tolerans: number, lage: "LAGG_TILL" | "TA_BORT" | "ENDERA"): { ring: Ring; index?: number } | undefined {
  const horn = lage === "LAGG_TILL" ? undefined : narmastHorn(ring, p, tolerans);
  if (horn) {
    const ny = taBortHorn(ring, horn.index);
    return ny ? { ring: ny } : undefined;
  }
  if (lage === "TA_BORT") return undefined;
  const s = narmastStracka(ring, p, tolerans);
  if (!s) return undefined;
  const ny = infogaHorn(ring, s.index, s.at);
  return ny ? { ring: ny, index: s.index + 1 } : undefined;
}
