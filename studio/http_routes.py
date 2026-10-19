"""Auditable allowlist for the application and shared media transport."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str
    access: str
    name: str
    body_limit: int = 0


SHARED_ROUTES = (
    Route("GET", r"/", "ui", "home"),
    Route("GET", r"/api/health", "read", "health"),
    Route("GET", r"/media/.+", "media", "production_media"),
)
RETIRED_ROUTES = (
    Route("GET", r"/api/episodes", "read", "episodes"),
    Route("GET", r"/api/episode/[a-z0-9][a-z0-9_-]{0,60}", "read", "episode"),
    Route("GET", r"/api/jobs", "read", "legacy_jobs"),
    Route("POST", r"/api/start", "task_control", "legacy_start", 8192),
)
PID = r"[a-f0-9]{12}"
JID = r"[a-f0-9]{32}"
PROJECT = r"/api/factory/projects/" + PID
FACTORY_ROUTES = (
    Route('GET', r'/api/factory/dramas', 'read', 'dramas'),
    Route('POST', r'/api/factory/dramas', 'modify', 'create_drama', 262144),
    Route('GET', r'/api/factory/dramas/' + PID + r'(?:/(episodes|batches))?', 'read', 'drama'),
    Route('GET', r'/api/factory/dramas/' + PID + r'/episodes/' + PID, 'read', 'drama_episode'),
    Route('GET', r'/api/factory/dramas/' + PID + r'/download', 'read', 'drama_download'),
    Route('POST', r'/api/factory/dramas/' + PID + r'/(confirm|generate|actions)', 'task_control', 'drama_control', 1048576),
    Route("GET", r"/legacy", "ui", "legacy_alias"),
    Route("GET", r"/factory/static/.+", "ui", "factory_static"),
    *(Route("GET", "/api/factory/" + name, "read", name) for name in (
        "production-profiles", "workflow", "models", "overview", "capabilities", "console", "system")),
    Route("GET", r"/api/factory/workflow-templates(?:/[a-f0-9]{64})?", "read", "workflow_templates"),
    Route("POST", r"/api/factory/workflow-templates", "configuration", "workflow_template_import", 1048576),
    Route("GET", PROJECT, "read", "project"),
    *(Route("GET", PROJECT + "/" + name, "read", name) for name in (
        "shots", "series", "cloud-budget", "flow", "delivery-readiness", "workspace", "team", "tasks")),
    Route("GET", PROJECT + r"/tasks/[a-z_]+:[A-Za-z0-9_.-]{1,120}", "read", "team_task"),
    Route("GET", PROJECT + r"/interventions", "read", "team_instructions"),
    Route("GET", PROJECT + r"/generation-pipeline", "read", "generation_plan"),
    Route("POST", PROJECT + r"/generation-pipeline(?:/settle)?", "configuration", "generation_plan_configure", 4096),
    Route("GET", PROJECT + r"/prompts(?:/calls(?:/[a-f0-9]{32})?)?", "read", "prompt_workbench"),
    Route("POST", PROJECT + r"/prompts/(save|preview|validate|apply)", "modify", "prompt_edit", 131072),
    Route("POST", PROJECT + r"/prompts/activate", "configuration", "prompt_activate", 4096),
    Route("POST", PROJECT + r"/prompts/(trial|settle)", "financial", "prompt_trial", 131072),
    Route("GET", PROJECT + r"/quality-baselines(?:/([a-f0-9]{32}|compare))?", "read", "quality_baselines"),
    Route("POST", PROJECT + r"/quality-baselines", "modify", "freeze_quality_baseline", 4096),
    Route("POST", PROJECT + r"/interventions(?:/(preview|[a-f0-9]{32}/dismiss))?", "task_control", "team_intervention", 65536),
    Route("POST", PROJECT + r"/tasks/[a-z_]+:[A-Za-z0-9_.-]{1,120}/actions", "task_control", "team_task_control", 65536),
    Route("POST", PROJECT + r"/team/actions", "task_control", "team_enable", 65536),
    Route("GET", r"/api/factory/jobs/" + JID, "read", "job"),
    Route("POST", r"/api/factory/jobs/" + JID + r"/(pause|cancel)", "task_control", "job_control", 65536),
    Route("POST", r"/api/factory/models/test", "network_probe", "model_probe", 65536),
    Route("POST", r"/api/factory/models/discover", "network_probe", "model_discovery", 65536),
    Route("POST", r"/api/factory/models", "configuration", "model_save", 65536),
    Route("POST", r"/api/factory/projects", "modify", "create_project", 65536),
    Route("GET", r"/api/factory/library", "read", "material_library"),
    Route("POST", r"/api/factory/library", "modify", "material_edit", 12*1024*1024),
    Route("GET", r"/api/factory/library/assets/[A-Za-z][A-Za-z0-9_-]{0,63}", "read", "material_version"),
    Route("GET", PROJECT + r"/materials", "read", "project_materials"),
    Route("POST", PROJECT + r"/materials", "modify", "project_materials_edit", 65536),
    Route("GET", r"/api/factory/library/tasks", "read", "material_tasks"),
    Route("POST", r"/api/factory/library/tasks", "task_control", "material_task_create", 65536),
    Route("POST", r"/api/factory/library/tasks/mt_[a-f0-9]{32}", "financial", "material_task_control", 65536),
    Route("POST", PROJECT + r"/shots/S[0-9]{2,3}", "modify", "shot_edit", 65536),
    Route("POST", PROJECT + r"/series", "modify", "series_edit", 65536),
    Route("POST", PROJECT + r"/(cloud-authorize|cloud-confirm-cost)", "financial", "cloud_consent", 65536),
    Route("POST", PROJECT + r"/(decision|action)", "task_control", "project_control", 65536),
)


def routes(factory=False):
    return SHARED_ROUTES + RETIRED_ROUTES + (FACTORY_ROUTES if factory else ())


def invalid_identifier(path, *, factory=False):
    if path.startswith("/api/episode/"):
        return not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,60}", path[len("/api/episode/"):])
    if factory:
        for prefix, pattern in (("/api/factory/projects/", PID), ("/api/factory/jobs/", JID), ("/api/factory/dramas/", PID)):
            if path.startswith(prefix):
                parts = path[len(prefix):].split("/")
                if not re.fullmatch(pattern, parts[0]):
                    return True
                if prefix.endswith("projects/") and len(parts) >= 3 and parts[1] == "shots":
                    return not re.fullmatch(r"S[0-9]{2,3}", parts[2])
    return False


def match_route(method, path, *, factory=False):
    return next((route for route in routes(factory)
                 if route.method == method and re.fullmatch(route.pattern, path)), None)
