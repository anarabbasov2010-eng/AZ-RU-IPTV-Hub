from __future__ import annotations
import argparse, json, re, shutil, tempfile, time, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import yaml

ROOT = Path(__file__).resolve().parents[1]
UA = "AZ-RU-IPTV-Hub/2.0"

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
    feed: str = ""
    protocol: str = ""
    status: str = "unknown"
    latency_ms: float | None = None
    resolution: str = ""
    bitrate: int = 0
    reason: str = ""
    score: float = 0.0
    epg_url: str = ""
    epg_sources: list = None
    referrer: str = ""
    user_agent: str = ""
    attempts: int = 0
    checked_at: str = ""
    deep_checked: bool = False
    segment_ok: bool = False
    historical_score: float = 0.0
    def __post_init__(self):
        if self.epg_sources is None:
            self.epg_sources = []

def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def fetch(url, timeout, max_bytes=1048576, headers=None):
    h={"User-Agent":UA}; h.update(headers or {})
    with urlopen(Request(url,headers=h),timeout=timeout) as r:
        return r.read(max_bytes).decode("utf-8","replace"),r.geturl(),dict(r.headers)

def canon(url):
    try:
        p=urlsplit((url or "").strip())
        if p.scheme.lower() not in ("http","https") or p.username or p.password: return ""
        host=(p.hostname or "").lower(); port=p.port; netloc=host
        if port and not ((p.scheme=="http" and port==80) or (p.scheme=="https" and port==443)): netloc+=f":{port}"
        return urlunsplit((p.scheme.lower(),netloc,p.path or "/",p.query,""))
    except Exception: return ""

def normalize_country(s,default=""):
    x=str(s or default).strip().lower()
    if x in ("az","azerbaijan","азербайджан"): return "AZ"
    if x in ("ru","russia","россия"): return "RU"
    return str(s or default).strip().upper()

def clean_name(s):
    s=re.sub(r"\s+"," ",(s or "").strip())
    s=re.sub(r"\s*\((?:\d{3,4}p|\d{3,4}i)\)","",s,flags=re.I)
    s=re.sub(r"\s*\[(?:Not 24/7|Geo-blocked|Geo blocked)\]","",s,flags=re.I)
    return s.replace("&amp;","&").strip()

def normkey(s):
    return re.sub(r"[^\w\u0400-\u04ff]+"," ",(s or "").lower(),flags=re.UNICODE).strip()

def display_name(name,country,cfg):
    clean=clean_name(name)
    if country!="RU": return clean
    a=cfg.get("display_names",{}); k=normkey(clean)
    return a.get(k,a.get(k.replace(" ","_"),clean))

def key(name,aliases):
    n=normkey(name); return aliases.get(n,n.replace(" ","_")[:100] or "unknown")

def categories(name,group,cfg):
    s=(name+" "+group).lower(); out=[]
    for c,words in cfg.get("category_keywords",{}).items():
        if any(str(w).lower() in s for w in words): out.append(c)
    return out or ["general"]

def load_json_api(cfg,keyname,default_size):
    b=cfg.get(keyname) or {}
    if not b.get("enabled") or not b.get("api_url"): return []
    try:
        body,_,_=fetch(b["api_url"],cfg["discovery"]["timeout_seconds"],b.get("max_bytes",default_size))
        return json.loads(body)
    except Exception as e:
        print(f"[WARN] {keyname}: {e}"); return []

def load_metadata(cfg):
    return {x.get("id"):x for x in load_json_api(cfg,"metadata",40*1024*1024) if isinstance(x,dict) and x.get("id")}

def load_feeds(cfg):
    return {(x.get("channel"),x.get("id")):x for x in load_json_api(cfg,"feeds",20*1024*1024) if isinstance(x,dict) and x.get("channel")}

def load_logos(cfg):
    out={}
    for x in load_json_api(cfg,"logos",40*1024*1024):
        if not isinstance(x,dict) or not x.get("channel") or not x.get("url"): continue
        k=(x.get("channel"),x.get("feed")); score=(1000 if x.get("in_use") else 0)+int(x.get("width") or 0)
        if score>out.get(k,(None,-1))[1]: out[k]=(x["url"],score)
    return {k:v[0] for k,v in out.items()}

def load_stream_api(cfg):
    return [x for x in load_json_api(cfg,"streams",80*1024*1024) if isinstance(x,dict) and x.get("url")]

def load_guides(cfg): return load_json_api(cfg,"epg",40*1024*1024)

