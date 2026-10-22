# Letter Smith publishing identity and repository plan

Letter Smith already publishes under the connected user's GitHub account. The current publisher does not route new customers to TheTola. The missing pieces are explicit destination selection, repository management, flexible paths, and safe migration. This document covers the investigation and proposed changes; no application behavior or GitHub resources were changed.

Inspection date: October 20, 2026. Local HEAD: `5f041ce7c8fd6038c1d4873856497a193d72c4ff`, plus existing uncommitted work. Source references describe that working tree, including its newer GitHub library implementation.

## A Current system assessment

### Customer identity and developer identity

`GitHubAuthenticator._validated_session()` calls authenticated `GET /user` and builds the session from GitHub's returned login and numeric account ID. `GitHubPagesPublisher.publish()` uses that session login as owner. `github_pages_repository_name()` derives `<login>.github.io`. No literal TheTola destination was found in the publishing modules.

There are three separate identities:

| Identity | Current behavior | Implication |
| --- | --- | --- |
| Publishing account | Authenticated GitHub user | Michael publishes under Michael's account. |
| GitHub integration | Shared `letter-smith-publisher` GitHub App, owned by TheTola | Customers authorize the product integration; they do not inherit the developer's token. |
| Product creator | `Oluwatola Ayedun` in `application_identity.py`, used by About/support information | Creator attribution is separate from the letter's publishing owner. |

The public client ID and app slug in `publishing/github_config.py` identify the integration; they are not access credentials. Tokens and refresh tokens use `GitHubCredentialStore` and OS keyring, not the release manifest or project settings. Explicit isolated development profiles disable access to the real keyring store.

Live `GET /apps/letter-smith-publisher` returned owner `TheTola`, homepage `https://github.com/TheTola`, and Administration/Contents/Pages write plus Metadata read permissions. The homepage is a visible personal-brand connection worth replacing with a real product homepage. A shared developer-operated integration remains a dependency even when customer repositories are independent. Moving the integration to a product organization is a separate operational decision, not a requirement for customer-owned URLs. Do not rotate its identity casually and invalidate existing connections.

