import { describe, it, expect } from "vitest";
import { ownedMedia, parseMoney, pendingGate } from "../../ui/factory/src/api";
const id = "012345abcdef";
describe("媒体归属与精确计价边界", () => {
  it("允许当前项目规范路径并拒绝其它项目、外部URL及路径变体", () => {
    const path =
      "/media/production/factory-" +
      id +
      "/release/v1-aaaaaaaaaaaa/episode.mp4";
    expect(ownedMedia(id, path)).toBe(new URL(path, location.origin).href);
    for (const raw of [
      path.replace(id, "fedcba654321"),
      "https://example.org" + path,
      path + "?different=1",
      path.replace("/release/", "/../release/"),
      path.replace("/release/", "/%2e%2e/release/"),
      path.replace("/release/", "//release/"),
      path + "\0",
      path.replace("/release/", "\\release/"),
    ])
      expect(ownedMedia(id, raw)).toBe("");
  });
  it("金额按整数微单位准确处理，不接受负数、指数与超精度", () => {
    expect(parseMoney("12.000001")).toBe(12000001);
    expect(parseMoney("0")).toBe(0);
    for (const value of [
      "-1",
      "1e3",
      "0.0000001",
      "NaN",
      "Infinity",
      "<script>",
    ])
      expect(() => parseMoney(value)).toThrow();
  });
  it("后台明确没有人工待办时，不暴露自动决策中的旧gate", () => {
    expect(
      pendingGate({
        status: "awaiting_story",
        decisions: [{ id: "old", type: "story", status: "pending" }],
        operator: { attention: null },
      }),
    ).toBeNull();
    expect(
      pendingGate({
        status: "approved",
        operator: { attention: { blocking: true, gate: { id: "stale" } } },
      }),
    ).toBeNull();
  });
});
