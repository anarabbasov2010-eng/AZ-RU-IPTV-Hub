# AZ-RU-IPTV-Hub

Automated IPTV catalog for Azerbaijani and Russian-language/publicly available channels.

## What it does

- Pulls channel/stream candidates from configured public catalogs.
- Normalizes names, countries, languages and categories.
- Removes duplicate channels and duplicate stream URLs.
- Tests candidates with retries and timeouts.
- Scores healthy candidates and publishes one best stream per channel.
- Generates separate AZ, RU, sports, movies, news, kids, music and all-channel M3U playlists.
- Keeps the last-known-good output when an update run fails.
- Produces machine-readable health/status data and multi-source diagnostics.
- Maps channel metadata to Russian Cyrillic display names where the public metadata provides them.
- Integrates public EPG guide mappings and downloads a merged XMLTV program guide.\n- Adds channel/feed metadata, logos, automatic stream discovery, source headers and multiple public fallback URLs.\n- Deep-checks HLS manifests/media playlists and keeps per-source rolling uptime/latency history.
- Generates AZ/RU sports playlists plus HD, Full HD and low-bandwidth playlists.
- Keeps source-level diagnostics in `data/sources.json` and a rolling build history in `status/history.json`.
- Runs automatically with GitHub Actions.

The project follows the same basic principles documented by iptv-org: stream links should be direct, stable, non-tokenized and not Xtream Codes links.

## Important

This repository does not host video files. It links to externally hosted streams. Only use streams that are publicly available and permitted for redistribution/linking by their provider. The fallback system never bypasses authentication, paid access or geo-restrictions.

## Playlists

Generated files live in playlists/:

- all.m3u\n- `epg/programs.xml` — merged XMLTV program schedule for mapped channels
- azerbaijan.m3u
- russia.m3u
- russian-language.m3u
- sports.m3u
- movies.m3u
- news.m3u
- kids.m3u
- music.m3u
- az-sports.m3u
- ru-sports.m3u
- hd.m3u
- full-hd.m3u
- low-bandwidth.m3u

They are regenerated automatically.

## Architecture

public catalogs
      ↓
fetch + parse
      ↓
normalize + aliases
      ↓
deduplicate
      ↓
health checks + retries
      ↓
quality scoring
      ↓
one best candidate/channel
      ↓
metadata + Russian localization
      ↓
EPG mapping
      ↓
M3U + multi-source diagnostics + health/history

## Automation

The updater runs every four hours and can also be started manually from the Actions tab.

## Sources

The default discovery source is iptv-org's public IPTV ecosystem. Channel metadata, streams and EPG are maintained as separate datasets there.

## License

Code: MIT. External channel metadata, logos and streams remain subject to their respective rights and source terms.


## Reliability model

The normal playlists publish one best currently healthy source per channel. The generated `data/sources.json` retains every healthy candidate found during the build, so a consuming application can implement its own failover. Sources are rechecked by GitHub Actions every four hours and on relevant repository changes.

A successful HTTP response is not a permanent uptime guarantee: external stream providers can change or disable a URL after the catalog was generated.

## Coverage

The project discovers Azerbaijani and Russian channels from public catalogs and supplements them with configured public candidates. "All channels" therefore means all channels discoverable through those public sources that pass the current health checks; channels without a working public stream are not silently presented as working.

## EPG

EPG integration uses the public channel/guide metadata exposed by the iptv-org ecosystem. The API documents channels, streams, logos and guides as separate datasets. citeturn0search2
