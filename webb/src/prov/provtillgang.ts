/*
 * Vem får se provsidan? Servern avgör (403 från /api/prov/*); klienten döljer bara sidan.
 * Svaret från `GET /konto/jag` tolkas här: lokalt med inloggning av är sidan öppen, annars krävs
 * superadmin. Klienten litar aldrig på detta för säkerhet.
 */
export type ProvTillgang = "tillaten" | "forbjuden" | "ej-inloggad" | "okand";

export function provTillgang(status: number, data: unknown): ProvTillgang {
  if (status === 401) return "ej-inloggad";
  const d = (data ?? {}) as { inloggning?: string; konto?: { superadmin?: boolean } };
  if (status === 200 && d.inloggning === "av") return "tillaten";
  if (status === 200 && d.inloggning === "pa") {
    return d.konto?.superadmin === true ? "tillaten" : "forbjuden";
  }
  return "okand";
}

export const TILLGANG_TEXT: Record<Exclude<ProvTillgang, "tillaten">, string> = {
  forbjuden: "Provsidan är bara för superadmin.",
  "ej-inloggad": "Logga in för att se provsidan. Den är bara för superadmin.",
  okand: "Det gick inte att avgöra om du får se provsidan. Servern svarar inte som väntat.",
};
