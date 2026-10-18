import { CloudAuthorization } from "./Assets";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, AlertTriangle, Inbox } from "lucide-react";
import {
  request,
  rows,
  pendingGate,
  ownedMedia,
  mediaUrl,
  type RecordData,
  keys,
} from "./api";
import { useQueryClient } from "@tanstack/react-query";
import { Panel, Badge, Empty, ErrorNotice, Field } from "./ui";
import { ReviewAutomation, ReviewAdvisor } from "./ReviewAssistance";
export function actionsFor(project: RecordData) {
  return rows(project.operator?.attention?.actions);
}

export function ReviewFeedback({
  label,
  value,
  onChange,
  choices,
  suggestion,
  maxLength = 4000,
  required = false,
  disabled = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  choices?: RecordData[];
  suggestion?: string;
  maxLength?: number;
  required?: boolean;
  disabled?: boolean;
}) {
  const [selected, setSelected] = useState<RecordData[]>([]);
  const [notes, setNotes] = useState(value);
  const emitted = useRef(value);
  useEffect(() => {
    if (value !== emitted.current) {
      setSelected([]);
      setNotes(value);
      emitted.current = value;
    }
  }, [value]);
  const options =
    choices === undefined && suggestion
      ? [
          {
            id: "all-review",
            label: "按全部审核建议修改",
            feedback: suggestion,
            kind: "all_issues",
          },
        ]
      : rows(choices);
  function change(next: RecordData[], text: string) {
    const combined = [
      ...next.map((c) => c.feedback),
      ...(text.trim() ? [text] : []),
    ].join("\n\n");
    setSelected(next);
    setNotes(text);
    emitted.current = combined;
    onChange(combined);
  }
  function toggle(choice: RecordData) {
    if (selected.some((c) => c.id === choice.id)) {
      change(
        selected.filter((c) => c.id !== choice.id),
        notes,
      );
      return;
    }
    // Keep the selected wording as a draft snapshot if polling changes choices.
    const remaining = selected.filter((c) =>
      choice.kind === "all_issues"
        ? c.kind !== "issue"
        : choice.kind === "issue"
          ? c.kind !== "all_issues"
          : true,
    );
    change([...remaining, { ...choice }], notes);
  }
  return (
    <div className="review-feedback">
      {options.length > 0 && (
        <fieldset className="review-choice-group" disabled={disabled}>
          <legend>选择修改要求（可多选）</legend>
          <p>先选择修改要求，再点击下方按钮提交；文字可以留空。</p>
          <div className="review-choices">
            {options.map((choice) => (
              <button
                key={choice.id}
                type="button"
                className="review-choice"
                data-review-choice={choice.id}
                title={choice.feedback}
                aria-pressed={selected.some((c) => c.id === choice.id)}
                onClick={() => toggle(choice)}
              >
                <span className="review-choice-check" aria-hidden="true">
                  <Check size={13} />
                </span>
                <span>{choice.label}</span>
              </button>
            ))}
          </div>
        </fieldset>
      )}
      {selected.length > 0 && (
        <details className="review-selected">
          <summary>已选择 {selected.length} 项处理意见</summary>
          {selected.map((choice) => (
            <p key={choice.id}>
              <strong>{choice.label}</strong>
              <span>{choice.feedback}</span>
            </p>
          ))}
          <button
            type="button"
            className="text-link"
            disabled={disabled}
            onClick={() => change([], notes)}
          >
            清除选项
          </button>
        </details>
      )}
      <Field
        label={label}
        hint={options.length ? "选择修改要求后，文字可以留空。" : undefined}
      >
        <textarea
          name="feedback"
          rows={3}
          maxLength={maxLength}
          required={required && selected.length === 0}
          disabled={disabled}
          aria-invalid={value.length > maxLength}
          value={notes}
          onChange={(e) => change(selected, e.target.value)}
          placeholder={
            options.length
              ? "补充具体要求（可选）；也可以直接输入你的意见。"
              : "填写具体修改要求。"
          }
        />
        {suggestion && (
          <button
            type="button"
            className="text-link"
            data-action="use-review-feedback"
            disabled={disabled}
            onClick={() => change([], suggestion)}
          >
            填入审核建议
          </button>
        )}
      </Field>
      {value.length > maxLength && (
        <ErrorNotice
          error={`所选要求与补充文字合计超过 ${maxLength} 字，请减少选项或缩短文字。`}
        />
      )}
    </div>
  );
}

