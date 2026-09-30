from pathlib import Path
import json
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=ROOT/"playlists"
    files=list(p.glob("*.m3u"))
    assert files, "No playlists generated"
    alltext=(p/"all.m3u").read_text(encoding="utf-8")
    urls=[x.strip() for x in alltext.splitlines() if x.startswith(("http://","https://"))]
    assert urls and len(urls)==len(set(urls)), "Duplicate stream URLs in all.m3u"
    health=json.loads((ROOT/"status/health.json").read_text(encoding="utf-8"))
    assert health["online_unique_channels"]>0
    assert health["online_unique_channels"]==len(health["channels"])
    for c in health["channels"]:
        assert c["url"].startswith(("http://","https://"))
        assert c["status"]=="online"
        assert c["tvg_id"], f"Missing tvg-id: {c['name']}"
    epg=ROOT/"epg/programs.xml"
    assert epg.exists() and epg.stat().st_size>100, "EPG XMLTV file missing/empty"
    root=ET.parse(epg).getroot()
    assert root.tag=="tv"
    assert health.get("epg_channels",0)>=0
    assert health.get("epg_programs",0)>=0
    sources=json.loads((ROOT/"data/sources.json").read_text(encoding="utf-8"))
    for cid,rows in sources.items():
        seen=set()
        for row in rows:
            assert row["url"] not in seen, f"Duplicate source URL for {cid}"
            seen.add(row["url"])
    print(f"OK: {health['online_unique_channels']} healthy channels; {health.get('epg_programs',0)} EPG programs; {health.get('deep_segment_ok',0)} deep checks passed")

if __name__=="__main__":
    main()
