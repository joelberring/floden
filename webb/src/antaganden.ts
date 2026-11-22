/** Webbens rader för takvatten och avtappning under "Antaganden" (lösarens egna texter). */
import type { VattenAntagande } from "./fragor-typer.ts";

function esc(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** En rad per olik text, i den ordning analyserna kom; inga tal räknas här. */
export function vattenAntagandenRader(v: VattenAntagande[]): string[] {
  const texter: string[] = [];
  for (const p of v) {
    for (const t of [p.takvatten_text, p.avtappning_text]) {
      if (t && !texter.includes(t)) texter.push(t);
    }
  }
  return texter.map((t) => `<li><span class="etikett-liten">Antagande</span> <span>${esc(t)}</span></li>`);
}
