"""Official stable-release APIs with bounded responses and ETag caching."""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .config import https_url
from .version import canonical, key

LIMIT = 4 * 1024 * 1024


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
            redirected.remove_header("PRIVATE-TOKEN")
        return redirected


def fetch(url, etag=None, token=None):
    https_url(url)
    headers = {"User-Agent": "pharos-release/0.1.0", "Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.build_opener(SafeRedirect()).open(request, timeout=15) as response:
            data = response.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError("Release response exceeds 4 MiB")
            return json.loads(data), response.headers.get("ETag")
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return None, etag
        # Do not expose authenticated URLs, response bodies or headers.
        raise RuntimeError(f"Release source returned HTTP {error.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("Release source connection failed") from None


def parse(source, data):
    entries = data if isinstance(data, list) else [data]
    releases = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or entry.get("draft")
            or entry.get("prerelease")
            or entry.get("upcoming_release")
        ):
            continue
        tag = entry.get(source.get("version_field", "tag_name"))
        version = canonical(tag)
        if not version or ("major" in source and int(version.split(".")[0]) != source["major"]):
            continue
        url = entry.get("html_url") or entry.get("_links", {}).get("self") or entry.get("url") or source.get("url", "")
        if url:
            https_url(url)
        releases.append(
            {
                "version": version,
                "tag": tag,
                "url": url,
                "published_at": entry.get("published_at") or entry.get("released_at"),
            }
        )
    if not releases:
        raise ValueError("Source contains no supported stable numeric releases")
    return max(releases, key=lambda release: key(release["version"]))


def latest(source, cache, force=False):
    kind = source["type"]
    url = f"https://api.github.com/repos/{source['repo']}/releases?per_page=100" if kind == "github" else source["url"]
    # Feed identity and policy changes cannot reuse a previous source's baseline.
    identity = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    old = cache if cache.get("identity") == identity else {}
    if not force and time.time() - old.get("checked_at", 0) < 3600:
        return old["release"], old
    token = None
    if source.get("token_env"):
        token = os.environ.get(source["token_env"])
        if not token:
            raise ValueError("Configured source token environment variable is missing")
    data, etag = fetch(url, old.get("etag"), token)
    if data is None:
        if not old.get("release"):
            raise ValueError("Source returned 304 without a cached release")
        release = old["release"]
    else:
        release = parse(source, data)
    return release, {"identity": identity, "checked_at": time.time(), "etag": etag, "release": release}
