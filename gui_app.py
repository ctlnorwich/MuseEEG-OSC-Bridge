"""PyQt6 desktop UI for running the Muse OSC bridge."""

from __future__ import annotations

from dataclasses import dataclass
import multiprocessing as mp
from queue import Empty
import threading
import time

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from osc_bridge import start_bridge_processes, stop_bridge_processes
from stream_supervisor import MuseStreamSupervisor


@dataclass
class BridgeConfig:
    """Runtime options mirrored from the CLI flags."""

    aux: bool
    verbose: bool
    osc_ip: str
    osc_port: int
    muse_address: str | None
    muse_name: str | None
    backend: str
    interface: str | None
    reconnect_delay: float
    no_ppg: bool
    no_acc: bool
    no_gyro: bool


class BridgeSession:
    """Own the streaming lifecycle for one connect/disconnect session."""

    def __init__(self, log_queue: mp.Queue) -> None:
        self._log_queue = log_queue
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def is_running(self) -> bool:
        """Return True while the session thread is active."""
        return bool(self._thread and self._thread.is_alive())

    def start(self, config: BridgeConfig) -> None:
        """Start bridge workers if no active session exists."""
        if self.is_running():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(config,),
            daemon=True,
            name="bridge-session",
        )
        self._thread.start()

    def stop(self) -> None:
        """Request graceful shutdown and wait briefly for completion."""
        if not self.is_running():
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=8.0)

    def _log(self, message: str) -> None:
        """Push a log line to the GUI queue without blocking."""
        try:
            self._log_queue.put_nowait(f"[app] {message}")
        except Exception:
            pass

    def _run(self, config: BridgeConfig) -> None:
        """Launch and supervise supervisor + OSC bridge process workers."""
        supervisor = MuseStreamSupervisor(
            address=config.muse_address,
            name=config.muse_name,
            backend=config.backend,
            interface=config.interface,
            ppg_enabled=not config.no_ppg,
            acc_enabled=not config.no_acc,
            gyro_enabled=not config.no_gyro,
            reconnect_delay_seconds=config.reconnect_delay,
            verbose=config.verbose,
            log_queue=self._log_queue,
        )
        processes = []
        try:
            self._log("Starting Muse supervisor and bridge workers")
            supervisor.start()
            processes = start_bridge_processes(
                use_aux=config.aux,
                osc_ip=config.osc_ip,
                osc_port=config.osc_port,
                ppg_enabled=not config.no_ppg,
                acc_enabled=not config.no_acc,
                gyro_enabled=not config.no_gyro,
                verbose=config.verbose,
                log_queue=self._log_queue,
            )
            self._log("Connected")
            while not self._stop_event.is_set():
                if any(not process.is_alive() for process in processes):
                    self._log("A worker exited unexpectedly; reconnect logic is active")
                    time.sleep(1.0)
                    continue
                time.sleep(0.2)
        except Exception as err:
            self._log(f"Fatal session error: {err}")
        finally:
            supervisor.stop()
            stop_bridge_processes(processes)
            self._log("Disconnected")


