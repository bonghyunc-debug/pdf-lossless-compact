from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pikepdf
from pikepdf import Array, Dictionary, Name

from compress import PdfSkipped, compress
from tests.samples import make_sample, render_snapshot


class CompressionTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / "한글 견본 (1).pdf"

    def test_render_search_and_embedded_data(self):
        for precompressed in (False, True):
            with self.subTest(precompressed=precompressed):
                make_sample(self.source, precompressed=precompressed)
                original = self.source.read_bytes()
                result = compress(self.source)
                self.assertLess(result.candidate_size, len(original))
                self.assertEqual(self.source.read_bytes(), original)
                before, after = render_snapshot(self.source), render_snapshot(result.output)
                self.assertEqual(before, after)
                self.assertIn("한글", after[0][2])
                self.assertIn("Searchable scan 002", after[1][2])
                with pikepdf.Pdf.open(self.source) as old, pikepdf.Pdf.open(result.output) as new:
                    self.assertEqual(old.pdf_version, new.pdf_version)
                    self.assertEqual(old.docinfo.Title, new.docinfo.Title)
                    self.assertEqual(old.pages[1].Resources.XObject.Scan.read_raw_bytes(),
                                     new.pages[1].Resources.XObject.Scan.read_raw_bytes())
                    old_font = old.pages[0].Resources.Font.K
                    new_font = new.pages[0].Resources.Font.K
                    for glyph in ("/Han", "/Geul"):
                        self.assertEqual(old_font.CharProcs[glyph].read_bytes(),
                                         new_font.CharProcs[glyph].read_bytes())
                    self.assertEqual(old_font.ToUnicode.read_bytes(), new_font.ToUnicode.read_bytes())
                    self.assertEqual(new.Root.AcroForm.Fields[0].V, "42")
                    self.assertEqual(new.Root.AcroForm.Fields[0], new.pages[0].Annots[1])
                    self.assertEqual(new.pages[0].Annots[0].Dest[0], new.pages[0].obj)
                    self.assertEqual(new.attachments["sample.txt"].get_file().read_bytes(),
                                     b"Local sample attachment.\n")
                    with new.open_outline() as outline:
                        self.assertEqual(outline.root[0].title, "Sample register")
                print(f"precompressed={precompressed}: {len(original)} -> {result.candidate_size} bytes; "
                      f"2 pages matched at 144 dpi; image sha256={sha256(after[1][1]).hexdigest()[:16]}")

    def test_older_pdf_keeps_version(self):
        make_sample(self.source, version="1.4")
        result = compress(self.source)
        with pikepdf.Pdf.open(result.output) as pdf:
            self.assertEqual(pdf.pdf_version, "1.4")

    def test_already_compact_keeps_original_only(self):
        make_sample(self.source)
        compact = compress(self.source).output
        before = {f.name: f.read_bytes() for f in self.folder.iterdir()}
        result = compress(compact)
        self.assertIsNone(result.output)
        self.assertGreaterEqual(result.candidate_size, result.original_size)
        self.assertEqual(before, {f.name: f.read_bytes() for f in self.folder.iterdir()})

    def test_collisions_and_simultaneous_calls(self):
        make_sample(self.source)
        existing = self.folder / "한글 견본 (1)_압축.pdf"
        existing.write_bytes(b"keep existing output")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compress, [self.source, self.source]))
        self.assertEqual({r.output.name for r in results},
                         {"한글 견본 (1)_압축 (2).pdf", "한글 견본 (1)_압축 (3).pdf"})
        self.assertEqual(existing.read_bytes(), b"keep existing output")
        self.assertEqual(list(self.folder.glob("*.tmp")), [])

    def assert_skipped(self, message):
        original = self.source.read_bytes()
        with self.assertRaisesRegex(PdfSkipped, message):
            compress(self.source)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(list(self.folder.iterdir()), [self.source])

    def test_password_and_owner_restricted_files(self):
        for password in ("example", ""):
            with self.subTest(password=password):
                with pikepdf.Pdf.new() as pdf:
                    pdf.add_blank_page()
                    pdf.save(self.source, encryption=pikepdf.Encryption(owner="owner", user=password))
                self.assert_skipped("보호")

    def test_signature_and_timestamp(self):
        for kind in (Name.Sig, Name.DocTimeStamp, None):
            with self.subTest(kind=kind):
                with pikepdf.Pdf.new() as pdf:
                    pdf.add_blank_page()
                    signature = Dictionary(Contents=b"test marker", ByteRange=Array([0, 12, 16, 8]))
                    if kind:
                        signature.Type = kind
                    # The untyped case tests a nested direct dictionary.
                    pdf.Root.TestSignatures = Array([Dictionary(Entry=signature)])
                    pdf.save(self.source)
                self.assert_skipped("전자서명")

    def test_empty_signature_field_stays_fillable(self):
        make_sample(self.source)
        file_with_field = self.folder / "빈 서명란.pdf"
        with pikepdf.Pdf.open(self.source) as pdf:
            pdf.Root.AcroForm.Fields.append(pdf.make_indirect(Dictionary(T="Signature", FT=Name.Sig)))
            pdf.save(file_with_field, compress_streams=False)
        result = compress(file_with_field)
        with pikepdf.Pdf.open(result.output) as pdf:
            self.assertEqual(pdf.Root.AcroForm.Fields[-1].FT, Name.Sig)
            self.assertNotIn("/V", pdf.Root.AcroForm.Fields[-1])

    def test_invalid_input(self):
        self.source.write_bytes(b"not a PDF")
        self.assert_skipped("읽을")

    def test_error_cleans_staged_data(self):
        make_sample(self.source)
        original = self.source.read_bytes()
        with patch("compress._publish", side_effect=PermissionError("folder is read-only")):
            with self.assertRaises(PermissionError):
                compress(self.source)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(list(self.folder.iterdir()), [self.source])
