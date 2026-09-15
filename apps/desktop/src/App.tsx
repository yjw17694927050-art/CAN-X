import { lazy, Suspense } from "react";
import { useTranslation } from "react-i18next";

import "./i18n/config";
import "./styles.css";

const DockWorkspace = lazy(async () => {
  const module = await import("./components/workspace/DockWorkspace");
  return { default: module.DockWorkspace };
});

export function App() {
  const { t } = useTranslation();

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>{t("app.name")}</h1>
        <p>{t("app.tagline")}</p>
      </header>
      <Suspense fallback={<main className="workspace-shell">{t("workspace.loading")}</main>}>
        <DockWorkspace />
      </Suspense>
    </div>
  );
}
