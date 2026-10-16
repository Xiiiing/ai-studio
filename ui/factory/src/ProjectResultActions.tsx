import { ArrowUpRight, Download, Film } from "lucide-react";
import { ownedMedia, rows, type RecordData } from "./api";

export function currentResultPage(project: RecordData) {
  if (ownedMedia(project.id, project.delivery?.video_url)) return "delivery";
  if (
    rows(project.shots).some(
      (shot) =>
        ownedMedia(project.id, shot.video_url || shot.video?.path) ||
        ownedMedia(project.id, shot.image_url),
    )
  )
    return "shots";
  if (rows(project.styles).length) return "decisions";
  if (project.script) return "script";
  return "decisions";
}

export function ProjectResultActions({
  project,
  onOpen,
}: {
  project: RecordData;
  onOpen: (page: string) => void;
}) {
  const video = ownedMedia(project.id, project.delivery?.video_url);
  return (
    <div className="project-result-actions" aria-label="查看与下载作品">
      <button
        className="button"
        data-project-result
        onClick={() => onOpen(currentResultPage(project))}
      >
        <Film size={16} />
        查看作品
        <ArrowUpRight size={14} />
      </button>
      {video ? (
        <a
          className="button primary"
          data-project-download
          href={video}
          download
        >
          <Download size={16} />
          下载成片
        </a>
      ) : (
        <div className="project-download-pending">
          <button
            className="button"
            disabled
            aria-describedby="download-reason"
          >
            <Download size={16} />
            下载成片
          </button>
          <small id="download-reason">成片尚未生成</small>
        </div>
      )}
    </div>
  );
}
