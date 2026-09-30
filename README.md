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
- Produces machine-readable health/status data.
- Runs automatically with GitHub Actions.

The project follows the same basic principles documented by iptv-org: stream links should be direct, stable, non-tokenized and not Xtream Codes links.

## Important

This repository does not host video files. It links to externally hosted streams. Only use streams that are publicly available and permitted for redistribution/linking by their provider. The fallback system never bypasses authentication, paid access or geo-restrictions.

## Playlists

Generated files live in playlists/:

- all.m3u
- azerbaijan.m3u
- russia.m3u
- russian-language.m3u
- sports.m3u
- movies.m3u
- news.m3u
- kids.m3u
- music.m3u

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
M3U + EPG + health.json

## Automation

The updater runs every four hours and can also be started manually from the Actions tab.

## Sources

The default discovery source is iptv-org's public IPTV ecosystem. Channel metadata, streams and EPG are maintained as separate datasets there.

## License

Code: MIT. External channel metadata, logos and streams remain subject to their respective rights and source terms.
