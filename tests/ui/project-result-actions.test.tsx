import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ProjectResultActions } from "../../ui/factory/src/ProjectResultActions";

it("keeps pending download explanations uniquely associated across instances", () => {
  render(
    <>
      <ProjectResultActions project={{ id: "012345abcdef" }} onOpen={vi.fn()} />
      <ProjectResultActions project={{ id: "abcdef012345" }} onOpen={vi.fn()} />
    </>,
  );
  const buttons = screen.getAllByRole("button", { name: "下载成片" });
  expect(buttons).toHaveLength(2);
  expect(buttons[0].getAttribute("aria-describedby")).not.toBe(
    buttons[1].getAttribute("aria-describedby"),
  );
  for (const button of buttons) {
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("成片尚未生成");
  }
});

it("can describe a disabled download in the project summary without duplicating the caption", () => {
  render(
    <>
      <ProjectResultActions
        project={{ id: "012345abcdef" }}
        onOpen={vi.fn()}
        reasonId="project-download-description"
      />
      <p id="project-download-description">成片尚未生成</p>
    </>,
  );
  expect(screen.getAllByText("成片尚未生成")).toHaveLength(1);
  expect(
    screen.getByRole("button", { name: "下载成片" }),
  ).toHaveAccessibleDescription("成片尚未生成");
});
