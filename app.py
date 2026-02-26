import base64
import posixpath
import datetime
import random
import string
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
BRANCH = get_secret("branch", "main")  # default branch shown in UI
BASE_BRANCH = get_secret("base_branch", BRANCH)  # protected branch you merge INTO
PR_TITLE_PREFIX = get_secret("pr_title_prefix", "Docs update")

DOCS_ROOT = get_secret("docs_root", "docs")  # usually "docs"
ASSETS_DIR = get_secret("assets_dir", "docs/assets")  # upload target
COMMITTER_NAME = get_secret("committer_name", "Docs Editor Bot")
COMMITTER_EMAIL = get_secret("committer_email", "docs-editor@example.com")

if not GITHUB_TOKEN or not REPO:
    st.error(
        "Missing secrets. Please set [github].token and [github].repo in Streamlit secrets."
    )
    st.stop()

API = "https://api.github.com"
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",  # IMPORTANT for modern PATs
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
        try:
            msg = r.json()
        except Exception:
            msg = r.text
        raise RuntimeError(f"GitHub API error {r.status_code}: {msg}")
    return r


def normalize_docs_path(p: str) -> str:
    # Keep posix paths for GitHub.
    p = p.replace("\\", "/").strip("/")
    return p


def is_under(parent: str, child: str) -> bool:
    parent = parent.rstrip("/") + "/"
    return child.startswith(parent)


def list_tree_recursive(branch: str) -> List[dict]:
    """
    Uses Git Trees API to list the repo tree recursively for a given branch.
    """
    ref_url = f"{API}/repos/{REPO}/git/ref/heads/{branch}"
    ref = gh_request("GET", ref_url).json()
    commit_sha = ref["object"]["sha"]

    commit_url = f"{API}/repos/{REPO}/git/commits/{commit_sha}"
    commit = gh_request("GET", commit_url).json()
    tree_sha = commit["tree"]["sha"]

    tree_url = f"{API}/repos/{REPO}/git/trees/{tree_sha}?recursive=1"
    tree = gh_request("GET", tree_url).json()
    return tree.get("tree", [])


def read_file(path: str, branch: str) -> GHFile:
    url = f"{API}/repos/{REPO}/contents/{path}"
    j = gh_request("GET", url, params={"ref": branch}).json()
    if j.get("type") != "file":
        raise RuntimeError(f"Path is not a file: {path}")
    raw = base64.b64decode(j["content"]).decode("utf-8", errors="replace")
    return GHFile(path=path, sha=j["sha"], content=raw)


def upsert_file(
    path: str,
    content_text: str,
    message: str,
    branch: str,
    sha: Optional[str] = None,
):
    url = f"{API}/repos/{REPO}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_text.encode("utf-8")).decode("utf-8"),
        "branch": branch,
        "committer": {"name": COMMITTER_NAME, "email": COMMITTER_EMAIL},
    }
    if sha:
        payload["sha"] = sha
    gh_request("PUT", url, json=payload).json()


def delete_file(path: str, sha: str, message: str, branch: str):
    url = f"{API}/repos/{REPO}/contents/{path}"
    payload = {
        "message": message,
        "sha": sha,
        "branch": branch,
        "committer": {"name": COMMITTER_NAME, "email": COMMITTER_EMAIL},
    }
    gh_request("DELETE", url, json=payload).json()


# -----------------------------
# PR workflow helpers
# -----------------------------
def create_branch_from_base(base_branch: str) -> str:
    # get base branch SHA
    ref = gh_request("GET", f"{API}/repos/{REPO}/git/ref/heads/{base_branch}").json()
    base_sha = ref["object"]["sha"]

    suffix = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    new_branch = f"docs-edit-{suffix}-{rand}"

    gh_request(
        "POST",
        f"{API}/repos/{REPO}/git/refs",
        json={"ref": f"refs/heads/{new_branch}", "sha": base_sha},
    )
    return new_branch


def create_pull_request(head_branch: str, base_branch: str, title: str, body: str = "") -> str:
    pr = gh_request(
        "POST",
        f"{API}/repos/{REPO}/pulls",
        json={"title": title, "head": head_branch, "base": base_branch, "body": body},
    ).json()
    return pr.get("html_url", "")


def open_pr_for_change(
    *,
    title: str,
    body: str,
    commit_message: str,
    file_ops_fn,
) -> str:
    """
    Generic helper:
    - create a new branch from BASE_BRANCH
    - run file_ops_fn(branch_name) which commits one or more changes to that branch
    - open a PR back to BASE_BRANCH
    Returns PR URL.
    """
    new_branch = create_branch_from_base(BASE_BRANCH)
    file_ops_fn(new_branch)
    pr_url = create_pull_request(
        head_branch=new_branch,
        base_branch=BASE_BRANCH,
        title=title,
        body=body,
    )
    return pr_url


# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title="Hypatos Docs Editor", layout="wide")
st.title("Hypatos Documentation Editor")
st.caption(f"Repo: `{REPO}` · Base branch: `{BASE_BRANCH}`")


@st.cache_data(ttl=30)
def cached_tree(branch: str):
    return list_tree_recursive(branch)


# ------------------------------------------------
# 🔎 GitHub connection test
# ------------------------------------------------
with st.expander("🔎 GitHub connection test", expanded=False):
    try:
        me = gh_request("GET", f"{API}/user").json()
        st.success(f"Authenticated as: {me.get('login')}")

        repo_info = gh_request("GET", f"{API}/repos/{REPO}").json()
        st.success(f"Repo access OK: {repo_info.get('full_name')}")

        ref = gh_request("GET", f"{API}/repos/{REPO}/git/ref/heads/{BASE_BRANCH}").json()
        st.success(f"Base branch OK: {BASE_BRANCH}")

    except Exception as e:
        st.error(str(e))
        st.stop()
