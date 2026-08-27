import { defineConfig } from "astro/config";
import react from "@astrojs/react";

// Build outputs static assets to ./dist, which the Dockerfile copies into
// web/static and FastAPI serves at "/".
export default defineConfig({
  integrations: [react()],
  outDir: "dist",
  compressHTML: true,
  server: { port: 4321 },
});
