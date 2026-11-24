/* Felklasser för API-anrop (egen fil utan beroenden så att ren logik och tester kan importera dem). */

export class OinloggadFel extends Error {
  constructor() {
    super("Inloggning krävs.");
    this.name = "OinloggadFel";
  }
}

/** Fel från API:t med HTTP-status (0 = ingen kontakt med servern). Meddelandet är serverns svenska text. */
export class ApiFel extends Error {
  readonly status: number;
  constructor(meddelande: string, status: number) {
    super(meddelande);
    this.name = "ApiFel";
    this.status = status;
  }
}
