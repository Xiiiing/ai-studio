import { SelectionGroup } from "./SelectionGroup";
import { useQuery } from "@tanstack/react-query";
import { Activity, Info, Palette, Plug } from "lucide-react";
import { AppearanceSettings } from "./AppearanceSettings";
import { Models } from "./LazyViews";
import { PageHeader } from "./PageHeader";
import { Badge, ErrorNotice, Loading, Panel } from "./ui";
import { date, request, rows, type RecordData } from "./api";
import "./system-settings.css";

const categories = [
  {
    id: "appearance",
    label: "外观显示",
    description: "显示模式与主题色",
    Icon: Palette,
  },
  {
    id: "models",
    label: "模型与服务",
    description: "连接、分工与高级工具",
    Icon: Plug,
  },
  {
    id: "runtime",
    label: "运行状态",
    description: "执行器、队列与资源",
    Icon: Activity,
  },
  {
    id: "about",
    label: "关于与版本",
    description: "当前服务与版本信息",
    Icon: Info,
  },
] as const;

const environmentNames: Record<string, string> = {
  stable: "稳定版",
  test: "测试版",
  development: "开发环境",
  unspecified: "未标记",
};
const value = (input: unknown) =>
  typeof input === "number" && Number.isFinite(input) ? input : "未提供";

