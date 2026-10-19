import { useEffect, useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  LayoutDashboard,
  FolderKanban,
  Inbox,
  Settings,
  Activity,
  Plus,
  Clapperboard,
  ArrowUpRight,
  ArrowRight,
  Cpu,
  Bot,
  Search,
  CheckCircle2,
  Clock3,
  Play,
  Pause,
  Hand,
  X,
  AlertCircle,
  ChevronRight,
  ArrowLeft,
  ChevronDown,
} from "lucide-react";
import { lockPageScroll } from "./ScrollLock";
import { ThemeProvider } from "./Theme";
import { SystemSettings } from "./SystemSettings";
import { BrandMark } from "./Brand";
import { PageHeader, PageHeaderProvider } from "./PageHeader";
import { ProjectNavigation } from "./ProjectNavigation";
import {
  ProjectResultActions,
  currentResultPage,
} from "./ProjectResultActions";
import { useUnsavedShotGuard } from "./SessionDraft";
import { MaterialLibrary } from "./LazyViews";
import { CreateDrama, DramaDetail, LongEpisodeDetail } from './Dramas';
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
  activityName,
  text,
  type RecordData,
} from "./api";
import {
  Heading,
  PageFocus,
  WorkProgress,
  Panel,
  Badge,
  Empty,
  Loading,
  ErrorNotice,
  FloatingErrorNotice,
  FeedbackProvider,
  ActionNotice,
  Field,
  Modal,
  Readable,
  Artifact,
} from "./ui";
import { Workflow, Team } from "./Workflow";
import { TeamConsole, TeamOverview } from "./TeamConsole";
import { DecisionCenter, ActionForm } from "./Decisions";
import { ReviewAutomation } from "./ReviewAssistance";
import { Shots, Series, Costs } from "./Assets";
import { ShotQuality } from "./ShotQuality";
import { PromptWorkbench } from "./PromptWorkbench";
import { QualityComparison } from "./QualityComparison";
import {
  CreativeContract,
  creativeChoice,
  projectVisualLabel,
  visualChoices,
} from "./CreativeContract";
type Route = { view: string; project: string; tab: string };
function readRoute(): Route {
  const p = new URLSearchParams(location.hash.slice(1));
  const requested = p.get("view") || "dashboard";
  const view = requested === "inbox" ? "dashboard" : requested === "workflow" ? "projects" : requested;
  const project = p.get("project") || "";
  const legacyModels = requested === "models" && !project;
  return {
    view: legacyModels
      ? "settings"
      : ["dashboard", "projects", "library", "activity", "settings"].includes(view)
        ? view
        : "dashboard",
    project,
    tab: legacyModels ? "models" : p.get("tab") || "overview",
  };
}
const links = [
  ["dashboard", "总览", LayoutDashboard],
  ["projects", "项目", FolderKanban],
  ["library", "素材库", FolderKanban],
  ["activity", "运行记录", Activity],
  ["settings", "系统设置", Settings],
] as const;
export default function App() {
  return (
    <ThemeProvider>
      <FeedbackProvider>
        <Workbench />
      </FeedbackProvider>
    </ThemeProvider>
  );
}
function Workbench() {
  useUnsavedShotGuard();
  const client = useQueryClient(),
    [route, setRoute] = useState(readRoute),
    [create, setCreate] = useState<boolean | RecordData>(false),
    [mobile, setMobile] = useState(false),
    [narrow, setNarrow] = useState(
      () => window.matchMedia("(max-width: 1023px)").matches,
    ),
    [toast, setToast] = useState("");
  const navigationRef = useRef<HTMLElement>(null);
  const navigationTrigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1023px)");
    const update = () => {
      setNarrow(media.matches);
      if (!media.matches) setMobile(false);
    };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!mobile || !narrow) return;
    const releaseScroll = lockPageScroll();
    let focusFrame = requestAnimationFrame(() => {
      focusFrame = requestAnimationFrame(() =>
        navigationRef.current
          ?.querySelector<HTMLButtonElement>(".navigation-close")
          ?.focus(),
      );
    });
    return () => {
      cancelAnimationFrame(focusFrame);
      releaseScroll();
      navigationTrigger.current?.focus({ preventScroll: true });
    };
  }, [mobile, narrow]);
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
    profiles = useQuery({
      queryKey: keys.profiles,
      queryFn: () => request("/production-profiles"),
      staleTime: 60000,
    });
  useEffect(() => {
    const listener = () => {
      const next = readRoute();
      // Keep saved model links working without a second settings page or an
      // extra history entry when returning with the browser's Back button.
      if (
        !next.project &&
        new URLSearchParams(location.hash.slice(1)).get("view") === "models"
      ) {
        history.replaceState(
          history.state,
          "",
          location.pathname + location.search + "#view=settings&tab=models",
        );
      }
      setRoute(next);
      setMobile(false);
      const workspace = document.querySelector<HTMLElement>(".main-shell");
      if (workspace) workspace.scrollTop = 0;
    };
    listener();
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
    } else if (view !== "dashboard") {
      p.set("view", view);
      if (view === "settings" && tab !== "overview") p.set("tab", tab);
    }
    location.hash = p.toString();
    setRoute({ view, project, tab });
    setMobile(false);
    const workspace = document.querySelector<HTMLElement>(".main-shell");
    if (workspace) workspace.scrollTop = 0;
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
      <a
        className="skip-link"
        href="#workspace"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("workspace")?.focus({ preventScroll: true });
          document
            .getElementById("workspace")
            ?.scrollIntoView({ block: "start" });
        }}
      >
        跳至工作区
      </a>
      <div className={"app-shell " + (mobile ? "navigation-open" : "")}>
        {mobile && narrow && (
          <button
            className="navigation-backdrop"
            aria-label="关闭导航遮罩"
            onClick={() => setMobile(false)}
            tabIndex={-1}
          />
        )}
        <div className="sidebar-rail">
          <aside
            ref={navigationRef}
            id="studio-navigation"
            className="sidebar"
            aria-label="工作室导航"
            inert={narrow && !mobile ? true : undefined}
            aria-hidden={narrow && !mobile ? true : undefined}
            onKeyDown={(event) => {
              if (!mobile || !narrow) return;
              if (event.key === "Escape") {
                event.preventDefault();
                setMobile(false);
              }
              if (event.key === "Tab") {
                const items = Array.from(
                  navigationRef.current?.querySelectorAll<HTMLElement>(
                    "a[href],button:not(:disabled),select",
                  ) || [],
                ).filter((el) => el.getClientRects().length > 0);
                const first = items[0],
                  last = items[items.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                  event.preventDefault();
                  last?.focus();
                }
                if (!event.shiftKey && document.activeElement === last) {
                  event.preventDefault();
                  first?.focus();
                }
              }
            }}
          >
            <div className="sidebar-header">
              <a
                className="brand"
                href="#"
                aria-label="映序首页"
                onClick={() => navigate("dashboard")}
              >
                <BrandMark />
                <span className="brand-name">映序</span>
              </a>
              <button
                type="button"
                className="icon-button navigation-close"
                aria-label="关闭导航"
                onClick={() => setMobile(false)}
              >
                <X size={20} />
              </button>
            </div>
            <button
              className="button primary new-project"
              data-action="new-project"
              onClick={() => {
                setMobile(false);
                setCreate(true);
              }}
            >
              <Plus size={16} />
              新建制作
            </button>
            <nav className="main-nav" aria-label="主导航">
                  {links.map(([view, label, Icon]) => (
                    <button
                      key={view}
                      className={
                        "nav-item " +
                        (!route.project && route.view === view ? "active" : "")
                      }
                      data-action={view === "dashboard" ? "dashboard" : "view"}
                      data-view={view}
                      aria-current={
                        !route.project && route.view === view ? "page" : undefined
                      }
                      onClick={() => navigate(view)}
                    >
                      <Icon size={17} />
                      {label}
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
                    <small>{activityName(p.current_activity || p.status)}</small>
                  </span>
                </button>
              ))}
              {!projects.length && !overview.isPending && (
                <p className="sidebar-empty">从一段故事需求开始。</p>
              )}
            </div>
          </aside>
        </div>
        <div className="main-shell" inert={mobile && narrow ? true : undefined}>
          <PageHeaderProvider
            value={{
              navigationOpen: mobile,
              navigationTrigger,
              toggleNavigation: () => setMobile(!mobile),
              syncError: overview.isError,
              syncing: overview.isFetching,
              refresh,
            }}
          >
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
                  onRemake={(project) => setCreate(project)}
                />
              ) : route.view === "settings" ? (
                <SystemSettings
                  section={route.tab}
                  onSection={(tab) => navigate("settings", "", tab)}
                  overview={overview.data}
                  overviewError={overview.error}
                  retryOverview={() => { void overview.refetch(); }}
                  notify={setToast}
                />
              ) : route.view === "library" ? (
                <MaterialLibrary notify={setToast} />
              ) : (
                <>
                  <FloatingErrorNotice error={overview.error} />
                  {!overview.data ? (
                    <>
                      <Heading
                        eyebrow=""
                        title={
                          links.find((link) => link[0] === route.view)?.[1] ||
                          "总览"
                        }
                      />
                      {overview.error ? (
                        <button
                          className="button"
                          onClick={() => overview.refetch()}
                        >
                          重新读取工作室
                        </button>
                      ) : (
                        <Loading />
                      )}
                    </>
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
                  ) : route.view === "activity" ? (
                    <>
                      <Heading
                        eyebrow="OPERATIONS"
                        title="运行记录"
                        description="从项目事件定位执行、审核与异常，所有操作均保留历史。"
                      />
                      <PageFocus label="最近运行事件" value={`${rows(overview.data.activity).length} 条`} detail="展示本次载入的项目事件，可按项目和事件类型筛选并定位执行记录。" />
                      <Panel title="最近运行事件">
                        <ActivityList
                          events={rows(overview.data.activity)}
                          openProject={openProject}
                          filterable
                        />
                      </Panel>
                    </>
                  ) : null}
                </>
              )}
            </main>
          </PageHeaderProvider>
        </div>
      </div>
      <ActionNotice message={toast} />
      {create === true && <CreateDrama onClose={() => setCreate(false)} onLegacy={() => setCreate({})} onCreated={p => {
        setCreate(false); void client.invalidateQueries(); openProject(p.id);
      }} />}
      {create && create !== true && (
        <CreateProject
          catalog={profiles.data}
          inventory={models.data}
          seed={typeof create === "object" && Object.keys(create).length ? create : undefined}
          onClose={() => setCreate(false)}
          onCreated={(p) => {
            setCreate(false);
            setToast(
              p.material_setup_requested
                ? "项目已建立，请先准备并确认素材。"
                : "制作项目已建立，AI 团队开始策划。",
            );
            openProject(
              p.id,
              p.material_setup_requested ? "materials" : "overview",
            );
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
  const focus = rows(o.attention_projects).find((p) => p.attention?.blocking)
    || projects.find((p) => p.attention?.blocking)
    || projects.find((p) => p.job_running)
    || projects.find((p) => !["approved", "cancelled"].includes(p.status))
    || projects[0];
  const poster = focus && ownedMedia(focus.id, focus.poster);
  const focusProgress = typeof focus?.progress === "number" && Number.isFinite(focus.progress)
    ? Math.max(0, Math.min(100, focus.progress)) : undefined;
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
    <div className="dashboard-view">
      <Heading
        eyebrow="PRODUCTION OVERVIEW"
        title="制片总览"
        description="整剧规划、逐集制作与作品交付。"
      />
      <section className={"dashboard-focus" + (!focus ? " dashboard-focus-empty" : "")} aria-label="当前制作">
        <div className="dashboard-focus-copy">
          <small>{focus ? "当前制作" : "你的创作空间"}</small>
          <h2>{focus?.title || "开始第一部作品"}</h2>
          {!focus && <p>从故事梗概或已有剧本开始，规划一部属于你的剧。</p>}
          {focus && <div className="dashboard-focus-meta">
            <Badge status={focus.status} />
            {focus.record_kind === "drama" && !!focus.episode_count && <span>{focus.episode_count} 集</span>}
            {!!focus.duration_seconds && <span>{focus.record_kind === "drama" ? "每集 " : ""}{focus.duration_seconds} 秒</span>}
          </div>}
          {!focus && <div className="dashboard-focus-actions"><button className="button primary" onClick={create}><Plus size={16} aria-hidden="true" />新建一部剧</button></div>}
        </div>
        {focus && <div className="dashboard-focus-next"><section className="dashboard-focus-feature" aria-label="制作进展">
          <div className="focus-feature-head">
            <span>当前阶段</span>
            <div className="focus-poster" aria-hidden="true">
              <Clapperboard size={28} />
              {poster && <img key={poster} src={poster} alt="" loading="lazy" onError={(event) => { event.currentTarget.hidden = true; }} />}
            </div>
          </div>
          <strong>{activityName(focus.current_activity || focus.stage || focus.status)}</strong>
          {focusProgress !== undefined && <div className="focus-feature-progress">
            <span>{Math.round(focusProgress)}<small>%</small></span>
            <span>制作进度</span>
          </div>}
          {focusProgress !== undefined && <WorkProgress label="当前制作进度" completed={focusProgress} total={100} />}
        </section><div className="dashboard-focus-actions">
          <button className="button primary" onClick={() => openProject(focus.id, focus.attention?.target_tab || "overview")}>打开当前项目 <ArrowUpRight size={16} aria-hidden="true" /></button>
        </div></div>}
      </section>
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
      <div
        className={
          "dashboard-grid" +
          (rows(o.recent_deliveries).length ? " has-deliveries" : "")
        }
      >
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
        <PendingDecisions overview={o} openProject={openProject} />
        <Panel title="最近动态" className="dashboard-activity">
          <ActivityList
            events={rows(o.activity).slice(0, 5)}
            openProject={openProject}
          />
        </Panel>
        <Panel
          title="制作资源"
          description="已记录用量与服务快照"
          className="dashboard-resources"
        >
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
        {rows(o.recent_deliveries).length > 0 && (
          <Panel title="最近交付" className="dashboard-deliveries">
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
      </div>
    </div>
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
                    <small>{activityName(p.current_activity)}</small>
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
    [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0);
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search.trim());
      setOffset(0);
    }, 200);
    return () => clearTimeout(timer);
  }, [search]);
  const page = useQuery({
    queryKey: ["project-page", offset, query, filter],
    queryFn: () =>
      request(
        "/console?" +
          new URLSearchParams({
            offset: String(offset),
            limit: "100",
            q: query,
            filter,
          }),
      ),
  });
  const snapshot =
    page.data ||
    (!query && filter === "all" && offset === 0 ? overview : undefined);
  const projects = rows(snapshot?.projects);
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
      {snapshot && <PageFocus label="筛选结果" value={`${snapshot.pagination?.total ?? projects.length} 项`} detail={query || filter !== "all" ? "当前搜索与分类下的项目，保留原有剧集和制作成果。" : "整剧和已有制作集中管理，进入项目后查看规划、素材、流程与交付。"} />}
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
                aria-pressed={filter === v}
                onClick={() => {
                  setFilter(v);
                  setOffset(0);
                }}
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
              maxLength={200}
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
            />
          </label>
        </div>
        {(query !== search.trim() || page.isFetching) && (
          <p className="muted" role="status">
            正在搜索全部项目…
          </p>
        )}
        {page.isPending && !snapshot ? (
          <Loading />
        ) : projects.length ? (
          <ProjectTable projects={projects} openProject={openProject} />
        ) : (
          <div>
            <table className="console-project-table">
              <tbody />
            </table>
            {!page.error && (
              <Empty title={search ? "没有匹配的项目" : "暂无此类项目"} />
            )}
          </div>
        )}
        <Pagination
          pagination={snapshot?.pagination}
          offset={offset}
          onOffset={setOffset}
          disabled={query !== search.trim() || page.isFetching}
        />
        <FloatingErrorNotice error={page.error} />
        {page.error && (
          <button className="button" onClick={() => page.refetch()}>
            重新读取项目
          </button>
        )}
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
    <Empty title="当前没有执行中的任务">可从项目工作流查看待确认、暂停或阻塞的进度。</Empty>
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
    [page, setPage] = useState(0),
    items = events.filter(
      (e) =>
        !filter ||
        ["error", "decision_required", "review_blocked"].includes(e.kind),
    ),
    pages = Math.ceil(items.length / 8),
    current = Math.min(page, Math.max(0, pages - 1));
  return (
    <>
      {filterable && (
        <div className="segmented activity-filter">
          <button
            className={!filter ? "selected" : ""}
            onClick={() => {
              setFilter(false);
              setPage(0);
            }}
          >
            全部事件
          </button>
          <button
            className={filter ? "selected" : ""}
            onClick={() => {
              setFilter(true);
              setPage(0);
            }}
          >
            问题与待办
          </button>
        </div>
      )}
      <div className="activity-list">
        {items.slice(current * 8, (current + 1) * 8).map((e, i) => (
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
      {items.length > 8 && (
        <nav className="pagination" aria-label="事件分页">
          <span>
            共 {items.length} 条 · 第 {current + 1} / {pages} 页
          </span>
          <div className="button-row">
            <button
              className="button small"
              disabled={current === 0}
              onClick={() => setPage(current - 1)}
            >
              上一页事件
            </button>
            <button
              className="button small"
              disabled={current + 1 >= pages}
              onClick={() => setPage(current + 1)}
            >
              下一页事件
            </button>
          </div>
        </nav>
      )}
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
  onRemake,
}: {
  id: string;
  tab: string;
  onTab: (tab: string) => void;
  onBack: () => void;
  notify: (v: string) => void;
  models?: RecordData;
  onRemake: (project: RecordData) => void;
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
  const actionsMenu = useRef<HTMLDetailsElement>(null);
  const downloadReasonId = useId();
  useEffect(() => {
    if (actionsMenu.current) actionsMenu.current.open = false;
  }, [tab]);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (
        actionsMenu.current &&
        !actionsMenu.current.contains(event.target as Node)
      )
        actionsMenu.current.open = false;
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, []);
  const [selectedFlowNode, setSelectedFlowNode] = useState(""),
    [selectedExecution, setSelectedExecution] = useState(""),
    [workflowOpen, setWorkflowOpen] = useState(false),
    [workflowSeen, setWorkflowSeen] = useState(false),
    [selectedShot, setSelectedShot] = useState(""),
    [storyboardPage, setStoryboardPage] = useState(0),
    [preview, setPreview] = useState<RecordData | null>(null),
    [control, setControl] = useState<RecordData | null>(null),
    [busy, setBusy] = useState(false),
    [statusOpen, setStatusOpen] = useState(false),
    [error, setError] = useState("");
  const w = workspace.data;
  if (!w)
    return (
      <>
        <PageHeader
          className="project-heading"
          title="制作项目"
          parent={
            <button
              className="icon-button"
              aria-label="返回项目列表"
              title="返回项目列表"
              onClick={onBack}
            >
              <ArrowLeft size={20} />
            </button>
          }
        />
        <FloatingErrorNotice error={workspace.error} />
        {workspace.error ? (
          <button className="button" onClick={() => workspace.refetch()}>
            重新读取项目
          </button>
        ) : (
          <Loading />
        )}
      </>
    );
  if (w.project.record_kind === 'drama') return <DramaDetail key={id} id={id} activeTab={tab} onTab={onTab} onBack={onBack} openEpisode={(episodeId, targetTab = 'overview') => {
    location.hash = new URLSearchParams({project:episodeId, tab:targetTab}).toString();
  }} />;
  if (w.project.record_kind === 'episode' && w.project.duration_seconds > 90) return <LongEpisodeDetail key={id} episode={w.project} activeTab={tab} onTab={onTab} openProject={(projectId, targetTab = 'overview') => {
    location.hash = new URLSearchParams({project:projectId, tab:targetTab}).toString();
  }} />;
  const p = { ...w.project, operator: w.operator },
    storyboardShots = rows(p.shots),
    storyboardPages = Math.max(1, Math.ceil(storyboardShots.length / 6)),
    currentStoryboardPage = Math.min(storyboardPage, storyboardPages - 1),
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
  return (
    <>
      <PageHeader
        className="project-heading"
        title={p.title || "未命名制作"}
        parent={
          <button
            className="icon-button project-back"
            aria-label="返回项目列表"
            title="返回项目列表"
            onClick={() => { if (p.parent_id) location.hash=new URLSearchParams({project:p.parent_id,tab:'overview'}).toString(); else onBack(); }}
          >
            <ArrowLeft size={20} aria-hidden="true" />
          </button>
        }
        meta={
          <div className="project-meta">
            <button
              type="button"
              className="project-status-trigger"
              aria-label="查看项目状态"
              aria-haspopup="dialog"
              aria-expanded={statusOpen}
              onClick={() => setStatusOpen(true)}
            >
              {attention?.blocking ? (
                <AlertCircle size={16} />
              ) : (
                <Activity size={16} />
              )}
              <span>{names[p.status] || p.status || "未记录"}</span>
              <ChevronRight size={14} />
            </button>
            <span>
              {projectVisualLabel(p)} · {p.duration_seconds} 秒
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
            {!ownedMedia(p.id, p.delivery?.video_url) && (
              <span id={downloadReasonId} className="project-download-reason">
                成片尚未生成
              </span>
            )}
          </div>
        }
        action={
          <div className="heading-actions">
            <ProjectResultActions
              project={p}
              onOpen={onTab}
              reasonId={downloadReasonId}
            />
            <details
              ref={actionsMenu}
              className="project-actions-menu"
              onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node))
                  event.currentTarget.open = false;
              }}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  event.stopPropagation();
                  event.currentTarget.open = false;
                  event.currentTarget.querySelector("summary")?.focus();
                }
              }}
            >
              <summary className="button">
                项目操作
                <ChevronDown size={16} aria-hidden="true" />
              </summary>
              <div className="project-actions-list">
                <button className="button small" onClick={() => onRemake(p)}>
                  按新规格重制
                </button>
                {active.map((a) => (
                  <button
                    className={
                      "button small " +
                      (a.id === "take_control" ? "primary" : "")
                    }
                    key={a.id}
                    disabled={busy || a.enabled === false}
                    title={a.disabled_reason || a.impact}
                    onClick={() =>
                      rows(a.inputs).length
                        ? setControl(a)
                        : action({ action: a.id })
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
            </details>
          </div>
        }
      />
      <FloatingErrorNotice error={workspace.error || (!control ? error : "")} />
      <div className="project-content-layout">
        <ProjectNavigation
          tab={tab === "workflow" ? "overview" : tab}
          onTab={onTab}
          attention={attention?.blocking}
        />
        <div className="project-pane">
          {tab === "workflow" ? (
                  <Workflow
                    key={id}
                    onProject={projectId=>{location.hash=new URLSearchParams({project:projectId,tab:"workflow"}).toString();}}
                    graph={graph}
                    flow={actualFlow}
                    attention={attention}
                    selectedNode={selectedFlowNode}
                    onSelectNode={(node) => {
                      setSelectedFlowNode(node);
                      setSelectedExecution("");
                    }}
                    selectedExecution={selectedExecution}
                    onOpen={onTab}
                    onDecision={() => onTab("decisions")}
                    onShot={(shot) => {
                      setSelectedShot(shot);
                      onTab("shots");
                    }}
                  />
          ) : tab === "overview" ? (
            <>
              <div className="project-context">
                <article>
                  <small>当前阶段</small>
                  <strong>
                    {activityName(w.operator?.current_activity || p.status)}
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
                      onClick={() => onTab(currentResultPage(p))}
                    >
                      打开详情
                      <ArrowUpRight size={14} />
                    </button>
                  }
                >
                  <CurrentOutput project={p} />
                </Panel>
                <Panel title="当前任务与下一步" className="work-focus project-next-action">
                  {p.review_automation?.status !== "passed" && (
                    <ReviewAutomation project={p} />
                  )}
                  <div className="current-task">
                    <h3>
                      {attention?.title ||
                        activityName(w.operator?.current_activity || p.status)}
                    </h3>
                    <p>
                      {attention?.summary ||
                        "团队按工作流继续推进，实际产物与审核结果会持续更新。"}
                    </p>
                    {attention?.blocking ? (
                      <button
                        className="button primary"
                        onClick={() =>
                          onTab(attention.target_tab || "decisions")
                        }
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
                    <details className="current-task-members">
                      <summary>参与角色</summary>
                      <div className="member-chips">
                        {(
                          rows(graph.nodes).find(
                            (n) => n.id === graph.active_node,
                          )?.members || []
                        ).map((r: string) => (
                          <span key={r}>{roleName(r)}</span>
                        ))}
                      </div>
                    </details>
                  </div>
                </Panel>
              </div>
              <nav className="project-output-links" aria-label="查看项目成果">
                <button onClick={() => onTab("script")}>
                  <span>剧本与分镜</span>
                  <small>{p.script ? "查看正文" : "尚未生成"}</small>
                  <ArrowUpRight size={16} />
                </button>
                <button onClick={() => onTab("materials")}>
                  <span>素材与定稿</span>
                  <small>查看角色与场景</small>
                  <ArrowUpRight size={16} />
                </button>
                <button onClick={() => onTab("shots")}>
                  <span>镜头作品</span>
                  <small>{rows(p.shots).length} 个计划镜头</small>
                  <ArrowUpRight size={16} />
                </button>
                <button onClick={() => onTab("delivery")}>
                  <span>成片交付</span>
                  <small>
                    {ownedMedia(id, p.delivery?.video_url)
                      ? "查看完整成片"
                      : "尚未生成"}
                  </small>
                  <ArrowUpRight size={16} />
                </button>
              </nav>
              <details
                className="project-process"
                open={workflowOpen}
                onToggle={(event) => {
                  setWorkflowOpen(event.currentTarget.open);
                  if (event.currentTarget.open) setWorkflowSeen(true);
                }}
              >
                <summary>
                  完整制作流程<span>查看各阶段、输入输出与执行记录</span>
                </summary>
                {(workflowOpen || workflowSeen) && (
                  <Workflow
                    key={id}
                    onProject={projectId=>{location.hash=new URLSearchParams({project:projectId,tab:"workflow"}).toString();}}
                    graph={graph}
                    flow={actualFlow}
                    attention={attention}
                    selectedNode={selectedFlowNode}
                    onSelectNode={(node) => {
                      setSelectedFlowNode(node);
                      setSelectedExecution("");
                    }}
                    selectedExecution={selectedExecution}
                    onOpen={onTab}
                    onDecision={() => onTab("decisions")}
                    onShot={(shot) => {
                      setSelectedShot(shot);
                      onTab("shots");
                    }}
                  />
                )}
              </details>
              <details className="project-background">
                <summary>制作规格与团队概况</summary>
                <CreativeContract project={p} compact />
                <TeamOverview projectId={id} onOpen={() => onTab("team")} />
              </details>
              <details className="project-background">
                <summary>创作背景与最近事件</summary>
                <div className="project-bottom-grid">
                  <Panel title="创作任务与当前故事">
                    <p className="brief-text">{p.brief}</p>
                    {p.selected_story && (
                      <div className="selected-story">
                        <small>
                          已选故事 ·{" "}
                          {p.script_review?.passed
                            ? "当前已通过剧本"
                            : "选题方向"}
                        </small>
                        <h3>
                          {p.script?.title ||
                            rows(p.proposals).find(
                              (s) => s.id === p.selected_story,
                            )?.title ||
                            p.title}
                        </h3>
                        <p>
                          {p.script?.logline ||
                            p.script?.summary ||
                            rows(p.proposals).find(
                              (s) => s.id === p.selected_story,
                            )?.logline}
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
                          <Badge
                            label={s.rights || "仅研究"}
                            status="pending"
                          />
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
              </details>
            </>
          ) : tab === "decisions" ? (
            <DecisionCenter
              key={id + ":" + (pendingGate(p)?.id || attention?.kind || "none")}
              project={p}
              onNotify={notify}
              onTab={onTab}
              onShot={(shot) => {
                setSelectedShot(shot);
                onTab("shots");
              }}
            />
          ) : tab === "team" ? (
            <>
              {w.operator?.delegation?.reason && (
                <div className="delegation-note">
                  <Bot size={16} />
                  {w.operator.delegation.reason}
                </div>
              )}
              <FloatingErrorNotice
                error={
                  flow.error
                    ? new Error("历史回执读取失败：" + flow.error.message)
                    : null
                }
              />
              <TeamConsole projectId={id}>
                <Team
                  flow={actualFlow}
                  onInspect={(step, execution) => {
                    setWorkflowOpen(true);
                    setSelectedFlowNode(step);
                    setSelectedExecution(execution || "");
                    onTab("overview");
                  }}
                  graph={graph}
                  models={models}
                  projectRevision={p.revision}
                  onOpen={onTab}
                />
              </TeamConsole>
            </>
          ) : tab === "shots" ? (
            <Shots project={p} notify={notify} initialShot={selectedShot} />
          ) : tab === "script" ? (
            <div className="stack script-workspace">
              <Panel title="剧本与人物设定">
                {p.script ? (
                  <>
                    <h3 className="script-title">
                      {p.script.title || p.title}
                    </h3>
                    {p.script.logline && (
                      <p className="script-logline">{p.script.logline}</p>
                    )}
                    <Readable
                      value={Object.fromEntries(
                        Object.entries({
                          ...p.script,
                          cast: p.script.character_names || p.script.cast,
                        }).filter(([key]) =>
                          [
                            "synopsis",
                            "ending",
                            "cast",
                            "story_structure",
                          ].includes(key),
                        ),
                      )}
                    />
                    <details className="script-evidence">
                      <summary>完整剧本与人物设定</summary>
                      <div
                        className="evidence-content"
                        tabIndex={0}
                        aria-label="完整剧本原文"
                      >
                        <Readable value={p.script} />
                      </div>
                    </details>
                  </>
                ) : (
                  <Empty title="剧本尚未生成" />
                )}
              </Panel>
              <Panel title="逐镜分镜">
                <div className="shot-story-grid">
                  {storyboardShots
                    .slice(
                      currentStoryboardPage * 6,
                      currentStoryboardPage * 6 + 6,
                    )
                    .map((s) => (
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
                {storyboardPages > 1 && (
                  <nav className="pagination" aria-label="分镜分页">
                    <span>
                      第 {currentStoryboardPage + 1} / {storyboardPages} 页 · 共{" "}
                      {storyboardShots.length} 镜
                    </span>
                    <div className="button-row">
                      <button
                        className="button small"
                        disabled={currentStoryboardPage === 0}
                        onClick={() =>
                          setStoryboardPage(currentStoryboardPage - 1)
                        }
                      >
                        上一页分镜
                      </button>
                      <button
                        className="button small"
                        disabled={currentStoryboardPage + 1 >= storyboardPages}
                        onClick={() =>
                          setStoryboardPage(currentStoryboardPage + 1)
                        }
                      >
                        下一页分镜
                      </button>
                    </div>
                  </nav>
                )}
              </Panel>
            </div>
          ) : tab === "prompts" ? (
            <PromptWorkbench project={p} />
          ) : tab === "comparison" ? (
            <QualityComparison project={p} />
          ) : tab === "quality" ? (
            <div className="stack">
              <ShotQuality
                shots={rows(p.shots)}
                onShot={(shot) => {
                  setSelectedShot(shot);
                  onTab("shots");
                }}
              />
              <Panel title="生产事件">
                <ActivityList
                  filterable
                  events={rows(p.events)
                    .map((e) => ({ ...e, project_id: id, title: p.title }))
                    .reverse()}
                  openProject={(_, t) => onTab(t || "quality")}
                />
              </Panel>
            </div>
          ) : tab === "series" ? (
            <Series project={p} notify={notify} />
          ) : tab === "materials" ? (
            <MaterialLibrary project={p} notify={notify} />
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
              <details className="project-background delivery-specification">
                <summary>制作规格与回执依据</summary>
                <CreativeContract project={p} />
              </details>
            </Panel>
          )}
        </div>
      </div>
      {statusOpen && (
        <Modal
          id="project-status-dialog"
          className="project-status-drawer"
          title="项目状态"
          onClose={() => setStatusOpen(false)}
        >
          <div className="status-summary">
            <span className="status-caption">{p.title || "未命名制作"}</span>
            <h3>
              {attention?.title ||
                activityName(w.operator?.current_activity || p.status)}
            </h3>
            <p>
              {attention?.summary ||
                "当前没有待处理的阻断问题。制作进度和产物可在项目页面查看。"}
            </p>
          </div>
          <dl className="status-facts">
            <div>
              <dt>当前状态</dt>
              <dd>{names[p.status] || p.status || "未记录"}</dd>
            </div>
            <div>
              <dt>当前阶段</dt>
              <dd>
                {activityName(
                  w.operator?.current_activity || p.stage || p.status,
                )}
              </dd>
            </div>
          </dl>
          {rows(attention?.issues).length > 0 && (
            <ul className="status-issues" aria-label="待处理问题">
              {rows(attention?.issues).map((issue, index) => (
                <li key={issue.id || index}>
                  <strong>
                    {issue.title || issue.description || "审核问题"}
                  </strong>
                  {issue.suggestion && <p>{issue.suggestion}</p>}
                  {Array.isArray(issue.shot_ids) &&
                    issue.shot_ids.length > 0 && (
                      <div className="button-row">
                        {issue.shot_ids.map((shotId: string) => (
                          <button
                            key={shotId}
                            type="button"
                            className="text-link"
                            onClick={() => {
                              setSelectedShot(shotId);
                              setStatusOpen(false);
                              onTab("shots");
                            }}
                          >
                            查看 {shotId}
                            <ArrowUpRight size={16} />
                          </button>
                        ))}
                      </div>
                    )}
                </li>
              ))}
            </ul>
          )}
          {workspace.error && <ErrorNotice error={workspace.error} />}
          {attention?.blocking && (
            <button
              type="button"
              className="button primary status-next-action"
              onClick={() => {
                setStatusOpen(false);
                onTab(attention.target_tab || "decisions");
              }}
            >
              {attention.action_label || "处理待办"}
              <ArrowRight size={16} />
            </button>
          )}
        </Modal>
      )}
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
          <ErrorNotice error={error} reserveSpace />
        </Modal>
      )}
    </>
  );
}
export function CreateProject({
  catalog,
  inventory,
  onClose,
  onCreated,
  seed,
}: {
  catalog?: RecordData;
  inventory?: RecordData;
  onClose: () => void;
  onCreated: (p: RecordData) => void;
  seed?: RecordData;
}) {
  const client = useQueryClient(),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [visualStyle, setVisualStyle] = useState(
      visualChoices.some(
        (choice) => choice.id === seed?.creative_contract?.visual_style,
      )
        ? seed!.creative_contract.visual_style
        : "anime_2d",
    ),
    [preset, setPreset] = useState(
      catalog?.presets?.compact6 ? "compact6" : "legacy15",
    ),
    [requestId] = useState(() => crypto.randomUUID());
  const supported = rows(inventory?.roles).some(
      (r) => r.role === "executive_producer",
    ),
    configured = !!inventory?.role_assignments?.executive_producer;
  return (
    <Modal
      id="project-dialog"
      title={seed ? "按新规格发起重制" : "发起一部新制作"}
      onClose={onClose}
    >
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
              ...creativeChoice(visualStyle),
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
            if (values.prepare_materials) payload.prepare_materials = true;
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
        <p className="form-intro">
          给团队一个明确的创作目标。AI
          将完成研究、创作与独立审核，你掌握费用授权和最终审片。
        </p>
        {seed && (
          <p className="creative-remake-note">
            已带入原项目的名称与创作方向。提交后建立新项目，原项目的成片、素材与历史记录继续保留。
          </p>
        )}
        <Field label="项目名称 / 可选">
          <input
            name="title"
            maxLength={80}
            placeholder="故事选定后也可以命名"
            defaultValue={
              seed
                ? `${String(seed.title || "未命名制作").slice(0, 72)} · 重制`
                : ""
            }
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
            defaultValue={seed?.brief || ""}
          />
        </Field>
        <label className="check">
          <input type="checkbox" name="prepare_materials" />
          先准备人物、场景与画风，再开始制作
        </label>
        <div className="form-row">
          <Field label="创作方式">
            <select name="creation_mode">
              <option value="original">原创故事</option>
              <option value="adaptation">授权素材改编</option>
            </select>
          </Field>
          <Field label="画面与运动方式">
            <select
              name="visual_style"
              value={visualStyle}
              onChange={(e) => setVisualStyle(e.target.value)}
            >
              {visualChoices.map((choice) => (
                <option key={choice.id} value={choice.id}>
                  {choice.label}
                </option>
              ))}
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
        <div className="creative-create-summary">
          <strong>单部闭环短剧</strong>
          <p>
            开场进入冲突，人物主动行动，情节递进反转，结尾兑现主线，不以“下一集”悬念代替结局。
          </p>
          <p>
            {visualStyle === "illustrated"
              ? "静帧动态漫画使用图片与运镜，不生成角色动作或同步口型。"
              : "使用视频模型生成角色动作，需逐镜通过质检；当前流程不承诺同步口型，配音可采用画外音。"}
          </p>
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
        <ErrorNotice error={error} reserveSpace />
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
  disabled = false,
}: {
  pagination?: RecordData;
  offset: number;
  onOffset: (n: number) => void;
  disabled?: boolean;
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
          disabled={!offset || disabled}
          onClick={() => onOffset(Math.max(0, offset - pagination.limit))}
        >
          上一页
        </button>
        <button
          className="button small"
          disabled={!pagination.has_more || disabled}
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
function PendingDecisions({
  overview,
  openProject,
}: {
  overview: RecordData;
  openProject: (id: string, tab?: string) => void;
}) {
  const [offset, setOffset] = useState(0),
    query = useQuery({
      queryKey: ["attention-page", offset],
      queryFn: () =>
        request("/console?attention_offset=" + offset + "&attention_limit=100"),
      refetchInterval: 10000,
    });
  const snapshot = query.data || (offset === 0 ? overview : undefined);
  return (
      <Panel title="待办" className="dashboard-inbox">
        <FloatingErrorNotice error={query.error} />
        {snapshot ? (
          <>
            <Attention
              projects={rows(
                snapshot.attention_projects || snapshot.projects,
              )}
              openProject={openProject}
            />
            <Pagination
              pagination={snapshot.attention_pagination}
              offset={offset}
              onOffset={setOffset}
              disabled={query.isFetching}
            />
          </>
        ) : (
          query.error ? <Empty title="待办暂时无法读取" /> : <Loading />
        )}
        {query.error && <button className="button" onClick={() => query.refetch()}>重新读取待办</button>}
      </Panel>
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
        </div>
      </div>
    );
  const shot = rows(p.shots).find(
    (item) =>
      ownedMedia(p.id, item.video_url || item.video?.path) ||
      ownedMedia(p.id, item.image_url),
  );
  if (shot) {
    const previewVideo = ownedMedia(p.id, shot.video_url || shot.video?.path);
    return (
      <div className="current-output-video">
        {previewVideo ? (
          <video src={previewVideo} controls playsInline preload="metadata" />
        ) : (
          <img
            src={ownedMedia(p.id, shot.image_url)}
            alt={`${shot.id} 镜头画面`}
          />
        )}
        <div>
          <small>当前已生成作品</small>
          <h3>{shot.id} · 镜头预览</h3>
          <p>
            {shot.dramatic_beat ||
              shot.description ||
              "打开详情查看候选与制作记录。"}
          </p>
          <Badge status={shot.status} />
        </div>
      </div>
    );
  }
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
