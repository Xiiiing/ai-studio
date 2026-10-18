import { readFileSync } from "node:fs";
import path from "node:path";
import { render } from "@testing-library/react";
import { expect, it } from "vitest";
import { BrandMark } from "../../ui/factory/src/Brand";

const read = (file: string) =>
  readFileSync(path.join(process.cwd(), file), "utf8");

it("uses the unchanged README logo for navigation and the browser icon", () => {
  const canonical = read("docs/assets/logo.svg");
  expect(read("ui/factory/public/brand.svg")).toBe(canonical);
  expect(read("README.md")).toContain('src="docs/assets/logo.svg"');
  const html = new DOMParser().parseFromString(
    read("ui/factory/index.html"),
    "text/html",
  );
  expect(html.querySelector('link[rel="icon"]')?.getAttribute("href")).toBe(
    "%BASE_URL%brand.svg",
  );

  const { container } = render(<BrandMark size={40} />);
  const mark = container.querySelector("img");
  expect(mark).toHaveAttribute("src", `${import.meta.env.BASE_URL}brand.svg`);
  expect(mark).toHaveAttribute("width", "40");
  expect(mark).toHaveAttribute("height", "40");
  expect(mark).toHaveAttribute("alt", "");
  expect(mark).toHaveAttribute("aria-hidden", "true");
  expect(container.querySelector("use")).toBeNull();
});