export function DecisionCenter({
  project,
  onNotify,
  onTab,
  onShot,
}: {
  project: RecordData;
  onNotify: (message: string) => void;
  onTab: (tab: string) => void;
  onShot?: (id: string) => void;
}) {
  const client = useQueryClient(),
    gate = pendingGate(project),
    attention = project.operator?.attention;
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [feedback, setFeedback] = useState("");
  const [manualFeedback, setManualFeedback] = useState("");
  const [base, setBase] = useState(project.revision),
    [dirty, setDirty] = useState(false);
  const current = useRef(project.revision);
  current.current = project.revision;
  const revision = dirty ? base : project.revision,
    stale = dirty && revision !== project.revision;
  const edit = (value: string) => {
    if (!dirty) {
      setBase(project.revision);
      setDirty(true);
    }
    setFeedback(value);
  };
  async function submit(payload: RecordData, decision = false) {
    if (busy) return;
    if (
      typeof payload.feedback === "string" &&
      payload.feedback.length > 4000
    ) {
      setError("修改要求超过 4000 字，请减少选项或缩短补充文字。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await request(
        "/projects/" + project.id + (decision ? "/decision" : "/action"),
        {
          ...payload,
          expected_revision: payload.expected_revision ?? revision,
        },
      );
      setFeedback("");
      setDirty(false);
      onNotify(
        payload.action === "request_review_advice"
          ? "已安排 AI 分析当前审核问题，建议返回后由你选择。"
          : "操作已提交，制作团队将按当前决定继续。",
      );
      await client.invalidateQueries();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const gateKind = gate?.type;
  const allowed = new Set(
    rows(gate?.options).map((x) => (typeof x === "string" ? x : x.id)),
  );
  const can = (v: string) => allowed.has(v);
  const canonical = Array.isArray(attention?.options);
  const options = rows(
    canonical
      ? attention.options
      : gateKind === "story"
        ? project.proposals
        : project.styles,
  ).filter(
    (c) =>
      allowed.has(c.id) &&
      (gateKind !== "story" ||
        canonical ||
        (project.workflow_version >= 2
          ? c.eligible === true
          : c.eligible !== false)),
  );
  const issues = rows(attention?.issues || project.needs_decision?.issues);
  const suggestion =
    attention?.suggested_feedback ||
    issues.map((i) => i.suggestion || i.description).join("\n");
  if (!attention?.blocking && !gate)
    return (
      <>
        <ReviewAutomation project={project} />
        <Empty title="当前没有人工待办">
          Agent 团队正在自动执行。你可以随时查看任务与产物，并在必要时暂停介入。
        </Empty>
      </>
    );
  const confirm = stale ? (
    <div className="notice decision-draft-stale">
      <div>
        <strong>项目状态已更新，你的意见仍保留。</strong>
        <p>核对当前问题后，再将草稿绑定到新版本。</p>
      </div>
      <button
        className="button"
        type="button"
        data-action="confirm-decision-revision"
        onClick={() => setBase(project.revision)}
      >
        已核对当前状态
      </button>
    </div>
  ) : null;
  const feedbackField = (
    <ReviewFeedback
      label="你的处理意见"
      value={feedback}
      onChange={edit}
      choices={attention?.feedback_choices}
      suggestion={suggestion}
      maxLength={4000}
      disabled={busy}
    />
  );
  const actions = actionsFor(project);
  const primaryReview = actions.find((a) => a.id === "provide_feedback");
  const otherActions = actions.filter(
    (a) =>
      a.id !== primaryReview?.id &&
      !["request_review_advice", "continue_auto_review"].includes(a.id),
  );
  function actionForm(action: RecordData) {
    return (
      <ActionForm
        key={action.id + ":" + project.id}
        action={action}
        project={project}
        busy={busy}
        onFeedbackChange={
          action.id === "provide_feedback" ? setManualFeedback : undefined
        }
        onSubmit={(body) => submit(body)}
        suggestion={suggestion}
      />
    );
  }
  return (
    <Panel
      className="decision-section"
      title={
        attention?.title ||
        (
          {
            story: "选择故事方向",
            style: "选择人物与画风",
            final: "审看并确认终片",
          } as RecordData
        )[gateKind] ||
        "需要你处理"
      }
      description={
        attention?.summary || gate?.message || "选择后，团队继续制作。"
      }
    >
      <div
        data-decision-id={gate?.id || ""}
        data-decision-revision={project.revision}
      >
        <div className="decision-context">
          <Inbox size={17} />
          <span>当前版本 {project.revision} · 所有决定都会留下记录</span>
        </div>
        <ErrorNotice error={error} reserveSpace />
        <ReviewAutomation project={project} />
        <ReviewAdvisor
          project={project}
          busy={busy}
          manualFeedback={manualFeedback}
          onSubmit={(body) => submit(body)}
        />
        {String(project.needs_decision?.code || "").startsWith("cloud_") &&
          project.cloud_quote && (
            <CloudAuthorization
              quote={project.cloud_quote}
              projectRevision={revision}
              busy={busy}
              onSubmit={async (payload) => {
                setBusy(true);
                setError("");
                try {
                  await request(
                    "/projects/" + project.id + "/cloud-authorize",
                    payload,
                  );
                  onNotify("云媒体批次已授权。");
                  await client.invalidateQueries();
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            />
          )}
        {primaryReview && (
          <div className="review-primary-action">
            {actionForm(primaryReview)}
          </div>
        )}
        {issues.length > 0 && (
          <div className="decision-issues">
            <h3>审核问题与依据</h3>
            {issues.map((i, index) => (
              <article className="decision-issue" key={index}>
                <div className="row-between">
                  <Badge
                    status="blocked"
                    label={i.severity === "minor" ? "优化建议" : "审核问题"}
                  />
                  <div>
                    {(i.shot_ids || []).map((id: string) => (
                      <button
                        className="shot-tag"
                        type="button"
                        onClick={() => (onShot ? onShot(id) : onTab("shots"))}
                        key={id}
                      >
                        {id}
                      </button>
                    ))}
                  </div>
                </div>
                <p>{i.description}</p>
                {i.suggestion && (
                  <div className="issue-recommendation">
                    <strong>建议修改</strong>
                    <p>{i.suggestion}</p>
                  </div>
                )}
                {i.evidence && (
                  <details>
                    <summary>查看依据</summary>
                    <p>{i.evidence}</p>
                  </details>
                )}
              </article>
            ))}
          </div>
        )}
        {["story", "style"].includes(gateKind) && (
          <>
            <div className="decision-options">
              {options.map((c, index) => {
                const image = ownedMedia(
                  project.id,
                  c.preview_url || c.image_url || c.preview,
                );
                return (
                  <article className="decision-option" key={c.id}>
                    {gateKind === "style" ? (
                      <div className="choice-image">
                        {image ? (
                          <img src={image} alt={c.name || c.id} />
                        ) : (
                          <span>预览尚未返回</span>
                        )}
                      </div>
                    ) : (
                      <div className="choice-heading">
                        <span>方案 {String(index + 1).padStart(2, "0")}</span>
                        <Badge status="passed" />
                      </div>
                    )}
                    <h3>{c.title || c.name || c.id}</h3>
                    <p>{c.logline || c.description || c.synopsis}</p>
                    {c.hook && (
                      <div className="choice-hook">
                        <small>开场钩子</small>
                        <p>{c.hook}</p>
                      </div>
                    )}
                    <details>
                      <summary>完整方案与审核结论</summary>
                      <p>{c.synopsis || c.description || c.prompt}</p>
                      {c.ending && <p>结局：{c.ending}</p>}
                      {c.review?.summary && <p>{c.review.summary}</p>}
                    </details>
                    <button
                      className="button primary full"
                      disabled={busy}
                      data-action="decide"
                      data-choice={c.id}
                      data-type={gateKind}
                      onClick={() =>
                        submit(
                          {
                            type: gateKind,
                            choice: c.id,
                            decision_id: gate.id,
                          },
                          true,
                        )
                      }
                    >
                      {gateKind === "style" ? "采用这套视觉" : "选择这个故事"}
                      <ArrowRight size={16} />
                    </button>
                  </article>
                );
              })}
            </div>
            {!options.length && (
              <Empty title="当前没有可选择的合格方案">
                查看审核原因，并通过下方操作重新生成；不会跳过审核。
              </Empty>
            )}
            <details className="new-direction">
              <summary>
                {gateKind === "story"
                  ? "这些方向都不合适？"
                  : "重新调整视觉方向"}
              </summary>
              <form
                data-form="repropose"
                data-project-revision={revision}
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!feedback.trim()) {
                    setError("请选择修改方向，或填写新的创作方向。");
                    return;
                  }
                  submit(
                    {
                      type: gateKind,
                      choice:
                        gateKind === "story" ? "__repropose__" : "__restyle__",
                      feedback,
                      decision_id: gate.id,
                    },
                    true,
                  );
                }}
              >
                {confirm}
                {feedbackField}
                <button className="button" disabled={busy || stale}>
                  提交新方向并重新生成
                </button>
              </form>
            </details>
          </>
        )}
        {gateKind === "final" &&
          (ownedMedia(project.id, project.delivery?.video_url) ? (
            <div className="final-review-layout">
              <video
                className="delivery-video"
                controls
                playsInline
                preload="metadata"
                src={ownedMedia(project.id, project.delivery.video_url)}
              />
              <form
                data-form="final"
                data-project-revision={revision}
                onSubmit={(e) => {
                  e.preventDefault();
                  const choice = (
                    (e.nativeEvent as SubmitEvent)
                      .submitter as HTMLButtonElement
                  )?.value;
                  if (!["approve", "revise", "reedit"].includes(choice)) {
                    setError("请明确选择通过、修改或重剪。");
                    return;
                  }
                  if (choice !== "approve" && !feedback.trim()) {
                    setError("请选择修改要求，或填写具体修改意见。");
                    return;
                  }
                  submit(
                    { type: "final", choice, feedback, decision_id: gate.id },
                    true,
                  );
                }}
              >
                <h3>出品人终审</h3>
                <p>请观看成片，核对叙事、人物一致性、对白与字幕。</p>
                {confirm}
                {feedbackField}
                <div className="final-actions">
                  {can("approve") && (
                    <button
                      className="button primary"
                      type="submit"
                      name="choice"
                      value="approve"
                      disabled={busy || stale}
                    >
                      <Check size={16} />
                      确认通过并交付
                    </button>
                  )}
                  {can("revise") && (
                    <button
                      className="button"
                      type="submit"
                      name="choice"
                      value="revise"
                      disabled={busy || stale}
                    >
                      局部修改
                    </button>
                  )}
                  {can("reedit") && (
                    <button
                      className="button"
                      type="submit"
                      name="choice"
                      value="reedit"
                      disabled={busy || stale}
                    >
                      重新剪辑
                    </button>
                  )}
                </div>
              </form>
            </div>
          ) : (
            <Empty
              title="暂时没有可播放的成片"
              action={
                <button className="button" onClick={() => onTab("quality")}>
                  查看制作记录
                </button>
              }
            >
              缺少有效媒体，当前不能批准交付。
            </Empty>
          ))}
        {primaryReview && otherActions.length > 0 ? (
          <details className="review-other-actions">
            <summary>其他处理方式</summary>
            <div className="decision-action-list">
              {otherActions.map(actionForm)}
            </div>
          </details>
        ) : (
          !primaryReview &&
          actions.length > 0 && (
            <div className="decision-action-list">
              {otherActions.map(actionForm)}
            </div>
          )
        )}
        {!["story", "style", "final"].includes(gateKind) &&
          actions.length === 0 && (
            <Empty title="此状态尚无可执行操作">
              请同步当前工作区并检查服务版本，当前服务未返回可执行动作契约。
            </Empty>
          )}
        <div className="decision-inline-error" role="alert">
          {error}
        </div>
      </div>
    </Panel>
  );
}
export function ActionForm({
  action,
  project,
  busy,
  onSubmit,
  suggestion,
  onFeedbackChange,
}: {
  action: RecordData;
  project: RecordData;
  busy: boolean;
  onSubmit: (body: RecordData) => void;
  suggestion?: string;
  onFeedbackChange?: (feedback: string) => void;
}) {
  const [values, setValues] = useState<RecordData>({}),
    [base, setBase] = useState(project.revision),
    [dirty, setDirty] = useState(false),
    [fieldError, setFieldError] = useState("");
  const stale = dirty && base !== project.revision;
  const revision = dirty ? base : project.revision;
  const update = (name: string, value: any) => {
    if (!dirty) {
      setDirty(true);
      setBase(project.revision);
    }
    setValues((v) => ({ ...v, [name]: value }));
    if (name === "feedback") onFeedbackChange?.(value);
  };
  useEffect(() => () => onFeedbackChange?.(""), [onFeedbackChange]);
  return (
    <form
      className="action-form"
      data-form={action.id === "provide_feedback" ? "retry" : action.id}
      data-project-revision={revision}
      onSubmit={(e) => {
        e.preventDefault();
        const fields = Object.fromEntries(
          rows(action.inputs)
            .filter((input) => input.value !== undefined)
            .map((input) => [input.name, input.value]),
        );
        Object.assign(fields, values);
        const tooLong = rows(action.inputs).find(
          (input) =>
            typeof fields[input.name] === "string" &&
            input.maxlength &&
            fields[input.name].length > input.maxlength,
        );
        if (tooLong) {
          setFieldError(
            `${tooLong.label}超过 ${tooLong.maxlength} 字，请减少选项或缩短文字。`,
          );
          return;
        }
        for (const input of rows(action.inputs))
          if (
            input.type === "number" &&
            fields[input.name] !== undefined &&
            fields[input.name] !== ""
          )
            fields[input.name] = Number(fields[input.name]);
        const missing = rows(action.inputs).find(
          (input) =>
            input.required &&
            (fields[input.name] == null ||
              fields[input.name] === "" ||
              (Array.isArray(fields[input.name]) &&
                !fields[input.name].length) ||
              (input.type === "checkbox" && !fields[input.name])),
        );
        if (missing) {
          setFieldError("请填写或选择" + missing.label);
          return;
        }
        setFieldError("");
        onSubmit({ action: action.id, ...fields, expected_revision: revision });
      }}
    >
      <h3>{action.label}</h3>
      <ErrorNotice error={fieldError} reserveSpace />
      {action.impact && <p>{action.impact}</p>}
      {rows(action.inputs).map((input) =>
        input.name === "feedback" && input.type === "textarea" ? (
          <ReviewFeedback
            key={input.name}
            label={input.label || input.name}
            value={values[input.name] ?? input.value ?? ""}
            onChange={(value) => update(input.name, value)}
            choices={
              input.choices ?? project.operator?.attention?.feedback_choices
            }
            suggestion={suggestion}
            maxLength={input.maxlength || 4000}
            required={input.required}
            disabled={busy || action.enabled === false}
          />
        ) : (
          <Field
            label={input.label || input.name}
            hint={input.help || input.description}
            key={input.name}
          >
            {input.type === "textarea" ? (
              <>
                <textarea
                  name={input.name}
                  rows={4}
                  value={values[input.name] ?? input.value ?? ""}
                  onChange={(e) => update(input.name, e.target.value)}
                  required={input.required}
                  maxLength={input.maxlength || 6000}
                />
                {suggestion && input.name === "feedback" && (
                  <button
                    className="text-link"
                    type="button"
                    data-action="use-review-feedback"
                    onClick={() => update(input.name, suggestion)}
                  >
                    填入审核建议
                  </button>
                )}
              </>
            ) : input.type === "select" ? (
              <select
                name={input.name}
                required={input.required}
                value={values[input.name] ?? input.value ?? ""}
                onChange={(e) => update(input.name, e.target.value)}
              >
                <option value="">请选择</option>
                {rows(input.options).map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : input.type === "multiselect" ? (
              <div className="shot-options">
                {rows(input.options).map((o) => (
                  <label className="check" key={o.id}>
                    <input
                      type="checkbox"
                      name={input.name}
                      checked={(values[input.name] || []).includes(o.id)}
                      onChange={(e) =>
                        update(
                          input.name,
                          e.target.checked
                            ? [...(values[input.name] || []), o.id]
                            : (values[input.name] || []).filter(
                                (v: string) => v !== o.id,
                              ),
                        )
                      }
                    />
                    {o.label}
                  </label>
                ))}
              </div>
            ) : input.type === "checkbox" ? (
              <input
                type="checkbox"
                name={input.name}
                checked={!!(values[input.name] ?? input.value)}
                required={input.required}
                onChange={(e) => update(input.name, e.target.checked)}
              />
            ) : (
              <input
                name={input.name}
                type={input.type === "number" ? "number" : "text"}
                min={input.min}
                max={input.max}
                maxLength={input.maxlength}
                value={values[input.name] ?? input.value ?? ""}
                required={input.required}
                onChange={(e) => update(input.name, e.target.value)}
              />
            )}
          </Field>
        ),
      )}
      {stale && (
        <div className="notice decision-draft-stale">
          项目已更新，输入仍保留。
          <button
            className="button small"
            type="button"
            data-action="confirm-decision-revision"
            onClick={() => setBase(project.revision)}
          >
            已核对当前状态
          </button>
        </div>
      )}
      <button
        type="submit"
        className="button"
        disabled={busy || stale || action.enabled === false}
      >
        {action.label}
        <ArrowRight size={14} />
      </button>
      {action.enabled === false && <small>{action.disabled_reason}</small>}
    </form>
  );
}
