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

export function CreativeContract({ project }: { project: RecordData }) {
  const contract = project.creative_contract;
  const methods = rows(project.shots).flatMap((shot) => [
    shot.video?.motion_method,
    shot.video?.generation_kind,
    shot.video?.origin,
    shot.generation_kind,
  ]);
  const provenance = project.delivery?.motion_provenance;
  const recordedMotion = String(project.delivery?.limitations?.motion || "");
  const still =
    Number(provenance?.still_camera_shots) > 0 ||
    methods.includes("still_camera") ||
    methods.includes("procedural_camera_motion") ||
    recordedMotion.includes("procedural_camera_motion");
  const generated =
    methods.some((method) =>
      [
        "local_diffusion_video",
        "cloud_diffusion_video",
        "generated_video",
      ].includes(method),
    ) ||
    Number(provenance?.generated_video_shots) > 0 ||
    recordedMotion.includes("local_diffusion_video");
  const actual =
    still && generated
      ? "混合制作：部分镜头为生成视频，部分镜头为静帧运镜。"
      : still
        ? "实际制作：静帧图片加程序运镜，未生成角色表演或口型。"
        : generated
          ? "实际制作：模型生成运动视频；动作效果以镜头预览和质检为准。"
          : "实际制作方式：尚无可确认的镜头制作回执。";
  return (
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
        <p>{actual}</p>
        {provenance && (
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
}
