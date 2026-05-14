# Module Impact Rules

Use this file together with `docs/modules/module-map.md` to infer impacted modules and repositories when converting a requirement discussion into a PRD.

## Start From The Module Map

Use these fields from `docs/modules/module-map.md` as the primary evidence:

- `Module`
- `Responsibility`
- `Boundaries`
- `Upstream Dependencies`
- `Downstream Consumers`
- `Target Repo`

Use the `Dependency Overview` section when it exists to confirm cross-module edges before adding supporting repos.

## Primary Owner Selection

Choose exactly one primary owning module:

- Pick the module that owns the main user value or business behavior.
- If two modules feel equally primary, the requirement may be too broad and should likely become two PRDs.

## Supporting Module Heuristics

- UI plus backend change usually impacts the frontend module and the owning backend module.
- Shared schema, model, or client changes usually impact the shared module plus the business module that needs the change.
- Framework or platform-level behavior changes should only pull in the shared framework/platform repo when the change truly affects multiple business modules.
- Monitoring, alerting, cleanup, audit, or runtime configuration changes usually impact an ops/observability module in addition to the owning business module.
- New or changed integration contracts usually impact the owning integration module and any directly coupled consumer/provider modules.

## Repo Mapping Rule

After selecting modules, translate them to repositories using the `Target Repo` column in `docs/modules/module-map.md`.

- Keep `affected_repos` limited to repos that must change for this requirement.
- Do not add transitive repos unless the discussion makes the change explicit.

## When To Escalate

Recommend an RFC if the requirement:

- Re-draws module boundaries
- Introduces a new platform pattern or shared abstraction
- Requires a new protocol or integration contract
- Forces broad shared-library changes without a settled design
