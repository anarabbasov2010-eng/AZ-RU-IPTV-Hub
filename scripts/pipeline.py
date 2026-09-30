from __future__ import annotations
import argparse, json, re, shutil, tempfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import yaml

ROOT = Path(__file__).resolve().parents[1]
UA = "AZ-RU-IPTV-Hub/1.0"

@dataclass
class Candidate:
    channel_id: str
    name: str
    url: str
    country: str
    languages: list
    categories: list
    logo: str = ""
    tvg_id: str = ""
    website: str = ""
    source: str = ""
    protocol: str = ""
    status: str = "unknown"
    latency_ms: float | None = None
    resolution: str = ""
    bitrate: int = 0
    reason: str = ""
    score: float = 0.0

def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def fetch(url, timeout, max_bytes=1048576, headers=None):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = Request(url, headers=h)
    with urlopen(req, timeout=timeout) as r:
        data = r.read(max_bytes)
        return data.decode("utf-8", "replace"), r.geturl(), dict(r.headers)

def canon(url):
    p = urlsplit(url.strip())
    if p.scheme.lower() not in ("http", "https") or p.username or p.password:
        return ""
    host = (p.hostname or "").lower()
    port = p.port
    netloc = host
    if port and not ((p.scheme == "http" and port == 80) or (p.scheme == "https" and port == 443)):
        netloc += f":{port}"
    return urlunsplit((p.scheme.lower(), netloc, p.path or "/", p.query, ""))

