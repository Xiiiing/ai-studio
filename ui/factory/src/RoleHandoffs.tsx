import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request, rows, roleName, date, type RecordData } from "./api";
import { Readable } from "./ui";

const states: Record<string, string> = {
  submitted: "待接收校验",
  accepted: "控制层已接收",
  returned: "已退回补料",
};

function FrozenMaterial({
  projectId,
  record,
  stale,
}: {
  projectId: string;
  record: RecordData;
  stale: boolean;
}) {
  const [open, setOpen] = useState(false);
  const content = useQuery({
    queryKey: ["controlled-handoff", projectId, record.id],
    queryFn: () => request(`/projects/${projectId}/handoffs/${record.id}`),
    enabled: open && !stale,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });
  return (
    <>
      <button
        aria-expanded={open}
        disabled={stale}
        onClick={() => setOpen(!open)}
      >
        {open ? "收起交付原文" : "查看交付原文"}
      </button>
      {open && !stale && (
        <div className="handoff-frozen-material">
          {content.isPending ? (
            <p>正在读取交付原文…</p>
          ) : content.isError ? (
            <p role="alert">交付原文暂时无法读取，请重新打开重试。</p>
          ) : (
            <>
              <h5>此次交付的冻结内容</h5>
              <Readable value={content.data?.value} />
              <details>
                <summary>完整 JSON 原文</summary>
                <pre>{JSON.stringify(content.data?.value, null, 2)}</pre>
              </details>
              {content.data?.approval && (
                <>
                  <h5>交付时的审核依据</h5>
                  <Readable value={content.data.approval} />
                </>
              )}
            </>
          )}
        </div>
      )}
    </>
  );
}

interface HandoffProps {
  projectId?: string;
  value?: RecordData;
  roles?: string[];
  pairs?: RecordData[];
  stale?: boolean;
}
export function RoleHandoffs(props: HandoffProps) {
  return props.value && props.projectId ? (
    <HandoffList {...props} projectId={props.projectId} value={props.value} />
  ) : null;
}

function HandoffList({
  projectId,
  value,
  roles,
  pairs,
  stale = false,
}: {
  projectId?: string;
  value?: RecordData;
  roles?: string[];
  pairs?: RecordData[];
  stale?: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const [show, setShow] = useState(false);
  const page = useQuery({
    queryKey: ["controlled-handoffs", projectId, offset],
    queryFn: () =>
      request(`/projects/${projectId}/handoffs?offset=${offset}&limit=30`),
    enabled: !!projectId && !!value && offset > 0 && show && !stale,
    retry: false,
    refetchOnWindowFocus: false,
  });
  if (!value || !projectId) return null;
  const current = offset ? page.data : value;
  const records = rows(current?.handoffs).filter((r) =>
    pairs
      ? pairs.some((p) => p.source === r.source && p.target === r.target)
      : !roles?.length || roles.includes(r.source) || roles.includes(r.target),
  );
  return (
    <section className="controlled-handoffs" aria-label="控制层交接与补料">
      <h4>控制层交接与补料</h4>
      <p className="muted">
        记录谁把哪个版本的内容交给谁。岗位职责、输入内容匹配与实际交接分别展示；接收通过仍需后续审核。
      </p>
      {value.history_state === "not_recorded" ? (
        <p className="notice">
          此项目尚未记录受控交接；历史资料不补记为实际交接。
        </p>
      ) : (
        <>
          <button aria-expanded={show} onClick={() => setShow(!show)}>
            {show ? "收起交接记录" : `查看交接记录 · ${value.total} 条`}
          </button>
          {show && (
            <>
              {offset > 0 && page.isPending ? (
                <p>正在读取交接记录…</p>
              ) : page.isError ? (
                <p role="alert">交接记录暂时无法读取。</p>
              ) : records.length ? (
                records.map((r) => {
                  const supplement = rows(current?.requests).find(
                    (q) => q.id === r.request_id || q.id === r.resolves,
                  );
                  return (
                    <article
                      className="controlled-handoff"
                      key={r.id}
                      data-controlled-handoff={r.id}
                    >
                      <header>
                        <strong>
                          {roleName(r.source)} → {roleName(r.target)}
                        </strong>
                        <span>{states[r.state] || r.state}</span>
                      </header>
                      <p className="muted">
                        {r.current_version ? "当前业务版本" : "历史业务版本"} ·{" "}
                        {date(r.created_at)} ·{" "}
                        {(r.scope || []).join("、") || "本次交付范围"}
                      </p>
                      <details>
                        <summary>版本与校验依据</summary>
                        <Readable value={r.version} />
                        <p>
                          交接编号：<code>{r.id}</code>
                        </p>
                        <p>
                          内容校验值：<code>{r.content_sha256}</code>
                        </p>
                        <p>
                          来源校验值：<code>{r.source_sha256}</code>
                        </p>
                      </details>
                      {supplement && (
                        <div className="handoff-supplement">
                          <strong>
                            {supplement.state === "supplemented"
                              ? "补料已交回，仍需分镜及复审"
                              : "等待指定岗位补料"}
                          </strong>
                          <p>
                            {roleName(supplement.source)} →{" "}
                            {roleName(supplement.target)} ·{" "}
                            {(supplement.scope || []).join("、") ||
                              "本次交付范围"}
                          </p>
                          <ul>
                            {rows(supplement.issues).map((issue, i) => (
                              <li key={i}>{issue.description}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <ul className="handoff-events">
                        {rows(r.events).map((e, i) =>
                          e.action === "call_recorded" ? (
                            <li key={i}>
                              实际调用：{e.call_id} ·{" "}
                              {e.status === "succeeded" ? "已获得响应" : "失败"}{" "}
                              ·{" "}
                              {e.delivery === "response_received"
                                ? "已收到模型响应"
                                : e.delivery === "attempted"
                                  ? "已尝试提交"
                                  : "提交证据未记录"}
                            </li>
                          ) : e.action === "cached_result_reused" ? (
                            <li key={i}>
                              复用已有结果，未发起新的模型调用 ·{" "}
                              {e.origin_call_id || "原始调用来源未知"}
                            </li>
                          ) : null,
                        )}
                      </ul>
                      {!rows(r.events).some((e) =>
                        ["call_recorded", "cached_result_reused"].includes(
                          e.action,
                        ),
                      ) && (
                        <p className="muted">
                          尚无接收方模型调用或缓存复用记录。
                        </p>
                      )}
                      <FrozenMaterial
                        projectId={projectId}
                        record={r}
                        stale={stale}
                      />
                    </article>
                  );
                })
              ) : (
                <p className="muted">
                  本页暂无与所选岗位或连线对应的受控交接。
                </p>
              )}
              {(value.total > 30 || offset > 0) && (
                <nav aria-label="交接分页">
                  <button
                    disabled={offset === 0 || stale}
                    onClick={() => setOffset(Math.max(0, offset - 30))}
                  >
                    上一页
                  </button>
                  <span>第 {Math.floor(offset / 30) + 1} 页</span>
                  <button
                    disabled={
                      offset + 30 >= (current?.total || value.total) || stale
                    }
                    onClick={() => setOffset(offset + 30)}
                  >
                    下一页
                  </button>
                </nav>
              )}
            </>
          )}
        </>
      )}
      {stale && (
        <p className="notice">项目状态已变化，请同步后再查看交付原文。</p>
      )}
    </section>
  );
}
