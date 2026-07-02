import { useState } from "react";
import { Layout } from "./components/Layout";
import type { Tab } from "./components/Nav";
import { PipelinePanel } from "./components/PipelinePanel";
import { ProductsGallery } from "./components/ProductsGallery";
import { TrustPanel } from "./components/TrustPanel";
import { SettingsPanel } from "./components/SettingsPanel";

export default function App() {
  const [tab, setTab] = useState<Tab>("pipeline");
  return (
    <Layout tab={tab} onTabChange={setTab}>
      {tab === "pipeline" && <PipelinePanel />}
      {tab === "products" && <ProductsGallery />}
      {tab === "trust" && <TrustPanel />}
      {tab === "settings" && <SettingsPanel />}
    </Layout>
  );
}
