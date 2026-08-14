import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/onest/300.css";
import "@fontsource/onest/400.css";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "@/auth";
import { App } from "@/App";
import "@/styles.css";
import "@/technical.css";

const root = document.getElementById("root");
if (!root) throw new Error("Не найден корневой элемент #root");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
