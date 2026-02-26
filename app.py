import base64
import os
import posixpath
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests
import streamlit as st


# -----------------------------
# Config from Streamlit secrets
# -----------------------------
def get_secret(key: str, default=None):
    if "github" in st.secrets and key in st.secrets["github"]:
        return st.secrets["github"][key]
    return default


GITHUB_TOKEN = get_secret("token")
REPO = get_secret("repo")  # "org-or-user/repo"
BRANCH = get_secret("branch", "main")
DOCS_ROOT = get_secret("docs_root", "docs")  # usually "docs"
ASSETS_DIR = get_secret("assets_dir", "docs/assets")  # upload target
COMMITTER_NAME = get_secret("committer_name", "Docs Editor Bot")
COMMITTER_EMAIL = get_secret("committer_email", "docs-editor@example.com")


if not GITHUB_TOKEN or not REPO:
    st.error(
        "Missing secrets. Please set [github].token and [github].repo in Streamlit secrets."
    )
    st.stop()

# ------------------------------------------------
# 🔎 ADD CONNECTION TEST RIGHT HERE
# ------------------------------------------------

with st.expander("🔎 GitHub connection test", expanded=True):
    try:
        me = gh_request("GET", f"{API}/user").json()
        st.success(f"Authenticated as: {me.get('login')}")

        repo_info = gh_request("GET", f"{API}/repos/{REPO}").json()
        st.success(f"Repo access OK: {repo_info.get('full_name')}")

        ref = gh_request("GET", f"{API}/repos/{REPO}/git/ref/heads/{BRANCH}").json()
        st.success(f"Branch OK: {BRANCH}")
    except Exception as e:
        st.error(str(e))
        st.stop()

# ------------------------------------------------

API = "https://api.github.com"
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


# -----------------------------
# Small GitHub client helpers
# -----------------------------
@dataclass
class GHFile:
    path: str
    sha: str
    content: str  # decoded text


def gh_request(method: str, url: str, **kwargs):
    r = requests.request(method, url, headers=HEADERS, timeout=30, **kwargs)
    if r.status_code >= 400:
        msg = r.text
        try:
            msg = r.json()
        except Exception:
            pass
        raise RuntimeError(f"GitHub API error {r.status_code}: {msg}")
    return r


def list_tree_recursive() -> List[dict]:
    """
    Uses Git Trees API to list the repo tree recursively.
    """
    ref_url = f"{API}/repos/{REPO}/git/ref/heads/{BRANCH}"
    ref = gh_request("GET", ref_url).json()
    commit_sha = ref["object"]["sha"]

    commit_url = f"{API}/repos/{REPO}/git/commits/{commit_sha}"
    commit = gh_request("GET", commit_url).json()
    tree_sha = commit["tree"]["sha"]

    tree_url = f"{API}/repos/{REPO}/git/trees/{tree_sha}?recursive=1"
    tree = gh_request("GET", tree_url).json()
    return tree.get("tree", [])


def read_file(path: str) -> GHFile:
    url = f"{API}/repos/{REPO}/contents/{path}"
    j = gh_request("GET", url, params={"ref": BRANCH}).json()
    if j.get("type") != "file":
        raise RuntimeError(f"Path is not a file: {path}")
    raw = base64.b64decode(j["content"]).decode("utf-8", errors="replace")
    return GHFile(path=path, sha=j["sha"], content=raw)


def upsert_file(path: str, content_text: str, message: str, sha: Optional[str] = None):
    url = f"{API}/repos/{REPO}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_text.encode("utf-8")).decode("utf-8"),
        "branch": BRANCH,
        "committer": {"name": COMMITTER_NAME, "email": COMMITTER_EMAIL},
    }
    if sha:
        payload["sha"] = sha
    gh_request("PUT", url, json=payload).json()


def delete_file(path: str, sha: str, message: str):
    url = f"{API}/repos/{REPO}/contents/{path}"
    payload = {
        "message": message,
        "sha": sha,
        "branch": BRANCH,
        "committer": {"name": COMMITTER_NAME, "email": COMMITTER_EMAIL},
    }
    gh_request("DELETE", url, json=payload).json()


def normalize_docs_path(p: str) -> str:
    # Keep posix paths for GitHub.
    p = p.replace("\\", "/").strip("/")
    return p


def is_under(parent: str, child: str) -> bool:
    parent = parent.rstrip("/") + "/"
    return child.startswith(parent)


# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title="Hypatos Docs Editor", layout="wide")
st.title("Hypatos Documentation Editor")
st.caption(f"Repo: `{REPO}` · Branch: `{BRANCH}`")


@st.cache_data(ttl=30)
def cached_tree():
    return list_tree_recursive()


tree = cached_tree()

# Collect markdown pages under docs root
docs_root = normalize_docs_path(DOCS_ROOT)
md_files = sorted(
    [
        item["path"]
        for item in tree
        if item.get("type") == "blob"
        and item.get("path", "").endswith(".md")
        and is_under(docs_root, item["path"])
    ]
)

# Assets directory listing (optional)
assets_root = normalize_docs_path(ASSETS_DIR)
asset_files = sorted(
    [
        item["path"]
        for item in tree
        if item.get("type") == "blob"
        and is_under(assets_root, item["path"])
    ]
)

