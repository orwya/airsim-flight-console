import socket
import unittest
from unittest.mock import patch

import msgpack

from app import create_app
from weather_service import PARAMETER_IDS, WEATHER_PRESETS


class RpcConnection:
    """Emulates AirSim wire responses, including fragmented TCP packets."""

    def __init__(self, error_at=None, timeout=False):
        self.requests = []
        self.chunks = []
        self.error_at = error_at
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, value):
        pass

    def sendall(self, data):
        request = msgpack.unpackb(data, raw=False)
        self.requests.append(request)
        error = "not supported" if len(self.requests) == self.error_at else None
        result = True if request[2] == "ping" else None
        response = msgpack.packb([1, request[1], error, result], use_bin_type=True)
        self.chunks.extend([response[:2], response[2:]])

    def recv(self, size):
        if self.timeout:
            raise socket.timeout()
        return self.chunks.pop(0)


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def test_all_presets_send_every_parameter_including_resets(self):
        for weather, parameters in WEATHER_PRESETS.items():
            with self.subTest(weather=weather):
                connection = RpcConnection()
                with patch('weather_service.socket.create_connection', return_value=connection):
                    response = self.client.post('/api/weather', json={"weather": weather})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json, {"weather": weather, "status": "applied"})
                methods = [(request[2], request[3]) for request in connection.requests]
                self.assertEqual(methods[:2], [('ping', []), ('simEnableWeather', [True])])
                self.assertEqual(methods[2:], [
                    ('simSetWeatherParameter', [PARAMETER_IDS[key], float(value)])
                    for key, value in parameters.items()
                ])
                self.assertEqual(len(methods), 10)

    def test_unknown_weather_or_malformed_body_never_calls_airsim(self):
        with patch('weather_service.socket.create_connection') as connect:
            for payload in [{"weather": "cloudy"}, {"weather": []}, {}, [], {"weather": "__dict__"}]:
                self.assertEqual(self.client.post('/api/weather', json=payload).status_code, 400)
            self.assertEqual(self.client.post('/api/weather', data='{', content_type='application/json').status_code, 400)
            connect.assert_not_called()

    def test_rpc_failure_and_timeout_do_not_report_success(self):
        for connection in [RpcConnection(error_at=5), RpcConnection(timeout=True)]:
            with self.subTest(connection=connection), patch('weather_service.socket.create_connection', return_value=connection):
                response = self.client.post('/api/weather', json={"weather": "rain"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json['error'], 'airsim_unavailable')
            self.assertNotIn('status', response.json)
        self.assertFalse(self.app.extensions['weather_service'].lock.locked())

    def test_busy_request_does_not_interleave_parameters(self):
        service = self.app.extensions['weather_service']
        service.lock.acquire()
        try:
            response = self.client.post('/api/weather', json={"weather": "snow"})
        finally:
            service.lock.release()
        self.assertEqual(response.status_code, 409)

    def test_status_probe_only_pings_and_never_changes_weather(self):
        connection = RpcConnection()
        with patch('weather_service.socket.create_connection', return_value=connection):
            response = self.client.get('/api/airsim')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['connected'])
        self.assertEqual([(r[2], r[3]) for r in connection.requests], [('ping', [])])

    def test_status_reports_rpc_failure(self):
        for connection in [RpcConnection(timeout=True), RpcConnection(error_at=1)]:
            with self.subTest(connection=connection), patch('weather_service.socket.create_connection', return_value=connection):
                response = self.client.get('/api/airsim')
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json['connected'])

    def test_configuration_and_fixed_weather_ui(self):
        app = create_app({"TESTING": True, "AIRSIM_HOST": "example.test", "AIRSIM_PORT": 4242})
        service = app.extensions['weather_service']
        self.assertEqual((service.host, service.port), ('example.test', 4242))
        html = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('data-add="weather"', html)
        self.assertNotIn('data-remove="weather"', html)
        self.assertNotIn('data-add="scene"', html)
        self.assertIn('id="weather-status"', html)


if __name__ == '__main__':
    unittest.main()
