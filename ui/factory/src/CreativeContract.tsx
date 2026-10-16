import { rows, type RecordData } from "./api";

export const visualChoices = [
  { id: "anime_2d", label: "2D 动漫 · 生成角色动作", format: "animation" },
  { id: "stylized_3d", label: "3D 动画 · 生成角色动作", format: "animation" },
  {
    id: "photorealistic",
    label: "真人短剧 · 生成角色动作",
    format: "live_action",
  },
  {
    id: "illustrated",
    label: "静帧动态漫画 · 图片加运镜",
    format: "motion_comic",
  },
] as const;

export function creativeChoice(value: string) {
  const choice = visualChoices.find((item) => item.id === value);
  if (!choice) throw new Error("请选择有效的画面与运动方式。");
  return {
    format: choice.format,
    creative_contract: {
      schema_version: 1,
      visual_style: choice.id,
      story_mode: "standalone",
      pacing: "short_drama",
      motion: choice.id === "illustrated" ? "still_camera" : "generated_video",
    },
  };
}

export function projectVisualLabel(project: RecordData) {
  const choice = visualChoices.find(
    (item) => item.id === project.creative_contract?.visual_style,
  );
  if (choice) return choice.label;
  return (
    (
      {
        animation: "动画 · 旧项目未声明画风",
        live_action: "真人影视 · 旧规格",
        motion_comic: "静帧动态漫画",
      } as Record<string, string>
    )[project.format] ||
    project.format ||
    "制作形式未声明"
  );
}

// Keep precedence aligned with factory.media_contract.video_motion.
function videoMotion(record: RecordData | undefined) {
  if (
    record?.origin === "procedural_camera_motion" ||
    record?.generation_kind === "procedural_camera_motion" ||
    record?.motion_method === "still_camera" ||
    record?.diffusion_video === false
  )
    return "still_camera";
  if (
    ["local_diffusion_video", "cloud_diffusion_video"].includes(
      record?.generation_kind,
    )
  ) {
    return "generated_video";
  }
  return "unknown";
}

export function CreativeContract({
  project,
  compact = false,
}: {
  project: RecordData;
  compact?: boolean;
}) {
  const contract = project.creative_contract;
  const shots = rows(project.shots);
  const methods = shots.map((shot) => videoMotion(shot.video));
  const delivered = project.delivery?.motion_provenance;
  const hasDeliveredProvenance =
    delivered != null &&
    typeof delivered === "object" &&
    !Array.isArray(delivered);
  const provenance = hasDeliveredProvenance
    ? delivered
    : {
        generated_video_shots: methods.filter(
          (method) => method === "generated_video",
        ).length,
        still_camera_shots: methods.filter(
          (method) => method === "still_camera",
        ).length,
        unknown_shots: methods.filter((method) => method === "unknown").length,
      };
  const hasVideoRecords = shots.some(
    (shot) => shot.video && Object.keys(shot.video).length > 0,
  );
  const recordedMotion = String(project.delivery?.limitations?.motion || "");
  const historicalCameraOnly =
    !hasDeliveredProvenance &&
    !hasVideoRecords &&
    recordedMotion.includes("procedural_camera_motion");
  const still = Number(provenance.still_camera_shots) > 0;
  const generated = Number(provenance.generated_video_shots) > 0;
  const unknownShots = Number(provenance?.unknown_shots) || 0;
  const actual =
    still && generated
      ? "混合制作：部分镜头为生成视频，部分镜头为静帧运镜。"
      : still
        ? "实际制作：静帧图片加程序运镜，未生成角色表演或口型。"
        : generated
          ? "实际制作：模型生成运动视频；动作效果以镜头预览和质检为准。"
          : historicalCameraOnly
            ? "历史交付说明：静帧图片加程序运镜；尚无可确认的逐镜制作回执。"
            : "实际制作方式：尚无可确认的镜头制作回执。";
  const specification = (
    <section className="creative-contract" aria-label="创作与制作规格">
      <div>
        <small>目标画面与叙事规格</small>
        <strong>{projectVisualLabel(project)}</strong>
        <p>
          {contract?.story_mode === "standalone"
            ? "单部故事闭环 · 短剧节奏：开场冲突、递进反转、结尾兑现，不依赖下一集。"
            : "旧项目未声明单部闭环规格，保留原有故事与成片。"}
        </p>
      </div>
      <div>
        <small>以制作记录为准</small>
        <p>
          {unknownShots > 0 && (still || generated)
            ? "已确认的部分镜头：" + actual.replace(/^实际制作：/, "")
            : actual}
        </p>
        {unknownShots > 0 && (
          <p>
            仍有 {unknownShots} 镜制作方式尚未确认，不能据此确认全片的制作方式。
          </p>
        )}
        {(hasDeliveredProvenance || shots.length > 0) && (
          <p>
            生成视频 {provenance.generated_video_shots || 0} 镜 · 静帧运镜{" "}
            {provenance.still_camera_shots || 0} 镜 · 未知{" "}
            {provenance.unknown_shots || 0} 镜
          </p>
        )}
        {contract?.motion === "generated_video" && (
          <p>目标为角色动作视频；当前流程不承诺同步口型，配音可采用画外音。</p>
        )}
      </div>
    </section>
  );
  if (!compact) return specification;
  return (
    <section
      className="creative-contract-summary"
      aria-label="创作与制作规格摘要"
    >
      <div>
        <small>制作规格</small>
        <strong>{projectVisualLabel(project)}</strong>
      </div>
      <p>
        {unknownShots > 0 && (still || generated)
          ? "已确认的部分镜头：" + actual.replace(/^实际制作：/, "")
          : actual}
        {unknownShots > 0 ? ` 仍有 ${unknownShots} 镜方式待确认。` : ""}
      </p>
      <details>
        <summary>规格与回执依据</summary>
        {specification}
      </details>
    </section>
  );
}