with st.sidebar:
    st.header("Pages")

    action = st.radio(
        "What do you want to do?",
        ["Edit existing page", "Create new page", "Upload asset", "Delete page"],
    )

    if action == "Edit existing page":
        selected = st.selectbox("Select a page", md_files, index=0 if md_files else None)

    if action == "Delete page":
        delete_target = st.selectbox(
            "Select a page to delete", md_files, index=0 if md_files else None
        )
        st.warning("Deletion commits immediately.")

    if action == "Upload asset":
        st.write(f"Uploads to: `{ASSETS_DIR}`")
        st.write("Existing assets:")
        st.code("\n".join(asset_files[:30]) + ("\n..." if len(asset_files) > 30 else ""))

    st.divider()
    st.header("Navigation (mkdocs.yml)")
    st.write("This app does **not** auto-edit `mkdocs.yml` yet.")
    st.write("After creating a page, add it under `nav:` in `mkdocs.yml`.")


def two_col_editor(initial_text: str) -> Tuple[str, str]:
    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Markdown")
        edited = st.text_area("",
                              value=initial_text,
                              height=650,
                              label_visibility="collapsed")
    with right:
        st.subheader("Preview")
        st.markdown(edited)
    return edited, ""


if action == "Edit existing page":
    if not md_files:
        st.info("No Markdown files found under docs root.")
        st.stop()

    try:
        f = read_file(selected)
    except Exception as e:
        st.error(str(e))
        st.stop()

    st.write(f"Editing: `{f.path}`")

    new_text, _ = two_col_editor(f.content)

    col_a, col_b, col_c = st.columns([2, 2, 6])
    with col_a:
        commit_msg = st.text_input("Commit message", value=f"Update {f.path}")
    with col_b:
        do_commit = st.button("✅ Commit changes", type="primary")

    if do_commit:
        try:
            upsert_file(f.path, new_text, commit_msg, sha=f.sha)
            st.success("Committed to GitHub.")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(str(e))

elif action == "Create new page":
    st.subheader("Create new page")

    st.write(f"Docs root: `{DOCS_ROOT}`")

    col1, col2 = st.columns([3, 2])
    with col1:
        relative_path = st.text_input(
            "New page path (relative to docs/)",
            value="user-guide/my-new-page.md",
            help="Example: user-guide/getting-started.md",
        )
    with col2:
        title = st.text_input("Page title", value="My New Page")

    default_body = f"# {title}\n\nWrite your content here.\n"
    body = st.text_area("Content", value=default_body, height=500)

    commit_msg = st.text_input("Commit message", value="Add new documentation page")
    create_btn = st.button("➕ Create & Commit", type="primary")

    if create_btn:
        rel = normalize_docs_path(relative_path)
        if not rel.endswith(".md"):
            st.error("Path must end with .md")
            st.stop()

        full_path = normalize_docs_path(posixpath.join(DOCS_ROOT, rel))
        try:
            # Ensure it doesn't already exist
            existing = None
            try:
                existing = read_file(full_path)
            except Exception:
                pass
            if existing:
                st.error("A file already exists at that path.")
                st.stop()

            upsert_file(full_path, body, commit_msg, sha=None)
            st.success(f"Created `{full_path}` and committed to GitHub.")
            st.info("Remember to add it to mkdocs.yml under nav:.")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(str(e))

elif action == "Upload asset":
    st.subheader("Upload asset to docs/assets")

    uploaded = st.file_uploader(
        "Choose a file (png/jpg/pdf/mp4/etc)",
        accept_multiple_files=False,
    )

    target_name = st.text_input(
        "Target filename",
        value=uploaded.name if uploaded else "my-asset.png",
        help="Saved under docs/assets/",
    )

    commit_msg = st.text_input("Commit message", value="Add documentation asset")
    upload_btn = st.button("⬆️ Upload & Commit", type="primary", disabled=uploaded is None)

    if upload_btn and uploaded is not None:
        try:
            target = normalize_docs_path(posixpath.join(ASSETS_DIR, target_name))
            data = uploaded.getvalue()
            content_b64 = base64.b64encode(data).decode("utf-8")

            # Check if file exists to get sha
            sha = None
            try:
                url = f"{API}/repos/{REPO}/contents/{target}"
                j = gh_request("GET", url, params={"ref": BRANCH}).json()
                if j.get("type") == "file":
                    sha = j.get("sha")
            except Exception:
                sha = None

            url = f"{API}/repos/{REPO}/contents/{target}"
            payload = {
                "message": commit_msg,
                "content": content_b64,
                "branch": BRANCH,
                "committer": {"name": COMMITTER_NAME, "email": COMMITTER_EMAIL},
            }
            if sha:
                payload["sha"] = sha

            gh_request("PUT", url, json=payload).json()
            st.success(f"Uploaded and committed `{target}`.")
            st.caption("Use it in Markdown like:")
            st.code(f"![Alt text]({posixpath.relpath(target, DOCS_ROOT)})")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(str(e))

elif action == "Delete page":
    if not md_files:
        st.info("No Markdown files found.")
        st.stop()

    if delete_target:
        st.write(f"Deleting: `{delete_target}`")
        confirm = st.checkbox("I understand this will commit a deletion to GitHub.")
        commit_msg = st.text_input("Commit message", value=f"Delete {delete_target}")
        if st.button("🗑️ Delete & Commit", type="primary", disabled=not confirm):
            try:
                f = read_file(delete_target)
                delete_file(delete_target, f.sha, commit_msg)
                st.success("Deleted and committed.")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(str(e))
