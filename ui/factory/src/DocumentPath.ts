import { ownedMedia } from "./api";

export function documentUrl(projectId: string | undefined, raw: unknown) {
  if (!projectId || typeof raw !== "string") return "";
  try {
    // Saved receipts may carry a production-relative path rather than a media URL.
    const path = raw.startsWith("production/") ? "/media/" + raw : raw;
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || url.search || url.hash)
      return "";
    return ownedMedia(projectId, decodeURIComponent(url.pathname));
  } catch {
    return "";
  }
}
