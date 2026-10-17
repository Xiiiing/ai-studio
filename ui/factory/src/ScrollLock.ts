// Keep the page's content width while allowing top-layer dialogs to paint across
// the full viewport. A reserved root gutter otherwise clips full-width dialogs.
const owners = new Set<symbol>();
let restore: (() => void) | undefined;

export function lockPageScroll(): () => void {
  if (!owners.size) {
    const root = document.documentElement;
    const body = document.body;
    const properties = [
      [root, "scrollbar-gutter"],
      [root, "overflow"],
      [body, "overflow"],
      [body, "padding-inline-end"],
    ] as const;
    const previous = properties.map(([element, property]) => ({
      element,
      property,
      value: element.style.getPropertyValue(property),
      priority: element.style.getPropertyPriority(property),
    }));
    const layoutWidth = root.getBoundingClientRect().width;
    const reservedGutter =
      getComputedStyle(root).scrollbarGutter.includes("stable") &&
      layoutWidth > 0
        ? window.innerWidth - layoutWidth
        : 0;
    // Chromium can report the whole viewport as clientWidth even though a
    // stable gutter still reduces the root's actual layout width.
    const gutter = Math.max(
      0,
      root.clientWidth ? window.innerWidth - root.clientWidth : 0,
      reservedGutter,
    );
    const padding = parseFloat(getComputedStyle(body).paddingInlineEnd) || 0;
    root.style.setProperty("scrollbar-gutter", "auto");
    root.style.setProperty("overflow", "hidden");
    body.style.setProperty("overflow", "hidden");
    if (gutter)
      body.style.setProperty("padding-inline-end", `${padding + gutter}px`);
    restore = () => {
      for (const { element, property, value, priority } of previous) {
        if (value) element.style.setProperty(property, value, priority);
        else element.style.removeProperty(property);
      }
    };
  }
  const owner = Symbol();
  owners.add(owner);
  return () => {
    if (!owners.delete(owner) || owners.size) return;
    restore?.();
    restore = undefined;
  };
}
