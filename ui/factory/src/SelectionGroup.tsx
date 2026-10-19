import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type HTMLAttributes,
  type ReactNode,
} from "react";
import { motion, useReducedMotion } from "motion/react";
import "./styles/selection.css";

type Box = { x: number; y: number; width: number; height: number };
/** A single visual selection follows the active control without moving its text. */
export function SelectionGroup({
  as: Tag = "div",
  variant = "outline",
  value,
  selector = '[aria-current="page"], [aria-pressed="true"], [aria-selected="true"], [data-selected="true"], .active, .selected',
  className = "",
  children,
  ...props
}: HTMLAttributes<HTMLElement> & {
  as?: "div" | "nav" | "ul";
  variant?: "outline" | "underline";
  value?: unknown;
  selector?: string;
  children: ReactNode;
}) {
  const root = useRef<HTMLElement>(null),
    id = useId(),
    reduce = useReducedMotion();
  const [box, setBox] = useState<Box | null>(null);
  useLayoutEffect(() => {
    const group = root.current;
    if (!group) return;
    let frame = 0,
      alive = true;
    const measure = () => {
      const active = Array.from(
        group.querySelectorAll<HTMLElement>(selector),
      ).find(
        (el) =>
          el.closest("[data-selection-group]") === group &&
          el.getClientRects().length > 0,
      );
      if (!active) {
        setBox(null);
        return;
      }
      const outer = group.getBoundingClientRect(),
        inner = active.getBoundingClientRect();
      const next = {
        x: inner.left - outer.left + group.scrollLeft - group.clientLeft,
        y:
          (variant === "underline" ? inner.bottom - 2 : inner.top) -
          outer.top +
          group.scrollTop -
          group.clientTop,
        width: inner.width,
        height: variant === "underline" ? 2 : inner.height,
      };
      if (next.width <= 0 || next.height <= 0) {
        setBox(null);
        return;
      }
      setBox((old) =>
        old &&
        Object.keys(next).every(
          (k) => Math.abs(old[k as keyof Box] - next[k as keyof Box]) < 0.25,
        )
          ? old
          : next,
      );
    };
    const schedule = () => {
      if (!alive) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    measure();
    const resize =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(schedule)
        : null;
    resize?.observe(group);
    for (const child of group.children)
      if (!child.hasAttribute("data-selection-marker")) resize?.observe(child);
    const mutation = new MutationObserver(schedule);
    mutation.observe(group, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: [
        "aria-current",
        "aria-pressed",
        "aria-selected",
        "data-selected",
        "class",
        "hidden",
      ],
    });
    group.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    document.fonts?.ready.then(schedule);
    return () => {
      alive = false;
      cancelAnimationFrame(frame);
      resize?.disconnect();
      mutation.disconnect();
      group.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, [value, variant, selector]);
  // Motion tracks both position and changing item width; the spring retains its
  // current velocity when a user selects again before the previous move ends.
  return (
    <Tag
      {...props}
      ref={root as never}
      className={`selection-group ${className}`}
      data-selection-group
      data-selection-variant={variant}
    >
      {children}
      {box && (
        <motion.span
          aria-hidden="true"
          data-selection-marker
          layout
          layoutId={`selection-${id}`}
          className="selection-marker"
          initial={false}
          animate={box}
          transition={
            reduce
              ? { duration: 0 }
              : { type: "spring", stiffness: 440, damping: 34, mass: 0.75 }
          }
        />
      )}
    </Tag>
  );
}
