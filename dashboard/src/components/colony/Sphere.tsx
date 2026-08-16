// A body on the Colony map, finished according to how its judged work has
// actually gone (see `finish.ts` for what may and may not count as evidence).
//
// Everything below the base circle is drawn in UNIT space — a circle of radius
// 1 at the origin — and scaled to each body, so one clip path and one set of
// gradients serve every sphere on the map whatever its size, and the marks
// scale with the body instead of swamping the small ones.
//
// The signals are deliberately layered by how far away they read, because these
// spheres are ~15px on screen: the aura and the overall lightness carry at a
// glance, the gloss and grime carry when you look at one, and the scratches and
// glints are the close-up detail. The hover card says the same thing in words —
// no state is only expressible as texture.

import { useMemo } from "react";
import { accentFor } from "../../lib/utils";
import type { SphereFinish } from "./finish";

interface Props {
  cx: number;
  cy: number;
  r: number;
  /** Team accent key ("amber", "sky", …) — picks the base gradient. */
  accent: string | undefined;
  finish: SphereFinish;
  /** Stable per-body seed. The layout module is deterministic so Sheraj can
   *  learn where Ruth lives; grime that crawled around on every render would be
   *  that same bug wearing a different coat. */
  seed: string;
  /** Floor under the aura, for team cores — they glowed before this existed and
   *  a core with no judged work behind it yet shouldn't go dark. */
  minAura?: number;
}

/** Shared defs for every sphere. Rendered once, inside the graph's own <defs>. */
export function SphereDefs() {
  return (
    <>
      <clipPath id="sphere-clip">
        <circle cx="0" cy="0" r="1" />
      </clipPath>
      {/* The wet-looking hotspot of a polished body, lit from the upper left
          like everything else in the scene. */}
      <radialGradient id="sphere-gloss" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
        <stop offset="55%" stopColor="#ffffff" stopOpacity="0.32" />
        <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
      </radialGradient>
      {/* A patch of dross: dark, faintly ochre, soft-edged so it pools rather
          than sits on the surface as a drawn shape. */}
      <radialGradient id="sphere-grime" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stopColor="#241c10" stopOpacity="0.95" />
        <stop offset="60%" stopColor="#1a160f" stopOpacity="0.55" />
        <stop offset="100%" stopColor="#141109" stopOpacity="0" />
      </radialGradient>
      {/* The travelling sheen — a soft band of light crossing a polished face. */}
      <linearGradient id="sphere-sheen" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stopColor="#ffffff" stopOpacity="0" />
        <stop offset="50%" stopColor="#ffffff" stopOpacity="0.5" />
        <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
      </linearGradient>
      {/* The rim light fades out at both ends. A rim stroke of even opacity
          reads as a drawn crescent stuck to the ball rather than as light
          catching an edge — that is exactly how the first version looked. */}
      <linearGradient id="sphere-rim" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stopColor="#ffffff" stopOpacity="0" />
        <stop offset="45%" stopColor="#ffffff" stopOpacity="1" />
        <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
      </linearGradient>
    </>
  );
}

export function Sphere({ cx, cy, r, accent, finish, seed, minAura = 0 }: Props) {
  const hex = accentFor(accent).hex;
  const { shine, wear } = finish;
  const marks = useMemo(() => wearMarks(seed), [seed]);
  const stagger = useMemo(() => `-${(hashUnit(seed) * 5).toFixed(2)}s`, [seed]);

  // Marks are revealed from a FIXED set in order, so worsening work adds dross
  // to a body rather than rearranging what is already there.
  const blotches = marks.blotches.slice(0, Math.round(wear * marks.blotches.length));
  const scratches = marks.scratches.slice(0, Math.round(wear * marks.scratches.length));
  const aura = Math.max(minAura, shine);

  return (
    <>
      {aura > 0.01 && (
        <circle
          cx={cx} cy={cy} r={r * 1.1}
          fill={hex}
          opacity={0.08 + 0.24 * aura}
          filter="url(#colony-glow)"
        />
      )}
      <circle cx={cx} cy={cy} r={r} fill={`url(#sphere-${accent ?? "amber"})`} />

      <g transform={`translate(${cx} ${cy}) scale(${r})`} clipPath="url(#sphere-clip)">
        {/* Dross first — a body loses its colour and its light before it shows
            individual marks, and that overall dimming is the part still legible
            at map scale. */}
        {wear > 0 && (
          <>
            <circle cx={0} cy={0} r={1} fill="#39404b" opacity={0.3 * wear} />
            <circle cx={0} cy={0} r={1} fill="#05070c" opacity={0.42 * wear} />
          </>
        )}
        {blotches.map((b, i) => (
          <ellipse
            key={`blot-${i}`}
            cx={b.cx} cy={b.cy} rx={b.rx} ry={b.ry}
            transform={`rotate(${b.rot} ${b.cx} ${b.cy})`}
            fill="url(#sphere-grime)"
            opacity={b.tone * wear}
          />
        ))}
        {/* Scratches sit on top of the dross, because a scratch cuts through
            it — that is what makes it read as a scratch and not a smear. */}
        {scratches.map((s, i) => (
          // Hairline width and low contrast are the whole difference between a
          // scratch and a white stick lying on the ball.
          <g key={`scratch-${i}`} opacity={wear}>
            <path d={s.shadow} fill="none" stroke="#05070c" strokeWidth={0.028}
                  strokeLinecap="round" opacity={0.45} />
            <path d={s.d} fill="none" stroke="#e2e8f0" strokeWidth={0.022}
                  strokeLinecap="round" opacity={0.3} />
          </g>
        ))}

        {/* Then the polish, over everything. */}
        {shine > 0.01 && (
          <>
            <ellipse
              cx={-0.36} cy={-0.44} rx={0.3} ry={0.19}
              transform="rotate(-32 -0.36 -0.44)"
              fill="url(#sphere-gloss)"
              opacity={0.35 + 0.6 * shine}
            />
            <circle cx={-0.41} cy={-0.49} r={0.12} fill="#ffffff"
                    opacity={0.2 + 0.6 * shine} />
            {/* Rim light along the lower-right edge, away from the key light —
                the cue that says "curved and hard-polished" more than any
                highlight does. It hugs the edge: pulled inward it stops being
                a rim and becomes a smile drawn on the face. */}
            <path
              d="M 0.946 0.254 A 0.98 0.98 0 0 1 -0.254 0.946"
              fill="none" stroke="url(#sphere-rim)" strokeWidth={0.08}
              strokeLinecap="round" opacity={0.42 * shine}
            />
          </>
        )}
        {shine > 0.6 && (
          <g transform="rotate(22)">
            <rect
              x={-0.16} y={-1.3} width={0.32} height={2.6}
              fill="url(#sphere-sheen)"
              className="colony-sphere-sheen"
              style={{ animationDelay: stagger }}
            />
          </g>
        )}
      </g>

      {/* Glints sit OUTSIDE the clip: they catch on the rim and just past it,
          which is what makes a body read as glimmering rather than merely
          bright. Only the top grade earns them. */}
      {finish.grade === "radiant" && marks.glints.map((g, i) => (
        <g key={`glint-${i}`} transform={`translate(${cx + g.x * r} ${cy + g.y * r}) scale(${g.s * r})`}>
          <path
            d="M0,-1 C0.12,-0.3 0.3,-0.12 1,0 C0.3,0.12 0.12,0.3 0,1
               C-0.12,0.3 -0.3,0.12 -1,0 C-0.3,-0.12 -0.12,-0.3 0,-1 Z"
            fill="#ffffff"
            className="colony-sphere-glint"
            style={{ animationDelay: `-${(g.delay * 3.2).toFixed(2)}s` }}
          />
        </g>
      ))}
    </>
  );
}

