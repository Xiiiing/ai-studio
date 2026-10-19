import { expect, it } from "vitest";
import { lockPageScroll } from "../../ui/factory/src/ScrollLock";

it("preserves page content space while removing the gutter that clips modal focus", () => {
  const root = document.documentElement,
    body = document.body;
  const rootStyle = root.getAttribute("style"),
    bodyStyle = body.getAttribute("style");
  const descriptor = Object.getOwnPropertyDescriptor(root, "clientWidth");
  Object.defineProperty(root, "clientWidth", {
    configurable: true,
    value: window.innerWidth - 15,
  });
  root.style.scrollbarGutter = "stable";
  body.style.paddingInlineEnd = "4px";
  const release = lockPageScroll();
  try {
    expect(root.style.scrollbarGutter).toBe("auto");
    expect(root.style.overflow).toBe("hidden");
    expect(body.style.paddingInlineEnd).toBe("19px");
    release();
    expect(root.style.scrollbarGutter).toBe("stable");
    expect(body.style.paddingInlineEnd).toBe("4px");
    expect(body.style.overflow).not.toBe("hidden");
  } finally {
    release();
    if (descriptor) Object.defineProperty(root, "clientWidth", descriptor);
    else Reflect.deleteProperty(root, "clientWidth");
    if (rootStyle === null) root.removeAttribute("style");
    else root.setAttribute("style", rootStyle);
    if (bodyStyle === null) body.removeAttribute("style");
    else body.setAttribute("style", bodyStyle);
  }
});

it("keeps nested and independently closed overlays locked until the last owner exits", () => {
  const first = lockPageScroll(),
    second = lockPageScroll();
  try {
    first();
    expect(document.body.style.overflow).toBe("hidden");
    first();
    expect(document.body.style.overflow).toBe("hidden");
    second();
    expect(document.body.style.overflow).not.toBe("hidden");
  } finally {
    first();
    second();
  }
});

it("compensates a stable layout gutter when Chromium clientWidth includes it", () => {
  const root = document.documentElement,
    body = document.body;
  const originalStyle = root.getAttribute("style");
  const descriptor = Object.getOwnPropertyDescriptor(root, "clientWidth");
  const originalRect = root.getBoundingClientRect;
  Object.defineProperty(root, "clientWidth", {
    configurable: true,
    value: window.innerWidth,
  });
  root.getBoundingClientRect = () =>
    ({ width: window.innerWidth - 15 }) as DOMRect;
  root.style.scrollbarGutter = "stable";
  const release = lockPageScroll();
  try {
    expect(body.style.paddingInlineEnd).toBe("15px");
    release();
    expect(root.style.scrollbarGutter).toBe("stable");
    expect(body.style.paddingInlineEnd).toBe("");
  } finally {
    release();
    root.getBoundingClientRect = originalRect;
    if (descriptor) Object.defineProperty(root, "clientWidth", descriptor);
    else Reflect.deleteProperty(root, "clientWidth");
    if (originalStyle === null) root.removeAttribute("style");
    else root.setAttribute("style", originalStyle);
  }
});


it("locks the desktop workspace through nested dialogs and restores its scroll position", () => {
  const workspace = document.createElement("div");
  workspace.className = "main-shell";
  workspace.style.overflow = "auto";
  workspace.scrollTop = 123;
  document.body.append(workspace);
  const first = lockPageScroll(), second = lockPageScroll();
  try {
    expect(workspace.style.overflow).toBe("hidden");
    first();
    expect(workspace.style.overflow).toBe("hidden");
    second();
    expect(workspace.style.overflow).toBe("auto");
    expect(workspace.scrollTop).toBe(123);
  } finally {
    first();second();workspace.remove();
  }
});
