# Documentation

[Project home](../README.en.md) · [简体中文](README.md) · [Contributing](../CONTRIBUTING.md)

Start with the English README and getting-started guide. Most detailed references are currently Chinese; the table labels below describe their subjects. Application UI labels remain Chinese. This index separates usage, implementation, operations and historical evidence while preserving existing document paths.

## Start here

| Goal | Suggested reading order |
| --- | --- |
| First installation and production | [Getting started](GETTING_STARTED.en.md) → [Your models and APIs](USER_MODELS.md) → [Short-drama contract](SHORT_DRAMA_CONTRACT.md) |
| Characters, assets and shots | [Asset library](MATERIAL_LIBRARY.md) → [Series Bible](SERIES_BIBLE.md) → [Shot workbench](SHOT_WORKBENCH.md) |
| Code and extensions | [Architecture](ARCHITECTURE.md) → [Model protocols](MODEL_PROVIDERS.md) → [API](API.md) → [Contribution guide](../CONTRIBUTING.md) |
| Services and data protection | [Deployment](DEPLOYMENT.md) → [HTTP boundaries](HTTP_SECURITY.md) → [Consistent backup](CONSISTENT_BACKUP.md) |

## Creative production

| Document | Subject |
| --- | --- |
| [Workspace themes and editing (0.0.2)](UI_REDESIGN_0_0_2.md) | Light/dark themes, mobile navigation, session drafts, search and recovery (Chinese) |
| [Interruptible AI production team (0.1.0)](TEAM_CONTROL_0_1_0.md) | Departments, task evidence, scoped instructions, locks and actual scheduling (Chinese) |
| [Getting started](GETTING_STARTED.en.md) / [中文](GETTING_STARTED.md) | Installation, connections, CLI, artifacts, review and recovery |
| [Your models and APIs](USER_MODELS.md) | Cloud, self-hosted and mixed routes; accounts, regions, costs and recovery |
| [Short-drama contract](SHORT_DRAMA_CONTRACT.md) | Standalone story, visual direction, motion and recorded production |
| [Asset libraries](MATERIAL_LIBRARY.md) | Shared/project libraries, image versions, approved production references |
| [Series Bible](SERIES_BIBLE.md) | Stable characters, visual versions and shot dependencies |
| [Shot workbench](SHOT_WORKBENCH.md) | Candidates, selection, targeted changes and revision conflicts |
| [Production profiles](PRODUCTION_PROFILES.md) | Shot length, pacing, voices and production presets |
| [Team cockpit](TEAM_COCKPIT.md) | Roles, status and production entry points |
| [Team tasks](TEAM_TASKS.md) | Actual work orders, artifacts and execution evidence |
| [Executive producer](TEAM_SUPERVISOR.md) | Bounded delegation, automatic choices, human takeover and final approval |
| [Delivery readiness](DELIVERY_READINESS.md) | Deliverables, checks and repeat validation |

## Development, APIs and evaluation

| Document | Subject |
| --- | --- |
| [Architecture and repository map](ARCHITECTURE.md) | Control layer, model/media resources, state and code ownership |
| [Model providers and roles](MODEL_PROVIDERS.md) | Protocol matrix, network scope, configuration, ComfyUI and video |
| [API and workflow configuration](API.md) | HTTP routes, decisions, model configuration and CLI |
| [Remote-video scheduling](REMOTE_VIDEO_SCHEDULING.md) | Persistent submit/query/download, concurrency and recovery |
| [Cloud-video budgets](CLOUD_VIDEO_BUDGET.md) | Estimates, authorization, ledger and limits; other cloud media in the provider guide |
| [Production acceptance](PRODUCTION_ACCEPTANCE.md) | Read-only validation bound to project, production version and media hash |
| [Quality evaluation](QUALITY_EVALUATION.md) | Technical quality, ASR, VLM, optional VBench and evidence limits |
| [Versioned creative prompts](VERSIONED_CREATIVE_PROMPTS.md) | Prompt modules, sources, signatures and invalidation; English |
| [Offline creative A/B protocol](CREATIVE_AB_PROTOCOL.md) | Frozen cases, blind review and regression aggregation; English |
| [Architecture references](REFERENCE_ARCHITECTURES.md) | Official upstream sources, adopted principles and license boundaries |
| [Contributing and validation](../CONTRIBUTING.md) | Development setup, changes, tests and documentation; bilingual |
| [UI testing](../tests/ui/README.md) | Component, Python contract and browser checks |
| [Creative evaluation corpus](../evaluation/creative/README.md) | Offline cases and evidence recording |

