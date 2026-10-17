// Only marked status dots and the active return flow may add semantic color.
// Provider images and generated media keep their original colors.
export async function paletteViolations(page) {
  return page.evaluate(() => {
    const neutralOrBlue = (value) => {
      const components = value.match(/[\d.]+/g)?.map(Number);
      if (!components || components.length < 3) return true;
      const [r, g, b, alpha = 1] = components;
      if (alpha === 0 || Math.max(r, g, b) - Math.min(r, g, b) <= 14)
        return true;
      return b > r + 10 && b >= g && g >= r && (g - r) / (b - r) <= 0.85;
    };
    const properties = [
      "color",
      "backgroundColor",
      "borderTopColor",
      "borderRightColor",
      "borderBottomColor",
      "borderLeftColor",
      "fill",
      "stroke",
    ];
    return [...document.querySelectorAll("body *")]
      .filter((element) => {
        const style = getComputedStyle(element);
        return (
          element.getClientRects().length &&
          style.visibility !== "hidden" &&
          style.opacity !== "0" &&
          !element.closest("img,video,canvas")
        );
      })
      .flatMap((element) => {
        const style = getComputedStyle(element);
        const dot = element.matches("[data-status-indicator]");
        const repairLine = element.matches(
          ".is-repair .workflow-flow-line, .flow-arrow-repair",
        );
        return properties.flatMap((property) => {
          const value = style[property];
          if (!value || neutralOrBlue(value)) return [];
          if (dot || (repairLine && ["fill", "stroke"].includes(property)))
            return [];
          return [
            {
              element: `${element.tagName}.${element.className?.baseVal ?? element.className}`,
              property,
              value,
            },
          ];
        });
      })
      .slice(0, 20);
  });
}
