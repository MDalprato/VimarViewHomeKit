"""On-demand QUII to timestamped MPEG-TS, bound to loopback only."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import secrets
import select
import socket
import subprocess
import threading
from .protocol.vimar_watch import Client
from .protocol.vimar_video import video_packets

_LOGGER = logging.getLogger(__name__)

class VideoBridge:
    def __init__(self, data):
        self.data = data
        self.token = secrets.token_urlsafe(32)
        self.stopping = threading.Event()
        self.locks = {i: threading.Lock() for i in range(1, data["stations"] + 1)}
        self.server = None
        self.processes = set()
        self.process_lock = threading.Lock()

    def start(self):
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Never log stream tokens or credentials.

            def do_GET(self):
                station = next((i for i in bridge.locks if self.path == f"/{bridge.token}/{i}.ts"), None)
                if station is None:
                    self.send_error(404)
                    return
                lock = bridge.locks[station]
                if bridge.stopping.is_set() or not lock.acquire(blocking=False):
                    self.send_error(503)
                    return
                process = None
                feeder = None
                done = threading.Event()
                try:
                    self.connection.settimeout(5)
                    # Raw QUII has no container timestamps. Re-encode without B frames
                    # to supply monotonic PTS/DTS; assumed 15 fps until timing is decoded.
                    process = subprocess.Popen([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "+genpts",
                        "-f", "h264", "-r", "15", "-probesize", "32768", "-analyzeduration", "0",
                        "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "ultrafast",
                        "-tune", "zerolatency", "-bf", "0", "-g", "30",
                        "-f", "mpegts", "-mpegts_flags", "+resend_headers",
                        "-flush_packets", "1", "pipe:1",
                    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    with bridge.process_lock:
                        bridge.processes.add(process)
                        if bridge.stopping.is_set():
                            process.terminate()

                    def feed():
                        packets = None
                        try:
                            client = Client(bridge.data["host"], bridge.data["password"])
                            packets = video_packets(client, bridge.data["host"], 34567, station, 0)
                            for packet in packets:
                                if done.is_set() or bridge.stopping.is_set():
                                    break
                                # Release QUII even if the HTTP consumer disconnected
                                # before FFmpeg produced enough data to write a response.
                                if select.select([self.connection], [], [], 0)[0]:
                                    if not self.connection.recv(1, socket.MSG_PEEK):
                                        break
                                process.stdin.write(packet)
                                process.stdin.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        except Exception as exc:
                            _LOGGER.warning("Video targa %s interrotto (%s)", station, type(exc).__name__)
                        finally:
                            if packets is not None:
                                packets.close()
                            try:
                                process.stdin.close()
                            except OSError:
                                pass

                    feeder = threading.Thread(target=feed, daemon=True)
                    feeder.start()
                    first = process.stdout.read1(188 * 7)
                    if not first:
                        self.send_error(502)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp2t")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(first)
                    while not bridge.stopping.is_set():
                        packet = process.stdout.read1(188 * 7)
                        if not packet:
                            break
                        self.wfile.write(packet)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                except Exception as exc:
                    _LOGGER.warning("Bridge video targa %s interrotto (%s)", station, type(exc).__name__)
                finally:
                    done.set()
                    if process is not None:
                        if process.poll() is None:
                            process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        if feeder is not None:
                            feeder.join(timeout=10)
                        process.stdout.close()
                        with bridge.process_lock:
                            bridge.processes.discard(process)
                    lock.release()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def source(self, station):
        return f"http://127.0.0.1:{self.server.server_port}/{self.token}/{station}.ts"

    def stop(self):
        self.stopping.set()
        with self.process_lock:
            for process in self.processes:
                if process.poll() is None:
                    process.terminate()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
