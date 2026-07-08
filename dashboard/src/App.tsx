import { useState } from "react";
import { Layout } from "./components/Layout";
import type { Tab } from "./components/Nav";
import { PipelinePanel } from "./components/PipelinePanel";
import { ProductsGallery } from "./components/ProductsGallery";
import { TrustPanel } from "./components/TrustPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { SecretaryPanel } from "./components/SecretaryPanel";
import { XPostsPanel } from "./components/XPostsPanel";

export default function App() {
  const [tab, setTab] = useState<Tab>("pipeline");
  return (
    <Layout tab={tab} onTabChange={setTab}>
      {tab === "pipeline" && <PipelinePanel />}
      {tab === "products" && <ProductsGallery />}
      {tab === "x-posts" && <XPostsPanel />}
      {tab === "secretary" && <SecretaryPanel />}
      {tab === "trust" && <TrustPanel />}
      {tab === "settings" && <SettingsPanel />}
    </Layout>
  );
}
