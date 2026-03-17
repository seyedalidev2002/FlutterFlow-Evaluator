---
name: flutterflow-audit
description: Run a comprehensive read-only audit of a FlutterFlow project by calling the FlutterFlow API directly (no MCP required). Fetches project YAML, extracts and unzips it locally, then produces a structured Markdown audit report covering security , architecture (folder structure, naming conventions, component hygiene, data model consistency), authentication,analytics, crashlytics, and environment variable configuration.Trigger this skill whenever the user asks to "audit", "review", "check", "inspect", or "analyse" their FlutterFlow project, or when they mention security concerns, code quality, naming conventions, or wanting to understand the health of their FF project.

compatibility:
  requires: python3, requests, pyyaml
scripts:

- fetch_project.py     — Phase 0B: fetch & unzip project YAML from FlutterFlow API
- extract_names.py     — Phase 0C: extract names/types into names.json (context-safe)

---

# FlutterFlow Audit Skill

You are an expert FlutterFlow auditor. Your job is to fetch a FlutterFlow project's YAML via the
official API, extract it locally, analyse it with the provided scripts, and produce a structured
Markdown audit report.

**Golden rule: this is a read-only audit.** Never write back to the FlutterFlow API. Never modify
any YAML files. Only read, analyse, and report.

All scripts referenced below live alongside this SKILL.md file. Copy them to the working
directory before running.

---

## Phase 0 — Credentials & Setup

**Ask the user for these two values before doing anything else (if not already provided):**

1. **FlutterFlow API key** — FlutterFlow → Settings → Integrations → API Key
2. **Project ID** — visible in the project URL:
   `https://app.flutterflow.io/project/YOUR-PROJECT-ID`

Store as shell variables — **never echo the key**:

```bash
FF_API_KEY="<user's api key>"
FF_PROJECT_ID="<user's project id>"
```

Install dependencies once:

```bash
pip install requests pyyaml --break-system-packages -q
```

---

## Phase 0B — Fetch, Save & Unzip Project YAML

```bash
python3 fetch_project.py "$FF_API_KEY" "$FF_PROJECT_ID"
```

This saves `<projectId>.zip` and extracts all YAML to `./<projectId>_yaml/`.
See `fetch_project.py` for full details.

---

## Phase 0C — Lightweight Name Extraction

**Run this before opening any individual YAML files** to avoid context overflow:

```bash
python3 extract_names.py "${FF_PROJECT_ID}_yaml/" > names.json
python3 -c "
import json
d = json.load(open('extracted_names.json'))
print(f\"Pages: {len(d['pages'])}, Components: {len(d['components'])}, \"
      f\"Folders: {len(d['folders'])}, Empty folders: {len(d['empty_folders'])}, \"
      f\"API calls: {len(d['api_calls'])}\")
"
```

Load `extracted_names.json` into context. Use it for **all** naming convention audits. Only open individual
YAML files when deeper inspection of a specific item is required.

---

## Phase 1 — Data Collection

### 1B — Architecture Data

All naming data comes from `names.json`. For component depth inspection, open only the specific
YAML file at `names.json["components"][i]["file"]` for components flagged by name similarity.

### 1C — Platform & Service Configuration

