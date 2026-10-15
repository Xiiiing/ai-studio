import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  root: "ui/factory",
  base: "/factory/static/",
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  test: {
    root: ".",
    environment: "jsdom",
    include: ["tests/ui/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/ui/setup.ts"],
  },
});
