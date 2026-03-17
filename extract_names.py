"""
extract_names.py
----------------
Walks a FlutterFlow project YAML directory and extracts ONLY names and types
into a compact JSON file (names.json).

Actual project layout
~~~~~~~~~~~~~~~~~~~~~
  app-details.yaml          – app name, initial page key, routing settings
  app-assets.yaml           – asset paths
  app-state.yaml            – app state variable definitions
  folders.yaml              – rootFolders list + widgetClassKeyToFolderKey map
  api-endpoint/
    id-<key>.yaml           – { identifier: { name, key }, url, ... }
  data-structs/
    id-<key>.yaml           – { identifier: { name, key }, fields: { … } }
  page/
    id-<key>.yaml           – page definition: { name, node: { key } }
    id-<key>/
      page-widget-tree-outline.yaml          – widget tree
      page-widget-tree-outline/node/
        id-<WidgetType_key>.yaml             – widget node
        id-<WidgetType_key>/trigger_actions/ – actions
  component/                – same structure as page/ (may be absent)
    id-<key>.yaml
    ...
  collections/
    id-<key>.yaml           – { identifier: { name, key }, fields: { … } }

Usage:
    python extract_names.py <yaml_dir> > names.json

Output shape:
{
  "app_name":              string,
  "initial_page_key":      string,
  "pages":                 [string, ...],
  "components":            [string, ...],
  "folders":               [string, ...],
  "empty_folders":         [string, ...],
  "firestore_collections": [string, ...],
  "app_state_vars":        [{ "name": string, "default": string }, ...],
  "api_endpoints":         [string, ...],
  "data_structs":          [string, ...]
}
"""