# ------------------------------------------------


tree = cached_tree(BASE_BRANCH)

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
        st.warning("This will create a PR that deletes the file.")

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
        edited = st.text_area(
            "",
            value=initial_text,
            height=650,
            label_visibility="collapsed",
        )
    with right:
        st.subheader("Preview")
        st.markdown(edited)
    return edited, ""


# -----------------------------
# Actions
# -----------------------------
if action == "Edit existing page":
    if not md_files:
        st.info("No Markdown files found under docs root.")
        st.stop()

    try:
        f = read_file(selected, BASE_BRANCH)
    except Exception as e:
        st.error(str(e))
        st.stop()

    st.write(f"Editing: `{f.path}`")

    new_text, _ = two_col_editor(f.content)

    col_a, col_b, col_c = st.columns([2, 2, 6])
    with col_a:
        commit_msg = st.text_input("Commit message", value=f"Update {f.path}")
    with col_b:
        do_commit = st.button("✅ Create PR", type="primary")

    if do_commit:
        try:
            def ops(branch_name: str):
                # Read file SHA from base branch, then update on new branch using that SHA
                base_file = read_file(f.path, BASE_BRANCH)
                upsert_file(f.path, new_text, commit_msg, branch=branch_name, sha=base_file.sha)

            pr_url = open_pr_for_change(
                title=f"{PR_TITLE_PREFIX}: Update {f.path}",
                body="Created via Streamlit Docs Editor (protected branch → PR).",
                commit_message=commit_msg,
                file_ops_fn=ops,
            )
            st.success("Pull request created.")
            if pr_url:
                st.markdown(f"[Open PR]({pr_url})")
            st.cache_data.clear()
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
    create_btn = st.button("➕ Create PR", type="primary")

    if create_btn:
        rel = normalize_docs_path(relative_path)
        if not rel.endswith(".md"):
            st.error("Path must end with .md")
            st.stop()

        full_path = normalize_docs_path(posixpath.join(DOCS_ROOT, rel))
        try:
            # Ensure it doesn't already exist on base
            try:
                _ = read_file(full_path, BASE_BRANCH)
                st.error("A file already exists at that path.")
                st.stop()
            except Exception:
                pass

            def ops(branch_name: str):
                upsert_file(full_path, body, commit_msg, branch=branch_name, sha=None)

            pr_url = open_pr_for_change(
                title=f"{PR_TITLE_PREFIX}: Add {full_path}",
                body="Created via Streamlit Docs Editor (protected branch → PR).",
                commit_message=commit_msg,
                file_ops_fn=ops,
            )
            st.success("Pull request created.")
            if pr_url:
                st.markdown(f"[Open PR]({pr_url})")
            st.info("Remember to add it to mkdocs.yml under nav:.")
            st.cache_data.clear()
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
    upload_btn = st.button("⬆️ Create PR", type="primary", disabled=uploaded is None)

    if upload_btn and uploaded is not None:
        try:
            target = normalize_docs_path(posixpath.join(ASSETS_DIR, target_name))
            data = uploaded.getvalue()
            content_b64 = base64.b64encode(data).decode("utf-8")

            def ops(branch_name: str):
                # check if exists on base to get sha (for overwrite)
                sha = None
                try:
                    url = f"{API}/repos/{REPO}/contents/{target}"
                    j = gh_request("GET", url, params={"ref": BASE_BRANCH}).json()
                    if j.get("type") == "file":
                        sha = j.get("sha")
                except Exception:
                    sha = None

                url = f"{API}/repos/{REPO}/contents/{target}"
                payload = {
                    "message": commit_msg,
                    "content": content_b64,
                    "branch": branch_name,
                    "committer": {"name": COMMITTER_NAME, "email": COMMITTER_EMAIL},
                }
                if sha:
                    payload["sha"] = sha
                gh_request("PUT", url, json=payload).json()

            pr_url = open_pr_for_change(
                title=f"{PR_TITLE_PREFIX}: Upload {target}",
                body="Created via Streamlit Docs Editor (protected branch → PR).",
                commit_message=commit_msg,
                file_ops_fn=ops,
            )
            st.success("Pull request created.")
            if pr_url:
                st.markdown(f"[Open PR]({pr_url})")

            st.caption("Use it in Markdown like:")
            st.code(f"![Alt text]({posixpath.relpath(target, DOCS_ROOT)})")
            st.cache_data.clear()
        except Exception as e:
            st.error(str(e))

elif action == "Delete page":
    if not md_files:
        st.info("No Markdown files found.")
        st.stop()

    if delete_target:
        st.write(f"Deleting: `{delete_target}`")
        confirm = st.checkbox("I understand this will create a PR that deletes the file.")
        commit_msg = st.text_input("Commit message", value=f"Delete {delete_target}")

        if st.button("🗑️ Create PR", type="primary", disabled=not confirm):
            try:
                base_file = read_file(delete_target, BASE_BRANCH)

                def ops(branch_name: str):
                    delete_file(delete_target, base_file.sha, commit_msg, branch=branch_name)

                pr_url = open_pr_for_change(
                    title=f"{PR_TITLE_PREFIX}: Delete {delete_target}",
                    body="Created via Streamlit Docs Editor (protected branch → PR).",
                    commit_message=commit_msg,
                    file_ops_fn=ops,
                )
                st.success("Pull request created.")
                if pr_url:
                    st.markdown(f"[Open PR]({pr_url})")
                st.cache_data.clear()
            except Exception as e:
                st.error(str(e))
