import { render } from "@testing-library/react";
import { expect, it } from "vitest";
import {
  StatusIndicator,
  statusTone,
} from "../../ui/factory/src/StatusIndicator";

it.each([
  ["running", "running"],
  ["producing", "running"],
  ["creating", "running"],
  ["researching", "running"],
  ["scripting", "running"],
  ["visual_development", "running"],
  ["waiting_remote", "running"],
  ["reviewing", "review"],
  ["semantic_pending", "review"],
  ["human", "review"],
  ["human_required", "review"],
  ["awaiting_story", "review"],
  ["awaiting_style", "review"],
  ["awaiting_final", "review"],
  ["blocked", "failed"],
  ["failed", "failed"],
  ["error", "failed"],
  ["rejected", "failed"],
  ["succeeded", "success"],
  ["completed", "success"],
  ["approved", "success"],
  ["passed", "success"],
  ["pass", "success"],
  ["ready", "success"],
  ["queued", "idle"],
  ["pending", "idle"],
  ["paused", "idle"],
  ["cancelled", "idle"],
  ["waiting_resources", "idle"],
  ["unverified", "idle"],
  ["configured", "idle"],
  [undefined, "idle"],
  ["unknown-status", "idle"],
  ["constructor", "idle"],
  ["toString", "idle"],
  ["__proto__", "idle"],
])(
  "maps recorded %s to %s without inventing an active review",
  (status, tone) => {
    expect(statusTone(status)).toBe(tone);
  },
);

it("renders only a hidden decorative marker, leaving the caller's label intact", () => {
  const { container } = render(
    <span>
      <StatusIndicator status="awaiting_final" />
      等待审成片
    </span>,
  );
  const indicator = container.querySelector("i");
  expect(indicator).toHaveAttribute("data-status-indicator", "review");
  expect(indicator).toHaveAttribute("aria-hidden", "true");
  expect(indicator).toHaveTextContent("");
  expect(container).toHaveTextContent("等待审成片");
});
