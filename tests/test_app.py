import unittest

from app import create_app


class FlaskConsoleTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "XPRA_URL": "http://example.test:10000/"})
        self.client = self.app.test_client()

    def test_page_renders_assets_and_configuration(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('/static/styles.css', html)
        self.assertIn('/static/app.js', html)
        self.assertIn('data-xpra-url="http://example.test:10000/"', html)
        self.assertNotIn('{{', html)

    def test_static_resources(self):
        for path, mime in [("/static/app.js", "javascript"), ("/static/styles.css", "text/css")]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(mime, response.content_type)
                response.close()

    def test_health_and_configuration(self):
        self.assertEqual(self.client.get('/api/health').json, {"status": "ok", "backend": "flask"})
        configuration = self.client.get('/api/config').json
        self.assertEqual(configuration['xpra_url'], 'http://example.test:10000/')
        self.assertFalse(configuration['flight_control_available'])

    def test_source_files_are_not_exposed(self):
        for path in ['/app.py', '/requirements.txt', '/.env', '/static/../app.py', '/missing']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post('/api/config', json={}).status_code, 405)

    def test_invalid_or_credentialed_configuration_is_rejected(self):
        for url in ['ws://example.test/', 'javascript:alert(1)', 'http://user:pass@example.test/', 'http://example.test/?token=secret']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                create_app({"XPRA_URL": url})


if __name__ == '__main__':
    unittest.main()
