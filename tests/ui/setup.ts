import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});
Object.defineProperty(window, "scrollTo", { value: () => {}, writable: true });
Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
  value: () => {},
  writable: true,
});
Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
  value: function () {
    this.setAttribute("open", "");
  },
  writable: true,
});
Object.defineProperty(HTMLDialogElement.prototype, "close", {
  value: function () {
    this.removeAttribute("open");
  },
  writable: true,
});
Object.defineProperty(window, "matchMedia", {
  value: () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  }),
  writable: true,
});
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(globalThis, "ResizeObserver", {
  value: ResizeObserverMock,
  writable: true,
  configurable: true,
});
class DOMMatrixMock {
  m11 = 1;
  m22 = 1;
  m41 = 0;
  m42 = 0;
  constructor(_value?: string) {}
}
Object.defineProperty(globalThis, "DOMMatrixReadOnly", {
  value: DOMMatrixMock,
  writable: true,
  configurable: true,
});