def enrich_from_apis(candidates,metadata,feeds,logos):
    for c in candidates:
        row=metadata.get(c.tvg_id) or metadata.get(c.channel_id)
        if row:
            c.tvg_id=c.tvg_id or row.get("id",""); c.website=c.website or row.get("website") or ""
            if row.get("country") in ("AZ","RU"): c.country=row["country"]
            if not c.categories or c.categories==["general"]: c.categories=row.get("categories") or c.categories
        if c.feed:
            f=feeds.get((c.tvg_id,c.feed))
            if f and f.get("languages"): c.languages=f["languages"]
        logo=logos.get((c.tvg_id,c.feed)) or logos.get((c.tvg_id,None))
        if logo and not c.logo: c.logo=logo

def parse_m3u(text,default_country,source,cfg):
    lines=[x.strip() for x in text.splitlines() if x.strip()]; out=[]; meta={}
    for line in lines:
        if line.startswith("#EXTINF"):
            attrs={m.group(1):str(m.group(2)) for m in re.finditer(r'([\w-]+)="([^"]*)"',line)}
            title=line.split(",",1)[1].strip() if "," in line else attrs.get("tvg-name","Unknown")
            meta={"name":clean_name(title),"logo":attrs.get("tvg-logo",""),"tvg_id":attrs.get("tvg-id",""),
                  "group":attrs.get("group-title",""),"country":attrs.get("tvg-country",default_country),"lang":attrs.get("tvg-language","")}
        elif line.startswith("#"): continue
        else:
            u=canon(urljoin(source,line))
            if not u: continue
            country=normalize_country(meta.get("country"),default_country); name=display_name(meta.get("name","Unknown"),country,cfg)
            out.append(Candidate(key(name,cfg.get("aliases",{})),name,u,country,
                [x.strip() for x in re.split("[,;|]",meta.get("lang","")) if x.strip()],
                categories(name,meta.get("group",""),cfg),meta.get("logo",""),meta.get("tvg_id",""),source=source))
            meta={}
    return out

def manual_candidates(cfg):
    out=[]
    for item in cfg.get("manual_streams",[]):
        country=normalize_country(item.get("country","AZ"),"AZ"); name=display_name(item.get("name","Unknown"),country,cfg); u=canon(item.get("url",""))
        if not u: continue
        out.append(Candidate(key(name,cfg.get("aliases",{})),name,u,country,item.get("languages",["Azerbaijani"]),
            item.get("categories") or categories(name,item.get("group",""),cfg),item.get("logo",""),item.get("tvg_id",""),
            item.get("website",""),"manual",item.get("feed",""),referrer=item.get("referrer",""),user_agent=item.get("user_agent","")))
    return out

def api_stream_candidates(rows,metadata,feeds,logos,cfg):
    out=[]; countries=set(cfg["discovery"].get("countries",["AZ","RU"]))
    for x in rows:
        u=canon(x.get("url","")); cid=x.get("channel"); row=metadata.get(cid) if cid else None; country=(row or {}).get("country","")
        if not u or country not in countries: continue
        name=display_name((row or {}).get("name") or x.get("title") or "Unknown",country,cfg); feed=x.get("feed") or ""
        f=feeds.get((cid,feed)) if cid else None; langs=(f or {}).get("languages") or []
        cats=(row or {}).get("categories") or categories(name,x.get("title",""),cfg)
        logo=logos.get((cid,feed)) or logos.get((cid,None)) or ""
        out.append(Candidate(key(name,cfg.get("aliases",{})),name,u,country,langs,cats,logo,cid or "",(row or {}).get("website") or "",
            "iptv-org-api",feed,resolution=(x.get("quality") or "").strip(),referrer=x.get("referrer") or "",user_agent=x.get("user_agent") or ""))
    return out

def allowed(c,cfg):
    u=c.url.lower(); p=cfg["policy"]
    if p.get("reject_xtream") and ("get.php?" in u or "player_api.php" in u or "xtream" in u): return False
    if p.get("reject_tokenized_urls") and any(x in u for x in ("token=","sig=","signature=","expires=","auth=")): return False
    return True

