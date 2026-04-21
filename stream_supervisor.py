"""Supervise the muselsl streamer so it restarts after disconnects."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from threading import Event, Thread
import time

import pylsl


@dataclass
class MuseStreamSupervisor:
    """Keep the muselsl streamer running and retry after failures."""

    address: str | None = None
    name: str | None = None
    backend: str = "auto"
    interface: str | None = None
    ppg_enabled: bool = True
    acc_enabled: bool = True
    gyro_enabled: bool = True
    reconnect_delay_seconds: float = 3.0
    verbose: bool = False

    def __post_init__(self) -> None:
        self._stop_event = Event()
        self._thread = Thread(target=self._run, daemon=True, name="muselsl-supervisor")

    def start(self) -> None:
        """Start the background muselsl supervisor thread."""
        self._thread.start()

    def stop(self) -> None:
        """Request that the supervisor stop after the current run returns."""
        self._stop_event.set()

    # Processes that exit faster than this are assumed to have failed at the
    # BLE scan stage (device not found / not powered on) rather than after a
    # genuine connected stream.  They get a longer backoff to avoid hammering
    # the BLE stack.
    _MIN_CONNECTED_SECONDS = 10.0

    def _lsl_stream_available(self) -> bool:
        """Return True if an EEG LSL stream is already broadcasting."""
        return bool(pylsl.resolve_byprop("type", "EEG", timeout=1.0))

    def _run(self) -> None:
        """Loop forever, restarting muselsl whenever it exits."""
        while not self._stop_event.is_set():
            # If an LSL stream is already present (e.g. muselsl was started
            # manually, or is still running from a previous cycle), wait and
            # monitor rather than spawning a competing muselsl process.
            if self._lsl_stream_available():
                if self.verbose:
                    print("[muselsl] LSL stream already active; monitoring...")
                while not self._stop_event.is_set():
                    self._stop_event.wait(2.0)
                    if not self._lsl_stream_available():
                        print(
                            "[muselsl] LSL stream lost; "
                            f"retrying in {self.reconnect_delay_seconds:.1f}s"
                        )
                        self._stop_event.wait(self.reconnect_delay_seconds)
                        break
                continue

            try:
                if self.verbose:
                    print("[muselsl] searching for Muse device...")

                cmd = self._build_muselsl_command()
                # Discard subprocess output to prevent pipe-buffer stalls;
                # the supervisor's own print() statements provide status.
                sink = None if self.verbose else subprocess.DEVNULL
                started_at = time.monotonic()
                process = subprocess.Popen(cmd, stdout=sink, stderr=sink)

                # Wait for the process to exit
                while not self._stop_event.is_set():
                    if process.poll() is not None:
                        break
                    time.sleep(0.5)
                else:
                    # Stop event was set — terminate gracefully and exit loop.
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                    break

                if self._stop_event.is_set():
                    break

                elapsed = time.monotonic() - started_at
                if elapsed < self._MIN_CONNECTED_SECONDS:
                    # Exited almost immediately — device not found during scan.
                    print(
                        "[muselsl] Muse device not found; "
                        f"retrying in {self.reconnect_delay_seconds:.1f}s"
                    )
                else:
                    print(
                        "[muselsl] streamer stopped or disconnected; "
                        f"retrying in {self.reconnect_delay_seconds:.1f}s"
                    )

            except Exception as err:
                if self._stop_event.is_set():
                    break
                print(
                    f"[muselsl] streamer failed: {err}; retrying in "
                    f"{self.reconnect_delay_seconds:.1f}s"
                )
            self._stop_event.wait(self.reconnect_delay_seconds)

    def _build_muselsl_command(self) -> list[str]:
        """Build the muselsl stream command with the configured parameters."""
        cmd = ["muselsl", "stream"]
        
        if self.address:
            cmd.extend([self.address])
        if self.backend and self.backend != "auto":
            cmd.extend(["--backend", self.backend])
        if self.interface:
            cmd.extend(["--interface", self.interface])
        if self.name:
            cmd.extend(["--name", self.name])
        if self.ppg_enabled:
            cmd.append("--ppg")
        if self.acc_enabled:
            cmd.append("--acc")
        if self.gyro_enabled:
            cmd.append("--gyro")
        # Let muselsl handle brief BLE hiccups internally rather than exiting
        # and forcing our supervisor to restart it for every minor dropout.
        cmd.extend(["--retries", "10"])

        return cmd