"""Mirror published GitHub Releases and their uploaded assets to Gitee."""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import requests
from requests_toolbelt import MultipartEncoder


GITHUB_API = "https://api.github.com"
GITEE_API = "https://gitee.com/api/v5"
TIMEOUT = 60
UPLOAD_TIMEOUT = 600
UPLOAD_ATTEMPTS = 3


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


github_repository = required_env("GITHUB_REPOSITORY")
github_token = required_env("GITHUB_TOKEN")
gitee_token = required_env("GITEE_TOKEN")
gitee_owner = required_env("GITEE_OWNER")
gitee_repo = required_env("GITEE_REPO")
sync_release_tag = os.environ.get("SYNC_RELEASE_TAG", "").strip()

github = requests.Session()
github.headers.update(
    {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "siming-ai-gitee-mirror",
    }
)

gitee = requests.Session()
gitee.headers.update({"User-Agent": "siming-ai-gitee-mirror"})


def checked(response: requests.Response) -> requests.Response:
    if not response.ok:
        detail = response.text[:1000]
        raise RuntimeError(
            f"API request failed: {response.request.method} {response.url} "
            f"returned {response.status_code}: {detail}"
        )
    return response


def gitee_request(method: str, path: str, **kwargs) -> requests.Response:
    params = dict(kwargs.pop("params", {}))
    params["access_token"] = gitee_token
    timeout = kwargs.pop("timeout", TIMEOUT)
    response = gitee.request(
        method,
        f"{GITEE_API}{path}",
        params=params,
        timeout=timeout,
        **kwargs,
    )
    return checked(response)


def github_releases() -> list[dict]:
    releases: list[dict] = []
    page = 1
    while True:
        response = checked(
            github.get(
                f"{GITHUB_API}/repos/{github_repository}/releases",
                params={"per_page": 100, "page": page},
                timeout=TIMEOUT,
            )
        )
        batch = response.json()
        releases.extend(batch)
        if len(batch) < 100:
            return releases
        page += 1


def find_gitee_release(tag_name: str) -> dict | None:
    response = gitee.get(
        f"{GITEE_API}/repos/{gitee_owner}/{gitee_repo}/releases/tags/{quote(tag_name, safe='')}",
        params={"access_token": gitee_token},
        timeout=TIMEOUT,
    )
    if response.status_code == 404:
        return None
    return checked(response).json()


def upsert_gitee_release(release: dict) -> dict:
    payload = {
        "tag_name": release["tag_name"],
        "name": release["name"] or release["tag_name"],
        "body": release["body"] or "",
        "prerelease": bool(release["prerelease"]),
        "target_commitish": release["target_commitish"],
    }
    existing = find_gitee_release(release["tag_name"])
    base_path = f"/repos/{gitee_owner}/{gitee_repo}/releases"
    if existing is None:
        print(f"Creating Gitee release {release['tag_name']}")
        return gitee_request("POST", base_path, json=payload).json()

    print(f"Updating Gitee release {release['tag_name']}")
    update_payload = {
        key: payload[key] for key in ("tag_name", "name", "body", "prerelease")
    }
    return gitee_request(
        "PATCH", f"{base_path}/{existing['id']}", json=update_payload
    ).json()


