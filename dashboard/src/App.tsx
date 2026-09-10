import { lazy, useCallback, useState } from "react";
import { Layout } from "./components/Layout";
import type { Tab } from "./components/Nav";
import { PanelBoundary } from "./components/PanelBoundary";
import { confirmLeave } from "./lib/navGuard";

/**
 * Every panel is loaded when it is first opened, not when the app starts
 * (rule 112).
 *
 * All eight used to be static imports, so opening the dashboard downloaded and
 * parsed the Colony's graph and layout maths, the video editor, the product
 * editor, Abigail's panel and the whole realtime consultation client -- before
 * anything was on screen, and whether or not any of them were ever opened. That
 * is one 692 kB bundle on a laptop that is also running Ollama and ComfyUI.
 *
 * The shell -- Layout, Nav and this file -- stays eager, because it IS the first
 * paint. Nothing else is.
 *
 * Two things this deliberately does NOT change:
 *   * Inactive panels are still unmounted, exactly as before. Keeping them
 *     mounted and hidden to preserve their state would trade the download for a
 *     permanent cost in timers, polls and memory -- the opposite of the point.
 *     State that must survive a tab switch already persists properly (the
 *     Pipeline tab's running job, the Video tab's UI state, rules 33c/53).
 *   * No panel is prefetched on a hunch. A prefetch that guesses wrong is the
 *     original problem again, one guess at a time.
 */
const HomePanel = lazy(() =>
  import("./components/HomePanel").then((m) => ({ default: m.HomePanel })));
const GatheringsPanel = lazy(() =>
  import("./components/GatheringsPanel").then((m) => ({ default: m.GatheringsPanel })));
const PipelinePanel = lazy(() =>
  import("./components/PipelinePanel").then((m) => ({ default: m.PipelinePanel })));
const ProductsGallery = lazy(() =>
  import("./components/ProductsGallery").then((m) => ({ default: m.ProductsGallery })));
const VideoPanel = lazy(() =>
  import("./components/VideoPanel").then((m) => ({ default: m.VideoPanel })));
const XPostsPanel = lazy(() =>
  import("./components/XPostsPanel").then((m) => ({ default: m.XPostsPanel })));
const SecretaryPanel = lazy(() =>
  import("./components/SecretaryPanel").then((m) => ({ default: m.SecretaryPanel })));
const ConsultationPanel = lazy(() =>
  import("./components/consultation/ConsultationPanel").then(
    (m) => ({ default: m.ConsultationPanel })));
const ColonyPanel = lazy(() =>
  import("./components/colony/ColonyPanel").then((m) => ({ default: m.ColonyPanel })));
const SettingsPanel = lazy(() =>
  import("./components/SettingsPanel").then((m) => ({ default: m.SettingsPanel })));

export default function App() {
  // Home, not the Pipeline form (rule 120). The app used to open on a theme box
  // and a target score, which answers "make me a bookmark" rather than "what
  // was I doing?".
  const [tab, setTab] = useState<Tab>("home");

  /**
   * Every navigation goes through here, so a screen that is in the middle of
   * something irreversible gets asked first (rule 114). Today that is the live
   * consultation and only the live consultation; every other tab answers
   * immediately because nothing is guarding.
   *
   * It covers the sidebar AND the in-app links -- the Products shelf handing
   * off to Video, the Colony sending you to Abigail -- because they all call
   * this same function. A guard that only covered a Back button would be a
   * guard with a hole in it.
   */
  const navigate = useCallback((next: Tab) => {
    if (next === tab) return;
    void confirmLeave().then((mayLeave) => { if (mayLeave) setTab(next); });
  }, [tab]);

  return (
    <Layout tab={tab} onTabChange={navigate}>
      {tab === "home" && (
        <PanelBoundary name="Home"><HomePanel onNavigate={navigate} /></PanelBoundary>
      )}
      {/* A piece of community service, from preparing to reflecting
          (rules 117-119). */}
      {tab === "gatherings" && (
        <PanelBoundary name="Gatherings"><GatheringsPanel /></PanelBoundary>
      )}
      {tab === "pipeline" && (
        <PanelBoundary name="Pipeline"><PipelinePanel /></PanelBoundary>
      )}
      {/* The shelf lists finished videos too, and can hand you off to the
          Video tab to re-edit or re-render one. */}
      {tab === "products" && (
        <PanelBoundary name="Products"><ProductsGallery onNavigate={navigate} /></PanelBoundary>
      )}
      {tab === "video" && <PanelBoundary name="Video"><VideoPanel /></PanelBoundary>}
      {tab === "x-posts" && <PanelBoundary name="X posts"><XPostsPanel /></PanelBoundary>}
      {tab === "secretary" && (
        <PanelBoundary name="Abigail"><SecretaryPanel /></PanelBoundary>
      )}
      {/* A live meeting between people, heard through the browser. Its own
          subsystem and its own private store (rules 73-86). */}
      {tab === "consultation" && (
        <PanelBoundary name="Consultation"><ConsultationPanel /></PanelBoundary>
      )}
      {/* The Colony can send you to Abigail's own tab — she is a node in the
          graph but is never chatted with through it (rules 15/16). */}
      {tab === "colony" && (
        <PanelBoundary name="Colony"><ColonyPanel onNavigate={navigate} /></PanelBoundary>
      )}
      {tab === "settings" && (
        <PanelBoundary name="Settings"><SettingsPanel /></PanelBoundary>
      )}
    </Layout>
  );
}