```bash
YAML_DIR="${FF_PROJECT_ID}_yaml"

echo "=== Analytics ==="
grep -r "measurementId" "$YAML_DIR" --include="*.yaml" | head -5

echo "=== Crashlytics ==="
grep "enabled" "$YAML_DIR/firebase-crashlytics.yaml" 2>/dev/null || echo "firebase-crashlytics.yaml not found"

echo "=== Auth Provider ==="
grep -E "^(active|firebase|firebaseConfigFileInfos):" "$YAML_DIR/authentication.yaml" 2>/dev/null | head -10

echo "=== Environment Variables ==="
python3 - "$YAML_DIR/environment-settings.yaml" "$YAML_DIR/authentication.yaml" << 'PYEOF'
import yaml, sys

with open(sys.argv[1]) as f:
    env_settings = yaml.safe_load(f) or {}

current = env_settings.get('currentEnvironment', {})
print(f"Current environment: {current.get('name')} ({current.get('key')})")

with open(sys.argv[2]) as f:
    auth = yaml.safe_load(f) or {}

configs = auth.get('firebaseConfigFileInfos', [])
envs = [c['environment'] for c in configs if isinstance(c, dict) and 'environment' in c]
keys = [e.get('key') for e in envs]
print(f"Configured environments: {[e.get('name') for e in envs]}")

if len(envs) <= 1 or all(k == 'PROD' for k in keys):
    print("WARNING: Only PROD environment found — no DEV/staging separation.")
else:
    print("OK: Multiple environments configured.")
PYEOF

echo "=== Firestore Security Rules ==="
python3 - "$YAML_DIR/firestore-settings.yaml" << 'PYEOF'
import yaml, sys

with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)

rules = data.get('rules', {}).get('collectionRules', {})
write_ops = ['creat', 'update', 'delete']

critical = []
warnings = []

for col, rule in rules.items():
    if not isinstance(rule, dict):
        continue
    for op in write_ops:
        op_rule = rule.get(op) or {}
        if 'everyone' in op_rule:
            critical.append(f"  {col} → {op}: everyone")
        elif 'authenticatedUsers' in op_rule:
            warnings.append(f"  {col} → {op}: authenticatedUsers")

print("CRITICAL — write open to everyone (must fix):")
print('\n'.join(critical) if critical else "  None")
print("\nWARNING — write open to all authenticated users (restrict to taggedUsers + API endpoint):")
print('\n'.join(warnings) if warnings else "  None")
PYEOF
```

Absence of output = feature not configured → flag in report.

---

## Phase 2 — Analysis Rules

### Security

**Warnings:**

- Generic API key param matches
- App state vars from `names.json["app_state_vars"]` whose `name` contains `apiKey`, `secret`,
  `token`, `password`, or `auth` AND whose `default` field is non-empty

**Firestore Security Rules:**

- `creat`/`update`/`delete` set to `everyone` → **Critical** — completely open write access, no auth check whatsoever. Must be locked down immediately.
- `creat`/`update`/`delete` set to `authenticatedUsers` → **Warning** — any signed-in user can write. Recommend:
  1. Tighten the rule to `taggedUsers` (scoped to the record owner or an admin field), **and**
  2. Move the write behind a Cloud Function / API endpoint that validates business logic before committing.

**Severity:**

- 🔴 High: any critical finding or any `everyone` write rule
- 🟡 Medium: warnings only, or `authenticatedUsers` write rules present
- 🟢 Low: nothing found

### Naming Conventions

Detect dominant casing per category (≥ 60% = dominant). Flag deviations. No dominant style →
flag entire category as "inconsistent".

| Category | Expected | Examples |
|----------|----------|---------|
| Pages & Components | `PascalCase` | ✅ `LoginPage` — ❌ `loginPage`, `home_screen` |
| Folder Names | `PascalCase` | ✅ `AuthScreens` — ❌ `auth_screens`, `New Folder` |
| State Variables | `camelCase` | ✅ `isLoading` — ❌ `IsLoading`, `current_user_id` |
| Struct Names | `PascalCase`, singular | ✅ `Product` — ❌ `products`, `order_item` |
| Enum Names | `PascalCase`, singular | ✅ `PaymentStatus` — ❌ `payment_status` |
| Struct Field Names | `camelCase` | ✅ `firstName` — ❌ `FirstName`, `created_at` |
| Firestore Collections | `camelCase` or `snake_case` (consistent) | ❌ mixing both |

Also flag: placeholder names (`Copy of X`, `X New`, `Untitled`, `Page1`), names < 3 chars,
generic names (`data`, `temp`, `value`), structs ending in `Model` or `Data`.

### Architecture

**Folder red flags:**
>
- > 8 pages all in root with no folders
- Empty folders (list from `names.json["empty_folders"]` by name)
- Placeholder folder names (`New Folder`, `Untitled`, `Folder1`, `Misc`)
- Misplaced pages (e.g. `PaymentPage` inside `Auth/`)

**Component hygiene:**

- Similar names (`ProductCard` + `ProductCardNew`) → likely duplication
- 0 usages across pages → dead code
- > 40 widgets in component YAML → needs splitting

**Authentication:**

- Not Firebase Auth → recommend **Firebase Custom Token bridge**: keep existing provider for
  identity; mint a Firebase custom token server-side; sign into Firebase with it. Unlocks
  Firestore rules, FCM, and Crashlytics user tracking without a full migration.
- Firebase Auth detected → ✅ note and move on.

**Analytics & Crashlytics:**

