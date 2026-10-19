// Keep the page's content width while allowing top-layer dialogs to paint across
// the full viewport. A reserved root gutter otherwise clips full-width dialogs.
const owners = new Set<symbol>();
let restore: (() => void) | undefined;

export function lockPageScroll(): () => void {
  if (!owners.size) {
    const root = document.documentElement;
    const body = document.body;
    const workspace = document.querySelector<HTMLElement>(".main-shell");
    const properties = [
      [root, "scrollbar-gutter"],
      [root, "overflow"],
      [body, "overflow"],
      [body, "padding-inline-end"],
      ...(workspace ? [[workspace, "overflow"] as const] : []),
    ] as const;
    const previous = properties.map(([element, property]) => ({
      element,
      property,
      value: element.style.getPropertyValue(property),
      priority: element.style.getPropertyPriority(property),
    }));
    const originalGutter = previous[0];
    const padding = parseFloat(getComputedStyle(body).paddingInlineEnd) || 0;
    const compensateGutter = () => {
      // Read the unlocked CSS at the current breakpoint. Desktop uses an
      // internal workspace scroller; mobile still reserves a document gutter.
      if (originalGutter.value)
        root.style.setProperty(
          "scrollbar-gutter",
          originalGutter.value,
          originalGutter.priority,
        );
      else root.style.removeProperty("scrollbar-gutter");
      const layoutWidth = root.getBoundingClientRect().width;
      const reservedGutter =
        getComputedStyle(root).scrollbarGutter.includes("stable") &&
        layoutWidth > 0
          ? window.innerWidth - layoutWidth
          : 0;
      const gutter = Math.max(
        0,
        root.clientWidth ? window.innerWidth - root.clientWidth : 0,
        reservedGutter,
      );
      root.style.setProperty("scrollbar-gutter", "auto");
      body.style.setProperty("padding-inline-end", `${padding + gutter}px`);
    };
    compensateGutter();
    root.style.setProperty("overflow", "hidden");
    body.style.setProperty("overflow", "hidden");
    workspace?.style.setProperty("overflow", "hidden");
    window.addEventListener("resize", compensateGutter);
    restore = () => {
      window.removeEventListener("resize", compensateGutter);
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