// --- Deterministic marks -----------------------------------------------------

/** FNV-1a seeded mulberry32: same body, same freckles, every render. */
function rng(seed: string): () => number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h = (h + 0x6d2b79f5) | 0;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashUnit(seed: string): number {
  return rng(seed)();
}

interface Blotch { cx: number; cy: number; rx: number; ry: number; rot: number; tone: number }
interface Scratch { d: string; shadow: string }
interface Glint { x: number; y: number; s: number; delay: number }

interface Marks { blotches: Blotch[]; scratches: Scratch[]; glints: Glint[] }

function wearMarks(seed: string): Marks {
  const rand = rng(seed);
  const between = (lo: number, hi: number) => lo + rand() * (hi - lo);

  const blotches: Blotch[] = [];
  for (let i = 0; i < 5; i++) {
    // Dross gathers where the light doesn't reach — biased to the lower right,
    // opposite the key light, so a dirty body still reads as a lit sphere.
    const theta = between(-0.5, 2.4);
    const dist = between(0.1, 0.7);
    blotches.push({
      cx: Math.cos(theta) * dist,
      cy: Math.sin(theta) * dist,
      rx: between(0.3, 0.6),
      ry: between(0.22, 0.45),
      rot: between(0, 180),
      tone: between(0.55, 0.95),
    });
  }

  const scratches: Scratch[] = [];
  for (let i = 0; i < 4; i++) {
    const theta = between(0, Math.PI * 2);
    const dist = between(0, 0.55);
    const x0 = Math.cos(theta) * dist;
    const y0 = Math.sin(theta) * dist;
    const dir = between(0, Math.PI);
    const len = between(0.35, 0.85);
    const x1 = x0 + Math.cos(dir) * len;
    const y1 = y0 + Math.sin(dir) * len;
    // A slight bow, so four scratches don't read as a hatch pattern.
    const bow = between(-0.12, 0.12);
    const bx = (x0 + x1) / 2 - Math.sin(dir) * bow;
    const by = (y0 + y1) / 2 + Math.cos(dir) * bow;
    scratches.push({
      d: `M ${f(x0)} ${f(y0)} Q ${f(bx)} ${f(by)} ${f(x1)} ${f(y1)}`,
      // The shadow half-sits below the highlight, giving the groove depth.
      shadow: `M ${f(x0 + 0.022)} ${f(y0 + 0.022)} Q ${f(bx + 0.022)} ${f(by + 0.022)} `
        + `${f(x1 + 0.022)} ${f(y1 + 0.022)}`,
    });
  }

  const glints: Glint[] = [];
  for (let i = 0; i < 3; i++) {
    // Spread around the rim rather than placed at random, so three glints never
    // pile into one corner.
    const theta = between(-2.6, -2.2) + (i * Math.PI * 2) / 3;
    const dist = between(0.85, 1.05);
    glints.push({
      x: Math.cos(theta) * dist,
      y: Math.sin(theta) * dist,
      s: between(0.11, 0.18),
      delay: rand(),
    });
  }

  return { blotches, scratches, glints };
}

/** Trim the generated path numbers — full float noise in the DOM helps nobody. */
function f(n: number): string {
  return n.toFixed(3);
}
