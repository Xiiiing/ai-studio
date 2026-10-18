// White/black surfaces and the selected accent carry the interface.
// Additional colors must come from the shared semantic status tokens.
// Provider images and generated media keep their original colors.
export async function paletteViolations(page) {
  return page.evaluate(() => {
    const neutral = (value) => {
      const components = value.match(/[\d.]+/g)?.map(Number);
      if (!components || components.length < 3) return true;
      const [r, g, b, alpha = 1] = components;
      if (alpha === 0 || Math.max(r, g, b) - Math.min(r, g, b) <= 14)
        return true;
      return false;
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
        "accent",
        "accent-hover",
        "accent-text",
        "accent-soft",
        "primary",
        "primary-hover",
        "on-primary",
        "on-accent",
        "focus",
        "selection",
        "status-running",
        "crop-fill",
        "crop-stroke",
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
          !element.closest("img,video,canvas,.appearance-swatch")
        );
      })
      .flatMap((element) => {
        const style = getComputedStyle(element);
        return properties.flatMap((property) => {
          const value = style[property];
          if (!value || neutral(value)) return [];
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
