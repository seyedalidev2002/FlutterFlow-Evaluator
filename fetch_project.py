"""
fetch_project.py
----------------
Fetches a FlutterFlow project's YAML bundle via the official API.

Extracts $.value.project_yaml_bytes (base64-encoded ZIP), saves it as
<projectId>.zip, then unzips it to ./<projectId>_yaml/.

Usage:
    python fetch_project.py <api_key> <project_id>

Arguments are positional to avoid the key ever appearing in a --flag that
might be captured by shell history or process listings.
"""

import base64
import os
import subprocess
import sys
import zipfile

try:
    import requests
except ImportError:
    print("[fetch_project] ERROR: 'requests' not installed.")
    print("  Fix: pip install requests --break-system-packages")
    sys.exit(1)


def main():
    if len(sys.argv) != 3:
        print("Usage: python fetch_project.py <api_key> <project_id>")
        sys.exit(1)

    api_key    = sys.argv[1]
    project_id = sys.argv[2]

    url     = f"https://api.flutterflow.io/v2/projectYamls?projectId={project_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    print(f"[fetch_project] Project : {project_id}")
    print(f"[fetch_project] Endpoint: {url}")
    print("[fetch_project] Calling FlutterFlow API…")

    try:
        response = requests.get(url, headers=headers, timeout=60)
    except requests.exceptions.RequestException as e:
        print(f"[fetch_project] Network error: {e}")
        sys.exit(1)

    if response.status_code == 401:
        print("[fetch_project] ERROR 401: Invalid or expired API key.")
        sys.exit(1)
    if response.status_code == 404:
        print(f"[fetch_project] ERROR 404: Project '{project_id}' not found. Check the project ID.")
        sys.exit(1)
    if response.status_code != 200:
        print(f"[fetch_project] ERROR {response.status_code}: {response.text[:500]}")
        sys.exit(1)

    try:
        data = response.json()
    except Exception:
        print("[fetch_project] ERROR: Response is not valid JSON.")
        print(f"  Raw (first 200 chars): {response.text[:200]}")
        sys.exit(1)

    # Navigate $.value.project_yaml_bytes
    try:
        b64_bytes = data["value"]["project_yaml_bytes"]
    except KeyError:
        print("[fetch_project] ERROR: Unexpected response shape.")
        print(f"  Top-level keys  : {list(data.keys())}")
        print(f"  'value' keys    : {list(data.get('value', {}).keys())}")
        sys.exit(1)

    # Decode — normalize URL-safe chars and add missing padding
    try:
        b64_str = b64_bytes.replace("-", "+").replace("_", "/")
        b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
        raw_bytes = base64.b64decode(b64_str)
    except Exception as e:
        print(f"[fetch_project] ERROR: Failed to base64-decode project_yaml_bytes: {e}")
        sys.exit(1)

    # Save ZIP
    zip_path = f"{project_id}.zip"
    with open(zip_path, "wb") as f:
        f.write(raw_bytes)
    print(f"[fetch_project] Saved ZIP → {zip_path} ({len(raw_bytes):,} bytes)")

    # Unzip
    extract_dir = f"{project_id}_yaml"
    os.makedirs(extract_dir, exist_ok=True)
    print(f"[fetch_project] First 4 bytes: {raw_bytes[:4]!r}")  # should be b'PK\x03\x04' for ZIP

    # Try stdlib zipfile first, fall back to system unzip
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(extract_dir)
    except zipfile.BadZipFile as e:
        print(f"[fetch_project] zipfile failed ({e}), trying system unzip…")
        result = subprocess.run(
            ["unzip", "-o", zip_path, "-d", extract_dir],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"[fetch_project] ERROR: system unzip also failed.")
            print(f"  stdout: {result.stdout[:300]}")
            print(f"  stderr: {result.stderr[:300]}")
            sys.exit(1)

    file_count = sum(len(files) for _, _, files in os.walk(extract_dir))
    print(f"[fetch_project] Extracted {file_count} files → {extract_dir}/")
    print(f"[fetch_project] ✅ Done. Ready to audit: {extract_dir}/")


if __name__ == "__main__":
    main()
