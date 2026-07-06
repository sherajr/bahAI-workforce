import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { CardCopy, Listing, ProductRow, Review } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Human labels for the 9 constitution principle keys the Reviewer returns.
export const PRINCIPLE_LABELS: Record<string, string> = {
  "1_work_as_worship": "Work as Worship",
  "2_fruit_not_words": "Judge by Fruit, Not Motion",
  "3_trustworthiness": "Trustworthiness (Amanah)",
  "4_consultation": "Consultation",
  "5_moderation": "Moderation",
  "6_deeds_over_words": "Deeds Over Words",
  "7_craft_in_service": "Craft in Service of Social Good",
  "8_justice": "Justice ('Adl)",
  "9_independent_investigation": "Independent Investigation of Truth",
  // Quote-card rubric keys (reviewer.score_quote_card) — cards are scored on
  // a purpose-built scale, not the 9 Etsy-listing principles.
  quote_citation: "Quote & Citation Accuracy",
  translation: "Translation Honesty & Quality",
  artwork_fit: "Artwork Fit",
  newcomer_accessibility: "Newcomer Accessibility",
  legibility: "Print Legibility",
};

export function principleLabel(key: string): string {
  return PRINCIPLE_LABELS[key] ?? key.replace(/^\d+_/, "").replace(/_/g, " ");
}

export type Badge = "EXCEPTIONAL" | "APPROVED" | "BORDERLINE" | "REJECTED" | "BEST EFFORT" | string;

export function badgeFor(overall: number): Badge {
  if (overall >= 9.0) return "EXCEPTIONAL";
  if (overall >= 7.0) return "APPROVED";
  if (overall >= 5.0) return "BORDERLINE";
  return "REJECTED";
}

/**
 * Badge for a saved product: a product that shipped below its target score
 * (revision loop stalled or ran out of attempts) wears BEST EFFORT so it never
 * looks like a clean pass. target_reached null/undefined = saved before the
 * pipeline tracked this — fall back to the score-range badge.
 */
export function badgeForProduct(row: ProductRow, overall: number): Badge {
  return row.target_reached === 0 ? "BEST EFFORT" : badgeFor(overall);
}

export function badgeClasses(badge: Badge): string {
  switch (badge) {
    case "EXCEPTIONAL":
      return "bg-amber-400/15 text-amber-300 border-amber-400/40";
    case "APPROVED":
      return "bg-emerald-400/10 text-emerald-300 border-emerald-400/40";
    case "BORDERLINE":
    case "BEST EFFORT":
      return "bg-orange-400/10 text-orange-300 border-orange-400/40";
    case "REJECTED":
      return "bg-rose-400/10 text-rose-300 border-rose-400/40";
    default:
      return "bg-slate-700/40 text-slate-300 border-slate-600";
  }
}

export function scoreBarColor(score: number): string {
  if (score >= 9) return "bg-amber-400";
  if (score >= 7) return "bg-emerald-400";
  if (score >= 6) return "bg-orange-400";
  return "bg-rose-400";
}

export const AGENT_COLORS: Record<string, string> = {
  Artist: "border-l-sky-400 text-sky-300",
  Scribe: "border-l-violet-400 text-violet-300",
  Reviewer: "border-l-emerald-400 text-emerald-300",
  Librarian: "border-l-amber-400 text-amber-300",
  System: "border-l-rose-400 text-rose-300",
  Sheraj: "border-l-fuchsia-400 text-fuchsia-300",
};

export function parseListing(row: ProductRow): Listing | null {
  if (!row.listing_copy) return null;
  try {
    return JSON.parse(row.listing_copy) as Listing;
  } catch {
    return null;
  }
}

export function isQuoteCard(row: ProductRow): boolean {
  return row.product_type === "quote_card";
}

export function parseCardCopy(row: ProductRow): CardCopy | null {
  if (!isQuoteCard(row) || !row.listing_copy) return null;
  try {
    return JSON.parse(row.listing_copy) as CardCopy;
  } catch {
    return null;
  }
}

export function parseReview(row: ProductRow): Review | null {
  if (!row.reviewer_scores) return null;
  try {
    return JSON.parse(row.reviewer_scores) as Review;
  } catch {
    return null;
  }
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function usd(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}
