import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("Root element '#root' not found. Check index.html for <div id=\"root\"></div>.");
}

createRoot(rootElement).render(<App />);
