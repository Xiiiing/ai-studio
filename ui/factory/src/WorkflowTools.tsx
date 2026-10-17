import { useState } from "react";
import { Field, Panel, ErrorNotice } from "./ui";
import { request } from "./api";
export function WorkflowTools({ notify }: { notify: (s: string) => void }) {
  const [json, setJSON] = useState("");
  const [id, setId] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Panel
      title="ComfyUI 工作流"
      description="导入 API 格式工作流和输入／输出映射，再将模板编号填写到制作服务。不会运行工作流或安装节点。"
    >
      <ErrorNotice error={error} reserveSpace />
      <Field label="工作流模板 JSON">
        <textarea
          rows={8}
          value={json}
          onChange={(e) => setJSON(e.target.value)}
          disabled={busy}
        />
      </Field>
      <div className="button-row">
        <button
          className="button"
          disabled={busy || !json.trim()}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              const template = await request(
                "/workflow-templates",
                JSON.parse(json),
              );
              setId(template.id);
              notify("工作流已导入，请在制作服务中选择此模板。");
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          导入模板
        </button>
        <button
          className="button"
          disabled={busy || !/^[a-f0-9]{64}$/.test(id)}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              const t = await request("/workflow-templates/" + id);
              const url = URL.createObjectURL(
                new Blob([JSON.stringify(t, null, 2)], {
                  type: "application/json",
                }),
              );
              const a = document.createElement("a");
              a.href = url;
              a.download = id + ".json";
              a.click();
              URL.revokeObjectURL(url);
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          导出模板
        </button>
      </div>
      <Field label="模板编号">
        <input
          value={id}
          onChange={(e) => setId(e.target.value)}
          disabled={busy}
        />
      </Field>
    </Panel>
  );
}