GitHub permits an app to act only within the intersection of user and app access. External-customer onboarding still needs a real second-account test and verification of app visibility/device-flow settings. Public installation requires a public app registration. [Authorization rules](https://docs.github.com/en/apps/using-github-apps/authorizing-github-apps), [app visibility](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/making-a-github-app-public-or-private).

### Current publishing behavior

| Question | Verified answer and source |
| --- | --- |
| What selects the repository? | `github_config.py:github_pages_repository_name`, `github_pages.py:configure/publish`, and authentication access checks all derive the personal-site repository. |
| Can the user choose a custom repository? | No current selection interface. Stored session repository fields are access metadata; publishing still recomputes the destination. |
| When is it created? | `_ensure_repository()` reads the derived repository and posts to `/user/repos` on `not_found`; it creates a public repository during publishing. |
| Is there confirmation? | `Forge_Tab.py:publish_letter()` asks whether the finished letter should become public, using a saved acknowledgement. It does not preview and separately confirm repository creation or Pages reconfiguration. |
| How are cards organized? | One shared personal-site repository, one top-level folder per card. The legacy `LetterSmith-Published` layout uses `letters/<path>/`. |
| How are paths named? | `_slugify_title()` normalizes/transliterates to lowercase ASCII and hyphens, with an 80-character limit. `Valley & Shepard` becomes `valley-shepard`. Entirely non-ASCII titles can collapse to an empty slug and fail. |
| Is the original title overwritten? | The publisher reads `recipient_title` and stores the original text in the publication marker. Slug creation does not write a replacement title. |
| What happens on republish? | Saved paths survive title changes. Matching project markers guard updates; stale files inside that letter's folder are removed. Other letter folders remain. |
| How are collisions handled? | Existing folder names are compared case-insensitively and get suffixes such as `-2`. The allocator currently considers paths containing `/`; extend it to account for root files as well. |
| How are retries handled? | `_recover_public_path()` finds markers by project UUID after a prior upload lacked local success metadata. Ambiguous or mismatched identities fail instead of being silently reused. |
| What is recorded locally? | Settings include owner, repository, public path, URL, timestamps, provider, verification flag, and source fingerprint. Forge and saved-letter publication records also carry this state. |
| What establishes success? | The publisher checks exact hosted marker and index bytes before recording success. Unpublish requires confirmed absence; network errors preserve local records. |
| Can accounts be switched? | Sign out/sign in changes the connection. Existing publications belonging to another login are rejected for republish/unpublish and require reconnecting the original account. There is no migration workflow. |
| Can destinations change safely? | No. Republish rejects a saved repository different from the derived personal site and currently tells the user to unpublish first. That is unsuitable as the migration strategy. |

`_ensure_pages()` uses the repository default branch and root source. It can change an existing Pages source to those values. Although it returns GitHub's `html_url`, `publish()` ignores that result and reconstructs the default hostname. Custom domains and existing alternative publishing sources therefore need explicit handling.

The publisher adds `.nojekyll`, `.lettersmith-publishing.json`, and a generic Letter Smith root page if needed. The root page is product-branded, not personally branded. Existing root index content is normally preserved, but choosing an existing site requires inspecting all shared configuration changes before applying them.

`publishing/github_library.py` also hardcodes personal-site, legacy, and private backup repository names in discovery, API-target validation, cache decoding, and URL reconstruction. A publisher-only change would leave custom destinations missing or rejected in the library. Private editable backups in `LetterSmith-Projects` are a separate feature; never turn them into public publishing content.

### How your repository originated

Read-only GitHub API inspection verified:

- `TheTola/TheTola.github.io` was created August 28, 2026 at 22:25:50 UTC. Its initial commit was `82e58ba` one second later.
- Commit [`cccb5c7`](https://github.com/TheTola/TheTola.github.io/commit/cccb5c72bfbfac11f08702ca610ac0ff43bb394a) at 22:30:43 UTC says “Migrate Letter Smith publications to clean URLs.” Commit `cdba965` then completed migrated publication fingerprints.
- Its current inspected head was `abb49ba088e17426c72042d20b5cec67e1fc1069`, dated October 14 UTC, with message “Update published Letter Smith letter.” Pages reported `built`, branch `main`, source `/`, URL `https://thetola.github.io/`, and no custom domain.
- `TheTola/LetterSmith-Published` reported no Pages site. Its latest commit was `c6f1704`, August 28, “Unpublish legacy Letter Smith site.”
- Local source history contains the username-derived helper in commit `54f1512`, dated August 29.

This establishes a historical migration to the personal-site layout and explains current selection. It does not establish which program or person issued the original repository-creation request; commit history is not a repository-creation audit log.

### Distribution evidence and limits

The release specification includes explicitly listed resources rather than the whole development folder. The inspected manifest does not package root `settings.json`. Release validation rejects stock/example publication URLs. A focused scan of all 25 JSON files under stock letters and examples found no `TheTola` strings or nonempty owner/repository/publication URL fields.

That is source/seed evidence, not proof about an existing installer. A fresh packaged installation must still start disconnected and produce its own publication identity after creating a letter from stock/example content. Inspect outgoing bundles, shortcuts, URLs, logs, and release payloads without displaying secrets. Existing public product metadata placeholders in `application_identity.py` also need real release values; they are unrelated to repository routing.

Validation this investigation: 72 existing publishing/authentication/UI-unit tests passed using `.\.venv\Scripts\python.exe -m unittest discover -s test -p test_github_publishing.py -q`; 18 library tests passed using the same command with `test_github_library.py`. The publishing coverage includes different accounts receiving different URLs, owner mismatch rejection, title-change path preservation, collision suffixes, timeout recovery, and keyring behavior. These use simulated GitHub responses. Live work was read-only API inspection, not a publish/republish/unpublish transaction or a fresh-customer install.

## B Proposed architecture

### Destination and publication identity

Extend existing authentication, profile paths, atomic JSON persistence, settings, and saved-letter records. Keep one shared repository per collection and one card folder per publication. Do not introduce another authoring model or provider.

Add a small versioned collection registry under the existing application profile settings directory. Store nonsecret configuration only: collection ID, friendly name, authenticated account ID, repository owner ID/login, repository ID/name, site type, publishing branch/source directory, card prefix, confirmed Pages base URL, and validation state. Persist the default collection by account ID. Keep credentials exclusively in keyring.

Each published card retains an independent destination snapshot, project UUID, raw path, exact URL, and verification record. Add stable account/repository IDs and collection association without removing existing fields. Default destination changes affect new publications only; republishing and unpublishing use the card's saved destination. Friendly-name changes affect labels only. Existing cards remain visible after sign-out and show which account must reconnect.

Use numeric GitHub IDs to detect account renames, repository renames/transfers, and reuse of an old repository name. Re-fetch current identity and permissions before mutation. A changed owner or account must never silently inherit a destination. Reuse the existing connection-generation guards so switching accounts invalidates pending jobs and stale callbacks.

### Repository compatibility and setup

Support personal sites and public project repositories owned by the connected personal account initially. Clearly reject unsupported organization ownership rather than guessing access. Paginate repository and installation listings. A 404 can mean inaccessible, so handle name conflicts and creation 422 responses without selecting or modifying an unrelated repository.

Existing branch-based Pages sources may use any supported branch with `/` or `/docs`; preserve that configuration. New collections use the repository's actual default branch and `/`. A `docs` publishing source changes the Git tree prefix, not the website URL prefix. Preserve root content, `CNAME`, existing workflows, and other folders. Mark unsupported/custom Actions deployments incompatible unless a separately reviewed implementation supports them; never silently replace them.

Repository names and card folders need different validators. For newly entered repository names, use a conservative ASCII letters/digits/dot/underscore/hyphen policy, reject separators and reserved dot names, and surface GitHub's authoritative validation/conflict response. Clearly label application restrictions rather than claiming they are all GitHub's restrictions. Existing compatible repository names must not be rewritten by the new-name validator.

GitHub defines the default patterns below; custom domains can change the base address. Use validated Pages `html_url` after configuration rather than guessing the deployed base. Preserve existing custom-domain configuration and report HTTPS readiness failures accurately. [Site types](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages), [publishing sources](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site), [Pages API](https://docs.github.com/en/rest/pages/pages#get-a-github-pages-site).

| Collection | Default card URL |
| --- | --- |
| Michael's personal site | `https://michael.github.io/card/` |
| Michael's project `My-Letter-Collection` | `https://michael.github.io/My-Letter-Collection/card/` |
| Your project `My-Letter-Collection` | `https://thetola.github.io/My-Letter-Collection/card/` |

Changing your repository name cannot remove `thetola.github.io`. A custom domain or different publishing account is needed for that. A friendly collection name changes neither hostname nor repository name. Custom-domain purchase/DNS setup is separate; this change should recognize configured domains without silently modifying DNS.

### Titles, paths, and URLs

Maintain three separate values: exact visible title, raw folder component, and encoded URL. Add “Readable slug,” “Use title,” and “Custom folder” choices. Existing publications keep their path regardless of title changes. Path changes require a separate copy-and-verify operation.

For new title/custom paths, propose NFC normalization for the folder while leaving title text untouched. Preserve case, spaces, ampersands, and supported Unicode. Validate one component, reject `/`, `\`, dot traversal, NUL/control characters, Windows-reserved characters/device names, leading/trailing spaces, and trailing dots. Reserve application/site control names and existing file paths. Initially reject literal `%` and invisible directional controls as explicit application policy to avoid decoding ambiguity. Do not silently rewrite unsupported names; offer a suggested slug with a preview. Bound raw UTF-8 component length and full local staging paths; use a documented conservative 180-byte component budget, lowering it if actual staging constraints require.

Encode each raw URL segment once with UTF-8 `quote(segment, safe='')`; do not use query/form encoding or encode the entire URL. `Valley & Shepard` becomes `Valley%20%26%20Shepard`. Use raw names in Git tree JSON, and properly encoded components in API request paths. Escape display text separately for HTML/Qt. Percent encoding does not make a traversal or unsupported filesystem name safe. [Python quoting](https://docs.python.org/3/library/urllib.parse.html#urllib.parse.quote), [URI syntax](https://www.rfc-editor.org/rfc/rfc3986), [Windows naming](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file).

Compare collisions using Unicode normalization plus case folding, while preserving the actual folder bytes/case. Include root files, directories, aliases, legacy prefixes, and reserved names. Never mutate historical paths merely to normalize them. A concurrent remote change requires re-reading the tree and a new preview if the allocated path changes; never silently publish somewhere other than the confirmed URL. The ampersand example's encoding was verified locally; actual Pages serving and browser/media behavior for the new path policy require live acceptance tests.

## C Migration strategy

### Existing installations

1. Read existing session hints, settings, saved publication snapshots, and remote markers. Preserve the raw URL and path. Match account/repository IDs when accessible; retain unresolved records without substituting the signed-in account.
2. Register existing destinations locally without remote writes. Keep legacy `LetterSmith-Published/letters/<path>/` bindings. Conflicting evidence produces a review state, not a guessed destination.
3. Permit normal updates at the original compatible destination, including legacy destinations. Do not require unpublishing before selecting another collection.
4. Save new publication fields through existing persistence paths and migrate their readers/writers together. If introducing multiple historical locations, use one versioned publication-location extension and update settings, saved-project records, snapshots, library caches, and export/import consistently. Preserve the existing primary fields for compatibility. Do not claim older binaries support newly configured destinations.
5. Keep a resumable journal under `ApplicationPaths.migration_root`: source/target IDs, source commit, chosen mappings, hashes, target commits, verification results, and local-record updates. Record completed remote actions before advancing local state. Cancellation or restart resumes from evidence rather than repeating writes blindly.

### Your recommended migration

Keep `TheTola/TheTola.github.io` and its current letter folders operating. Give it a friendly label such as “Original letters.” Then create or select your chosen project collection through the confirmed setup flow. Set that as the default for new letters.

To migrate selected existing letters, copy their verified published contents to the new repository, retaining the old copies. Pin the source commit so a concurrent republish cannot mix revisions. Preserve titles and paths unless explicitly changed. Inventory every blob under each card, reject symlinks/unsafe paths, and compare source/target bytes or content digests. Verify the deployed marker, index, media, and rendered behavior before switching the primary publication record. Keep old URLs as separately tracked retained locations with their last verified revision; copies do not automatically stay synchronized.

If a copy fails, the old URL remains primary. If the upload succeeds but deployment or local persistence fails, mark the operation pending, keep both locations recorded, and resume verification. Never automatically remove the target or old repository as rollback. Do not assume every published presentation includes editable project source; migrate the hosted presentation and associate a local editable project only when its UUID matches.

### Rename, switch, copy, and remove

| Action | What changes | Link consequences |
| --- | --- | --- |
| Edit collection name | Local label | None. |
| Change default destination | Future new publications | Existing cards stay bound to their original location. |
| Rename repository | GitHub name, same repository ID | Pages address may change; all affected cards need a plan and revalidation. |
| Copy published letters | New hosted copies and tracked locations | Old links remain while old hosting remains. |
| Remove old hosted copies | Explicitly selected old folders only | Their old URLs may stop working; Git history can retain old content. |

GitHub redirects repository URLs after renaming but excludes Pages project-site URLs. Do not promise old website redirects. Renaming a personal-site repository away from `<owner>.github.io` also changes its site model. Recommend copy-to-new for your transition; do not rename the existing personal site as the default migration. [Repository rename behavior](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository).

Optional static redirect pages require retaining control of the old hosting location and an explicit replacement operation. They are not GitHub-provided HTTP 301/308 guarantees, may not cover asset/deep links, and do not preserve the old letter as an independent copy. Keep the old letter by default. A later “move” must be implemented as copy, verify, then separately confirm removal; repository deletion is outside this workflow.

## D User interface specification

Add “Publishing collection” to the existing GitHub account area and Forge destination summary, using current dialogs, theme, busy state, and cancellation patterns.

| Screen | Controls and behavior |
| --- | --- |
| Connect | Existing Sign in with GitHub; show authenticated account. Cancel performs no repository mutations. |
| Collection setup | Personal site / New project repository / Existing repository. Personal name is read-only with the naming explanation. Project name is editable. Existing list has Refresh, compatibility status, and permission repair. |
| Name and preview | Friendly collection name; account; exact `owner/repo`; repository link; source branch/folder; expected Pages base and example card URL; “Create new” or “Use existing” status. |
| Confirm setup | “Create collection” or “Use repository,” plus Cancel. State every proposed remote change, including public visibility and Pages-source configuration. Creation happens only here. Persist readiness only after validation. |
| Publish | Read-only title, selected collection, path choice, raw folder, encoded final link, and “Publish letter.” Explain that repository contents and letter content are public. If a collision changes the link, require a refreshed confirmation. |
| Existing publication | Show exact saved destination and link. “Update published letter” keeps the address. “Publish a copy elsewhere” is separate. |
| Manage collection | Edit display name; Choose another destination; Create collection; Copy published letters; Advanced: Rename repository. Show account, repository, source, and last checked URL. |
| Switch destination | “Use for new letters” and Cancel. State that existing letters remain at their saved locations; provide a separate migration action. |
| Copy letters | Select cards, show old/new URLs and collision proposals, default to retaining old copies. Confirm “Copy selected letters”; show progress and resumable failures per card. |
| Rename repository | Show old/new repository and every affected known URL; distinguish untracked content in an existing repo. Explicit “Rename repository” confirmation with link-impact acknowledgement. Disable when permissions or source compatibility are insufficient. |
| Account switch | Sign out/sign in; retain local cards and destination records. Clear active readiness and cancel/invalidate old-account work. Show “Reconnect [account] to update” for other-account cards. |

A new repository left behind by a partially failed setup is shown as “Created; setup incomplete,” with Retry and Open on GitHub. Cancel does not delete it. No repository deletion control is needed.

## E Implementation handoff

Use [the implementation prompt](github-publishing-implementation-prompt-2026-10-20.md). Implementation is a separate task. The investigation and design are complete; new configuration, rename, migration, and title-preserving paths have not been implemented.
