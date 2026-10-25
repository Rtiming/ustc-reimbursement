#!/usr/bin/env python3
"""从已观察到的公开入口记录 GET/登录跳转；不提交认证凭据。"""
import argparse
import hashlib
import http.cookiejar
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
URLS = ["https://cwzh.ustc.edu.cn/", "https://cwzh.ustc.edu.cn/WFManager/home2.jsp",
        "https://cwzh.ustc.edu.cn/YBX/main.jsp"]
HOSTS = {"cwzh.ustc.edu.cn", "passport.ustc.edu.cn", "id.ustc.edu.cn"}


def safe_url(url):
    u = urllib.parse.urlsplit(url)
    path = re.sub(r";jsessionid=[^;/]+", ";jsessionid=[REDACTED]", u.path, flags=re.I)
    query = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    safe = [(k, v if k == "service" and "ticket" not in v.lower() else "[REDACTED]") for k, v in query]
    return urllib.parse.urlunsplit((u.scheme, u.netloc, path, urllib.parse.urlencode(safe), ""))


class TraceRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, trace):
        self.trace = trace

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if urllib.parse.urlsplit(target).hostname not in HOSTS:
            raise ValueError("Redirect outside the configured finance/SSO hosts")
        self.trace.append({"status": code, "from": safe_url(req.full_url), "to": safe_url(target),
                           "cookie_names": [v.split("=", 1)[0] for v in headers.get_all("Set-Cookie", [])]})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=ROOT / "evidence/public_probe.json")
    args = ap.parse_args()
    entries = []
    for url in URLS:
        trace = []
        # On macOS some python.org installations lack a populated system CA path.
        # Prefer certifi when available, preserving certificate verification.
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = ssl.create_default_context()
        op = urllib.request.build_opener(TraceRedirect(trace), urllib.request.HTTPSHandler(context=context),
             urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        try:
            try:
                resp = op.open(urllib.request.Request(url, headers={"User-Agent": "ReimbursementResearch/0.1"}), timeout=20)
            except urllib.error.HTTPError as e:
                resp = e
            with resp:
                raw = resp.read(2 * 1024 * 1024)
                text = raw.decode("utf-8", "replace")
                entries.append({"requested": url, "effective": safe_url(resp.url), "status": resp.code,
                  "redirects": trace, "response_bytes_read": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                  "title": re.findall(r"<title>(.*?)</title>", text, re.S),
                  "static_scripts": [safe_url(urllib.parse.urljoin(resp.url, x)) for x in
                      re.findall(r'<script[^>]+src=["\x27]([^"\x27]+)', text, re.I)],
                  "authentication_probe": "GET only; no credentials; ephemeral cookie jar"})
        except (OSError, ValueError) as e:
            entries.append({"requested": url, "error": type(e).__name__, "message": str(e), "redirects": trace})
    result = {"checked_at": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat(), "entries": entries}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