- Firebase Analytics absent → recommend enabling (zero-config in FF; retention, funnels, events).
- Firebase Crashlytics absent → recommend enabling (catches Flutter + native crashes; essential
  for production).

**Environment Variables:**

- Absent or disabled → recommend enabling. Allows safe dev/staging/prod separation without
  hardcoding keys in YAML.
- Only `PROD` environment exists (no `DEV` or staging) → **High advisory**: running a single
  environment means every test, API key rotation, or schema change hits production directly.
  Strongly recommend adding a `DEV` environment with separate Firebase project, API keys, and
  Firestore database so changes can be validated before they reach real users.

**Architecture severity:**

- 🔴 High: no folder structure with > 10 pages, or no consistent naming in any category
- 🟡 Medium: mixed conventions in 2+ categories, duplicate components, missing analytics or
  crashlytics, non-Firebase auth without token bridge
- 🟢 Low: mostly consistent with isolated deviations

---

## Phase 3 — Report Generation

Save to `/mnt/user-data/outputs/ff-audit-report.md`. Use exactly this structure:

```markdown
# FlutterFlow Audit Report — [Project Name or ID]
**Date:** [today's date]
**Project ID:** [id]
**Audited by:** FlutterFlow Audit Skill (read-only, direct API)

---

## Executive Summary
2–4 sentences. Lead with the most critical finding.

**Overall Risk Level:** 🔴 High / 🟡 Medium / 🟢 Low

| Domain | Issues Found | Risk Level |
|--------|-------------|------------|
| Security | N | 🔴/🟡/🟢 |
| Architecture | N | 🔴/🟡/🟢 |

---

## 🔐 Security Audit

### Critical Issues
[File path + field + partial value (first 6 chars + ...)]

### Warnings
[Test keys, suspicious state variable defaults]

### Recommendations
[Concrete, prioritised action items]

---

## 🏗️ Architecture Audit

### Folder Structure
[Findings; list empty folders by name; flag naming violations]

### Naming Conventions

#### Pages & Components
[Detected style + deviation table: | Name | Expected | Found |]

#### Folder Names
[Detected style + deviation table; list empty folders separately]

#### State Variables
[Detected style + deviation table]

#### Data Models (Structs & Enums)
[Detected style + deviation table]

#### Firestore Collections
[Detected style + deviation table]

### Component Hygiene
[Duplicated components, 0-usage, oversized]

### Firestore Security Rules

#### 🚨 Critical — Write access open to `everyone`
[Table: | Collection | Operation | Rule | Fix |]
If none: "No collections expose write access to everyone."

#### ⚠️ Warning — Write access open to `authenticatedUsers`
[Table: | Collection | Operation | Recommendation |]
For each: restrict to `taggedUsers` AND move write behind a Cloud Function / API endpoint.
If none: "All write rules are appropriately scoped."

### Authentication
[Provider detected. If not Firebase: Custom Token bridge recommendation. If Firebase: ✅]

### Analytics & Crash Reporting
[Firebase Analytics: ✅ / ⚠️ not found]
[Firebase Crashlytics: ✅ / ⚠️ not found]

### Environment Variables
[✅ configured with DEV + PROD / ⚠️ not found — recommend enabling]

**If only PROD exists:**
> ⚠️ **No DEV environment configured.** Every untested change, key rotation, or schema update
> goes straight to production. Add a `DEV` environment with its own Firebase project, API keys,
> and Firestore instance. Use FlutterFlow's Environment Values to gate all credentials behind
> the active environment so switching from dev to prod is a single toggle.

### Recommendations
[Concrete, prioritised action items]

---

## Appendix: Scripts & Artefacts
- `fetch_project.py`  → fetched ZIP, saved as `<projectId>.zip`, extracted to `<projectId>_yaml/`
- `extract_names.py`  → produced `names.json`
- grep commands       → checked analytics, crashlytics, auth, env vars, Firestore security rules
```

---

## Report Writing Rules

- Name the exact file, page, endpoint, component, or variable for every issue.
- Every issue must have a corresponding recommendation.
- If no issues: write "No issues found." — never invent warnings.
- Secrets: first 6 chars + `...` only. Never reproduce a full credential.
- Naming deviations: always a table `| Name | Expected | Found |`
- Auth recommendation must explain the Custom Token bridge pattern explicitly.
- Empty folders must be listed by name.
- The API key must never appear in report output, logs, or script stdout.
