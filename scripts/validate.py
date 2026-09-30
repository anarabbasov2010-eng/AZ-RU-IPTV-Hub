from pathlib import Path
import json
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
    print(f"OK: {health['online_unique_channels']} healthy unique channels")
if __name__=="__main__": main()