export function SystemSettings({
  section,
  onSection,
  overview,
  overviewError,
  retryOverview,
  notify,
}: {
  section: string;
  onSection: (section: string) => void;
  overview?: RecordData;
  overviewError: Error | null;
  retryOverview: () => void;
  notify: (message: string) => void;
}) {
  const selected =
    categories.find((item) => item.id === section) || categories[0];
  const system = useQuery({
    queryKey: ["system-info"],
    queryFn: () => request("/system"),
    staleTime: 60000,
    enabled: selected.id === "about",
  });
  const queue = overview?.queue || {},
    capabilities = overview?.capabilities || {};
  const worker = capabilities.worker;
  return (
    <div className="system-settings">
      <PageHeader
        title="系统设置"
        description="管理界面外观、制作服务，查看当前系统状态与版本。"
      />
      <div className="settings-layout">
        <SelectionGroup
          as="nav"
          className="settings-navigation"
          aria-label="系统设置分类"
          value={selected.id}
        >
          {categories.map(({ id, label, description, Icon }) => (
            <button
              key={id}
              type="button"
              aria-current={selected.id === id ? "page" : undefined}
              aria-label={label}
              aria-controls="settings-content"
              className="settings-category"
              onClick={() => onSection(id)}
            >
              <Icon size={18} aria-hidden="true" />
              <span>
                <strong>{label}</strong>
                <small>{description}</small>
              </span>
            </button>
          ))}
        </SelectionGroup>
        <section
          id="settings-content"
          className="settings-content"
          aria-label={selected.label}
        >
          {selected.id === "appearance" && <AppearanceSettings />}
          {selected.id === "models" && (
            <>
              <div className="settings-section-heading">
                <h2>模型与服务</h2>
                <p>管理制作所用的模型连接、角色分工和高级工具。</p>
              </div>
              <Models embedded notify={notify} />
            </>
          )}
          {selected.id === "runtime" && (
            <>
              <ErrorNotice error={overviewError} />
              {overviewError && (
                <button className="button" onClick={retryOverview}>
                  重新读取运行状态
                </button>
              )}
              {!overview && !overviewError && <Loading />}
              {overview && (
                <>
                  <Panel title="任务与执行器">
                    <dl className="settings-facts">
                      <div>
                        <dt>执行器</dt>
                        <dd>
                          <Badge
                            status={
                              worker?.available === true
                                ? "succeeded"
                                : "pending"
                            }
                            label={
                              worker?.available === true
                                ? "在线"
                                : worker?.available === false
                                  ? "未就绪"
                                  : "未提供"
                            }
                          />
                        </dd>
                      </div>
                      <div>
                        <dt>正在执行</dt>
                        <dd>{value(queue.running)}</dd>
                      </div>
                      <div>
                        <dt>排队任务</dt>
                        <dd>{value(queue.queued)}</dd>
                      </div>
                      <div>
                        <dt>等待远端结果</dt>
                        <dd>{value(queue.waiting_remote)}</dd>
                      </div>
                      <div>
                        <dt>等待制作资源</dt>
                        <dd>{value(queue.waiting_resources)}</dd>
                      </div>
                      <div>
                        <dt>执行并发上限</dt>
                        <dd>{value(queue.worker_concurrency)}</dd>
                      </div>
                      <div>
                        <dt>任务持久化</dt>
                        <dd>
                          {queue.durable === true
                            ? "已启用"
                            : queue.durable === false
                              ? "未启用"
                              : "未提供"}
                        </dd>
                      </div>
                      <div>
                        <dt>执行器最近心跳</dt>
                        <dd>
                          {worker?.heartbeat
                            ? date(worker.heartbeat)
                            : "未提供"}
                        </dd>
                      </div>
                    </dl>
                  </Panel>
                  <Panel title="制作资源">
                    <div className="settings-resource-list">
                      {[
                        ["llm", "文本创作"],
                        ["image", "图像制作"],
                        ["video", "视频制作"],
                        ["audio", "配音"],
                        ["quality", "质量审核"],
                      ].map(([id, label]) => {
                        const capability = capabilities[id];
                        return (
                          <article key={id}>
                            <div>
                              <h3>{label}</h3>
                              <p>{capability?.model || "未提供模型信息"}</p>
                            </div>
                            <Badge
                              status={
                                capability?.available === true
                                  ? "succeeded"
                                  : "pending"
                              }
                              label={
                                capability?.available === true
                                  ? "可用"
                                  : capability?.available === false
                                    ? "当前不可用"
                                    : "待验证"
                              }
                            />
                          </article>
                        );
                      })}
                    </div>
                    <p className="settings-note">
                      连接与资源状态来自最近一次系统观测；制作结果可在对应任务中查看。
                    </p>
                  </Panel>
                  <Panel title="GPU 资源">
                    {!rows(capabilities.gpus).length ? (
                      <p>{capabilities.gpu_message || "暂无 GPU 观测数据。"}</p>
                    ) : (
                      <div className="settings-resource-list">
                        {rows(capabilities.gpus).map((gpu, index) => (
                          <article key={gpu.index ?? index}>
                            <div>
                              <h3>GPU {gpu.index ?? index}</h3>
                              <p>{gpu.name || "未提供设备名称"}</p>
                            </div>
                            <span>
                              {typeof gpu.free_mb === "number" &&
                              typeof gpu.total_mb === "number"
                                ? `${(gpu.free_mb / 1024).toFixed(1)} / ${(gpu.total_mb / 1024).toFixed(1)} GB 可用`
                                : "未提供显存数据"}
                            </span>
                          </article>
                        ))}
                      </div>
                    )}
                  </Panel>
                </>
              )}
            </>
          )}
          {selected.id === "about" && (
            <>
              <ErrorNotice error={system.error} />
              {system.isPending && !system.error && <Loading />}
              {system.error && (
                <button
                  className="button"
                  onClick={() => {
                    void system.refetch();
                  }}
                >
                  重新读取版本信息
                </button>
              )}
              {system.data && (
                <Panel title="关于映序">
                  <dl className="settings-facts">
                    <div>
                      <dt>服务版本</dt>
                      <dd>{system.data.version || "未提供"}</dd>
                    </div>
                    <div>
                      <dt>部署环境</dt>
                      <dd>
                        {environmentNames[system.data.environment] || "未标记"}
                      </dd>
                    </div>
                    <div>
                      <dt>代码提交</dt>
                      <dd>
                        <code>{system.data.source_commit || "未提供"}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>服务启动时间</dt>
                      <dd>
                        {system.data.started_at
                          ? date(system.data.started_at)
                          : "未提供"}
                      </dd>
                    </div>
                    <div>
                      <dt>当前访问入口</dt>
                      <dd>{window.location.origin}</dd>
                    </div>
                    <div>
                      <dt>外观偏好保存位置</dt>
                      <dd>当前浏览器</dd>
                    </div>
                  </dl>
                  <p className="settings-note">
                    版本信息由当前连接的服务提供。显示模式与主题色在当前浏览器即时生效。
                  </p>
                </Panel>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
