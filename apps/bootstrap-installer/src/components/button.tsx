export function buttonClass(kind: "primary" | "ghost" = "primary"): string {
  return kind === "primary" ? "button button-primary" : "button button-ghost";
}

export function button(label: string, id: string, kind: "primary" | "ghost" = "primary"): string {
  return `<button id="${id}" class="${buttonClass(kind)}" type="button">${label}</button>`;
}