import json
import os
import re
import sys

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed.", file=sys.stderr)
    print("  Fix: pip install pyyaml --break-system-packages", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_yaml(path):
    """Return parsed YAML or None on error."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def top_level_id_yamls(directory):
    """
    Yield (path, key) for every id-<key>.yaml directly inside *directory*.
    Does NOT recurse into sub-directories.
    """
    if not os.path.isdir(directory):
        return
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith((".yaml", ".yml")):
            continue
        m = re.match(r"^id-(.+)\.ya?ml$", fname)
        if not m:
            continue
        yield os.path.join(directory, fname), m.group(1)


def extract_items(base, kind, key_to_folder):
    """
    Walk base/<kind>/ and return list of dicts for each id-*.yaml found at
    the first level.  kind is 'page' or 'component'.
    """
    items = []
    directory = os.path.join(base, kind)
    for path, key in top_level_id_yamls(directory):
        data = load_yaml(path)
        if data is None:
            continue
        name = data.get("name")
        if not name:
            # fall back to node key if name is missing
            node = data.get("node", {})
            name = node.get("key") if isinstance(node, dict) else None
        # The node key stored in the file (may differ from the id in the filename)
        node_key = None
        if isinstance(data.get("node"), dict):
            node_key = data["node"].get("key")
        folder_key = key_to_folder.get(node_key or key, "")
        items.append({
            "name":   name,
            "key":    node_key or key,
            "folder": folder_key,
            "file":   path,
        })
    return items


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        print("Usage: python extract_names.py <yaml_dir>", file=sys.stderr)
        sys.exit(1)

    base = sys.argv[1]
    if not os.path.isdir(base):
        print(f"ERROR: Directory not found: {base}", file=sys.stderr)
        sys.exit(1)

    # ── App details ────────────────────────────────────────────────────────
    app_name = ""
    initial_page_key = ""
    details_path = os.path.join(base, "app-details.yaml")
    details = load_yaml(details_path)
    if details:
        app_name = details.get("name", "")
        ipk = details.get("initialPageKeyRef")
        if isinstance(ipk, dict):
            initial_page_key = ipk.get("key", "")

    # ── Folders ────────────────────────────────────────────────────────────
    folders_path = os.path.join(base, "folders.yaml")
    folders_data = load_yaml(folders_path) or {}

    # rootFolders is a nested tree of { key, name, children? }
    def collect_folders(nodes):
        result = []
        for fld in (nodes or []):
            if not isinstance(fld, dict):
                continue
            result.append({"key": fld.get("key", ""), "name": fld.get("name", "")})
            result.extend(collect_folders(fld.get("children", [])))
        return result

    folders = collect_folders(folders_data.get("rootFolders", []))

    # widgetClassKeyToFolderKey: { <widget_key>: <folder_key> }
    raw_map = folders_data.get("widgetClassKeyToFolderKey", {})
    key_to_folder = raw_map if isinstance(raw_map, dict) else {}

    # Build reverse map: folder_key → folder_name (for empty-folder detection)
    folder_key_to_name = {f["key"]: f["name"] for f in folders}

    # ── Pages & Components ─────────────────────────────────────────────────
    pages      = extract_items(base, "page",      key_to_folder)
    components = extract_items(base, "component", key_to_folder)

    # ── Empty folders ──────────────────────────────────────────────────────
    # A folder is "empty" if no page or component is assigned to it.
    used_folder_keys = {item["folder"] for item in pages + components if item["folder"]}
    empty_folders = [
        f for f in folders
        if f["key"] and f["key"] not in used_folder_keys
    ]

    # ── Firestore collections ──────────────────────────────────────────────
    firestore_collections = []
    collections_dir = os.path.join(base, "collections")
    for path, _key in top_level_id_yamls(collections_dir):
        data = load_yaml(path)
        if data is None:
            continue
        ident = data.get("identifier", {})
        if not isinstance(ident, dict):
            continue
        name = ident.get("name")
        col_key = ident.get("key", "")
        if not name:
            continue
        raw_fields = data.get("fields", {})
        if isinstance(raw_fields, dict):
            fields = [
                {
                    "name":     fname,
                    "dataType": (fval.get("dataType", {}) if isinstance(fval, dict) else {}),
                }
                for fname, fval in raw_fields.items()
            ]
        else:
            fields = []
        firestore_collections.append({
            "name":   name,
            "key":    col_key,
            "fields": fields,
            "file":   path,
        })

    # ── App state variables ────────────────────────────────────────────────
    app_state_vars = []
    state_path = os.path.join(base, "app-state.yaml")
    state_data = load_yaml(state_path) or {}
    for field in state_data.get("fields", []):
        if not isinstance(field, dict):
            continue
        param = field.get("parameter", {})
        if not isinstance(param, dict):
            continue
        ident = param.get("identifier", {})
        if not isinstance(ident, dict):
            continue
        name = ident.get("name")
        if not name:
            continue
        raw_default = field.get("serializedDefaultValue", [])
        default = raw_default[0] if isinstance(raw_default, list) and raw_default else ""
        app_state_vars.append({"name": name, "default": default})

    # ── API endpoints ──────────────────────────────────────────────────────
    api_endpoints = []
    for path, _key in top_level_id_yamls(os.path.join(base, "api-endpoint")):
        data = load_yaml(path)
        if data is None:
            continue
        ident = data.get("identifier", {})
        name = ident.get("name") if isinstance(ident, dict) else None
        if name:
            api_endpoints.append(name)

    # ── Data structs ───────────────────────────────────────────────────────
    data_structs = []
    for path, _key in top_level_id_yamls(os.path.join(base, "data-structs")):
        data = load_yaml(path)
        if data is None:
            continue
        ident = data.get("identifier", {})
        name = ident.get("name") if isinstance(ident, dict) else None
        if name:
            data_structs.append(name)

    # ── Assemble result ────────────────────────────────────────────────────
    result = {
        "app_name":              app_name,
        "initial_page_key":      initial_page_key,
        "pages":                 [p["name"] for p in pages],
        "components":            [c["name"] for c in components],
        "folders":               [f["name"] for f in folders],
        "empty_folders":         [f["name"] for f in empty_folders],
        "firestore_collections": [c["name"] for c in firestore_collections],
        "app_state_vars":        app_state_vars,
        "api_endpoints":         api_endpoints,
        "data_structs":          data_structs,
    }

    # ── Summary to stderr ──────────────────────────────────────────────────
    print(
        f"[extract_names] app={app_name!r}  "
        f"pages={len(pages)}  "
        f"components={len(components)}  "
        f"folders={len(folders)}  "
        f"empty_folders={len(empty_folders)}  "
        f"firestore={len(firestore_collections)}  "
        f"state_vars={len(app_state_vars)}  "
        f"api_endpoints={len(api_endpoints)}  "
        f"data_structs={len(data_structs)}",
        file=sys.stderr,
    )

    # print(json.dumps(result, indent=2))
    # Save the JSON result to a file
    with open("extracted_names.json", "w", encoding="utf-8") as f:
        json.dump(result, f)

if __name__ == "__main__":
    main()
