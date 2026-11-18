const nf = new Intl.NumberFormat("pt-BR");
const nf1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export const int = (n: number): string => nf.format(Math.round(n));

export const pct = (n: number, digits = 1): string =>
  `${n.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;

/** 27049049 -> "27,0 mi"; com long=true -> "27,0 milhões". */
export function compact(n: number, long = false): string {
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${nf1.format(n / 1e9)} ${long ? "bilhões" : "bi"}`;
  if (abs >= 1e6) return `${nf1.format(n / 1e6)} ${long ? "milhões" : "mi"}`;
  if (abs >= 1e3) return `${nf1.format(n / 1e3)} mil`;
  return nf.format(n);
}

/** Numero com sinal explicito: 348635 -> "+348.635", -5 -> "-5". */
export function signed(n: number): string {
  return `${n > 0 ? "+" : n < 0 ? "-" : ""}${int(Math.abs(n))}`;
}

/** Como compact, com sinal: 348635 -> "+348,6 mil". */
export function signedCompact(n: number): string {
  return `${n > 0 ? "+" : n < 0 ? "-" : ""}${compact(Math.abs(n))}`;
}

/** Diferenca em pontos percentuais com sinal: 0.04 -> "+0,04 p.p.". */
export function signedPp(n: number): string {
  const text = Math.abs(n).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${n > 0 ? "+" : n < 0 ? "-" : ""}${text} p.p.`;
}

export function brl(n: number): string {
  return `R$ ${nf.format(Math.round(n))}`;
}

/** Bytes em unidades binarias legiveis: 5621242597 -> "5,2 GiB". */
export function bytes(n: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${i === 0 ? nf.format(value) : nf1.format(value)} ${units[i]}`;
}

export function seconds(n: number): string {
  if (n < 60) return `${nf1.format(n)} s`;
  const minutes = Math.floor(n / 60);
  const rest = Math.round(n - minutes * 60);
  return `${minutes} min ${rest} s`;
}

const MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

/** "2026-10" -> "out/2026". */
export function monthLabel(value: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) return value;
  const name = MONTHS[Number(match[2]) - 1];
  return name ? `${name}/${match[1]}` : value;
}

export const NO_UF = "Sem UF informada";

export function ufLabel(uf: string | null | undefined): string {
  return uf ? uf : NO_UF;
}

export function textOrDash(value: string | null | undefined): string {
  return value ? value : "Não informado";
}

/** Capitaliza nomes em caixa alta ("SAO PAULO" -> "Sao Paulo"), mantendo preposicoes. */
export function titleCase(value: string): string {
  const small = new Set(["de", "da", "do", "das", "dos", "e"]);
  return value
    .toLowerCase()
    .split(" ")
    .map((word, i) => (i > 0 && small.has(word) ? word : word.charAt(0).toUpperCase() + word.slice(1)))
    .join(" ");
}