class MainWindow(QMainWindow):
    """Main application window with control panel and live logs."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Muse OSC Bridge")
        self.resize(980, 700)

        self._log_queue: mp.Queue = mp.Queue(maxsize=5000)
        self._session = BridgeSession(self._log_queue)

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        controls_group = QGroupBox("Connection")
        controls_layout = QGridLayout(controls_group)

        self.connect_button = QPushButton("Connect")
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setEnabled(False)

        self.connect_button.clicked.connect(self._on_connect)
        self.disconnect_button.clicked.connect(self._on_disconnect)

        controls_layout.addWidget(self.connect_button, 0, 0)
        controls_layout.addWidget(self.disconnect_button, 0, 1)

        options_group = QGroupBox("Options")
        options_layout = QHBoxLayout(options_group)

        left_form_container = QWidget()
        left_form = QFormLayout(left_form_container)

        self.osc_ip_input = QLineEdit("127.0.0.1")
        self.osc_port_input = QSpinBox()
        self.osc_port_input.setRange(1, 65535)
        self.osc_port_input.setValue(9000)

        self.muse_address_input = QLineEdit()
        self.muse_name_input = QLineEdit()
        self.backend_input = QLineEdit("auto")
        self.interface_input = QLineEdit()

        self.reconnect_delay_input = QDoubleSpinBox()
        self.reconnect_delay_input.setRange(0.2, 60.0)
        self.reconnect_delay_input.setSingleStep(0.1)
        self.reconnect_delay_input.setValue(3.0)

        left_form.addRow(QLabel("OSC IP"), self.osc_ip_input)
        left_form.addRow(QLabel("OSC Port"), self.osc_port_input)
        left_form.addRow(QLabel("Muse Address"), self.muse_address_input)
        left_form.addRow(QLabel("Muse Name"), self.muse_name_input)
        left_form.addRow(QLabel("Backend"), self.backend_input)
        left_form.addRow(QLabel("Interface"), self.interface_input)
        left_form.addRow(QLabel("Reconnect Delay (s)"), self.reconnect_delay_input)

        right_form_container = QWidget()
        right_form = QFormLayout(right_form_container)

        self.aux_checkbox = QCheckBox("Include AUX EEG channel")
        self.verbose_checkbox = QCheckBox("Verbose logs")
        self.no_ppg_checkbox = QCheckBox("Disable PPG stream")
        self.no_acc_checkbox = QCheckBox("Disable ACC stream")
        self.no_gyro_checkbox = QCheckBox("Disable GYRO stream")

        right_form.addRow(self.aux_checkbox)
        right_form.addRow(self.verbose_checkbox)
        right_form.addRow(self.no_ppg_checkbox)
        right_form.addRow(self.no_acc_checkbox)
        right_form.addRow(self.no_gyro_checkbox)

        options_layout.addWidget(left_form_container)
        options_layout.addWidget(right_form_container)

        logs_group = QGroupBox("Application Log")
        logs_layout = QVBoxLayout(logs_group)
        self.logs_output = QPlainTextEdit()
        self.logs_output.setReadOnly(True)
        self.logs_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.logs_output.setPlaceholderText("Logs will appear here after you click Connect")
        logs_layout.addWidget(self.logs_output)

        layout.addWidget(controls_group)
        layout.addWidget(options_group)
        layout.addWidget(logs_group, stretch=1)

        self._log_timer = QTimer(self)
        self._log_timer.setInterval(80)
        self._log_timer.timeout.connect(self._drain_logs)
        self._log_timer.start()

    def closeEvent(self, event) -> None:
        """Stop background workers before the app closes."""
        if self._session.is_running():
            self._on_disconnect()
        super().closeEvent(event)

    def _collect_config(self) -> BridgeConfig | None:
        """Read, normalize, and validate all option widgets."""
        osc_ip = self.osc_ip_input.text().strip()
        if not osc_ip:
            QMessageBox.warning(self, "Invalid OSC IP", "OSC IP cannot be empty.")
            return None

        backend = self.backend_input.text().strip() or "auto"

        muse_address = self.muse_address_input.text().strip() or None
        muse_name = self.muse_name_input.text().strip() or None
        interface = self.interface_input.text().strip() or None

        return BridgeConfig(
            aux=self.aux_checkbox.isChecked(),
            verbose=self.verbose_checkbox.isChecked(),
            osc_ip=osc_ip,
            osc_port=int(self.osc_port_input.value()),
            muse_address=muse_address,
            muse_name=muse_name,
            backend=backend,
            interface=interface,
            reconnect_delay=float(self.reconnect_delay_input.value()),
            no_ppg=self.no_ppg_checkbox.isChecked(),
            no_acc=self.no_acc_checkbox.isChecked(),
            no_gyro=self.no_gyro_checkbox.isChecked(),
        )

    def _on_connect(self) -> None:
        """Start a new bridge session from the current form values."""
        config = self._collect_config()
        if config is None:
            return

        if self._session.is_running():
            QMessageBox.information(self, "Already connected", "The bridge is already running.")
            return

        self._session.start(config)
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)

    def _on_disconnect(self) -> None:
        """Stop the current bridge session and re-enable connect."""
        self._session.stop()
        self.connect_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)

    def _drain_logs(self) -> None:
        """Move queued log lines into the text view at UI cadence."""
        drained = 0
        while drained < 200:
            try:
                line = self._log_queue.get_nowait()
            except Empty:
                break
            self.logs_output.appendPlainText(str(line))
            drained += 1


def main() -> int:
    """Run the Muse OSC Bridge desktop GUI."""
    mp.freeze_support()
    app = QApplication([])
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
