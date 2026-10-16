import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  LayoutDashboard,
  FolderKanban,
  Inbox,
  Workflow as WorkflowIcon,
  Settings2,
  Activity,
  Plus,
  RefreshCw,
  Clapperboard,
  ArrowUpRight,
  ArrowRight,
  Cpu,
  Bot,
  Search,
  Menu,
  CheckCircle2,
  Clock3,
  Play,
  Pause,
  Hand,
} from "lucide-react";
import {
  request,
  keys,
  rows,
  number,
  roleName,
  date,
  mediaUrl,
  ownedMedia,
  pendingGate,
  names,
  text,
  type RecordData,
} from "./api";
import {
  Heading,
  Panel,
  Badge,
  Empty,
  Loading,
  ErrorNotice,
  Field,
  Modal,
  Readable,
  Artifact,
} from "./ui";
import { Workflow, Team } from "./Workflow";
import { DecisionCenter, ActionForm } from "./Decisions";
import { ReviewAutomation } from "./ReviewAssistance";
import { Models } from "./Models";
import { Shots, Series, Costs } from "./Assets";
type Route = { view: string; project: string; tab: string };
function readRoute(): Route {
  const p = new URLSearchParams(location.hash.slice(1));
  return {
    view: p.get("view") || "dashboard",
    project: p.get("project") || "",
    tab: p.get("tab") || "overview",
  };
}
const links = [
  ["dashboard", "制片总览", LayoutDashboard],
  ["projects", "全部项目", FolderKanban],
  ["inbox", "待办决策", Inbox],
  ["workflow", "Agent 指挥中心", Bot],
  ["models", "模型与连接", Settings2],
  ["activity", "运行记录", Activity],
] as const;
export default function App() {
  const client = useQueryClient(),
    [route, setRoute] = useState(readRoute),
    [create, setCreate] = useState(false),
    [mobile, setMobile] = useState(false),
    [toast, setToast] = useState("");
  const overview = useQuery({
      queryKey: keys.overview,
      queryFn: () => request("/console"),
      refetchInterval: 5000,
    }),
    models = useQuery({
      queryKey: keys.models,
      queryFn: () => request("/models"),
      staleTime: 60000,
    }),
    spec = useQuery({
      queryKey: keys.workflow,
      queryFn: () => request("/workflow"),
      staleTime: 60000,
    }),
    profiles = useQuery({
      queryKey: keys.profiles,
      queryFn: () => request("/production-profiles"),
      staleTime: 60000,
    });
  useEffect(() => {
    const listener = () => setRoute(readRoute());
    window.addEventListener("hashchange", listener);
    return () => window.removeEventListener("hashchange", listener);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 7000);
    return () => clearTimeout(timer);
  }, [toast]);
  function navigate(view: string, project = "", tab = "overview") {
    const p = new URLSearchParams();
    if (project) {
      p.set("project", project);
      p.set("tab", tab);
    } else if (view !== "dashboard") p.set("view", view);
    location.hash = p.toString();
    setRoute({ view, project, tab });
    setMobile(false);
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  async function refresh() {
    await client.invalidateQueries();
  }
  const projects = rows(overview.data?.projects),
    metrics = overview.data?.metrics || {};
  const openProject = (id: string, tab = "overview") =>
    navigate("dashboard", id, tab);
  return (
    <>
      <a className="skip-link" href="#workspace">
        跳至工作区
      </a>
      <div className={"app-shell " + (mobile ? "navigation-open" : "")}>
        <aside className="sidebar" aria-label="工作室导航">
          <a className="brand" href="#" onClick={() => navigate("dashboard")}>
            <span className="brand-mark">
              <Clapperboard size={22} />
            </span>
            <span>
              映序<small>AI PRODUCTION STUDIO</small>
            </span>
          </a>
          <button
            className="button primary new-project"
            data-action="new-project"
            onClick={() => setCreate(true)}
          >
            <Plus size={16} />
            新建制作
          </button>
          <div className="sidebar-label">工作室</div>
          <nav className="main-nav">
            {links.map(([view, label, Icon]) => (
              <button
                key={view}
                className={
                  "nav-item " +
                  (!route.project && route.view === view ? "active" : "")
                }
                data-action={
                  view === "models"
                    ? "models"
                    : view === "dashboard"
                      ? "dashboard"
                      : "view"
                }
                data-view={view}
                aria-current={
                  !route.project && route.view === view ? "page" : undefined
                }
                onClick={() => navigate(view)}
              >
                <Icon size={17} />
                {label}
                {view === "inbox" && (
                  <span className="nav-count">{metrics.attention || 0}</span>
                )}
              </button>
            ))}
          </nav>
          <div className="sidebar-label row-between">
            <span>最近制作</span>
            <span>{metrics.projects ?? projects.length}</span>
          </div>
          <div id="project-list" className="project-list">
            {projects.slice(0, 10).map((p) => (
              <button
                key={p.id}
                data-id={p.id}
                className={
                  "project-item " + (route.project === p.id ? "active" : "")
                }
                onClick={() => openProject(p.id)}
              >
                <span className="project-dot" />
                <span>
                  <strong>{p.title || "未命名制作"}</strong>
                  <small>
                    {p.current_activity || names[p.status] || p.status}
                  </small>
                </span>
              </button>
            ))}
            {!projects.length && !overview.isPending && (
              <p className="sidebar-empty">从一段故事需求开始。</p>
            )}
          </div>
          <div className="sidebar-bottom">
            <span className="workspace-symbol">
              <Bot size={17} />
            </span>
            <div>
              <strong>AI 制片工作室</strong>
              <small>自主协作 · 人工掌舵</small>
            </div>
          </div>
        </aside>
        <div className="main-shell">
          <header className="topbar">
            <div className="topbar-context">
              <button
                className="icon-button mobile-navigation"
                data-action="toggle-navigation"
                aria-label="展开导航"
                onClick={() => setMobile(!mobile)}
              >
                <Menu size={20} />
              </button>
              <span className="breadcrumb">
                工作室 <span>/</span>{" "}
                {route.project
                  ? "制作项目"
                  : links.find((l) => l[0] === route.view)?.[1]}
              </span>
            </div>
            <div className="topbar-right">
              <span
                className={
                  "sync-state " + (overview.isError ? "error-text" : "")
                }
              >
                <i />
                {overview.isError
                  ? "同步中断"
                  : overview.isFetching
                    ? "正在同步"
                    : "已同步"}
              </span>
              <button
                className="icon-button"
                data-action="refresh"
                aria-label="刷新项目"
                onClick={refresh}
              >
                <RefreshCw size={17} />
              </button>
              <span className="producer-avatar">出</span>
            </div>
          </header>
          <main id="workspace" tabIndex={-1}>
            {route.project ? (
              <Project
                key={route.project}
                id={route.project}
                tab={route.tab}
                onTab={(tab) => navigate("dashboard", route.project, tab)}
                onBack={() => navigate("projects")}
                notify={setToast}
                models={models.data}
              />
            ) : route.view === "models" ? (
              <Models notify={setToast} />
            ) : (
              <>
                <ErrorNotice error={overview.error} />
                {!overview.data ? (
                  <Loading />
                ) : route.view === "dashboard" ? (
                  <Dashboard
                    overview={overview.data}
                    openProject={openProject}
                    navigate={navigate}
                    create={() => setCreate(true)}
                  />
                ) : route.view === "projects" ? (
                  <Projects
                    overview={overview.data}
                    openProject={openProject}
                    create={() => setCreate(true)}
                  />
                ) : route.view === "inbox" ? (
                  <InboxPage openProject={openProject} />
                ) : route.view === "activity" ? (
                  <>
                    <Heading
                      eyebrow="OPERATIONS"
                      title="运行记录"
                      description="从项目事件定位执行、审核与异常，所有操作均保留历史。"
                    />
                    <Panel title="最近运行事件">
                      <ActivityList
                        events={rows(overview.data.activity)}
                        openProject={openProject}
                        filterable
                      />
                    </Panel>
                  </>
                ) : (
                  <>
                    <Heading
                      eyebrow="AGENT COMMAND CENTER"
                      title="Agent 指挥中心"
                      description="查看团队如何分工、交接与独立审核，进入项目追踪实际执行。"
                    />
                    <Panel title="当前执行任务">
                      <RunningTasks
                        tasks={rows(overview.data.running_tasks)}
                        openProject={openProject}
                      />
                    </Panel>
                    <Panel
                      title="标准制作流程"
                      description="读取服务器工作流定义；项目中的实际运行和返修记录分别展示。"
                    >
                      <Workflow
                        graph={
                          spec.data?.production_graph ||
                          spec.data?.workflow || {
                            nodes: rows(spec.data?.nodes),
                            edges: rows(spec.data?.edges),
                          }
                        }
                      />
                    </Panel>
                    <Panel title="已配置的 Agent">
                      <div className="agent-grid">
                        {rows(models.data?.roles).map((r) => (
                          <article className="agent-card" key={r.role}>
                            <span className="agent-avatar">
                              <Bot size={20} />
                            </span>
                            <h3>{r.label || roleName(r.role)}</h3>
                            <p>
                              {r.model ||
                                models.data?.editable_profiles?.[r.profile]
                                  ?.model ||
                                "自动编排 / 尚未分配"}
                            </p>
                            <small>
                              {r.requires_vision
                                ? "需要图像理解"
                                : "工作流角色"}
                            </small>
                          </article>
                        ))}
                      </div>
                    </Panel>
                  </>
                )}
              </>
            )}
          </main>
          <footer className="workspace-footer">
            <span>映序 · AI 制片工作室</span>
            <span>创作、审核与交付记录可追溯</span>
          </footer>
        </div>
      </div>
      {toast && (
        <div id="toast" className="toast" role="status">
          {toast}
        </div>
      )}
      {create && (
        <CreateProject
          catalog={profiles.data}
          inventory={models.data}
          onClose={() => setCreate(false)}
          onCreated={(p) => {
            setCreate(false);
            setToast("制作项目已建立，AI 团队开始策划。");
            openProject(p.id);
          }}
        />
      )}
    </>
  );
}
function Dashboard({
  overview: o,
  openProject,
  navigate,
  create,
}: {
  overview: RecordData;
  openProject: (id: string, tab?: string) => void;
  navigate: (view: string) => void;
  create: () => void;
}) {
  const projects = rows(o.projects),
    m = o.metrics || {},
    running = rows(o.running_tasks);
  const stats = [
    ["正在执行", m.running ?? o.queue?.running ?? 0, "实际执行中的任务", Play],
    [
      "等待你处理",
      m.attention ?? projects.filter((p) => p.attention?.blocking).length,
      "明确决策与阻断处理",
      Inbox,
    ],
    [
      "制作队列",
      number(m.queued ?? o.queue?.queued) + number(m.waiting_remote),
      "排队与云端生成",
      Clock3,
    ],
    ["已交付作品", m.delivered ?? 0, "已确认的最终成片", CheckCircle2],
  ] as const;
  return (
    <>
      <Heading
        eyebrow="PRODUCTION OVERVIEW"
        title="制片总览"
        description="让创作自动推进，把注意力留给真正需要你的决定。"
        action={
          <button className="button primary" onClick={create}>
            <Plus size={16} />
            新建制作
          </button>
        }
      />
      <section className="console-metrics">
        {stats.map(([label, value, detail, Icon], i) => (
          <article className="metric-card" key={label}>
            <div className="row-between">
              <span>{label}</span>
              <Icon size={17} />
            </div>
            <strong className="metric-number" data-metric={i}>
              {value}
            </strong>
            <small>{detail}</small>
          </article>
        ))}
      </section>
      <div className="dashboard-grid">
        <Panel
          title="制作中的任务"
          description={
            running.length ? "团队正在推进以下任务" : "查看项目当前阶段与下一步"
          }
          action={
            <button className="text-link" onClick={() => navigate("projects")}>
              全部项目
              <ArrowUpRight size={14} />
            </button>
          }
          className="dashboard-main"
        >
          {running.length ? (
            <RunningTasks tasks={running} openProject={openProject} />
          ) : (
            <ProjectTable
              projects={projects
                .filter((p) => !["approved", "cancelled"].includes(p.status))
                .slice(0, 5)}
              openProject={openProject}
            />
          )}
        </Panel>
        <Panel
          title="需要你的决定"
          description="每项待办都有明确的原因与下一步"
          action={
            <button className="text-link" onClick={() => navigate("inbox")}>
              全部待办
              <ArrowUpRight size={14} />
            </button>
          }
          className="dashboard-inbox"
        >
          <Attention
            projects={rows(o.attention_projects || o.projects)}
            openProject={openProject}
            limit={3}
          />
        </Panel>
        <Panel title="最近动态">
          <ActivityList
            events={rows(o.activity).slice(0, 5)}
            openProject={openProject}
          />
        </Panel>
        <Panel title="制作资源" description="已记录用量与服务快照">
          <div className="resource-summary">
            <Cpu size={24} />
            <div>
              <strong>
                {number(m.gpu_minutes).toFixed(1)}
                <small> GPU 分钟</small>
              </strong>
              <p>累计项目用量</p>
            </div>
            <div>
              <strong>
                {rows(o.capabilities?.gpus).length}
                <small> 张</small>
              </strong>
              <p>返回状态的 GPU</p>
            </div>
          </div>
          <div className="gpu-grid">
            {rows(o.capabilities?.gpus).map((g) => (
              <div key={g.index}>
                <div className="row-between">
                  <small>GPU {g.index}</small>
                  <small>
                    {g.free_mb == null
                      ? "待检测"
                      : (number(g.free_mb) / 1024).toFixed(1) + " GB 空闲"}
                  </small>
                </div>
                <div className="progress-track">
                  <span
                    style={{
                      width:
                        Math.max(
                          0,
                          Math.min(
                            100,
                            100 -
                              (number(g.free_mb) /
                                number(g.total_mb || 24576)) *
                                100,
                          ),
                        ) + "%",
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
          {!rows(o.capabilities?.gpus).length && (
            <p className="muted">实测资源状态尚未返回。</p>
          )}
        </Panel>
      </div>
      {rows(o.recent_deliveries).length > 0 && (
        <Panel title="最近交付">
          <div className="delivery-grid">
            {rows(o.recent_deliveries).map((d) => (
              <article key={d.project_id}>
                <span className="delivery-icon">
                  <FilmIcon />
                </span>
                <h3>{d.title}</h3>
                <p>{date(d.accepted_at)}</p>
                <button
                  className="text-link"
                  onClick={() => openProject(d.project_id, "delivery")}
                >
                  查看成片
                  <ArrowUpRight size={15} />
                </button>
              </article>
            ))}
          </div>
        </Panel>
      )}
    </>
  );
}
function FilmIcon() {
  return <Clapperboard size={25} />;
}
function ProjectTable({
  projects,
  openProject,
}: {
  projects: RecordData[];
  openProject: (id: string, tab?: string) => void;
}) {
  return projects.length ? (
    <div className="table-scroll">
      <table className="console-project-table">
        <thead>
          <tr>
            <th>制作项目</th>
            <th>当前阶段</th>
            <th>镜头进度</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {projects.map((p) => (
            <tr key={p.id}>
              <td>
                <button
                  className="project-table-title"
                  data-id={p.id}
                  onClick={() => openProject(p.id)}
                >
                  <span className="project-thumb">
                    <Clapperboard size={22} />
                  </span>
                  <span>
                    <strong>{p.title || "未命名制作"}</strong>
                    <small>{p.current_activity || "等待调度"}</small>
                  </span>
                </button>
              </td>
              <td>
                <Badge status={p.status} />
              </td>
              <td>
                <strong>
                  {p.shot_videos || 0}
                  <small> / {p.shot_total || rows(p.shots).length} 镜</small>
                </strong>
                <div className="progress-track">
                  <span
                    style={{ width: Math.min(100, number(p.progress)) + "%" }}
                  />
                </div>
              </td>
              <td>
                <button
                  className="text-link"
                  onClick={() =>
                    openProject(
                      p.id,
                      p.attention?.blocking ? "decisions" : "overview",
                    )
                  }
                >
                  {p.attention?.blocking ? "处理待办" : "打开"}
                  <ArrowUpRight size={15} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty title="当前没有进行中的制作">
      新建项目后，团队会从研究和故事提案开始。
    </Empty>
  );
}
function Projects({
  overview,
  openProject,
  create,
}: {
  overview: RecordData;
  openProject: (id: string, tab?: string) => void;
  create: () => void;
}) {
  const [filter, setFilter] = useState("all"),
    [search, setSearch] = useState(""),
    [offset, setOffset] = useState(0);
  const page = useQuery({
    queryKey: ["project-page", offset],
    queryFn: () => request("/console?offset=" + offset + "&limit=100"),
  });
  const snapshot = page.data || overview;
  const projects = rows(snapshot.projects).filter(
    (p) =>
      (filter === "all" ||
        (filter === "attention" && p.attention?.blocking) ||
        (filter === "delivered" && p.status === "approved") ||
        (filter === "active" &&
          !["approved", "cancelled", "blocked", "paused"].includes(
            p.status,
          ))) &&
      (!search ||
        [p.title, p.id, p.brief].some((v) =>
          String(v || "")
            .toLowerCase()
            .includes(search.toLowerCase()),
        )),
  );
  return (
    <>
      <Heading
        eyebrow="PRODUCTIONS"
        title="全部项目"
        description="从创作需求到成片，集中管理每一部制作。"
        action={
          <button className="button primary" onClick={create}>
            <Plus size={16} />
            新建制作
          </button>
        }
      />
      <Panel>
        <div className="project-toolbar">
          <div className="segmented">
            {Object.entries({
              all: "全部项目",
              active: "制作中",
              attention: "需要处理",
              delivered: "已交付",
            }).map(([v, l]) => (
              <button
                key={v}
                className={filter === v ? "selected" : ""}
                onClick={() => setFilter(v)}
              >
                {l}
              </button>
            ))}
          </div>
          <label className="search-box">
            <Search size={16} />
            <input
              data-console-search
              aria-label="搜索项目"
              placeholder="搜索名称或需求"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
        {projects.length ? (
          <ProjectTable projects={projects} openProject={openProject} />
        ) : (
          <div>
            <table className="console-project-table">
              <tbody />
            </table>
            <Empty title={search ? "没有匹配的项目" : "暂无此类项目"} />
          </div>
        )}
        <Pagination
          pagination={snapshot.pagination}
          offset={offset}
          onOffset={setOffset}
        />
        <ErrorNotice error={page.error} />
      </Panel>
    </>
  );
}
function Attention({
  projects,
  openProject,
  limit = 100,
}: {
  projects: RecordData[];
  openProject: (id: string, tab?: string) => void;
  limit?: number;
}) {
  const items = projects.filter((p) => p.attention?.blocking).slice(0, limit);
  return items.length ? (
    <div className="attention-rows">
      {items.map((p) => (
        <article className="attention-row" key={p.id}>
          <span className="attention-icon">
            <Inbox size={19} />
          </span>
          <div>
            <small>{p.title}</small>
            <h3>{p.attention.title}</h3>
            <p>{p.attention.summary}</p>
          </div>
          <button
            className="button small"
            onClick={() =>
              openProject(p.id, p.attention.target_tab || "decisions")
            }
          >
            {p.attention.action_label || "处理"}
            <ArrowRight size={14} />
          </button>
        </article>
      ))}
    </div>
  ) : (
    <Empty title="当前没有需要你处理的事项">
      Agent 团队会在需要选择或遇到阻断时通知你。
    </Empty>
  );
}
function RunningTasks({
  tasks,
  openProject,
}: {
  tasks: RecordData[];
  openProject: (id: string, tab?: string) => void;
}) {
  return tasks.length ? (
    <div className="running-tasks">
      {tasks.map((t, i) => (
        <article key={t.project_id + ":" + i}>
          <span className="running-orb">
            <Bot size={21} />
          </span>
          <div>
            <h3>{t.title}</h3>
            <p>{t.activity || t.stage}</p>
            <small>
              {t.elapsed_seconds == null
                ? ""
                : Math.floor(t.elapsed_seconds / 60) + " 分钟"}{" "}
              · {date(t.started_at)}
            </small>
          </div>
          <Badge status="running" />
          <button
            className="icon-button"
            aria-label={"查看 " + t.title}
            onClick={() => openProject(t.project_id)}
          >
            <ArrowUpRight size={17} />
          </button>
        </article>
      ))}
    </div>
  ) : (
    <Empty title="当前没有执行中的任务">有新需求时，AI 团队会自动接续。</Empty>
  );
}
function ActivityList({
  events,
  openProject,
  filterable = false,
}: {
  events: RecordData[];
  openProject: (id: string, tab?: string) => void;
  filterable?: boolean;
}) {
  const [filter, setFilter] = useState(false),
    items = events.filter(
      (e) =>
        !filter ||
        ["error", "decision_required", "review_blocked"].includes(e.kind),
    );
  return (
    <>
      {filterable && (
        <div className="segmented activity-filter">
          <button
            className={!filter ? "selected" : ""}
            onClick={() => setFilter(false)}
          >
            全部事件
          </button>
          <button
            className={filter ? "selected" : ""}
            onClick={() => setFilter(true)}
          >
            问题与待办
          </button>
        </div>
      )}
      <div className="activity-list">
        {items.map((e, i) => (
          <article key={e.project_id + ":" + e.at + ":" + i}>
            <span
              className={
                "activity-dot " +
                (["error", "review_blocked"].includes(e.kind) ? "problem" : "")
              }
            />
            <div>
              <p>{e.message}</p>
              <button
                className="text-link"
                onClick={() => openProject(e.project_id, "quality")}
              >
                {e.title || e.project_id}
              </button>
            </div>
            <time>{date(e.at)}</time>
          </article>
        ))}
        {!items.length && <Empty title="暂无运行记录" />}
      </div>
    </>
  );
}
function Project({
  id,
  tab,
  onTab,
  onBack,
  notify,
  models,
}: {
  id: string;
  tab: string;
  onTab: (tab: string) => void;
  onBack: () => void;
  notify: (v: string) => void;
  models?: RecordData;
}) {
  const client = useQueryClient(),
    workspace = useQuery({
      queryKey: keys.workspace(id),
      queryFn: async () => {
        const data = await request("/projects/" + id + "/workspace");
        if (data.project?.id !== id || !Number.isInteger(data.revision))
          throw new Error("项目快照无效，保留上次记录。");
        const previous = client.getQueryData<RecordData>(keys.workspace(id));
        if (previous && previous.revision > data.revision)
          throw new Error("项目快照版本落后，正在重新同步。");
        return data;
      },
      refetchInterval: 5000,
    });
  const flow = useQuery({
    queryKey: keys.flow(id),
    queryFn: () => request("/projects/" + id + "/flow"),
    enabled: !!workspace.data && !workspace.data.flow,
    refetchInterval: 5000,
  });
  const [showFlow, setShowFlow] = useState(false),
    [selectedShot, setSelectedShot] = useState(""),
    [preview, setPreview] = useState<RecordData | null>(null),
    [control, setControl] = useState<RecordData | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const w = workspace.data;
  if (!w)
    return (
      <>
        <ErrorNotice error={workspace.error} />
        <Loading />
      </>
    );
  const p = { ...w.project, operator: w.operator },
    graph = w.workflow || {},
    actualFlow = w.flow || flow.data || {},
    attention = w.operator?.attention,
    controls = rows(w.operator?.actions || w.available_actions),
    active = controls.filter((a) =>
      ["pause", "resume", "take_control", "delegate_control"].includes(a.id),
    );
  async function action(body: RecordData) {
    setBusy(true);
    setError("");
    try {
      await request("/projects/" + id + "/action", {
        ...body,
        expected_revision: body.expected_revision ?? p.revision,
      });
      setControl(null);
      notify("操作已记录，将在安全边界完成交接。");
      await client.invalidateQueries();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const tabs = [
    ["overview", "制作工作流"],
    ["decisions", "待办决策"],
    ["team", "团队与回执"],
    ["shots", "镜头制作台"],
    ["script", "剧本与分镜"],
    ["quality", "制作与质检"],
    ["delivery", "成片交付"],
    ["series", "系列资产"],
    ["costs", "云任务与费用"],
  ];
  return (
    <>
      <header className="project-heading">
        <div>
          <button className="breadcrumb-link" onClick={onBack}>
            全部项目 /
          </button>
          <h1>{p.title || "未命名制作"}</h1>
          <div className="project-meta">
            <Badge status={p.status} />
            <span>
              {(
                {
                  motion_comic: "动态漫剧",
                  live_action: "真人影视",
                  animation: "二维动画",
                } as RecordData
              )[p.format] || p.format}{" "}
              · {p.duration_seconds} 秒
            </span>
            <span>
              <Bot size={14} />
              {w.operator?.delegation?.mode === "delegated"
                ? "AI 自主管理"
                : w.operator?.delegation?.mode === "human_fallback"
                  ? "AI 待配置 · 人工接续"
                  : p.team_mode === "delegated"
                    ? "总制片协作"
                    : "人工把关"}
            </span>
          </div>
        </div>
        <div className="heading-actions">
          {active.map((a) => (
            <button
              className={
                "button small " + (a.id === "take_control" ? "primary" : "")
              }
              key={a.id}
              disabled={busy || a.enabled === false}
              title={a.disabled_reason || a.impact}
              onClick={() =>
                rows(a.inputs).length ? setControl(a) : action({ action: a.id })
              }
            >
              {a.id === "take_control" ? (
                <Hand size={14} />
              ) : a.id === "pause" ? (
                <Pause size={14} />
              ) : (
                <Bot size={14} />
              )}{" "}
              {a.label}
            </button>
          ))}
        </div>
      </header>
      {w.operator?.delegation?.reason && (
        <div className="delegation-note">
          <Bot size={16} />
          {w.operator.delegation.reason}
        </div>
      )}
      <ErrorNotice error={error || workspace.error} />
      {attention?.blocking && tab !== "decisions" && (
        <div className="project-attention-banner">
          <span className="attention-icon">
            <Inbox size={20} />
          </span>
          <div>
            <strong>{attention.title}</strong>
            <span>{attention.summary}</span>
          </div>
          <button
            className="button primary small"
            onClick={() => onTab(attention.target_tab || "decisions")}
          >
            {attention.action_label || "处理待办"}
            <ArrowRight size={14} />
          </button>
        </div>
      )}
      <nav className="tabbar" aria-label="项目内容">
        {tabs.map(([v, l]) => (
          <button
            key={v}
            data-tab={v}
            className={v === tab ? "active" : ""}
            aria-current={v === tab ? "page" : undefined}
            onClick={() => onTab(v)}
          >
            {l}
            {v === "decisions" && attention?.blocking && <i />}
          </button>
        ))}
      </nav>
      <div className="project-pane">
        {tab === "overview" ? (
          <>
            <div className="project-context">
              <article>
                <small>当前阶段</small>
                <strong>
                  {w.operator?.current_activity || names[p.status] || p.status}
                </strong>
              </article>
              <article>
                <small>制作进度</small>
                <strong>
                  {
                    rows(p.shots).filter((s) => s.video_url || s.video?.path)
                      .length
                  }
                  <small> / {rows(p.shots).length} 镜视频</small>
                </strong>
              </article>
              <article>
                <small>预算用量</small>
                <strong>
                  {number(p.budget?.spent_gpu_minutes).toFixed(1)}
                  <small> / {p.budget?.gpu_minutes || "—"} GPU 分钟</small>
                </strong>
              </article>
            </div>
            <div className="project-focus">
              <Panel
                title="当前创作产物"
                action={
                  <button
                    className="text-link"
                    onClick={() =>
                      onTab(
                        p.delivery?.video_url
                          ? "delivery"
                          : p.styles?.length
                            ? "decisions"
                            : "script",
                      )
                    }
                  >
                    打开详情
                    <ArrowUpRight size={14} />
                  </button>
                }
              >
                <CurrentOutput project={p} />
              </Panel>
              <Panel title="当前任务与下一步">
                <ReviewAutomation project={p} />
                <div className="current-task">
                  <Badge status={p.status} />
                  <h3>{w.operator?.current_activity || names[p.status]}</h3>
                  <p>
                    {attention?.summary ||
                      "团队按工作流继续推进，实际产物与审核结果会持续更新。"}
                  </p>
                  <div className="member-chips">
                    {(
                      rows(graph.nodes).find((n) => n.id === graph.active_node)
                        ?.members || []
                    ).map((r: string) => (
                      <span key={r}>{roleName(r)}</span>
                    ))}
                  </div>
                  {attention?.blocking ? (
                    <button
                      className="button primary"
                      onClick={() => onTab(attention.target_tab || "decisions")}
                    >
                      {attention.action_label || "处理当前待办"}
                      <ArrowRight size={15} />
                    </button>
                  ) : (
                    <button className="button" onClick={() => onTab("team")}>
                      查看任务与交接
                      <ArrowRight size={15} />
                    </button>
                  )}
                </div>
              </Panel>
            </div>
            <Panel
              title="制作阶段"
              action={
                <button
                  className="text-link"
                  onClick={() => setShowFlow(!showFlow)}
                >
                  {showFlow ? "收起完整工作流" : "展开完整工作流"}
                  <WorkflowIcon size={14} />
                </button>
              }
            >
              <div className="stage-timeline">
                {rows(graph.nodes).map((n, i) => (
                  <button
                    key={n.id}
                    className={
                      "timeline-stage " +
                      (n.id === graph.active_node ? "current" : "")
                    }
                    onClick={() => {
                      setShowFlow(true);
                    }}
                  >
                    <span>{String(i + 1).padStart(2, "0")}</span>
                    <strong>{n.label}</strong>
                    <Badge status={n.status} />
                  </button>
                ))}
              </div>
            </Panel>
            {showFlow && (
              <Workflow
                graph={graph}
                flow={actualFlow}
                attention={attention}
                onDecision={() => onTab("decisions")}
                onShot={(shot) => {
                  setSelectedShot(shot);
                  onTab("shots");
                }}
              />
            )}
            <div className="project-bottom-grid">
              <Panel title="创作任务与当前故事">
                <p className="brief-text">{p.brief}</p>
                {p.selected_story && (
                  <div className="selected-story">
                    <small>
                      已选故事 ·{" "}
                      {p.script_review?.passed ? "当前已通过剧本" : "选题方向"}
                    </small>
                    <h3>
                      {p.script?.title ||
                        rows(p.proposals).find((s) => s.id === p.selected_story)
                          ?.title ||
                        p.title}
                    </h3>
                    <p>
                      {p.script?.logline ||
                        p.script?.summary ||
                        rows(p.proposals).find((s) => s.id === p.selected_story)
                          ?.logline}
                    </p>
                  </div>
                )}
                <details>
                  <summary>研究资料与使用范围</summary>
                  {rows(p.sources || p.research?.sources).map((s, i) => (
                    <div className="source-item" key={i}>
                      <Artifact
                        item={{ url: s.url, label: s.title || s.url }}
                      />
                      <Badge label={s.rights || "仅研究"} status="pending" />
                    </div>
                  ))}
                </details>
              </Panel>
              <Panel title="最近项目事件">
                <ActivityList
                  events={rows(p.events)
                    .slice(-5)
                    .reverse()
                    .map((e) => ({ ...e, project_id: id, title: p.title }))}
                  openProject={(_, t) => onTab(t || "quality")}
                />
              </Panel>
            </div>
          </>
        ) : tab === "decisions" ? (
          <DecisionCenter
            key={id + ":" + (pendingGate(p)?.id || attention?.kind || "none")}
            project={p}
            onNotify={notify}
            onTab={onTab}
          />
        ) : tab === "team" ? (
          <>
            <ErrorNotice
              error={
                flow.error
                  ? new Error("历史回执读取失败：" + flow.error.message)
                  : null
              }
            />
            <Team
              flow={actualFlow}
              graph={graph}
              models={models}
              projectRevision={p.revision}
              onOpen={onTab}
            />
          </>
        ) : tab === "shots" ? (
          <Shots project={p} notify={notify} initialShot={selectedShot} />
        ) : tab === "script" ? (
          <div className="stack">
            <Panel title="剧本与人物设定">
              <Readable value={p.script} />
            </Panel>
            <Panel title="逐镜分镜">
              <div className="shot-story-grid">
                {rows(p.shots).map((s) => (
                  <article className="shot-story-card" key={s.id}>
                    <button
                      className="shot-preview"
                      data-action="preview"
                      data-shot={s.id}
                      onClick={() => setPreview(s)}
                    >
                      {mediaUrl(s.image_url) ? (
                        <img src={mediaUrl(s.image_url)} alt={s.id} />
                      ) : (
                        <Clapperboard size={32} />
                      )}
                      <span>
                        <Play size={17} />
                      </span>
                    </button>
                    <div>
                      <div className="row-between">
                        <h3>
                          {s.id} · {s.seconds} 秒
                        </h3>
                        <Badge status={s.status} />
                      </div>
                      <p>{s.dramatic_beat || s.action || s.description}</p>
                      <Readable value={s.dialogue} />
                    </div>
                  </article>
                ))}
              </div>
            </Panel>
          </div>
        ) : tab === "quality" ? (
          <div className="stack">
            <Panel title="镜头质检与返修">
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>镜头</th>
                      <th>状态</th>
                      <th>尝试</th>
                      <th>审核结果与问题</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows(p.shots).map((s) => (
                      <tr key={s.id}>
                        <td>{s.id}</td>
                        <td>
                          <Badge status={s.status} />
                        </td>
                        <td>
                          {rows(s.attempts).length || s.attempt_count || 0}
                        </td>
                        <td>
                          <Readable value={s.qa || s.quality || s.review} />
                          <details>
                            <summary>素材尝试记录</summary>
                            <Readable value={s.attempts} />
                          </details>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
            <Panel title="生产事件">
              <ActivityList
                events={rows(p.events)
                  .map((e) => ({ ...e, project_id: id, title: p.title }))
                  .reverse()}
                openProject={(_, t) => onTab(t || "quality")}
              />
            </Panel>
          </div>
        ) : tab === "series" ? (
          <Series project={p} notify={notify} />
        ) : tab === "costs" ? (
          <Costs project={p} notify={notify} />
        ) : (
          <Panel
            title="成片交付"
            description={
              p.status === "approved"
                ? "此版本已由出品人确认。"
                : "当前制作版本，完成终审后正式交付。"
            }
          >
            {ownedMedia(p.id, p.delivery?.video_url) ? (
              <div className="delivery-layout">
                <video
                  className="delivery-video"
                  src={ownedMedia(p.id, p.delivery.video_url)}
                  controls
                  playsInline
                  preload="metadata"
                />
                <div>
                  <h3>{p.title}</h3>
                  <p>
                    {p.duration_seconds} 秒 · 制作版本{" "}
                    {p.production_revision || 1}
                  </p>
                  <a
                    className="button primary"
                    href={ownedMedia(p.id, p.delivery.video_url)}
                    download
                  >
                    下载完整 MP4
                    <ArrowUpRight size={15} />
                  </a>
                  <div className="artifact-grid">
                    {[
                      ...rows(p.delivery.files),
                      ...rows(p.delivery.artifacts),
                      ...(p.delivery.manifest_url
                        ? [{ url: p.delivery.manifest_url, name: "制作清单" }]
                        : []),
                    ].map((f, i) => (
                      <Artifact
                        key={i}
                        item={typeof f === "string" ? { path: f } : f}
                        projectId={p.id}
                      />
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <Empty title="完整成片尚未交付">
                镜头、配音、字幕与剪辑完成后，成片将显示在这里。
              </Empty>
            )}
          </Panel>
        )}
      </div>
      {preview && (
        <Modal
          id="preview-dialog"
          title={preview.id + " · 镜头预览"}
          onClose={() => setPreview(null)}
        >
          {ownedMedia(p.id, preview.video_url) ? (
            <video
              controls
              autoPlay
              playsInline
              src={ownedMedia(p.id, preview.video_url)}
            />
          ) : ownedMedia(p.id, preview.image_url) ? (
            <img src={ownedMedia(p.id, preview.image_url)} alt={preview.id} />
          ) : (
            <Empty title="尚无可预览素材" />
          )}
        </Modal>
      )}
      {control && (
        <Modal
          id="control-dialog"
          title={control.label}
          onClose={() => setControl(null)}
        >
          <ActionForm
            action={control}
            project={p}
            busy={busy}
            onSubmit={action}
          />
          <ErrorNotice error={error} />
        </Modal>
      )}
    </>
  );
}
function CreateProject({
  catalog,
  inventory,
  onClose,
  onCreated,
}: {
  catalog?: RecordData;
  inventory?: RecordData;
  onClose: () => void;
  onCreated: (p: RecordData) => void;
}) {
  const client = useQueryClient(),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [preset, setPreset] = useState(
      catalog?.presets?.compact6 ? "compact6" : "legacy15",
    ),
    [requestId] = useState(() => crypto.randomUUID());
  const supported = rows(inventory?.roles).some(
      (r) => r.role === "executive_producer",
    ),
    configured = !!inventory?.role_assignments?.executive_producer;
  return (
    <Modal id="project-dialog" title="发起一部新制作" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const values = Object.fromEntries(new FormData(e.currentTarget));
          setError("");
          setBusy(true);
          try {
            const sources = String(values.sources || "")
              .split(/\r?\n/)
              .map((v) => v.trim())
              .filter(Boolean);
            if (
              sources.length > 12 ||
              sources.some((v) => !/^https?:\/\//i.test(v) || !mediaUrl(v))
            )
              throw new Error("最多填写 12 个完整的 HTTP(S) 来源链接。");
            const profile: RecordData = { schema_version: 1, preset };
            if (values.profile_shot_seconds) {
              const secs = String(values.profile_shot_seconds)
                .split(/[,，\s]+/)
                .map(Number);
              if (secs.some((v) => !Number.isFinite(v) || v <= 0))
                throw new Error("逐镜时长需要正数。");
              Object.assign(profile, {
                shot_seconds: secs,
                shot_count: secs.length,
                duration_seconds: secs.reduce((s, n) => s + n, 0),
              });
            }
            if (values.profile_characters)
              profile.characters = JSON.parse(
                String(values.profile_characters),
              );
            const payload: RecordData = {
              request_id: requestId,
              title: String(values.title || "").trim(),
              brief: String(values.brief).trim(),
              format: values.format,
              creation_mode: values.creation_mode,
              team_mode: values.team_mode,
              production_profile: profile,
              budget: {
                gpu_minutes: Number(values.gpu_minutes),
                wall_minutes: Number(values.wall_minutes),
                max_attempts: Number(values.max_attempts),
              },
              sources: sources.map((url) => ({
                url,
                title: new URL(url).hostname,
                rights: values.rights,
              })),
            };
            if (preset === "legacy15" && !values.profile_shot_seconds)
              payload.duration_seconds = Number(values.duration_seconds);
            if (String(values.series_key || "").trim())
              payload.series_key = String(values.series_key).trim();
            const result = await request("/projects", payload);
            await client.invalidateQueries();
            onCreated(result.project || result);
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <ErrorNotice error={error} />
        <p className="form-intro">
          给团队一个明确的创作目标。AI
          将完成研究、创作与独立审核，你掌握费用授权和最终审片。
        </p>
        <Field label="项目名称 / 可选">
          <input
            name="title"
            maxLength={80}
            placeholder="故事选定后也可以命名"
          />
        </Field>
        <Field label="创作方向">
          <textarea
            name="brief"
            rows={5}
            required
            minLength={5}
            maxLength={6000}
            placeholder="题材、受众、核心情绪、人物与结局期待，以及需要避免的内容。"
            autoFocus
          />
        </Field>
        <div className="form-row">
          <Field label="创作方式">
            <select name="creation_mode">
              <option value="original">原创故事</option>
              <option value="adaptation">授权素材改编</option>
            </select>
          </Field>
          <Field label="制作形式">
            <select name="format">
              <option value="motion_comic">动态漫剧</option>
              <option value="live_action">真人影视</option>
              <option value="animation">二维动画</option>
            </select>
          </Field>
          <Field
            label="团队协作方式"
            hint={
              !configured
                ? "总制片尚未绑定时会明确转人工接续。"
                : "AI 代选合格创意；费用和终片仍由你确认。"
            }
          >
            <select name="team_mode" defaultValue="delegated">
              <option value="delegated">AI 自主管理</option>
              <option value="guided">人工把关故事与视觉</option>
            </select>
          </Field>
        </div>
        <div className="form-row">
          <Field label="创作规格">
            <select
              name="production_preset"
              value={preset}
              onChange={(e) => setPreset(e.target.value)}
            >
              {Object.entries(
                catalog?.presets || { legacy15: { label: "经典短剧 · 15 镜" } },
              ).map(([id, p]: [string, any]) => (
                <option key={id} value={id}>
                  {p.label}
                </option>
              ))}
            </select>
          </Field>
          {preset === "legacy15" && (
            <Field label="经典规格目标时长">
              <select name="duration_seconds">
                <option value="60">60 秒</option>
                <option value="75">75 秒</option>
                <option value="90">90 秒</option>
              </select>
            </Field>
          )}
        </div>
        <details>
          <summary>预算、系列与研究资料</summary>
          <div className="form-row three">
            <Field label="GPU 分钟上限">
              <input
                name="gpu_minutes"
                type="number"
                defaultValue={120}
                min={5}
                max={1440}
                required
              />
            </Field>
            <Field label="累计制作分钟上限">
              <input
                name="wall_minutes"
                type="number"
                defaultValue={180}
                min={10}
                max={1440}
                required
              />
            </Field>
            <Field label="每素材最多尝试">
              <input
                name="max_attempts"
                type="number"
                defaultValue={3}
                min={1}
                max={5}
                required
              />
            </Field>
          </div>
          <Field label="系列标识 / 可选">
            <input
              name="series_key"
              maxLength={100}
              placeholder="同一系列剧集使用相同标识"
            />
          </Field>
          <Field label="研究来源 / 每行一个链接">
            <textarea name="sources" rows={3} maxLength={8000} />
          </Field>
          <Field label="资料使用范围">
            <select name="rights">
              <option value="analysis_only">仅研究分析</option>
              <option value="owned">自有原创作品</option>
              <option value="licensed">已获改编授权</option>
              <option value="public_domain">公版素材</option>
            </select>
          </Field>
          <Field label="指定逐镜秒数 / 可选">
            <input name="profile_shot_seconds" placeholder="4, 5, 6, 4, 5, 6" />
          </Field>
          <Field label="稳定角色 JSON / 可选">
            <textarea name="profile_characters" rows={4} maxLength={5000} />
          </Field>
        </details>
        <div className="dialog-actions">
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button type="submit" className="button primary" disabled={busy}>
            {busy ? "建立项目中…" : "开始自主策划"}
            <ArrowRight size={15} />
          </button>
        </div>
      </form>
    </Modal>
  );
}

function Pagination({
  pagination,
  offset,
  onOffset,
}: {
  pagination?: RecordData;
  offset: number;
  onOffset: (n: number) => void;
}) {
  if (!pagination || (!offset && !pagination.has_more)) return null;
  return (
    <div className="pagination">
      <span>
        第 {offset + 1}–{Math.min(offset + pagination.limit, pagination.total)}{" "}
        项 / 共 {pagination.total} 项
      </span>
      <div className="button-row">
        <button
          className="button small"
          disabled={!offset}
          onClick={() => onOffset(Math.max(0, offset - pagination.limit))}
        >
          上一页
        </button>
        <button
          className="button small"
          disabled={!pagination.has_more}
          onClick={() =>
            onOffset(pagination.next_offset ?? offset + pagination.limit)
          }
        >
          下一页
        </button>
      </div>
    </div>
  );
}
function InboxPage({
  openProject,
}: {
  openProject: (id: string, tab?: string) => void;
}) {
  const [offset, setOffset] = useState(0),
    query = useQuery({
      queryKey: ["attention-page", offset],
      queryFn: () =>
        request("/console?attention_offset=" + offset + "&attention_limit=100"),
      refetchInterval: 10000,
    });
  return (
    <>
      <Heading
        eyebrow="DECISION INBOX"
        title="待办决策"
        description="按实际阻断与人工关卡整理，较早项目的待办同样保留。"
      />
      <Panel title="需要你处理">
        <ErrorNotice error={query.error} />
        {query.data ? (
          <>
            <Attention
              projects={rows(
                query.data.attention_projects || query.data.projects,
              )}
              openProject={openProject}
            />
            <Pagination
              pagination={query.data.attention_pagination}
              offset={offset}
              onOffset={setOffset}
            />
          </>
        ) : (
          <Loading />
        )}
      </Panel>
    </>
  );
}
function CurrentOutput({ project: p }: { project: RecordData }) {
  const video = ownedMedia(p.id, p.delivery?.video_url);
  if (video)
    return (
      <div className="current-output-video">
        <video src={video} controls playsInline preload="metadata" />
        <div>
          <small>当前版本成片</small>
          <h3>{p.title}</h3>
          <p>
            {p.duration_seconds} 秒 · 制作版本 {p.production_revision || 1}
          </p>
          <Badge status={p.status} />
        </div>
      </div>
    );
  if (rows(p.styles).length)
    return (
      <div className="current-visuals">
        {rows(p.styles)
          .slice(0, 3)
          .map((s) => (
            <article key={s.id}>
              {ownedMedia(p.id, s.preview_url || s.image_url || s.preview) && (
                <img
                  src={ownedMedia(
                    p.id,
                    s.preview_url || s.image_url || s.preview,
                  )}
                  alt={s.name || s.id}
                />
              )}
              <h3>{s.name || s.id}</h3>
              <p>{s.description}</p>
            </article>
          ))}
      </div>
    );
  const selected = rows(p.proposals).find((s) => s.id === p.selected_story);
  if (p.script || selected)
    return (
      <div className="current-story">
        <small>
          {p.script_review?.passed ? "当前已通过剧本" : "已选故事方向"}
        </small>
        <h3>{p.script?.title || selected?.title || p.title}</h3>
        <p>
          {p.script?.logline ||
            p.script?.summary ||
            selected?.logline ||
            selected?.synopsis ||
            p.brief}
        </p>
        <span>{rows(p.shots).length} 个计划镜头</span>
      </div>
    );
  if (rows(p.proposals).length)
    return (
      <div className="current-proposals">
        {rows(p.proposals)
          .filter((p) => p.eligible !== false)
          .slice(0, 3)
          .map((s, i) => (
            <article key={s.id}>
              <small>方案 {i + 1}</small>
              <h3>{s.title}</h3>
              <p>{s.logline || s.synopsis}</p>
            </article>
          ))}
      </div>
    );
  return (
    <div className="current-story">
      <small>创作需求</small>
      <p>{p.brief}</p>
      <span>首份产物完成后会显示在这里。</span>
    </div>
  );
}
