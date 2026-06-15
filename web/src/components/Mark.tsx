// The AEGIS mark — shield + `>_` terminal prompt, gold→green gradient.
// Shared by the titlebar, the desktop shell rail, and anywhere else that needs
// the inline logo (no asset request, scales crisply at any size).

export function Mark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 256 256" aria-hidden>
      <defs>
        <linearGradient id="aegis-mark" x1="78" y1="56" x2="178" y2="208" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#f2c878" />
          <stop offset="0.55" stopColor="#d8913f" />
          <stop offset="1" stopColor="#7ecf8f" />
        </linearGradient>
      </defs>
      <path d="M128 50 196 76 V128 C196 168 166 196 128 210 C90 196 60 168 60 128 V76 Z" fill="url(#aegis-mark)" />
      <path d="M104 104 L128 128 L104 152" fill="none" stroke="#14100a" strokeWidth="13" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M134 152 H158" fill="none" stroke="#14100a" strokeWidth="13" strokeLinecap="round" />
    </svg>
  );
}
