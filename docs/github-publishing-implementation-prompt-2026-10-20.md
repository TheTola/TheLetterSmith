# Letter Smith publishing implementation prompt

Implement the approved GitHub publishing improvements in `C:\Users\Oluwatola Ayedun\Desktop\SmithLetter`, following `docs/github-publishing-assessment-2026-10-20.md`. Recheck the current working tree before editing: the assessment examined HEAD `5f041ce7c8fd6038c1d4873856497a193d72c4ff` with unrelated uncommitted work. Preserve that work. Follow applicable AGENTS instructions, existing architecture, naming, and UI patterns. Read relevant files only. Do not add dependencies without a demonstrated need.

The objective is customer-owned publishing with explicit, remembered repository choices, clear URL previews, safe collection management, and stable existing links. One collection uses one shared repository with separate card folders. Never create a repository per card.

## Phase 1 Establish compatibility and destination records

Audit these actual integration points before changing them:

- `publishing/github_config.py`: public app configuration and `github_pages_repository_name`.
- `publishing/github_auth.py`: `GitHubSession`, `GitHubCredentialStore`, `_validated_session`, `_github_app_publishing_access`, `_oauth_publishing_access`, `_target_repository_access`, installation pagination, connection persistence, and generation guards.
- `publishing/github_pages.py`: configure/publish/unpublish, `_ensure_repository`, `_ensure_pages`, `_publication_prefix`, `_publication_url`, `_valid_public_path`, `_allocate_public_path`, `_recover_public_path`, marker validation, tree updates, and hosted verification.
- `publishing/github_library.py`: repository allowlist, discovery, cache parsing, URL reconstruction, download safety, and distinction between public presentations and private editable backups.
- `Forge_Tab.py`, `publishing/github_ui.py`, `github_library_ui.py`: authentication, publish confirmation, job lifecycle, account display, and library flows.
- `settings_store.py`, `project_paths.py`, `saved_letters.py`, and the canonical saved-project publication readers/writers reached from them. Extend these contracts consistently; do not create a competing project serializer.
- `release/LetterSmith.spec`, `release/release_manifest.json`, `release/build_release.py`, existing stock/example metadata, and `application_identity.py` for distribution boundaries.

Add a small versioned nonsecret collection registry under the existing application-profile settings path using existing atomic persistence helpers. Each collection records a stable local ID, friendly name, authenticated account ID, repository owner ID/login, repository ID/name, personal/project site type, source branch/directory, optional legacy card prefix, confirmed Pages base URL, and validation state. Default collection is account-specific. Tokens stay in OS keyring.

Extend existing publication records with stable identity and destination associations without dropping their current owner/repository/path/URL fields. Retain per-card destination snapshots independently of the default collection. Use one publication-location extension if retained copies need history, and update every actual persistence boundary together. Define version handling so unsupported newer metadata is preserved or rejected clearly, never silently discarded.

Import existing personal and legacy destinations locally without remote writes. Preserve exact paths/URLs and project IDs. Resolve conflicting or offline metadata as pending review. Changing the default must never redirect an existing card's update or unpublish request. Revalidate account/repository IDs before mutation; detect renames, transfers, name reuse, and stale callbacks. Keep disconnected and other-account cards visible but require the correct account for writes.

## Phase 2 Explicit repository setup and publishing

Extend the existing account/Forge UI with personal site, new named project, and existing compatible repository choices; friendly name; owner/repository/source summary; and URL preview. Use existing theme and dialog components.

List all accessible candidates with pagination and exact owner/permission validation. Initial scope is public repositories owned by the connected personal account. Clearly explain unsupported organization/private/custom-Actions configurations; do not change their visibility or deployment settings implicitly. Support existing compatible branch sources at `/` and `/docs` and preserve their branch. For new repositories use the actual default branch and root. Keep Git paths distinct from URL paths: `/docs` is not a URL prefix.

Perform setup discovery and preview read-only. Only the final explicitly labelled confirmation may create a repository or change Pages settings. Display proposed visibility, files, source changes, and whether the repository is new or reused. Preserve unrelated content, existing root index, CNAME, workflows, and other cards. A partial creation is journaled and resumable, not silently deleted.

Thread the chosen destination through access checks, publish/unpublish, marker recovery, library, caches, and retries. Remove recomputation of the personal site as the universal target. Preserve the helper for the personal-site option and legacy compatibility. Handle 404 versus access uncertainty, creation conflicts, missing installation access, protected branches, and permission repair explicitly. Use selected-repository access when practical; do not demand all-repository access by default.

Use Pages API `html_url` as the validated deployed base, including already configured custom domains. Before creation show an explicitly expected URL. Preserve HTTPS verification, public-fetch credential isolation, timeouts, refresh, cancellation, and error classification. Keep the current exact hosted-marker/index verification and project-UUID recovery protections. Never report a queued commit as verified publication.

## Phase 3 Path customization and collision safety

Keep exact `recipient_title`, raw folder name, and encoded URL separate. Offer Readable slug, Use title, and Custom folder. Existing publications keep their saved path unless a separate rename/copy action is confirmed.

Retain spaces/case/ampersands/supported Unicode in new raw folder components. Follow the assessment's explicit safe-component policy, NFC normalization for newly selected folders, and byte/path limits. Do not lowercase historical names or decode arbitrary path input. Preserve title text even when a proposed folder is invalid. Use a distinct repository-name validator and GitHub's authoritative validation for repository creation/rename.