def inspect(c,cfg,deep=False):
    start=time.perf_counter(); last=""; c.attempts=cfg["discovery"].get("retries",2); c.checked_at=now()
    headers={}
    if c.referrer: headers["Referer"]=c.referrer
    if c.user_agent: headers["User-Agent"]=c.user_agent
    for attempt in range(c.attempts):
        try:
            body,final,hdr=fetch(c.url,cfg["discovery"]["timeout_seconds"],cfg["discovery"]["max_manifest_bytes"],headers)
            c.latency_ms=round((time.perf_counter()-start)*1000,1); low=body[:300000].lower(); ct=(hdr.get("Content-Type") or "").lower()
            if "#extm3u" in low:
                c.protocol="hls"; variants=re.findall(r"RESOLUTION=(\d+x\d+)",body[:300000],re.I); bw=re.findall(r"BANDWIDTH=(\d+)",body[:300000],re.I)
                if variants: c.resolution=max(variants,key=lambda x:int(x.split("x")[0])*int(x.split("x")[1]))
                if bw: c.bitrate=max(map(int,bw))
                if deep:
                    target=None; lines=body.splitlines()
                    for i,ln in enumerate(lines[:-1]):
                        if ln.strip().upper().startswith("#EXT-X-STREAM-INF"): target=lines[i+1].strip(); break
                    if target is None:
                        target=next((ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")),None)
                    if target:
                        child=canon(urljoin(final,target))
                        if child:
                            seg,_,_=fetch(child,cfg["discovery"].get("segment_timeout_seconds",8),cfg["discovery"].get("max_segment_bytes",262144),headers)
                            c.deep_checked=True; c.segment_ok=("#EXTM3U" in seg.upper() or "#EXTINF" in seg.upper())
            elif "<mpd" in low: c.protocol="dash"
            elif "video/" in ct or "audio/" in ct or "octet-stream" in ct: c.protocol="media"
            else: raise ValueError(f"unrecognized content-type={ct}")
            c.status="online"; c.reason="ok"; c.url=canon(final) or c.url
            res=0
            if c.resolution:
                try: w,h=map(int,c.resolution.split("x")); res=min((w*h)/8294400,1)
                except: pass
            deep_bonus=0.12 if c.segment_ok else (0 if not deep else -0.10)
            c.score=100*(0.28+0.16*int((c.latency_ms or 99999)<1000)+0.22*res+0.14*min(c.bitrate/8000000,1)+0.10*int(c.url.startswith("https://"))+deep_bonus+0.10*min(c.historical_score/100,1))
            return c
        except Exception as e: last=str(e); time.sleep(0.4*(attempt+1))
    c.status="offline"; c.reason=last[:240]; c.score=-1; return c

def enrich_epg(candidates,guides):
    exact={}; channel={}
    for g in guides if isinstance(guides,list) else []:
        cid=g.get("channel"); feed=g.get("feed"); urls=list(dict.fromkeys(s["url"] for s in (g.get("sources") or []) if isinstance(s,dict) and s.get("url")))
        if not urls: continue
        if cid and feed: exact[(cid,feed)]=urls
        if cid: channel.setdefault(cid,[]).extend(urls)
    for c in candidates:
        c.epg_sources=list(dict.fromkeys(exact.get((c.tvg_id,c.feed)) or channel.get(c.tvg_id) or [])); c.epg_url=c.epg_sources[0] if c.epg_sources else ""
    return candidates

def choose_best(checked,history):
    for c in checked: c.historical_score=history.get(c.url,{}).get("score",0)
    best={}
    for c in checked:
        if c.status=="online" and (c.channel_id not in best or c.score>best[c.channel_id].score): best[c.channel_id]=c
    byurl={}
    for c in best.values():
        u=canon(c.url)
        if u and (u not in byurl or c.score>byurl[u].score): byurl[u]=c
    return list(byurl.values())

def write_m3u(items,path,title,epg_url):
    header=f'#EXTM3U x-tvg-url="{epg_url}"'
    lines=[header,f"# {title} | generated {now()}"]
    for c in sorted(items,key=lambda x:(x.country,x.name.lower())):
        attrs=[f'tvg-id="{c.tvg_id or c.channel_id}"',f'tvg-name="{c.name}"']
        if c.logo: attrs.append(f'tvg-logo="{c.logo}"')
        if c.epg_url: attrs.append(f'tvg-url="{c.epg_url}"')
        attrs.append(f'group-title="{c.country} • {c.categories[0].title()}"')
        lines += [f"#EXTINF:-1 {' '.join(attrs)},{c.name}",c.url]
    path.write_text("\n".join(lines)+"\n",encoding="utf-8")

def merge_epg(items,out_path,cfg):
    wanted={c.tvg_id or c.channel_id for c in items}; sources=list(dict.fromkeys(s for c in items for s in c.epg_sources))
    root=ET.Element("tv",{"generator-info-name":"AZ-RU-IPTV-Hub","generated":now()}); seen_channels=set(); seen_programs=set(); used=[]
    for src in sources[:cfg.get("epg",{}).get("max_sources",80)]:
        try:
            body,_,_=fetch(src,cfg["epg"].get("download_timeout_seconds",12),cfg["epg"].get("max_source_bytes",30*1024*1024)); parsed=ET.fromstring(body)
            for ch in parsed.findall("channel"):
                if ch.get("id") in wanted and ch.get("id") not in seen_channels: root.append(ch); seen_channels.add(ch.get("id"))
            for p in parsed.findall("programme"):
                cid=p.get("channel")
                if cid in wanted:
                    k=(cid,p.get("start",""),p.get("stop",""),"".join(p.itertext())[:120])
                    if k not in seen_programs: root.append(p); seen_programs.add(k)
            used.append(src)
        except Exception as e: print(f"[WARN] EPG source {src}: {e}")
    ET.ElementTree(root).write(out_path,encoding="utf-8",xml_declaration=True)
    return used,len(seen_channels),len(seen_programs)

def update_source_history(checked,old):
    data=dict(old); ts=now()
    for c in checked:
        r=data.get(c.url,{}); total=r.get("checks",0)+1; online=r.get("online",0)+(c.status=="online")
        data[c.url]={"checks":total,"online":online,"uptime_pct":round(100*online/total,2),"score":round(c.score,2),
                     "latency_ms":c.latency_ms,"last_status":c.status,"last_reason":c.reason,"last_checked":ts,"deep_ok":c.segment_ok}
    return data

def run(config_path):
    cfg=yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    metadata=load_metadata(cfg); feeds=load_feeds(cfg); logos=load_logos(cfg); guides=load_guides(cfg); stream_rows=load_stream_api(cfg)
    candidates=manual_candidates(cfg)+api_stream_candidates(stream_rows,metadata,feeds,logos,cfg)
    for src in cfg["catalogs"]:
        try:
            body,_,_=fetch(src["url"],cfg["discovery"]["timeout_seconds"],16*1024*1024); got=parse_m3u(body,src.get("country",""),src["url"],cfg)
            if src.get("filter_countries"): got=[x for x in got if x.country in src["filter_countries"]]
            for x in got:
                row=metadata.get(x.tvg_id) if x.tvg_id else None
                if row:
                    x.country=row.get("country") or x.country
                    x.languages=x.languages or row.get("languages") or []
                    x.categories=row.get("categories") or x.categories
            if src.get("filter_languages"):
                wanted={str(x).lower() for x in src["filter_languages"]}
                got=[x for x in got if any(str(lang).lower() in wanted for lang in x.languages)]
            if src.get("filter_categories"):
                wanted={str(x).lower() for x in src["filter_categories"]}
                got=[x for x in got if any(str(cat).lower() in wanted for cat in x.categories)]
            if src.get("filter_countries"):
                got=[x for x in got if x.country in src["filter_countries"]]
            candidates.extend(got)
        except Exception as e: print(f"[WARN] catalog {src['id']}: {e}")
    enrich_from_apis(candidates,metadata,feeds,logos)
    for c in candidates: c.channel_id=key(c.name,cfg.get("aliases",{}))
    grouped={}
    for c in candidates:
        if not allowed(c,cfg): continue
        u=canon(c.url)
        if not u: continue
        grouped.setdefault(c.channel_id,[])
        if all(x.url!=u for x in grouped[c.channel_id]): grouped[c.channel_id].append(c)
    max_per=int(cfg["discovery"].get("max_candidates_per_channel",12)); candidates=[x for rows in grouped.values() for x in rows[:max_per]]
    print(f"[INFO] candidates={len(candidates)} channels={len(grouped)}")
    hp=ROOT/"status/source-history.json"
    try: history=json.loads(hp.read_text(encoding="utf-8")) if hp.exists() else {}
    except: history={}
    checked=[]
    with ThreadPoolExecutor(max_workers=cfg["discovery"].get("concurrency",24)) as ex:
        for f in as_completed([ex.submit(inspect,c,cfg,False) for c in candidates]): checked.append(f.result())
    online=sorted([c for c in checked if c.status=="online"],key=lambda x:x.score,reverse=True)
    deep=[]
    with ThreadPoolExecutor(max_workers=min(12,cfg["discovery"].get("concurrency",24))) as ex:
        for f in as_completed([ex.submit(inspect,c,cfg,True) for c in online[:cfg["discovery"].get("deep_check_limit",350)] ]): deep.append(f.result())
    deep_map={c.url:c for c in deep}; checked=[deep_map.get(c.url,c) for c in checked]
    items=choose_best(checked,history)
    if not items: raise RuntimeError("No healthy public stream candidates survived validation")
    enrich_epg(items,guides); source_history=update_source_history(checked,history)
    build=Path(tempfile.mkdtemp(prefix="iptv-build-"))
    try:
        for d in ("playlists","data","status","epg"): (build/d).mkdir(parents=True,exist_ok=True)
        epg_path=build/"epg/programs.xml"; used_epg,epg_channels,epg_programs=merge_epg(items,epg_path,cfg)
        epg_public="https://raw.githubusercontent.com/anarabbasov2010-eng/AZ-RU-IPTV-Hub/main/epg/programs.xml"
        write_m3u(items,build/"playlists/all.m3u","AZ-RU-IPTV-Hub • all",epg_public)
        filters=[
            ("azerbaijan.m3u",lambda c:c.country=="AZ","Azerbaijan"),("russia.m3u",lambda c:c.country=="RU","Russia"),
            ("russian-language.m3u",lambda c:c.country=="RU" or any("rus" in str(x).lower() or str(x).lower()=="ru" for x in c.languages),"Russian language"),
            ("sports.m3u",lambda c:"sports" in c.categories,"Sports"),("movies.m3u",lambda c:"movies" in c.categories,"Movies"),
            ("news.m3u",lambda c:"news" in c.categories,"News"),("kids.m3u",lambda c:"kids" in c.categories,"Kids"),("music.m3u",lambda c:"music" in c.categories,"Music"),
            ("az-sports.m3u",lambda c:c.country=="AZ" and "sports" in c.categories,"Azerbaijan Sports"),("ru-sports.m3u",lambda c:c.country=="RU" and "sports" in c.categories,"Russian Sports"),
            ("hd.m3u",lambda c:bool(c.resolution) and int(re.match(r"\d+",c.resolution).group())>=1280,"HD"),
            ("full-hd.m3u",lambda c:bool(c.resolution) and int(re.match(r"\d+",c.resolution).group())>=1920,"Full HD"),
            ("low-bandwidth.m3u",lambda c:bool(c.resolution) and int(re.match(r"\d+",c.resolution).group())<=1280,"Low bandwidth")]
        for fn,pred,title in filters: write_m3u([c for c in items if pred(c)],build/"playlists"/fn,title,epg_public)
        data=[asdict(c) for c in items]; sources_data={}
        for c in checked:
            if c.status=="online":
                bucket=sources_data.setdefault(c.channel_id,[])
                if all(x["url"]!=c.url for x in bucket): bucket.append(asdict(c))
        (build/"data/catalog.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        (build/"data/sources.json").write_text(json.dumps(sources_data,ensure_ascii=False,indent=2),encoding="utf-8")
        (build/"data/epg-sources.json").write_text(json.dumps({"generated_at":now(),"sources":used_epg},ensure_ascii=False,indent=2),encoding="utf-8")
        (build/"status/source-history.json").write_text(json.dumps(source_history,ensure_ascii=False,indent=2),encoding="utf-8")
        report={"generated_at":now(),"candidates_checked":len(checked),"online_unique_channels":len(items),
                "online_candidates":sum(c.status=="online" for c in checked),"offline_candidates":sum(c.status=="offline" for c in checked),
                "deep_checked":sum(c.deep_checked for c in checked),"deep_segment_ok":sum(c.segment_ok for c in checked),
                "epg_mapped_channels":sum(bool(c.epg_url) for c in items),"epg_channels":epg_channels,"epg_programs":epg_programs,
                "epg_sources_used":len(used_epg),"source_count":sum(len(v) for v in sources_data.values()),"channels":data}
        (build/"status/health.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        hist_path=ROOT/"status/history.json"
        try: hist=json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
        except: hist=[]
        hist.append({k:report[k] for k in ("generated_at","online_unique_channels","online_candidates","offline_candidates","deep_checked","deep_segment_ok","epg_mapped_channels","epg_channels","epg_programs")})
        (build/"status/history.json").write_text(json.dumps(hist[-168:],ensure_ascii=False,indent=2),encoding="utf-8")
        for src in build.iterdir():
            for f in src.rglob("*"):
                if f.is_file():
                    rel=f.relative_to(build); dest=ROOT/rel; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(f,dest)
    finally: shutil.rmtree(build,ignore_errors=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--config",default="config/sources.yml"); run(ap.parse_args().config)
