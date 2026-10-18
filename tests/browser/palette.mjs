// White/black surfaces and one blue accent carry the interface.
// Additional colors must come from the shared semantic status tokens.
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
    const probe = document.createElement("span");
    document.body.append(probe);
    const semanticColors = new Set(
      [
        "success",
        "success-soft",
        "success-line",
        "warning",
        "warning-soft",
        "warning-line",
        "danger",
        "danger-soft",
        "danger-line",
        "status-review",
        "status-failed",
        "status-success",
      ].map((token) => {
        probe.style.color = `var(--${token})`;
        return getComputedStyle(probe).color;
      }),
    );
    probe.remove();
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
        return properties.flatMap((property) => {
          const value = style[property];
          if (!value || neutralOrBlue(value)) return [];
          if (semanticColors.has(value)) return [];
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