def clean_name(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s.replace("&amp;", "&")

def key(name, aliases):
    n = re.sub(r"[^\w\u0400-\u04ff]+", " ", name.lower(), flags=re.UNICODE).strip()
    return aliases.get(n, n.replace(" ", "_")[:80] or "unknown")

def categories(name, group, cfg):
    s = (name + " " + group).lower()
    out = []
    for c, words in cfg.get("category_keywords", {}).items():
        if any(w.lower() in s for w in words):
            out.append(c)
    return out or ["general"]

def parse_m3u(text, default_country, source, cfg):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    out, meta = [], {}
    for line in lines:
        if line.startswith("#EXTINF"):
            attrs = dict((m.group(1), m.group(2)) for m in re.finditer(r'([\w-]+)="([^"]*)"', line))
            title = line.split(",", 1)[1].strip() if "," in line else attrs.get("tvg-name", "Unknown")
            meta = {
                "name": clean_name(title),
                "logo": attrs.get("tvg-logo", ""),
                "tvg_id": attrs.get("tvg-id", ""),
                "group": attrs.get("group-title", ""),
                "country": attrs.get("tvg-country", default_country),
                "lang": attrs.get("tvg-language", ""),
            }
        elif line.startswith("#"):
            continue
        else:
            u = canon(urljoin(source, line))
            if not u:
                continue
            name = meta.get("name", "Unknown")
            out.append(Candidate(
                channel_id=key(name, cfg.get("aliases", {})),
                name=name,
                url=u,
                country=meta.get("country") or default_country,
                languages=[x.strip() for x in re.split("[,;|]", meta.get("lang", "")) if x.strip()],
                categories=categories(name, meta.get("group", ""), cfg),
                logo=meta.get("logo", ""),
                tvg_id=meta.get("tvg_id", ""),
                source=source
            ))
            meta = {}
    return out

def allowed(c, cfg):
    pol = cfg["policy"]
    u = c.url.lower()
    if pol.get("reject_xtream") and ("get.php?" in u or "player_api.php" in u or "xtream" in u):
        return False
    if pol.get("reject_tokenized_urls") and any(x in u for x in ("token=", "sig=", "signature=", "expires=", "auth=")):
        return False
    return True

def inspect(c, cfg):
    start = time.perf_counter()
    last = ""
    retries = cfg["discovery"].get("retries", 3)
    for attempt in range(retries):
        try:
            body, final, hdr = fetch(
                c.url,
                cfg["discovery"]["timeout_seconds"],
                cfg["discovery"]["max_manifest_bytes"],
                {"User-Agent": cfg["discovery"].get("user_agent", UA)}
            )
            c.latency_ms = round((time.perf_counter() - start) * 1000, 1)
            ct = (hdr.get("Content-Type") or "").lower()
            low = body[:200000].lower()
            if "#extm3u" in low:
                c.protocol = "hls"
                if "#ext-x-stream-inf" in low:
                    m = re.findall(r"RESOLUTION=(\d+x\d+)", body[:200000], re.I)
                    if m:
                        c.resolution = max(m, key=lambda x: int(x.split("x")[0]) * int(x.split("x")[1]))
                    b = re.findall(r"BANDWIDTH=(\d+)", body[:200000], re.I)
                    if b:
                        c.bitrate = max(map(int, b))
            elif "<mpd" in low:
                c.protocol = "dash"
            elif "video/" in ct or "audio/" in ct or "octet-stream" in ct:
                c.protocol = "media"
            else:
                last = f"unrecognized content-type={ct}"
                raise ValueError(last)
            c.status = "online"
            c.reason = "ok"
            c.url = final
            https = 1 if c.url.startswith("https://") else 0
            res = 0
            if c.resolution:
                try:
                    w, h = map(int, c.resolution.split("x"))
                    res = min((w * h) / 8294400, 1)
                except Exception:
                    pass
            c.score = 100 * (
                0.35
                + 0.15 * int(c.latency_ms is not None and c.latency_ms < 1000)
                + 0.25 * res
                + 0.15 * min(c.bitrate / 8000000, 1)
                + 0.10 * https
            )
            return c
        except Exception as e:
            last = str(e)
            time.sleep(0.6 * (attempt + 1))
    c.status = "offline"
    c.reason = last[:240]
    c.score = -1
    return c

def write_m3u(items, path, title):
    lines = ["#EXTM3U", f"# {title} | generated {now()}"]
    for c in sorted(items, key=lambda x: (x.country, x.name.lower())):
        attrs = [f'tvg-id="{c.tvg_id or c.channel_id}"', f'tvg-name="{c.name}"']
        if c.logo:
            attrs.append(f'tvg-logo="{c.logo}"')
        attrs.append(f'group-title="{c.country} • {c.categories[0].title()}"')
        lines += [f"#EXTINF:-1 {' '.join(attrs)},{c.name}", c.url]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def run(config_path):
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    candidates = []
    for src in cfg["catalogs"]:
        try:
            body, base, _ = fetch(src["url"], cfg["discovery"]["timeout_seconds"], 12 * 1024 * 1024)
            got = parse_m3u(body, src.get("country", ""), src["url"], cfg)
            if src.get("filter_countries"):
                got = [x for x in got if x.country in src["filter_countries"]]
            candidates.extend(got)
        except Exception as e:
            print(f"[WARN] catalog {src['id']}: {e}")
    uniq = {}
    for c in candidates:
        if not allowed(c, cfg):
            continue
        k = (c.channel_id, canon(c.url))
        if k not in uniq:
            uniq[k] = c
    candidates = list(uniq.values())
    print(f"[INFO] candidates={len(candidates)}")
    checked = []
    with ThreadPoolExecutor(max_workers=cfg["discovery"].get("concurrency", 18)) as ex:
        fs = [ex.submit(inspect, c, cfg) for c in candidates]
        for f in as_completed(fs):
            checked.append(f.result())
    best = {}
    for c in checked:
        if c.status != "online":
            continue
        prev = best.get(c.channel_id)
        if prev is None or c.score > prev.score:
            best[c.channel_id] = c
    if not best:
        raise RuntimeError("No healthy public stream candidates survived validation")
    items = list(best.values())
    build = Path(tempfile.mkdtemp(prefix="iptv-build-"))
    try:
        for d in ("playlists", "data", "status"):
            (build / d).mkdir(parents=True, exist_ok=True)
        write_m3u(items, build / "playlists/all.m3u", "AZ-RU-IPTV-Hub • all")
        filters = [
            ("azerbaijan.m3u", lambda c: c.country == "AZ", "Azerbaijan"),
            ("russia.m3u", lambda c: c.country == "RU", "Russia"),
            ("russian-language.m3u", lambda c: any("ru" in x.lower() or "russian" in x.lower() for x in c.languages), "Russian language"),
            ("sports.m3u", lambda c: "sports" in c.categories, "Sports"),
            ("movies.m3u", lambda c: "movies" in c.categories, "Movies"),
            ("news.m3u", lambda c: "news" in c.categories, "News"),
            ("kids.m3u", lambda c: "kids" in c.categories, "Kids"),
            ("music.m3u", lambda c: "music" in c.categories, "Music"),
        ]
        for filename, pred, title in filters:
            write_m3u([c for c in items if pred(c)], build / "playlists" / filename, title)
        data = [asdict(c) for c in items]
        (build / "data/catalog.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        report = {
            "generated_at": now(),
            "candidates_checked": len(checked),
            "online_unique_channels": len(items),
            "offline_candidates": sum(c.status == "offline" for c in checked),
            "channels": data,
        }
        (build / "status/health.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for src in build.iterdir():
            for f in src.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(build)
                    dest = ROOT / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
    finally:
        shutil.rmtree(build, ignore_errors=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sources.yml")
    run(ap.parse_args().config)
