import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { documentUrl } from "./DocumentPath";

export default function MarkdownBody({
  value,
  projectId,
  baseUrl,
}: {
  value: string;
  projectId?: string;
  baseUrl?: string;
}) {
  return (
    <div className="document-markdown">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children }) => {
            if (!href) return <span>{children}</span>;
            let safe = "";
            try {
              const url = new URL(
                href || "",
                new URL(baseUrl || "/", window.location.origin),
              );
              if (["http:", "https:"].includes(url.protocol)) safe = url.href;
            } catch {
              /* Invalid links remain readable text. */
            }
            return safe ? (
              <a href={safe} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            );
          },
          img: ({ src, alt }) => {
            if (typeof src !== "string" || !src)
              return <span>{alt || "图片未提供项目文件"}</span>;
            let raw = "";
            try {
              raw = new URL(
                typeof src === "string" ? src : "",
                new URL(baseUrl || "/", window.location.origin),
              ).href;
            } catch {
              /* No image request. */
            }
            const safe = documentUrl(projectId, raw);
            return safe ? (
              <img src={safe} alt={alt || "文档插图"} loading="lazy" />
            ) : (
              <span>{alt || "图片未提供项目文件"}</span>
            );
          },
        }}
      >
        {value}
      </Markdown>
    </div>
  );
}
