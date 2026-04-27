"""Supervise the muselsl streamer so it restarts after disconnects."""

from __future__ import annotations

import logging
import multiprocessing as mp
from queue import Full
from dataclasses import dataclass
from threading import Event, Thread
import time

import pylsl


def _run_muselsl_stream(
    address: str | None,
    backend: str,
    interface: str | None,
    name: str | None,
    ppg_enabled: bool,
    acc_enabled: bool,
    gyro_enabled: bool,
    verbose: bool,
    log_queue: mp.Queue | None = None,
) -> None:
    """Run muselsl streaming inside a child process."""
    # Patch stdio before any import so that windowed/frozen builds never hit
    # 'NoneType has no attribute write' during muselsl print() or tracebacks.
    import sys
    from queue import Full

    if log_queue is not None:
        from osc_bridge import _QueueWriter
        _writer = _QueueWriter(log_queue, "muselsl")
        sys.stdout = _writer
        sys.stderr = _writer
    elif sys.stdout is None or sys.stderr is None:
        class _NullWriter:
            def write(self, d): return len(d)
            def flush(self): pass
        _null = _NullWriter()
        if sys.stdout is None:
            sys.stdout = _null
        if sys.stderr is None:
            sys.stderr = _null

    from muselsl import stream

    log_level = logging.INFO if verbose else logging.ERROR
    try:
        stream(
            address=address,
            backend=backend,
            interface=interface,
            name=name,
            ppg_enabled=ppg_enabled,
            acc_enabled=acc_enabled,
            gyro_enabled=gyro_enabled,
            retries=10,
            log_level=log_level,
        )
    except Exception as err:
        print(f"[muselsl] stream exited with error: {err}")


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
    log_queue: object | None = None

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

    def _log(self, message: str) -> None:
        """Emit one supervisor log line to stdout or an optional GUI queue."""
        if self.log_queue is None:
            print(message)
            return
        try:
            self.log_queue.put_nowait(f"[supervisor] {message}")
        except Full:
            pass

    def _run(self) -> None:
        """Loop forever, restarting muselsl whenever it exits."""
        while not self._stop_event.is_set():
            # If an LSL stream is already present (e.g. muselsl was started
            # manually, or is still running from a previous cycle), wait and
            # monitor rather than spawning a competing muselsl process.
            if self._lsl_stream_available():
                if self.verbose:
                    self._log("[muselsl] LSL stream already active; monitoring...")
                while not self._stop_event.is_set():
                    self._stop_event.wait(2.0)
                    if not self._lsl_stream_available():
                        self._log(
                            "[muselsl] LSL stream lost; "
                            f"retrying in {self.reconnect_delay_seconds:.1f}s"
                        )
                        self._stop_event.wait(self.reconnect_delay_seconds)
                        break
                continue

            try:
                if self.verbose:
                    self._log("[muselsl] searching for Muse device...")

                started_at = time.monotonic()
                process = mp.Process(
                    target=_run_muselsl_stream,
                    args=(
                        self.address,
                        self.backend,
                        self.interface,
                        self.name,
                        self.ppg_enabled,
                        self.acc_enabled,
                        self.gyro_enabled,
                        self.verbose,
                        self.log_queue,
                    ),
                    daemon=True,
                    name="muselsl-stream",
                )
                process.start()

                # Wait for the process to exit
                while not self._stop_event.is_set():
                    if not process.is_alive():
                        break
                    time.sleep(0.5)
                else:
                    # Stop event was set — terminate gracefully and exit loop.
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=5)
                    break

                if self._stop_event.is_set():
                    break

                elapsed = time.monotonic() - started_at
                if elapsed < self._MIN_CONNECTED_SECONDS:
                    # Exited almost immediately — device not found during scan.
                    self._log(
                        "[muselsl] Muse device not found; "
                        f"retrying in {self.reconnect_delay_seconds:.1f}s"
                    )
                else:
                    self._log(
                        "[muselsl] streamer stopped or disconnected; "
                        f"retrying in {self.reconnect_delay_seconds:.1f}s"
                    )

            except Exception as err:
                if self._stop_event.is_set():
                    break
                self._log(
                    f"[muselsl] streamer failed: {err}; retrying in "
                    f"{self.reconnect_delay_seconds:.1f}s"
                )
            self._stop_event.wait(self.reconnect_delay_seconds)