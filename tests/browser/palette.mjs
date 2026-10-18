// White/black surfaces and the selected accent carry the interface.
// Additional colors must come from the shared semantic status tokens.
// Provider images and generated media keep their original colors.
export async function paletteViolations(page) {
  return page.evaluate(() => {
    const neutral = (value) => {
      const components = value.match(/[\d.]+/g)?.map(Number);
      if (!components || components.length < 3) return true;
      const [r, g, b, alpha = 1] = value.startsWith("color(srgb ")
        ? components.map((component, index) => index < 3 ? component * 255 : component)
        : components;
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
        "accent-feature",
        "on-accent-feature",
        "primary",
        "primary-hover",
        "on-primary",
        "on-accent",
        "focus",
        "selection",
        "status-running",
        "crop-fill",
        "crop-stroke",
        "studio-edge",
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

// Inspect rendered colors, not merely token names: the featured surface and
// primary action must carry different roles even when the palette is changed.
export async function colorHierarchy(page) {
  return page.evaluate(() => {
    const channels = (value) => {
      const values = value.match(/[\d.]+/g)?.map(Number) || [];
      return values.slice(0, 3).map((v) => Math.round(value.startsWith("color(srgb ") ? v * 255 : v));
    };
    const luminance = (color) => channels(color).map((channel) => {
      const value = channel / 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    }).reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
    const inspect = (element) => {
      if (!element) return undefined;
      const style = getComputedStyle(element), fg = luminance(style.color), bg = luminance(style.backgroundColor);
      return { background: channels(style.backgroundColor), color: channels(style.color), image: style.backgroundImage,
        contrast: (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05) };
    };
    const probe = document.createElement("span");
    document.body.append(probe);
    const tokens = Object.fromEntries(["panel", "surface", "surface-strong", "ink", "primary", "on-primary", "accent-feature", "on-accent-feature"].map((token) => {
      probe.style.color = `var(--${token})`;
      return [token, channels(getComputedStyle(probe).color)];
    }));
    probe.remove();
    const gradients = [...document.querySelectorAll("body *")].filter((element) => element.getClientRects().length)
      .flatMap((element) => [null, "::before", "::after"].flatMap((pseudo) => {
        const style = getComputedStyle(element, pseudo);
        if (pseudo && ["none", "normal"].includes(style.content)) return [];
        return /gradient\(/.test(style.backgroundImage) ? [`${element.tagName}.${element.className?.baseVal ?? element.className}${pseudo || ""}`] : [];
      })).slice(0, 20);
    const featureElement = document.querySelector(".dashboard-focus-feature, .appearance-preview-feature");
    const featureBackground = featureElement && luminance(getComputedStyle(featureElement).backgroundColor);
    const featureText = featureElement ? [...featureElement.querySelectorAll("*")]
      .filter((element) => !element.closest('[aria-hidden="true"]') && [...element.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim()))
      .map((element) => {
        const color = getComputedStyle(element).color, fg = luminance(color);
        return { text: element.textContent.trim(), color: channels(color), contrast: (Math.max(fg, featureBackground) + 0.05) / (Math.min(fg, featureBackground) + 0.05) };
      }) : [];
    return {
      tokens, gradients, featureText,
      primary: inspect(document.querySelector(".dashboard-focus-actions .primary, .appearance-preview-action")),
      feature: inspect(document.querySelector(".dashboard-focus-feature, .appearance-preview-feature")),
      focus: inspect(document.querySelector(".dashboard-focus")),
      metrics: [...document.querySelectorAll(".metric-card")].map(inspect),
    };
  });
}
