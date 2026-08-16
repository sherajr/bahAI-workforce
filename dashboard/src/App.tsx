import { useState } from "react";
import { Layout } from "./components/Layout";
import type { Tab } from "./components/Nav";
import { PipelinePanel } from "./components/PipelinePanel";
import { ProductsGallery } from "./components/ProductsGallery";
import { ColonyPanel } from "./components/colony/ColonyPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { SecretaryPanel } from "./components/SecretaryPanel";
import { XPostsPanel } from "./components/XPostsPanel";
import { VideoPanel } from "./components/VideoPanel";

export default function App() {
  const [tab, setTab] = useState<Tab>("pipeline");
  return (
    <Layout tab={tab} onTabChange={setTab}>
      {tab === "pipeline" && <PipelinePanel />}
      {/* The shelf lists finished videos too, and can hand you off to the
          Video tab to re-edit or re-render one. */}
      {tab === "products" && <ProductsGallery onNavigate={setTab} />}
      {tab === "video" && <VideoPanel />}
      {tab === "x-posts" && <XPostsPanel />}
      {tab === "secretary" && <SecretaryPanel />}
      {/* The Colony can send you to Abigail's own tab — she is a node in the
          graph but is never chatted with through it (rules 15/16). */}
      {tab === "colony" && <ColonyPanel onNavigate={setTab} />}
      {tab === "settings" && <SettingsPanel />}
    </Layout>
  );
}