## Operations

| Document | Subject and scope |
| --- | --- |
| [Deployment, upgrades and recovery](DEPLOYMENT.md) | General installation, dependencies, systemd, access and data; its opening cami/Wkp topology is deployment-specific |
| [HTTP security boundaries](HTTP_SECURITY.md) | Loopback, Host/Origin, media, proxy and task control |
| [Consistent backup and isolated restore](CONSISTENT_BACKUP.md) | Maintenance windows, integrity, recovery and audit |
| [Systemd model credentials](SYSTEMD_MODEL_CREDENTIALS.md) | Credential references, authentication and compatibility; English |

## Deployment-specific and historical evidence

These documents retain their original paths, dates, commits and validation scopes. They describe a particular deployment or development stage, **not current server state, general prerequisites or access authorization**. Recheck live conditions before operations. Mock, real HTTP, real inference and server evidence are separate.

| Document | Recorded scope |
| --- | --- |
| [Wkp remote-development handoff](WKP_REMOTE_DEV_HANDOFF.md) | A specific LAN environment, service ownership and development/production boundaries |
| [cami control plane](CAMI_CONTROL_PLANE.md) | Deployment and isolated directories for a particular environment |
| [cami preview](CAMI_PREVIEW.md) | A historical UI preview and entry point |
| [cami / Wkp inference gateway](WKP_LIVE_GATEWAY.md) | Specific test topology and inference integration |
| [Team-studio plan](TEAM_STUDIO_PLAN.md) | Staged design and delivery goals; not proof that all goals shipped |
| [2026-10-13 audit](AUDIT_2026-10-13.md) | Issues, fixes and validation boundaries |
| [2026-10-13 upgrade verification](UPGRADE_VERIFICATION_2026-10-13.md) | Six upgrade checkpoints and known limits |
| [2026-10-15 studio upgrade](STUDIO_UPGRADE_2026-10-15.md) | Refactor baseline, migration, UI and validation |
| [2026-10-16 studio checks](STUDIO_QUALITY_REVIEW.md) | Workbench, file reading, mobile navigation and mock evidence |
| [Creative prompt validation](CREATIVE_PROMPTS_VALIDATION.md) | Historical package and publication-fix checks; English |
| [Package 10 audit](NEXT_ROUND_AUDIT.md) | Baseline-specific issues and fixes |
| [Package 10 integration](NEXT_ROUND_INTEGRATION.md) | Actual integration scope and checks not run |
| [Package 10 delivery report](NEXT_ROUND_PR_REPORT.md) | PRs, commits and validation status |

Earlier PR evidence:

| PR | Audit | Integration | Delivery |
| --- | --- | --- | --- |
| #8: HTTP boundaries | [Audit](pr-history/pr-8/NEXT_ROUND_AUDIT.md) | [Integration](pr-history/pr-8/NEXT_ROUND_INTEGRATION.md) | [Report](pr-history/pr-8/NEXT_ROUND_PR_REPORT.md) |
| #9: production acceptance | [Audit](pr-history/pr-9/NEXT_ROUND_AUDIT.md) | [Integration](pr-history/pr-9/NEXT_ROUND_INTEGRATION.md) | [Report](pr-history/pr-9/NEXT_ROUND_PR_REPORT.md) |
| #10: consistent backup | [Audit](pr-history/pr-10/NEXT_ROUND_AUDIT.md) | [Integration](pr-history/pr-10/NEXT_ROUND_INTEGRATION.md) | [Report](pr-history/pr-10/NEXT_ROUND_PR_REPORT.md) |

## Brand, citation and licensing

- [Brand and presentation assets](assets/README.md): logo, banner, screenshot provenance and reproduction; bilingual.
- [Citation metadata](../CITATION.cff): cite the repository and record the commit; no invented paper, DOI or release version.
- [Third-party notices](../THIRD_PARTY_NOTICES.md): dependency, model and reference terms remain independent. The repository currently has no unified source-code license.
