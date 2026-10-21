"""Weather presets from the supplied weather.py, applied via AirSim MessagePack RPC.

Uses the same RPC calls and WeatherParameter enum as the official AirSim SDK,
without requiring its legacy Tornado / NumPy dependency stack.
"""

from contextlib import contextmanager

import socket
import threading
import time

import msgpack


WEATHER_PRESETS = {
    "clear": dict(rain=0, roadwetness=0, snow=0, road_snow=0, maple_leaf=0, road_leaf=0, dust=0, fog=0),
    "rain": dict(rain=0.84, roadwetness=0.95, snow=0, road_snow=0, maple_leaf=0, road_leaf=0, dust=0, fog=0.12),
    "fog": dict(rain=0, roadwetness=0.05, snow=0, road_snow=0, maple_leaf=0, road_leaf=0, dust=0, fog=0.36),
    "snow": dict(rain=0, roadwetness=0, snow=0.58, road_snow=0.72, maple_leaf=0, road_leaf=0, dust=0, fog=0.08),
}
PARAMETER_IDS = dict(rain=0, roadwetness=1, snow=2, road_snow=3, maple_leaf=4, road_leaf=5, dust=6, fog=7)


class WeatherUnavailable(Exception):
    pass


class AirSimWeatherService:
    def __init__(self, host, port=41451, timeout=5):
        self.host, self.port, self.timeout = host, port, timeout
        self.lock = threading.Lock()

    @contextmanager
    def _rpc(self):
        """One bounded RPC connection, shared by read-only probes and presets."""
        deadline = time.monotonic() + self.timeout
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as connection:
                unpacker = msgpack.Unpacker(raw=False, max_buffer_size=1024 * 1024)
                request_id = 0

                def call(method, *args):
                    nonlocal request_id
                    request_id += 1
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    connection.settimeout(remaining)
                    connection.sendall(msgpack.packb([0, request_id, method, list(args)], use_bin_type=True))
                    while True:
                        for response in unpacker:
                            if not isinstance(response, list) or len(response) != 4 or response[0] != 1 or response[1] != request_id:
                                raise WeatherUnavailable("AirSim RPC 返回了无效响应")
                            if response[2] is not None:
                                raise WeatherUnavailable("AirSim 拒绝 RPC 调用，请检查模拟器日志与接口支持")
                            return response[3]
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError()
                        connection.settimeout(remaining)
                        data = connection.recv(65536)
                        if not data:
                            raise WeatherUnavailable("AirSim RPC 连接已断开")
                        unpacker.feed(data)

                yield call
        except (OSError, ValueError, msgpack.UnpackException) as error:
            raise WeatherUnavailable("AirSim RPC 连接失败，请检查模拟器及 RPC 地址、端口；若天气部分生效，请重试完整预设") from error

    def probe(self):
        """Verify RPC availability without changing the simulation."""
        with self._rpc() as call:
            if call("ping") is not True:
                raise WeatherUnavailable("AirSim 未确认连接")

    def apply(self, weather):
        if not self.lock.acquire(blocking=False):
            raise WeatherBusy("正在切换天气，请稍后重试")
        try:
            with self._rpc() as call:
                if call("ping") is not True:
                    raise WeatherUnavailable("AirSim 未确认连接")
                call("simEnableWeather", True)
                for name, value in WEATHER_PRESETS[weather].items():
                    call("simSetWeatherParameter", PARAMETER_IDS[name], float(value))
        finally:
            self.lock.release()


class WeatherBusy(Exception):
    pass
