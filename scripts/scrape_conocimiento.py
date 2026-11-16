#!/usr/bin/env python3
"""Captura revisable de fuentes oficiales; nunca publica ni pisa el corpus."""
import argparse, html, json, re
import sys
from pathlib import Path
import urllib.request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core import data_packs  # noqa: E402

ALLOW = ("pinamar.gob.ar", "gba.gob.ar", "ms.gba.gov.ar", "argentina.gob.ar", "servicios.infoleg.gob.ar")
MAX_BYTES, TIMEOUT = 1_000_000, 12

def allowed(url):
    p = urlparse(url); host = (p.hostname or "").lower().rstrip(".")
    return p.scheme == "https" and any(host == d or host.endswith("." + d) for d in ALLOW)

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl): raise ValueError("redirect fuera de allowlist")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def extract_readable(raw):
    raw = re.sub(r"(?is)<(script|style|nav|footer|header).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)</(p|div|h[1-6]|li|article|section|br)>", "\n", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"\n{2,}", "\n", re.sub(r"[ \t]+", " ", text)).strip()

def fetch(url):
    if not allowed(url): raise ValueError("URL no permitida")
    opener = urllib.request.build_opener(SafeRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "SanaTurnosKnowledgeReview/1.0"})
    with opener.open(req, timeout=TIMEOUT) as r:
        if not allowed(r.geturl()): raise ValueError("destino final no permitido")
        raw = r.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("respuesta demasiado grande")
        return extract_readable(raw.decode("utf-8", "replace"))

def main():
    p = argparse.ArgumentParser(); p.add_argument("--output", required=True); p.add_argument("--base", default=str(Path(data_packs.private_pack_dir("ar-b-pinamar")) / "conocimiento.json")); a = p.parse_args()
    docs = json.load(open(a.base, encoding="utf-8")).get("articles", [])
    caps = []
    for d in docs:
        url = (d.get("source") or {}).get("url")
        if not url: continue
        try: caps.append({"id": d.get("id"), "url": url, "texto": fetch(url)[:12000]})
        except Exception as e: caps.append({"id": d.get("id"), "url": url, "error": str(e)})
    with open(a.output, "w", encoding="utf-8") as out: json.dump({"capturas": caps}, out, ensure_ascii=False, indent=2)
if __name__ == "__main__": main()
