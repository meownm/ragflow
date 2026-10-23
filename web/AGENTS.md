# Frontend agent instructions

Read [the repository guide](../AGENTS.md) first. These rules apply to work in `web/` by any agent, including tasks started at the repository root.

## Product and visual consistency

- For new screens, changed user journeys, interface copy or visual consistency reviews, read [the project profile](../docs/develop/frontend-product-design-ru.md) and use the shared `frontend-product-design` skill when available. Follow existing tokens and components rather than inventing a style for each page.
- Keep user-facing task, result and recovery clear. Show technical details when they support the audience's decision; move safe implementation diagnostics to details rather than using internal versions, IDs or raw exceptions as primary interface copy. Preserve meaningful technical content in SQL and specification documents.
- Select the domain customer/end-user role from the project profile: `business_analyst`, `systems_analyst` or `data_analyst`. `product_manager` owns product scenario/copy proposals, `ux_ui_designer` owns interaction/layout/state proposals, and `art_director` owns visual hierarchy/consistency proposals. The main agent owns implementation and verification. Delegate according to the user request or applicable instructions; small fixes do not require every role. Recommendations do not authorize shared-library edits or deployment.
- Review complete affected states and adjacent screens, including standalone/embedded variants. Distinguish browser evidence, source inspection and proposed acceptance; record actual viewport/theme and any runtime/source mismatch.

## Stack and checks

React/TypeScript/Vite, shadcn/ui, Tailwind CSS, Zustand, TanStack Query and react-i18next.

- Use existing prepared dependencies. For CI and qualified frontend artifacts, use the pinned pnpm/frozen-lockfile process documented in [the local guide](../deployment/local/README.md) and implemented by `tools/quality/frontend_artifact.py`. Hook preparation has its existing `tools/hooks/prepare_web.py` npm path. Do not switch package managers or regenerate either lockfile as an unrelated part of a UI change.
- Run commands from `web/`: `npm run dev`, `npm run build`, `npm run type-check`; scripts are defined in `package.json` and may also be invoked with the prepared pnpm environment.
- Prefer `npm run test:focused -- --runInBand <test-path>` for focused Jest tests and changed-file ESLint without `--fix`. `npm run test` collects coverage; use it when the affected behavior requires that lane. `npm run lint` checks all of `src/`.
- Use [the regression guide](../test/REGRESSION.md) for browser lanes and their prerequisites. Build in a managed disposable workspace when a separate dependency/source tree is needed; keep test evidence outside the reviewed source.

## Layout and shared components

- For CSS/layout defects, inspect the full parent hierarchy up to the nearest non-flex ancestor, including `flex-shrink`, `min-width` and `overflow`. Before editing, explain the actual constraint and why the proposed change fixes it. Do not repeatedly apply `min-w-0` without verifying the cause.
- After each structural component extraction/refactor, verify original props/behavior, parent layout, buttons, alignment, responsive behavior and syntax/types. Pay attention to `flex-1`, conditional rendering and flex direction.
- `src/components/ui/` contains shared primitives and project components. Editing, refactoring, restyling, adding components there or upgrading a primitive requires explicit user authorization for that shared-library change in the current conversation. Prefer composition or a wrapper outside this folder when the task only concerns one consumer. Do not request authorization again when it has already been given.
- Prefer `requestAnimationFrame` or `useLayoutEffect` to `setTimeout(..., 0)` for focus/DOM measurement. Keep components lean, extract complex logic into appropriate hooks/utils and reuse repeated JSX structures. Use PascalCase for constants and component names.

## Queries and network boundaries

- Use a query-key factory `{Domain}Keys` in the hooks' file, returning `as const` tuples. Queries and invalidations must reference the same factory; do not duplicate raw key arrays.
- For cache defects, find every consuming query/component and mutation invalidation, compare complete keys (including optional parameters such as `refreshCount`) and check whether parent rendering remounts observers.
- Hooks in `src/hooks/use-xx-request.ts(x)` call services, not `@/utils/request`, `@/utils/next-request` or `@/utils/api` directly. Services register endpoints with `registerNextServer` through the shared axios instance in `src/utils/next-request.ts`, which owns token handling, 401 redirects and error notifications.
- Response/data types belong in `src/interfaces/database/`; request parameters/bodies in `src/interfaces/request/`.
- Model/provider/factory endpoints belong in `src/services/llm-service.ts`. Register GET endpoints with `method: 'get'`; pass native axios config using the service's second `true` argument, for example `service.listProviders({ params: { available: true } }, true)`.

## Translation and reuse

- Add translation keys only to the requested language files; do not propagate to every locale without a request. English uses sentence case, preserving proper nouns. Prefer `useTranslation` from react-i18next over `useTranslate` wrappers.
- Use dayjs for dates/times; prefer existing project hooks/utilities before adding dependencies. ahooks and Lodash are available for appropriate common operations.
- Before adding validation, existence checks, API calls or mutation helpers, search the same file and related modules for current implementations. Reuse them; extract genuinely repeated logic into an appropriate shared hook/utility without introducing pass-through wrappers.
