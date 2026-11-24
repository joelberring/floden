/* Formen på src/matematik/data.generated.ts (genereras av tools/generera_matematik.py). */
export interface MatematikLank {
  text: string;
  /** Länk till koden på GitHub; null när posten inte är en sökväg. */
  href: string | null;
}

export type FlodeNiva = "flode" | "api" | "cli" | "fristaende";

export interface MatematikMetod {
  id: string;
  namn: string;
  sammanfattning: string;
  /** Formlerna som läsbar text (LaTeX finns i docs/MATEMATIK.md). */
  formler: string[];
  flode: FlodeNiva;
  flodeText: string;
  /** Klassningen ur data/mcp/metodstatus.toml: skiss eller rapport. */
  status: string;
  kod: MatematikLank[];
  kontrakt: string[];
  facit: MatematikLank[];
  anmarkning: string;
}

export interface MatematikDoman {
  id: string;
  namn: string;
  metoder: MatematikMetod[];
}

export interface MatematikFraga {
  id: string;
  titel: string;
  kort: string;
  underlag: MatematikLank[];
}

export interface MatematikData {
  doman: MatematikDoman[];
  fragor: MatematikFraga[];
  flodeText: Record<string, string>;
  /** Adressen till docs/MATEMATIK.md på GitHub. */
  kodbas: string;
}
