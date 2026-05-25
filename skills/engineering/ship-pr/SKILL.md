# ship-pr

Ship a finished branch through solar-scout's PR pipeline: draft PR → CodeRabbit
review → address findings → squash-merge → hand back the link.

## Hard facts about this repo

- **GitHub MCP tools only** (`mcp__github__*`). There is no `gh` CLI.
- **CodeRabbit is the only automated reviewer.** There is NO GitHub Actions test
  CI — `get_check_runs` returns 0. "Green CI" here means CodeRabbit and nothing else.
- **CodeRabbit skips draft PRs** — you must trigger it by hand (step 3).
- **Draft PRs cannot be merged** — mark them ready first.
- Merges are **squash**, with commit title `... (#NN)`.
- `main` auto-deploys to production via Railway, so a merge goes live. Confirm the
  user actually wants it live before merging.

## Steps

### 1. Verify before shipping
```bash
python -m pytest tests/ -q                                   # must be green
python -c "import ast; ast.parse(open('app.py').read()); print('OK')"
```
Keep one concern per PR — rebase onto `main` and drop anything unrelated
(e.g. a stray commit already merged elsewhere) so the diff is only your change.

### 2. Push + open a DRAFT PR
- `git push -u origin <branch>` (retry on network errors, 2/4/8/16s backoff).
- `mcp__github__create_pull_request` with `draft: true`, `base: main`.

### 3. Trigger the review (drafts are skipped)
- `mcp__github__add_issue_comment`, body `@coderabbitai review`.
- Then STOP and wait for the webhook event. Never poll with `sleep`.

### 4. Address findings — critically, not blindly
- Verify each claim before acting (narrow vs broad fix, unverified APIs, etc.).
- Fix confident/small items, push, let CodeRabbit re-check.
- Ambiguous or architectural → ask the user (`AskUserQuestion`).
- Reply on the PR only to explain a declined or deviated-from suggestion.

### 5. Mark ready + squash-merge
- `mcp__github__update_pull_request` with `draft: false`.
- `mcp__github__merge_pull_request`, `merge_method: squash`,
  `commit_title: "<PR title> (#NN)"`.

### 6. Hand back the link
Give the user the PR URL + merge-commit sha, and note that Railway will deploy `main`.

## Smoke-test the app first (optional but recommended)

A fresh cloud box is missing deps and has no API keys. Install with the workarounds
that are otherwise painful to rediscover:
```bash
SETUPTOOLS_USE_DISTUTILS=stdlib python -m pip install -q -r requirements.txt
# ^ the env var fixes the googlemaps sdist build under Debian's setuptools
python -m pip install -q supabase --ignore-installed PyJWT
# ^ Debian-managed PyJWT can't be uninstalled by pip; ignore it
```
Boot headlessly — catches load crashes and deprecation warnings without a browser:
```bash
python -c "from streamlit.testing.v1 import AppTest; at=AppTest.from_file('app.py'); at.run(); print('exception:', at.exception)"
```
Empty `at.exception` = the app loads. A real scan (images → AI → leads) needs live
keys and CANNOT run in the cloud box — say so rather than claiming the scan works.
