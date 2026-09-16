import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { notify } from "./lib/notify";
import { navigate } from "./lib/nav";
import { queueStore } from "./lib/queue";
import { settingsStore } from "./lib/settings";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./styles.css";

// 调试/验收钩子（CDP 审计用；本地应用无暴露面）
(window as unknown as Record<string, unknown>).__bc = { notify, navigate, queue: queueStore, settings: settingsStore };

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