def sync_assets(github_release: dict, gitee_release: dict) -> None:
    base_path = (
        f"/repos/{gitee_owner}/{gitee_repo}/releases/{gitee_release['id']}/attach_files"
    )
    existing_assets = {
        item["name"]: item for item in gitee_request("GET", base_path).json()
    }

    for asset in github_release.get("assets", []):
        existing = existing_assets.get(asset["name"])
        if existing and int(existing.get("size", -1)) == int(asset["size"]):
            print(f"Asset already current: {github_release['tag_name']}/{asset['name']}")
            continue

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_path = Path(temp_dir) / asset["name"]
            download_url = asset.get("browser_download_url") or asset["url"]
            sha256 = hashlib.sha256()
            with checked(
                github.get(
                    download_url,
                    headers={"Accept": "application/octet-stream"},
                    stream=True,
                    timeout=TIMEOUT,
                )
            ) as download:
                with asset_path.open("wb") as output:
                    for chunk in download.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
                            sha256.update(chunk)

            expected_size = int(asset["size"])
            actual_size = asset_path.stat().st_size
            if actual_size != expected_size:
                raise RuntimeError(
                    f"Downloaded asset size mismatch for "
                    f"{github_release['tag_name']}/{asset['name']}: "
                    f"expected {expected_size}, got {actual_size}"
                )

            actual_digest = sha256.hexdigest()
            expected_digest = str(asset.get("digest") or "")
            if expected_digest.startswith("sha256:"):
                expected_sha256 = expected_digest.removeprefix("sha256:").lower()
                if actual_digest.lower() != expected_sha256:
                    raise RuntimeError(
                        f"Downloaded asset SHA-256 mismatch for "
                        f"{github_release['tag_name']}/{asset['name']}: "
                        f"expected {expected_sha256}, got {actual_digest}"
                    )
            print(
                f"Verified GitHub asset: {github_release['tag_name']}/{asset['name']} "
                f"({actual_size} bytes, sha256:{actual_digest})"
            )

            if existing:
                print(
                    f"Replacing changed asset: "
                    f"{github_release['tag_name']}/{asset['name']}"
                )
                gitee_request("DELETE", f"{base_path}/{existing['id']}")

            print(f"Uploading asset: {github_release['tag_name']}/{asset['name']}")
            for attempt in range(1, UPLOAD_ATTEMPTS + 1):
                try:
                    with asset_path.open("rb") as upload:
                        multipart = MultipartEncoder(
                            fields={
                                "access_token": gitee_token,
                                "file": (
                                    asset["name"],
                                    upload,
                                    "application/octet-stream",
                                ),
                            }
                        )
                        gitee_request(
                            "POST",
                            base_path,
                            timeout=UPLOAD_TIMEOUT,
                            data=multipart,
                            headers={"Content-Type": multipart.content_type},
                        )
                    break
                except requests.RequestException:
                    mirrored_assets = {
                        item["name"]: item
                        for item in gitee_request("GET", base_path).json()
                    }
                    mirrored = mirrored_assets.get(asset["name"])
                    if int((mirrored or {}).get("size", -1)) == expected_size:
                        print(
                            f"Upload response was interrupted, but Gitee has the "
                            f"complete asset: {github_release['tag_name']}/"
                            f"{asset['name']}"
                        )
                        break
                    if attempt == UPLOAD_ATTEMPTS:
                        raise
                    print(
                        f"Upload attempt {attempt}/{UPLOAD_ATTEMPTS} failed for "
                        f"{github_release['tag_name']}/{asset['name']}; retrying"
                    )
                    time.sleep(5)

            mirrored = None
            mirrored_size = -1
            for attempt in range(5):
                mirrored_assets = {
                    item["name"]: item
                    for item in gitee_request("GET", base_path).json()
                }
                mirrored = mirrored_assets.get(asset["name"])
                mirrored_size = int((mirrored or {}).get("size", -1))
                if mirrored_size == expected_size:
                    break
                if attempt < 4:
                    time.sleep(2)
            if mirrored_size != expected_size:
                raise RuntimeError(
                    f"Gitee asset size mismatch after upload for "
                    f"{github_release['tag_name']}/{asset['name']}: "
                    f"expected {expected_size}, got {mirrored_size}"
                )
            existing_assets[asset["name"]] = mirrored
            print(
                f"Verified Gitee asset: {github_release['tag_name']}/{asset['name']} "
                f"({mirrored_size} bytes)"
            )


def main() -> None:
    releases = github_releases()
    published = [release for release in releases if not release["draft"]]
    if sync_release_tag == "latest":
        published = published[:1]
    elif sync_release_tag:
        published = [
            release for release in published if release["tag_name"] == sync_release_tag
        ]
        if not published:
            raise RuntimeError(
                f"Published GitHub release not found for tag: {sync_release_tag}"
            )
    print(f"Found {len(published)} published GitHub releases")
    for release in reversed(published):
        gitee_release = upsert_gitee_release(release)
        sync_assets(release, gitee_release)


if __name__ == "__main__":
    main()
