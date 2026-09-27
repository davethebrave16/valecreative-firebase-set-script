#!/usr/bin/env python3
"""Normalize EXISTING Firestore/Storage data to the rules the backoffice applies to new uploads.

Rules (ported 1:1 from valecreative-admin-backoffice, see normalizer/):
  - slugs: toSlug / SLUG_PATTERN / CONTENT_SLUG_PATTERN, unique per collection (categories: report only)
  - file names: cover {slug}.{ext}, gallery {artworkSlug}-{n}.{ext}, same uuid folder
  - Cache-Control: public, max-age=31536000, immutable on every referenced object
  - alt: record title when the current alt is empty or looks like a file name

Commands (everything is a DRY RUN unless --apply is passed):
  normalize          [--apply] [--only <collection>/<docId>]
  rollback           <runs/.../rollback.json> [--apply]
  cleanup-old-files  (--renamed <runs/.../rollback.json> | --orphans) [--apply]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import firebase_admin
from dotenv import load_dotenv
from firebase_admin import credentials, firestore, storage

from normalizer.alt_rules import looks_like_filename
from normalizer.file_names import (
    build_download_url, cover_file_name, folder_of, gallery_file_name, name_of, path_from_url, stem_of,
)
from normalizer.slug_rules import slug_pattern_for, to_slug

ROOT = Path(__file__).resolve().parent
CACHE_CONTROL = 'public, max-age=31536000, immutable'
SLUG_COLLECTIONS = ['artworks', 'series', 'contents', 'techniques', 'categories']
READ_ONLY_SLUGS = {'categories'}  # CategoryEdit keeps the slug disabled: report, never modify
TITLE_FIELD = {'artworks': 'title', 'series': 'name', 'contents': 'title', 'techniques': 'name', 'categories': 'name'}
IMAGE_FIELD = {'artworks': 'coverImage', 'series': 'coverImage', 'contents': 'image'}
URL_FIELDS = ('original', 'thumb', 'medium')
# Public site paths per collection (valecreative-site routes), for redirects and text-reference search.
SITE_PATHS = {'artworks': 'works', 'series': 'series', 'techniques': 'techniques', 'categories': 'works/category'}
LOCALES = ('it', 'en')
FAR_FUTURE = dt.datetime.max.replace(tzinfo=dt.timezone.utc)


# ─── Plan model ────────────────────────────────────────────────────────────────

@dataclass
class ImagePlan:
    doc_path: str                 # Firestore document that owns the image
    prefix: str                   # 'coverImage.' / 'image.' / '' (gallery doc)
    old_path: str
    new_path: str
    old_alt: str
    new_alt: str
    urls: dict = field(default_factory=dict)       # url field -> current value (original/thumb/medium)
    cache_control: str = ''       # 'set' | 'already' | 'missing-object'

    @property
    def rename(self) -> bool:
        return self.old_path != self.new_path

    @property
    def alt_changes(self) -> bool:
        return self.old_alt != self.new_alt


@dataclass
class DocPlan:
    collection: str
    doc_id: str
    doc_path: str
    title: str
    old_slug: str | None = None
    new_slug: str | None = None
    slug_reason: str = ''         # non-conforming | duplicate | empty | report-only
    images: list = field(default_factory=list)
    text_refs: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def slug_changes(self) -> bool:
        return self.new_slug is not None and self.new_slug != self.old_slug


# ─── Firebase ──────────────────────────────────────────────────────────────────

def init_firebase() -> tuple:
    load_dotenv(ROOT / '.env')
    sa_path = os.getenv('SERVICE_ACCOUNT_PATH')
    project_id = os.getenv('PROJECT_ID')
    if not sa_path or not project_id:
        sys.exit('SERVICE_ACCOUNT_PATH and PROJECT_ID must be set in .env')
    sa_path = str((ROOT / sa_path).resolve()) if not os.path.isabs(sa_path) else sa_path
    bucket_name = os.getenv('STORAGE_BUCKET') or f'{project_id}.firebasestorage.app'
    firebase_admin.initialize_app(credentials.Certificate(sa_path), {'projectId': project_id, 'storageBucket': bucket_name})
    return firestore.client(), storage.bucket()


def created_at(data: dict) -> dt.datetime:
    value = data.get('createdAt')
    return value if isinstance(value, dt.datetime) else FAR_FUTURE


def load_state(db) -> dict:
    docs = {col: list(db.collection(col).stream()) for col in SLUG_COLLECTIONS}
    gallery = {d.id: list(d.reference.collection('gallery').stream()) for d in docs['artworks']}
    return {'docs': docs, 'gallery': gallery}


def iter_image_refs(state: dict):
    """(doc_path, prefix, image_dict) for every image stored in Firestore."""
    for col, fname in IMAGE_FIELD.items():
        for d in state['docs'][col]:
            img = d.to_dict().get(fname)
            if isinstance(img, dict):
                yield d.reference.path, f'{fname}.', img
    for docs in state['gallery'].values():
        for g in docs:
            yield g.reference.path, '', g.to_dict()


def referenced_paths(state: dict) -> dict:
    """Storage path -> list of 'docPath:field' that reference it (original/thumb/medium)."""
    refs: dict = {}
    for doc_path, prefix, img in iter_image_refs(state):
        for f in URL_FIELDS:
            p = path_from_url(img.get(f))
            if p:
                refs.setdefault(p, []).append(f'{doc_path}:{prefix}{f}')
    return refs


# ─── Planning ──────────────────────────────────────────────────────────────────

def plan_slugs(collection: str, docs: list) -> dict:
    """doc id -> (old_slug, new_slug, reason). Deterministic: processed oldest first."""
    pattern = slug_pattern_for(collection)
    title_field = TITLE_FIELD[collection]
    ordered = sorted(docs, key=lambda d: (created_at(d.to_dict()), d.id))
    info = {d.id: (str(d.to_dict().get('slug') or ''), str(d.to_dict().get(title_field) or '')) for d in ordered}

    if collection in READ_ONLY_SLUGS:
        seen: dict = {}
        out = {}
        for d in ordered:
            slug, _ = info[d.id]
            bad = not pattern.match(slug)
            dup = slug in seen
            seen.setdefault(slug, d.id)
            if bad or dup:
                out[d.id] = (slug, slug, 'report-only: ' + ('non-conforming' if bad else f'duplicate of {seen[slug]}'))
        return out

    # Conforming slugs used by more than one doc: one keeper per slug.
    groups: dict = {}
    for d in ordered:
        slug, _ = info[d.id]
        if pattern.match(slug):
            groups.setdefault(slug, []).append(d.id)
    keepers = {}
    for slug, ids in groups.items():
        matching = [i for i in ids if to_slug(info[i][1]) == slug]
        keepers[slug] = (matching or ids)[0]  # title-consistent first, then oldest

    taken = set(keepers)
    out = {}
    for d in ordered:
        slug, title = info[d.id]
        if pattern.match(slug) and keepers.get(slug) == d.id:
            continue
        if not slug:
            reason, candidate = 'empty', to_slug(title)
        elif not pattern.match(slug):
            reason, candidate = 'non-conforming', to_slug(slug) or to_slug(title)
        else:
            reason = 'duplicate'
            own = to_slug(title)
            candidate = own if own and own != slug else slug
        candidate = candidate or 'untitled'
        base, n = candidate, 2
        while candidate in taken:
            candidate, n = f'{base}-{n}', n + 1
        taken.add(candidate)
        out[d.id] = (slug, candidate, reason)
    return out


def gallery_sort_key(g) -> tuple:
    data = g.to_dict()
    pos = data.get('imagePosition')
    return (pos if isinstance(pos, (int, float)) else float('inf'), str(data.get('uploadedAt') or ''), g.id)


def known_file_stems(blobs: dict) -> frozenset:
    return frozenset(filter(None, (to_slug(stem_of(name_of(p))) for p in blobs)))


def plan_image(doc_path: str, prefix: str, img: dict, target_name: str, alt_title: str,
               slug_for_alt: str | None, blobs: dict, stems: frozenset = frozenset()) -> ImagePlan:
    old_path = path_from_url(img.get('original'))
    if not old_path:
        raise ValueError(f'{prefix}original is empty or not a Storage URL')
    new_path = f'{folder_of(old_path)}/{target_name}'
    old_alt = str(img.get('alt') or '')
    new_alt = old_alt
    if alt_title.strip() and old_alt.strip() != alt_title.strip() \
            and looks_like_filename(old_alt, name_of(old_path), slug_for_alt, stems):
        new_alt = alt_title.strip()
    blob = blobs.get(old_path)
    if blob is None:
        cache = 'missing-object'
    else:
        cache = 'already' if blob.cache_control == CACHE_CONTROL else 'set'
    urls = {f: img[f] for f in URL_FIELDS if img.get(f) and path_from_url(img[f]) == old_path}
    return ImagePlan(doc_path, prefix, old_path, new_path, old_alt, new_alt, urls, cache)


def build_plan(state: dict, blobs: dict) -> list:
    plans = []
    stems = known_file_stems(blobs)
    slug_plans = {col: plan_slugs(col, state['docs'][col]) for col in SLUG_COLLECTIONS}
    for col in SLUG_COLLECTIONS:
        for d in state['docs'][col]:
            data = d.to_dict()
            title = str(data.get(TITLE_FIELD[col]) or '')
            p = DocPlan(col, d.id, d.reference.path, title, old_slug=str(data.get('slug') or ''))
            if d.id in slug_plans[col]:
                _, p.new_slug, p.slug_reason = slug_plans[col][d.id]
                if p.slug_reason.startswith('report-only'):
                    p.new_slug = None
            final_slug = p.new_slug if p.slug_changes else p.old_slug
            try:
                fname = IMAGE_FIELD.get(col)
                img = data.get(fname) if fname else None
                if isinstance(img, dict) and img.get('original'):
                    target = cover_file_name(final_slug, name_of(path_from_url(img['original']) or ''))
                    p.images.append(plan_image(d.reference.path, f'{fname}.', img, target, title, p.old_slug, blobs, stems))
            except Exception as e:  # noqa: BLE001 - per-document isolation
                p.errors.append(f'cover: {e}')
            plans.append(p)

            if col == 'artworks':
                for n, g in enumerate(sorted(state['gallery'].get(d.id, []), key=gallery_sort_key), start=1):
                    gp = DocPlan('artworks/gallery', f'{d.id}/gallery/{g.id}', g.reference.path, title)
                    try:
                        gdata = g.to_dict()
                        target = gallery_file_name(final_slug, n, name_of(path_from_url(gdata.get('original')) or ''))
                        gp.images.append(plan_image(g.reference.path, '', gdata, target, title, p.old_slug, blobs, stems))
                    except Exception as e:  # noqa: BLE001
                        gp.errors.append(f'gallery: {e}')
                    plans.append(gp)
    return plans


def find_text_references(db, plans: list) -> list:
    """Site paths that use a slug about to change, in any string field of any document. Report only."""
    changed = [(p, SITE_PATHS[p.collection]) for p in plans
               if p.slug_changes and p.slug_reason != 'duplicate' and p.collection in SITE_PATHS]
    if not changed:
        return []
    patterns = []
    for p, site_path in changed:
        variants = {re.escape(p.old_slug), re.escape(quote(p.old_slug))}
        rx = re.compile(rf'/(?:it|en)/{re.escape(site_path)}/(?:{"|".join(variants)})(?=$|[/?#"\'\s<)])')
        patterns.append((p, rx))

    found = []

    def scan(doc_path: str, value, field_path: str):
        if isinstance(value, str):
            for p, rx in patterns:
                m = rx.search(value)
                if m:
                    found.append({'target': f'{p.collection}/{p.doc_id}', 'old_slug': p.old_slug, 'new_slug': p.new_slug,
                                  'doc': doc_path, 'field': field_path, 'match': m.group(0)})
        elif isinstance(value, dict):
            for k, v in value.items():
                scan(doc_path, v, f'{field_path}.{k}' if field_path else k)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                scan(doc_path, v, f'{field_path}[{i}]')

    for coll in db.collections():
        for d in coll.stream():
            scan(d.reference.path, d.to_dict(), '')
            for sub in d.reference.collections():
                for sd in sub.stream():
                    scan(sd.reference.path, sd.to_dict(), '')
    for ref in found:
        for p, _ in changed:
            if ref['target'] == f'{p.collection}/{p.doc_id}':
                p.text_refs.append(f"{ref['doc']}:{ref['field']}")
    return found


def find_orphans(blobs: dict, refs: dict) -> list:
    referenced_folders = {folder_of(p) for p in refs}
    folders: dict = {}
    for path, blob in blobs.items():
        folders.setdefault(folder_of(path), []).append(blob)
    out = []
    for folder, items in sorted(folders.items()):
        if folder in referenced_folders:
            continue
        out.append({
            'folder': folder,
            'files': [name_of(b.name) for b in items],
            'bytes': sum(b.size or 0 for b in items),
            'has_tiff': any(b.name.lower().endswith(('.tif', '.tiff')) for b in items),
        })
    return out


# ─── Output ────────────────────────────────────────────────────────────────────

def new_run_dir(kind: str) -> Path:
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    d = ROOT / 'runs' / f'{stamp}-{kind}'
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_report(run_dir: Path, plans: list) -> None:
    with open(run_dir / 'report.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['collection', 'id', 'title', 'slug_old', 'slug_new', 'slug_reason', 'file_old', 'file_new',
                    'alt_old', 'alt_new', 'cache_control', 'text_references', 'errors'])
        for p in plans:
            rows = p.images or [None]
            for img in rows:
                if not (p.slug_changes or p.slug_reason or p.errors or (img and (img.rename or img.alt_changes or img.cache_control))):
                    continue
                w.writerow([
                    p.collection, p.doc_id, p.title,
                    p.old_slug if p.slug_changes or p.slug_reason else '', p.new_slug if p.slug_changes else '', p.slug_reason,
                    img.old_path if img else '', img.new_path if img and img.rename else '',
                    img.old_alt if img and img.alt_changes else '', img.new_alt if img and img.alt_changes else '',
                    img.cache_control if img else '', ' | '.join(p.text_refs), ' | '.join(p.errors),
                ])


def write_redirects(run_dir: Path, plans: list) -> dict:
    by_col: dict = {}
    firebase = []
    for p in plans:
        if not p.slug_changes or p.slug_reason == 'duplicate' or p.collection not in SITE_PATHS:
            continue
        by_col.setdefault(p.collection, []).append({'id': p.doc_id, 'old': p.old_slug, 'new': p.new_slug})
        site_path = SITE_PATHS[p.collection]
        for loc in LOCALES:
            for src in dict.fromkeys([p.old_slug, quote(p.old_slug)]):  # raw + URL-encoded (e.g. spaces)
                firebase.append({'source': f'/{loc}/{site_path}/{src}', 'destination': f'/{loc}/{site_path}/{p.new_slug}', 'type': 301})
    out = {'by_collection': by_col, 'firebase_json_redirects': firebase}
    (run_dir / 'redirects.json').write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n')
    return out


def write_csv(path: Path, rows: list, fields: list) -> None:
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (' | '.join(v) if isinstance(v, list) else v) for k, v in r.items() if k in fields})


def summarize(plans: list, text_refs: list, orphans: list, blobs: dict, refs: dict) -> str:
    lines = []
    slug_changes = [p for p in plans if p.slug_changes]
    lines.append(f'Slug changes: {len(slug_changes)}')
    for p in slug_changes:
        lines.append(f'  {p.collection}/{p.doc_id}  "{p.old_slug}" -> "{p.new_slug}"  ({p.slug_reason}; "{p.title}")')
    for p in plans:
        if p.slug_reason.startswith('report-only'):
            lines.append(f'  [not modified] {p.collection}/{p.doc_id} "{p.old_slug}" {p.slug_reason}')
    images = [i for p in plans for i in p.images]
    renames = [i for i in images if i.rename]
    lines.append(f'Images referenced: {len(images)}  | renamed: {len(renames)}  | Cache-Control only: {sum(1 for i in images if not i.rename)}')
    lines.append(f'Cache-Control to set: {sum(1 for i in images if i.cache_control == "set")}  | already set: {sum(1 for i in images if i.cache_control == "already")}'
                 f'  | object missing: {sum(1 for i in images if i.cache_control == "missing-object")}')
    lines.append(f'Alt changes: {sum(1 for i in images if i.alt_changes)}')
    lines.append(f'Text references to changing slugs: {len(text_refs)}')
    for r in text_refs:
        lines.append(f"  {r['doc']} field {r['field']}: {r['match']} (-> {r['new_slug']})")
    orphan_bytes = sum(o['bytes'] for o in orphans)
    lines.append(f'Orphan folders: {len(orphans)}  ({orphan_bytes / 1e6:.1f} MB recoverable)')
    for o in orphans:
        lines.append(f"  {o['folder']}/  {', '.join(o['files'])}  {o['bytes'] / 1e6:.1f} MB{'  [TIFF]' if o['has_tiff'] else ''}")
    tiffs = [p for p in blobs if p.lower().endswith(('.tif', '.tiff'))]
    lines.append(f'TIFF objects: {len(tiffs)}  (referenced: {sum(1 for t in tiffs if t in refs)})')
    errors = [(p, e) for p in plans for e in p.errors]
    lines.append(f'Errors: {len(errors)}')
    for p, e in errors:
        lines.append(f'  {p.collection}/{p.doc_id}: {e}')
    return '\n'.join(lines)


# ─── Apply ─────────────────────────────────────────────────────────────────────

def serializable(value):
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serializable(v) for v in value]
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if hasattr(value, 'path') and hasattr(value, 'id'):  # DocumentReference
        return {'__ref__': value.path}
    return value


def copy_with_new_name(bucket, blobs: dict, img: ImagePlan) -> str:
    """Copy the object to its new name (same folder), with Cache-Control and a fresh download token.
    Resumable: if the target already exists (partial previous run), reuse it. Returns the new download URL."""
    target = bucket.get_blob(img.new_path)
    if target is None:
        source = blobs[img.old_path]
        target = bucket.copy_blob(source, bucket, img.new_path)
        target.content_type = source.content_type
    token = ((target.metadata or {}).get('firebaseStorageDownloadTokens') or '').split(',')[0]
    if not token or target.cache_control != CACHE_CONTROL:
        token = token or str(uuid.uuid4())
        target.cache_control = CACHE_CONTROL
        target.metadata = {**(target.metadata or {}), 'firebaseStorageDownloadTokens': token}
        target.patch()
    return build_download_url(bucket.name, img.new_path, token)


def apply_plans(db, bucket, blobs: dict, plans: list, run_dir: Path, log) -> None:
    involved = [p for p in plans if not p.errors and (p.slug_changes or any(i.rename or i.alt_changes for i in p.images))]

    # 1. Backup + rollback manifest BEFORE any write.
    backup = {p.doc_path: serializable(db.document(p.doc_path).get().to_dict()) for p in involved}
    (run_dir / 'backup.json').write_text(json.dumps(backup, indent=2, ensure_ascii=False, default=str) + '\n')
    rollback = []
    for p in involved:
        restore = {}
        if p.slug_changes:
            restore['slug'] = p.old_slug
        storage_moves = []
        for i in p.images:
            if i.rename:
                for f, url in i.urls.items():
                    restore[f'{i.prefix}{f}'] = url
                storage_moves.append({'old_path': i.old_path, 'new_path': i.new_path})
            if i.alt_changes:
                restore[f'{i.prefix}alt'] = i.old_alt
        rollback.append({'doc_path': p.doc_path, 'restore': restore, 'storage': storage_moves})
    (run_dir / 'rollback.json').write_text(json.dumps(rollback, indent=2, ensure_ascii=False) + '\n')
    log(f'Backup ({len(backup)} docs) and rollback manifest written to {run_dir}')

    # 2. Per document: copy renamed files, then one Firestore update.
    for p in involved:
        try:
            updates = {}
            if p.slug_changes:
                updates['slug'] = p.new_slug
            for i in p.images:
                if i.rename:
                    new_url = copy_with_new_name(bucket, blobs, i)
                    for f in i.urls:
                        updates[f'{i.prefix}{f}'] = new_url
                if i.alt_changes:
                    updates[f'{i.prefix}alt'] = i.new_alt
            if updates:
                db.document(p.doc_path).update(updates)
            log(f'OK   {p.doc_path} {sorted(updates)}')
        except Exception as e:  # noqa: BLE001 - one failure must not stop the others
            p.errors.append(f'apply: {e}')
            log(f'FAIL {p.doc_path}: {e}\n{traceback.format_exc()}')

    # 3. Cache-Control on every referenced object (incl. old names still used by the published site).
    for i in (i for p in plans for i in p.images):
        blob = blobs.get(i.old_path)
        if blob is not None and blob.cache_control != CACHE_CONTROL:
            try:
                blob.cache_control = CACHE_CONTROL
                blob.patch()
            except Exception as e:  # noqa: BLE001
                log(f'FAIL cache-control {i.old_path}: {e}')


# ─── Commands ──────────────────────────────────────────────────────────────────

def cmd_normalize(args) -> None:
    db, bucket = init_firebase()
    run_dir = new_run_dir('apply' if args.apply else 'dryrun')
    log_file = open(run_dir / 'log.txt', 'w')

    def log(msg: str) -> None:
        print(msg)
        log_file.write(msg + '\n')

    log(f"{'APPLY' if args.apply else 'DRY RUN (nothing is written)'} — bucket {bucket.name}")
    state = load_state(db)
    blobs = {b.name: b for b in bucket.list_blobs()}
    plans = build_plan(state, blobs)
    refs = referenced_paths(state)
    text_refs = find_text_references(db, plans)
    orphans = find_orphans(blobs, refs)

    if args.only:
        col, _, doc_id = args.only.partition('/')
        plans = [p for p in plans if (p.collection == col and p.doc_id == doc_id)
                 or (col == 'artworks' and p.collection == 'artworks/gallery' and p.doc_id.startswith(f'{doc_id}/'))]
        if not plans:
            sys.exit(f'--only {args.only}: document not found')

    write_report(run_dir, plans)
    write_redirects(run_dir, plans)
    write_csv(run_dir / 'text_references.csv', text_refs, ['target', 'old_slug', 'new_slug', 'doc', 'field', 'match'])
    write_csv(run_dir / 'orphans.csv', orphans, ['folder', 'files', 'bytes', 'has_tiff'])

    if args.apply:
        apply_plans(db, bucket, blobs, plans, run_dir, log)
        write_report(run_dir, plans)  # includes apply errors

    summary = summarize(plans, text_refs, orphans if not args.only else [], blobs, refs)
    (run_dir / 'summary.txt').write_text(summary + '\n')
    log(summary)
    log(f'\nOutput: {run_dir}')
    log_file.close()


def cmd_rollback(args) -> None:
    db, bucket = init_firebase()
    manifest = json.loads(Path(args.manifest).read_text())
    mode = 'APPLY' if args.apply else 'DRY RUN'
    print(f'Rollback {mode}: {len(manifest)} documents from {args.manifest}')
    for entry in manifest:
        missing = [m['old_path'] for m in entry['storage'] if bucket.get_blob(m['old_path']) is None]
        if missing:
            print(f"SKIP {entry['doc_path']}: old files already deleted {missing}")
            continue
        print(f"{'RESTORE' if args.apply else 'would restore'} {entry['doc_path']}: {entry['restore']}")
        if args.apply:
            try:
                db.document(entry['doc_path']).update(entry['restore'])
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {entry['doc_path']}: {e}")
    if not args.apply:
        print('\nNothing written. Re-run with --apply to restore.')


def cmd_cleanup(args) -> None:
    db, bucket = init_firebase()
    state = load_state(db)
    refs = referenced_paths(state)  # re-read NOW: only delete what nothing references
    blobs = {b.name: b for b in bucket.list_blobs()}
    log_path = ROOT / 'runs' / f"cleanup-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{'apply' if args.apply else 'dryrun'}.log"
    log_path.parent.mkdir(exist_ok=True)
    to_delete = []
    with open(log_path, 'w') as log:
        def out(msg: str) -> None:
            print(msg)
            log.write(msg + '\n')

        if args.renamed:
            for entry in json.loads(Path(args.renamed).read_text()):
                for m in entry['storage']:
                    old, new = m['old_path'], m['new_path']
                    if old not in blobs:
                        out(f'already gone   {old}')
                    elif new not in blobs:
                        out(f'KEEP (new file missing) {old}')
                    elif old in refs:
                        out(f'KEEP (still referenced by {refs[old]}) {old}')
                    else:
                        to_delete.append(old)
        else:
            referenced_folders = {folder_of(p) for p in refs}
            for path in blobs:
                if folder_of(path) not in referenced_folders:
                    to_delete.append(path)

        total = sum(blobs[p].size or 0 for p in to_delete)
        out(f"{'DELETE' if args.apply else 'would delete'} {len(to_delete)} objects, {total / 1e6:.1f} MB:")
        for path in to_delete:
            out(f'  {path}  {(blobs[path].size or 0) / 1e6:.1f} MB')
            if args.apply:
                try:
                    blobs[path].delete()
                except Exception as e:  # noqa: BLE001
                    out(f'  FAIL {path}: {e}')
        if not args.apply:
            out('\nNothing deleted. Re-run with --apply to delete.')
    print(f'Log: {log_path}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('normalize', help='plan (default) or apply the normalization')
    p.add_argument('--apply', action='store_true', help='write changes (default: dry run)')
    p.add_argument('--only', metavar='COLLECTION/DOC_ID', help='limit changes to one document (artworks include their gallery)')
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser('rollback', help='restore slug/URL/alt values from a rollback.json')
    p.add_argument('manifest')
    p.add_argument('--apply', action='store_true')
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser('cleanup-old-files', help='delete old renamed files or orphan folders (after the site is republished)')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--renamed', metavar='ROLLBACK_JSON', help='(a) old files replaced by a renamed copy')
    g.add_argument('--orphans', action='store_true', help='(b) folders not referenced by any document (incl. TIFFs)')
    p.add_argument('--apply', action='store_true')
    p.set_defaults(func=cmd_cleanup)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
