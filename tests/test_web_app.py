import json
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WebAppTests(unittest.TestCase):
    def test_manifest_and_offline_app_shell_exist(self):
        manifest = json.loads((ROOT / "manifest.webmanifest").read_text(encoding="utf-8"))
        html = (ROOT / "index.html").read_text(encoding="utf-8")

        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "./")
        self.assertTrue((ROOT / "sw.js").is_file())
        for icon in manifest["icons"]:
            icon_path = ROOT / icon["src"].removeprefix("./")
            self.assertTrue(icon_path.is_file())
            if icon["type"] == "image/png":
                with icon_path.open("rb") as image:
                    self.assertEqual(image.read(8), b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(struct.unpack(">I", image.read(4))[0], 13)
                    self.assertEqual(image.read(4), b"IHDR")
                    width, height = struct.unpack(">II", image.read(8))
                expected_width, expected_height = map(int, icon["sizes"].split("x"))
                self.assertEqual((width, height), (expected_width, expected_height))
        self.assertIn('id="install-app"', html)
        self.assertIn('id="fundamental-outlook"', html)
        self.assertIn('id="fundamental-drivers"', html)

    def test_tradingview_charts_only_show_dashboard_instruments(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn("OANDA%3AXAUUSD", html)
        self.assertIn("TVC%3ADXY", html)
        self.assertIn("NYMEX%3ACL1%21", html)
        self.assertNotIn("NASDAQ%3AAAPL", html)
        self.assertNotIn("AAPL", html)


if __name__ == "__main__":
    unittest.main()
