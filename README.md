# Valecreative Firebase scripts

Command-line utilities for the `valecreative-prod` Firebase project:

- `set_admin_claim.py` — grant/remove the `admin` custom claim used by the backoffice
- `normalize_data.py` — align existing Firestore/Storage data (image file names, Cache-Control, alt texts, slugs) with the rules the backoffice applies to new uploads, and clean up unused Storage files (see below)

## Requirements

- Python 3.10+
- Firebase project with Authentication and Firestore enabled
- Service account key with appropriate permissions

## Setup

1. Create and activate virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment variables (see Configuration section below)

## Configuration

Create a `.env` file in the project root with the following variables:

```env
# Required: Path to your Firebase service account JSON file
SERVICE_ACCOUNT_PATH=./google-sa-key_staging.json

# Required: Your Firebase project ID
PROJECT_ID=your-firebase-project-id

# For set_admin_claim.py
USERS_TO_SET_ADMIN=user1@example.com,user2@example.com
USERS_TO_REMOVE_ADMIN=user3@example.com
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SERVICE_ACCOUNT_PATH` | Yes | Path to Firebase service account JSON file |
| `PROJECT_ID` | Yes | Firebase project ID |
| `USERS_TO_SET_ADMIN` | No | Comma-separated emails/UIDs to grant admin claim |
| `USERS_TO_REMOVE_ADMIN` | No | Comma-separated emails/UIDs to remove admin claim |

Users can be specified by email address or Firebase UID.

## Usage

### Set/Remove Admin Claims

```bash
python set_admin_claim.py
```

The script will:
- Initialize Firebase Admin SDK using the service account
- Process users to grant the claim (if specified)
- Process users to remove the claim (if specified)
- Print a summary of successful/failed operations

### Normalize existing images and slugs (`normalize_data.py`)

Brings existing Firestore/Storage data to the same state the backoffice produces for new uploads
(rules ported 1:1 from `valecreative-admin-backoffice/src/utils/slugify.ts` and `imageFileName.ts`, see `normalizer/`):

- **Slugs**: `toSlug` / `SLUG_PATTERN` (`contents`: `CONTENT_SLUG_PATTERN`, underscores allowed). Only non-conforming, empty or
  duplicated slugs change; duplicates: the title-consistent doc keeps the slug, then the oldest by `createdAt`, the others
  get their own title slug or `-2`, `-3`. `categories` slugs are never modified (report only).
- **File names**: cover `{slug}.{ext}`, gallery `{artworkSlug}-{n}.{ext}` (n by `imagePosition`, then `uploadedAt`), in the same
  uuid folder. Storage has no rename: the object is copied with a new download token; the old file is kept until cleanup.
- **Cache-Control** `public, max-age=31536000, immutable` on every referenced object.
- **Alt**: set to the record title (series: `name`; gallery: artwork title) when empty or file-name-like; descriptive alts are kept.

Everything is a dry run unless `--apply` is passed. Each run writes to `runs/<timestamp>-<dryrun|apply>/`:
`report.csv`, `summary.txt`, `redirects.json` (old→new slugs + rules ready for the site's `firebase.json`),
`text_references.csv`, `orphans.csv`, `log.txt`; with `--apply` also `backup.json` and `rollback.json` (written before any write).

```bash
.venv/bin/python -m unittest discover -s tests -t .              # rule tests (Python output == TypeScript output)
./tests/generate_ts_fixtures.sh                                   # regenerate TS fixtures after changing the backoffice rules

.venv/bin/python normalize_data.py normalize                      # full dry run
.venv/bin/python normalize_data.py normalize --only artworks/<id> # one document (artworks include their gallery)
.venv/bin/python normalize_data.py normalize --apply              # apply
.venv/bin/python normalize_data.py rollback runs/<run>/rollback.json [--apply]
# only after the site has been republished:
.venv/bin/python normalize_data.py cleanup-old-files --renamed runs/<run>/rollback.json [--apply]  # (a) old renamed files
.venv/bin/python normalize_data.py cleanup-old-files --orphans [--apply]                          # (b) unreferenced folders
```

The script is idempotent (a second run on normalized data plans no changes) and isolates errors per document.
It needs `SERVICE_ACCOUNT_PATH` and `PROJECT_ID` in `.env` (optional `STORAGE_BUCKET`, default `<PROJECT_ID>.firebasestorage.app`).

#### History and current state (valecreative-prod)

| Date | Run | What happened |
|---|---|---|
| 2026-09-27 | `runs/20260927-213756-apply` | Test on one document (`artworks/0TJGid1E1OrsGeKdezGL`, "Nonna") |
| 2026-09-27 | `runs/20260927-215132-apply` | Full apply: 205 files renamed, Cache-Control on 212 objects, 206 alt texts, 10 slugs (redirects added to the site's `firebase.json`) |
| 2026-09-27 | `runs/cleanup-20260927-230940-apply.log`, `…-231247-apply.log` | Cleanup (a): 206 old renamed files deleted, after the site was republished and verified |
| 2026-09-27 | `runs/cleanup-20260927-231611-apply.log` | Cleanup (b): 23 orphan folders deleted (122.7 MB, incl. 5 unreferenced TIFFs) |

After the cleanup the bucket holds 212 objects (195.6 MB), all referenced by a document, and a full `normalize` dry run plans 0 changes.
The `rollback` command can no longer be used for these runs: the old files it would point back to have been deleted.

**Backup** — `backup-storage-20260927/` (git-ignored) is a full copy of the bucket taken *before* the normalization
(235 original files, 319 MB, same paths as in the bucket). It is the only remaining copy of the original file names,
of the 23 deleted orphans and of the 5 TIFFs (checked: same size and MD5 as the deleted objects). Keep a copy outside
this repository (external disk / cloud) and do not delete it. The `runs/` folders (reports, `backup.json`, rollback
manifests, cleanup logs) are git-ignored too.

**Recommended routine** — run `normalize` in dry run from time to time (e.g. monthly or after a big upload session).
If it plans changes: `--apply`, then "Pubblica" in the backoffice, add any slug redirects from `redirects.json` to the
site's `firebase.json`, and only after the site is republished run `cleanup-old-files --renamed` / `--orphans`
(dry run first). If the backoffice rules in `slugify.ts` / `imageFileName.ts` change, regenerate the fixtures with
`./tests/generate_ts_fixtures.sh` and run the tests before using the script.
