import tempfile
import unittest
import zipfile
from pathlib import Path

from portable_export import (
    PortableExportError,
    create_publish_package,
    create_single_html,
    create_zip_package,
)
from Template import TEMPLATE_HTML, TEMPLATE_JS


class PortableExportTests(unittest.TestCase):
    def _play(self, root: Path) -> Path:
        play = root / "output/Play/ada/hello"
        gallery = play / "gallery/pages"
        gallery.mkdir(parents=True)
        (gallery / "cover.png").write_bytes(b"\x89PNG\r\n")
        (play / "styles.css").write_text(
            "body{background-image:url('gallery/pages/cover.png')}", encoding="utf-8"
        )
        (play / "script.js").write_text(
            "const cover='gallery/pages/cover.png';", encoding="utf-8"
        )
        (play / "index.html").write_text(
            '<html><head><link rel="stylesheet" href="styles.css"></head>'
            '<body><img src="gallery/pages/cover.png"><script src="script.js"></script></body></html>',
            encoding="utf-8",
        )
        return play

    def test_zip_publish_and_single_html(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            play = self._play(root)
            package = create_zip_package(play, root / "packages")
            publish = create_publish_package(play, root / "publish")
            single = create_single_html(play, root / "file")

            with zipfile.ZipFile(package) as archive:
                self.assertIn("hello/index.html", archive.namelist())
            with zipfile.ZipFile(publish) as archive:
                self.assertIn("index.html", archive.namelist())
                self.assertIn(".nojekyll", archive.namelist())
            html = single.read_text(encoding="utf-8")
            self.assertIn("data:image/png;base64,", html)
            self.assertNotIn('src="script.js"', html)
            self.assertNotIn('href="styles.css"', html)

    def test_single_html_size_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            play = self._play(root)
            (play / "gallery/pages/large.bin").write_bytes(b"x" * (2 * 1024 * 1024))
            with self.assertRaises(PortableExportError):
                create_single_html(play, root / "file", max_size_mb=1)

    def test_mobile_viewer_controls_are_present(self):
        self.assertIn('data-tap-navigation="true"', TEMPLATE_HTML)
        self.assertIn("Sound starts after you tap Begin.", TEMPLATE_HTML)
        self.assertIn("fullscreen-button", TEMPLATE_HTML)
        self.assertIn("share-button", TEMPLATE_HTML)
        self.assertIn("slideshowEl.addEventListener('pointerdown'", TEMPLATE_JS)
        self.assertIn("wall.classList.contains('is-open')", TEMPLATE_JS)
        self.assertIn("navigator.share", TEMPLATE_JS)
        self.assertIn("requestFullscreen", TEMPLATE_JS)
        self.assertIn("localStorage.setItem", TEMPLATE_JS)
        self.assertIn("url.startsWith('data:')", TEMPLATE_JS)


if __name__ == "__main__":
    unittest.main()
