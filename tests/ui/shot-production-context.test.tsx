import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ShotProductionContext } from "../../ui/factory/src/ShotProductionContext";

describe("shot production context", () => {
  it("shows pinned references and the chosen scope without hiding unresolved tasks", () => {
    const shot = {
      id: "S01",
      seconds: 6,
      shot_revision: 3,
      action: "递出书籍",
      asset_references: [
        {
          kind: "character",
          entity_id: "lead",
          version: 2,
          sha256: "a".repeat(64),
        },
      ],
      operation_impact: {
        edit_image: {
          affected: [
            { shot_id: "S01", stages: ["image", "video"] },
            { shot_id: "S03", stages: ["image", "video"] },
          ],
          preserved_shot_ids: ["S02"],
          unresolved_tasks: [{ shot_id: "S03", status: "unknown" }],
        },
        edit_video: {
          affected: [{ shot_id: "S01", stages: ["video"] }],
          preserved_shot_ids: ["S02", "S03"],
          unresolved_tasks: [],
        },
      },
    };
    const view = render(
      <ShotProductionContext shot={shot} revision={8} stage="image" />,
    );
    expect(screen.getByText(/character · lead · v2/)).toBeInTheDocument();
    expect(screen.getByText(/S03（首帧、视频）/)).toBeInTheDocument();
    expect(screen.getByText(/待核对云任务/)).toBeInTheDocument();
    view.rerender(
      <ShotProductionContext shot={shot} revision={8} stage="video" />,
    );
    expect(screen.getByText("S01（视频）")).toBeInTheDocument();
    expect(screen.queryByText(/待核对云任务/)).not.toBeInTheDocument();
  });
});
