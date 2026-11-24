/* Vad för slags projekt kartverktyget visar: demokvarteret eller ett projekt i en geografi. Ren text, inga beroenden. */
import type { ProjektInfo } from "../karta/kartapi.ts";

type Info = Pick<ProjektInfo, "slag" | "geografi" | "avgransning_3011" | "avgransning_polygon_3011" | "oppen" | "licens">;

/**
 * En kort rad om projektets underlag för steg 2 och 3, eller null för demokvarteret (som har sin egen text).
 * Säger geografin, avgränsningen (polygon eller rektangel, mått i meter) och om underlaget är öppna data.
 */
export function projektKallaText(info: Info): string | null {
  if (info.slag !== "geografi") return null;
  const a = info.avgransning_3011;
  const mått = a && a.length === 4 ? ` ${Math.round((a[2] ?? 0) - (a[0] ?? 0))} × ${Math.round((a[3] ?? 0) - (a[1] ?? 0))} m` : "";
  const form = info.avgransning_polygon_3011 ? "polygon" : "rektangel";
  const oppet = info.oppen
    ? "Underlaget är öppna data."
    : `Underlaget är inte öppna data${info.licens ? ` (licens: ${info.licens})` : ""}: det visas för projektets medlemmar men lämnas inte ut via MCP.`;
  return `Projekt i geografin ${info.geografi ?? "okänd"}, avgränsning (${form}${mått ? "," + mått : ""}). ${oppet}`;
}
