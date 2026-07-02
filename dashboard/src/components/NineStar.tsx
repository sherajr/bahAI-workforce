// Nine-pointed star — the mark of the workforce.
export function NineStar({ size = 28, className = "" }: { size?: number; className?: string }) {
  const points: string[] = [];
  const cx = 50, cy = 50, outer = 46, inner = 26, n = 9;
  for (let i = 0; i < n * 2; i++) {
    const r = i % 2 === 0 ? outer : inner;
    const a = (Math.PI / n) * i - Math.PI / 2;
    points.push(`${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`);
  }
  return (
    <svg viewBox="0 0 100 100" width={size} height={size} className={className} aria-hidden="true">
      <polygon points={points.join(" ")} fill="currentColor" />
    </svg>
  );
}
