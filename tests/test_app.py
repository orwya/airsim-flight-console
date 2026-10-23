import unittest

from app import create_app
from config import Settings


class FlaskConsoleTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(Settings(ip="192.0.2.10", http_port=5173, xpra_port=10000))
        self.client = self.app.test_client()

    def test_page_renders_assets_and_configuration(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("/static/styles.css", html)
        self.assertIn("/static/app.js", html)
        self.assertIn('data-xpra-url="http://192.0.2.10:10000/"', html)
        self.assertNotIn("{{", html)

    def test_compact_console_preserves_controls_without_promotional_header(self):
        html = self.client.get("/").get_data(as_text=True)
        for text in [
            "飞行，无限可能",
            "工作空间 01",
            "模拟环境",
            "YOUR SKY. YOUR COMMAND.",
            "让探索，从此刻起飞",
            "连接你的无人机，用自然语言开启下一次飞行。",
        ]:
            self.assertNotIn(text, html)
        self.assertIn('id="connect"', html)
        self.assertIn('id="focus-feed"', html)
        self.assertIn('id="xpra-frame" tabindex="0"', html)

    def test_static_resources(self):
        for path, mime in [
            ("/static/app.js", "javascript"),
            ("/static/styles.css", "text/css"),
        ]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(mime, response.content_type)
                response.close()

    def test_health_and_configuration(self):
        self.assertEqual(
            self.client.get("/api/health").json, {"status": "ok", "backend": "flask"}
        )
        configuration = self.client.get("/api/config").json
        self.assertEqual(configuration["xpra_url"], "http://192.0.2.10:10000/")
        self.assertEqual(configuration["console_url"], "http://192.0.2.10:5173/")
        self.assertFalse(configuration["flight_control_available"])

    def test_source_files_are_not_exposed(self):
        for path in [
            "/app.py",
            "/requirements.txt",
            "/.env",
            "/static/../app.py",
            "/missing",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post("/api/config", json={}).status_code, 405)

    def test_invalid_settings_are_rejected(self):
        for kwargs in (
            {"ip": "0.0.0.0"},
            {"ip": "example.test"},
            {"ip": "::"},
            {"xpra_port": 5173},
            {"rpc_port": 5173},
            {"display": ":100; kill"},
            {"poll_interval": float("nan")},
            {"http_port": 70000},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs)
        with self.assertRaises(ValueError):
            Settings(airsim_settings="/nonexistent/settings.json", rpc_port=0)


if __name__ == "__main__":
    unittest.main()
