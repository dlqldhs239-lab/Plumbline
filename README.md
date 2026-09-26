# Plumbline

A self-hostable hackathon submission and judging portal, built for
[DOGFOOD 2026](https://dogfoodhack.com/) by Hackathon Raptors.

A plumb line is the weighted string a builder hangs to see whether a wall is
truly vertical. This portal is built around the same idea: judging that is
straight because the backend holds the line, not because a button is hidden.

## Run it

```sh
docker compose up
```

Then open <http://localhost:8080>. The seed step loads `fixtures.json` and
prints the four auth headers used by `.dogfood.toml`.

## Status

Work in progress during the 72-hour window (kickoff 2026-09-26 18:00 UTC).
Tier claims live in `.dogfood.toml`; the receipt is `acceptance-report.txt`.