Use UTF-8 `quote(segment, safe='')` once per URL segment and raw folder text in Git tree JSON. Do not use `quote_plus`, encode the whole URL, or treat percent escapes as filesystem authorization. Escape UI/HTML text independently. Validate staging/download paths and Windows reserved names.

Check file and directory collisions, Unicode normalization/case equivalents, prefixes, and reserved control names. Prevent replacement of another project's folder with UUID/marker checks. If a concurrent update changes a proposed path, stop and obtain a new preview confirmation. Keep branch updates non-forced and reconcile stale heads safely.

Verify new encoded paths on actual Pages and in rendered browsers before declaring full support. Include relative image/audio links, downloaded presentations, sharing/copying links, and project-site prefixes in that validation.

## Phase 4 Collection management and recoverable migration

Implement separate actions for editing a friendly label, switching the default for new letters, renaming the actual repository, copying existing published letters, and optionally removing explicitly selected old copies after verification. Never combine these silently.

Repository rename requires admin capability, a name/conflict check, a before/after URL plan covering all known publications, explicit link-impact confirmation, and journaled reconciliation by repository ID. Warn about untracked content and personal-site naming changes. Verify actual new Pages configuration/base and cards before marking records verified. Do not promise Pages redirects or automatically recreate an old repository name.

For migration, inventory marker-identified published cards and pin a source commit. Preview exact old/new paths and URLs. Default to copying while preserving old hosting. Copy public presentation assets only; private backups and editable authoring records must never be published accidentally. Reject unsafe paths/symlinks and preserve independent project IDs. Validate bytes/digests in the target and public marker/index, then exercise hosted media and rendering.

Journal operations using `ApplicationPaths.migration_root` and existing atomic helpers. Track operation ID, source/target account and repository IDs, source head, mappings, target commits, hashes, verification state, and local reconciliation. Recover after cancellation, network errors, process termination, and local persistence failure. A lost response must be reconciled before retrying a creation/rename. Never undo by deleting repositories or resetting another writer's commits.

After verified copy, change the primary record only as confirmed and retain the original location with its revision/status. Retained copies are not automatically synchronized. On partial failure leave the original publication usable. A later move is copy plus verified completion plus a separate explicit old-copy removal confirmation. Removing hosted files does not erase Git history. Never bulk-delete a repository.

Preserve your current TheTola personal site by default. A new project repository still uses the account's `github.io` hostname; eliminating the personal hostname requires a separate account or custom domain. DNS/domain purchases and GitHub App ownership/branding changes are separate operations, not implicit side effects of this implementation.

## Phase 5 Tests and acceptance

Extend existing focused test files where practical; add new files only for substantial new modules. Use synthetic users and isolated temporary profiles, never live credentials in fixtures. The assessment ran 72 current publishing tests successfully; rerun against the actual edited tree rather than treating that as current proof.

Cover:

- Two independent accounts, account switching during an operation, restore/refresh/sign-out, and no developer account fallback.
- Secure token storage and redaction; no tokens in settings, markers, generated bundles, caches, journals, or installers.
- Setup cancellation causes zero remote mutations; confirmed creation/reuse; pagination; selected installation permissions; misleading 404, name conflicts, and partial setup retries.
- Personal/project/custom-domain bases; non-main branches; `/docs`; preservation of unrelated Pages configuration; unsupported Actions/private/organization targets.
- Legacy destination import, update/unpublish on original destinations, default switching without migration, and saved-project/export/import persistence.
- Exact titles; `Valley & Shepard`; non-ASCII titles; reserved characters; literal percent rejection; traversal; Unicode/case collisions; root-file collisions; encoded URLs and asset resolution.
- Project-marker ownership, concurrent writes, stable republish paths, timeout recovery without duplicate folders, unpublish network failures retaining metadata.
- Friendly rename versus repository rename versus migration; every migration checkpoint and restart; partial deployment/local-save failure; unchanged old copies; renamed/transferred repository detection.
- Library discovery, cache invalidation, downloads, and restore for selected and retained destinations without exposing private editable backups.
- Fresh package starts disconnected; stock/example copies have fresh identities and empty publication bindings; release payload excludes developer settings/credentials.

Run the existing relevant publishing, TLS, library, settings/persistence, and packaging checks using the repository's documented commands. Do not broaden into unrelated suites without a reason. Record commands and actual results.

For live acceptance, prepare a specific disposable collection and synthetic card plan, then obtain any needed explicit authorization for real external writes. Never use production letters as a test fixture. Exercise first publish, same-URL update, collision prevention, encoded-title path, project-site media, restart/retry, copy migration, library discovery, and confirmed unpublish. A second independent GitHub account must complete onboarding with the packaged app. Do not substitute mocked tests, API metadata, or successful launch for this evidence. Do not delete repositories as cleanup without explicit authorization.

Verify the public GitHub App can be installed by customers and its user authorization flow works. Report its current personal homepage and product release metadata placeholders as separate distribution work; do not invent URLs or remove legitimate creator attribution to mask missing configuration.

At each phase report `Phase X/5 — [goal]. Phases remaining: N.` Complete the phase before advancing. Finish with changed files, behavior, exact checks, source versus live/package evidence, limitations, and a clear task status. Do not claim distribution-ready publishing while the real second-account/package or migration acceptance remains unverified.
