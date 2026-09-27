#!/bin/bash
# Regenerates tests/fixtures/ts_expected.json by running the backoffice TypeScript rules
# (src/utils/slugify.ts, src/utils/imageFileName.ts) on tests/fixtures/inputs.json.
# Requires the sibling repo ../valecreative-admin-backoffice with node_modules installed.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
BO="${BACKOFFICE_DIR:-$HERE/../../valecreative-admin-backoffice}"
TMP="$(mktemp -d)"
cat > "$TMP/gen.ts" <<TS
import { toSlug, SLUG_PATTERN, CONTENT_SLUG_PATTERN } from '$BO/src/utils/slugify'
import { fileExtension, coverFileName } from '$BO/src/utils/imageFileName'
import { readFileSync, writeFileSync } from 'fs'
const input = JSON.parse(readFileSync('$HERE/fixtures/inputs.json', 'utf8'))
// galleryFileName without collisions == '{toSlug(slug) || base}-{n}.{ext}'; exercised with an empty existing list.
import { galleryFileName } from '$BO/src/utils/imageFileName'
const out = {
	slugs: Object.fromEntries(input.slugs.map((s: string) => [s, toSlug(s)])),
	patterns: Object.fromEntries(input.patterns.map((s: string) => [s, { slug: SLUG_PATTERN.test(s), content: CONTENT_SLUG_PATTERN.test(s) }])),
	extensions: Object.fromEntries(input.files.map((f: string) => [f, fileExtension(f)])),
	cover: input.cover.map(([slug, file]: [string | null, string]) => coverFileName(slug ?? undefined, file)),
	gallery: input.gallery.map(([slug, n, file]: [string, number, string]) => galleryFileName(slug, n, file, [])),
}
writeFileSync('$HERE/fixtures/ts_expected.json', JSON.stringify(out, null, '\t') + '\n')
TS
(cd "$BO" && npx esbuild "$TMP/gen.ts" --bundle --platform=node --format=esm --log-level=error --outfile="$TMP/gen.mjs")
node "$TMP/gen.mjs"
rm -rf "$TMP"
echo "Wrote $HERE/fixtures/ts_expected.json"
