#!/usr/bin/env python3
"""Upload an extension to the Chrome Web Store through its API (v1.1).

    python3 scripts/publish_extension.py hsi-humanizador            # upload a draft
    python3 scripts/publish_extension.py hsi-humanizador --publish  # and submit it

It uploads extensions/dist/<name>-store.zip (build it first with
scripts/build_extension.py). The item id lives in
extensions/<name>/store/item.json as {"item_id": "…", "visibility": "unlisted"};
without an id a new item is created and its id is written back.

Credentials come only from the environment and are never printed:
CWS_CLIENT_ID, CWS_CLIENT_SECRET, CWS_REFRESH_TOKEN (an OAuth client of the
developer account with the chromewebstore scope).

The API uploads the package and submits it for review. The listing itself —
description, screenshots, privacy answers, and the first choice of visibility —
is filled once in the developer console from extensions/<name>/store/.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://www.googleapis.com"


def call(method, url, token=None, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.add_header("x-goog-api-version", "2")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        # The body may echo request details: report the status only.
        sys.exit("Chrome Web Store answered %d on %s %s" % (err.code, method, url.split("?")[0]))


def access_token():
    missing = [k for k in ("CWS_CLIENT_ID", "CWS_CLIENT_SECRET", "CWS_REFRESH_TOKEN") if not os.environ.get(k)]
    if missing:
        sys.exit("missing environment: " + ", ".join(missing))
    body = urllib.parse.urlencode({
        "client_id": os.environ["CWS_CLIENT_ID"],
        "client_secret": os.environ["CWS_CLIENT_SECRET"],
        "refresh_token": os.environ["CWS_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    _, out = call("POST", "https://oauth2.googleapis.com/token", data=body,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    return out["access_token"]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    name = args[0]
    package = os.path.join(ROOT, "extensions", "dist", name + "-store.zip")
    if not os.path.isfile(package):
        sys.exit("%s not found — run scripts/build_extension.py %s" % (os.path.relpath(package, ROOT), name))
    item_path = os.path.join(ROOT, "extensions", name, "store", "item.json")
    item = {}
    if os.path.isfile(item_path):
        with open(item_path, encoding="utf-8") as fh:
            item = json.load(fh)

    token = access_token()
    with open(package, "rb") as fh:
        data = fh.read()
    if item.get("item_id"):
        status, out = call("PUT", "%s/upload/chromewebstore/v1.1/items/%s" % (API, item["item_id"]), token, data)
    else:
        status, out = call("POST", API + "/upload/chromewebstore/v1.1/items", token, data)
        if out.get("id"):
            item["item_id"] = out["id"]
            os.makedirs(os.path.dirname(item_path), exist_ok=True)
            with open(item_path, "w", encoding="utf-8") as fh:
                json.dump(item, fh, indent=2)
                fh.write("\n")
    print("upload: %d %s item=%s" % (status, out.get("uploadState", "?"), item.get("item_id", "?")))
    if out.get("uploadState") != "SUCCESS":
        sys.exit("upload did not succeed: " + ", ".join(e.get("error_code", "?") for e in out.get("itemError", [])))

    if "--publish" in sys.argv:
        target = "default" if item.get("visibility") in ("unlisted", "public") else "trustedTesters"
        status, out = call("POST", "%s/chromewebstore/v1.1/items/%s/publish?publishTarget=%s"
                           % (API, item["item_id"], target), token, b"")
        print("publish (%s): %d %s" % (target, status, ", ".join(out.get("status", []))))


if __name__ == "__main__":
    main()
