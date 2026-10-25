# Contributing to FamilyAgent

FamilyAgent focuses on role collaboration, explicit human approval, private context and recoverable execution. Small, verifiable changes are welcome.

## Development

Use Python 3.12 and Node 22. Install `requirements.lock` into a virtual environment and run `npm ci`. Copy `.env.example` only when testing a local app; automated checks use demo mode and isolated databases.

```bash
python -m pip install --only-binary=cryptography,cffi,rpds-py,pydantic-core -r requirements.lock
npm ci
python scripts/check.py
```

The browser stage needs Chrome or `npx playwright install chromium`; Linux may require `npx playwright install --with-deps chromium`. `PYTHON_BIN` and `CHROME_PATH` can select local executables. See [CI](docs/10-ci.md) and [containers](docs/11-containers.md).

## Changes and pull requests

- Explain the user-visible problem, resulting behavior, validation and remaining limits.
- Use synthetic fixtures; never commit API keys, `.env`, databases, personal notebooks or private conversations.
- Keep approval writes transactional. New tools need argument/result validation and explicit role/origin authorization.
- Preserve attempt fencing, ownership checks, lease bounds and authorized context filtering.
- Append database migrations instead of editing released SQL; test upgrade, rollback and old-data preservation.
- Record behavioral changes and before/after examples in `docs/PROGRESS.md` and `docs/04-improvements.md`.
- Add meaningful regression coverage for changed behavior and run the relevant checks. Do not claim a real provider or deployment works based only on mock tests.

Real model evaluations require explicit provider configuration, `--live` and a call budget. Do not include credentials in issues or ask maintainers to run unbounded paid tests. A model judge's opinion must be labeled separately from human review.

## Scope and license

Code contributions are under the repository's MIT license. Keep dependency notices intact; project licensing does not replace third-party terms. Before implementing a large feature, describe the intended behavior and trade-offs in an issue.
