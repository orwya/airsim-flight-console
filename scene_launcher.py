"""Trusted scene templates. Only these IDs can be requested by the browser."""
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
import shlex

@dataclass(frozen=True)
class Scene:
    name: str
    executable: str

SCENES = MappingProxyType({
    name.lower(): Scene(name, f"/data/airsim/{name}/LinuxNoEditor/{name}/Binaries/Linux/{name}")
    for name in ("AirSimNH", "AbandonedPark", "MSBuild2018")
})

# Templates are administrator-controlled argv, never browser-provided shell text.
def get_commands(scene_key, config, runtime):
    scene = SCENES[scene_key]
    child = [scene.executable, scene.name,
             f"-settings={runtime / 'settings.json'}",
             f"-userdir={runtime / 'home'}",
             f"-abslog={runtime / 'logs' / (scene_key + '.log')}",
             "-windowed", "-ResX=1280", "-ResY=720"]
    common = [f"--socket-dir={runtime / 'sockets'}",
              f"--socket-dirs={runtime / 'sockets'}",
              f"--sessions-dir={runtime / 'sessions'}"]
    start = ["/usr/bin/xpra", "start", config.display, "--backend=x11",
             "--daemon=no", "--use-display=no", "--start-via-proxy=no",
             f"--start-child={shlex.join(child)}",
             "--exit-with-children=yes", "--terminate-children=yes",
             "--sharing=yes", f"--bind-tcp=0.0.0.0:{config.tcp_port}", "--html=on",
             "--dbus=no", "--dbus-launch=", "--pulseaudio=no",
             "--notifications=no", "--mdns=no", "--printing=no", "--file-transfer=no",
             f"--log-dir={runtime / 'logs'}",
             f"--pidfile={runtime / 'xpra.pid'}",
             "--xvfb=Xvfb -screen 0 1280x720x24 +extension GLX +extension RANDR +extension RENDER +extension Composite -extension DOUBLE-BUFFER -nolisten tcp -noreset",
             *common]
    endpoint = "socket://" + str(runtime / "sessions" / config.display[1:] / "socket")
    stop = ["/usr/bin/xpra", "stop", endpoint, *common]
    return start, stop

def scene_list():
    return [{"id":key, "name":scene.name,
             "available":Path(scene.executable).is_file() and __import__("os").access(scene.executable, 1)}
            for key,scene in SCENES.items()]
