"""Runs cloudflared so the glasses can reach FreeCAD over a real certificate.

A published lens may only use `wss://` and `https://`, which needs a
certificate, which needs a public hostname. A quick tunnel supplies both for
free and without an account: cloudflared opens an outbound connection and
Cloudflare publishes `https://<four-words>.trycloudflare.com` in front of it.

So the glasses reach FreeCAD through a hostname nobody had to buy, and no
model passes through anybody's server but the user's own machine.

The one cost is that the hostname is random and changes every time the
tunnel restarts, which is why the addon shows the four words and the lens
asks for them.

Standard library only, like everything else that runs inside FreeCAD.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request

# cloudflared prints the url inside a box of plus signs and dashes.
URL_RE = re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com")

WINDOWS_PATHS = (
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
)


def find_cloudflared():
    """The cloudflared binary, or None. PATH first, then where winget puts it."""
    found = shutil.which("cloudflared")
    if found:
        return found
    for candidate in WINDOWS_PATHS:
        if os.path.isfile(candidate):
            return candidate
    return None


def process_alive(pid: int) -> bool:
    """Whether a pid is still running, without signals or psutil."""
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", "PID eq {0}".format(pid), "/NH", "/FO", "CSV"],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Tunnel:
    """One cloudflared quick tunnel in front of the local server.

    A quick tunnel's hostname is random, and a new one means retyping four
    words on a headset. So the tunnel deliberately outlives FreeCAD: the
    hostname and pid are written down, and the next FreeCAD start adopts the
    running tunnel instead of making a new name. Closing FreeCAD does not
    stop sharing; the toolbar button does.
    """

    def __init__(self, log=None, warn=None, on_ready=None, state_path=None):
        self._process = None
        self._thread = None
        self._log = log or (lambda message: None)
        self._warn = warn or (lambda message: None)
        self._on_ready = on_ready or (lambda host: None)
        self._state_path = state_path
        self._adopted = False
        self._port = 0
        self.hostname = ""
        self.error = ""

    # ---- remembering a tunnel across FreeCAD restarts ----

    def _read_state(self):
        if not self._state_path or not os.path.isfile(self._state_path):
            return None
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _write_state(self, pid, port) -> None:
        if not self._state_path:
            return
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump({"pid": pid, "hostname": self.hostname,
                           "port": port}, f)
        except OSError as e:
            self._warn("could not remember the tunnel: {0}".format(e))

    def _clear_state(self) -> None:
        if not self._state_path:
            return
        try:
            os.remove(self._state_path)
        except OSError:
            pass

    def adopt(self, port: int) -> bool:
        """Reuse a tunnel left running by an earlier FreeCAD, if it works.

        Checked rather than assumed: the pid must still exist and the
        hostname must actually answer, since a stale note would hand the
        lens an address that silently goes nowhere.
        """
        state = self._read_state()
        if not state:
            return False
        pid = int(state.get("pid", 0))
        hostname = state.get("hostname", "")
        if not hostname or state.get("port") != port or not process_alive(pid):
            self._clear_state()
            return False
        try:
            request = urllib.request.Request(
                "https://{0}/status".format(hostname))
            with urllib.request.urlopen(request, timeout=15) as r:
                if r.status != 200:
                    raise OSError("status {0}".format(r.status))
        except Exception:
            self._clear_state()
            return False

        self.hostname = hostname
        self._adopted = True
        self._log("reusing the tunnel already running at https://{0}".format(
            hostname))
        self._log("    the words in the lens are still {0}".format(self.words))
        self._on_ready("https://" + hostname)
        return True

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def starting(self) -> bool:
        """Running, but Cloudflare has not named it yet."""
        return self.running and not self.hostname

    @property
    def words(self) -> str:
        """Just the part a person has to type, without the fixed suffix."""
        if not self.hostname:
            return ""
        return self.hostname.split(".")[0]

    def start(self, port: int) -> bool:
        if self.running or self._adopted:
            return True
        # A tunnel left running by an earlier FreeCAD keeps its hostname,
        # which keeps the words already typed into the lens valid.
        if self.adopt(port):
            return True
        binary = find_cloudflared()
        if binary is None:
            self.error = (
                "cloudflared is not installed. Get it with "
                "winget install Cloudflare.cloudflared, or from "
                "https://developers.cloudflare.com/cloudflared"
            )
            self._warn(self.error)
            return False

        self.hostname = ""
        self.error = ""
        command = [
            binary, "tunnel",
            "--url", "http://127.0.0.1:{0}".format(port),
            "--no-autoupdate",
        ]
        # CREATE_NO_WINDOW, so starting a tunnel does not flash a console
        # over FreeCAD. Only defined on Windows.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )
        except OSError as e:
            self.error = "could not start cloudflared: {0}".format(e)
            self._warn(self.error)
            return False

        self._port = port
        self._thread = threading.Thread(
            target=self._read_output, name="HoloCAD tunnel", daemon=True)
        self._thread.start()
        self._log("opening a tunnel, this takes a few seconds")
        return True

    def _read_output(self) -> None:
        """Watch for the hostname, and keep reading so the pipe never fills.

        cloudflared keeps logging for as long as it runs. A full pipe would
        block it, so this drains the stream for the life of the process even
        though only the first url matters.
        """
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            if self.hostname:
                continue
            match = URL_RE.search(line)
            if match:
                self.hostname = match.group(0).replace("https://", "")
                self._write_state(process.pid, self._port)
                self._log("=" * 58)
                self._log("TUNNEL READY. Type these words into the lens:")
                self._log("")
                self._log("    {0}".format(self.words))
                self._log("")
                self._log("full address https://{0}".format(self.hostname))
                self._log("=" * 58)
                self._log("    they stay valid until you stop sharing, even "
                          "across FreeCAD restarts")
                try:
                    self._on_ready("https://" + self.hostname)
                except Exception as e:
                    self._warn("tunnel callback failed: {0}".format(e))

    def stop(self) -> None:
        """Stop sharing. Also kills a tunnel this FreeCAD only adopted."""
        state = self._read_state()
        self._clear_state()
        if self._process is not None:
            self._terminate(self._process)
            self._process = None
        elif self._adopted and state:
            self._terminate_pid(int(state.get("pid", 0)))
        self._adopted = False
        self.hostname = ""
        self._log("tunnel closed, sharing is off")

    def detach(self) -> None:
        """Let the tunnel keep running after FreeCAD closes.

        The hostname is the only thing standing between the user and
        retyping four words on a headset, so closing FreeCAD deliberately
        does not throw it away. Stop sharing from the toolbar to end it.
        """
        if self.hostname:
            self._log("leaving the tunnel open at https://{0}, so the words "
                      "stay valid".format(self.hostname))
        self._process = None
        self._thread = None

    def _terminate(self, process) -> None:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _terminate_pid(self, pid: int) -> None:
        if pid <= 0:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.kill(pid, 15)
        except Exception as e:
            self._warn("could not stop the tunnel process: {0}".format(e))
