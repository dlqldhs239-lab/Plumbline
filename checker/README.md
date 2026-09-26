# Organizer files

`fixtures.json` (repo root) and `checker/run.py` are published by Hackathon Raptors
for DOGFOOD 2026 and are copied here unchanged so a judge can run the checker
from a clean clone:

```sh
python3 checker/run.py .dogfood.toml --fixtures fixtures.json > acceptance-report.txt
```

They are not project code.
