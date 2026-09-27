"""Run with: .venv/bin/python -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from normalizer.alt_rules import looks_like_filename
from normalizer.file_names import (
    build_download_url, cover_file_name, file_extension, gallery_file_name, path_from_url,
)
from normalizer.slug_rules import CONTENT_SLUG_PATTERN, SLUG_PATTERN, to_slug

FIXTURES = Path(__file__).parent / 'fixtures'
INPUTS = json.loads((FIXTURES / 'inputs.json').read_text())
TS = json.loads((FIXTURES / 'ts_expected.json').read_text())


class MatchesTypeScript(unittest.TestCase):
    """Python output must be identical to the backoffice TypeScript output for the same inputs."""

    def test_to_slug(self):
        for value in INPUTS['slugs']:
            with self.subTest(value=value):
                self.assertEqual(to_slug(value), TS['slugs'][value])

    def test_patterns(self):
        for value in INPUTS['patterns']:
            with self.subTest(value=value):
                self.assertEqual(bool(SLUG_PATTERN.match(value)), TS['patterns'][value]['slug'])
                self.assertEqual(bool(CONTENT_SLUG_PATTERN.match(value)), TS['patterns'][value]['content'])

    def test_extensions(self):
        for value in INPUTS['files']:
            with self.subTest(value=value):
                self.assertEqual(file_extension(value), TS['extensions'][value])

    def test_cover_file_name(self):
        for (slug, file_name), expected in zip(INPUTS['cover'], TS['cover']):
            with self.subTest(slug=slug, file=file_name):
                self.assertEqual(cover_file_name(slug, file_name), expected)

    def test_gallery_file_name(self):
        for (slug, n, file_name), expected in zip(INPUTS['gallery'], TS['gallery']):
            with self.subTest(slug=slug, n=n, file=file_name):
                self.assertEqual(gallery_file_name(slug, n, file_name), expected)


class RealExamples(unittest.TestCase):
    def test_slugs_from_production(self):
        self.assertEqual(to_slug('Coppia di fidanzati'), 'coppia-di-fidanzati')
        self.assertEqual(to_slug('diana---sottobosco'), 'diana-sottobosco')
        self.assertEqual(to_slug("La Compagnia dell'Anello"), 'la-compagnia-dellanello')
        self.assertEqual(to_slug('Città – d’arte!'), 'citta-darte')
        self.assertTrue(CONTENT_SLUG_PATTERN.match('homepage_hero'))
        self.assertFalse(SLUG_PATTERN.match('homepage_hero'))

    def test_nonna_example(self):
        url = ('https://firebasestorage.googleapis.com/v0/b/valecreative-prod.firebasestorage.app/o/'
            'artworks%2F8eea7e3b-aaaa%2F495658824_1234_n.jpg?alt=media&token=abc')
        path = path_from_url(url)
        self.assertEqual(path, 'artworks/8eea7e3b-aaaa/495658824_1234_n.jpg')
        self.assertEqual(cover_file_name('nonna', path.rsplit('/', 1)[1]), 'nonna.jpg')
        self.assertTrue(looks_like_filename('495658824_1234_n', '495658824_1234_n.jpg', 'nonna'))

    def test_download_url_round_trip(self):
        path = 'artworks/abc/gallery/uuid-1/padre-e-figlio-2.jpg'
        url = build_download_url('valecreative-prod.firebasestorage.app', path, 'tok')
        self.assertEqual(url, 'https://firebasestorage.googleapis.com/v0/b/valecreative-prod.firebasestorage.app'
            '/o/artworks%2Fabc%2Fgallery%2Fuuid-1%2Fpadre-e-figlio-2.jpg?alt=media&token=tok')
        self.assertEqual(path_from_url(url), path)
        self.assertEqual(path_from_url(build_download_url('b', 'artworks/u/Città d’arte (1).JPG', 't')), 'artworks/u/Città d’arte (1).JPG')


class AltHeuristic(unittest.TestCase):
    def test_file_name_like(self):
        cases = [
            ('', 'x.jpg'), (None, 'x.jpg'), ('IMG_0760', 'IMG_0760.JPG'), ('4-IMG_1261', 'other.jpg'),
            ('23-IMG_5853bs', 'other.jpg'), ('img_1068', 'other.jpg'), ('WhatsApp Image 2026-07-15 at 12 (4)', 'x.jpg'),
            ('pitture-jpg_10 (1)', 'pitture-jpg_10 (1).jpg'), ('100_6222', 'z.jpg'), ('photo.jpg', 'a.jpg'),
            ('hf', 'hf.jpg'), ('prova-citta-darte', 'prova-citta-darte.jpg'), ('prova-citta-darte', 'x.jpg'),
            ('DSC01234', 'x.jpg'), ('1012199_397475200359098_1305', 'x.jpg'),
        ]
        for alt, file_name in cases:
            with self.subTest(alt=alt):
                self.assertTrue(looks_like_filename(alt, file_name, 'prova-citta-darte'))

    def test_leftovers_from_other_files_and_snake_case(self):
        stems = frozenset({'generosita', 'libel-libellula', 'rosso'})
        self.assertTrue(looks_like_filename('generosità', 'autostrada.jpg', 'autostrada', stems))
        self.assertTrue(looks_like_filename('libel-libellula', 'spillo.jpg', 'spillo', stems))
        self.assertTrue(looks_like_filename('bicchere_personalizzato', '5082_n.jpg', 'bicchiere'))
        self.assertTrue(looks_like_filename('immagine_vetro_personalizzat', '5082_n.jpg', 'vetro'))
        self.assertFalse(looks_like_filename('Zeno', 'WhatsApp Image.jpg', 'il-piccolo-zeno', stems))

    def test_descriptive_kept(self):
        cases = [
            ('An ancient man', 'IMG_1.jpg', 'uomo-antico'), ('Nonna', 'n.jpg', 'nonna'),
            ('Ritratto di Rino Gattuso', 'x.jpg', 'ritratto'), ('Le radici del futuro', 'IMG_2.JPG', 'le-radici-del-futuro'),
            ('Dipinto a olio di una piazza', 'WhatsApp Image.jpg', 'piazza'),
        ]
        for alt, file_name, slug in cases:
            with self.subTest(alt=alt):
                self.assertFalse(looks_like_filename(alt, file_name, slug))


if __name__ == '__main__':
    unittest.main()
