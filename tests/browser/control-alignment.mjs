import { expect } from "@playwright/test";

export async function controlGeometry(control) {
  return control.evaluate((element) => {
    const box = element.getBoundingClientRect();
    const rect = (value) => ({
      x: value.x,
      y: value.y,
      width: value.width,
      height: value.height,
      centerY: value.y + value.height / 2,
    });
    const text = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode,
        parent = node.parentElement;
      if (
        !node.textContent.trim() ||
        !parent ||
        parent.closest("svg,.sr-only") ||
        getComputedStyle(parent).visibility === "hidden"
      )
        continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const value of range.getClientRects())
        if (value.width && value.height)
          text.push({ value: node.textContent.trim(), ...rect(value) });
    }
    const icons = [...element.querySelectorAll("svg")]
      .map((icon) => icon.getBoundingClientRect())
      .filter((value) => value.width && value.height)
      .map(rect);
    return {
      box: rect(box),
      text,
      icons,
      display: getComputedStyle(element).display,
      label: element.getAttribute("aria-label") || element.textContent.trim(),
    };
  });
}

export function expectCenteredControl(geometry, label = geometry.label) {
  expect(
    Math.abs(geometry.box.height - 44),
    `${label}: control height`,
  ).toBeLessThanOrEqual(1);
  for (const text of geometry.text)
    expect(
      Math.abs(text.centerY - geometry.box.centerY),
      `${label}: text ${JSON.stringify(text.value)} must center in its 44px target`,
    ).toBeLessThanOrEqual(2);
  for (const icon of geometry.icons)
    expect(
      Math.abs(icon.centerY - geometry.box.centerY),
      `${label}: SVG must center in its 44px target`,
    ).toBeLessThanOrEqual(1);
  for (const text of geometry.text)
    for (const icon of geometry.icons)
      expect(
        Math.abs(icon.centerY - text.centerY),
        `${label}: text and SVG center lines`,
      ).toBeLessThanOrEqual(2);
}

export async function documentAnchors(page) {
  return page.evaluate(() =>
    Object.fromEntries(
      [".shared-page-header", ".project-navigation-bar", ".project-pane"].map(
        (selector) => {
          const box = document.querySelector(selector).getBoundingClientRect();
          return [selector, { x: box.x, y: box.y + scrollY, width: box.width }];
        },
      ),
    ),
  );
}

export async function expectAnchorsUnchanged(page, baseline) {
  const actual = await documentAnchors(page);
  for (const selector of Object.keys(baseline))
    for (const key of ["x", "y", "width"])
      expect(
        Math.abs(actual[selector][key] - baseline[selector][key]),
        `${selector}: ${key} after opening/closing project operations`,
      ).toBeLessThanOrEqual(1);
}
